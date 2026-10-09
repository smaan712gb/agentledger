"""Adversarial review of the write-ahead deletion (re-audit of 952ee96, finding 4): a deletion locks the client it read
before taking the lock. The first review found (PostgreSQL) that _commit_deletion locked the client it had read, while
pipeline.assign moved the document to another client without any lock; the deletion then decided for the new client
under the old client's lock, so a hold placed on the new client meanwhile was not serialized with it and the document
was tombstoned under a hold on record first. Asserted now: a move in that window is either serialized before the
deletion takes its lock (PostgreSQL: the move commits, the deletion re-reads the document under its lock, sees it was
moved and leaves it for the next run, which a hold on the new client then keeps it from) or after it (SQLite: the
deletion's BEGIN IMMEDIATE makes the move wait, and the move of the deleted document is refused).
"""

from __future__ import annotations

import sys
import threading
from datetime import date

from agentledger import db
from agentledger.evidence import records
from agentledger.intake.pipeline import assign
from agentledger.ledger import store
from test_evidence import _doc

TODAY = date(2026, 10, 8)
REASON = "annual retention review"
PG = db.backend() == "postgres"


class Worker:
    """Another API worker: `fn` runs in its own thread, on its own database connection."""

    def __init__(self, fn):
        self.result, self.error = None, None
        self.thread = threading.Thread(target=self._run, args=(fn,), daemon=True)
        self.thread.start()

    def _run(self, fn):
        try:
            self.result = fn()
        except Exception as exc:
            self.error = exc

    def waiting(self, seconds: float = 1.5) -> bool:
        self.thread.join(seconds)
        return self.thread.is_alive()

    def finish(self) -> "Worker":
        self.thread.join(60)
        assert not self.thread.is_alive(), "the other worker never finished"
        return self


def _in_commit_deletion() -> bool:
    f = sys._getframe(2)                          # lock <- evidence_lock <- _commit_deletion
    for _ in range(3):
        if f is None:
            return False
        if f.f_code.co_name == "_commit_deletion":
            return True
        f = f.f_back
    return False


def test_a_move_before_the_deletion_takes_its_lock_is_seen_under_the_lock(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    store.add_client(conn, id="beta", name="Beta Builders LLC", kind="business")
    loc = _doc(conn, vault, "doc_misfiled", "acme", b"2017 supplier invoice, Beta Builders", "2025-01-01")
    assert [d["id"] for d in records.due_for_deletion(conn, TODAY)] == ["doc_misfiled"]
    real_lock = records.lock
    seen: dict = {}

    def lock(c, key):
        if key == "evidence:acme" and "move" not in seen and _in_commit_deletion():
            # The deletion read client acme and is about to lock it: a reviewer moves the document to Beta now.
            seen["move"] = Worker(lambda: assign(conn, vault, "doc_misfiled", "beta", "lee",
                                                 move_reason="Beta Builders' invoice, filed to Acme by mistake"))
            seen["waited"] = seen["move"].waiting(15 if PG else 1.5)    # PostgreSQL: returns once the move commits
        return real_lock(c, key)

    monkeypatch.setattr(records, "lock", lock)
    out = records.purge_expired(conn, vault, TODAY, actor="lee", role="cpa", attested=True, reason=REASON)
    move = seen["move"].finish()
    monkeypatch.setattr(records, "lock", real_lock)
    doc = db.one(conn, "SELECT client_id, deleted_at FROM documents WHERE id = 'doc_misfiled'")
    receipts = db.rows(conn, "SELECT client_id FROM deletion_receipts WHERE document_id = 'doc_misfiled'")
    if PG:
        # The move committed before the deletion's lock; under the lock the deletion saw it and decided nothing.
        assert seen["waited"] is False and move.error is None and move.result["client_id"] == "beta"
        assert out == [] and out.failures == [] and receipts == []
        assert doc == {"client_id": "beta", "deleted_at": None} and vault.exists(loc)
        hold = records.place_hold(conn, client_id="beta", actor="maya", role="cpa",
                                  reason="IRS summons dated 2026-10-07 for Beta Builders")
        assert records.hold_on(conn, "doc_misfiled", "beta")["id"] == hold
        assert records.purge_expired(conn, vault, TODAY, actor="lee", role="cpa", attested=True, reason=REASON) == []
        assert vault.exists(loc)
    else:
        # The deletion held the write lock: the move waited, and the deleted document cannot be moved.
        assert seen["waited"] is True and [r["document_id"] for r in out] == ["doc_misfiled"] and out.failures == []
        assert isinstance(move.error, ValueError) and "deleted under the retention policy" in str(move.error)
        assert doc["client_id"] == "acme" and doc["deleted_at"] and receipts == [{"client_id": "acme"}]
        assert db.rows(conn, "SELECT id FROM document_moves WHERE document_id = 'doc_misfiled'") == []
