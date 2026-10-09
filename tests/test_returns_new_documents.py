"""T1-01 S0: the new document types (1099-SA, 5498, 5498-SA, 1095-A, the prior-year return) and the prior-year group.

Classification, population with provenance, the facts gates (required, implied and coded amounts are never zero or a
default), unknown keys refused at every depth, and the workflow: a return whose new document item lacks its required
amount, or whose saver's credit facts are unknown, never reaches review (acceptance Q19; idiom of
tests/test_reaudit_952ee96_missing_amounts.py).
"""

from __future__ import annotations

import json
import typing
from pathlib import Path

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)

from agentledger import coverage
from agentledger.evidence import records
from agentledger.intake import classify
from agentledger.ledger import store as ledger
from agentledger.returns import documents, facts
from agentledger.returns.facts import InputRejected
from agentledger.returns.model import IndividualReturn
from agentledger.returns.store import TAX_FORMS, Returns, _check_fields
from agentledger.workflow.engine import TransitionError

REPO = Path(__file__).resolve().parent.parent
NEW_TYPES = ("1099-SA", "5498", "5498-SA", "1095-A", "Prior-year return")
F1040_PAGE = ("Form1040 U.S. Individual Income Tax Return\nDepartment of the Treasury-Internal Revenue Service\n2025 OMB No. 1545-0074\n"
              "For the year Jan. 1-Dec. 31, 2025 ... Attach Form(s) W-2 here. Also attach Forms W-2G and 1099-R if tax was withheld.\n"
              "11 Subtract line 10 from line 9. This is your adjusted gross income 11a 57,000.00")


# ------------------------------------------------------------------------------------------------- classification
@pytest.mark.parametrize("text,expected", [
    ("Form 1099-SA Distributions From an HSA, Archer MSA, or Medicare Advantage MSA 2026 Copy B", "1099-SA"),
    ("Form 5498-SA HSA, Archer MSA, or Medicare Advantage MSA Information 2026", "5498-SA"),
    ("Form 5498 IRA Contribution Information 2026 Copy B For Participant", "5498"),
    ("Form 5498-ESA Coverdell ESA Contribution Information 2026", None),
    ("Form 1095-A Health Insurance Marketplace Statement Part III Coverage Information", "1095-A"),
    (F1040_PAGE, "Prior-year return"),
    ("Form 1099-R Distributions From Pensions, Annuities, Retirement 2026", "1099-R"),
], ids=["1099-SA", "5498-SA", "5498", "5498-ESA-is-not-5498", "1095-A", "filed-1040-not-the-1099-R-it-mentions", "1099-R-still"])
def test_detectors(text, expected):
    assert classify.detect(text).doc_type == expected


def test_new_types_are_known_everywhere():
    literal = set(typing.get_args(classify.DocType))
    assert set(NEW_TYPES) <= literal and set(NEW_TYPES) <= set(classify.FOLDERS) and set(NEW_TYPES) <= set(TAX_FORMS)
    policy = records.load_policy(REPO / "config")
    assert policy["by_doc_type"]["5498"] == "property_basis"                 # IRA basis records are kept until released
    assert all(policy["by_doc_type"][t] == "tax_return_support" for t in ("1099-SA", "5498-SA", "1095-A", "Prior-year return"))
    assert coverage.lookup("f8880", 2026)["status"] == "manual-assisted" and coverage.form_id("ws_capital_loss_carryover") is None
    assert "capital loss carryover to 2027 not computed" not in coverage.lookup("sch_d", 2026).get("limits", [])


# ------------------------------------------------------------------------------------------------- population
def test_1099_sa_5498_and_5498_sa_populate_with_provenance_and_codes(fam):  # noqa: F811
    add_doc(fam.conn, "d_sa", "rivera", "1099-SA", {"payer_name": "Health Trust", "recipient_tin_last4": "0001", "box1": "1,250.00",
                                                     "box3": "1", "box5": "HSA"})
    add_doc(fam.conn, "d_5498", "rivera", "5498", {"payer_name": "Vanguard", "recipient_tin_last4": "0002", "box1": "7000", "box5": "88,000",
                                                   "box7": "Roth IRA", "box10": "0", "box11": "X"})
    add_doc(fam.conn, "d_5498sa", "rivera", "5498-SA", {"payer_name": "Health Trust", "recipient_tin_last4": "0001", "box2": "4150",
                                                        "box3": "500", "box6": "HSA"})
    pop = documents.populate(fam.conn, "rivera", 2026, joint=True)
    [sa] = pop.inputs["hsa_distributions"]
    assert sa == {"owner": "taxpayer", "source_document": "d_sa", "gross_distribution": "1250.00", "distribution_code": "1",
                  "account_type": "hsa", "trustee": "Health Trust"}
    assert pop.provenance["hsa_distributions[0].distribution_code"] == {"document_id": "d_sa", "box": "box3", "value": "1"}
    [ira] = pop.inputs["ira_accounts"]
    assert ira == {"owner": "spouse", "source_document": "d_5498", "ira_contributions": "7000", "fmv": "88000", "account_type": "roth",
                   "roth_contributions": "0", "rmd_required_next_year": True, "trustee": "Vanguard"}
    assert pop.provenance["ira_accounts[0].fmv"] == {"document_id": "d_5498", "box": "box5", "value": "88000"}
    [hsa] = pop.inputs["hsa_contributions"]
    assert hsa == {"owner": "taxpayer", "source_document": "d_5498sa", "total_contributions": "4150", "following_year_contributions": "500",
                   "account_type": "hsa", "trustee": "Health Trust"}
    assert not pop.unreadable and not facts.missing_required(pop.inputs, pop.provenance, pop.unreadable)
    IndividualReturn.model_validate({**household(), **{k: pop.inputs[k] for k in ("hsa_distributions", "ira_accounts", "hsa_contributions")}})


def test_1095_a_monthly_columns_and_annual_totals(fam):  # noqa: F811
    fields = {"issuer_name": "Blue Plan", "recipient_tin_last4": "0001", "policy_number": "P-1", "marketplace": "TX",
              "covered_individuals": "Alex Rivera; Sam Rivera", "line21_a": "900.00", "line21_b": "950.00", "line21_c": "400.00",
              "premium_02": "900.00", "slcsp_2": "950.00", "aptc_02": "400.00", "line33_a": "1800", "line33_b": "1900", "line33_c": "800"}
    add_doc(fam.conn, "d_1095", "rivera", "1095-A", fields)
    pop = documents.populate(fam.conn, "rivera", 2026, joint=True)
    [m] = pop.inputs["marketplace_coverage"]
    assert m["premium_01"] == "900.00" and m["slcsp_01"] == "950.00" and m["aptc_01"] == "400.00"
    assert m["premium_02"] == "900.00" and m["slcsp_02"] == "950.00" and m["aptc_02"] == "400.00"
    assert (m["annual_premium"], m["annual_slcsp"], m["annual_aptc"]) == ("1800", "1900", "800")
    assert m["policy_number"] == "P-1" and m["marketplace"] == "TX" and m["issuer"] == "Blue Plan"
    assert m["covered_individuals"] == ["Alex Rivera", "Sam Rivera"]
    assert pop.provenance["marketplace_coverage[0].aptc_02"] == {"document_id": "d_1095", "box": "aptc_02", "value": "400.00"}
    assert not facts.missing_required(pop.inputs, pop.provenance, pop.unreadable)
    IndividualReturn.model_validate({**household(), "marketplace_coverage": pop.inputs["marketplace_coverage"]})


def test_prior_year_return_populates_the_prior_year_group(fam):  # noqa: F811
    add_doc(fam.conn, "d_1040_2025", "rivera", "Prior-year return",
            {"filing_status": "Married filing jointly", "line11": "88,000.00", "line24": "6,400", "capital_loss_carryover_long": "10000"},
            year=2025)
    add_doc(fam.conn, "d_1040_2026_draft", "rivera", "Prior-year return", {"line11": "1"}, year=2026)   # this year's own return
    pop = documents.populate(fam.conn, "rivera", 2026, joint=True)
    assert pop.inputs["prior_year"] == {"filing_status": "mfj", "agi": "88000.00", "tax": "6400", "capital_loss_carryover_long": "10000"}
    assert pop.provenance["prior_year.capital_loss_carryover_long"] == {"document_id": "d_1040_2025", "box": "capital_loss_carryover_long",
                                                                        "value": "10000"}
    assert "d_1040_2025" in pop.documents and "d_1040_2026_draft" not in pop.documents
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    v = R.latest(rid)
    assert v["inputs"]["prior_year"]["capital_loss_carryover_long"] == "10000" and v["result"]["forms"]["sch_d"]["14"] == "-10000"
    assert v["provenance"]["prior_year.agi"]["document_id"] == "d_1040_2025"
    unaccounted = R.unaccounted_documents(rid)
    assert "d_1040_2025" not in unaccounted and "d_1040_2026_draft" in unaccounted   # a person says what this year's copy is


def test_two_prior_year_returns_that_disagree_leave_the_line_to_a_person(fam):  # noqa: F811
    add_doc(fam.conn, "d_a", "rivera", "Prior-year return", {"line11": "88000"}, year=2025)
    add_doc(fam.conn, "d_b", "rivera", "Prior-year return", {"line11": "91000", "_unverified": {"line24": "6,4OO"}}, year=2025)
    pop = documents.populate(fam.conn, "rivera", 2026, joint=True)
    assert pop.inputs["prior_year"]["agi"] == "88000"                            # the first stays; nothing is averaged
    missing = facts.missing_required(pop.inputs, pop.provenance, pop.unreadable)
    assert sorted(m["anchor"] for m in missing) == ["missing:prior_year.agi", "missing:prior_year.tax"]
    assert any(i["code"] == "prior_year_disagrees" and i["blocking"] == "true" for i in pop.issues)
    entered = {**pop.inputs, "prior_year": {**pop.inputs["prior_year"], "agi": "88000", "tax": "6400"}}
    prov = {**pop.provenance, "prior_year.agi": {"source": "preparer", "confirmed": True}}
    assert facts.missing_required(entered, prov, pop.unreadable) == []


# ------------------------------------------------------------------------------------------------- the facts gates
@pytest.mark.parametrize("lst,item,field,why", [
    ("hsa_distributions", {"owner": "taxpayer", "distribution_code": "1"}, "gross_distribution", "required"),
    ("hsa_distributions", {"owner": "taxpayer", "gross_distribution": "500", "distribution_code": "9"}, "distribution_code", "a valid code is required"),
    ("hsa_distributions", {"owner": "taxpayer", "gross_distribution": "500"}, "distribution_code", "a valid code is required"),
    ("hsa_contributions", {"owner": "taxpayer", "fmv": "9000"}, "total_contributions", "required"),
    ("ira_accounts", {"owner": "taxpayer", "ira_contributions": "7000"}, "fmv", "required"),
    ("marketplace_coverage", {"owner": "taxpayer", "annual_slcsp": "950"}, "annual_premium", "required"),
    ("marketplace_coverage", {"owner": "taxpayer", "annual_premium": "1800", "annual_aptc": "800"}, "annual_slcsp", "implied by annual_aptc"),
], ids=["sa-gross", "sa-bad-code", "sa-no-code", "5498sa-total", "5498-fmv", "1095a-premium", "1095a-slcsp-implied"])
def test_new_lists_report_missing_amounts_never_zero(lst, item, field, why):
    [m] = facts.missing_required({lst: [item]})
    assert (m["list"], m["field"], m["why"]) == (lst, field, why)
    assert not facts.missing_required({lst: [{**item, field: "950" if field != "distribution_code" else "3"}]})


def test_codes_are_checked_per_list():
    assert facts.valid_code("7D") and facts.valid_code("G", "retirement") and not facts.valid_code("7D", "hsa_distributions")
    assert facts.valid_code("3", "hsa_distributions") and not facts.valid_code("7", "hsa_distributions") and not facts.valid_code("", "hsa_distributions")
    assert facts.satisfied("hsa_distributions", "distribution_code", "6") and not facts.satisfied("hsa_distributions", "distribution_code", "0")


@pytest.mark.parametrize("extra,key", [
    ({"hsa_distributions": [{"owner": "taxpayer", "gross_distribution": "500", "distribution_code": "1", "box1": "500"}]}, "hsa_distributions[0].box1"),
    ({"ira_accounts": [{"owner": "taxpayer", "fmv": "100", "box7": "IRA"}]}, "ira_accounts[0].box7"),
    ({"hsa_contributions": [{"owner": "taxpayer", "total_contributions": "100", "employer": "x"}]}, "hsa_contributions[0].employer"),
    ({"marketplace_coverage": [{"owner": "taxpayer", "annual_premium": "100", "premium_13": "1"}]}, "marketplace_coverage[0].premium_13"),
    ({"retirement_savings": [{"owner": "taxpayer", "testing_period": "0"}]}, "retirement_savings[0].testing_period"),
], ids=["1099-SA", "5498", "5498-SA", "1095-A-month-13", "retirement_savings"])
def test_unknown_keys_inside_the_new_lists_are_refused(extra, key):
    with pytest.raises(InputRejected) as e:
        _check_fields({**household(), **extra})
    assert key in str(e.value)


# ------------------------------------------------------------------------------------------------- the workflow (Q19)
def _must_not_reach_review(R, rid, why, expect):
    try:
        st = R.submit_for_review(rid, "maya")
    except TransitionError as e:
        assert R.status(rid).status == "preparing"
        assert expect in str(e), f"{why}: review was refused, but not for the reason under test: {e}"
        return
    pytest.fail(f"{why}: the return reached '{st.status}'")


DOCS = [  # a filed document whose required amount, implied amount or code cannot be read
    ("1099-SA", {"payer_name": "Health Trust", "recipient_tin_last4": "0001", "box3": "1"}, "hsa_distributions", "gross_distribution"),
    ("1099-SA", {"payer_name": "Health Trust", "recipient_tin_last4": "0001", "box1": "900", "box3": "l"}, "hsa_distributions", "distribution_code"),
    ("5498", {"payer_name": "Vanguard", "recipient_tin_last4": "0001", "box1": "7000"}, "ira_accounts", "fmv"),
    ("5498-SA", {"payer_name": "Health Trust", "recipient_tin_last4": "0001", "box5": "9000"}, "hsa_contributions", "total_contributions"),
    ("1095-A", {"issuer_name": "Blue Plan", "recipient_tin_last4": "0001", "line33_a": "1800", "line33_c": "800"}, "marketplace_coverage", "annual_slcsp"),
]


@pytest.mark.parametrize("doc_type,fields,lst,fld", DOCS, ids=[f"{d[0]}-{d[3]}" for d in DOCS])
def test_a_new_document_without_its_required_amount_never_reaches_review(fam, doc_type, fields, lst, fld):  # noqa: F811
    add_doc(fam.conn, "d_new", "rivera", doc_type, fields)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    [item] = R.latest(rid)["inputs"][lst]
    assert item["source_document"] == "d_new" and not facts.satisfied(lst, fld, item.get(fld))   # on the return, amount missing
    assert any(c["anchor"] == f"missing:{lst}[d_new].{fld}" for c in R.conflicts(rid))
    R.account_for_document(rid, "d_nec", "not_applicable", "issued in error; the payer is sending a corrected 1099", "maya")
    R.confirm(rid, None, "maya")
    _must_not_reach_review(R, rid, f"{doc_type} without {fld}", "lack a required amount")


@pytest.fixture
def solo(foundry):
    ledger.add_client(foundry.conn, id="jordan", name="Jordan Lee", kind="individual", emails=[], tax_id_last4="0009",
                      domain="general", facts={"taxpayer_ssn_last4": "0009", "taxpayer_name": "Jordan Lee"})
    return foundry


JORDAN = {"tax_year": 2026, "filing_status": "single",
          "taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0009", "dob": "1990-01-01"},
          "w2s": [{"owner": "taxpayer", "employer_name": "Night Shift Co", "wages": "30000", "box12": {"D": "3000"}}]}


def test_unknown_savers_credit_facts_block_review_until_stated(solo):
    R = Returns(solo.conn, solo.kb)
    rid = R.create("jordan", 2026, "maya", JORDAN)
    R.compute(rid, "maya")
    res = R.latest(rid)["result"]
    assert [d["code"] for d in res["diagnostics"] if d["severity"] == "error"] == [
        "form_8880_testing_period_unknown", "form_8880_student_status_unknown"]                 # both asked at once
    assert res["forms"].get("sch_3", {}).get("4", "0") == "0" and "f8880" not in res["forms"]   # a zero Schedule 3 is dropped
    _must_not_reach_review(R, rid, "saver's credit facts unknown", "blocking diagnostic(s)")
    stated = json.loads(json.dumps(JORDAN))
    stated["retirement_savings"] = [{"owner": "taxpayer", "testing_period_distributions": "0"}]
    R.save_inputs(rid, stated, "maya")
    assert [d["code"] for d in R.latest(rid)["result"]["diagnostics"] if d["severity"] == "error"] == ["form_8880_student_status_unknown"]
    _must_not_reach_review(R, rid, "student status unknown", "blocking diagnostic(s)")
    stated["taxpayer"]["full_time_student"] = False
    R.save_inputs(rid, stated, "maya")
    res = R.latest(rid)["result"]
    assert res["forms"]["f8880"]["12"] == "200" and res["forms"]["sch_3"]["4"] == "200"
    assert res["coverage"]["forms"]["f8880"] == "manual-assisted" and res["coverage"]["below_preparation"] == []
    assert R.submit_for_review(rid, "maya").status == "in_review"


def test_a_filed_1040_of_the_prior_year_is_evidence_the_return_relies_on(fam):  # noqa: F811
    add_doc(fam.conn, "d_1040_2025", "rivera", "Prior-year return", {"line11": "88000", "capital_loss_carryover_short": "4000"}, year=2025)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    uses = {r[0] for r in fam.conn.execute("SELECT document_id FROM return_document_uses WHERE return_id = ?", (rid,)).fetchall()}
    assert "d_1040_2025" in uses
    assert fam.conn.execute("SELECT detail FROM return_retention_facts WHERE return_id = ? AND kind = 'carryover' ORDER BY version DESC",
                            (rid,)).fetchone()[0] == "prior_year.capital_loss_carryover_short"
    assert R.latest(rid)["result"]["forms"]["sch_d"]["6"] == "-4000"
