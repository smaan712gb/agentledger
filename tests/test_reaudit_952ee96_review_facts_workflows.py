"""Review of the 952ee96 re-audit fixes: the legitimate workflows still work end to end after the gates began re-running
population, refusing unknown inputs and hand-made identities, and requiring implied amounts and box 7 codes. A return
populated from documents goes through review, approval, signature and paper filing; an amendment of it starts from the
filed facts without duplicating any document; a return entered by hand, its documents accounted for, goes through
review and approval.
"""

from __future__ import annotations

import json

import pytest
from test_return_workflow import fam, household  # noqa: F401  (fam is a fixture)

from agentledger import coverage
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

NEC_NOTE = "issued in error; the payer is sending a corrected 1099"
FILED_NOTE = "mailed by certified mail on 2027-04-10, receipt 7019 0000 0000"


def _paper_filed(R):
    rid = R.create("rivera", 2026, "maya", household())
    out = R.populate_from_documents(rid, "maya")
    assert set(out["documents"]) == {"d_w2a", "d_w2b", "d_int"} and out["conflicts"] == 0
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    approved = R.approve(rid, "lee", "cpa")
    assert R.request_signature(rid, "lee", "cpa").status == "awaiting_signature"
    R.record_signature(rid, "taxpayer", method="wet_signature", return_hash=approved.facts["approved_hash"])
    with pytest.raises(TransitionError, match="record how and when it was filed"):
        R.mark_paper_filed(rid, "lee", "cpa", "mailed")
    with pytest.raises(TransitionError, match="requires one of"):
        R.mark_paper_filed(rid, "maya", "staff", FILED_NOTE)
    assert R.mark_paper_filed(rid, "lee", "cpa", FILED_NOTE).status == "paper_filed"
    return rid


def test_a_populated_return_goes_from_population_to_paper_filing(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = _paper_filed(R)
    v = R.latest(rid)
    assert v["result"]["forms"]["f1040"]["1a"] == "93000" and v["result"]["forms"]["f1040"]["25a"] == "7000"
    with pytest.raises(TransitionError, match="frozen"):
        R.populate_from_documents(rid, "maya")


def test_an_amendment_starts_from_the_filed_facts_without_duplicates(fam, monkeypatch):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = _paper_filed(R)
    # Form 1040-X is "unsupported" in today's coverage registry; simulate the registry in which it is supported.
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})
    amended = R.start_amendment(rid, "lee")
    out = R.populate_from_documents(amended, "maya")
    v = R.latest(amended)
    assert out["conflicts"] == 0 and out["fields"] == 0
    assert [w["source_document"] for w in v["inputs"]["w2s"]] == ["d_w2a", "d_w2b"]
    assert [i["source_document"] for i in v["inputs"]["interest"]] == ["d_int"]
    edited = json.loads(json.dumps(v["inputs"]))
    next(w for w in edited["w2s"] if w["source_document"] == "d_w2b")["wages"] = "43000"      # the reason for amending
    R.save_inputs(amended, edited, "maya")
    R.populate_from_documents(amended, "maya")
    [c] = R.conflicts(amended)
    assert (c["anchor"], c["proposed_value"]) == ("w2s[d_w2b].wages", "41000")
    R.resolve_conflict(amended, c["id"], "keep", "lee", note="W-2c from City Schools shows 43,000 in box 1")
    for doc in R.unaccounted_documents(amended):
        R.account_for_document(amended, doc, "not_applicable", NEC_NOTE, "maya")
    R.confirm(amended, None, "maya")
    assert R.submit_for_review(amended, "maya").status == "in_review"
    assert R.approve(amended, "lee", "cpa").status == "approved"
    assert R.latest(amended)["result"]["forms"]["f1040"]["1a"] == "95000"


def test_a_return_entered_by_hand_goes_through_review(fam):  # noqa: F811
    hand = {**household(),
            "w2s": [{"owner": "taxpayer", "employer_name": "Lakeside Market", "wages": "52000", "federal_withholding": "4100",
                     "ss_wages": "52000", "ss_tax": "3224", "medicare_wages": "52000", "medicare_tax": "754",
                     "box12": {"D": "3000"}},
                    {"owner": "spouse", "employer_name": "City Schools", "wages": "41000", "federal_withholding": "2900",
                     "ss_wages": "41000", "medicare_wages": "41000"}],
            "interest": [{"owner": "taxpayer", "payer": "First Bank", "interest": "312.40"}],
            "retirement": [{"owner": "spouse", "payer": "Teachers Pension", "gross_distribution": "1000",
                            "taxable_amount": "1000", "distribution_code": "7"}]}
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", hand)
    R.compute(rid, "maya")
    with pytest.raises(TransitionError, match="not on the return"):
        R.submit_for_review(rid, "maya")
    for doc in ("d_w2a", "d_w2b", "d_int"):
        R.account_for_document(rid, doc, "entered_by_hand", "typed from the client's paper copies at the interview", "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    R.populate_from_documents(rid, "maya")                # nothing to add: every document is accounted for
    assert len(R.latest(rid)["inputs"]["w2s"]) == 2 and R.conflicts(rid) == []
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"
    assert R.latest(rid)["result"]["forms"]["f1040"]["1a"] == "93000"
