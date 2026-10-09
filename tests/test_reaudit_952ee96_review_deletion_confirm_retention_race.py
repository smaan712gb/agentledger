"""Adversarial review of the write-ahead deletion (re-audit of 952ee96, findings 4 and 5): confirming a document's
retention while a retention run deletes it. The first review found (PostgreSQL) that confirm_retention read the
document and wrote its new class without any lock, so a run deleted the document in between and the confirmation then
succeeded on the tombstone: the CPA was told a deleted document was now a basis record kept until released. Asserted
now: confirm_retention re-reads the document under the client's evidence lock and updates it only while it is live
and filed to that client, and a run deciding about the document serializes with it; both orders are forced (the
other worker starts while the first holds its locks) and each ends in a serial outcome. A review-inbox document is
refused. Both backends.
"""

from __future__ import annotations

import sys
import threading
from datetime import date

import pytest

from agentledger import db
from agentledger.evidence import records
from agentledger.intake import pipeline
from test_evidence import _doc

TODAY = date(2026, 10, 8)
REASON = "annual retention review"
NOTE = "a closing disclosure: basis record, keep until the property is sold"


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


def _purge(conn, vault):
    return records.purge_expired(conn, vault, TODAY, actor="lee", role="cpa", attested=True, reason=REASON)


def _confirm(conn):
    return records.confirm_retention(conn, "doc_closing", tax_year=None, retention_class="property_basis", actor="maya",
                                     role="cpa", note=NOTE)


def _closing(conn, vault):
    loc = _doc(conn, vault, "doc_closing", "acme", b"2017 closing disclosure, 12 Elm Street", "2025-01-01")
    assert [d["id"] for d in records.due_for_deletion(conn, TODAY)] == ["doc_closing"]   # due under its old class
    return loc


def test_a_run_during_a_confirmation_waits_and_keeps_the_basis_record(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    loc = _closing(conn, vault)
    real = records.retention_for
    seen: dict = {}

    def retention_for(*args, **kwargs):
        # confirm_retention holds the client's lock and has re-read the live document: a run starts now.
        if "run" not in seen and sys._getframe(1).f_code.co_name == "confirm_retention":
            seen["run"] = Worker(lambda: _purge(conn, vault))
            seen["waited"] = seen["run"].waiting()
        return real(*args, **kwargs)

    monkeypatch.setattr(records, "retention_for", retention_for)
    doc = _confirm(conn)
    run = seen["run"].finish()
    assert seen["waited"], "the run did not wait for the confirmation"
    assert run.error is None and list(run.result) == [] and run.result.failures == []
    state = db.one(conn, "SELECT deleted_at, retention_class FROM documents WHERE id = 'doc_closing'")
    assert doc["retention_class"] == "property_basis" and state == {"deleted_at": None, "retention_class": "property_basis"}
    assert vault.exists(loc) and records.due_for_deletion(conn, TODAY) == []


def test_a_confirmation_during_a_deletion_waits_and_is_refused(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    loc = _closing(conn, vault)
    real = records.retention_end
    seen: dict = {}

    def retention_end(c, pol, d):
        if "confirm" not in seen and sys._getframe(1).f_code.co_name == "_commit_deletion":
            seen["confirm"] = Worker(lambda: _confirm(conn))
            seen["waited"] = seen["confirm"].waiting()
        return real(c, pol, d)

    monkeypatch.setattr(records, "retention_end", retention_end)
    out = _purge(conn, vault)
    confirm = seen["confirm"].finish()
    assert seen["waited"], "the confirmation did not wait for the deletion"
    assert [r["document_id"] for r in out] == ["doc_closing"] and out.failures == [] and not vault.exists(loc)
    assert isinstance(confirm.error, KeyError) and confirm.result is None     # refused: the document is gone
    state = db.one(conn, "SELECT deleted_at, retention_class FROM documents WHERE id = 'doc_closing'")
    assert state["deleted_at"] and state["retention_class"] == "tax_return_support"
    actions = [r["action"] for r in db.rows(conn, "SELECT action FROM audit WHERE action IN ('evidence.deleted', "
                                                  "'evidence.retention_confirmed') ORDER BY seq")]
    assert actions == ["evidence.deleted"]


def test_a_review_inbox_document_cannot_be_confirmed(biz):
    conn, vault = biz.conn, biz.vault
    [doc] = pipeline.ingest(conn, None, vault, "scan.bin", b"\x00\x01 unreadable scan", channel="upload")
    assert doc["status"] == "needs_review" and doc["client_id"] is None
    with pytest.raises(records.RetentionError, match="file the document to a client first"):
        records.confirm_retention(conn, doc["id"], tax_year=None, retention_class="tax_return_support", actor="maya",
                                  role="cpa", note="checked the scan, no tax year")
    assert db.one(conn, "SELECT retention_confirmed_at FROM documents WHERE id = ?", doc["id"])["retention_confirmed_at"] is None
