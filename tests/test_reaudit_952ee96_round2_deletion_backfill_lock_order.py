"""Second adversarial review of the write-ahead deletion (re-audit of 952ee96, finding 4): the relied-on index backfill
(Returns._backfill_uses, run once on the first Returns() of a store saved before return_document_uses existed) took
each return's evidence lock in return-id order and kept it, while a move between two clients takes both locks sorted,
so on PostgreSQL the two deadlocked and one of them failed. Asserted now: the backfill takes every client's evidence
lock first, sorted, before indexing anything (both backends); a move started while the backfill holds them, and a
backfill started while a move holds them, wait for the other and both complete (PostgreSQL, each wait seen in
pg_locks).
"""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agentledger import db
from agentledger.evidence import records
from agentledger.intake.pipeline import assign
from agentledger.ledger import store
from agentledger.returns import store as returns_store
from agentledger.returns.store import Returns
from test_evidence import _doc

PG = db.backend() == "postgres"
pg_only = pytest.mark.skipif(not PG, reason="PostgreSQL advisory locks; SQLite serializes every write")
MOVE = "Zoe Zeta's receipt, filed to Alpha by mistake"
ALPHA_RETURN, ZETA_RETURN = "ret_ffffffffffff", "ret_000000000001"     # Zeta's return comes first in return-id order


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

    def blocked(self, conn, seconds: float = 15.0) -> bool:
        """Its session waits for a lock (an ungranted lock in pg_locks) while the caller's transaction holds it."""
        end = time.monotonic() + seconds
        while time.monotonic() < end and self.thread.is_alive():
            if conn.execute("SELECT 1 FROM pg_locks WHERE pid = ? AND NOT granted", (self.pid,)).fetchone():
                return True
            time.sleep(0.02)
        return False

    def finish(self) -> "Worker":
        self.thread.join(60)
        assert not self.thread.is_alive(), "the other worker never finished"
        return self


def _called_from(name: str, depth: int = 2) -> bool:
    f = sys._getframe(2)
    for _ in range(depth):
        if f is None:
            return False
        if f.f_code.co_name == name:
            return True
        f = f.f_back
    return False


def _store_before_the_index(biz):
    """Alpha's and Zeta's returns relying on their W-2s, saved before return_document_uses existed (the next Returns()
    backfills it), and a receipt of Zeta's filed to Alpha by mistake."""
    conn, vault = biz.conn, biz.vault
    for cid, name in (("alpha", "Alex Alpha"), ("zeta", "Zoe Zeta")):
        store.add_client(conn, id=cid, name=name, kind="individual")
    _doc(conn, vault, "doc_alpha_w2", "alpha", b"Alex Alpha 2025 Form W-2", "2033-01-01")
    _doc(conn, vault, "doc_zeta_w2", "zeta", b"Zoe Zeta 2025 Form W-2", "2033-01-01")
    _doc(conn, vault, "doc_misfiled", "alpha", b"Zoe Zeta's receipt, filed to Alpha by mistake", "2033-01-01")
    R = Returns(conn, biz.kb)
    ids = iter([ALPHA_RETURN[4:], ZETA_RETURN[4:]])
    with pytest.MonkeyPatch.context() as m:
        m.setattr(returns_store, "secrets", SimpleNamespace(token_hex=lambda n: next(ids)))
        for client, doc in (("alpha", "doc_alpha_w2"), ("zeta", "doc_zeta_w2")):
            R.create(client, 2025, "maya", {}, provenance={"w2s[0].wages": {"document_id": doc, "box": "box1", "value": "1.00",
                                                                           "confirmed": False}})
    conn.execute("UPDATE kv SET key = 'return_document_uses_indexed_before' WHERE key = ?", (returns_store.USES_INDEXED,))
    assert not records.uses_indexed(conn)


def test_the_backfill_takes_every_clients_evidence_lock_first_in_one_order(biz, monkeypatch):
    conn = biz.conn
    _store_before_the_index(biz)
    calls: list[str] = []
    real_lock, real_uses = returns_store.lock, Returns._record_uses

    def lock(c, key):
        calls.append(key)
        real_lock(c, key)

    def record_uses(self, rid, documents, version):
        calls.append(f"index {rid}")
        real_uses(self, rid, documents, version)

    monkeypatch.setattr(returns_store, "lock", lock)
    monkeypatch.setattr(Returns, "_record_uses", record_uses)
    Returns(conn, biz.kb)                                          # the backfill
    assert [c for c in calls if c.startswith("index")] == [f"index {ZETA_RETURN}", f"index {ALPHA_RETURN}"]
    first = calls.index(f"index {ZETA_RETURN}")
    assert calls[:first] == ["evidence:alpha", "evidence:zeta"], calls   # every client, sorted, before any return
    assert records.uses_indexed(conn)


@pg_only
def test_a_move_started_while_the_backfill_holds_the_locks_waits_and_neither_fails(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    _store_before_the_index(biz)
    real_lock = returns_store.lock
    seen: dict = {}

    def lock(c, key):
        real_lock(c, key)
        if key == "evidence:zeta" and "move" not in seen and _called_from("_backfill_uses"):
            # The backfill holds evidence:zeta (Zeta's return comes first): a reviewer moves the misfiled receipt now.
            seen["move"] = Worker(conn, lambda: assign(conn, vault, "doc_misfiled", "zeta", "lee", move_reason=MOVE))
            seen["move_waited"] = seen["move"].blocked(conn)

    monkeypatch.setattr(returns_store, "lock", lock)
    Returns(conn, biz.kb)                                          # the backfill: no deadlock aborts it
    assert "move" in seen, "the backfill never locked evidence:zeta"
    move = seen["move"].finish()
    assert seen["move_waited"] is True, "the move did not wait for the backfill"
    assert move.error is None, f"the move failed: {move.error!r}"
    assert move.result["client_id"] == "zeta" and records.uses_indexed(conn)
    assert db.rows(conn, "SELECT from_client, to_client FROM document_moves WHERE document_id = 'doc_misfiled'") == [
        {"from_client": "alpha", "to_client": "zeta"}]


@pg_only
def test_a_backfill_started_while_a_move_holds_the_locks_waits_and_neither_fails(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    _store_before_the_index(biz)
    real_on_hold = records.on_hold
    seen: dict = {}

    def on_hold(c, d):
        if "backfill" not in seen and _called_from("assign", 1):
            # The move holds evidence:alpha and evidence:zeta: another worker opens the returns store (the backfill) now.
            seen["backfill"] = Worker(conn, lambda: Returns(conn, biz.kb))
            seen["backfill_waited"] = seen["backfill"].blocked(conn)
        return real_on_hold(c, d)

    monkeypatch.setattr(records, "on_hold", on_hold)
    moved = assign(conn, vault, "doc_misfiled", "zeta", "lee", move_reason=MOVE)
    assert "backfill" in seen, "the move never checked for holds"
    backfill = seen["backfill"].finish()
    assert seen["backfill_waited"] is True, "the backfill did not wait for the move"
    assert backfill.error is None, f"the backfill failed: {backfill.error!r}"
    assert moved["client_id"] == "zeta" and records.uses_indexed(conn)
