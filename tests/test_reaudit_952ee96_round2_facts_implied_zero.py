"""Second adversarial review of the 952ee96 re-audit fixes, implied amounts: a W-2 with Medicare tax withheld on
Medicare wages of 0 (left at 0 by hand, or box 5 read as 0.00) passed review, so Form 8959 refunded all of box 6.
Asserted now: an implied amount must be positive (Medicare wages under Medicare tax, social security wages under social
security tax): a 0 refuses review as missing, keeping it is refused, and once the wages are entered the return goes
through with nothing of box 6 refunded (Form 1040 line 25c is 0).
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from test_return_workflow import add_doc

from agentledger.ledger import store
from agentledger.returns import facts
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

JORDAN = {"tax_year": 2026, "filing_status": "single",
          "taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0009", "dob": "1990-01-01"}}
HAND = {"employer_name": "Night Shift Co", "wages": "60000", "federal_withholding": "6000", "ss_wages": "60000",
        "ss_tax": "3720", "medicare_wages": "60000", "medicare_tax": "870"}
W2 = {"employer_name": "Night Shift Co", "recipient_tin_last4": "0009", "box1": "60,000.00", "box2": "6,000.00",
      "box3": "60,000.00", "box4": "3,720.00", "box5": "0.00", "box6": "870.00"}
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


def _goes_through(R, rid):
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"
    return Decimal(R.latest(rid)["result"]["forms"]["f1040"].get("25c", "0"))


def test_an_implied_amount_must_be_positive():
    for field in ("medicare_wages", "ss_wages"):
        assert not facts.satisfied("w2s", field, "0") and not facts.satisfied("w2s", field, "0.00")
        assert facts.satisfied("w2s", field, "60000")
    assert facts.satisfied("w2s", "federal_withholding", "0")              # not implied: 0 is an answer there


@pytest.mark.parametrize("field,implied_by", [("medicare_wages", "medicare_tax"), ("ss_wages", "ss_tax")])
def test_a_hand_entered_w2_with_zero_wages_under_withheld_tax_is_missing_them(solo, field, implied_by):
    R = Returns(solo.conn, solo.kb)
    rid = R.create("jordan", 2026, "maya", {**JORDAN, "w2s": [{**HAND, field: "0"}]})   # a form UI defaulting to 0
    R.compute(rid, "maya")
    v = R.latest(rid)
    assert [m["anchor"] for m in facts.missing_required(v["inputs"], v["provenance"])] == [f"missing:w2s[#0].{field}"]
    R.confirm(rid, None, "maya")
    _refused_for(R, rid, MISSING)

    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    edited["w2s"][0][field] = "60000"
    R.save_inputs(rid, edited, "maya")
    assert _goes_through(R, rid) == 0                                     # box 6 is not refunded


def test_a_w2_whose_box5_was_read_as_zero_is_missing_its_medicare_wages(solo):
    add_doc(solo.conn, "d_w2z", "jordan", "W-2", W2)
    R = Returns(solo.conn, solo.kb)
    rid = R.create("jordan", 2026, "maya", JORDAN)
    R.populate_from_documents(rid, "maya")
    anchor = "missing:w2s[d_w2z].medicare_wages"
    assert [c["anchor"] for c in R.conflicts(rid)] == [anchor]
    R.confirm(rid, None, "maya")
    _refused_for(R, rid, MISSING)
    [c] = R.conflicts(rid)
    with pytest.raises(ValueError, match="the amount is still missing"):    # "0.00" does not answer it
        R.resolve_conflict(rid, c["id"], "keep", "maya", note="the W-2 shows 0.00 in box 5")
    _refused_for(R, rid, MISSING)

    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    edited["w2s"][0]["medicare_wages"] = "60000.00"                        # from the employer's corrected copy
    R.save_inputs(rid, edited, "maya")
    R.resolve_conflict(rid, c["id"], "keep", "maya", note="box 5 reads 60,000.00 on the employer's copy")
    R.confirm(rid, None, "maya")
    _refused_for(R, rid, "1 value(s) disagree with the documents and were never decided")   # the document still says 0.00
    R.populate_from_documents(rid, "maya")
    [c] = R.conflicts(rid)
    assert (c["anchor"], c["current_value"], c["proposed_value"]) == ("w2s[d_w2z].medicare_wages", "60000.00", "0.00")
    R.resolve_conflict(rid, c["id"], "keep", "lee", note="box 5 was misread as 0.00; the employer's copy shows 60,000.00")
    assert _goes_through(R, rid) == 0
