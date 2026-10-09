"""Second adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv2_holds_destroy_unrecorded.py): an
offboarding interrupted after its store was removed but before 'store_removed' was recorded could never be finished
(each retry sealed again and found no store), leaving the firm's objects and data key behind. Asserted now:
'store_removing' is recorded before the store is touched, so after such an interruption (the process killed, the
platform store busy, on PostgreSQL the runtime role's drop failing after the schema's) cancel_offboarding refuses to
put the firm back in use without its store, a killed run's lease keeps a retry out until it expires, and a retry
finishes the removal (the missing store reported as already removed), deleting the objects, shredding the key and
marking the firm deleted.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3

import pytest

from agentledger import audit, db
from agentledger.evidence import blobs as blobstore
from agentledger.evidence import offboarding, records
from agentledger.ledger import store
from agentledger.security.crypto import CryptoError, Keyring
from agentledger.security.platform import AuthError, Platform
from agentledger.security.vault import Vault

FIRM, CLIENT = "unrecorded-cpa", "jordan-lee"
OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
CANCEL = "subpoena received 2026-10-02: the firm's records must be preserved"
W2 = b"Form W-2 2026 Employer: Brightline LLC Employee: Jordan Lee SSN 400-00-0001 Wages 61,200.00"


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


class Killed(BaseException):
    """The process stops (container restart, OOM kill): no exception handler of the code under test runs."""


@pytest.fixture
def bucket(monkeypatch):
    b = MemoryS3()
    if db.backend() == "postgres":
        monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")
    monkeypatch.setenv("AGENTLEDGER_BLOBS", "s3")
    monkeypatch.setattr(blobstore, "for_firm",
                        lambda root, firm_id: blobstore.S3Blobs(bucket="evidence", prefix=f"firms/{firm_id}", client=b))
    return b


def _firm_with_w2(tmp_path):
    plat = Platform(tmp_path, dev=True)
    plat.create_firm(FIRM, "Unrecorded CPA", by="ops")
    path = plat.tenant_dir(FIRM) / "state" / "agentledger.db"
    conn = db.open_store(path)
    try:
        store.add_client(conn, id=CLIENT, name="Jordan Lee", kind="individual")
        loc = Vault(plat.tenant_dir(FIRM) / "vault", plat.keys, FIRM).put(W2, owner="doc_w2")
        sha = hashlib.sha256(W2).hexdigest()
        conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, "
                     "vault_path, doc_type, tax_year) VALUES ('doc_w2', ?, ?, 'w2-2026.txt', 'text/plain', 'upload', ?, 'filed', "
                     "?, 'W-2', 2026)", (CLIENT, sha, audit.now(), loc))
        records.add_version(conn, "doc_w2", loc, sha, len(W2), "intake-agent")
    finally:
        conn.close()
    return plat, path


# How each interruption is injected (inside a monkeypatch context); returns the exception delete_firm raises.
def _killed(m):
    real_destroy = offboarding.destroy

    def removed_then_killed(ref, **kw):
        real_destroy(ref, **kw)
        raise Killed("the process stopped after the store was removed")

    m.setattr(offboarding, "destroy", removed_then_killed)
    m.setattr(Platform, "_release_run", lambda self, firm_id, holder: None)    # a dead process never releases its lease
    return Killed


def _platform_store_busy(m):
    real_stage = Platform._stage

    def stage(self, firm_id, name, detail=""):
        if name == "store_removed":
            raise sqlite3.OperationalError("database is locked")
        return real_stage(self, firm_id, name, detail)

    m.setattr(Platform, "_stage", stage)
    return sqlite3.OperationalError


def _runtime_role_drop_fails(m):
    if db.backend() != "postgres":
        pytest.skip("the runtime role is PostgreSQL's")
    from agentledger import pg

    def drop_role(owner, role):                          # DROP SCHEMA has committed; the role's drop fails
        raise ConnectionError(f"connection lost while dropping {role}")

    m.setattr(pg, "drop_role", drop_role)
    return ConnectionError


def _key_alive(plat) -> bool:
    try:
        Keyring(plat.conn, plat.keys.master).current_version(FIRM)
        return True
    except CryptoError:
        return False


@pytest.mark.parametrize("interrupt", [_killed, _platform_store_busy, _runtime_role_drop_fails],
                         ids=["killed", "platform-store-busy", "runtime-role-drop-fails"])
def test_offboarding_interrupted_after_the_store_was_removed_is_finished_by_a_retry(tmp_path, monkeypatch, bucket, interrupt):
    plat, path = _firm_with_w2(tmp_path)
    with monkeypatch.context() as m:
        raised = interrupt(m)
        with pytest.raises(raised):
            plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    assert not db.store_exists(path) and plat._stages(FIRM) == {"sealed", "store_removing"}
    assert plat.firm(FIRM)["status"] == "offboarding" and len(bucket.objects) == 1 and _key_alive(plat)

    if interrupt is _killed:                             # the dead run's lease is live: nothing else runs meanwhile
        with pytest.raises(AuthError, match="an offboarding run of this firm is already in progress"):
            plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
        with pytest.raises(AuthError, match="being removed by a run in progress; the offboarding can only be finished"):
            plat.cancel_offboarding(FIRM, by="ops", reason=CANCEL)
        plat.conn.execute("UPDATE offboarding_runs SET lease_until = '2000-01-01T00:00:00+00:00' WHERE firm_id = ?", (FIRM,))
    with pytest.raises(AuthError, match="partly removed; the offboarding can only be finished"):
        plat.cancel_offboarding(FIRM, by="ops", reason=CANCEL)        # never back into use without its store
    with pytest.raises(AuthError, match="only a deleted firm's data can be destroyed"):
        plat.destroy_firm_data(FIRM, by="ops")
    assert plat.firm(FIRM)["status"] == "offboarding" and plat._stages(FIRM) == {"sealed", "store_removing"}

    plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)                 # the retry resumes at the removal
    removed = plat.conn.execute("SELECT detail FROM firm_destruction WHERE firm_id = ? AND stage = 'store_removed'",
                                (FIRM,)).fetchone()["detail"]
    assert removed.startswith("no schema ") if db.backend() == "postgres" else removed.endswith("(already removed)"), removed
    assert plat.firm(FIRM)["status"] == "deleted" and not _key_alive(plat) and not bucket.objects
    assert plat._stages(FIRM) == {"sealed", "store_removing", "store_removed", "blobs_removed", "tenant_removed", "key_shredded"}
    assert not plat.tenant_dir(FIRM).exists() and not db.store_exists(path)
    assert [r["reason"] for r in plat.offboarding_records(FIRM)] == [OFFBOARD]
    assert plat.destroy_firm_data(FIRM, by="ops") == "already destroyed"
    if db.backend() == "postgres":                                    # the runtime role went with the store
        from agentledger import pg

        role = pg.runtime_role(plat.get(FIRM)["database"], plat.get(FIRM)["name"])
        owner = pg.connect(pg.migration_url())
        try:
            assert not owner.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        finally:
            owner.close()
