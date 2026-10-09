"""Second adversarial review of the retention fix (re-audit of 952ee96), foreign tax detection: only the exact keys
"box6" (1099-INT) and "box7" (1099-DIV) of documents currently dated to the year were read, so a 2026 return claiming an
85.00 foreign tax credit from a box spelled "box7_foreign_tax_paid" or "Box 7" let the form go from 2034-07-15, inside
the 10-year claim window of IRC 6511(d)(3)(A) (to 2037-04-15), and re-dating the 1099-DIV to 2025 took the window away
from it and from the year's other records. Asserted now: the box is read under any spelling documents.canonical maps
(unverified fields included), the year's returns record their foreign tax, and the form and the 2026 W-2 are kept to
2037-04-15 plus the margin, re-dated or not.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agentledger import db
from agentledger.evidence import records
from agentledger.returns.store import Returns
from test_reaudit_952ee96_premature_retention import GRACE, _event, confirm, deletable_from, due, end_of, on, paper_file
from test_reaudit_952ee96_review_retention_later_year_support import put_doc
from test_return_workflow import fam, household  # noqa: F401  (fixture)

DAY = timedelta(days=1)
WINDOW_ENDS = date(2037, 4, 15)                 # 10 years from the due date of the 2026 return
SEVEN_YEARS = date(2034, 4, 15)                 # 7 years from it: the count of a year without foreign tax
DIV = {"payer_name": "Harbor International Index Fund", "recipient_tin_last4": "0001", "box1a": "2400.00", "box1b": "2000.00"}
INT = {"payer_name": "Overseas Savings Bank", "recipient_tin_last4": "0001", "box1": "1200.00"}


def _file_2026(f, doc_type: str, fields: dict) -> str:
    """The 2026 return populated from the year's documents (the 1099 among them), paper-filed 2027-04-01 with the 85.00
    foreign tax credit (Schedule 3 line 1); the 1099 confirmed 2026."""
    put_doc(f.conn, f.vault, "d_foreign", "rivera", doc_type=doc_type, tax_year=2026, received=date(2027, 2, 10), fields=fields)
    R = Returns(f.conn, f.kb, segregation=False)
    with on(date(2027, 4, 1)):
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
        assert paper_file(R, rid).status == "paper_filed"
    assert R.latest(rid)["result"]["forms"]["sch_3"]["1"] == "85"
    confirm(f.conn, "d_foreign", 2026)
    return rid


SPELLINGS = [("1099-DIV", "box7_foreign_tax_paid", DIV, "dividends[0]"), ("1099-DIV", "Box 7", DIV, "dividends[0]"),
             ("1099-INT", "box6_foreign_tax_paid", INT, "interest[1]")]       # interest[0]: the household's 1099-INT


@pytest.mark.parametrize("doc_type, key, fields, item", SPELLINGS, ids=[f"{s[0]}:{s[1]}" for s in SPELLINGS])
def test_the_credit_claimed_from_a_box_population_accepts_keeps_the_form(fam, doc_type, key, fields, item):  # noqa: F811
    """The review's spelling cases: the filed return records its foreign tax, and the 1099 is kept 10 years from the
    due date plus the margin."""
    rid = _file_2026(fam, doc_type, {**fields, key: "85.00"})
    facts = [r["detail"] for r in db.rows(fam.conn, "SELECT detail FROM return_retention_facts WHERE return_id = ? AND "
                                                    "kind = 'foreign_tax' ORDER BY version", rid)]
    assert facts[-1] == f"{item},schedule 3 line 1"                         # the item's foreign tax and the credit
    assert end_of(fam.conn, "d_foreign") == (WINDOW_ENDS + GRACE, "ok")
    assert "d_foreign" not in due(fam.conn, WINDOW_ENDS)
    deletable_from(fam.conn, "d_foreign", WINDOW_ENDS + GRACE + DAY)


FROM_THE_DOCUMENT = [
    # (id, form, fields, the year's count runs to)
    ("div-box7", "1099-DIV", {**DIV, "box7": "85.00"}, WINDOW_ENDS),
    ("div-Box 7", "1099-DIV", {**DIV, "Box 7": "85.00"}, WINDOW_ENDS),
    ("div-box_7_foreign_tax_paid", "1099-DIV", {**DIV, "box_7_foreign_tax_paid": "$85.00"}, WINDOW_ENDS),
    ("div-unverified", "1099-DIV", {**DIV, "_unverified": {"box7": "85.00"}}, WINDOW_ENDS),   # not grounded at intake
    ("div-unreadable", "1099-DIV", {**DIV, "box7": "see statement"}, WINDOW_ENDS),           # it may be foreign tax
    ("int-Box 6", "1099-INT", {**INT, "Box 6": "1,085.00"}, WINDOW_ENDS),
    ("div-box7-zero", "1099-DIV", {**DIV, "box7": "0.00"}, SEVEN_YEARS),
    ("div-box6", "1099-DIV", {**DIV, "box6": "85.00"}, SEVEN_YEARS),                         # investment expenses
]


@pytest.mark.parametrize("doc_type, fields, runs_to", [c[1:] for c in FROM_THE_DOCUMENT], ids=[c[0] for c in FROM_THE_DOCUMENT])
def test_foreign_tax_is_read_from_the_document_under_any_spelling(fam, doc_type, fields, runs_to):  # noqa: F811
    """The 2026 return filed with other software (recorded, 2027-04-01): the 1099's own box decides, for it and for the
    year's other records (the 2026 W-2)."""
    put_doc(fam.conn, fam.vault, "d_foreign", "rivera", doc_type=doc_type, tax_year=2026, received=date(2027, 2, 10), fields=fields)
    confirm(fam.conn, "d_foreign", 2026)
    confirm(fam.conn, "d_w2a", 2026)
    _event(fam.conn, "rivera", 2026, "filed", "2027-04-01", "1040", note="IRS account transcript: 2026 Form 1040 received 2027-04-01")
    assert end_of(fam.conn, "d_foreign") == (runs_to + GRACE, "ok")
    assert end_of(fam.conn, "d_w2a") == (runs_to + GRACE, "ok")


def _redated(f) -> str:
    """The review's re-dating: after the filed 2026 return relied on it, the 1099-DIV is confirmed as a 2025 document
    and the 2025 return (filed 2026-04-10) is recorded."""
    rid = _file_2026(f, "1099-DIV", {**DIV, "box7": "85.00"})
    assert end_of(f.conn, "d_foreign") == (WINDOW_ENDS + GRACE, "ok")
    confirm(f.conn, "d_foreign", 2025)
    _event(f.conn, "rivera", 2025, "filed", "2026-04-10", "1040", note="IRS account transcript: 2025 Form 1040 received 2026-04-10")
    return rid


def test_the_redated_1099_div_keeps_the_window_of_the_return_that_claimed_the_credit(fam):  # noqa: F811
    rid = _redated(fam)
    assert records.explain(fam.conn, "d_foreign")["supports"] == [
        {"client_id": "rivera", "tax_year": 2025, "why": ["its confirmed tax year"]},
        {"client_id": "rivera", "tax_year": 2026, "why": [f"return {rid} relied on it"]}]
    assert end_of(fam.conn, "d_foreign") == (WINDOW_ENDS + GRACE, "ok")
    deletable_from(fam.conn, "d_foreign", WINDOW_ENDS + GRACE + DAY)


def test_the_years_other_records_keep_the_window_after_the_redating(fam):  # noqa: F811
    _redated(fam)
    confirm(fam.conn, "d_w2a", 2026)                                         # the household's 2026 W-2, on the return
    assert end_of(fam.conn, "d_w2a") == (WINDOW_ENDS + GRACE, "ok")
    deletable_from(fam.conn, "d_w2a", WINDOW_ENDS + GRACE + DAY)
