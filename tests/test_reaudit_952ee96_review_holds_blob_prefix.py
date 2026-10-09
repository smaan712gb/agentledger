"""Adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv_holds_blob_prefix.py): a firm could take the
id "dev", whose objects share the prefix firms/dev/ with the single-firm store's (evidence/blobs.for_firm), and
deleting that firm deleted the single-firm store's evidence under an active legal hold. Asserted now: the reserved ids
are refused as firm ids for that reason, nothing is created for them, and deleting a firm whose id merely starts with
"dev" removes only its own objects; the single-firm store's held object stays stored and readable, its hold active.
"""

from __future__ import annotations

import hashlib
import io

import pytest

from agentledger import audit, db
from agentledger.evidence import blobs as blobstore
from agentledger.evidence import records
from agentledger.ledger import store
from agentledger.security.crypto import CryptoError, Keyring
from agentledger.security.platform import FIRM_ID, RESERVED_FIRM_IDS, AuthError, Platform
from agentledger.security.vault import Vault

OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
REASON = "IRS examination notice dated 2026-09-30: preserve all 2026 records"
W2 = b"Form W-2 2026 Employer: Brightline LLC Employee: Jordan Lee SSN 400-00-0001 Wages 61,200.00"
OTHER = b"Form 1099-NEC 2026 Payer: Dev Partners CPA Recipient: Sam Ortiz Box 1 4,800.00"


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
    """AGENTLEDGER_BLOBS=s3 against an in-memory bucket: every store's objects, the single-firm store's included."""
    b = MemoryS3()
    monkeypatch.setenv("AGENTLEDGER_BLOBS", "s3")
    monkeypatch.setenv("AGENTLEDGER_BLOB_PREFIX", "")
    real_s3 = blobstore.S3Blobs
    monkeypatch.setattr(blobstore, "S3Blobs", lambda **kw: real_s3(bucket="evidence", prefix=kw.get("prefix", ""), client=b))
    return b


def _filed_w2(conn, vault, doc_id: str, data: bytes) -> str:
    loc = vault.put(data, owner=doc_id)
    sha = hashlib.sha256(data).hexdigest()
    conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, "
                 "vault_path, doc_type, tax_year) VALUES (?, 'jordan-lee', ?, 'w2-2026.txt', 'text/plain', 'cli', ?, 'filed', "
                 "?, 'W-2', 2026)", (doc_id, sha, audit.now(), loc))
    records.add_version(conn, doc_id, loc, sha, len(data), "intake-agent")
    return loc


def _single_firm_store_with_held_w2(root):
    """The single-firm store under the platform root (what `agentledger ingest`, the MCP server and the dev profile
    use: a Vault with no firm, so its objects live under firms/dev/), with a filed W-2 under an active hold."""
    conn = db.open_store(root / "state" / "agentledger.db")
    try:
        store.add_client(conn, id="jordan-lee", name="Jordan Lee", kind="individual")
        vault = Vault(root / "vault")
        loc = _filed_w2(conn, vault, "doc_w2", W2)
        hold = records.place_hold(conn, client_id="jordan-lee", reason=REASON, actor="u_lee", role="cpa")
    finally:
        conn.close()
    return vault, loc, hold


def _single_firm_holds(root) -> list[int]:
    conn = db.open_store(root / "state" / "agentledger.db")
    try:
        return [h["id"] for h in records.active_holds(conn)]
    finally:
        conn.close()


def test_firm_id_dev_is_refused_and_the_single_firm_stores_held_object_is_kept(tmp_path, bucket):
    vault, loc, hold = _single_firm_store_with_held_w2(tmp_path)
    held_key = "firms/dev/" + loc.removeprefix("blob:")
    assert list(bucket.objects) == [held_key]

    plat = Platform(tmp_path, dev=True)
    assert "dev" in RESERVED_FIRM_IDS and FIRM_ID.match("dev")      # the id pattern alone would accept it
    with pytest.raises(AuthError, match="not a reserved name"):
        plat.create_firm("dev", "Dev Partners CPA", by="ops")

    with pytest.raises(AuthError, match="firm not found"):          # nothing was created for it ...
        plat.firm("dev")
    with pytest.raises(CryptoError):
        Keyring(plat.conn, plat.keys.master).current_version("dev")
    assert not plat.tenant_dir("dev").exists() and plat.get("dev") is None
    with pytest.raises(AuthError, match="reserved id"):             # ... and a reserved id is never deleted either
        plat.delete_firm("dev", by="ops", reason=OFFBOARD)

    assert list(bucket.objects) == [held_key] and vault.read(loc) == W2
    assert _single_firm_holds(tmp_path) == [hold]


@pytest.mark.parametrize("firm_id", sorted(RESERVED_FIRM_IDS))
def test_every_reserved_id_is_refused(tmp_path, firm_id):
    plat = Platform(tmp_path, dev=True)
    with pytest.raises(AuthError, match="reserved name"):
        plat.create_firm(firm_id, "Reserved", by="ops")
    assert not [e for e in plat.events(firm_id) if e["event"] == "firm_created"]


def test_deleting_a_firm_whose_id_starts_with_dev_removes_only_its_own_objects(tmp_path, bucket):
    vault, loc, hold = _single_firm_store_with_held_w2(tmp_path)
    held_key = "firms/dev/" + loc.removeprefix("blob:")

    plat = Platform(tmp_path, dev=True)
    plat.create_firm("dev-partners", "Dev Partners CPA", by="ops")
    conn = db.open_store(plat.tenant_dir("dev-partners") / "state" / "agentledger.db")
    try:
        store.add_client(conn, id="jordan-lee", name="Jordan Lee", kind="individual")
        own = _filed_w2(conn, Vault(plat.tenant_dir("dev-partners") / "vault", plat.keys, "dev-partners"), "doc_1099", OTHER)
    finally:
        conn.close()
    assert sorted(bucket.objects) == sorted([held_key, "firms/dev-partners/" + own.removeprefix("blob:")])

    plat.delete_firm("dev-partners", by="ops", reason=OFFBOARD)

    assert plat.firm("dev-partners")["status"] == "deleted"
    assert list(bucket.objects) == [held_key] and vault.read(loc) == W2
    assert _single_firm_holds(tmp_path) == [hold]
