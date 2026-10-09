"""Review of the fix for finding 5 (re-audit of 952ee96), "not required" versus a return the firm is preparing. The
first adversarial review found that once a CPA recorded that no 2026 return was required, the delinquent 2026 return
later prepared from the W-2 (after an IRS non-filer notice, so no return filed: 6501(c)(3)) did not stop the W-2 being
deleted while it was in preparation (due from 2034-07-31). Asserted now: the "not required" record counts only while
AgentLedger holds no return for the year; a return in preparation that relied on the W-2 keeps it, and its filing
starts the count.
"""

from __future__ import annotations

from datetime import date, timedelta

from agentledger import db
from agentledger.returns.store import Returns
from test_reaudit_952ee96_premature_retention import (GRACE, W2_2026, _event, confirm, deletable_from, due, end_of, on,
                                                      paper_file, receive, w2_2026_router)
from test_return_workflow import fam, household  # noqa: F401  (fixture)

DAY = timedelta(days=1)


def test_a_delinquent_return_in_preparation_keeps_its_documents(fam):  # noqa: F811
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1), router=w2_2026_router())
    confirm(fam.conn, w2["id"], 2026)
    _event(fam.conn, "rivera", 2026, "not_required", "2027-05-01", "1040",
           note="client reported 2026 gross income below the filing threshold; no return required")
    assert end_of(fam.conn, w2["id"]) == (date(2034, 5, 1) + GRACE, "ok")     # the CPA's determination, before the notice
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2034, 8, 1)):                                    # IRS non-filer notice: the delinquent return is prepared
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
    assert R.status(rid).status == "preparing"
    assert db.one(fam.conn, "SELECT 1 AS x FROM return_document_uses WHERE return_id = ? AND document_id = ?", rid, w2["id"])
    end, why = end_of(fam.conn, w2["id"])
    assert end is None and f"return {rid} (2026), which relied on it, is not filed" in why
    assert w2["id"] not in due(fam.conn, date(2034, 8, 10))
    assert w2["id"] not in due(fam.conn, date(2060, 1, 1))
    with on(date(2034, 9, 1)):
        assert paper_file(R, rid).status == "paper_filed"
    assert end_of(fam.conn, w2["id"]) == (date(2041, 9, 1) + GRACE, "ok")     # 7 years from the delinquent filing
    deletable_from(fam.conn, w2["id"], date(2041, 9, 1) + GRACE + DAY)


def test_a_return_held_for_the_year_voids_not_required(fam):  # noqa: F811
    """A return created for the year voids the 'not required' record even for a document it never relied on: until a
    filing is on record, no period runs."""
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1), router=w2_2026_router())
    confirm(fam.conn, w2["id"], 2026)
    _event(fam.conn, "rivera", 2026, "not_required", "2027-05-01", "1040",
           note="client reported 2026 gross income below the filing threshold; no return required")
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2034, 8, 1)):
        rid = R.create("rivera", 2026, "maya", household())       # created, not populated: relies on nothing yet
    assert not db.one(fam.conn, "SELECT 1 AS x FROM return_document_uses WHERE return_id = ? AND document_id = ?", rid, w2["id"])
    end, why = end_of(fam.conn, w2["id"])
    assert end is None and "no 2026 income tax return of rivera" in why and "6501(c)(3)" in why
    assert w2["id"] not in due(fam.conn, date(2060, 1, 1))
