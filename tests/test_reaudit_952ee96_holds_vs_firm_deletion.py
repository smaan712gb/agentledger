"""Re-audit of 952ee96, finding 3: firm deletion bypasses active legal holds. Regression tests: on 952ee96 each one
destroyed held evidence. What 952ee96 did:

F-06 promises that a legal hold on a client, or on a single document, blocks deletion until a CPA releases it with a
reason (evidence/records.py:5-6, config/retention.yaml:1-2). Only the retention purge honours that
(records.due_for_deletion and purge_expired, records.py:114 and 129). Offboarding a firm never reads legal_holds:

* Platform.delete_firm (security/platform.py:288-301) marks the firm deleted (295), crypto-shreds every version of its
  data key (299 -> crypto.Keyring.destroy, crypto.py:126-132) and calls destroy_firm_data (301).
* Platform.destroy_firm_data (platform.py:303-327), also the retry that delete_firm's docstring prescribes after a
  failed removal, checks only that the firm is marked deleted (306). It then removes the store with its legal_holds,
  deletion_receipts and audit tables (db.destroy_store, 314 -> db.py:468-482; on PostgreSQL pg/provision.destroy drops
  the schema or deletes the Neon database). It also deletes every object of the firm in the object store
  (evidence.blobs.destroy_firm, 315-317 -> blobs.py:132-141, the R2 configuration) and removes the tenant directory
  with the vault ciphertext (318-319).

No API route or CLI command deletes or abandons a firm. These Platform methods are the offboarding mechanism, called by
operators (and by tests/test_tenancy.py, test_security.py and test_firm_lifecycle.py). Platform.abandon_firm
(platform.py:240-252) has no hold check either. But only a firm still in 'provisioning' can be abandoned (243). SQLite
never sets that status (208), and on PostgreSQL such a firm cannot be signed in to or opened (558, api/app.py:122-123).
So no hold can exist in it, and it is not reproduced here.

Each test asserts the safe outcome: the held evidence stays stored and readable, the hold stays on record, and the
refusal names the hold (delete_firm now needs a reason, given here, so that the hold is what refuses). They run on the
default SQLite backend; the object store in the R2 case is an in-memory fake. After the hold is released, offboarding
goes ahead and leaves a record of what the store held (the last tests).
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3

import pytest

from agentledger import audit, db
from agentledger.evidence import blobs as blobstore
from agentledger.evidence import records
from agentledger.ledger import store
from agentledger.security.crypto import CryptoError, Keyring
from agentledger.security.platform import AuthError, Platform
from agentledger.security.vault import Vault
from test_tenancy import PW, accept, api, enrol  # noqa: F401  (api is a fixture)

FIRM, CLIENT = "holdrepro-cpa", "jordan-lee"
OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
W2 = b"Form W-2 2026 Employer: Brightline LLC Employee: Jordan Lee SSN 400-00-0001 Wages 61,200.00"
REASON = "IRS examination notice dated 2026-09-30: preserve all 2026 records"
# How a refusal would surface: platform refusals are AuthError, retention refusals RetentionError (a PermissionError).
REFUSAL = (AuthError, PermissionError)
LOST = {
    "store": "firm store removed (its legal_holds, deletion_receipts and audit tables with it)",
    "hold": "legal hold no longer on record as active",
    "ciphertext": "held document's ciphertext deleted",
    "key": "firm data key shredded",
    "readable": "held document can no longer be read",
}


@pytest.fixture(autouse=True)
def _never_real_storage(monkeypatch):
    """A local .env may point AGENTLEDGER_BLOBS at a real R2 bucket. These tests delete a firm, so they pin the file
    store (the R2 case uses an in-memory fake) and local sign-in for the API test."""
    monkeypatch.setenv("AGENTLEDGER_BLOBS", "file")
    monkeypatch.setenv("AGENTLEDGER_IDENTITY", "local")
    for var in ("AGENTLEDGER_BLOB_ENDPOINT", "AGENTLEDGER_BLOB_BUCKET", "AGENTLEDGER_BLOB_ACCESS_KEY_ID",
                "AGENTLEDGER_BLOB_SECRET_ACCESS_KEY", "AGENTLEDGER_BLOB_PREFIX"):
        monkeypatch.delenv(var, raising=False)


class MemoryS3:
    """The S3 calls S3Blobs makes, against one in-memory bucket."""

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


def _firm_with_held_w2(root, *, document_hold: bool):
    """A firm (SQLite store, encrypted vault) whose client has a filed 2026 W-2, kept until 2033-12-31, under an active
    legal hold placed by a CPA with records.place_hold, as POST /api/clients/{id}/holds does."""
    plat = Platform(root, dev=True)
    plat.create_firm(FIRM, "Hold Repro CPA", by="ops")
    tenant = plat.tenant_dir(FIRM)
    conn = db.open_store(tenant / "state" / "agentledger.db")
    store.add_client(conn, id=CLIENT, name="Jordan Lee", kind="individual")
    vault = Vault(tenant / "vault", plat.keys, FIRM)                     # as api/app.py:128 builds a firm's vault
    loc = vault.put(W2)
    sha = hashlib.sha256(W2).hexdigest()
    conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, vault_path, "
                 "doc_type, tax_year, retention_class, retain_until) VALUES ('doc_w2', ?, ?, 'w2-2026.txt', 'text/plain', 'upload', ?, "
                 "'filed', ?, 'W-2', 2026, 'tax_return_support', '2033-12-31')", (CLIENT, sha, audit.now(), loc))
    records.add_version(conn, "doc_w2", loc, sha, len(W2), "intake-agent")
    hold = records.place_hold(conn, client_id=CLIENT, reason=REASON, actor="u_lee", role="cpa",
                              document_id="doc_w2" if document_hold else None)
    return plat, conn, vault.root, loc, hold


def _hold_active(store_path, hold_id) -> bool:
    """Read from the store itself (a SQLite file, or a PostgreSQL schema or database), without creating it."""
    if not db.store_exists(store_path):
        return False
    conn = db.open_store(store_path)
    try:
        return any(h["id"] == hold_id for h in records.active_holds(conn))
    finally:
        conn.close()


def _survives(plat, vault_root, loc, hold_id) -> dict[str, bool]:
    """What is left of the held evidence, read with a fresh keyring (nothing cached from before the deletion)."""
    store_path = plat.tenant_dir(FIRM) / "state" / "agentledger.db"
    keys = Keyring(plat.conn, plat.keys.master)
    vault = Vault(vault_root, keys, FIRM)
    try:
        keys.current_version(FIRM)
        key = True
    except CryptoError:
        key = False
    try:
        readable = vault.read(loc) == W2
    except Exception:                       # bytes gone (FileNotFoundError, KeyError) or key shredded (CryptoError)
        readable = False
    exists = db.store_exists(store_path)
    return {"store": exists, "hold": exists and _hold_active(store_path, hold_id),
            "ciphertext": vault.exists(loc), "key": key, "readable": readable}


def _attempt(fn, *args, **kw) -> str:
    try:
        fn(*args, **kw)
    except REFUSAL as e:
        assert "legal hold" in str(e), f"refused, but not because of the hold: {e}"
        return f"refused ({type(e).__name__}: {e})"
    return "went ahead"


def _events(plat) -> list[str]:
    return [e["event"] for e in reversed(plat.events(FIRM))]


def test_delete_firm_destroys_evidence_under_an_active_hold(api):  # noqa: F811
    """Path: Platform.delete_firm (platform.py:288-301): Keyring.destroy (299), then destroy_firm_data (301) ->
    db.destroy_store (314) and rmtree of the tenant directory (318-319).

    End to end through the API: a CPA uploads a client's W-2 and places a client-wide legal hold
    (POST /api/clients/{id}/holds). Offboarding the firm must leave that evidence intact. On 952ee96 it shreds the firm's
    key and removes the store (hold record included) and the ciphertext without reading legal_holds."""
    mod, c = api
    mod.PLATFORM.bootstrap_admin("ops@agentledger.example", "Ops", PW)
    step = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    ops = enrol(c, step["challenge"], step["secret"])
    r = c.post("/api/platform/firms", json={"id": FIRM, "name": "Hold Repro CPA", "admin_email": "maya@holdrepro.example"},
               headers=ops)
    admin = accept(c, r.json()["admin_invite_token"], "Maya")
    tok = c.post("/api/auth/invite", json={"email": "lee@holdrepro.example", "role": "cpa"}, headers=admin).json()["invite_token"]
    lee = accept(c, tok, "Lee")
    assert c.post("/api/clients", json={"id": CLIENT, "name": "Jordan Lee", "kind": "individual"}, headers=lee).status_code == 200
    up = c.post("/api/documents/upload", files={"file": ("w2-2026.txt", W2, "text/plain")}, data={"client_id": CLIENT},
                headers=lee).json()[0]
    r = c.post(f"/api/documents/{up['id']}/assign", json={"client_id": CLIENT}, headers=lee)   # the CPA files it from review
    assert r.status_code == 200 and r.json()["client_id"] == CLIENT and r.json()["status"] == "filed", r.text
    doc = r.json()
    r = c.post(f"/api/clients/{CLIENT}/holds", json={"reason": REASON}, headers=lee)
    assert r.status_code == 200, r.text
    hold = r.json()["id"]
    vault_root = mod.firm_context(FIRM).foundry.vault.root
    assert all(_survives(mod.PLATFORM, vault_root, doc["vault_path"], hold).values())

    outcome = _attempt(mod.PLATFORM.delete_firm, FIRM, by="ops", reason=OFFBOARD)

    state = _survives(mod.PLATFORM, vault_root, doc["vault_path"], hold)
    lost = [LOST[k] for k, ok in state.items() if not ok]
    assert not lost, f"delete_firm with legal hold #{hold} active {outcome}: {lost}; platform events {_events(mod.PLATFORM)}"


def test_delete_firm_deletes_held_objects_from_the_object_store(tmp_path, monkeypatch):
    """Path: Platform.delete_firm -> destroy_firm_data -> evidence.blobs.destroy_firm (platform.py:315-317,
    blobs.py:132-141). With AGENTLEDGER_BLOBS=s3 (R2 in production) evidence lives in the bucket, not the tenant
    directory, and destroy_firm deletes every object under firms/<firm>/ with no hold check. Here the hold covers the
    single document (records.place_hold with document_id)."""
    bucket = MemoryS3()
    monkeypatch.setenv("AGENTLEDGER_BLOBS", "s3")
    monkeypatch.setattr(blobstore, "for_firm",
                        lambda root, firm_id: blobstore.S3Blobs(bucket="evidence", prefix=f"firms/{firm_id}", client=bucket))
    plat, conn, vault_root, loc, hold = _firm_with_held_w2(tmp_path, document_hold=True)
    try:
        assert list(bucket.objects) == [f"firms/{FIRM}/{loc.removeprefix('blob:')}"]
        assert all(_survives(plat, vault_root, loc, hold).values())

        outcome = _attempt(plat.delete_firm, FIRM, by="ops", reason=OFFBOARD)

        state = _survives(plat, vault_root, loc, hold)
        lost = [LOST[k] for k, ok in state.items() if not ok]
        assert not lost, (f"delete_firm with document hold #{hold} active {outcome}: {lost}; objects left in the bucket "
                          f"{sorted(bucket.objects)}; platform events {_events(plat)}")
    finally:
        conn.close()


def test_destroy_firm_data_retry_removes_a_store_with_an_active_hold(tmp_path):
    """Path: Platform.destroy_firm_data (platform.py:303-327), the retry that delete_firm's docstring prescribes after a
    failed removal. Its only gate is the firm's status (306). It then removes the store holding the active hold, the
    deletion receipts and the audit chain (db.destroy_store, 314), and the vault ciphertext (318-319).

    The firm is put in the state delete_firm leaves when its removal step raises (295-300 done, 301 raised and recorded
    as firm_data_destroy_failed). That happens, for example, on WinError 32 while the API process still has the SQLite
    file open (db.close_stores closes only this process's connections), or when the object store or Neon is
    unreachable. The firm is marked deleted and its key is shredded, but the store and ciphertext are still on disk and
    the hold is active. The retry must not finish the job while that hold is active."""
    plat, conn, vault_root, loc, hold = _firm_with_held_w2(tmp_path, document_hold=False)
    conn.close()
    plat.conn.execute("UPDATE firms SET status = 'deleted', deleted_at = datetime('now') WHERE id = ?", (FIRM,))   # platform.py:295
    plat.keys.destroy(FIRM)                                                                                      # platform.py:299
    before = _survives(plat, vault_root, loc, hold)
    assert before["store"] and before["hold"] and before["ciphertext"]

    outcome = _attempt(plat.destroy_firm_data, FIRM, by="ops")

    state = _survives(plat, vault_root, loc, hold)
    lost = [LOST[k] for k in ("store", "hold", "ciphertext") if not state[k]]
    assert not lost, f"destroy_firm_data with legal hold #{hold} active {outcome}: {lost}; platform events {_events(plat)}"

    assert "firm_destroy_refused" in _events(plat)


# --------------------------------------------------------------------------------------------- what must still work
def test_refusal_leaves_the_firm_in_use_and_on_record(tmp_path):
    """A refused offboarding changes nothing: the firm stays active, its key and store are intact, the refusal is an
    event naming the hold, and no offboarding record claims the firm was removed."""
    plat, conn, vault_root, loc, hold = _firm_with_held_w2(tmp_path, document_hold=False)
    conn.close()
    with pytest.raises(AuthError, match=f"legal hold.*#{hold}"):
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    assert plat.firm(FIRM)["status"] == "active"
    assert all(_survives(plat, vault_root, loc, hold).values())
    assert "firm_delete_refused" in _events(plat) and plat.offboarding_records(FIRM) == []
    with pytest.raises(AuthError, match="why the firm is being deleted"):
        plat.delete_firm(FIRM, by="ops")


def test_unreadable_store_is_treated_as_held(tmp_path, monkeypatch):
    """Fail closed: if the holds cannot be read, nothing is destroyed."""
    plat, conn, vault_root, loc, hold = _firm_with_held_w2(tmp_path, document_hold=False)
    conn.close()
    records.release_hold(db.open_store(plat.tenant_dir(FIRM) / "state" / "agentledger.db"), hold,
                         reason="examination closed, no-change letter received", actor="u_lee", role="cpa")

    def unreadable(ref, **kw):
        raise db.DatabaseError("injected: the store cannot be opened")

    from agentledger.evidence import offboarding

    with monkeypatch.context() as m:
        m.setattr(offboarding, "seal", unreadable)
        with pytest.raises(AuthError, match="could not be read"):
            plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    assert plat.firm(FIRM)["status"] == "active"
    state = _survives(plat, vault_root, loc, hold)
    assert state["store"] and state["ciphertext"] and state["key"] and state["readable"]


def test_after_release_offboarding_proceeds_and_keeps_a_record(tmp_path):
    """Once a CPA releases the hold with a reason, delete_firm removes the store and ciphertext, shreds the key, and
    the platform keeps what the store held: every hold (released), the document and receipt counts and the head of the
    store's audit chain."""
    plat, conn, vault_root, loc, hold = _firm_with_held_w2(tmp_path, document_hold=True)
    records.release_hold(conn, hold, reason="examination closed, no-change letter received", actor="u_lee", role="cpa")
    head = db.one(conn, "SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1")
    conn.close()
    plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    state = _survives(plat, vault_root, loc, hold)
    assert not any(state.values()), state
    [rec] = plat.offboarding_records(FIRM)
    summary = json.loads(rec["summary"])
    assert rec["by"] == "ops" and rec["reason"] == OFFBOARD
    assert [h["id"] for h in summary["holds"]] == [hold] and summary["holds"][0]["released_at"]
    assert summary["documents"] == 1 and summary["audit_head"] == {"seq": head["seq"], "hash": head["hash"]}
    assert plat.firm(FIRM)["status"] == "deleted" and "firm_data_destroyed" in _events(plat)
    with pytest.raises(sqlite3.DatabaseError):
        plat.conn.execute("DELETE FROM firm_offboarding")
