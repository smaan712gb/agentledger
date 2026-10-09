"""Second adversarial review of the retention fix (re-audit of 952ee96), carryovers: only return inputs named
"...carryover" or "...carryforward" were recorded as carryovers, so a 2026 return deducting a 9,000 suspended passive
activity loss (IRC 469(b), Form 8582) or a 25,000 net operating loss (IRC 172, Schedule 1 line 8a) let the 2020 record
behind the loss go from 2028-07-15 while the 2026 return was assessable to 2030-04-15 (Treas. Reg. 1.6001-1(e)).
Asserted now: the return records both as carryovers (return_retention_facts, kind 'carryover'), and the 2020 record is
kept as a record of 2026 too: 7 years from 2027-04-15 plus the margin.
"""

from __future__ import annotations

from datetime import date, timedelta

from agentledger import db
from agentledger.evidence import records
from agentledger.returns.store import Returns
from test_reaudit_952ee96_premature_retention import GRACE, _event, confirm, deletable_from, due, end_of, on, paper_file
from test_reaudit_952ee96_review_retention_later_year_support import put_doc
from test_return_workflow import fam, household  # noqa: F401  (fixture)

DAY = timedelta(days=1)


def _loss_year_record(f, doc_id: str, note: str) -> None:
    """A 2020 record behind the loss, confirmed 2020; the 2020 return filed 2021-04-01 (recorded)."""
    put_doc(f.conn, f.vault, doc_id, "rivera", doc_type="Invoice", tax_year=2020, received=date(2021, 2, 10))
    confirm(f.conn, doc_id, 2020)
    _event(f.conn, "rivera", 2020, "filed", "2021-04-01", "1040", note=note)
    assert end_of(f.conn, doc_id) == (date(2028, 4, 15) + GRACE, "ok")       # before a later return uses the loss


def _file_2026(f, extra: dict) -> str:
    """The 2026 return, paper-filed in AgentLedger on 2027-04-01 (assessable until 2030-04-15)."""
    R = Returns(f.conn, f.kb, segregation=False)
    with on(date(2027, 4, 1)):
        rid = R.create("rivera", 2026, "maya", {**household(), **extra})
        R.populate_from_documents(rid, "maya")
        assert paper_file(R, rid).status == "paper_filed"
    return rid


def _kept_as_a_2026_record(f, doc_id: str, rid: str, carried: str) -> None:
    details = {r["detail"] for r in db.rows(f.conn, "SELECT detail FROM return_retention_facts WHERE return_id = ? AND "
                                                    "kind = 'carryover'", rid)}
    assert details == {carried}
    assert {"client_id": "rivera", "tax_year": 2026, "why": ["a carryover is used in 2026"]} in records.explain(f.conn, doc_id)["supports"]
    assert doc_id not in due(f.conn, date(2030, 4, 15))
    assert end_of(f.conn, doc_id) == (date(2034, 4, 15) + GRACE, "ok")
    deletable_from(f.conn, doc_id, date(2034, 4, 15) + GRACE + DAY)


def test_suspended_passive_loss_keeps_the_loss_year_records(fam):  # noqa: F811
    _loss_year_record(fam, "d_rental_2020", "IRS account transcript: 2020 Form 1040 received 2021-04-01; Form 8582 "
                                             "suspended 9,000 of the 41 Elm Street rental loss")
    rental = {"address": "41 Elm Street", "rents": "18000", "expenses": {"repairs": "4000", "taxes": "3000"},
              "depreciation": "6000", "prior_year_unallowed_loss": "9000"}
    rid = _file_2026(fam, {"rentals": [rental]})
    assert Returns(fam.conn, fam.kb).latest(rid)["result"]["forms"]["sch_e"]["26"] == "-4000"   # less the suspended 9,000
    _kept_as_a_2026_record(fam, "d_rental_2020", rid, "rentals[0].prior_year_unallowed_loss")


def test_net_operating_loss_keeps_the_loss_year_records(fam):  # noqa: F811
    _loss_year_record(fam, "d_schc_2020", "IRS account transcript: 2020 Form 1040 received 2021-04-01; Schedule C loss, "
                                          "NOL carried forward")
    rid = _file_2026(fam, {"other_income": {"8a": "25000"}})
    assert Returns(fam.conn, fam.kb).latest(rid)["result"]["forms"]["sch_1"]["8a"] == "-25000"  # the NOL deduction
    _kept_as_a_2026_record(fam, "d_schc_2020", rid, "other_income.8a")
