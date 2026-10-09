"""Review of the 952ee96 re-audit fixes, missing amounts. The first adversarial review found that only box 1 of each form
was required: a 1099-R without a readable box 7 was computed with the model's default code 7 (no 10% additional tax),
and an unreadable box other than the required one (1099-INT box 3, 1099-DIV box 2a, W-2 box 10), or a W-2 box 5 left
empty while box 6 shows Medicare tax withheld, was dropped with a note nothing stored and taken as zero on an approved
return. Asserted now: each one is reported as missing, review is refused for that reason, a person can enter the
value from the document and keep it, and the return then goes through with that value on it.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from test_return_workflow import add_doc

from agentledger.ledger import store
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

JORDAN = {"tax_year": 2026, "filing_status": "single",
          "taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0009", "dob": "1990-01-01"}}
MISSING = "lack a required amount"
W2 = {"employer_name": "Night Shift Co", "recipient_tin_last4": "0009", "box1": "60,000.00", "box2": "6,000.00",
      "box3": "60,000.00", "box4": "3,720.00", "box5": "60,000.00", "box6": "870.00"}


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


def _anchors(R, rid):
    return sorted(c["anchor"] for c in R.conflicts(rid))


def _enter(R, rid, lst, doc, field, value):
    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    next(i for i in edited[lst] if i.get("source_document") == doc)[field] = value
    R.save_inputs(rid, edited, "maya")


def _keep(R, rid, anchor, note="entered from the paper copy of the form"):
    [c] = [c for c in R.conflicts(rid) if c["anchor"] == anchor]
    R.resolve_conflict(rid, c["id"], "keep", "maya", note=note)


def _goes_through(R, rid):
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"


# --------------------------------------------------------------------------- 1099-R box 7 (a required code)
@pytest.mark.parametrize("box7", [None, "l"], ids=["box7-not-extracted", "box7-misread-l-for-1"])
def test_a_1099r_without_its_distribution_code_does_not_pass_review(solo, box7):
    fields = {"payer_name": "Old Employer 401(k) Plan", "recipient_tin_last4": "0009", "box1": "20,000.00",
              "box2a": "20,000.00", "box4": "4,000.00"}
    if box7 is not None:
        fields["box7"] = box7
    add_doc(solo.conn, "d_r", "jordan", "1099-R", fields)
    R = Returns(solo.conn, solo.kb)
    rid = R.create("jordan", 2026, "maya", JORDAN)
    out = R.populate_from_documents(rid, "maya")
    [item] = R.latest(rid)["inputs"]["retirement"]
    assert "distribution_code" not in item                  # never the model's default "7", never "l" read as "1"
    if box7:
        assert any(i["code"] == "unreadable" and i["document_id"] == "d_r" for i in out["issues"])
    anchor = "missing:retirement[d_r].distribution_code"
    assert _anchors(R, rid) == [anchor]
    R.confirm(rid, None, "maya")
    _refused_for(R, rid, MISSING)

    _enter(R, rid, "retirement", "d_r", "distribution_code", "0")          # not a box 7 code: it does not answer it
    with pytest.raises(ValueError, match="still missing"):
        _keep(R, rid, anchor)
    _refused_for(R, rid, MISSING)

    _enter(R, rid, "retirement", "d_r", "distribution_code", "1")          # what the paper copy shows
    _keep(R, rid, anchor, note="box 7 reads 1 (early distribution) on the paper copy")
    R.populate_from_documents(rid, "maya")
    assert _anchors(R, rid) == []                            # answered once, not asked again
    _goes_through(R, rid)
    assert R.latest(rid)["result"]["summary"]["total_tax"] == "2393"        # with the 10% additional tax of 2,000


# --------------------------------------------------------------------------- an unreadable box other than box 1
CASES = [
    ("1099-INT", {"payer_name": "TreasuryDirect", "recipient_tin_last4": "0009", "box1": "0.00", "box3": "5,4OO.OO"},
     "interest", "us_savings_bond_interest", "5400.00"),
    ("1099-DIV", {"payer_name": "Index Fund", "recipient_tin_last4": "0009", "box1a": "300.00", "box2a": "8,2OO.OO"},
     "dividends", "capital_gain_distributions", "8200.00"),
]


@pytest.mark.parametrize("doc_type,fields,lst,field,paper", CASES, ids=[c[0] for c in CASES])
def test_an_unreadable_income_box_does_not_pass_review_as_zero(solo, doc_type, fields, lst, field, paper):
    add_doc(solo.conn, "d_x", "jordan", doc_type, fields)
    R = Returns(solo.conn, solo.kb)
    rid = R.create("jordan", 2026, "maya", {**JORDAN, "w2s": [{"employer_name": "Day Job", "wages": "50000",
                                                                "federal_withholding": "5000"}]})
    out = R.populate_from_documents(rid, "maya")
    assert any(i["code"] == "unreadable" and i["document_id"] == "d_x" for i in out["issues"])
    [item] = R.latest(rid)["inputs"][lst]
    assert field not in item                                 # never taken as zero
    anchor = f"missing:{lst}[d_x].{field}"
    assert _anchors(R, rid) == [anchor]
    R.confirm(rid, None, "maya")
    _refused_for(R, rid, MISSING)
    R.populate_from_documents(rid, "maya")                   # the stored unreadable box is asked again, every time
    assert _anchors(R, rid) == [anchor]
    _refused_for(R, rid, MISSING)

    agi = Decimal(R.latest(rid)["result"]["summary"]["agi"])
    _enter(R, rid, lst, "d_x", field, paper)
    _keep(R, rid, anchor)
    _goes_through(R, rid)
    assert Decimal(R.latest(rid)["result"]["summary"]["agi"]) == agi + Decimal(paper)


# --------------------------------------------------------------------------- W-2 box 5 (implied by box 6) and box 10
def _prepare(solo, fields, extra=None):
    add_doc(solo.conn, "d_w2n", "jordan", "W-2", fields)
    R = Returns(solo.conn, solo.kb)
    rid = R.create("jordan", 2026, "maya", {**JORDAN, **(extra or {})})
    out = R.populate_from_documents(rid, "maya")
    R.confirm(rid, None, "maya")
    return R, rid, out


@pytest.mark.parametrize("box5", ["6O,OOO.OO", None], ids=["box5-unreadable", "box5-not-extracted"])
def test_w2_without_medicare_wages_does_not_get_box6_refunded(solo, box5):
    fields = {k: v for k, v in W2.items() if k != "box5"}
    if box5 is not None:
        fields["box5"] = box5                                # OCR read the zeros as letters
    R, rid, out = _prepare(solo, fields)
    [w2] = R.latest(rid)["inputs"]["w2s"]
    assert "medicare_wages" not in w2 and w2["medicare_tax"] == "870.00"
    anchor = "missing:w2s[d_w2n].medicare_wages"
    assert _anchors(R, rid) == [anchor]
    _refused_for(R, rid, MISSING)

    _enter(R, rid, "w2s", "d_w2n", "medicare_wages", "60000.00")
    _keep(R, rid, anchor)
    _goes_through(R, rid)
    assert Decimal(R.latest(rid)["result"]["forms"]["f1040"].get("25c", "0")) == 0     # box 6 is not refunded


def test_w2_without_box10_does_not_inflate_the_dependent_care_credit(solo):
    hand = {"dependent_care_expenses": "6000", "dependent_care_qualifying_persons": 2}
    R, rid, out = _prepare(solo, {**W2, "box10": "5,OOO.OO"}, hand)       # box 10 says 5,000.00, unreadable
    [w2] = R.latest(rid)["inputs"]["w2s"]
    assert "dependent_care_benefits" not in w2
    anchor = "missing:w2s[d_w2n].dependent_care_benefits"
    assert _anchors(R, rid) == [anchor]
    _refused_for(R, rid, MISSING)
    as_if_zero = Decimal(R.latest(rid)["result"]["forms"].get("f2441", {}).get("11", "0"))

    _enter(R, rid, "w2s", "d_w2n", "dependent_care_benefits", "5000.00")
    _keep(R, rid, anchor)
    _goes_through(R, rid)
    credit = Decimal(R.latest(rid)["result"]["forms"].get("f2441", {}).get("11", "0"))
    assert credit < as_if_zero                               # the excluded benefits cut the expense limit
