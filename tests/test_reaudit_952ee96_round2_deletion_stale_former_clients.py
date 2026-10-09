"""Second adversarial review of the write-ahead deletion (re-audit of 952ee96, finding 4): a deletion computed its lock
set (the client and its former clients) before locking and re-checked only the client afterwards, so a document moved
away and back in that window had a former client the deletion never locked, and a legal hold placed on that client
while the deletion decided did not stop it; pipeline.assign had the same pattern. Asserted now: once locked,
_commit_deletion, _delete_object and assign re-check every client involved, current and former. On PostgreSQL a
deletion then decides nothing this run (an object is reported as 'retry'), so the hold on the former client keeps the
document and the next run locks both clients, and a move is refused as "just moved"; on SQLite the deletion's write
lock makes the moves wait (the deletion first, the move refused) and a second reviewer's move is the one refused.
"""

from __future__ import annotations

import sys
import threading
from datetime import date

import pytest

from agentledger import db
from agentledger.evidence import records
from agentledger.intake.pipeline import assign
from agentledger.ledger import store
from test_evidence import _doc

PG = db.backend() == "postgres"
TODAY = date(2026, 10, 8)
REASON = "annual retention review"
SUMMONS = "IRS summons dated 2026-10-07 for Beta Builders"
TO_BETA = "Beta Builders' invoice, filed to Acme by mistake"
BACK = "Acme's copy after all: the invoice names both firms"


class Worker:
    """Another API worker: `fn` runs in its own thread, on its own database connection."""

    def __init__(self, conn, fn):
        self.result, self.error, self.pid = None, None, None
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(conn, fn), daemon=True)
        self.thread.start()
        self._ready.wait(30)

    def _run(self, conn, fn):
        try:
            if PG:
                self.pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
            self._ready.set()
            self.result = fn()
        except Exception as exc:
            self.error = exc
        finally:
            self._ready.set()

    def blocked(self, seconds: float = 1.5) -> bool:
        """Still running after `seconds`: waiting for the write lock the caller's transaction holds (SQLite)."""
        self.thread.join(seconds)
        return self.thread.is_alive()

    def finish(self) -> "Worker":
        self.thread.join(60)
        assert not self.thread.is_alive(), "the other worker never finished"
        return self


def _called_from(name: str, depth: int = 1) -> bool:
    f = sys._getframe(2)
    for _ in range(depth):
        if f is None:
            return False
        if f.f_code.co_name == name:
            return True
        f = f.f_back
    return False


def _other_thread() -> bool:
    return threading.current_thread() is not threading.main_thread()


def _purge(conn, vault):
    return records.purge_expired(conn, vault, TODAY, actor="lee", role="cpa", attested=True, reason=REASON)


def _invoice(biz):
    conn, vault = biz.conn, biz.vault
    store.add_client(conn, id="beta", name="Beta Builders LLC", kind="business")
    loc = _doc(conn, vault, "doc_invoice", "acme", b"2017 supplier invoice, Acme and Beta Builders", "2025-01-01")
    assert [d["id"] for d in records.due_for_deletion(conn, TODAY)] == ["doc_invoice"]
    return loc


def _moves(conn):
    return [(r["from_client"], r["to_client"]) for r in db.rows(conn, "SELECT from_client, to_client FROM document_moves "
                                                                     "WHERE document_id = 'doc_invoice' ORDER BY id")]


def test_a_deletion_whose_lock_set_went_stale_decides_nothing_and_a_hold_on_the_former_client_keeps_it(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    loc = _invoice(biz)
    real_lock, real_former, real_end = records.evidence_lock, records.former_clients, records.retention_end
    computed = threading.Event()
    seen: dict = {}

    def away_and_back():
        assign(conn, vault, "doc_invoice", "beta", "lee", move_reason=TO_BETA)
        assign(conn, vault, "doc_invoice", "acme", "lee", move_reason=BACK)
        return records.confirm_retention(conn, "doc_invoice", tax_year=None, retention_class="tax_return_support",
                                         actor="maya", role="cpa", note="invoice checked again after the moves")

    def summons():
        return records.place_hold(conn, client_id="beta", actor="maya", role="cpa", reason=SUMMONS)

    def former_clients(c, doc_id):
        if _other_thread() and _called_from("assign"):
            computed.set()                       # the move read the document and computed its lock set
        return real_former(c, doc_id)

    def evidence_lock(c, *clients):
        if "moves" not in seen and _called_from("_commit_deletion"):
            # The deletion read client acme and no former client and is about to lock them: the invoice goes to Beta and
            # back (two audited moves, then its retention confirmed again, as every move requires).
            seen["clients"] = sorted(clients)
            seen["moves"] = Worker(conn, away_and_back)
            assert computed.wait(30), "the moves never started"
            if PG:
                seen["moves"].finish()           # no lock is held yet: both moves commit
            else:
                seen["moves_waited"] = seen["moves"].blocked()
        real_lock(c, *clients)

    def retention_end(c, pol, d):
        if PG and d["id"] == "doc_invoice" and "moves" in seen and "hold" not in seen and _called_from("_commit_deletion"):
            # Deciding under evidence:acme alone: an IRS summons for Beta's records (now a former client) arrives.
            seen["decided"] = True
            seen["hold"] = Worker(conn, summons)
            seen["hold"].thread.join(5)
        return real_end(c, pol, d)

    monkeypatch.setattr(records, "former_clients", former_clients)
    monkeypatch.setattr(records, "evidence_lock", evidence_lock)
    monkeypatch.setattr(records, "retention_end", retention_end)
    out = _purge(conn, vault)
    moves = seen["moves"].finish()
    monkeypatch.setattr(records, "former_clients", real_former)
    monkeypatch.setattr(records, "evidence_lock", real_lock)
    monkeypatch.setattr(records, "retention_end", real_end)
    assert seen["clients"] == ["acme"]
    doc = db.one(conn, "SELECT client_id, deleted_at FROM documents WHERE id = 'doc_invoice'")
    receipts = db.rows(conn, "SELECT client_id FROM deletion_receipts WHERE document_id = 'doc_invoice'")
    if PG:
        assert moves.error is None and moves.result["client_id"] == "acme" and _moves(conn) == [("acme", "beta"),
                                                                                               ("beta", "acme")]
        assert "decided" not in seen, (
            f"the deletion decided under the locks of the clients it read before locking ({seen['clients']}), although "
            f"Beta became a former client meanwhile; a hold on Beta placed then did not stop it: tombstoned "
            f"{doc['deleted_at']}, receipts {[r['document_id'] for r in out]}")
        assert out == [] and out.failures == [] and receipts == []    # no decision this run
        assert doc == {"client_id": "acme", "deleted_at": None} and vault.exists(loc)
        hold = summons()                                              # the summons arrives now
        assert records.former_clients(conn, "doc_invoice") == ["acme", "beta"]
        assert records.hold_on(conn, "doc_invoice", "acme")["id"] == hold     # the hold on Beta covers it
        assert _purge(conn, vault) == [] and vault.exists(loc)
        records.release_hold(conn, hold, reason="summons withdrawn, letter dated 2026-10-20", actor="maya",
                             role="cpa")
        locked: list = []

        def evidence_lock_next(c, *clients):
            if _called_from("_commit_deletion"):
                locked.append(sorted(clients))
            real_lock(c, *clients)

        monkeypatch.setattr(records, "evidence_lock", evidence_lock_next)
        out = _purge(conn, vault)
        assert [r["document_id"] for r in out] == ["doc_invoice"] and out.failures == []
        assert locked == [["acme", "beta"]] and not vault.exists(loc)     # the next run locks every client involved
    else:
        # The deletion held the write lock: the moves waited, and the deleted invoice cannot be moved.
        assert seen["moves_waited"] is True and [r["document_id"] for r in out] == ["doc_invoice"] and out.failures == []
        assert isinstance(moves.error, ValueError) and "deleted under the retention policy" in str(moves.error)
        assert doc["client_id"] == "acme" and doc["deleted_at"] and receipts == [{"client_id": "acme"}]
        assert _moves(conn) == [] and not vault.exists(loc)


def test_an_object_whose_documents_changed_hands_during_the_run_is_retried_next_run(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    store.add_client(conn, id="beta", name="Beta Builders LLC", kind="business")
    shared = _doc(conn, vault, "doc_old", "acme", b"2017 lease, signed by Acme and Beta Builders", "2025-01-01")
    assert _doc(conn, vault, "doc_kept", "acme", b"2017 lease, signed by Acme and Beta Builders", "2034-01-01") == shared
    real_lock = records.evidence_lock
    seen: dict = {}

    def evidence_lock(c, *clients):
        if "move" not in seen and _called_from("_delete_object"):
            # Phase 2 read the object's documents (both Acme's) and is about to lock them: doc_kept moves to Beta now.
            seen["clients"] = sorted(clients)
            seen["move"] = Worker(conn, lambda: assign(conn, vault, "doc_kept", "beta", "lee", move_reason=TO_BETA))
            if PG:
                seen["move"].finish()            # no lock is held yet: the move commits
            else:
                seen["move_waited"] = seen["move"].blocked()
        real_lock(c, *clients)

    monkeypatch.setattr(records, "evidence_lock", evidence_lock)
    out = _purge(conn, vault)
    move = seen["move"].finish()
    monkeypatch.setattr(records, "evidence_lock", real_lock)
    assert [r["document_id"] for r in out] == ["doc_old"] and seen["clients"] == ["acme"]
    assert move.error is None and move.result["client_id"] == "beta"
    outcomes = [r["outcome"] for r in db.rows(conn, "SELECT r.outcome FROM blob_deletion_results r JOIN blob_deletions b "
                                                    "ON b.id = r.deletion_id WHERE b.document_id = 'doc_old' ORDER BY r.id")]
    if PG:
        assert [f["stage"] for f in out.failures] == ["retry"], out.failures
        retry = out.failures[0]
        assert retry["document_id"] == "doc_old" and "changed hands" in retry["error"]
        assert outcomes == [] and [p["document_id"] for p in records.pending_deletions(conn)] == ["doc_old"]
        out = _purge(conn, vault)                                     # locks Acme and Beta; doc_kept still uses the bytes
        assert out == [] and out.failures == []
        outcomes = [r["outcome"] for r in db.rows(conn, "SELECT r.outcome FROM blob_deletion_results r JOIN blob_deletions "
                                                        "b ON b.id = r.deletion_id WHERE b.document_id = 'doc_old'")]
    else:
        assert seen["move_waited"] is True and out.failures == []     # the move waited for phase 2
    assert outcomes == ["kept_shared"] and vault.exists(shared) and records.pending_deletions(conn) == []
    assert records.integrity(conn, vault)["ok"] is True


def test_a_move_whose_lock_set_went_stale_is_refused_as_just_moved(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    for cid, name in (("beta", "Beta Builders LLC"), ("gamma", "Gamma Supply Co")):
        store.add_client(conn, id=cid, name=name, kind="business")
    _doc(conn, vault, "doc_invoice", "acme", b"2017 supplier invoice, Acme and Beta Builders", "2033-01-01")
    real_lock, real_former = records.evidence_lock, records.former_clients
    computed = threading.Event()
    seen: dict = {}

    def away_and_back():
        assign(conn, vault, "doc_invoice", "beta", "lee", move_reason=TO_BETA)
        return assign(conn, vault, "doc_invoice", "acme", "lee", move_reason=BACK)

    def former_clients(c, doc_id):
        if _other_thread() and _called_from("assign"):
            computed.set()                       # the other reviewer read the document and computed its lock set
        return real_former(c, doc_id)

    def evidence_lock(c, *clients):
        if "other" not in seen and not _other_thread() and _called_from("assign"):
            # This reviewer read client acme and no former client and is about to lock acme and gamma: another reviewer
            # moves the invoice to Beta and back.
            seen["clients"] = sorted(clients)
            seen["other"] = Worker(conn, away_and_back)
            assert computed.wait(30), "the other reviewer never started"
            if PG:
                seen["other"].finish()           # no lock is held yet: both moves commit
            else:
                seen["other_waited"] = seen["other"].blocked()
        real_lock(c, *clients)

    monkeypatch.setattr(records, "former_clients", former_clients)
    monkeypatch.setattr(records, "evidence_lock", evidence_lock)
    try:
        moved, error = assign(conn, vault, "doc_invoice", "gamma", "maya", move_reason="Gamma's copy of the invoice"), None
    except ValueError as exc:
        moved, error = None, exc
    other = seen["other"].finish()
    monkeypatch.setattr(records, "former_clients", real_former)
    monkeypatch.setattr(records, "evidence_lock", real_lock)
    assert seen["clients"] == ["acme", "gamma"]
    if PG:
        assert moved is None and "just moved by someone else" in str(error), (
            f"the move to gamma went ahead under the locks of the clients it read before locking ({seen['clients']}), "
            f"although Beta became a former client meanwhile: {moved or error!r}")
        assert other.error is None and other.result["client_id"] == "acme"
        assert _moves(conn) == [("acme", "beta"), ("beta", "acme")]
        records.place_hold(conn, client_id="beta", actor="maya", role="cpa", reason=SUMMONS)
        with pytest.raises(ValueError, match="a legal hold covers this document"):     # retried with the right locks
            assign(conn, vault, "doc_invoice", "gamma", "maya", move_reason="Gamma's copy of the invoice")
        assert db.one(conn, "SELECT client_id FROM documents WHERE id = 'doc_invoice'")["client_id"] == "acme"
    else:
        # This reviewer held the write lock: the other one waited, and its lock set is the one that went stale.
        assert seen["other_waited"] is True and error is None and moved["client_id"] == "gamma"
        assert isinstance(other.error, ValueError) and "just moved by someone else" in str(other.error)
        assert _moves(conn) == [("acme", "gamma")]

