"""Second adversarial review of the 952ee96 re-audit fixes, Forms 1098 and 1098-E: an unreadable 1098 box 5 (mortgage
insurance premiums) was dropped without a word and taken as zero on an approved return. Asserted now: a 1098 box 1 or
box 5, or a 1098-E box 1, that could not be read or that intake could not verify (`_unverified`) is a missing amount
(`missing:<group>.<field>`) that refuses review for that reason and cannot be kept until a person enters it; once
entered (the document accounted for as entered by hand when it is not on the return) the return goes through with it.
"""

from __future__ import annotations

import json

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)

from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

NEC_NOTE = "issued in error; the payer is sending a corrected 1099"
MISSING = "1 item(s) lack a required amount"
PAPER = "entered from the paper copy; the scanned box could not be read"

# (document type, extracted fields, the missing amount, whether the document is on the return through another box)
BOX5 = [("1098", {"box1": "36,400.00", "box5": "1,2OO.OO"}, "itemized", "mortgage_insurance_premiums", True),
        ("1098", {"box1": "36,400.00", "_unverified": {"box5": "1,200.00"}}, "itemized", "mortgage_insurance_premiums", True)]
BOX1 = [("1098", {"box1": "36,4OO.OO", "box5": "1,200.00"}, "itemized", "mortgage_interest_1098", False),
        ("1098", {"_unverified": {"box1": "36,400.00"}}, "itemized", "mortgage_interest_1098", False),
        ("1098-E", {"box1": "2,5OO.OO"}, "adjustments", "student_loan_interest_paid", False),
        ("1098-E", {"_unverified": {"box1": "2,500.00"}}, "adjustments", "student_loan_interest_paid", False)]
IDS = ["1098-box5-unreadable", "1098-box5-unverified", "1098-box1-unreadable", "1098-box1-unverified",
       "1098E-box1-unreadable", "1098E-box1-unverified"]


def _populated(fam, doc_type, fields):  # noqa: F811
    add_doc(fam.conn, "d_x", "rivera", doc_type, fields)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    out = R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    R.confirm(rid, None, "maya")
    return R, rid, out


def _refused_for(R, rid, reason):
    with pytest.raises(TransitionError) as e:
        R.submit_for_review(rid, "maya")
    assert reason in str(e.value), f"review was refused, but not for the reason under test: {e.value}"
    assert R.status(rid).status == "preparing"


def _anchors(R, rid):
    return sorted(c["anchor"] for c in R.conflicts(rid))


def _enter(R, rid, group, amounts):
    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    edited.setdefault(group, {}).update(amounts)
    R.save_inputs(rid, edited, "maya")


@pytest.mark.parametrize("doc_type,fields,group,field,on_return", BOX5 + BOX1, ids=IDS)
def test_an_unreadable_1098_box_refuses_review_and_is_never_zero(fam, doc_type, fields, group, field, on_return):  # noqa: F811
    R, rid, out = _populated(fam, doc_type, fields)
    anchor = f"missing:{group}.{field}"
    assert [i for i in out["issues"] if i.get("anchor") == anchor and i["document_id"] == "d_x"]
    assert field not in (R.latest(rid)["inputs"].get(group) or {})          # never taken as zero
    assert ("d_x" in out["documents"]) is on_return
    assert R.unaccounted_documents(rid) == ([] if on_return else ["d_x"])
    assert _anchors(R, rid) == [anchor]
    _refused_for(R, rid, MISSING)

    [c] = R.conflicts(rid)
    with pytest.raises(ValueError, match="the amount is still missing"):     # nothing entered: nothing to keep
        R.resolve_conflict(rid, c["id"], "keep", "maya", note=PAPER)
    with pytest.raises(ValueError, match="the document has no value here"):
        R.resolve_conflict(rid, c["id"], "document", "maya", note=PAPER)
    R.populate_from_documents(rid, "maya")                                 # asked again, every time
    assert _anchors(R, rid) == [anchor]
    _refused_for(R, rid, MISSING)


ENTERED = {"mortgage_interest_1098": "36400.00", "mortgage_insurance_premiums": "1200.00",
           "student_loan_interest_paid": "2500.00"}
LINE = {"mortgage_interest_1098": ("sch_a", "8a", "36400"), "mortgage_insurance_premiums": ("sch_a", "8d", "1200"),
        "student_loan_interest_paid": ("sch_1", "21", "2500")}


@pytest.mark.parametrize("doc_type,fields,group,field,on_return", BOX1, ids=IDS[2:])
def test_a_1098_off_the_return_goes_through_once_entered_by_hand(fam, doc_type, fields, group, field, on_return):  # noqa: F811
    """The document is not on the return (its box 1 could not be read): a person enters its amounts and accounts for
    it as entered by hand; population then no longer asks."""
    R, rid, _ = _populated(fam, doc_type, fields)
    amounts = {k: ENTERED[k] for k in (("mortgage_interest_1098", "mortgage_insurance_premiums") if doc_type == "1098"
                                       else ("student_loan_interest_paid",))}
    _enter(R, rid, group, amounts)
    _refused_for(R, rid, "filed document(s) for the year are not on the return (d_x)")
    R.account_for_document(rid, "d_x", "entered_by_hand", PAPER, "maya")
    R.populate_from_documents(rid, "maya")
    assert R.conflicts(rid) == []
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"
    v = R.latest(rid)
    assert {k: v["inputs"][group][k] for k in amounts} == amounts
    for k in amounts:
        form, line, value = LINE[k]
        assert v["result"]["forms"][form][line] == value


@pytest.mark.parametrize("doc_type,fields,group,field,on_return", BOX5, ids=IDS[:2])
def test_a_1098_box5_entered_by_hand_answers_the_missing_amount(fam, doc_type, fields, group, field, on_return):  # noqa: F811
    """The 1098 is on the return through box 1, so it cannot be accounted for: the preparer enters box 5 from the paper
    copy, keeps it, and the return goes through with the premiums deducted (Schedule A line 8d)."""
    R, rid, _ = _populated(fam, doc_type, fields)
    _enter(R, rid, group, {field: "1200.00"})
    [c] = R.conflicts(rid)
    R.resolve_conflict(rid, c["id"], "keep", "maya", note=PAPER)
    R.populate_from_documents(rid, "maya")
    v = R.latest(rid)
    assert R.conflicts(rid) == [], (
        f"{group}.{field} = {v['inputs'][group].get(field)} was entered by maya and kept, yet re-population asks for it "
        f"again: {[c['anchor'] for c in R.conflicts(rid)]}; its provenance is {v['provenance'].get(f'{group}.{field}')}")
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"
    assert R.latest(rid)["result"]["forms"]["sch_a"]["8d"] == "1200"
