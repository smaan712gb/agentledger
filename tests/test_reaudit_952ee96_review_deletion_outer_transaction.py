"""Adversarial review of the write-ahead deletion (re-audit of 952ee96, finding 4): purge_expired called by code that
already has a transaction open, or with a session that cannot see the whole firm. The first review found that inside
an open transaction phase 1's "commit" was only a savepoint release, so phase 2 deleted bytes whose receipt the
caller's rollback then took away. Asserted now: a run refuses to start inside any open transaction (opened by
db.unit_of_work or by hand), under a client-scoped PostgreSQL session, or without the CPA's attestation, with the
reason, before touching anything; with document_ids it deletes only the documents listed. Both backends.
"""

from __future__ import annotations

from datetime import date

import pytest

from agentledger import db
from agentledger.evidence import records
from test_evidence import _doc

TODAY = date(2026, 10, 8)
REASON = "annual retention review"


class _CallerFails(Exception):
    pass


def _purge(conn, vault, **kw):
    return records.purge_expired(conn, vault, TODAY, actor="maya", role="cpa", reason=REASON, **{"attested": True, **kw})


def _untouched(conn, vault, doc_id, loc):
    d = db.one(conn, "SELECT deleted_at FROM documents WHERE id = ?", doc_id)
    assert d["deleted_at"] is None and vault.exists(loc)
    assert db.rows(conn, "SELECT id FROM deletion_receipts WHERE document_id = ?", doc_id) == []
    assert records.pending_deletions(conn) == []


def test_purge_inside_a_unit_of_work_is_refused_before_anything_happens(biz):
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2017_receipts", "acme", b"2017 Schedule C expense receipts", "2025-01-01")
    with pytest.raises(_CallerFails):
        with db.unit_of_work(conn):                # e.g. a scheduled job or a command wrapper around the run
            with pytest.raises(records.RetentionError, match="outside any open transaction"):
                _purge(conn, vault)
            raise _CallerFails("the caller's next step fails, so its transaction rolls back")
    _untouched(conn, vault, "doc_2017_receipts", loc)
    out = _purge(conn, vault)                      # outside the transaction the same run goes ahead
    assert [r["document_id"] for r in out] == ["doc_2017_receipts"] and out.failures == [] and not vault.exists(loc)


def test_purge_inside_a_transaction_opened_by_hand_is_refused(biz):
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2017_receipts", "acme", b"2017 Schedule C expense receipts", "2025-01-01")
    conn.execute("BEGIN")
    try:
        with pytest.raises(records.RetentionError, match="outside any open transaction"):
            _purge(conn, vault)
    finally:
        conn.execute("ROLLBACK")
    _untouched(conn, vault, "doc_2017_receipts", loc)


@pytest.mark.skipif(db.backend() != "postgres", reason="client-scoped sessions exist on PostgreSQL only")
def test_purge_in_a_client_scoped_session_is_refused(biz):
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2017_receipts", "acme", b"2017 Schedule C expense receipts", "2025-01-01")
    conn.set_scope(["acme"])                       # a request scoped to one client
    try:
        with pytest.raises(records.RetentionError, match="firm-wide"):
            _purge(conn, vault)
    finally:
        conn.set_scope(["*"])
    _untouched(conn, vault, "doc_2017_receipts", loc)


def test_purge_without_the_attestation_is_refused(biz):
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2017_receipts", "acme", b"2017 Schedule C expense receipts", "2025-01-01")
    with pytest.raises(records.RetentionError, match="attestation"):
        _purge(conn, vault, attested=False)
    _untouched(conn, vault, "doc_2017_receipts", loc)


def test_purge_with_document_ids_deletes_only_the_reviewed_list(biz):
    conn, vault = biz.conn, biz.vault
    reviewed = _doc(conn, vault, "doc_reviewed", "acme", b"2017 receipts the CPA reviewed", "2025-01-01")
    other = _doc(conn, vault, "doc_not_listed", "acme", b"2017 receipts not on the reviewed list", "2025-01-01")
    assert {d["id"] for d in records.due_for_deletion(conn, TODAY)} == {"doc_reviewed", "doc_not_listed"}
    out = _purge(conn, vault, document_ids=["doc_reviewed", "doc_unknown"])
    assert [r["document_id"] for r in out] == ["doc_reviewed"] and out.failures == []
    assert not vault.exists(reviewed) and vault.exists(other)
    assert [d["id"] for d in records.due_for_deletion(conn, TODAY)] == ["doc_not_listed"]
    assert _purge(conn, vault, document_ids=[]) == [] and vault.exists(other)
