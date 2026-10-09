"""Second adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv2_holds_legacy_dev_firm.py): reserving
"dev" stopped new firms from taking it, but a firm "dev" created before the reservation could still be deleted, and
its object-store stage deleted everything under firms/dev/, where the single-firm store keeps its evidence, including
bytes under an active legal hold there. Asserted now: delete_firm, destroy_firm_data and abandon_firm refuse every
reserved id ("reserved id", removed by hand after checking the single-firm store's holds) before anything is sealed,
staged or removed, so the single-firm store's held object stays stored and readable, its hold active, and the legacy
firm's store and key are untouched.
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
from agentledger.security.platform import PLATFORM_FIRM, RESERVED_FIRM_IDS, AuthError, Platform
from agentledger.security.vault import Vault

OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
REASON = "IRS examination notice dated 2026-09-30: preserve all 2026 records"
W2 = b"Form W-2 2026 Employer: Brightline LLC Employee: Jordan Lee SSN 400-00-0001 Wages 61,200.00"
RESERVED = "is a reserved id \\(the single-firm store's objects share its prefix\\)"


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
    if db.backend() == "postgres":
        monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")
    monkeypatch.setenv("AGENTLEDGER_BLOBS", "s3")
    monkeypatch.setenv("AGENTLEDGER_BLOB_PREFIX", "")
    real_s3 = blobstore.S3Blobs
    monkeypatch.setattr(blobstore, "S3Blobs", lambda **kw: real_s3(bucket="evidence", prefix=kw.get("prefix", ""), client=b))
    return b


def _legacy_firm(plat, firm_id: str, status: str) -> None:
    """A firm row with a reserved id, as the release before the reservation created it (with its key and store)."""
    plat.conn.execute("INSERT INTO firms (id, name, status) VALUES (?, 'Dev Partners CPA', ?)", (firm_id, status))
    if firm_id == PLATFORM_FIRM:
        return                                                         # its key is the platform's own
    plat.keys.create(firm_id)
    if db.backend() == "postgres":
        from agentledger.pg import provision

        provision.provision(firm_id, plat)
    else:
        db.connect(plat.tenant_dir(firm_id) / "state" / "agentledger.db").close()


def _key_alive(plat, firm_id: str) -> bool:
    try:
        Keyring(plat.conn, plat.keys.master).current_version(firm_id)
        return True
    except CryptoError:
        return False


def test_legacy_firm_dev_is_never_removed_with_the_single_firm_stores_held_objects(tmp_path, bucket):
    root_store = db.open_store(tmp_path / "state" / "agentledger.db")       # the single-firm store
    try:
        store.add_client(root_store, id="jordan-lee", name="Jordan Lee", kind="individual")
        vault = Vault(tmp_path / "vault")
        loc = vault.put(W2, owner="doc_w2")
        sha = hashlib.sha256(W2).hexdigest()
        root_store.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, "
                           "status, vault_path, doc_type, tax_year) VALUES ('doc_w2', 'jordan-lee', ?, 'w2-2026.txt', "
                           "'text/plain', 'cli', ?, 'filed', ?, 'W-2', 2026)", (sha, audit.now(), loc))
        records.add_version(root_store, "doc_w2", loc, sha, len(W2), "intake-agent")
        hold = records.place_hold(root_store, client_id="jordan-lee", reason=REASON, actor="u_lee", role="cpa")
    finally:
        root_store.close()
    held_key = "firms/dev/" + loc.removeprefix("blob:")
    plat = Platform(tmp_path, dev=True)
    _legacy_firm(plat, "dev", "active")
    assert list(bucket.objects) == [held_key]

    with pytest.raises(AuthError, match=f"'dev' {RESERVED}"):
        plat.delete_firm("dev", by="ops", reason=OFFBOARD)
    assert plat.firm("dev")["status"] == "active"
    for status, remove in (("deleted", lambda: plat.destroy_firm_data("dev", by="ops")),
                           ("provisioning", lambda: plat.abandon_firm("dev", by="ops"))):
        plat.conn.execute("UPDATE firms SET status = ? WHERE id = 'dev'", (status,))     # the state each one accepts
        with pytest.raises(AuthError, match=f"'dev' {RESERVED}"):
            remove()
        assert plat.firm("dev")["status"] == status

    assert list(bucket.objects) == [held_key] and vault.read(loc) == W2
    root_store = db.open_store(tmp_path / "state" / "agentledger.db")
    try:
        assert [h["id"] for h in records.active_holds(root_store)] == [hold]
    finally:
        root_store.close()
    assert _key_alive(plat, "dev") and db.store_exists(plat.tenant_dir("dev") / "state" / "agentledger.db")
    assert not plat._stages("dev") and plat.offboarding_records("dev") == []
    if db.backend() == "postgres":
        assert plat.get("dev")["state"] == "ready"                     # nothing removed or abandoned


@pytest.mark.parametrize("firm_id", sorted(RESERVED_FIRM_IDS))
@pytest.mark.parametrize("status, call", [("active", "delete_firm"), ("deleted", "destroy_firm_data"),
                                          ("provisioning", "abandon_firm")])
def test_every_reserved_id_is_refused_by_every_removal(tmp_path, firm_id, status, call):
    plat = Platform(tmp_path, dev=True)
    _legacy_firm(plat, firm_id, status)
    kwargs = {"reason": OFFBOARD} if call == "delete_firm" else {}

    with pytest.raises(AuthError, match=f"{firm_id!r} {RESERVED}"):
        getattr(plat, call)(firm_id, by="ops", **kwargs)

    assert plat.firm(firm_id)["status"] == status and not plat._stages(firm_id)
    assert _key_alive(plat, firm_id)
    assert not [e for e in plat.events(firm_id) if e["event"] in ("firm_offboarding_started", "firm_data_destroyed",
                                                                   "firm_abandoned", "firm_deleted")]
