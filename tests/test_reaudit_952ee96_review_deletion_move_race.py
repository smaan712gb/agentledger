"""Adversarial review of the write-ahead deletion (re-audit of 952ee96, finding 4): a document moved to another client
(pipeline.assign with a move reason) while a retention run deletes it. The first review found that assign read,
checked and rewrote the document outside any transaction or lock, so (PostgreSQL) a document moved into a client under
a legal hold was tombstoned and its bytes destroyed, (both backends) a move that had read the live document re-filed
the tombstone and re-derived its cleared values, and a path-addressed document's recorded deletion left its moved
copy behind while integrity() reported ok. Asserted now: the move and the deletion serialize on both clients' evidence
locks (BEGIN IMMEDIATE on SQLite, advisory locks on PostgreSQL). Each interleaving is forced in both orders, by
starting the other worker while the first holds its locks: the second waits, and the outcome is one of the two serial
ones (the deletion first: the move is refused and nothing is re-filed; the move first: the run skips the moved
document).
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from datetime import date

from agentledger import audit, db
from agentledger.evidence import records
from agentledger.intake import pipeline
from agentledger.ledger import store
from test_evidence import _doc

TODAY = date(2026, 10, 8)
REASON = "annual retention review"
MOVE = "misfiled in 2017: this document belongs to Beta Holdings"
SUBPOENA = "Subpoena, district court case 26-cv-0142: preserve all records"
DELETED = "deleted under the retention policy"


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
        """Still running after `seconds`: it is waiting for a lock the caller's transaction holds."""
        self.thread.join(seconds)
        return self.thread.is_alive()

    def finish(self) -> "Worker":
        self.thread.join(60)
        assert not self.thread.is_alive(), "the other worker never finished"
        return self


def _called_from(name: str, depth: int = 4) -> bool:
    f = sys._getframe(2)
    for _ in range(depth):
        if f is None:
            return False
        if f.f_code.co_name == name:
            return True
        f = f.f_back
    return False


def _purge(conn, vault):
    return records.purge_expired(conn, vault, TODAY, actor="maya", role="cpa", attested=True, reason=REASON)


def _move(conn, vault, doc_id):
    return pipeline.assign(conn, vault, doc_id, "beta", "lee", "cpa", move_reason=MOVE)


def _beta(conn, *, held: bool):
    store.add_client(conn, id="beta", name="Beta Holdings LLC", kind="business")
    return records.place_hold(conn, client_id="beta", reason=SUBPOENA, actor="maya", role="cpa") if held else None


def _deletion_first(conn, vault, monkeypatch, doc_id):
    """The run is deciding about doc_id under its locks when the move starts. Returns (receipts, the move worker,
    whether the move waited)."""
    real = records.retention_end
    seen: dict = {}

    def retention_end(c, pol, d):
        if d["id"] == doc_id and "move" not in seen and _called_from("_commit_deletion"):
            seen["move"] = Worker(lambda: _move(conn, vault, doc_id))
            seen["waited"] = seen["move"].waiting()
        return real(c, pol, d)

    monkeypatch.setattr(records, "retention_end", retention_end)
    receipts = _purge(conn, vault)
    monkeypatch.setattr(records, "retention_end", real)
    assert "move" in seen, "the run never decided about the document"
    return receipts, seen["move"].finish(), seen["waited"]


def _move_first(conn, vault, monkeypatch, doc_id, *, at="on_hold"):
    """The move holds its locks and has re-read doc_id (at its hold check, or right after copying a legacy file) when
    the run starts. Returns (the moved document, the run worker, whether the run waited)."""
    seen: dict = {}

    def start_run():
        if "run" not in seen:
            seen["run"] = Worker(lambda: _purge(conn, vault))
            seen["waited"] = seen["run"].waiting()

    if at == "on_hold":
        real_on_hold = records.on_hold

        def on_hold(c, d):
            if _called_from("assign"):
                start_run()
            return real_on_hold(c, d)

        monkeypatch.setattr(records, "on_hold", on_hold)
    else:
        real_copy = vault.copy

        def copy(src, dst):
            real_copy(src, dst)                       # the file is at the new client's path, the record not yet
            start_run()

        monkeypatch.setattr(vault, "copy", copy)
    moved = _move(conn, vault, doc_id)
    assert "run" in seen, "the move never reached the point where the run starts"
    run = seen["run"].finish()
    assert run.error is None, run.error
    return moved, run, seen["waited"]


def _receipts(conn, doc_id):
    return db.rows(conn, "SELECT client_id FROM deletion_receipts WHERE document_id = ?", doc_id)


# ------------------------------------------------------------------------------ a move into a client under a legal hold
def test_a_move_into_a_held_client_waits_for_the_deletion_and_is_refused(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    _beta(conn, held=True)
    loc = _doc(conn, vault, "doc_misfiled", "acme", b"2017 invoice addressed to Beta Holdings LLC", "2025-01-01")
    receipts, move, waited = _deletion_first(conn, vault, monkeypatch, "doc_misfiled")
    assert waited, "the move did not wait for the deletion deciding about the document"
    assert [r["document_id"] for r in receipts] == ["doc_misfiled"] and receipts.failures == []
    assert isinstance(move.error, ValueError) and DELETED in str(move.error)
    d = db.one(conn, "SELECT client_id, deleted_at FROM documents WHERE id = 'doc_misfiled'")
    assert d["client_id"] == "acme" and d["deleted_at"] and _receipts(conn, "doc_misfiled") == [{"client_id": "acme"}]
    assert db.rows(conn, "SELECT id FROM document_moves WHERE document_id = 'doc_misfiled'") == []
    assert not vault.exists(loc)                     # acme's document, under no hold: it never became Beta's


def test_a_deletion_during_a_move_into_a_held_client_waits_and_skips_it(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    hold = _beta(conn, held=True)
    loc = _doc(conn, vault, "doc_misfiled", "acme", b"2017 invoice addressed to Beta Holdings LLC", "2025-01-01")
    moved, run, waited = _move_first(conn, vault, monkeypatch, "doc_misfiled")
    assert waited, "the run did not wait for the move"
    assert list(run.result) == [] and run.result.failures == []
    assert moved["client_id"] == "beta" and moved["deleted_at"] is None and vault.exists(loc)
    assert records.hold_on(conn, "doc_misfiled", "beta")["id"] == hold and _receipts(conn, "doc_misfiled") == []
    assert moved["retention_confirmed_at"] is None   # confirmed again for Beta before any run considers it


# ------------------------------------------------------------------------------ the values a move re-derives
def _nec(conn, vault):
    _doc(conn, vault, "doc_nec", "acme", b"Form 1099-NEC 2017 Payer: Brightline LLC Box 1 1,200.00", "2025-01-01",
         doc_type="1099-NEC")
    conn.execute("UPDATE documents SET tax_year = 2017, fields = ? WHERE id = 'doc_nec'",
                 (json.dumps({"payer_name": "Brightline LLC", "box1": "1200.00"}),))
    note = "IRS account transcript shows the 2017 return received 2018-04-10"
    records.record_tax_event(conn, "acme", 2017, "filed", "2018-04-10", actor="maya", role="cpa", note=note, form="1120-S")
    records.record_tax_event(conn, "acme", 2017, "owners_filed", "2018-04-10", actor="maya", role="cpa", form="1040",
                             note="owners' 2017 Forms 1040 on record, transcripts in the permanent file")
    assert [d["id"] for d in records.due_for_deletion(conn, TODAY)] == ["doc_nec"]


def _derived(conn, doc_id):
    return db.rows(conn, "SELECT client_id, payer, amount FROM info_returns WHERE document_id = ? ORDER BY id", doc_id)


def test_a_move_after_the_deletion_never_refiles_the_tombstone(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    _beta(conn, held=False)
    _nec(conn, vault)
    receipts, move, waited = _deletion_first(conn, vault, monkeypatch, "doc_nec")
    assert waited and [r["document_id"] for r in receipts] == ["doc_nec"]
    assert isinstance(move.error, ValueError) and DELETED in str(move.error)
    d = db.one(conn, "SELECT client_id, deleted_at, fields FROM documents WHERE id = 'doc_nec'")
    assert d["client_id"] == "acme" and d["deleted_at"] and d["fields"] == "{}"
    assert _derived(conn, "doc_nec") == []           # nothing re-derived from the cleared values


def test_a_deletion_during_a_move_waits_and_skips_the_moved_document(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    _beta(conn, held=False)
    _nec(conn, vault)
    moved, run, waited = _move_first(conn, vault, monkeypatch, "doc_nec")
    assert waited and list(run.result) == [] and run.result.failures == []
    assert moved["client_id"] == "beta" and moved["deleted_at"] is None and json.loads(moved["fields"])["box1"] == "1200.00"
    assert _derived(conn, "doc_nec") == [{"client_id": "beta", "payer": "Brightline LLC", "amount": "1200.00"}]
    assert _receipts(conn, "doc_nec") == []


# ------------------------------------------------------------------------------ documents filed before content addressing
def _legacy(conn, vault):
    data = b"2016 receipt scanned before content addressing"
    sha = hashlib.sha256(data).hexdigest()
    old = f"acme/undated/receipts/2017-01-05_Receipt_scan_{sha[:8]}.txt"
    vault.write(old, data)
    conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, "
                 "vault_path, fields, doc_type, retention_class, retention_confirmed_by, retention_confirmed_at) VALUES "
                 "('doc_legacy', 'acme', ?, 'scan.txt', 'text/plain', 'upload', '2017-01-05T00:00:00+00:00', 'filed', ?, "
                 "'{}', 'Receipt', 'tax_return_support', 'maya', ?)", (sha, old, audit.now()))
    assert [d["id"] for d in records.due_for_deletion(conn, TODAY)] == ["doc_legacy"]
    return data, old


def _files(vault, client):
    root = vault.root / client
    return sorted(p.relative_to(vault.root).as_posix() for p in root.rglob("*") if p.is_file()) if root.exists() else []


def test_a_legacy_document_moved_during_its_deletion_keeps_one_copy_at_the_new_client(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    _beta(conn, held=False)
    data, old = _legacy(conn, vault)
    moved, run, waited = _move_first(conn, vault, monkeypatch, "doc_legacy", at="copy")
    assert waited, "the run did not wait for the move that had already copied the file"
    assert list(run.result) == [] and run.result.failures == [] and _receipts(conn, "doc_legacy") == []
    new = moved["vault_path"]
    assert moved["client_id"] == "beta" and new.startswith("beta/") and vault.read(new) == data
    assert not vault.exists(old) and _files(vault, "acme") == [] and _files(vault, "beta") == [new]
    report = records.integrity(conn, vault)
    assert report["ok"] and report["live_documents_missing_bytes"] == [] and report["pending_deletions"] == []


def test_a_legacy_document_deleted_before_a_move_is_never_copied(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    _beta(conn, held=False)
    data, old = _legacy(conn, vault)
    receipts, move, waited = _deletion_first(conn, vault, monkeypatch, "doc_legacy")
    assert waited and [r["document_id"] for r in receipts] == ["doc_legacy"] and receipts.failures == []
    assert isinstance(move.error, ValueError) and DELETED in str(move.error)
    outcomes = [r["outcome"] for r in db.rows(conn, "SELECT r.outcome FROM blob_deletion_results r JOIN blob_deletions b "
                                                    "ON b.id = r.deletion_id WHERE b.document_id = 'doc_legacy'")]
    assert outcomes == ["started", "deleted"] and not vault.exists(old)
    assert _files(vault, "acme") == [] and _files(vault, "beta") == []     # no copy survives the recorded deletion
    assert records.integrity(conn, vault)["ok"]
