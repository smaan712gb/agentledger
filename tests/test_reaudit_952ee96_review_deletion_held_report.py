"""Adversarial review of the write-ahead deletion (re-audit of 952ee96, finding 4): what a run and integrity() report
for a pending deletion that a legal hold now covers. The first review found that after a storage delete that landed
but raised, a later hold was reported as "the bytes are kept until it is released" and integrity() marked it held
without looking at storage, although the bytes were already gone. Asserted now: the held report and integrity() look
at storage (bytes_present) and say the bytes are kept only while they exist. Both backends.
"""

from __future__ import annotations

from datetime import date

from agentledger import db
from agentledger.evidence import records
from test_evidence import _doc

TODAY = date(2026, 10, 8)
REASON = "annual retention review"
EXAM = "IRS examination letter dated 2026-10-01"


def _purge(conn, vault):
    return records.purge_expired(conn, vault, TODAY, actor="maya", role="cpa", attested=True, reason=REASON)


def _outcomes(conn, doc_id):
    return [r["outcome"] for r in db.rows(conn, "SELECT r.outcome FROM blob_deletion_results r JOIN blob_deletions b "
                                                "ON b.id = r.deletion_id WHERE b.document_id = ? ORDER BY r.id", doc_id)]


def test_a_hold_after_a_delete_that_landed_but_raised_reports_the_bytes_gone(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2017_payroll", "acme", b"2017 payroll register, fourth quarter", "2025-01-01")
    real = vault.blobs.delete

    def lands_then_raises(key):
        real(key)
        raise TimeoutError("injected: R2 carried out the delete, the reply was lost")

    monkeypatch.setattr(vault.blobs, "delete", lands_then_raises)
    first = _purge(conn, vault)
    assert [r["document_id"] for r in first] == ["doc_2017_payroll"] and [f["stage"] for f in first.failures] == ["storage"]
    assert not vault.exists(loc) and _outcomes(conn, "doc_2017_payroll") == ["started", "failed"]
    monkeypatch.setattr(vault.blobs, "delete", real)

    hold = records.place_hold(conn, client_id="acme", reason=EXAM, actor="maya", role="cpa")
    out = _purge(conn, vault)
    [held] = out.failures
    assert out == [] and held["stage"] == "held" and held["bytes_present"] is False
    assert f"legal hold #{hold}" in held["error"] and "no longer stored" in held["error"] and "kept" not in held["error"]
    report = records.integrity(conn, vault)
    [pending] = report["pending_deletions"]
    assert pending["document_id"] == "doc_2017_payroll" and pending["held"] is True and pending["bytes_present"] is False
    assert report["ok"] is False and report["live_documents_missing_bytes"] == []

    records.release_hold(conn, hold, reason="examination closed, no-change letter received", actor="maya", role="cpa")
    out = _purge(conn, vault)
    assert out == [] and out.failures == [] and _outcomes(conn, "doc_2017_payroll") == ["started", "failed", "started", "deleted"]
    assert records.integrity(conn, vault)["ok"] is True


def test_a_hold_after_a_delete_that_failed_reports_the_bytes_kept(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2017_payroll", "acme", b"2017 payroll register, fourth quarter", "2025-01-01")

    def unavailable(key):
        raise OSError("injected: storage unavailable, nothing deleted")

    real = vault.blobs.delete
    monkeypatch.setattr(vault.blobs, "delete", unavailable)
    first = _purge(conn, vault)
    assert [f["stage"] for f in first.failures] == ["storage"] and vault.exists(loc)
    monkeypatch.setattr(vault.blobs, "delete", real)

    hold = records.place_hold(conn, client_id="acme", reason=EXAM, actor="maya", role="cpa")
    out = _purge(conn, vault)
    [held] = out.failures
    assert held["stage"] == "held" and held["bytes_present"] is True
    assert f"legal hold #{hold}" in held["error"] and "kept until it is released" in held["error"]
    [pending] = records.integrity(conn, vault)["pending_deletions"]
    assert pending["held"] is True and pending["bytes_present"] is True and vault.exists(loc)
