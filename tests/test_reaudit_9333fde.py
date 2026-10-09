"""Re-audit of 9333fde (2026-10-08): four findings, each reproduced here before it was fixed.

1. Runtime connections kept the migration owner's credentials behind SET ROLE (RESET ROLE escaped it), and the
   shared application role could read another firm's schema.
2. Row-level security covered return headers but not their versions or workflow history (and other child records);
   a session scoped to client A could read client B's history and add a version to B's return.
3. Receipt matching cast money to REAL: a $19.99 receipt never matched its $19.99 expense on PostgreSQL.
4. A migration failure after Neon created the firm database left an orphan that blocked every retry.

Findings 1 and 2 need a PostgreSQL server (DATABASE_URL_UNPOOLED); 3 runs on whichever backend the suite uses;
4 runs against a fake Neon API.
"""

from __future__ import annotations

import os
import secrets
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from agentledger import db, envfile
from agentledger.ledger import store
from agentledger.ledger.store import Line

REPO = Path(__file__).resolve().parents[1]
envfile.load(REPO)


def _pg_ready() -> bool:
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL"))


needs_pg = pytest.mark.skipif(not _pg_ready() and not os.environ.get("AGENTLEDGER_REQUIRE_PG"),
                              reason="needs a PostgreSQL server (DATABASE_URL_UNPOOLED)")


@pytest.fixture
def stores(monkeypatch):
    """Firm stores in schema tenancy under a private prefix; schemas and runtime roles removed afterwards."""
    from agentledger import pg
    from agentledger.pg import compat

    prefix = "r" + secrets.token_hex(4) + "_"
    monkeypatch.setenv("AGENTLEDGER_PG_SCHEMA_PREFIX", prefix)
    monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")
    opened = []

    def open_firm(firm: str):
        s = compat.PgStore(db.schema_for(Path("tenants") / firm / "state" / "agentledger.db"))
        opened.append(s)
        return s

    yield open_firm
    compat.close_all(prefix)
    owner = pg.connect(pg.migration_url())
    try:
        for (name,) in owner.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE %s", (prefix + "%",)).fetchall():
            owner.execute(f'DROP SCHEMA "{name}" CASCADE')
        for (role,) in owner.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE %s", ("rt%" + prefix + "%",)).fetchall():
            pg.drop_role(owner, role)
    finally:
        owner.close()


# --------------------------------------------------------------------------- 1. runtime credentials
@needs_pg
def test_runtime_connection_cannot_regain_owner_privileges(stores):
    from urllib.parse import urlparse

    from agentledger import pg

    a = stores("iso-a")
    owner_user = urlparse(pg.migration_url()).username or "postgres"
    a.execute("RESET ROLE")                                            # nothing to reset into: it logged in as itself
    who = a.execute("SELECT current_user, session_user").fetchone()
    assert who[0] == who[1] == a.role and a.role.startswith("rt_")
    attrs = a.execute("SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolinherit FROM pg_roles "
                      "WHERE rolname = current_user").fetchone()
    assert list(attrs) == [False, False, False, False, False]
    with pytest.raises(db.DatabaseError):
        a.execute(f'SET ROLE "{owner_user}"')
    with pytest.raises(db.DatabaseError, match="permission denied"):
        a.execute("INSERT INTO entries (client_id, date, memo, source, created_by, prev_hash, hash) "
                  "VALUES ('x', '2026-01-01', 'm', 's', 'u', 'p', 'h')")
    with pytest.raises(db.DatabaseError, match="permission denied"):
        a.execute("SELECT * FROM schema_migrations")


@needs_pg
def test_runtime_connection_cannot_read_another_firm(stores):
    a, b = stores("iso-a"), stores("iso-b")
    store.add_client(b, id="lakeside", name="Lakeside Fuel", kind="business")
    assert b.execute("SELECT count(*) FROM clients").fetchone()[0] == 1
    with pytest.raises(db.DatabaseError, match="permission denied"):
        a.execute(f'SELECT * FROM "{b.schema}".clients')
    with pytest.raises(db.DatabaseError, match="permission denied"):
        a.execute(f'SELECT * FROM "{b.schema}".verify_audit()')


# --------------------------------------------------------------------------- 2. child records follow their parent
def _seed_two_clients(s):
    import json

    from agentledger import audit

    for cid in ("alpha", "bravo"):
        store.add_client(s, id=cid, name=cid.title(), kind="business")
        store.add_account(s, cid, "1000", "Cash", "asset")
        store.add_account(s, cid, "6000", "Supplies", "expense")
        s.execute("INSERT INTO tax_returns (id, client_id, tax_year, form, created_at, created_by) VALUES (?, ?, 2026, '1040', ?, 'm')",
                  (f"ret_{cid}", cid, audit.now()))
        s.execute("INSERT INTO tax_return_versions (return_id, version, inputs, provenance, input_hash, created_at, created_by) "
                  "VALUES (?, 1, '{}', '{}', 'h', ?, 'm')", (f"ret_{cid}", audit.now()))
        s.execute("INSERT INTO workflow_events (workflow_id, seq, kind, event, data, actor, at, prev_hash, hash) "
                  "VALUES (?, 1, 'return_1040', 'started', '{}', 'm', ?, 'genesis', 'h')", (f"ret_{cid}", audit.now()))
        eid = store.post(s, cid, date(2026, 3, 1), "supplies", [Line("6000", Decimal("19.99")), Line("1000", Decimal("-19.99"))],
                         source="t", actor="m")
        s.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, fields) "
                  "VALUES (?, ?, ?, 'r.pdf', 'application/pdf', 'upload', ?, 'filed', '{}')",
                  (f"doc_{cid}", cid, secrets.token_hex(16), audit.now()))
        s.execute("INSERT INTO entry_documents (entry_id, document_id, linked_by, at) VALUES (?, ?, 'm', ?)", (eid, f"doc_{cid}", audit.now()))
        s.execute("INSERT INTO findings (id, client_id, check_id, severity, title, detail, owner, first_seen) "
                  "VALUES (?, ?, 'c', 'low', 't', 'd', 'cpa', ?)", (f"f_{cid}", cid, audit.now()))
        s.execute("INSERT INTO finding_resolutions (finding_id, actor, role, action, note, at) VALUES (?, 'm', 'cpa', 'explained', 'n', ?)",
                  (f"f_{cid}", audit.now()))
        s.execute("INSERT INTO app_commands (scope, command_id, kind, payload_hash, result) VALUES (?, 'c1', 'k', 'h', ?)",
                  (f"client:{cid}", json.dumps({"ok": True})))
    s.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, fields) "
              "VALUES ('doc_inbox', NULL, ?, 'unknown.pdf', 'application/pdf', 'email', ?, 'needs_review', '{}')",
              (secrets.token_hex(16), audit.now()))
    s.execute("INSERT INTO kv (key, value) VALUES ('automation_cursor', '7')")


CHILD_QUERIES = {
    "tax_return_versions": "SELECT count(*) FROM tax_return_versions WHERE return_id = 'ret_bravo'",
    "workflow_events": "SELECT count(*) FROM workflow_events WHERE workflow_id = 'ret_bravo'",
    "entry_documents": "SELECT count(*) FROM entry_documents WHERE document_id = 'doc_bravo'",
    "finding_resolutions": "SELECT count(*) FROM finding_resolutions WHERE finding_id = 'f_bravo'",
    "app_commands": "SELECT count(*) FROM app_commands WHERE scope = 'client:bravo'",
    "documents (unassigned inbox)": "SELECT count(*) FROM documents WHERE client_id IS NULL",
    "kv (firm settings)": "SELECT count(*) FROM kv",
}


@needs_pg
def test_client_scope_covers_child_records_for_reads_and_writes(stores):
    s = stores("iso-c")
    _seed_two_clients(s)
    assert all(s.execute(q).fetchone()[0] == 1 for q in CHILD_QUERIES.values())   # firm-wide sees everything
    s.set_scope(["alpha"])
    leaks = {name: n for name, q in CHILD_QUERIES.items() if (n := s.execute(q).fetchone()[0])}
    assert leaks == {}, f"client-scoped session can read: {leaks}"
    assert s.execute("SELECT count(*) FROM tax_return_versions WHERE return_id = 'ret_alpha'").fetchone()[0] == 1
    # Writes into another client's records are refused; into its own they work.
    refused = {
        "version": ("INSERT INTO tax_return_versions (return_id, version, inputs, provenance, input_hash, created_at, created_by) "
                    "VALUES ('ret_bravo', 2, '{}', '{}', 'h', 'now', 'm')"),
        "workflow": ("INSERT INTO workflow_events (workflow_id, seq, kind, event, data, actor, at, prev_hash, hash) "
                     "VALUES ('ret_bravo', 2, 'return_1040', 'approve', '{}', 'm', 'now', 'h', 'h2')"),
        "resolution": "INSERT INTO finding_resolutions (finding_id, actor, role, action, note, at) VALUES ('f_bravo', 'm', 'cpa', 'explained', 'n', 'now')",
        "receipt": "INSERT INTO app_commands (scope, command_id, kind, payload_hash, result) VALUES ('client:bravo', 'c2', 'k', 'h', '{}')",
    }
    for name, sql in refused.items():
        with pytest.raises(db.DatabaseError, match="row-level security"):
            s.execute(sql)
    s.execute("INSERT INTO tax_return_versions (return_id, version, inputs, provenance, input_hash, created_at, created_by) "
              "VALUES ('ret_alpha', 2, '{}', '{}', 'h', 'now', 'm')")


def test_api_scopes_client_users_to_their_business():
    from agentledger.security.platform import client_scope

    assert client_scope({"role": "client", "client_id": "alpha"}) == ["alpha"]
    assert client_scope({"role": "client", "client_id": None}) == []        # an unlinked client user sees nothing
    assert client_scope({"role": "cpa"}) == ["*"]


# --------------------------------------------------------------------------- 3. exact money in receipt matching
@pytest.mark.parametrize("amount", ["19.99", "1234.56", "0.10", "98765.43"])
def test_receipt_links_to_expense_with_cents(biz, amount):
    from agentledger.intake.pipeline import link_receipt

    conn = biz.conn
    if "6100" not in store.accounts(conn, "acme"):
        store.add_account(conn, "acme", "6100", "Office supplies", "expense")
    eid = store.post(conn, "acme", date(2026, 3, 1), "supplies", [Line("6100", Decimal(amount)), Line("1000", -Decimal(amount))],
                     source="t", actor="t")
    conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, fields) "
                 "VALUES ('rcpt', 'acme', ?, 'r.jpg', 'image/jpeg', 'upload', '2026-03-02T00:00:00+00:00', 'filed', '{}')",
                 (secrets.token_hex(16),))
    assert link_receipt(conn, "acme", "rcpt", Decimal(amount), "2026-03-02") == eid
    assert link_receipt(conn, "acme", "rcpt", Decimal(amount) + Decimal("0.01"), "2026-03-02") is None


# --------------------------------------------------------------------------- 4. durable provisioning
class _Catalog:
    """A connection that answers the catalog lookups made before a firm's evidence is destroyed: the databases of the
    fake Neon project exist; no schema does, since the fake never runs migrations."""

    def __init__(self, dbs):
        self.dbs = dbs

    def execute(self, sql, params=()):
        found = "pg_database" in sql and bool(params) and params[0] in self.dbs
        return type("Result", (), {"fetchone": lambda self: (1,) if found else None})()

    def close(self):
        pass


@pytest.fixture
def neon_env(tmp_path, monkeypatch):
    from test_firm_lifecycle import FakeNeon

    from agentledger.pg import provision
    from agentledger.security.platform import Platform

    monkeypatch.setenv("AGENTLEDGER_DATABASE", "postgres")
    monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "database")
    monkeypatch.setenv("AGENTLEDGER_MIGRATION_URL", "postgresql://owner:pw@ep-x.neon.tech/neondb")
    monkeypatch.setenv("AGENTLEDGER_RUNTIME_DATABASE_URL", "postgresql://ignored@ep-x.neon.tech/neondb")
    monkeypatch.setenv("NEON_BRANCH", "dev")
    monkeypatch.delenv("NEON_BRANCH_ID", raising=False)
    monkeypatch.delenv("AGENTLEDGER_PG_SCHEMA_PREFIX", raising=False)
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)
    fake = FakeNeon()
    client = httpx.Client(base_url=provision.NEON_API, transport=httpx.MockTransport(fake), headers={"Authorization": "Bearer k"})
    neon = provision.Neon(key="k", project="p", client=client)
    monkeypatch.setattr(provision, "Neon", lambda: neon)
    migrations = {"calls": 0, "fail": 0}

    def migrate_store(url, schema, own):
        migrations["calls"] += 1
        if migrations["fail"]:
            migrations["fail"] -= 1
            raise RuntimeError("migration 0002 failed: connection reset")

    monkeypatch.setattr(provision, "_migrate_store", migrate_store)
    removed_roles = []
    monkeypatch.setattr(provision, "drop_role", lambda owner, role: removed_roles.append(role))
    monkeypatch.setattr(provision, "connect", lambda url: _Catalog(fake.dbs))
    return Platform(tmp_path, dev=True), fake, neon, migrations, removed_roles


def test_migration_failure_after_creation_resumes_on_retry(neon_env):
    plat, fake, neon, migrations, _ = neon_env
    migrations["fail"] = 1
    with pytest.raises(RuntimeError, match="migration 0002 failed"):
        plat.create_firm("rivera-cpa", "Rivera CPA", by="ops")
    assert plat.firm("rivera-cpa")["status"] == "provisioning"           # unusable until its store is ready
    assert plat.get("rivera-cpa")["state"] == "created" and "al_rivera_cpa" in fake.dbs
    creates = sum(1 for m, p in fake.calls if m == "POST")
    plat.create_firm("rivera-cpa", "Rivera CPA", by="ops")               # the same call retries, no "already exists"
    assert plat.firm("rivera-cpa")["status"] == "active" and plat.get("rivera-cpa")["state"] == "ready"
    assert sum(1 for m, p in fake.calls if m == "POST") == creates == 1     # created once, migrated on the retry
    assert migrations["calls"] == 2


def test_timeout_after_creation_is_resumed_not_duplicated(neon_env):
    plat, fake, neon, migrations, _ = neon_env
    neon.timeout = 0
    fake.stall = True               # Neon accepted the database, but its operation never reports finished in time
    with pytest.raises(Exception, match="did not finish"):
        plat.create_firm("lake-tax", "Lake Tax", by="ops")
    assert plat.get("lake-tax")["state"] == "creating" and "al_lake_tax" in fake.dbs
    fake.stall = False
    neon.timeout = 30
    plat.create_firm("lake-tax", "Lake Tax", by="ops")
    assert plat.firm("lake-tax")["status"] == "active"
    assert sum(1 for m, p in fake.calls if m == "POST") == 1


def test_existing_database_without_a_record_is_never_adopted(neon_env):
    plat, fake, _, _, _ = neon_env
    fake.dbs.add("al_ortiz_auto")                                          # someone else's, or a leftover
    with pytest.raises(Exception, match="refusing to adopt"):
        plat.create_firm("ortiz-auto", "Ortiz Auto", by="ops")
    assert "al_ortiz_auto" in fake.dbs                                     # and never deleted either
    assert plat.abandon_firm("ortiz-auto", by="ops") == "nothing recorded"
    assert "al_ortiz_auto" in fake.dbs


def test_concurrent_retry_is_refused_while_another_holds_the_lease(neon_env):
    from agentledger.pg import provision

    plat, _, _, _, _ = neon_env
    assert plat.claim("busy-cpa", "other-process", 600)
    with pytest.raises(provision.ProvisioningBusy):
        provision.provision("busy-cpa", plat)
    plat.release("busy-cpa", "other-process")


def test_abandon_removes_only_what_provisioning_created(neon_env):
    plat, fake, _, migrations, removed_roles = neon_env
    migrations["fail"] = 1
    with pytest.raises(RuntimeError):
        plat.create_firm("gone-cpa", "Gone CPA", by="ops")
    fake.dbs.add("al_unrelated")
    detail = plat.abandon_firm("gone-cpa", by="ops")
    assert "al_gone_cpa deleted" in detail and "al_gone_cpa" not in fake.dbs and "al_unrelated" in fake.dbs
    assert removed_roles == ["rt_al_gone_cpa_agentledger"]
    assert plat.firm("gone-cpa")["status"] == "deleted" and plat.get("gone-cpa")["state"] == "removed"
    with pytest.raises(Exception):
        plat.create_firm("gone-cpa", "Gone CPA", by="ops")                 # a retired id is never reused


# --------------------------------------------------------------------------- re-audit of 2b42c07: no owner credentials in the API
def test_api_without_owner_credentials_queues_provisioning_for_the_worker(neon_env, monkeypatch):
    plat, fake, _, migrations, _ = neon_env
    owner = "postgresql://owner:pw@ep-x.neon.tech/neondb"
    for var in ("AGENTLEDGER_MIGRATION_URL", "DATABASE_URL_UNPOOLED", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    firm = plat.create_firm("queued-cpa", "Queued CPA", by="ops")          # what the production API does
    assert firm["status"] == "provisioning" and not [c for c in fake.calls if c[0] == "POST"]
    assert any(e["event"] == "firm_store_provisioning_queued" for e in plat.events("queued-cpa"))
    monkeypatch.setenv("AGENTLEDGER_MIGRATION_URL", owner)                 # the provisioning worker's environment
    assert plat.provision_pending(by="worker") == [{"firm": "queued-cpa", "status": "active"}]
    assert plat.firm("queued-cpa")["status"] == "active" and migrations["calls"] == 1
