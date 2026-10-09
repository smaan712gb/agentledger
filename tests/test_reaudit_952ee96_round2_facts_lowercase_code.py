"""Second adversarial review of the 952ee96 re-audit fixes, 1099-R box 7: the misreading that makes box 7 unreadable
("l" for "1") answered the missing-code question when typed back, because the code check upper-cased it, and the early
distribution was approved without the 10% additional tax. Asserted now: box 7 codes are valid only in capitals
(facts.valid_code), so the typed-back "l" and a hand-entered lower-case code leave the code missing and refuse review,
and the return goes through only with the code the paper copy shows, taxed with the additional 10%.
"""

from __future__ import annotations

import json

import pytest
from test_return_workflow import add_doc

from agentledger.ledger import store
from agentledger.returns import facts
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

JORDAN = {"tax_year": 2026, "filing_status": "single",
          "taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0009", "dob": "1990-01-01"}}
R401K = {"payer_name": "Old Employer 401(k) Plan", "recipient_tin_last4": "0009", "box1": "20,000.00", "box2a": "20,000.00",
         "box4": "4,000.00", "box7": "l"}
MISSING = "1 item(s) lack a required amount"


@pytest.fixture
def solo(foundry):
    store.add_client(foundry.conn, id="jordan", name="Jordan Lee", kind="individual", emails=[], tax_id_last4="0009",
                     domain="general", facts={"taxpayer_ssn_last4": "0009", "taxpayer_name": "Jordan Lee"})
    return foundry


def _refused_for(R, rid, reason):
    with pytest.raises(TransitionError) as e:
        R.submit_for_review(rid, "maya")
    assert reason in str(e.value), f"review was refused, but not for the reason under test: {e.value}"
    assert R.status(rid).status == "preparing"


def _code(R, rid, code):
    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    edited["retirement"][0]["distribution_code"] = code
    R.save_inputs(rid, edited, "maya")


def test_box7_codes_are_valid_only_in_capitals():
    for code in ("1", "7", "L", "G", "7D"):
        assert facts.valid_code(code) and facts.satisfied("retirement", "distribution_code", code)
    for code in ("l", "o", "g", "7d", "", "0", "X"):
        assert not facts.valid_code(code) and not facts.satisfied("retirement", "distribution_code", code)


def test_a_typed_back_lower_case_box7_does_not_answer_the_missing_code(solo):
    add_doc(solo.conn, "d_r", "jordan", "1099-R", R401K)
    R = Returns(solo.conn, solo.kb)
    rid = R.create("jordan", 2026, "maya", JORDAN)
    out = R.populate_from_documents(rid, "maya")
    [note] = [i["message"] for i in out["issues"] if i["code"] == "unreadable"]
    assert "box7 = 'l' could not be read" in note
    anchor = "missing:retirement[d_r].distribution_code"
    assert [c["anchor"] for c in R.conflicts(rid)] == [anchor]

    _code(R, rid, "l")                                                 # typed back as the note shows it
    [c] = R.conflicts(rid)
    with pytest.raises(ValueError, match="the amount is still missing"):
        R.resolve_conflict(rid, c["id"], "keep", "maya", note="entered from the populate note")
    R.confirm(rid, None, "maya")
    _refused_for(R, rid, MISSING)

    _code(R, rid, "1")                                                 # what the paper copy shows
    R.resolve_conflict(rid, c["id"], "keep", "maya", note="box 7 reads 1 (early distribution) on the paper copy")
    R.populate_from_documents(rid, "maya")
    assert R.conflicts(rid) == []
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"
    assert R.latest(rid)["result"]["summary"]["total_tax"] == "2393"   # with the 10% additional tax of 2,000


def test_a_hand_entered_lower_case_code_refuses_review(solo):
    hand = {"payer": "Old Employer 401(k) Plan", "gross_distribution": "20000", "taxable_amount": "20000",
            "federal_withholding": "4000", "distribution_code": "l"}
    R = Returns(solo.conn, solo.kb)
    rid = R.create("jordan", 2026, "maya", {**JORDAN, "retirement": [hand]})
    R.compute(rid, "maya")
    v = R.latest(rid)
    assert [m["anchor"] for m in facts.missing_required(v["inputs"], v["provenance"])] == ["missing:retirement[#0].distribution_code"]
    R.confirm(rid, None, "maya")
    _refused_for(R, rid, MISSING)
    _code(R, rid, "1")
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.latest(rid)["result"]["summary"]["total_tax"] == "2393"
