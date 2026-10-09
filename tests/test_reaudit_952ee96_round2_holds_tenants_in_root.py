"""Second adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv2_holds_tenants_in_root.py): with the
platform root inside a directory named "tenants", db.firm_of took the component after the first "tenants" in a path,
so the API kept every firm's legal holds in one store (firm_agentledger) while offboarding sealed and checked the
journal's firm_<id>, found no hold and destroyed the held evidence's object-store copy and data key. Asserted now: the
platform refuses such a root before creating anything, a store path is read from its end (tenants/<firm>/state/...),
and the store the API opens for a firm is the one offboarding seals, so a hold placed through it refuses the deletion
by name with the store, the hold, the object and the key intact and the firm in use.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from agentledger import audit, db
from agentledger.evidence import blobs as blobstore
from agentledger.evidence import records
from agentledger.ledger import store
from agentledger.security.crypto import CryptoError, Keyring
from agentledger.security.platform import AuthError, Platform
from agentledger.security.vault import Vault

FIRM, CLIENT = "rooted-cpa", "jordan-lee"
OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
REASON = "IRS examination notice dated 2026-09-30: preserve all 2026 records"
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


@pytest.fixture
def bucket(monkeypatch):
    b = MemoryS3()
    if db.backend() == "postgres":
        monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")
    monkeypatch.setenv("AGENTLEDGER_BLOBS", "s3")
    monkeypatch.setattr(blobstore, "for_firm",
                        lambda root, firm_id: blobstore.S3Blobs(bucket="evidence", prefix=f"firms/{firm_id}", client=b))
    return b


def _key_alive(plat) -> bool:
    try:
        Keyring(plat.conn, plat.keys.master).current_version(FIRM)
        return True
    except CryptoError:
        return False


@pytest.mark.parametrize("layout", [("tenants", "agentledger"), ("srv", "tenants", "hosting", "agentledger")],
                         ids=["root-in-tenants", "tenants-higher-up"])
def test_platform_root_inside_a_directory_named_tenants_is_refused_before_anything_is_created(tmp_path, layout):
    root = tmp_path.joinpath(*layout)                                  # e.g. /srv/tenants/agentledger
    with pytest.raises(ValueError, match="must not lie inside a directory named 'tenants'"):
        Platform(root, dev=True)
    assert not root.exists()                                          # no platform store, no master key file


def test_store_paths_are_read_from_their_end():
    hosted = Path("srv") / "tenants" / "agentledger" / "tenants" / FIRM / "state" / "agentledger.db"
    assert db.firm_of(hosted) == FIRM
    assert db.schema_for(hosted) == db.pg_name(f"firm_{FIRM}") == db.schema_for(Path("tenants") / FIRM / "state" / "agentledger.db")
    assert db.schema_for(Path("srv") / "platform" / "state" / "agentledger.db") == db.pg_name("agentledger")


def test_hold_placed_through_the_api_store_refuses_the_offboarding(tmp_path, bucket):
    plat = Platform(tmp_path / "platform", dev=True)
    plat.create_firm(FIRM, "Rooted CPA", by="ops")
    api = db.open_store(plat.tenant_dir(FIRM).resolve() / "state" / "agentledger.db")    # as AppContext.open does
    try:
        if db.backend() == "postgres":                                # the store the journal records, not another one
            journal = plat.get(FIRM)
            assert (journal["resource"], journal["name"], journal["database"]) == ("schema", api.schema, api.database)
        store.add_client(api, id=CLIENT, name="Jordan Lee", kind="individual")
        loc = Vault(plat.tenant_dir(FIRM) / "vault", plat.keys, FIRM).put(W2, owner="doc_w2")
        sha = hashlib.sha256(W2).hexdigest()
        api.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, "
                    "vault_path, doc_type, tax_year) VALUES ('doc_w2', ?, ?, 'w2-2026.txt', 'text/plain', 'upload', ?, 'filed', "
                    "?, 'W-2', 2026)", (CLIENT, sha, audit.now(), loc))
        records.add_version(api, "doc_w2", loc, sha, len(W2), "intake-agent")
        hold = records.place_hold(api, client_id=CLIENT, reason=REASON, actor="u_lee", role="cpa")
    finally:
        api.close()
    assert list(bucket.objects) == [f"firms/{FIRM}/{loc.removeprefix('blob:')}"]

    with pytest.raises(AuthError, match=rf"active legal hold.*#{hold} \({CLIENT}\)"):
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)

    conn = db.open_store(plat.tenant_dir(FIRM) / "state" / "agentledger.db")
    try:
        assert [h["id"] for h in records.active_holds(conn)] == [hold]
    finally:
        conn.close()
    assert len(bucket.objects) == 1 and _key_alive(plat)
    assert plat.firm(FIRM)["status"] == "active" and plat.offboarding_records(FIRM) == [] and not plat._stages(FIRM)
    assert "firm_delete_refused" in [e["event"] for e in plat.events(FIRM)]

