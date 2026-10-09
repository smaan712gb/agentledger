"""Adversarial review of the write-ahead deletion (re-audit of 952ee96, finding 4): another worker acts while phase 2's
storage delete is in flight. The first review found (PostgreSQL) that a server-side idle-in-transaction timeout ended
the deleting session during a slow delete, which released its locks: a legal hold was then committed and acknowledged
and the delete landed afterwards, and identical bytes filed meanwhile lost their (shared) object. Asserted now: the
deleting transaction turns the idle timeout off (SET LOCAL), so a hold placed during the delete waits for it and holds
a document already deleted (a serial order), and an upload of identical bytes gets its own object, which the deletion
never touches. The deleting worker's session keeps a 1.5 s idle-in-transaction timeout and storage answers only after
the other worker finished or 3 s passed. Both backends (on SQLite the hold waits for the write lock; the timeout exists
on PostgreSQL only).
"""

from __future__ import annotations

import threading
from datetime import date

from agentledger import db
from agentledger.evidence import records
from agentledger.intake import pipeline
from test_evidence import _doc

TODAY = date(2026, 10, 8)
REASON = "annual retention review"
EXAM = "IRS examination letter dated 2026-10-01"
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

    def waiting(self, seconds: float) -> bool:
        self.thread.join(seconds)
        return self.thread.is_alive()

    def finish(self) -> "Worker":
        self.thread.join(60)
        assert not self.thread.is_alive(), "the other worker never finished"
        return self


def _guard_session(conn):
    """PostgreSQL: the server ends any transaction of this worker's session left idle for 1.5 s."""
    if PG:
        conn._get().raw.execute("SET idle_in_transaction_session_timeout = '1500ms'")
        return conn._get().raw
    return None


def _slow_delete(monkeypatch, vault, other, seconds: float = 3.0):
    """vault.blobs.delete starts `other` (another worker) while the request to storage is outstanding, waits up to
    `seconds` for it (twice the session's idle timeout), then deletes."""
    real = vault.blobs.delete
    seen: dict = {}

    def delete(key):
        if "worker" not in seen:
            seen["worker"] = Worker(other)
            seen["finished_during_delete"] = not seen["worker"].waiting(seconds)
        real(key)

    monkeypatch.setattr(vault.blobs, "delete", delete)
    return seen


def _outcomes(conn, doc_id):
    return [r["outcome"] for r in db.rows(conn, "SELECT r.outcome FROM blob_deletion_results r JOIN blob_deletions b "
                                                "ON b.id = r.deletion_id WHERE b.document_id = ? ORDER BY r.id", doc_id)]


def _purge(conn, vault):
    return records.purge_expired(conn, vault, TODAY, actor="maya", role="cpa", attested=True, reason=REASON)


def test_a_hold_placed_while_the_delete_is_in_flight_is_told_what_it_cannot_keep(biz, monkeypatch):
    """No lock or transaction is open across the storage call (so a session that ends mid-call loses nothing it
    protects): a hold placed meanwhile is placed at once, and in_flight() names the object it cannot keep."""
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2017_payroll", "acme", b"2017 payroll register, fourth quarter", "2025-01-01")
    _guard_session(conn)

    def place():
        hid = records.place_hold(conn, client_id="acme", reason=EXAM, actor="maya", role="cpa")
        return hid, records.in_flight(conn, "acme")

    seen = _slow_delete(monkeypatch, vault, place)
    out = _purge(conn, vault)
    hold = seen["worker"].finish()
    assert [r["document_id"] for r in out] == ["doc_2017_payroll"] and out.failures == []
    assert _outcomes(conn, "doc_2017_payroll") == ["started", "deleted"] and not vault.exists(loc)
    assert seen["finished_during_delete"] is True, "a hold waited on a storage call"
    hid, flying = hold.result
    assert hold.error is None and [h["id"] for h in records.active_holds(conn)] == [hid]
    assert [f["document_id"] for f in flying] == ["doc_2017_payroll"]          # told, not falsely reassured
    assert records.in_flight(conn, "acme") == []                                  # finished since
    held = db.one(conn, "SELECT deleted_at FROM documents WHERE id = 'doc_2017_payroll'")
    assert held["deleted_at"] and records.hold_on(conn, "doc_2017_payroll", "acme")["id"] == hid
    assert records.integrity(conn, vault)["ok"]


def test_identical_bytes_uploaded_while_the_delete_is_in_flight_keep_their_own_object(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    data = b"2017 receipt, uploaded again during the retention run"
    loc = _doc(conn, vault, "doc_old", "acme", data, "2025-01-01")
    _guard_session(conn)
    seen = _slow_delete(monkeypatch, vault, lambda: pipeline.ingest(conn, None, vault, "receipt.txt", data, channel="upload",
                                                                    client_hint="acme"), seconds=10.0 if PG else 3.0)
    out = _purge(conn, vault)
    upload = seen["worker"].finish()
    assert [r["document_id"] for r in out] == ["doc_old"] and out.failures == [] and upload.error is None
    if PG:                                           # the upload takes no lock the deletion holds: it finished meanwhile
        assert seen["finished_during_delete"] is True
    [new] = upload.result
    assert not new.get("duplicate") and new["vault_path"] != loc                  # its own object
    assert vault.read(new["vault_path"]) == data and not vault.exists(loc)
    assert db.one(conn, "SELECT deleted_at FROM documents WHERE id = ?", new["id"])["deleted_at"] is None
    assert db.rows(conn, "SELECT id FROM deletion_receipts WHERE document_id = ?", new["id"]) == []
    report = records.integrity(conn, vault)
    assert report["ok"] and report["live_documents_missing_bytes"] == []
