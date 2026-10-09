"""Second adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv2_holds_blob_location_from_environment.py):
the firm's objects were located from the offboarding job's environment, so a job without the object-store settings
recorded 'blobs_removed' ("no object store") and marked the firm deleted while every object stayed in the bucket.
Asserted now: where the firm's objects live is recorded when the firm is created, and a job configured for another
place (no object store, another bucket, another prefix) is refused at the object-store stage naming the recorded
place: the objects are not recorded as removed, they and the data key stay, and the firm is not marked deleted; a job
with the recorded settings then finishes the offboarding, removing exactly those objects.
"""

from __future__ import annotations

import hashlib
import io
import re

import pytest

from agentledger import audit, db
from agentledger.evidence import blobs as blobstore
from agentledger.evidence import records
from agentledger.ledger import store
from agentledger.security.crypto import CryptoError, Keyring
from agentledger.security.platform import AuthError, Platform
from agentledger.security.vault import Vault

FIRM, CLIENT = "leftover-cpa", "jordan-lee"
OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
CANCEL = "the firm's owner withdrew the offboarding request on 2026-10-02"
W2 = b"Form W-2 2026 Employer: Brightline LLC Employee: Jordan Lee SSN 400-00-0001 Wages 61,200.00"
RECORDED = f"s3:evidence/firms/{FIRM}/"


class MemoryS3:
    """The S3 calls S3Blobs makes, against in-memory buckets (stands in for R2): objects keyed by (bucket, key)."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType=None):
        self.objects[(Bucket, Key)] = bytes(Body)

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def head_object(self, *, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def list_objects_v2(self, *, Bucket, Prefix="", ContinuationToken=None):
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}


@pytest.fixture
def bucket(monkeypatch):
    """The API's environment: evidence in the bucket 'evidence' (bucket and prefix from the settings, as in production)."""
    b = MemoryS3()
    if db.backend() == "postgres":
        monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")
    monkeypatch.setenv("AGENTLEDGER_BLOBS", "s3")
    monkeypatch.setenv("AGENTLEDGER_BLOB_BUCKET", "evidence")
    monkeypatch.setenv("AGENTLEDGER_BLOB_PREFIX", "")
    real_s3 = blobstore.S3Blobs
    monkeypatch.setattr(blobstore, "S3Blobs", lambda **kw: real_s3(prefix=kw.get("prefix", ""), client=b))
    return b


def _key_alive(plat) -> bool:
    try:
        Keyring(plat.conn, plat.keys.master).current_version(FIRM)
        return True
    except CryptoError:
        return False


def _firm_with_w2(tmp_path):
    plat = Platform(tmp_path, dev=True)
    plat.create_firm(FIRM, "Leftover CPA", by="ops")
    conn = db.open_store(plat.tenant_dir(FIRM) / "state" / "agentledger.db")
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
    return plat, ("evidence", f"firms/{FIRM}/{loc.removeprefix('blob:')}")


@pytest.mark.parametrize("job, configured", [
    ({"AGENTLEDGER_BLOBS": None}, "file"),
    ({"AGENTLEDGER_BLOB_BUCKET": "evidence-staging"}, f"s3:evidence-staging/firms/{FIRM}/"),
    ({"AGENTLEDGER_BLOB_PREFIX": "staging/"}, f"s3:evidence/staging/firms/{FIRM}/"),
], ids=["no-object-store", "another-bucket", "another-prefix"])
def test_job_configured_for_another_object_store_is_refused_and_the_recorded_one_finishes(tmp_path, monkeypatch, bucket,
                                                                                         job, configured):
    plat, obj = _firm_with_w2(tmp_path)
    path = plat.tenant_dir(FIRM) / "state" / "agentledger.db"
    assert plat.firm(FIRM)["blob_location"] == RECORDED and list(bucket.objects) == [obj]

    with monkeypatch.context() as m:                                  # the offboarding job's environment
        for var, value in job.items():
            if value is None:
                m.delenv(var)
            else:
                m.setenv(var, value)
        with pytest.raises(AuthError, match=re.escape(f"objects are in {RECORDED}, but this job is configured for {configured}: "
                                                      "configure that object store and retry")):
            plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)

    assert plat._stages(FIRM) == {"sealed", "store_removing", "store_removed"}     # 'blobs_removed' is not claimed
    assert list(bucket.objects) == [obj] and _key_alive(plat)
    assert plat.firm(FIRM)["status"] == "offboarding" and not db.store_exists(path)
    failed = [e for e in plat.events(FIRM) if e["event"] == "firm_data_destroy_failed"]
    assert failed and RECORDED in failed[0]["detail"] and "firm_deleted" not in [e["event"] for e in plat.events(FIRM)]
    with pytest.raises(AuthError, match="already removed; the offboarding can only be finished"):
        plat.cancel_offboarding(FIRM, by="ops", reason=CANCEL)

    plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)                 # a job with the recorded settings finishes it
    detail = plat.conn.execute("SELECT detail FROM firm_destruction WHERE firm_id = ? AND stage = 'blobs_removed'",
                               (FIRM,)).fetchone()["detail"]
    assert detail == "1 object(s) deleted from the object store" and not bucket.objects
    assert plat.firm(FIRM)["status"] == "deleted" and not _key_alive(plat)
    assert plat._stages(FIRM) >= {"store_removed", "blobs_removed", "tenant_removed", "key_shredded"}



