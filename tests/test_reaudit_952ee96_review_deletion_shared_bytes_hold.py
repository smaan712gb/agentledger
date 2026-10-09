"""Adversarial review of the write-ahead deletion (re-audit of 952ee96, finding 4): one stored object shared by a
deleted document whose bytes a legal hold keeps and by another document. The first review found that phase 2 checked
holds only on the document whose pending deletion it processed, so a later copy's deletion destroyed bytes a hold kept.
Two new documents can no longer share an object (intake addresses each object by its document), so the shared case
is now an object stored before per-document addressing (vault.put without an owner, as test_evidence._doc stores it).
Asserted now: no deletion removes an object while a hold covers any document, live or deleted, that references it,
and the report says why and that the bytes are present; a copy filed today has its own object, whose deletion never
touches the held one. Both backends.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from agentledger import audit, db
from agentledger.evidence import records
from agentledger.intake import pipeline
from agentledger.ledger import store
from test_evidence import _doc

REASON = "annual retention review"
STATEMENT = (b"Joint brokerage account ending 4471. Statement period January 1 to December 31.\n"
             b"Opening balance $12,000.00 ... closing balance $15,250.00\n")
EXAM = "IRS examination of Acme 2010-2016, preserve all records"


class _StorageDown:
    """The vault's blob store with deletes failing (an R2 outage during the retention run)."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def delete(self, key):
        raise OSError("injected: storage unavailable")


def _at(y, m, d):
    return audit.clock(datetime(y, m, d, 12, tzinfo=timezone.utc))


def _purge(conn, vault, day):
    return records.purge_expired(conn, vault, day, actor="maya", role="cpa", attested=True, reason=REASON)


def _outcomes(conn, doc_id):
    return [r["outcome"] for r in db.rows(conn, "SELECT r.outcome FROM blob_deletion_results r JOIN blob_deletions b "
                                                "ON b.id = r.deletion_id WHERE b.document_id = ? ORDER BY r.id", doc_id)]


def _acme_deleted_and_held(conn, vault, monkeypatch):
    """2018: Acme's copy reaches the end of its retention; the receipt commits while storage is down, so its object
    deletion stays pending with the bytes present. An IRS examination of Acme opens: a hold keeps those bytes."""
    store.add_client(conn, id="dana-ortiz", name="Dana Ortiz", kind="individual")
    loc = _doc(conn, vault, "doc_acme_statement", "acme", STATEMENT, "2018-01-01")
    real = vault.blobs
    monkeypatch.setattr(vault, "blobs", _StorageDown(real))
    with _at(2018, 2, 1):
        first = _purge(conn, vault, date(2018, 2, 1))
    assert [r["document_id"] for r in first] == ["doc_acme_statement"] and vault.exists(loc)
    monkeypatch.setattr(vault, "blobs", real)
    with _at(2018, 2, 15):
        hold = records.place_hold(conn, client_id="acme", reason=EXAM, actor="maya", role="cpa")
    return loc, hold


def test_a_copy_sharing_a_pre_addressing_object_never_deletes_bytes_a_hold_keeps(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    loc, hold = _acme_deleted_and_held(conn, vault, monkeypatch)
    # Dana's copy of the same statement, stored before per-document addressing: the same object.
    assert _doc(conn, vault, "doc_dana_statement", "dana-ortiz", STATEMENT, "2026-01-01") == loc

    # 2026: the examination of Acme is still open. Dana's copy reaches the end of its own retention.
    out = _purge(conn, vault, date(2026, 10, 8))
    assert [r["document_id"] for r in out] == ["doc_dana_statement"]
    stages = {f["document_id"]: f for f in out.failures}
    assert set(stages) == {"doc_acme_statement", "doc_dana_statement"}
    for f in stages.values():
        assert f["stage"] == "held" and f["bytes_present"] is True
        assert f"legal hold #{hold} covers document doc_acme_statement" in f["error"]
    assert vault.exists(loc), "bytes a legal hold keeps were deleted with another document's copy"
    pending = records.integrity(conn, vault)["pending_deletions"]
    assert sorted(p["document_id"] for p in pending) == ["doc_acme_statement", "doc_dana_statement"]
    assert all(p["held"] is True and p["bytes_present"] is True for p in pending)

    # The examination closes: the next run deletes the object once, and both pending deletions complete.
    records.release_hold(conn, hold, reason="examination closed, no-change letter received", actor="maya", role="cpa")
    out = _purge(conn, vault, date(2026, 10, 9))
    assert out == [] and out.failures == [] and not vault.exists(loc)
    assert _outcomes(conn, "doc_acme_statement")[-1] == "deleted" and _outcomes(conn, "doc_dana_statement")[-1] == "deleted"
    assert records.integrity(conn, vault)["ok"]


def test_a_copy_filed_today_has_its_own_object_and_its_deletion_keeps_the_held_bytes(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    loc, hold = _acme_deleted_and_held(conn, vault, monkeypatch)
    # The co-owner sends the same statement for her own file (the tombstone released the hash: a new document).
    with _at(2018, 3, 1):
        [copy] = pipeline.ingest(conn, None, vault, "brokerage-statement.txt", STATEMENT, channel="upload",
                                 client_hint="dana-ortiz")
    assert copy["status"] == "filed" and not copy.get("duplicate") and copy["vault_path"] != loc
    records.confirm_retention(conn, copy["id"], tax_year=None, retention_class="tax_return_support", actor="maya",
                              role="cpa", note="statement checked, no tax year relied on")

    out = _purge(conn, vault, date(2026, 10, 8))
    assert [r["document_id"] for r in out] == [copy["id"]]
    assert [(f["document_id"], f["stage"]) for f in out.failures] == [("doc_acme_statement", "held")]
    assert _outcomes(conn, copy["id"]) == ["started", "deleted"] and not vault.exists(copy["vault_path"])
    assert vault.exists(loc) and vault.read(loc) == STATEMENT                       # the held bytes are untouched
    [pending] = records.integrity(conn, vault)["pending_deletions"]
    assert pending["document_id"] == "doc_acme_statement" and pending["held"] is True and pending["bytes_present"] is True
    assert [h["id"] for h in records.active_holds(conn)] == [hold]
