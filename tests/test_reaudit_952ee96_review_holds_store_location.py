"""Adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv_holds_store_location.py): the hold check
re-derived the store's location from the offboarding job's environment and read "no store there" as "no holds", so a
firm provisioned with a database of its own, offboarded where AGENTLEDGER_PG_TENANCY was unset (and a SQLite firm
whose tenants volume was not mounted), lost its held evidence's object-store copy and data key. Asserted now: the
store is located from the platform's records (the provisioning journal, the tenant directory), whatever the job's
environment says, so the hold is found and refuses the deletion by name; and a store that is not where the records
say is refused as not found. Either way the store, the hold, the object and the key are intact and the firm in use.
"""

from __future__ import annotations

import hashlib
import io
import shutil

import pytest

from agentledger import audit, db
from agentledger.evidence import blobs as blobstore
from agentledger.evidence import records
from agentledger.ledger import store
from agentledger.security.crypto import CryptoError, Keyring
from agentledger.security.platform import AuthError, Platform
from agentledger.security.vault import Vault

FIRM, CLIENT = "drift-cpa", "jordan-lee"
OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
REASON = "IRS examination notice dated 2026-09-30: preserve all 2026 records"
W2 = b"Form W-2 2026 Employer: Brightline LLC Employee: Jordan Lee SSN 400-00-0001 Wages 61,200.00"
needs_pg = pytest.mark.skipif(db.backend() != "postgres", reason="needs AGENTLEDGER_DATABASE=postgres")


class MemoryS3:
    """The S3 calls S3Blobs makes, against one in-memory bucket (stands in for R2)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = bytes(Body)

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)

    def list_objects_v2(self, *, Bucket, Prefix="", ContinuationToken=None):
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)], "IsTruncated": False}


@pytest.fixture
def bucket(monkeypatch):
    b = MemoryS3()
    monkeypatch.setenv("AGENTLEDGER_BLOBS", "s3")
    monkeypatch.setattr(blobstore, "for_firm",
                        lambda root, firm_id: blobstore.S3Blobs(bucket="evidence", prefix=f"firms/{firm_id}", client=b))
    return b


def _held_w2(plat, path):
    """A client with a filed W-2 (bytes in the object store) under an active client-wide hold."""
    conn = db.open_store(path)
    try:
        store.add_client(conn, id=CLIENT, name="Jordan Lee", kind="individual")
        loc = Vault(plat.tenant_dir(FIRM) / "vault", plat.keys, FIRM).put(W2, owner="doc_w2")
        sha = hashlib.sha256(W2).hexdigest()
        conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, "
                     "vault_path, doc_type, tax_year) VALUES ('doc_w2', ?, ?, 'w2-2026.txt', 'text/plain', 'upload', ?, 'filed', ?, "
                     "'W-2', 2026)", (CLIENT, sha, audit.now(), loc))
        records.add_version(conn, "doc_w2", loc, sha, len(W2), "intake-agent")
        return records.place_hold(conn, client_id=CLIENT, reason=REASON, actor="u_lee", role="cpa")
    finally:
        conn.close()


def _key_alive(plat) -> bool:
    try:
        Keyring(plat.conn, plat.keys.master).current_version(FIRM)
        return True
    except CryptoError:
        return False


def _untouched(plat, bucket) -> None:
    assert len(bucket.objects) == 1 and _key_alive(plat)
    assert plat.firm(FIRM)["status"] == "active" and plat.offboarding_records(FIRM) == [] and not plat._stages(FIRM)
    assert "firm_delete_refused" in [e["event"] for e in plat.events(FIRM)]


def _pg_holds(database: str, schema: str) -> list[int]:
    """The active holds, read as the owner straight from where the store is (no environment-derived location)."""
    from psycopg import sql

    from agentledger import pg

    c = pg.connect(pg.with_database(pg.migration_url(), database))
    try:
        return [r[0] for r in c.execute(sql.SQL("SELECT id FROM {}.legal_holds WHERE released_at IS NULL ORDER BY id")
                                        .format(sql.Identifier(schema))).fetchall()]
    finally:
        c.close()


# --------------------------------------------------------------------------------------------- PostgreSQL
@pytest.fixture
def local_neon(monkeypatch):
    """Neon's database API carried out on the local server (no network): CREATE / DROP DATABASE. The databases it
    creates are dropped afterwards."""
    from agentledger import pg
    from agentledger.pg import compat, provision

    owner_url = pg.migration_url()
    created: list[str] = []

    class LocalNeon:
        def databases(self):
            c = pg.connect(owner_url)
            try:
                return [r[0] for r in c.execute("SELECT datname FROM pg_database").fetchall()]
            finally:
                c.close()

        def create_database(self, name, owner):
            c = pg.connect(owner_url)
            try:
                c.execute(f'CREATE DATABASE "{name}" OWNER "{owner}"')
                created.append(name)
            finally:
                c.close()

        def delete_database(self, name):
            c = pg.connect(owner_url)
            try:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                c.close()

    monkeypatch.setattr(provision, "Neon", LocalNeon)
    yield created
    for s in list(db._OPEN):
        if isinstance(s, compat.PgStore) and s.database in created:
            s.close()
    c = pg.connect(owner_url)
    try:
        for name in created:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        c.close()


@needs_pg
def test_database_store_is_found_from_the_journal_when_the_job_has_no_tenancy_setting(tmp_path, monkeypatch, bucket, local_neon):
    from agentledger.pg import provision

    monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "database")             # production: a database per firm
    name = provision.firm_database(FIRM)
    plat = Platform(tmp_path, dev=True)
    plat.create_firm(FIRM, "Drift CPA", by="ops")
    journal = plat.get(FIRM)
    assert (journal["resource"], journal["name"], journal["database"], journal["state"]) == ("database", name, name, "ready")
    hold = _held_w2(plat, plat.tenant_dir(FIRM) / "state" / "agentledger.db")
    assert _pg_holds(name, provision.FIRM_SCHEMA) == [hold]

    monkeypatch.delenv("AGENTLEDGER_PG_TENANCY")                          # the offboarding job's environment: 'schema'
    with pytest.raises(AuthError, match=rf"active legal hold.*#{hold} \({CLIENT}\)"):
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)

    assert plat.get(FIRM)["state"] == "ready" and name in local_neon
    assert _pg_holds(name, provision.FIRM_SCHEMA) == [hold]
    _untouched(plat, bucket)


@needs_pg
@pytest.mark.parametrize("var", ["AGENTLEDGER_PG_TENANCY", "AGENTLEDGER_RUNTIME_DATABASE_URL", "AGENTLEDGER_PG_SCHEMA_PREFIX"])
def test_schema_store_is_found_from_the_journal_when_the_job_environment_differs(tmp_path, monkeypatch, bucket, var):
    from agentledger import pg

    monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")
    plat = Platform(tmp_path, dev=True)
    plat.create_firm(FIRM, "Drift CPA", by="ops")
    journal = plat.get(FIRM)
    assert journal["resource"] == "schema" and journal["database"] == pg.database_of(pg.runtime_base_url())
    hold = _held_w2(plat, plat.tenant_dir(FIRM) / "state" / "agentledger.db")

    drift = {"AGENTLEDGER_PG_TENANCY": "database",
             "AGENTLEDGER_RUNTIME_DATABASE_URL": pg.with_database(pg.migration_url(), "postgres"),
             "AGENTLEDGER_PG_SCHEMA_PREFIX": "elsewhere_"}
    monkeypatch.setenv(var, drift[var])                                   # how the offboarding job's environment differs
    with pytest.raises(AuthError, match=rf"active legal hold.*#{hold} \({CLIENT}\)"):
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)

    assert _pg_holds(journal["database"], journal["name"]) == [hold]
    _untouched(plat, bucket)


@needs_pg
def test_store_missing_where_the_journal_says_is_refused_as_not_found(tmp_path, monkeypatch, bucket):
    """As when the job reaches a server (or Neon branch) where the recorded database does not exist."""
    monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")
    plat = Platform(tmp_path, dev=True)
    plat.create_firm(FIRM, "Drift CPA", by="ops")
    journal = plat.get(FIRM)
    hold = _held_w2(plat, plat.tenant_dir(FIRM) / "state" / "agentledger.db")
    plat.put(FIRM, database="al_no_such_database")

    with pytest.raises(AuthError, match=r"not where the platform's records say \(database al_no_such_database"):
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)

    assert _pg_holds(journal["database"], journal["name"]) == [hold]
    _untouched(plat, bucket)
    assert any("store not found" in (e["detail"] or "") for e in plat.events(FIRM))


# --------------------------------------------------------------------------------------------- SQLite
@pytest.mark.skipif(db.backend() == "postgres", reason="the SQLite variant")
def test_sqlite_store_not_mounted_is_refused_as_not_found(tmp_path, bucket):
    plat = Platform(tmp_path, dev=True)
    plat.create_firm(FIRM, "Drift CPA", by="ops")
    path = plat.tenant_dir(FIRM) / "state" / "agentledger.db"
    hold = _held_w2(plat, path)

    tenants, unmounted = tmp_path / "tenants", tmp_path / "tenants-volume"
    shutil.move(str(tenants), str(unmounted))          # the tenants volume is not mounted when the job runs
    try:
        with pytest.raises(AuthError, match=r"not where the platform's records say .*does not exist"):
            plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    finally:
        shutil.move(str(unmounted), str(tenants))      # mounted again: the store and its hold are intact

    conn = db.open_store(path)
    try:
        assert [h["id"] for h in records.active_holds(conn)] == [hold]
    finally:
        conn.close()
    _untouched(plat, bucket)
