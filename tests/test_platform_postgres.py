"""The platform store (firms, users, sessions, wrapped keys, provisioning journal) on PostgreSQL (ADR-0002).

Runs only with AGENTLEDGER_DATABASE=postgres: the autouse fixture gives each test its own schema prefix on the local
server, so the platform schema here is `<prefix>platform`, migrated on first open with the owner credentials in the
environment and dropped afterwards with its runtime role.

* `agentledger platform migrate` creates the schema and the runtime role, which holds exactly the privileges the code
  needs: no UPDATE or DELETE on the append-only tables, no DDL, nothing in a firm's schema.
* Two API containers (two Platform instances) see one store; invitations and the first administrator are single use
  across them; failed sign-ins counted from concurrent threads are never lost.
* Production boots with runtime credentials only once the schema is migrated, and says what to run when it is not.
* The provisioning worker runs with owner database credentials and without the master key (separation of duties).
"""

from __future__ import annotations

import base64
import re
import secrets
import threading

import pytest

from agentledger import db
from agentledger.security.platform import MAX_FAILURES, AuthError, Platform, migrate_platform

pytestmark = pytest.mark.skipif(db.backend() != "postgres", reason="needs AGENTLEDGER_DATABASE=postgres")

PW = "correct horse battery staple"
ADMIN = "ops@agentledger.example"
ROLE_KEY = "test-role-key-" + secrets.token_hex(4)


def _master_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def test_migration_creates_the_platform_schema_and_a_role_with_exact_privileges(tmp_path):
    import psycopg

    from agentledger import pg
    from agentledger.pg import PLATFORM_SCHEMA

    plat = Platform(tmp_path, dev=True)                       # owner credentials in the environment: migrated on open
    schema = db.pg_name(PLATFORM_SCHEMA)
    assert schema.endswith("platform") and schema != "platform"           # the test prefix keeps runs apart
    role = plat.conn.role
    assert role == pg.runtime_role(pg.database_of(pg.migration_url()), schema) and role.endswith("_platform")
    plat.create_firm("acme-cpa", "Acme CPA", by="ops")        # provisions a firm schema next to the platform's
    firm_schema = db.schema_for(plat.tenant_dir("acme-cpa") / "state" / "agentledger.db")
    assert plat.conn.execute("SELECT datetime('now')").fetchone()[0] and \
        re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", plat.conn.execute("SELECT datetime('now')").fetchone()[0])

    owner = pg.connect(pg.migration_url())
    try:
        assert [r[0] for r in owner.execute(f'SELECT name FROM "{schema}".schema_migrations').fetchall()] == ["0001_platform.sql"]
        assert owner.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        grants: dict[str, set[str]] = {}
        for table, privilege in owner.execute("SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                                              "WHERE grantee = %s AND table_schema = %s", (role, schema)).fetchall():
            grants.setdefault(table, set()).add(privilege)
        rw = {"SELECT", "INSERT", "UPDATE"}
        assert grants == {**{t: rw for t in ("firms", "firm_keys", "users", "invites", "challenges", "sessions", "engagement_grants",
                                             "offboarding_runs", "sso_mfa_attestations", "idp_states", "provisioning")},
                          "auth_events": {"SELECT", "INSERT"}, "firm_offboarding": {"SELECT", "INSERT"},
                          "firm_destruction": {"SELECT", "INSERT", "DELETE"}}
        # The append-only tables refuse the owner too (the trigger), with the error code the code maps to IntegrityError.
        owner.execute(f'SET search_path TO "{schema}"')
        owner.execute("INSERT INTO firm_offboarding (firm_id, by, reason, summary) VALUES ('gone-cpa', 'ops', 'closed', '{}')")
        for statement in ("UPDATE auth_events SET detail = 'x'", "DELETE FROM auth_events", "TRUNCATE auth_events",
                          "UPDATE firm_offboarding SET reason = 'x'", "DELETE FROM firm_offboarding"):
            with pytest.raises(psycopg.Error, match="append-only") as info:
                owner.execute(statement)
            assert info.value.sqlstate == "AL006"
    finally:
        owner.close()

    runtime = pg.connect(pg.runtime_url(pg.runtime_base_url(), role))
    try:
        assert runtime.execute("SELECT current_user").fetchone()[0] == role
        runtime.execute(f'SET search_path TO "{schema}"')
        runtime.execute("INSERT INTO auth_events (event, detail) VALUES ('probe', 'as the runtime role')")
        assert runtime.execute("SELECT count(*) FROM auth_events WHERE event = 'probe'").fetchone()[0] == 1
        denied = ("UPDATE auth_events SET detail = 'x'", "DELETE FROM auth_events", "UPDATE firm_offboarding SET reason = 'x'",
                  "DELETE FROM firm_offboarding", "DELETE FROM users", "DELETE FROM firms", "SELECT * FROM schema_migrations",
                  "CREATE TABLE probe (id int)", "ALTER TABLE users ADD COLUMN probe text", "DROP TABLE auth_events",
                  f'SELECT * FROM "{firm_schema}".clients', f'CREATE TABLE "{firm_schema}".probe (id int)')
        for statement in denied:
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied|must be owner"):
                runtime.execute(statement)
    finally:
        runtime.close()
    # Through the store's own connection the refusal is a database error the callers already handle.
    with pytest.raises(db.DatabaseError, match="permission denied"):
        plat.conn.execute("DELETE FROM auth_events")


def test_two_platform_instances_share_one_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLEDGER_MASTER_KEY", _master_key())      # one master key, as two containers would have
    a = Platform(tmp_path / "container-a", dev=False)
    b = Platform(tmp_path / "container-b", dev=False)
    admin_id = a.bootstrap_admin(ADMIN, "Ops", PW)
    assert b.is_platform_admin(ADMIN)
    with pytest.raises(AuthError, match="a platform administrator already exists"):
        b.bootstrap_admin("second@agentledger.example", "Two", PW)
    a.create_firm("rivera-cpa", "Rivera CPA", by=admin_id)
    assert b.firm("rivera-cpa")["status"] == "active"
    token = a.invite("rivera-cpa", "maya@rivera.example", "firm_admin", by=a.public_user(a.user(admin_id)))
    enrol = b.accept_invite(token, "Maya Chen", PW)
    assert enrol.challenge
    with pytest.raises(AuthError, match="invalid or has expired"):          # single use, seen by the other instance
        a.accept_invite(token, "Maya Chen", PW)
    assert [u["email"] for u in a.users("rivera-cpa")] == ["maya@rivera.example"]
    blob = a.keys.encrypt("rivera-cpa", b"123-45-6789", "ssn")              # the wrapped firm key is in the shared store
    assert b.keys.decrypt("rivera-cpa", blob, "ssn") == b"123-45-6789"
    assert {e["event"] for e in b.events("rivera-cpa")} >= {"firm_created", "invite_created", "invite_accepted"}
    for root in (tmp_path / "container-a", tmp_path / "container-b"):       # nothing of the platform lands on disk
        assert not (root / "state").exists()


def test_bootstrap_admin_twice_is_refused(tmp_path):
    plat = Platform(tmp_path, dev=True)
    plat.bootstrap_admin(ADMIN, "Ops", PW)
    with pytest.raises(AuthError, match="a platform administrator already exists"):
        plat.bootstrap_admin("second@agentledger.example", "Two", PW)
    assert plat.conn.execute("SELECT count(*) FROM users WHERE role = 'platform_admin'").fetchone()[0] == 1
    assert plat.conn.execute("SELECT count(*) FROM users WHERE email = ?", (ADMIN,)).fetchone()[0] == 1


def test_failed_logins_counted_from_concurrent_threads_lose_no_increment(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLEDGER_MASTER_KEY", _master_key())
    plat = Platform(tmp_path, dev=False)
    uid = plat.bootstrap_admin(ADMIN, "Ops", PW)
    attempts = MAX_FAILURES - 1                                 # one short of the lockout, so every count is visible
    barrier = threading.Barrier(attempts)
    outcomes: list[str] = []

    def attempt() -> None:                                      # each thread has its own connection to the store
        barrier.wait(30)
        try:
            plat.login(ADMIN, "not the password at all")
            outcomes.append("signed in")
        except AuthError as e:
            outcomes.append(str(e))

    threads = [threading.Thread(target=attempt, name=f"api-worker-{i}") for i in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert outcomes == ["email or password is incorrect"] * attempts
    u = plat.user(uid)
    assert u["failed_logins"] == attempts and not u["locked_until"]
    details = sorted(e["detail"] for e in plat.events() if e["event"] == "login_failed")
    assert details == [f"failure {i}" for i in range(1, attempts + 1)]
    with pytest.raises(AuthError, match="email or password is incorrect"):   # the fifth locks the account
        plat.login(ADMIN, "still not the password")
    with pytest.raises(AuthError, match="too many attempts"):
        plat.login(ADMIN, PW)


def test_refusal_inside_a_critical_section_keeps_its_records(tmp_path):
    plat = Platform(tmp_path, dev=True)
    uid = plat.bootstrap_admin(ADMIN, "Ops", PW)
    step = plat.login(ADMIN, PW)
    with pytest.raises(AuthError, match="that code is not valid"):
        plat.complete_mfa(step["enroll"].challenge, "000000")
    assert plat.user(uid)["failed_logins"] == 1                 # counted and committed before the refusal was raised
    assert "mfa_failed" in [e["event"] for e in plat.events()]
    with pytest.raises(AuthError, match="that code is not valid"):     # the challenge is still open: not spent by a failure
        plat.complete_mfa(step["enroll"].challenge, "000001")
    assert plat.user(uid)["failed_logins"] == 2


def _runtime_only(monkeypatch, base_url: str) -> None:
    """The API container's environment: where to connect and the role key, no owner credentials."""
    monkeypatch.setenv("AGENTLEDGER_RUNTIME_DATABASE_URL", base_url)
    monkeypatch.setenv("AGENTLEDGER_MIGRATION_URL", "")
    monkeypatch.delenv("DATABASE_URL_UNPOOLED", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_production_boots_with_runtime_credentials_only_once_the_schema_is_migrated(tmp_path, monkeypatch):
    from agentledger import pg

    owner_url = pg.migration_url()
    monkeypatch.setenv("AGENTLEDGER_DB_ROLE_KEY", ROLE_KEY)      # the role's password is derived from it at migration
    monkeypatch.setenv("AGENTLEDGER_MASTER_KEY", _master_key())
    _runtime_only(monkeypatch, owner_url)
    assert pg.migration_url() is None
    with pytest.raises(RuntimeError, match="agentledger platform migrate") as info:    # never migrated: no role, no tables
        Platform(tmp_path, dev=False)
    assert "could not be opened as its runtime role" in str(info.value) and "AGENTLEDGER_DB_ROLE_KEY" in str(info.value)

    monkeypatch.setenv("AGENTLEDGER_MIGRATION_URL", owner_url)   # the release step, with owner credentials and no key
    monkeypatch.delenv("AGENTLEDGER_MASTER_KEY")
    assert migrate_platform() == ["0001_platform.sql"]
    assert migrate_platform() == []                              # idempotent
    monkeypatch.setenv("AGENTLEDGER_MASTER_KEY", _master_key())
    _runtime_only(monkeypatch, owner_url)

    plat = Platform(tmp_path, dev=False)                         # the API container
    assert plat.conn.execute("SELECT current_user").fetchone()[0] == plat.conn.role
    plat.bootstrap_admin(ADMIN, "Ops", PW)
    assert plat.is_platform_admin(ADMIN)
    assert not (tmp_path / "state").exists()                     # nothing written under state/ on PostgreSQL


def test_provisioning_worker_runs_without_the_master_key(tmp_path, monkeypatch):
    from agentledger import pg

    owner_url = pg.migration_url()
    monkeypatch.setenv("AGENTLEDGER_DB_ROLE_KEY", ROLE_KEY)
    monkeypatch.setenv("AGENTLEDGER_MIGRATION_URL", owner_url)
    monkeypatch.delenv("AGENTLEDGER_MASTER_KEY", raising=False)
    assert migrate_platform() == ["0001_platform.sql"]           # the release step

    # The API container: the master key and runtime credentials, no owner credentials. The firm is created with its
    # data key and queued for provisioning.
    monkeypatch.setenv("AGENTLEDGER_MASTER_KEY", _master_key())
    _runtime_only(monkeypatch, owner_url)
    api = Platform(tmp_path, dev=False)
    api.create_firm("lake-tax", "Lake Tax", by="ops")
    assert api.firm("lake-tax")["status"] == "provisioning" and api.get("lake-tax") is None
    assert "firm_store_provisioning_queued" in [e["event"] for e in api.events("lake-tax")]
    assert api.keys.current_version("lake-tax") == 1

    # The provisioning worker (`agentledger platform provision`): owner credentials, no master key.
    monkeypatch.delenv("AGENTLEDGER_MASTER_KEY")
    monkeypatch.setenv("AGENTLEDGER_MIGRATION_URL", owner_url)
    worker = Platform(tmp_path, dev=False, need_keys=False)
    with pytest.raises(RuntimeError, match="without the master key"):
        worker.keys
    assert worker.provision_pending(by="provisioning-worker") == [{"firm": "lake-tax", "status": "active"}]
    assert worker.get("lake-tax")["state"] == "ready"
    assert worker.provision_pending(by="provisioning-worker") == []
    worker.close()

    assert api.firm("lake-tax")["status"] == "active"             # the API sees the store in use
    assert api.keys.current_version("lake-tax") == 1              # the key the API created is intact
    events = [e["event"] for e in api.events("lake-tax")]
    assert "firm_store_provisioned" in events and "firm_created" in events
    assert not (tmp_path / "state").exists()
