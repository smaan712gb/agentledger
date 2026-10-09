"""Second adversarial review of the write-ahead deletion (re-audit of 952ee96, finding 4): the deleting session could
still end while phase 2's storage delete was in flight (transaction_timeout, a failover or compute restart), releasing
the evidence locks it relied on, so a legal hold committed meanwhile was acknowledged as keeping bytes deleted
afterwards, and the next run said the bytes were gone before the hold took effect. Asserted now: no lock or transaction
is open across the storage call (an in-flight marker with a deadline commits first under the locks), so
transaction_timeout never ends the session mid-delete, and a hold placed during the call, even after the deleting
session was terminated, is placed at once and told which objects are being removed (in_flight); a hold committed
before the marker keeps the bytes; a marker left by a worker that died is honoured until its deadline; and a held
object is reported by whether its bytes are still stored, never by which happened first. Sessions: PostgreSQL; the
rest on both backends.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from agentledger import audit, db, pg
from agentledger.evidence import records
from test_evidence import _doc

PG = db.backend() == "postgres"
pg_only = pytest.mark.skipif(not PG, reason="PostgreSQL sessions (timeouts, backend termination)")
TODAY = date(2026, 10, 8)
REASON = "annual retention review"
EXAM = "IRS examination letter dated 2026-10-01"
CLOSED = "examination closed, no-change letter received"
DOC = "doc_2017_payroll"
GONE = "the bytes are no longer stored (an earlier attempt removed them)"


class Worker:
    """Another API worker: `fn` runs in its own thread, on its own database connection."""

    def __init__(self, conn, fn):
        self.result, self.error = None, None
        self.thread = threading.Thread(target=self._run, args=(fn,), daemon=True)
        self.thread.start()

    def _run(self, fn):
        try:
            self.result = fn()
        except Exception as exc:
            self.error = exc

    def finish(self) -> "Worker":
        self.thread.join(60)
        assert not self.thread.is_alive(), "the other worker never finished"
        return self


class Killed(BaseException):
    """The deleting worker dies (SIGKILL, a container stop): nothing after this point runs, not even except clauses."""


def _called_from(name: str) -> bool:
    f = sys._getframe(2)
    return f is not None and f.f_code.co_name == name


def _payroll(conn, vault):
    return _doc(conn, vault, DOC, "acme", b"2017 payroll register, fourth quarter", "2025-01-01")


def _purge(conn, vault):
    return records.purge_expired(conn, vault, TODAY, actor="maya", role="cpa", attested=True, reason=REASON)


def _hold_and_ask(conn):
    """A CPA places a hold on acme; the answer names the objects it cannot keep (the API's objects_being_removed)."""
    return records.place_hold(conn, client_id="acme", reason=EXAM, actor="maya", role="cpa"), records.in_flight(conn, "acme")


def _outcomes(conn):
    return [r["outcome"] for r in db.rows(conn, "SELECT r.outcome FROM blob_deletion_results r JOIN blob_deletions b "
                                                "ON b.id = r.deletion_id WHERE b.document_id = ? ORDER BY r.id", DOC)]


def _hold_during_slow_delete(monkeypatch, conn, vault, *, before=None, seconds: float = 2.5):
    """vault.blobs.delete: note whether a transaction is open, optionally end the deleting session (`before`), then let
    another worker place a hold on acme while storage takes `seconds` to answer, then delete."""
    real = vault.blobs.delete
    seen: dict = {}

    def delete(key):
        if "hold" not in seen:
            began = time.monotonic()
            seen["in_transaction"] = db._raw(conn).in_transaction
            if before:
                before()
            seen["hold"] = w = Worker(conn, lambda: _hold_and_ask(conn))
            w.thread.join(seconds)
            seen["hold_placed_during_delete"] = not w.thread.is_alive()
            time.sleep(max(0.0, seconds - (time.monotonic() - began)))
        real(key)

    monkeypatch.setattr(vault.blobs, "delete", delete)
    return seen


def _told(seen):
    hold = seen["hold"].finish()
    assert hold.error is None, hold.error
    assert seen["hold_placed_during_delete"] is True, "the hold waited for a storage call"
    hid, flying = hold.result
    assert [f["document_id"] for f in flying] == [DOC], "the hold was not told the object is being removed"
    return hid


def test_a_hold_committed_before_the_in_flight_marker_keeps_the_bytes(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    loc = _payroll(conn, vault)
    real_refs, real_delete = records._referencing, vault.blobs.delete
    seen: dict = {}

    def referencing(c, locator):
        if "hold" not in seen and _called_from("_delete_object"):
            # The receipt is committed; phase 2 is about to take the locks and write its marker: the IRS letter comes first.
            seen["hold"] = Worker(conn, lambda: _hold_and_ask(conn)).finish()
        return real_refs(c, locator)

    calls: list[str] = []
    monkeypatch.setattr(records, "_referencing", referencing)
    monkeypatch.setattr(vault.blobs, "delete", lambda key: (calls.append(key), real_delete(key))[1])
    out = _purge(conn, vault)
    hold = seen["hold"]
    assert hold.error is None and hold.result[1] == []                   # nothing was in flight when it was placed
    hid = hold.result[0]
    assert [r["document_id"] for r in out] == [DOC]
    assert [f["stage"] for f in out.failures] == ["held"] and out.failures[0]["bytes_present"] is True
    assert f"legal hold #{hid}" in out.failures[0]["error"] and "kept until it is released" in out.failures[0]["error"]
    assert calls == [] and _outcomes(conn) == [] and vault.exists(loc)   # no marker, storage never called
    [pending] = records.integrity(conn, vault)["pending_deletions"]
    assert pending["held"] is True and pending["bytes_present"] is True
    records.release_hold(conn, hid, reason=CLOSED, actor="maya", role="cpa")
    out = _purge(conn, vault)
    assert out == [] and out.failures == [] and _outcomes(conn) == ["started", "deleted"] and not vault.exists(loc)
    assert records.integrity(conn, vault)["ok"] is True


@pg_only
def test_transaction_timeout_never_ends_the_deleting_session_mid_delete(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    if int(conn.execute("SHOW server_version_num").fetchone()[0]) < 170000:
        pytest.skip("transaction_timeout exists from PostgreSQL 17")
    loc = _payroll(conn, vault)
    conn._get().raw.execute("SET transaction_timeout = '1500ms'")       # an operator's guard on long transactions
    pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
    seen = _hold_during_slow_delete(monkeypatch, conn, vault, seconds=2.5)
    out = _purge(conn, vault)
    assert [r["document_id"] for r in out] == [DOC] and out.failures == []
    assert seen["in_transaction"] is False                             # nothing for the timeout to end
    assert conn.execute("SELECT pg_backend_pid()").fetchone()[0] == pid # the session was never ended
    _told(seen)
    assert _outcomes(conn) == ["started", "deleted"] and not vault.exists(loc)
    assert records.in_flight(conn, "acme") == [] and records.integrity(conn, vault)["ok"] is True


@pg_only
def test_a_hold_placed_after_a_failover_mid_delete_is_told_and_the_next_run_claims_no_order(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    loc = _payroll(conn, vault)
    pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]

    def failover():                       # the deleting session ends (failover, compute restart, dropped connection)
        owner = pg.connect(pg.migration_url())
        try:
            owner.execute("SELECT pg_terminate_backend(%s)", (pid,))
        finally:
            owner.close()

    seen = _hold_during_slow_delete(monkeypatch, conn, vault, before=failover)
    out = _purge(conn, vault)
    assert seen["in_transaction"] is False
    hid = _told(seen)
    assert [r["document_id"] for r in out] == [DOC] and not vault.exists(loc)   # the delete landed, as the hold was told
    assert [f["stage"] for f in out.failures] == ["storage"], out.failures     # its outcome died with the session
    assert conn.execute("SELECT pg_backend_pid()").fetchone()[0] != pid
    assert _outcomes(conn) == ["started", "failed"]
    out = _purge(conn, vault)
    [held] = out.failures
    assert out == [] and held["stage"] == "held" and held["bytes_present"] is False
    assert held["error"] == f"legal hold #{hid} covers document {DOC}: {GONE}"
    [pending] = records.integrity(conn, vault)["pending_deletions"]
    assert pending["held"] is True and pending["bytes_present"] is False
    records.release_hold(conn, hid, reason=CLOSED, actor="maya", role="cpa")
    out = _purge(conn, vault)
    assert out == [] and out.failures == [] and _outcomes(conn) == ["started", "failed", "started", "deleted"]
    assert records.integrity(conn, vault)["ok"] is True


def test_a_marker_left_by_a_worker_that_died_mid_delete_is_honoured_and_reported_without_an_order(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    loc = _payroll(conn, vault)
    real = vault.blobs.delete

    def dies_after_the_delete(key):
        real(key)
        raise Killed()

    monkeypatch.setattr(vault.blobs, "delete", dies_after_the_delete)
    with pytest.raises(Killed):
        _purge(conn, vault)
    monkeypatch.setattr(vault.blobs, "delete", real)
    assert not vault.exists(loc) and _outcomes(conn) == ["started"]
    [flying] = records.in_flight(conn, "acme", DOC)
    out = _purge(conn, vault)                                  # before the deadline: the worker might only be slow
    assert out == [] and [f["stage"] for f in out.failures] == ["in_flight"] and flying["until"] in out.failures[0]["error"]
    assert _outcomes(conn) == ["started"]
    hid, told = _hold_and_ask(conn)
    assert told == [flying]                                    # the hold is told what it cannot keep
    later = datetime.now(timezone.utc) + records.IN_FLIGHT + timedelta(minutes=1)
    with audit.clock(later):                                   # past the deadline: the worker is gone
        assert records.in_flight(conn, "acme") == []
        out = _purge(conn, vault)
    [held] = out.failures
    assert out == [] and held["stage"] == "held" and held["bytes_present"] is False
    assert held["error"] == f"legal hold #{hid} covers document {DOC}: {GONE}"
    assert _outcomes(conn) == ["started"]
    records.release_hold(conn, hid, reason=CLOSED, actor="maya", role="cpa")
    with audit.clock(later):
        out = _purge(conn, vault)
    assert out == [] and out.failures == [] and _outcomes(conn) == ["started", "started", "deleted"]
    assert records.integrity(conn, vault)["ok"] is True


def test_a_run_reaching_an_object_another_run_is_deleting_never_reports_its_bytes_kept(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    loc = _payroll(conn, vault)
    real = vault.blobs.delete
    seen: dict = {}

    def delete(key):
        if "other" not in seen:
            # Storage is slow to answer (the deleting session may even have ended): a CPA places a hold, then another
            # worker's retention run reaches the same pending object.
            seen["hold"] = Worker(conn, lambda: _hold_and_ask(conn)).finish()
            seen["other"] = Worker(conn, lambda: _purge(conn, vault)).finish()
        real(key)

    monkeypatch.setattr(vault.blobs, "delete", delete)
    out = _purge(conn, vault)
    assert [r["document_id"] for r in out] == [DOC] and out.failures == []
    hold, other = seen["hold"], seen["other"]
    assert hold.error is None and [f["document_id"] for f in hold.result[1]] == [DOC]     # the hold was told
    assert other.error is None and list(other.result) == []
    assert _outcomes(conn) == ["started", "deleted"] and not vault.exists(loc)
    [report] = other.result.failures
    assert "kept until it is released" not in report["error"], (
        f"another run reported {report} while this run's in-flight marker was unexpired; the bytes were deleted right "
        "after: the report says the hold keeps bytes that were being removed")
