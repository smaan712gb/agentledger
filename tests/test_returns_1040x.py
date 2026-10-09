"""Form 1040-X (T1-01 S9): columns A, B and C from a filed return and its amendment, worked by hand.

Expected values come from Form 1040-X (2025) lines 1-23 and its instructions (column A as filed or as adjusted by the
IRS, column B the net change, column C the correct amount; line 16 the tax paid with the original return and after it;
line 18 the original's overpayment), Rev. Proc. 2025-32 §4.01 (the 2026 rate schedules) and §2.15 / P.L. 119-21 §70102
(the 2026 standard deduction: 32,200 joint, 16,100 single), the 2026 Form 1040 instructions' Tax Table method (tax at
the midpoint of the $50 bracket), IRC §24 as amended by P.L. 119-21 §70104 (the 2,200 child tax credit), and IRC §6511(a),
§6513, §7503 and §6013(b) for the dates. Nothing is taken from the engine.

The household (tests/test_return_workflow.py): Alex and Sam Rivera, married filing jointly, W-2 wages 52,000 and 41,000
with 4,100 and 2,900 withheld, 312.40 of interest. As filed: AGI 93,312; standard deduction 32,200; taxable income 61,112;
tax at the 61,100-61,150 midpoint 61,125 = 2,480 + 12% x 36,325 = 6,839; withholding 7,000; overpaid and refunded 161.
"""

from __future__ import annotations

import importlib
import json
from datetime import date

import pytest
import yaml
from test_reaudit_952ee96_premature_retention import on
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)

from agentledger.ledger import store as ledger
from agentledger.returns import facts
from agentledger.returns.amendment import filing_due_date, routing_number_valid
from agentledger.returns.model import IndividualReturn
from agentledger.returns.store import Returns, _check_fields
from agentledger.workflow.engine import TransitionError

NEC_NOTE = "issued in error; the payer is sending a corrected 1099"
EXPLAIN = "W-2c from City Schools: box 1 wages 43,000, not 41,000"
X = "f1040x"


def _file(R: Returns, rid: str) -> dict:
    """Review, approve, sign and paper-file a return through the real workflow; the original's approval facts."""
    for doc in R.unaccounted_documents(rid):
        R.account_for_document(rid, doc, "not_applicable", NEC_NOTE, "maya")
    R.confirm(rid, None, "maya")
    R.submit_for_review(rid, "maya")
    st = R.approve(rid, "lee", "cpa")
    R.request_signature(rid, "lee", "cpa")
    R.record_signature(rid, "taxpayer", method="wet_signature", return_hash=st.facts["approved_hash"])
    assert R.mark_paper_filed(rid, "lee", "cpa", "mailed by certified mail on 2027-04-01, receipt 7019 0000 0000").status == "paper_filed"
    return R.status(rid).facts


def _rivera_filed(fam, filed=date(2027, 4, 1)):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    with on(filed):
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
        _file(R, rid)
    f = R.latest(rid)["result"]["forms"]["f1040"]
    assert (f["11a"], f["15"], f["16"], f["24c"], f["25d"], f["34"], f["35a"]) == ("93312", "61112", "6839", "6839", "7000", "161", "161")
    return R, rid


def _amend(R: Returns, rid: str, when: date, change, amendment: dict | None = None) -> tuple[str, dict]:
    """Start the amendment on `when`, apply `change` to a copy of its inputs and the amendment facts; the computed result."""
    with on(when):
        am = R.start_amendment(rid, "lee")
        inputs = json.loads(json.dumps(R.latest(am)["inputs"]))
        change(inputs)
        if amendment is not None:
            inputs["amendment"] = amendment
        result = R.save_inputs(am, inputs, "maya")
    return am, result


def _spouse_w2c(inputs: dict) -> None:
    next(w for w in inputs["w2s"] if w["owner"] == "spouse")["wages"] = "43000"


def _add_child(inputs: dict) -> None:
    inputs["dependents"] = [{"first_name": "Ana", "last_name": "Rivera", "ssn": "400-00-0100", "dob": "2020-01-15", "relationship": "daughter"}]


def _no_change(inputs: dict) -> None:
    pass


def _codes(result: dict, severity: str | None = None) -> list[str]:
    return [d["code"] for d in result["diagnostics"] if severity is None or d["severity"] == severity]


# ------------------------------------------------------------------------------------------------- the three columns
def test_wage_correction_columns_a_b_c(fam):  # noqa: F811
    """A W-2c raises the spouse's wages by 2,000: AGI 95,312, taxable income 63,112, tax at the 63,125 midpoint = 2,480 +
    12% x 38,325 = 7,079 (+240). Payments stand at 7,000 (line 17, nothing paid with the original), line 18 takes the 161
    refunded, line 19 = 6,839, line 20 amount owed = 7,079 - 6,839 = 240."""
    R, rid = _rivera_filed(fam)
    am, res = _amend(R, rid, date(2027, 9, 1), _spouse_w2c, {"explanation": EXPLAIN, "paid_with_original_return": "0"})
    x = res["forms"][X]
    assert (x["11a.A"], x["11a.B"], x["11a.C"]) == ("93312", "2000", "95312")
    assert (x["12e.A"], x["12e.B"], x["12e.C"]) == ("32200", "0", "32200")
    assert (x["15.A"], x["15.B"], x["15.C"]) == ("61112", "2000", "63112")
    assert (x["16.A"], x["16.B"], x["16.C"]) == ("6839", "240", "7079")
    assert (x["21.A"], x["21.C"], x["23.C"]) == ("0", "0", "0")
    assert (x["24c.A"], x["24c.B"], x["24c.C"]) == ("6839", "240", "7079")
    assert (x["25d.A"], x["25d.B"], x["25d.C"]) == ("7000", "0", "7000")
    assert (x["27a.C"], x["32c.C"], x["33.C"]) == ("0", "0", "7000")
    assert (x["paid_with_extension"], x["paid_with_original"], x["paid_after_filing"]) == ("0", "0", "0")
    assert (x["total_payments"], x["original_overpayment"], x["net_payments"]) == ("7000", "161", "6839")
    assert (x["amount_owed"], x["overpayment"], x["refund"], x["applied_to_estimated_tax"]) == ("240", "0", "0", "0")
    assert res["summary"]["amended_amount_owed"] == "240" and res["summary"]["amended_refund"] == "0"
    assert res["summary"]["agi"] == "95312" and res["summary"]["total_tax"] == "7079"      # the corrected return's figures
    fx = res["facts"][X]
    assert fx["amends"] == rid and fx["explanation"] == EXPLAIN and fx["superseding"] is False
    assert fx["original_status"] == "paper_filed" and fx["original_filed_on"] == "2027-04-01" and fx["due_date"] == "2027-04-15"
    assert (fx["original_refund"], fx["original_amount_owed"], fx["refund_claim_by"]) == ("161", "0", "2030-04-15")
    assert fx["lines"][:3] == ["11a", "12e", "12f"] and fx["filing_status"] == "mfj" and not fx["filing_status_changed"]
    assert not _codes(res, "error") and "amendment_no_change" not in _codes(res)
    assert res["coverage"]["forms"][X] == "manual-assisted" and X in res["forms"]
    assert R.get(am)["amends"] == rid and R.get(am)["form"] == "1040-X"


def test_credit_correction_adds_a_dependent(fam):  # noqa: F811
    """The original omitted Ana (born 2020, an SSN): a 2,200 child tax credit (IRC §24(h)(2) as amended by P.L. 119-21) on
    line 19, no phase-out at 93,312 of AGI. Tax after credits 4,639 (-2,200); line 19 = 6,839 against total tax 4,639:
    overpaid 2,200, 500 of it applied to 2027 estimated tax (line 23) and 1,700 refunded (line 22). Part I lists Ana."""
    R, rid = _rivera_filed(fam)
    am, res = _amend(R, rid, date(2027, 9, 1), _add_child, {"explanation": "dependent Ana Rivera omitted from the original return",
                                                            "paid_with_original_return": "0", "apply_to_estimated_tax": "500",
                                                            "original_refund_received": "161"})
    x = res["forms"][X]
    assert (x["11a.B"], x["15.B"], x["16.B"]) == ("0", "0", "0")
    assert (x["19.A"], x["19.B"], x["19.C"]) == ("0", "2200", "2200")
    assert (x["21.C"], x["22.A"], x["22.B"], x["22.C"]) == ("2200", "6839", "-2200", "4639")
    assert (x["24c.A"], x["24c.B"], x["24c.C"], x["28.C"]) == ("6839", "-2200", "4639", "0")
    assert (x["total_payments"], x["original_overpayment"], x["net_payments"]) == ("7000", "161", "6839")
    assert (x["amount_owed"], x["overpayment"], x["applied_to_estimated_tax"], x["refund"]) == ("0", "2200", "500", "1700")
    fx = res["facts"][X]
    assert fx["dependents_changed"] and fx["original_dependents"] == [] and [d["first_name"] for d in fx["dependents"]] == ["Ana"]
    assert fx["dependents"][0]["child_tax_credit"] is True
    assert "amendment_refund_received_differs" not in _codes(res) and not _codes(res, "error")


def test_payments_with_and_after_the_original_return(fam):  # noqa: F811
    """A single filer entered by hand: wages 60,000 with 4,000 withheld, taxable income 43,900, tax at the 43,925 midpoint =
    1,240 + 12% x 31,525 = 5,023, so 1,023 was owed and paid with the return. The amendment adds 2,000 of omitted
    interest: taxable income 45,900, tax 5,263 (+240). Line 16 carries the 1,023 (and later payments), line 17 the total."""
    ledger.add_client(fam.conn, id="ortiz", name="Jordan Ortiz", kind="individual", emails=[], tax_id_last4="0009", domain="general",
                      facts={"taxpayer_ssn_last4": "0009"})
    single = {"tax_year": 2026, "filing_status": "single", "taxpayer": {"first_name": "Jordan", "last_name": "Ortiz", "ssn": "400-00-0009", "dob": "1990-01-01"},
              "w2s": [{"employer_name": "Acme", "wages": "60000", "federal_withholding": "4000", "ss_wages": "60000", "medicare_wages": "60000"}]}
    R = Returns(fam.conn, fam.kb)
    with on(date(2027, 4, 1)):
        rid = R.create("ortiz", 2026, "maya", single)
        R.compute(rid, "maya")
        _file(R, rid)
    f = R.latest(rid)["result"]["forms"]["f1040"]
    assert (f["16"], f["33"], f["37"]) == ("5023", "4000", "1023")

    def interest(inputs: dict) -> None:
        inputs["interest"] = [{"owner": "taxpayer", "payer": "First Bank", "interest": "2000"}]

    # The amount paid with the original return is never assumed: None blocks, 0 is an answer.
    am, res = _amend(R, rid, date(2027, 9, 1), interest, {"explanation": "omitted 1099-INT, First Bank, 2,000 of interest"})
    x = res["forms"][X]
    assert "amendment_paid_with_return_unknown" in _codes(res, "error")
    assert (x["16.A"], x["16.B"], x["16.C"], x["paid_with_original"], x["total_payments"], x["amount_owed"]) == ("5023", "240", "5263", "0", "4000", "1263")
    assert res["facts"][X]["original_amount_owed"] == "1023"

    def recompute(**amendment) -> dict:
        inputs = json.loads(json.dumps(R.latest(am)["inputs"]))
        inputs["amendment"] = {"explanation": "omitted 1099-INT, First Bank, 2,000 of interest", **amendment}
        with on(date(2027, 9, 1)):
            return R.save_inputs(am, inputs, "maya")

    res = recompute(paid_with_original_return="1023")
    x = res["forms"][X]
    assert (x["paid_with_original"], x["total_payments"], x["net_payments"], x["amount_owed"], x["overpayment"]) == ("1023", "5023", "5023", "240", "0")
    assert not _codes(res, "error")
    res = recompute(paid_with_original_return="1023", paid_after_filing="100", last_payment_on="2027-06-01")
    x = res["forms"][X]
    assert (x["paid_after_filing"], x["total_payments"], x["amount_owed"]) == ("100", "5123", "140")
    assert res["facts"][X]["refund_claim_by"] == "2030-04-15"                     # 3 years from the due date, later than 2 from the payment
    res = recompute(paid_with_original_return="1023", paid_after_filing="300")
    x = res["forms"][X]
    assert (x["total_payments"], x["amount_owed"], x["overpayment"], x["refund"]) == ("5323", "0", "60", "60")
    assert "amendment_payment_date_unknown" in _codes(res, "warning")             # 300 paid, when is not stated
    res = recompute(paid_with_original_return="1023", paid_after_filing="300", last_payment_on="2027-07-15", apply_to_estimated_tax="25")
    x = res["forms"][X]
    assert (x["overpayment"], x["applied_to_estimated_tax"], x["refund"]) == ("60", "25", "35")
    assert "amendment_payment_date_unknown" not in _codes(res)
    res = recompute(paid_with_original_return="1500")
    assert "amendment_paid_with_return_exceeds_owed" in _codes(res, "warning")


# ------------------------------------------------------------------------------------------------- gates
def test_the_explanation_is_a_review_blocker(fam):  # noqa: F811
    """Part III is required: submitting for review is refused, naming it (and not as an anonymous blocking diagnostic);
    stated, the amendment goes through review and approval without any coverage patch (f1040x is manual-assisted)."""
    R, rid = _rivera_filed(fam)
    with on(date(2027, 9, 1)):
        am = R.start_amendment(rid, "lee")
        res = R.compute(am, "maya")
        assert "amendment_explanation_missing" in _codes(res, "error") and "amendment_no_change" in _codes(res, "warning")
        assert "explanation" not in res["facts"][X]
        with pytest.raises(TransitionError, match="explain the changes on Form 1040-X, Part III") as e:
            R.submit_for_review(am, "maya")
        assert "blocking diagnostic" not in str(e.value)
        inputs = json.loads(json.dumps(R.latest(am)["inputs"]))
        inputs["amendment"] = {"explanation": "short"}
        res = R.save_inputs(am, inputs, "maya")
        assert "amendment_explanation_missing" in _codes(res, "error")
        with pytest.raises(TransitionError, match="explain the changes"):
            R.submit_for_review(am, "maya")
        _spouse_w2c(inputs)
        inputs["amendment"] = {"explanation": EXPLAIN, "paid_with_original_return": "0"}
        res = R.save_inputs(am, inputs, "maya")
        assert not _codes(res, "error") and res["facts"][X]["explanation"] == EXPLAIN
        R.populate_from_documents(am, "maya")
        [c] = R.conflicts(am)
        R.resolve_conflict(am, c["id"], "keep", "lee", note="W-2c from City Schools shows 43,000 in box 1")
        R.confirm(am, None, "maya")
        assert R.submit_for_review(am, "maya").status == "in_review"
        assert R.approve(am, "lee", "cpa").status == "approved"
    assert R.latest(am)["result"]["forms"][X]["amount_owed"] == "240"


def test_column_a_is_pinned_to_the_filed_version_after_a_rule_change(fam):  # noqa: F811
    """The idiom of test_rule_change_after_signature_voids_it: the standard deduction rule changes after the original was
    filed. Column C follows today's rules; column A is the sealed version the approval pinned, unchanged, and the
    original return is not touched."""
    R, rid = _rivera_filed(fam)
    approved = R.status(rid).facts
    original_before = R.latest(rid)
    with on(date(2027, 9, 1)):
        am = R.start_amendment(rid, "lee")
        inputs = json.loads(json.dumps(R.latest(am)["inputs"]))
        inputs["amendment"] = {"explanation": EXPLAIN, "paid_with_original_return": "0"}
        _spouse_w2c(inputs)
        before = R.save_inputs(am, inputs, "maya")["forms"][X]
    path = fam.kb._paths["us_fed.individual.standard_deduction"]
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for v in doc["values"]:
        if v["effective_from"] == "2026-01-01":
            v["value"]["mfj"] = 60000                                    # a (hypothetical) rule change
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    fam.kb.reload()
    with on(date(2027, 9, 2)):
        res = R.compute(am, "recalc-agent")
    x = res["forms"][X]
    assert (x["12e.A"], x["15.A"], x["16.A"], x["24c.A"]) == ("32200", "61112", "6839", "6839") == (before["12e.A"], before["15.A"], before["16.A"], before["24c.A"])
    assert (x["12e.C"], x["15.C"], x["16.C"]) == ("60000", "35312", "3743")      # 95,312 - 60,000; tax at the 35,325 midpoint = 2,480 + 12% x 10,525
    assert (x["16.B"], x["amount_owed"], x["overpayment"], x["refund"]) == ("-3096", "0", "3096", "3096")   # 6,839 - 3,743 against 7,000 - 161
    fx = res["facts"][X]
    assert fx["original_version"] == approved["approved_version"] and fx["original_package_hash"] == approved["approved_hash"] == approved["signed_hash"]
    after = R.latest(rid)
    assert after["version"] == original_before["version"] and after["result"] == original_before["result"]
    assert res["pinned"]["kb_version"] != original_before["result"]["pinned"]["kb_version"]


def test_superseding_or_amended_from_the_due_date(fam):  # noqa: F811
    """Filed by the due date (IRC §7503: April 15, 2027 for 2026; October 15 with Form 4868) the return supersedes the
    original; after it, it amends. Stated to the contrary: an error after the deadline, a warning before it."""
    R, rid = _rivera_filed(fam, filed=date(2027, 3, 1))
    am, res = _amend(R, rid, date(2027, 3, 20), _spouse_w2c, {"explanation": EXPLAIN, "paid_with_original_return": "0"})
    fx = res["facts"][X]
    assert fx["superseding"] is True and fx["deadline_for_superseding"] == "2027-04-15" and fx["original_filed_on"] == "2027-03-01"
    assert fx["refund_claim_by"] == "2030-04-15"                               # an early return is deemed filed on the due date (§6513(a))

    def recompute(when: date, **amendment) -> dict:
        inputs = json.loads(json.dumps(R.latest(am)["inputs"]))
        inputs["amendment"] = {"explanation": EXPLAIN, "paid_with_original_return": "0", **amendment}
        with on(when):
            return R.save_inputs(am, inputs, "maya")

    res = recompute(date(2027, 3, 20), superseding=False)
    assert res["facts"][X]["superseding"] is False and "amendment_superseding_possible" in _codes(res, "warning")
    res = recompute(date(2027, 9, 1))
    assert res["facts"][X]["superseding"] is False and not _codes(res, "error")
    res = recompute(date(2027, 9, 1), superseding=True)
    assert "amendment_superseding_after_due_date" in _codes(res, "error")
    res = recompute(date(2027, 9, 1), superseding=True, extension_filed=True)
    assert res["facts"][X]["superseding"] is True and res["facts"][X]["deadline_for_superseding"] == "2027-10-15" and not _codes(res, "error")
    res = recompute(date(2027, 9, 1), original_filed_on="2027-04-20")             # a stated postmark overrides the recorded date
    assert res["facts"][X]["original_filed_on"] == "2027-04-20" and res["facts"][X]["refund_claim_by"] == "2030-04-20"


def test_a_joint_return_is_not_changed_to_separate_after_the_due_date(fam):  # noqa: F811
    R, rid = _rivera_filed(fam)

    def separate(inputs: dict) -> None:
        inputs["filing_status"] = "mfs"
        inputs["w2s"] = [w for w in inputs["w2s"] if w["owner"] == "taxpayer"]

    am, res = _amend(R, rid, date(2027, 9, 1), separate, {"explanation": "the spouses file separately for 2026", "paid_with_original_return": "0"})
    assert "amendment_joint_to_separate" in _codes(res, "error")
    assert res["facts"][X]["filing_status_changed"] and res["facts"][X]["original_filing_status"] == "mfj"
    with pytest.raises(TransitionError, match="blocking diagnostic"):
        with on(date(2027, 9, 1)):
            R.submit_for_review(am, "maya")


def test_as_previously_adjusted_replaces_column_a_line_by_line(fam):  # noqa: F811
    """The IRS corrected the original's tax to 6,900 and refunded 100 (a math error notice): column A shows the adjusted
    lines, line 18 the adjusted overpayment, and the refund actually received is checked against them."""
    R, rid = _rivera_filed(fam)
    adjusted = {"reason": "CP11 of 2027-06-15: tax figured as 6,900, refund reduced to 100",
                "lines": {"16": "6900", "18": "6900", "22": "6900", "24c": "6900", "34": "100", "35a": "100"}}
    am, res = _amend(R, rid, date(2027, 9, 1), _spouse_w2c,
                     {"explanation": EXPLAIN, "paid_with_original_return": "0", "as_previously_adjusted": adjusted, "original_refund_received": "100"})
    x = res["forms"][X]
    assert (x["16.A"], x["16.B"], x["16.C"], x["24c.A"], x["11a.A"]) == ("6900", "179", "7079", "6900", "93312")
    assert (x["original_overpayment"], x["net_payments"], x["amount_owed"]) == ("100", "6900", "179")
    assert res["facts"][X]["as_previously_adjusted"] == {"reason": adjusted["reason"], "lines": {k: v for k, v in sorted(adjusted["lines"].items())}}
    assert res["facts"][X]["original_refund"] == "100" and not _codes(res, "error") and "amendment_refund_received_differs" not in _codes(res)
    inputs = json.loads(json.dumps(R.latest(am)["inputs"]))
    inputs["amendment"]["original_refund_received"] = "161"                      # what the return said, not what the IRS paid (100)
    inputs["amendment"]["as_previously_adjusted"] = {"reason": "short", "lines": {"16": "6900", "35a": "100", "99": "1"}}
    with on(date(2027, 9, 1)):
        res = R.save_inputs(am, inputs, "maya")
    assert {"amendment_adjusted_reason_missing", "amendment_adjusted_line_unknown"} <= set(_codes(res, "error"))
    assert "amendment_refund_received_differs" in _codes(res, "warning")


def test_refund_claim_after_the_statute_is_a_warning_with_the_date(fam):  # noqa: F811
    R, rid = _rivera_filed(fam)
    am, res = _amend(R, rid, date(2031, 1, 1), _add_child, {"explanation": "dependent Ana Rivera omitted from the original return",
                                                            "paid_with_original_return": "0"})
    assert res["forms"][X]["overpayment"] == "2200"
    [w] = [d for d in res["diagnostics"] if d["code"] == "amendment_refund_statute_expired"]
    assert w["severity"] == "warning" and "ended 2030-04-15" in w["message"] and "2031-01-01" in w["message"]
    inputs = json.loads(json.dumps(R.latest(am)["inputs"]))
    inputs["amendment"].update({"paid_after_filing": "50", "last_payment_on": "2029-06-01"})   # 2 years from a later payment (§6511(a))
    with on(date(2031, 1, 1)):
        res = R.save_inputs(am, inputs, "maya")
    assert res["facts"][X]["refund_claim_by"] == "2031-06-01" and "amendment_refund_statute_expired" not in _codes(res)


def test_only_an_accepted_or_paper_filed_return_is_amended(fam, monkeypatch):  # noqa: F811
    from agentledger import coverage

    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    R.confirm(rid, None, "maya")
    R.submit_for_review(rid, "maya")
    st = R.approve(rid, "lee", "cpa")
    with pytest.raises(TransitionError, match="only a filed return \\(accepted or filed on paper\\) can be amended; this one is approved"):
        R.start_amendment(rid, "lee")
    R.request_signature(rid, "lee", "cpa")
    R.record_signature(rid, "taxpayer", method="wet_signature", return_hash=st.facts["approved_hash"])
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})
    R.compute(rid, "lee")
    with on(date(2027, 4, 10)):                                                        # the electronic postmark
        assert R.transmit(rid, "lee", "cpa", efile_ready=True, submit=lambda key: {"submission_id": "00000020263000000001"}).status == "transmitted"
    with pytest.raises(TransitionError, match="this one is transmitted"):            # it may yet be rejected and corrected
        R.start_amendment(rid, "lee")
    with on(date(2027, 4, 12)):
        R.wf.send(rid, "ack_accepted", "mef-poller", facts={"ack": "A"})
        am = R.start_amendment(rid, "lee")
        res = R.compute(am, "maya")
    fx = res["facts"][X]
    assert fx["original_status"] == "accepted" and fx["original_filed_on"] == "2027-04-10" and fx["refund_claim_by"] == "2030-04-15"
    assert res["forms"][X]["16.A"] == "6839" and res["forms"][X]["original_overpayment"] == "161"
    # A Form 1040-X that names no original (created by hand) computes column C only and blocks: nothing is guessed for line 18.
    ledger.add_client(fam.conn, id="solo", name="Solo", kind="individual", emails=[], tax_id_last4="0007", domain="general", facts={})
    bare = R.create("solo", 2026, "maya", {**household(), "filing_status": "single", "spouse": None}, form="1040-X")
    res = R.compute(bare, "maya")
    assert "amendment_original_missing" in _codes(res, "error") and "11a.C" in res["forms"][X] and "11a.A" not in res["forms"][X]
    assert "original_overpayment" not in res["forms"][X] and res["summary"]["amended_amount_owed"] == "0"


def test_check_fields_accepts_the_amendment_facts_and_nothing_else(fam):  # noqa: F811
    full = {**household(), "amendment": {
        "explanation": EXPLAIN, "as_previously_adjusted": {"reason": "CP11 of 2027-06-15", "lines": {"16": "6900"}},
        "paid_with_original_return": "0", "paid_after_filing": "100", "last_payment_on": "2027-06-01", "original_refund_received": "161",
        "original_overpayment_applied": "0", "superseding": False, "extension_filed": True, "original_filed_on": "2027-04-01",
        "apply_to_estimated_tax": "25", "direct_deposit": {"routing_number": "021000021", "account_number": "000123456789", "account_type": "checking"}}}
    IndividualReturn.model_validate(full)
    _check_fields(full)
    with pytest.raises(facts.InputRejected, match="amendment.notice"):
        _check_fields({**household(), "amendment": {"explanation": EXPLAIN, "notice": "CP11"}})
    with pytest.raises(facts.InputRejected, match="amendment.as_previously_adjusted.line_16"):
        _check_fields({**household(), "amendment": {"explanation": EXPLAIN, "as_previously_adjusted": {"reason": "x", "line_16": "1"}}})
    with pytest.raises(facts.InputRejected, match="amendment.direct_deposit.bank"):
        _check_fields({**household(), "amendment": {"explanation": EXPLAIN, "direct_deposit": {"bank": "Chase"}}})
    R, rid = _rivera_filed(fam)
    am, res = _amend(R, rid, date(2027, 9, 1), _add_child, {"explanation": "dependent Ana Rivera omitted from the original return",
                                                            "paid_with_original_return": "0",
                                                            "direct_deposit": {"routing_number": "123456789", "account_number": "", "account_type": None}})
    assert _codes(res, "error").count("amendment_direct_deposit_invalid") == 2
    assert res["facts"][X]["direct_deposit"] == {"routing_number": "123456789", "account_number_last4": "", "account_type": None}


def test_due_dates_and_routing_numbers():
    assert filing_due_date(2026) == date(2027, 4, 15) and filing_due_date(2026, extended=True) == date(2027, 10, 15)
    assert filing_due_date(2021) == date(2022, 4, 18)      # April 15, 2022 was Emancipation Day observed (April 16 a Saturday)
    assert filing_due_date(2022) == date(2023, 4, 18)      # April 15, 2023 a Saturday; Monday the 17th the observed holiday
    assert filing_due_date(2027) == date(2028, 4, 18)      # the same pattern as 2023
    assert routing_number_valid("021000021") and not routing_number_valid("123456789") and not routing_number_valid("02100002")


# ------------------------------------------------------------------------------------------------- the API
def test_api_reads_the_columns_for_the_cpa_and_the_taxpayer(home, monkeypatch):
    """Jordan Lee, single, wages 60,000 with 6,000 withheld (refund 977 as filed); the amendment corrects the wages to
    62,000: taxable income 45,900, tax 5,263 (+240), line 18 = 977, line 19 = 5,023, amount owed 240."""
    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))
    monkeypatch.setenv("AGENTLEDGER_AGENTS", "0")
    monkeypatch.setenv("AGENTLEDGER_DEV_AUTH", "1")
    import agentledger.api.app as api_mod

    importlib.reload(api_mod)
    from fastapi.testclient import TestClient

    c = TestClient(api_mod.app)
    cpa = {"Authorization": "Bearer dev-cpa"}
    jordan = {"Authorization": "Bearer dev-jordan"}
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=cpa).status_code == 200
    add_doc(api_mod.APP.conn, "d1", "jordan-lee", "W-2", {"employer_name": "Acme", "box1": "60000", "box2": "6000"})
    rid = c.post("/api/clients/jordan-lee/returns", json={"tax_year": 2026, "inputs": {
        "filing_status": "single", "taxpayer": {"first_name": "Jordan", "ssn": "400-00-0009", "dob": "1990-01-01"}}}, headers=cpa).json()["id"]
    c.post(f"/api/returns/{rid}/populate", headers=cpa)
    c.post(f"/api/returns/{rid}/confirm", json={}, headers=cpa)
    assert c.post(f"/api/returns/{rid}/submit", headers=cpa).json()["status"] == "in_review"
    assert c.post(f"/api/returns/{rid}/approve", headers=cpa).json()["status"] == "approved"
    assert c.post(f"/api/returns/{rid}/request-signature", headers=cpa).json()["status"] == "awaiting_signature"
    user = api_mod.users()["dev-cpa"]
    rs = api_mod.R(user)                                       # the signature and the paper filing have no API route yet
    rs.record_signature(rid, "taxpayer", method="wet_signature", return_hash=rs.status(rid).facts["approved_hash"])
    assert rs.mark_paper_filed(rid, user["id"], "cpa", "mailed by certified mail on 2027-04-10, receipt 7019 0000 0000").status == "paper_filed"
    assert c.post(f"/api/returns/{rid}/amend", headers=jordan).status_code == 403
    am = c.post(f"/api/returns/{rid}/amend", headers=cpa).json()["id"]
    assert c.post(f"/api/returns/{rid}/amend", headers=cpa).status_code == 409           # one Form 1040-X per year
    full = c.get(f"/api/returns/{am}", headers=cpa).json()
    assert full["return"]["amends"] == rid and full["return"]["form"] == "1040-X" and full["status"] == "preparing"
    inputs = full["inputs"]
    inputs["w2s"][0]["wages"] = "62000"
    inputs["amendment"] = {"explanation": "W-2c from Acme: box 1 wages 62,000, not 60,000", "paid_with_original_return": "0"}
    assert c.put(f"/api/returns/{am}/inputs", json={**inputs, "amendment": {**inputs["amendment"], "bogus": 1}}, headers=cpa).status_code == 400
    r = c.put(f"/api/returns/{am}/inputs", json=inputs, headers=cpa)
    assert r.status_code == 200, r.text
    full = c.get(f"/api/returns/{am}", headers=cpa).json()
    x = full["result"]["forms"]["f1040x"]
    assert (x["11a.A"], x["11a.B"], x["11a.C"]) == ("60000", "2000", "62000")
    assert (x["16.A"], x["16.B"], x["16.C"]) == ("5023", "240", "5263")
    assert (x["total_payments"], x["original_overpayment"], x["net_payments"], x["amount_owed"], x["refund"]) == ("6000", "977", "5023", "240", "0")
    assert full["summary"]["amended_amount_owed"] == "240" and full["result"]["facts"]["f1040x"]["amends"] == rid
    assert full["result"]["coverage"]["forms"]["f1040x"] == "manual-assisted"
    mine = c.get(f"/api/returns/{am}", headers=jordan).json()
    assert "inputs" not in mine and mine["forms"]["f1040x"]["amount_owed"] == "240" and mine["forms"]["f1040x"]["11a.A"] == "60000"
    assert mine["forms"]["f1040"]["11a"] == "62000" and mine["status"] == "preparing"
