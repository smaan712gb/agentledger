"""Adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv_holds_lifecycle.py): an offboarding
interrupted after the firm was set to 'offboarding' could never be undone (each retry was refused by the hold and put
'offboarding' back, so no CPA could sign in to release it), and a hold committed between delete_firm's two hold checks
left the firm deleted, its users locked out and an offboarding record listing no hold. Asserted now: a refused retry
puts the firm back in use, cancel_offboarding unseals and restores a firm interrupted after its seal, a hold committed
while the seal waits stops the offboarding (firm active, users enabled, no record), and one tried after the seal is
refused by the store itself.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import types

import pytest

from agentledger import audit, db
from agentledger.evidence import offboarding, records
from agentledger.ledger import store
from agentledger.security import totp
from agentledger.security.platform import AuthError, Platform

FIRM, CLIENT = "stuck-cpa", "jordan-lee"
CPA_EMAIL = "lee@stuck.example"
OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
REASON = "IRS examination notice dated 2026-09-30: preserve all 2026 records"
RELEASE = "examination closed, no-change letter received"
CANCEL = "subpoena received 2026-10-02: the firm's records must be preserved"
PW = "correct horse battery staple 42"
# How the store itself refuses a hold once it is sealed: the SQLite trigger, or the revoked INSERT on PostgreSQL.
SEALED = "being offboarded" if db.backend() != "postgres" else "permission denied"


@pytest.fixture(autouse=True)
def _schema_tenancy(monkeypatch):
    if db.backend() == "postgres":
        monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")


def _firm_with_cpa(tmp_path):
    """A firm with one client and a signed-in CPA. Returns the platform, the store path, the CPA's TOTP secret and
    the CPA's session token."""
    plat = Platform(tmp_path, dev=True)
    plat.create_firm(FIRM, "Stuck CPA", by="ops")
    admin = plat.bootstrap_admin("ops@platform.example", "Ops", PW)
    enrol = plat.accept_invite(plat.invite(FIRM, CPA_EMAIL, "cpa", by=plat.user(admin)), "Lee", PW)
    token = plat.complete_mfa(enrol.challenge, totp.code_at(enrol.secret, int(time.time() // 30) - 1))
    assert plat.session_user(token)
    path = plat.tenant_dir(FIRM) / "state" / "agentledger.db"
    conn = db.open_store(path)
    try:
        store.add_client(conn, id=CLIENT, name="Jordan Lee", kind="individual")
    finally:
        conn.close()
    return plat, path, enrol.secret, token


def _sign_in(plat, secret) -> dict | None:
    """The firm's CPA signs in (password, then a fresh one-time code) and uses the session. None if refused."""
    try:
        step = plat.login(CPA_EMAIL, PW)
        token = plat.complete_mfa(step["mfa"], totp.code_at(secret, int(time.time() // 30) + 1))
    except AuthError:
        return None
    return plat.session_user(token)


def _place(path, **kw) -> int:
    conn = db.open_store(path)
    try:
        return records.place_hold(conn, client_id=CLIENT, reason=REASON, actor="u_lee", role="cpa", **kw)
    finally:
        conn.close()


def _release(path, hold) -> None:
    conn = db.open_store(path)
    try:
        records.release_hold(conn, hold, reason=RELEASE, actor="u_lee", role="cpa")
    finally:
        conn.close()


def _active(path) -> list[int]:
    conn = db.open_store(path)
    try:
        return [h["id"] for h in records.active_holds(conn)]
    finally:
        conn.close()


def _events(plat) -> list[str]:
    return [e["event"] for e in reversed(plat.events(FIRM))]


# --------------------------------------------------------------------------------------------- 1. interrupted offboarding
@pytest.mark.parametrize("offboarding_from", ["active", None], ids=["killed-during-hold-check", "left-by-the-old-code"])
def test_interrupted_offboarding_is_put_back_in_use_by_the_refused_retry(tmp_path, offboarding_from):
    plat, path, secret, token = _firm_with_cpa(tmp_path)
    hold = _place(path)
    # What a delete_firm attempt killed while its seal waited leaves behind: only the status change was committed.
    plat.conn.execute("UPDATE firms SET status = 'offboarding', offboarding_from = ? WHERE id = ?", (offboarding_from, FIRM))
    assert plat.session_user(token) is None                           # stuck: nobody can use the firm

    with pytest.raises(AuthError, match=rf"active legal hold.*#{hold} \({CLIENT}\)"):
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)               # the operator retries; the hold refuses it

    f = plat.firm(FIRM)
    assert (f["status"], f["offboarding_from"]) == ("active", None)
    user = _sign_in(plat, secret)
    assert user and user["firm_id"] == FIRM                           # the CPA who must release the hold can sign in
    assert _active(path) == [hold] and plat.offboarding_records(FIRM) == [] and not plat._stages(FIRM)
    assert "firm_delete_refused" in _events(plat)

    _release(path, hold)                                              # the CPA releases it; the offboarding goes ahead
    plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    assert plat.firm(FIRM)["status"] == "deleted"
    [rec] = plat.offboarding_records(FIRM)
    assert [(h["id"], bool(h["released_at"])) for h in json.loads(rec["summary"])["holds"]] == [(hold, True)]


def test_offboarding_interrupted_after_its_seal_is_cancelled_back_into_use(tmp_path, monkeypatch):
    plat, path, secret, token = _firm_with_cpa(tmp_path)

    def removal_fails(ref, **kw):
        raise ConnectionError("injected: the database service is unreachable while removing the store")

    with monkeypatch.context() as m:
        m.setattr(offboarding, "destroy", removal_fails)
        with pytest.raises(ConnectionError, match="injected"):
            plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    # The removal had begun (recorded before the store is touched); the store is intact, so it can still come back.
    assert plat.firm(FIRM)["status"] == "offboarding" and plat._stages(FIRM) == {"sealed", "store_removing"}
    assert plat.session_user(token) is None                           # nobody uses a firm that is being offboarded
    with pytest.raises(Exception, match=SEALED):                       # and the store refuses new holds
        _place(path)

    with pytest.raises(AuthError, match="record why the offboarding is cancelled"):
        plat.cancel_offboarding(FIRM, by="ops", reason="")
    plat.cancel_offboarding(FIRM, by="ops", reason=CANCEL)

    f = plat.firm(FIRM)
    assert (f["status"], f["offboarding_from"]) == ("active", None) and not plat._stages(FIRM)
    assert plat.session_user(token)["firm_id"] == FIRM
    hold = _place(path)                                               # unsealed: the hold the cancellation was for
    assert _active(path) == [hold]
    assert "firm_offboarding_cancelled" in _events(plat)
    with pytest.raises(AuthError, match="not being offboarded"):
        plat.cancel_offboarding(FIRM, by="ops", reason=CANCEL)
    with pytest.raises(AuthError, match=rf"active legal hold.*#{hold}"):
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    assert plat.firm(FIRM)["status"] == "active"


# --------------------------------------------------------------------------------------------- 2. a hold in flight
@pytest.mark.skipif(db.backend() == "postgres",
                    reason="SQLite's write lock; PostgreSQL: test_reaudit_952ee96_review_holds_race.py")
def test_hold_committed_while_the_seal_waits_stops_the_offboarding(tmp_path, monkeypatch):
    """A request admitted while the firm was active has written its hold, not yet committed, when delete_firm starts.
    The seal's BEGIN IMMEDIATE waits for it; the request commits (the CPA gets the hold's id) and the seal sees it."""
    plat, path, secret, _ = _firm_with_cpa(tmp_path)
    inserted, release, sealing = threading.Event(), threading.Event(), threading.Event()
    real_record = audit.record

    def record(c, *a, **kw):
        out = real_record(c, *a, **kw)
        if threading.current_thread().name == "api-request":
            inserted.set()                         # the hold row and its audit record are written; COMMIT not sent yet
            assert release.wait(60)
        return out

    class Spy:
        """offboarding's SQLite connection, reporting when the seal asks for the write lock."""

        def __init__(self, c):
            self._c = c

        def execute(self, sql, *a):
            if sql.startswith("BEGIN IMMEDIATE"):
                sealing.set()
            return self._c.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(self._c, name)

    monkeypatch.setattr(audit, "record", record)
    monkeypatch.setattr(offboarding, "sqlite3", types.SimpleNamespace(connect=lambda *a, **kw: Spy(sqlite3.connect(*a, **kw))))
    placed: dict = {}

    def request():                                 # POST /api/clients/{id}/holds, after firm_context saw the firm active
        try:
            placed["id"] = _place(path)
        except Exception as exc:                   # pragma: no cover - diagnostic
            placed["error"] = f"{type(exc).__name__}: {exc}"

    req = threading.Thread(target=request, name="api-request", daemon=True)
    req.start()
    assert inserted.wait(30), placed
    result: dict = {}

    def offboard():
        try:
            plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
            result["outcome"] = "went ahead"
        except AuthError as e:
            result["refused"] = str(e)

    ops = threading.Thread(target=offboard, name="ops", daemon=True)
    ops.start()
    try:
        assert sealing.wait(30), result            # the seal is asking for the lock the request holds
        assert plat.firm(FIRM)["status"] == "offboarding"
    finally:
        release.set()
        req.join(30)
        ops.join(60)

    assert "id" in placed, placed
    assert f"#{placed['id']} ({CLIENT})" in result.get("refused", ""), result
    assert plat.firm(FIRM)["status"] == "active"
    assert [u["disabled"] for u in plat.users(FIRM)] == [0]
    assert _sign_in(plat, secret)
    assert _active(path) == [placed["id"]]
    assert plat.offboarding_records(FIRM) == [] and not plat._stages(FIRM)


def test_hold_tried_after_the_seal_is_refused_by_the_store(tmp_path, monkeypatch):
    """A request admitted before the offboarding began tries its hold only after the seal: the store refuses it, so no
    hold is ever acknowledged for evidence that is then destroyed."""
    plat, path, _, _ = _firm_with_cpa(tmp_path)
    real_destroy = offboarding.destroy
    attempt: dict = {}

    def destroy_after_a_late_request(ref, **kw):
        def request():
            try:
                attempt["id"] = _place(path)
            except Exception as exc:
                attempt["refused"] = f"{type(exc).__name__}: {exc}"

        t = threading.Thread(target=request, name="api-request", daemon=True)
        t.start()
        t.join(60)
        return real_destroy(ref, **kw)

    monkeypatch.setattr(offboarding, "destroy", destroy_after_a_late_request)
    plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)

    assert "id" not in attempt and SEALED in attempt.get("refused", ""), attempt
    assert plat.firm(FIRM)["status"] == "deleted"
    [rec] = plat.offboarding_records(FIRM)
    assert json.loads(rec["summary"])["holds"] == []
