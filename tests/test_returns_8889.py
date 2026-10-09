"""Form 8889, health savings accounts, IRC §223 (T1-01, S2).

Expected values are worked by hand from the 2026 limits of Rev. Proc. 2025-19 §2.01(1) ($4,400 self-only, $8,750 family;
the $1,000 additional contribution of §223(b)(3)), the Instructions for Form 8889 (2025 edition, the latest posted on
2026-10-09: the line 3 rules, the Line 3 Limitation Chart and Worksheet, lines 6, 7, 9, 13-21) and the examples of
Pub. 969 (2025), pages 5-7, re-worked with the 2026 figures. Lines are whole dollars (the engine's convention), so a
chart result of $729.17 appears as 729. Nothing below was taken from the engine. Schedule 2 line keys follow the 2026
draft (13c, 13d, 14), not the 2025 Form 8889's references to 17c/17d.
"""

from datetime import date
from decimal import Decimal

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)
from test_returns_1040 import ctx, run, spouse, you
from test_returns_new_documents import _must_not_reach_review

from agentledger.returns.facts import InputRejected
from agentledger.returns.individual import compute_individual
from agentledger.returns.model import (W2, Adjustments, HSAContribution, HSADistribution, HSAFacts, IndividualReturn, Person,
                                       PriorYear)
from agentledger.returns.store import Returns, _check_fields

F = "f8889[taxpayer]"


def months(kind="self_only", **override):
    """hsa_facts coverage fields: `kind` on the first day of every month, with overrides such as coverage_11="family"."""
    out = {f"coverage_{m:02d}": kind for m in range(1, 13)}
    out.update(override)
    return out


def lines(r, form=F):
    return {k: int(v) for k, v in r.forms[form].items()}


# ------------------------------------------------------------------------------------------------- Part I
def test_self_only_all_year_with_employer_contributions():
    """Instructions, line 3 rule 3: eligible with the same coverage on the first day of every month, line 3 = $4,400.
    Form 5498-SA box 2 shows 4,400 contributed, of which 2,000 came from the employer (W-2 box 12 code W): line 2 =
    2,400, lines 5, 6, 8 = 4,400, 7 = 0, 9 = 2,000, 11 = 2,000, 12 = 2,400, 13 = 2,400 to Schedule 1 line 13; wages
    60,000 give AGI 57,600. No last-month-rule amount carries."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000, box12={"W": 2000})],
            hsa_contributions=[HSAContribution(total_contributions=4400)], hsa_facts=[HSAFacts(**months())])
    assert lines(r) == {"2": 2400, "3": 4400, "5": 4400, "6": 4400, "7": 0, "8": 4400, "9": 2000, "10": 0, "11": 2000, "12": 2400, "13": 2400}
    assert r.line("sch_1", "13") == 2400 and r.line("f1040", "11a") == 57600 and r.line("sch_1", "8f") == 0
    assert r.sheets.facts[F]["1"] == "self_only" and r.sheets.facts[F]["last_month_rule"] is False
    assert r.carryforwards["hsa_last_month_rule_excess"] == 0 and not r.blocking
    assert "us_fed.individual.hsa_contribution_limit" in {s["rule_id"] for s in r.sources}


def test_pub_969_example_2_changed_coverage_under_the_last_month_rule():
    """Pub. 969 Example 2 with 2026 figures: age 39, self-only coverage January 1, family coverage from November 1,
    8,750 contributed because of the family coverage on December 1. The chart: 10 x 4,400 + 2 x 8,750 = 61,500, / 12 =
    5,125; line 3 is the greater of 5,125 and the December-1 family limit 8,750 (rule 4), so the whole 8,750 is deducted.
    The 3,625 (8,750 - 5,125) allowed only by the last-month rule carries to 2027: it is 2027 income (and a 10% tax) if
    the person fails the testing period (Pub. 969: 8,550 - 5,008.33 = 3,541.67 in 2025 terms)."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], hsa_contributions=[HSAContribution(total_contributions=8750)],
            hsa_facts=[HSAFacts(**months(coverage_11="family", coverage_12="family"))])
    assert lines(r) == {"2": 8750, "3": 8750, "5": 8750, "6": 8750, "7": 0, "8": 8750, "9": 0, "10": 0, "11": 0, "12": 8750, "13": 8750}
    assert r.sheets.facts[F]["chart_total"] == "61500" and r.sheets.facts[F]["chart"]["11"] == "8750" and r.sheets.facts[F]["chart"]["01"] == "4400"
    assert r.sheets.facts[F]["last_month_rule"] is True and r.sheets.facts[F]["1"] == "family"
    assert r.carryforwards["hsa_last_month_rule_excess"] == 3625 and not r.blocking


def test_pub_969_example_1_eligible_only_in_december():
    """Pub. 969 Example 1 with 2026 figures: age 53, became an eligible individual on December 1 with family coverage and
    contributed 8,750 under the last-month rule. The chart has only December: 8,750 / 12 = 729.17 (729 in whole dollars);
    line 3 = 8,750; the 8,021 (8,750 - 729, Pub. 969: 7,837.50 for 2025) carries for Part III of the 2027 return."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], hsa_contributions=[HSAContribution(total_contributions=8750)],
            hsa_facts=[HSAFacts(**months("none", coverage_12="family"))])
    assert r.line(F, "3") == 8750 and r.line(F, "13") == 8750 and r.sheets.facts[F]["chart_total"] == "8750"
    assert r.carryforwards["hsa_last_month_rule_excess"] == 8021 and not r.blocking


def test_pub_969_medicare_example_prorates_the_limit_and_the_catch_up():
    """Pub. 969, "Enrolled in Medicare" example with 2026 figures: age 61 (55 or older), self-only coverage, enrolled in
    Medicare from July. The chart counts January-June at 5,400 (4,400 + the 1,000 additional contribution): 32,400 / 12 =
    2,700 (Pub. 969: 5,300 x 6 / 12 = 2,650 for 2025). Not eligible on December 1, so no last-month rule: line 3 = 2,700,
    and 2,700 contributed is deducted in full."""
    r = run(filing_status="single", taxpayer=Person(first_name="Alex", last_name="Rivera", ssn="400-00-0001", dob=date(1965, 3, 1)),
            w2s=[W2(wages=60000)], hsa_contributions=[HSAContribution(total_contributions=2700)],
            hsa_facts=[HSAFacts(**months(), medicare_from_month=7)])
    assert r.line(F, "3") == 2700 and r.line(F, "13") == 2700 and r.sheets.facts[F]["chart"]["06"] == "5400" and r.sheets.facts[F]["chart"]["07"] == "0"
    assert r.sheets.facts[F]["last_month_rule"] is False and r.carryforwards["hsa_last_month_rule_excess"] == 0 and not r.blocking


def test_married_spouses_both_with_hsas_share_the_family_limit_and_line_7():
    """Instructions, "How To Complete Part I" and line 6: spouses who both have HSAs are both treated as having family
    coverage when either does; the 8,750 limit is divided equally (4,375 each) absent an agreed allocation. Line 7
    (instructions, line 7): the taxpayer is 58, married and had family coverage all year, so the 1,000 additional
    contribution goes on line 7, not line 3. Taxpayer: 6 = 4,375, 7 = 1,000, 8 = 5,375, 13 = min(5,000, 5,375) = 5,000.
    Spouse (40, self-only plan, treated as family): 6 = 4,375, 7 = 0, 13 = min(4,000, 4,375) = 4,000. Schedule 1 line 13 =
    9,000; both Forms 8889 are in the return."""
    tp = Person(first_name="Alex", last_name="Rivera", ssn="400-00-0001", dob=date(1968, 6, 1))
    r = run(filing_status="mfj", taxpayer=tp, spouse=spouse(), w2s=[W2(wages=90000)],
            hsa_contributions=[HSAContribution(owner="taxpayer", total_contributions=5000), HSAContribution(owner="spouse", total_contributions=4000)],
            hsa_facts=[HSAFacts(owner="taxpayer", **months("family")), HSAFacts(owner="spouse", **months("self_only"))])
    assert lines(r) == {"2": 5000, "3": 8750, "5": 8750, "6": 4375, "7": 1000, "8": 5375, "9": 0, "10": 0, "11": 0, "12": 5375, "13": 5000}
    assert lines(r, "f8889[spouse]") == {"2": 4000, "3": 8750, "5": 8750, "6": 4375, "7": 0, "8": 4375, "9": 0, "10": 0, "11": 0, "12": 4375, "13": 4000}
    assert r.line("sch_1", "13") == 9000 and not r.blocking


def test_agreed_allocation_of_the_family_limit():
    """Line 6: the spouses may allocate the family limit as they agree (Pub. 969, "Rules for married people"): 8,750 to the
    taxpayer, nothing to the spouse. The spouse's 1,000 contribution is then an excess (Form 5329), a blocking diagnostic."""
    r = run(filing_status="mfj", taxpayer=you(), spouse=spouse(), w2s=[W2(wages=90000)],
            hsa_contributions=[HSAContribution(owner="taxpayer", total_contributions=8750), HSAContribution(owner="spouse", total_contributions=1000)],
            hsa_facts=[HSAFacts(owner="taxpayer", family_limit_share=8750, **months("family")),
                       HSAFacts(owner="spouse", family_limit_share=0, **months("family"))])
    assert r.line(F, "6") == 8750 and r.line(F, "13") == 8750
    assert [d.code for d in r.blocking] == ["form_5329_excess_hsa_contributions"]


def test_unmarried_55_or_older_adds_the_catch_up_on_line_3():
    """Line 3 rule 6: unmarried and 55 or older with self-only coverage all year, line 3 = 4,400 + 1,000 = 5,400."""
    r = run(filing_status="single", taxpayer=Person(first_name="Alex", last_name="Rivera", ssn="400-00-0001", dob=date(1965, 3, 1)),
            w2s=[W2(wages=60000)], hsa_contributions=[HSAContribution(total_contributions=5400)], hsa_facts=[HSAFacts(**months())])
    assert r.line(F, "3") == 5400 and r.line(F, "7") == 0 and r.line(F, "13") == 5400 and not r.blocking


def test_following_year_contributions_and_funding_distribution():
    """Line 2 includes contributions made by April 15 of the next year for this year (5498-SA box 3) and excludes a
    qualified HSA funding distribution (line 10) and this year's contributions that were for last year: box 2 = 3,000
    (of which 500 was for 2025, 1,000 an IRA funding distribution), box 3 = 400: line 2 = 3,000 + 400 - 1,000 - 500 =
    1,900; line 10 = 1,000; line 12 = 4,400 - 1,000 = 3,400; line 13 = 1,900."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)],
            hsa_contributions=[HSAContribution(total_contributions=3000, following_year_contributions=400)],
            hsa_facts=[HSAFacts(qualified_funding_distribution=1000, contributions_for_last_year=500, **months())])
    assert r.line(F, "2") == 1900 and r.line(F, "10") == 1000 and r.line(F, "12") == 3400 and r.line(F, "13") == 1900 and not r.blocking


# ------------------------------------------------------------------------------------------------- Parts II and III
def test_part_ii_taxable_distribution_and_the_twenty_percent_tax():
    """Form 1099-SA box 1 = 3,000 (code 1), 2,500 used for qualified medical expenses: 14a = 14c = 3,000, 15 = 2,500,
    16 = 500 to Schedule 1 line 8f, 17b = 20% x 500 = 100 to Schedule 2 line 13c (line 14 = 100, into line 15 and the
    total tax). Wages 60,000: AGI 60,500."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)],
            hsa_distributions=[HSADistribution(gross_distribution=3000, distribution_code="1")], hsa_facts=[HSAFacts(qualified_medical_expenses=2500)])
    assert lines(r) == {"14a": 3000, "14b": 0, "14c": 3000, "15": 2500, "16": 500, "17b": 100}
    assert r.line("sch_1", "8f") == 500 and r.line("f1040", "11a") == 60500
    assert r.line("sch_2", "13c") == 100 and r.line("sch_2", "14") == 100 and r.line("sch_2", "15") == 100 and r.line("f1040", "23") == 100
    assert r.sheets.facts[F]["17a"] is False and not r.blocking


def test_sixty_five_before_the_year_and_disability_escape_the_twenty_percent_tax():
    """Instructions, lines 17a and 17b: no additional tax on distributions after the beneficiary turns 65 (here 65 before
    2026 began) or becomes disabled (code 3); the taxable 500 is still income."""
    older = run(filing_status="single", taxpayer=Person(first_name="Alex", last_name="Rivera", ssn="400-00-0001", dob=date(1958, 7, 1)),
                w2s=[W2(wages=60000)], hsa_distributions=[HSADistribution(gross_distribution=3000, distribution_code="1")],
                hsa_facts=[HSAFacts(qualified_medical_expenses=2500)])
    assert older.line(F, "16") == 500 and older.line(F, "17b") == 0 and older.sheets.facts[F]["17a"] is True and not older.blocking
    disabled = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)],
                   hsa_distributions=[HSADistribution(gross_distribution=3000, distribution_code="3")], hsa_facts=[HSAFacts(qualified_medical_expenses=2500)])
    assert disabled.line(F, "16") == 500 and disabled.line(F, "17b") == 0 and not disabled.blocking
    rolled = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)],
                 hsa_distributions=[HSADistribution(gross_distribution=3000, distribution_code="1")],
                 hsa_facts=[HSAFacts(qualified_medical_expenses=0, rollovers_and_withdrawn_excess=3000)])
    assert rolled.line(F, "14b") == 3000 and rolled.line(F, "16") == 0 and rolled.line("sch_1", "8f") == 0 and not rolled.blocking


def test_part_iii_pub_969_example_1_failing_the_testing_period():
    """Pub. 969 Example 1, the 2026 return: the 2025 contributions allowed only by the last-month rule (7,837.50, carried as
    7,838) are included in 2026 income because the person ceased to be eligible in June 2026: line 18 = 20 = 7,838 to
    Schedule 1 line 8f, line 21 = 10% = 784 to Schedule 2 line 13d (and line 14). Wages 60,000: AGI 67,838."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], prior_year=PriorYear(hsa_last_month_rule_excess=7838),
            hsa_facts=[HSAFacts(testing_period_failed=True)])
    assert lines(r) == {"18": 7838, "19": 0, "20": 7838, "21": 784}
    assert r.line("sch_1", "8f") == 7838 and r.line("f1040", "11a") == 67838
    assert r.line("sch_2", "13d") == 784 and r.line("sch_2", "14") == 784 and not r.blocking


# ------------------------------------------------------------------------------------------------- blocking (Q19)
def person(**kw):
    return Person(first_name="Alex", last_name="Rivera", ssn="400-00-0001", **kw)


@pytest.mark.parametrize("kw,code", [
    (dict(hsa_contributions=[HSAContribution(total_contributions=4400)]), "form_8889_coverage_unknown"),
    (dict(hsa_contributions=[HSAContribution(total_contributions=4400)], hsa_facts=[HSAFacts(**months(coverage_07=None))]), "form_8889_coverage_unknown"),
    (dict(w2s=[W2(wages=60000, box12={"W": 2000})], hsa_facts=[HSAFacts(**months())]), "form_8889_contributions_unknown"),
    (dict(taxpayer=person(), hsa_contributions=[HSAContribution(total_contributions=4400)], hsa_facts=[HSAFacts(**months())]), "form_8889_age_unknown"),
    (dict(hsa_distributions=[HSADistribution(gross_distribution=3000, distribution_code="1")]), "form_8889_medical_expenses_unknown"),
    (dict(hsa_distributions=[HSADistribution(gross_distribution=3000, distribution_code="1")], hsa_facts=[HSAFacts()]), "form_8889_medical_expenses_unknown"),
    (dict(taxpayer=person(dob=date(1961, 7, 1)), hsa_distributions=[HSADistribution(gross_distribution=3000, distribution_code="1")],
          hsa_facts=[HSAFacts(qualified_medical_expenses=0)]), "form_8889_exception_unknown"),
    (dict(hsa_contributions=[HSAContribution(total_contributions=5000)], hsa_facts=[HSAFacts(**months())]), "form_5329_excess_hsa_contributions"),
    (dict(w2s=[W2(wages=60000, box12={"W": 5000})], hsa_contributions=[HSAContribution(total_contributions=5000)], hsa_facts=[HSAFacts(**months())]),
     "form_8889_excess_employer_contributions"),
    (dict(hsa_contributions=[HSAContribution(total_contributions=4400, account_type="archer_msa")], hsa_facts=[HSAFacts(**months())]), "form_8853_required"),
    (dict(hsa_distributions=[HSADistribution(gross_distribution=3000, distribution_code="4")], hsa_facts=[HSAFacts(qualified_medical_expenses=0)]),
     "form_8889_death_distribution"),
    (dict(hsa_distributions=[HSADistribution(gross_distribution=300, distribution_code="2")], hsa_facts=[HSAFacts(qualified_medical_expenses=0)]),
     "form_8889_distribution_code_unsupported"),
    (dict(hsa_facts=[HSAFacts(testing_period_failed=True)]), "form_8889_last_month_rule_excess_unknown"),
    (dict(hsa_contributions=[HSAContribution(total_contributions=4400)], hsa_facts=[HSAFacts(**months())], adjustments=Adjustments(hsa_deduction=4400)),
     "form_8889_deprecated_input"),
], ids=["no-facts", "one-month-unknown", "code-W-without-5498-SA", "dob-unknown", "expenses-unknown", "expenses-not-stated", "turned-65-exception-unknown",
        "excess", "excess-employer", "archer", "death", "code-2", "testing-period-without-prior", "deprecated-hsa_deduction"])
def test_unknown_facts_and_scope_limits_block_instead_of_guessing(kw, code):
    kw = {"filing_status": "single", "taxpayer": you(), "w2s": [W2(wages=60000)], **kw}
    r = run(**kw)
    assert code in [d.code for d in r.blocking], [(d.code, d.message) for d in r.diagnostics]
    if code != "form_8889_deprecated_input":
        assert F not in r.forms and r.line("sch_1", "13") == 0                  # nothing claimed or taxed while unknown


def test_married_filing_separately_with_family_coverage_needs_the_allocation():
    r = run(filing_status="mfs", taxpayer=you(), spouse=spouse(), w2s=[W2(wages=60000)],
            hsa_contributions=[HSAContribution(total_contributions=4375)], hsa_facts=[HSAFacts(**months("family"))])
    assert [d.code for d in r.blocking] == ["form_8889_family_allocation_unknown"]
    stated = run(filing_status="mfs", taxpayer=you(), spouse=spouse(), w2s=[W2(wages=60000)],
                 hsa_contributions=[HSAContribution(total_contributions=4375)], hsa_facts=[HSAFacts(family_limit_share=4375, **months("family"))])
    assert stated.line(F, "6") == 4375 and stated.line(F, "13") == 4375 and not stated.blocking


def test_missing_published_limits_block_rather_than_guess(monkeypatch):
    c = ctx()
    original = c.try_param
    monkeypatch.setattr(c, "try_param", lambda rid, on: None if rid == "us_fed.individual.hsa_contribution_limit" else original(rid, on))
    ret = IndividualReturn(tax_year=2026, filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)],
                           hsa_contributions=[HSAContribution(total_contributions=4400)], hsa_facts=[HSAFacts(**months())])
    r = compute_individual(c, ret)
    assert [d.code for d in r.blocking] == ["hsa_contribution_limit_rule_missing"] and r.line("sch_1", "13") == 0


def test_the_rule_values_for_2026_and_the_hdhp_minimums():
    kb = ctx().kb
    lim = kb.resolve("us_fed.individual.hsa_contribution_limit", date(2026, 6, 30))
    assert lim.value == {"self_only": 4400, "family": 8750, "catch_up": 1000} and "Rev. Proc. 2025-19" in lim.source
    assert lim.url == "https://www.irs.gov/pub/irs-drop/rp-25-19.pdf" and kb.try_resolve("us_fed.individual.hsa_contribution_limit", date(2027, 1, 1)) is None
    assert kb.resolve("us_fed.individual.hsa_contribution_limit", date(2025, 6, 30)).value == {"self_only": 4300, "family": 8550, "catch_up": 1000}
    hdhp = kb.resolve("us_fed.individual.hdhp_minimums", date(2026, 6, 30)).value
    assert hdhp == {"self_only_deductible": 1700, "family_deductible": 3400, "self_only_out_of_pocket": 8500, "family_out_of_pocket": 17000}


@pytest.mark.parametrize("extra,key", [
    ({"hsa_facts": [{"owner": "taxpayer", "coverage_13": "family"}]}, "hsa_facts[0].coverage_13"),
    ({"hsa_facts": [{"owner": "taxpayer", "medical_expenses": "100"}]}, "hsa_facts[0].medical_expenses"),
])
def test_unknown_keys_inside_hsa_facts_are_refused(extra, key):
    with pytest.raises(InputRejected) as e:
        _check_fields({**household(), **extra})
    assert key in str(e.value)


def test_a_5498_sa_without_coverage_facts_never_reaches_review(fam):  # noqa: F811
    """Q19: the 5498-SA is on the return, its contributions known, but the HDHP coverage months are not stated; the
    deduction is not claimed and review is refused until the facts are entered."""
    add_doc(fam.conn, "d_5498sa", "rivera", "5498-SA", {"payer_name": "Health Trust", "recipient_tin_last4": "0001", "box2": "4400", "box6": "HSA"})
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", "issued in error; the payer is sending a corrected 1099", "maya")
    R.confirm(rid, None, "maya")
    res = R.latest(rid)["result"]
    assert "form_8889_coverage_unknown" in [d["code"] for d in res["diagnostics"] if d["severity"] == "error"]
    assert res["forms"].get("sch_1", {}).get("13", "0") == "0"
    _must_not_reach_review(R, rid, "HDHP coverage unknown", "blocking diagnostic(s)")
    v = R.latest(rid)
    stated = {**v["inputs"], "hsa_facts": [{"owner": "taxpayer", **{k: "self_only" for k in months()}}]}
    R.save_inputs(rid, stated, "maya")
    res = R.latest(rid)["result"]
    assert res["forms"]["f8889[taxpayer]"]["13"] == "4400" and res["forms"]["sch_1"]["13"] == "4400"
    assert res["coverage"]["forms"]["f8889"] == "manual-assisted" and res["coverage"]["below_preparation"] == []
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.carryforwards(rid)["hsa_last_month_rule_excess"] == Decimal(0)
