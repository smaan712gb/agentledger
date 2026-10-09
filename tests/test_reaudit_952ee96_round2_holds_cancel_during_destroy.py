"""Second adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv2_holds_cancel_during_destroy.py):
cancel_offboarding during a running delete_firm unsealed the store and put the firm back in use, and the run then
destroyed the store with the hold a CPA placed after the cancellation, the object-store copy and the data key.
Asserted now, with the interleavings forced deterministically and the cancellation coming from a second operator's
platform process: while the run is before its store's removal (before or after the seal) the cancellation is only
requested, the firm stays out of use (its store still sealed), and the run stops at its next stage, unseals and puts
the firm back, after which the hold is placed and kept with the store, the object and the key; once the removal began
the cancellation is refused with that reason, the sealed store refuses the hold (none is acknowledged), and the run
finishes; and a store unsealed behind the run's back is found unsealed in the removal's own locked step and kept.
"""

from __future__ import annotations

import hashlib
import io
import threading

import pytest

from agentledger import audit, db
from agentledger.evidence import blobs as blobstore
from agentledger.evidence import offboarding, records
from agentledger.ledger import store
from agentledger.security.crypto import CryptoError, Keyring
from agentledger.security.platform import AuthError, Platform
from agentledger.security.vault import Vault

FIRM, CLIENT = "cancel-cpa", "jordan-lee"
OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
CANCEL = "subpoena received 2026-10-02: the firm's records must be preserved"
REASON = "subpoena duces tecum dated 2026-10-02: preserve all records of Jordan Lee"
W2 = b"Form W-2 2026 Employer: Brightline LLC Employee: Jordan Lee SSN 400-00-0001 Wages 61,200.00"
CANCELLED = "the offboarding was cancelled before the store's removal began; the firm is back in use"
# How the store itself refuses a hold once it is sealed: the SQLite trigger, or the revoked INSERT on PostgreSQL.
SEALED = "being offboarded" if db.backend() != "postgres" else "permission denied for table legal_holds"
RUN = "ops-1"


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


def _firm_with_w2(tmp_path):
    plat = Platform(tmp_path, dev=True)
    plat.create_firm(FIRM, "Cancel CPA", by="ops")
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


def _pause_run(m, where: str):
    """Hold the offboarding run (thread RUN) at `where` until `go` is set. Returns (reached, go, seals): seals counts
    the run's calls of offboarding.seal."""
    reached, go, seals = threading.Event(), threading.Event(), []

    def pause():
        if threading.current_thread().name == RUN:
            reached.set()
            assert go.wait(60)

    real_seal, real_destroy, real_ref = offboarding.seal, offboarding.destroy, Platform._store_ref

    def store_ref(self, firm_id, **kw):                    # the run holds its lease; nothing is sealed yet
        if where == "before-the-seal":
            pause()
        return real_ref(self, firm_id, **kw)

    def seal(ref, **kw):
        if threading.current_thread().name == RUN:
            seals.append(ref)
        out = real_seal(ref, **kw)
        if where == "after-the-seal":                      # sealed (committed); the removal has not begun
            pause()
        return out

    def destroy(ref, **kw):                                # 'store_removing' is recorded: the removal has begun
        if where == "removal-began":
            pause()
        return real_destroy(ref, **kw)

    m.setattr(Platform, "_store_ref", store_ref)
    m.setattr(offboarding, "seal", seal)
    m.setattr(offboarding, "destroy", destroy)
    return reached, go, seals


def _offboard(plat, result):
    try:
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
        result["outcome"] = "went ahead"
    except Exception as exc:
        result["outcome"] = f"refused ({type(exc).__name__}: {exc})"


def _try_hold(path) -> str:
    conn = db.open_store(path)
    try:
        return f"placed #{records.place_hold(conn, client_id=CLIENT, reason=REASON, actor='u_lee', role='cpa')}"
    except Exception as exc:
        return f"refused ({type(exc).__name__}: {exc})"
    finally:
        conn.close()


def _active(path) -> list[int]:
    conn = db.open_store(path)
    try:
        return [h["id"] for h in records.active_holds(conn)]
    finally:
        conn.close()


def _key_alive(plat) -> bool:
    try:
        Keyring(plat.conn, plat.keys.master).current_version(FIRM)
        return True
    except CryptoError:
        return False


def _events(plat) -> list[str]:
    return [e["event"] for e in reversed(plat.events(FIRM))]


@pytest.mark.parametrize("where", ["before-the-seal", "after-the-seal"])
def test_cancellation_during_a_run_stops_it_before_the_removal_and_the_hold_is_kept(tmp_path, monkeypatch, bucket, where):
    plat, path = _firm_with_w2(tmp_path)
    result: dict = {}
    with monkeypatch.context() as m:
        reached, go, seals = _pause_run(m, where)
        run = threading.Thread(target=_offboard, args=(plat, result), name=RUN, daemon=True)
        run.start()
        try:
            assert reached.wait(30), result
            second = Platform(tmp_path, dev=True)             # a second operator's process, for the subpoena
            assert second.cancel_offboarding(FIRM, by="ops-2", reason=CANCEL) == "requested"
            assert plat.firm(FIRM)["status"] == "offboarding"     # out of use until the run has stopped
            if where == "after-the-seal":
                attempt = _try_hold(path)
                assert attempt.startswith("refused") and SEALED in attempt, attempt
        finally:
            go.set()
            run.join(60)
    assert not run.is_alive()
    assert result["outcome"] == f"refused (AuthError: {CANCELLED})", result
    assert len(seals) == (0 if where == "before-the-seal" else 1)

    f = plat.firm(FIRM)
    assert (f["status"], f["offboarding_from"]) == ("active", None)
    assert not plat._stages(FIRM) and plat._cancel_request(FIRM) is None
    cancelled = [(e["user_id"], e["detail"]) for e in plat.events(FIRM) if e["event"] == "firm_offboarding_cancelled"]
    assert cancelled == [("ops-2", CANCEL)] and "firm_offboarding_cancel_requested" in _events(plat)
    assert "firm_data_destroyed" not in _events(plat) and "firm_deleted" not in _events(plat)
    # Each seal leaves its record; a run stopped before sealing leaves none.
    assert [r["reason"] for r in plat.offboarding_records(FIRM)] == ([] if where == "before-the-seal" else [OFFBOARD])

    attempt = _try_hold(path)                                   # the hold the cancellation was for
    assert attempt.startswith("placed #"), attempt
    hold = int(attempt.removeprefix("placed #"))
    assert db.store_exists(path) and _active(path) == [hold]
    assert len(bucket.objects) == 1 and _key_alive(plat)
    with pytest.raises(AuthError, match=rf"active legal hold.*#{hold} \({CLIENT}\)"):
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)       # the next run is refused by the hold, not cancelled
    assert plat.firm(FIRM)["status"] == "active" and _active(path) == [hold] and len(bucket.objects) == 1


def test_cancellation_once_the_removal_began_is_refused_and_no_hold_is_acknowledged(tmp_path, monkeypatch, bucket):
    plat, path = _firm_with_w2(tmp_path)
    result: dict = {}
    with monkeypatch.context() as m:
        reached, go, _ = _pause_run(m, "removal-began")
        run = threading.Thread(target=_offboard, args=(plat, result), name=RUN, daemon=True)
        run.start()
        try:
            assert reached.wait(30), result
            second = Platform(tmp_path, dev=True)
            with pytest.raises(AuthError, match="being removed by a run in progress; the offboarding can only be finished"):
                second.cancel_offboarding(FIRM, by="ops-2", reason=CANCEL)
            assert plat.firm(FIRM)["status"] == "offboarding" and plat._cancel_request(FIRM) is None
            attempt = _try_hold(path)                           # the CPA's hold for the subpoena
            assert attempt.startswith("refused") and SEALED in attempt, attempt
        finally:
            go.set()
            run.join(60)
    assert not run.is_alive()
    assert result["outcome"] == "went ahead", result

    assert plat.firm(FIRM)["status"] == "deleted" and not db.store_exists(path)
    assert not bucket.objects and not _key_alive(plat)
    assert "firm_offboarding_cancel_requested" not in _events(plat) and "firm_offboarding_cancelled" not in _events(plat)
    [rec] = plat.offboarding_records(FIRM)
    assert rec["reason"] == OFFBOARD


def test_store_unsealed_behind_the_runs_back_is_verified_and_kept(tmp_path, monkeypatch, bucket):
    """Whatever gives back the right to add holds after the run's seal (an older release's cancellation, an operator's
    GRANT), the removal checks the seal in its own locked step: nothing is removed, and the hold placed meanwhile is
    kept with the store, the object and the key."""
    plat, path = _firm_with_w2(tmp_path)
    result: dict = {}
    with monkeypatch.context() as m:
        reached, go, _ = _pause_run(m, "removal-began")
        run = threading.Thread(target=_offboard, args=(plat, result), name=RUN, daemon=True)
        run.start()
        try:
            assert reached.wait(30), result
            offboarding.unseal(plat._store_ref(FIRM))              # out of band, behind the run's back
            attempt = _try_hold(path)
            assert attempt.startswith("placed #"), attempt
            hold = int(attempt.removeprefix("placed #"))
        finally:
            go.set()
            run.join(60)
    assert not run.is_alive()
    assert result["outcome"].startswith("refused (NotSealed: ") and "is not sealed" in result["outcome"], result

    assert db.store_exists(path) and _active(path) == [hold] and len(bucket.objects) == 1 and _key_alive(plat)
    assert plat.firm(FIRM)["status"] == "offboarding" and plat._stages(FIRM) == {"sealed", "store_removing"}
    assert "firm_data_destroy_failed" in _events(plat) and "firm_deleted" not in _events(plat)
    assert plat.cancel_offboarding(FIRM, by="ops-2", reason=CANCEL) == "cancelled"     # intact: back into use
    assert plat.firm(FIRM)["status"] == "active" and _active(path) == [hold]
