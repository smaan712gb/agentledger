"""Review of the fix for finding 5 (re-audit of 952ee96), a document moved to another client. The first adversarial
review found that after pipeline.assign moved a W-2 that the household's late-filed 2026 return relied on, retention
counted from the new client's on-time filing (deletable from 2034-07-15, inside 6501(a) for the household's return,
open to 2035-06-01), and that a legal hold on the household no longer covered it. Asserted now: a move of a document
a filed return relied on is refused, so the household's filing still governs and its hold covers the W-2; a move still
allowed (the return is not filed) keeps the former client's return and holds in force.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agentledger import db
from agentledger.evidence import records
from agentledger.intake import pipeline
from agentledger.ledger import store
from agentledger.returns.store import Returns
from test_reaudit_952ee96_premature_retention import (GRACE, W2_2026, _event, confirm, deletable_from, due, end_of, on,
                                                      paper_file, receive, w2_2026_router)
from test_return_workflow import fam, household  # noqa: F401  (fixture)

DAY = timedelta(days=1)
MOVE = "the W-2 belongs to Alex Rivera Sr., a separate client (SSN mismatch found in review)"


def _sr(fam) -> None:  # noqa: F811
    store.add_client(fam.conn, id="alex-rivera-sr", name="Alex Rivera (Sr.)", kind="individual", emails=[], tax_id_last4="0009",
                     domain="general", facts={"taxpayer_ssn_last4": "0009", "taxpayer_name": "Alex Rivera"})
    _event(fam.conn, "alex-rivera-sr", 2026, "filed", "2027-04-01", "1040",
           note="IRS account transcript: 2026 Form 1040 received 2027-04-01")


def _filed_then_move_refused(fam) -> tuple[dict, str]:  # noqa: F811
    """The household's 2026 return, filed late (2032-06-01), relied on the W-2; in 2033 the firm tries to move it."""
    _sr(fam)
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1), router=w2_2026_router())
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2032, 6, 1)):
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
        assert paper_file(R, rid).status == "paper_filed"
    assert db.one(fam.conn, "SELECT 1 AS x FROM return_document_uses WHERE return_id = ? AND document_id = ?", rid, w2["id"])
    with on(date(2033, 1, 10)), pytest.raises(ValueError, match=f"filed return {rid} relied on this document"):
        pipeline.assign(fam.conn, fam.vault, w2["id"], "alex-rivera-sr", "lee", move_reason=MOVE)
    assert db.one(fam.conn, "SELECT client_id FROM documents WHERE id = ?", w2["id"])["client_id"] == "rivera"
    assert not db.one(fam.conn, "SELECT 1 AS x FROM document_moves WHERE document_id = ?", w2["id"])
    confirm(fam.conn, w2["id"], 2026)
    return w2, rid


def test_a_hold_on_the_client_under_examination_covers_the_evidence(fam):  # noqa: F811
    """On 2034-09-01 the IRS opens an examination of the household's 2026 return and a CPA places a hold on the
    household: no retention run deletes the W-2, on 2034-09-02 or after its retention has ended (2040-01-01)."""
    w2, rid = _filed_then_move_refused(fam)
    records.place_hold(fam.conn, client_id="rivera", reason="IRS examination of the 2026 Form 1040 opened 2034-09-01",
                       actor="lee", role="cpa")
    for day in (date(2034, 9, 2), date(2040, 1, 1)):
        receipts = records.purge_expired(fam.conn, fam.vault, day, actor="lee", role="cpa", attested=True,
                                         reason="annual retention review")
        assert w2["id"] not in [r["document_id"] for r in receipts] and fam.vault.exists(w2["vault_path"]), day


def test_the_previous_clients_filed_return_still_counts(fam):  # noqa: F811
    """The household's late filing governs: kept 7 years from 2032-06-01 plus the margin, never from the other
    client's 2027 filing."""
    w2, rid = _filed_then_move_refused(fam)
    assert w2["id"] not in due(fam.conn, date(2035, 6, 1) - DAY)
    assert end_of(fam.conn, w2["id"]) == (date(2039, 6, 1) + GRACE, "ok")
    deletable_from(fam.conn, w2["id"], date(2039, 6, 1) + GRACE + DAY)


def test_a_move_still_allowed_keeps_the_former_clients_return_and_holds(fam):  # noqa: F811
    """The household's 2026 return is still in preparation when the W-2 is moved (allowed, with a reason on record).
    The return that relied on it keeps it while unfiled, whatever the new client's filings, and a hold on the household
    still covers it."""
    _sr(fam)
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1), router=w2_2026_router())
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2027, 3, 1)):
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
    assert R.status(rid).status == "preparing"
    with on(date(2027, 3, 10)):
        pipeline.assign(fam.conn, fam.vault, w2["id"], "alex-rivera-sr", "lee", move_reason=MOVE)
    assert db.one(fam.conn, "SELECT client_id FROM documents WHERE id = ?", w2["id"])["client_id"] == "alex-rivera-sr"
    assert records.former_clients(fam.conn, w2["id"]) == ["rivera"]
    assert end_of(fam.conn, w2["id"]) == (None, "a person has not confirmed its tax year and retention class")
    confirm(fam.conn, w2["id"], 2026)
    end, why = end_of(fam.conn, w2["id"])
    assert end is None and f"return {rid} (2026), which relied on it, is not filed" in why
    assert w2["id"] not in due(fam.conn, date(2045, 1, 1))
    hold = records.place_hold(fam.conn, client_id="rivera", reason="IRS examination of the household's 2026 year",
                              actor="lee", role="cpa")
    assert records.hold_on(fam.conn, w2["id"], "alex-rivera-sr")["id"] == hold
    receipts = records.purge_expired(fam.conn, fam.vault, date(2045, 1, 1), actor="lee", role="cpa", attested=True,
                                     reason="annual retention review")
    assert w2["id"] not in [r["document_id"] for r in receipts] and fam.vault.exists(w2["vault_path"])
