"""Form 8606 (nondeductible IRAs) and the IRA Deduction Worksheet, IRC §219, §72(e)(8), §408(d)(2), §408A (T1-01, S2).

Expected values are worked by hand from the 2026 figures of Notice 2025-67, page 4 ($7,500 limit, $1,100 catch-up at
50; phase-out ranges 81,000-91,000 single, 129,000-149,000 joint when the contributor is covered, 242,000-252,000 when
only the spouse is covered, 0-10,000 separate; Roth 153,000-168,000 single, 242,000-252,000 joint), the IRA Deduction
Worksheet of the Form 1040 instructions (lines 1-12; its 70%/35% multipliers are the limit over the 10,000/20,000
range, so 75%/37.5% for 2026, 86%/43% at age 50), Pub. 590-A (2025) Worksheet 1-2 and its Examples 1 and 2, Appendix B
(Social Security recipients), Pub. 590-B (2025) Worksheet 1-1 and its Rose Green example, the Form 8606 (2025) lines and
instructions (the line 4 example), and the Pub. 590-B ordering rules example (Amelia). Each IRS example is re-worked
with the 2026 figures, as stated in the test. Nothing below was taken from the engine.
"""

from datetime import date
from decimal import Decimal

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)
from test_returns_1040 import ctx, run, spouse, you
from test_returns_new_documents import _must_not_reach_review

from agentledger.returns.facts import InputRejected
from agentledger.returns.individual import compute_individual
from agentledger.returns.model import (W2, Adjustments, IndividualReturn, Interest, IRAAccount, IRAFacts, Person, PriorYear,
                                       Retirement, SocialSecurity)
from agentledger.returns.store import Returns, _check_fields

F = "f8606[taxpayer]"
WS = "ws_ira_deduction"


def lines(r, form):
    return {k: int(v) for k, v in r.forms[form].items()}


def trad(contributions, fmv, **kw):
    return IRAAccount(ira_contributions=contributions, fmv=fmv, account_type="ira", **kw)


# ------------------------------------------------------------------------------------------------- the deduction
def test_pub_590a_example_1_covered_spouse_in_the_phase_out_range():
    """Pub. 590-A Worksheet 1-2 Example 1 with 2026 figures: joint return, both 39, the taxpayer covered by an employer
    plan, the spouse not; salaries 69,000 and 51,500 plus 9,000 of interest put modified AGI at 129,500, inside the
    joint range 129,000-149,000. Each contributes 7,500. Taxpayer's column: line 6 = 149,000 - 129,500 = 19,500; x
    7,500 / 20,000 = 7,312.50, raised to the next $10 = 7,320 (2025: 19,500 x 35% = 6,825); deduction 7,320, the 180 left
    is nondeductible (Form 8606 line 1, with a stated zero basis: line 14 = 180). Spouse's column: not covered and the
    couple's income is below 242,000, so the full 7,500. Schedule 1 line 20 = 14,820 (2025: 13,825)."""
    r = run(filing_status="mfj", taxpayer=you(), spouse=spouse(), w2s=[W2(owner="taxpayer", wages=69000, retirement_plan=True), W2(owner="spouse", wages=51500)],
            interest=[Interest(interest=9000)],
            ira_accounts=[trad(7500, 7500, owner="taxpayer"), trad(7500, 7500, owner="spouse")],
            prior_year=PriorYear(traditional_ira_basis=0, spouse_traditional_ira_basis=0))
    assert r.line(WS, "5") == 129500 and r.line(WS, "2a") == 149000 and r.line(WS, "6a") == 19500 and r.line(WS, "7a") == 7320
    assert r.line(WS, "12a") == 7320 and r.line(WS, "7b") == 7500 and r.line(WS, "12b") == 7500 and r.line("sch_1", "20") == 14820
    assert r.sheets.facts[WS]["1a"] is True and r.sheets.facts[WS]["1b"] is False
    assert lines(r, F) == {"1": 180, "2": 0, "3": 180, "14": 180} and "f8606[spouse]" not in r.forms
    assert r.line("f1040", "11a") == 129500 - 14820 and not r.blocking
    assert r.carryforwards["traditional_ira_basis"] == 180 and r.carryforwards["spouse_traditional_ira_basis"] == 0


def test_pub_590a_example_2_spousal_ira_of_the_uncovered_spouse():
    """Pub. 590-A Worksheet 1-2 Example 2 with 2026 figures: joint return, both 39; the taxpayer earns 45,500 and is
    covered, the spouse has no compensation and is not covered; 199,000 of interest puts modified AGI at 244,500. The
    taxpayer's column: 244,500 is 149,000 or more, nothing is deductible (7,500 nondeductible on Form 8606). The spouse's
    column (Kay Bailey Hutchison spousal IRA): line 6 = 252,000 - 244,500 = 7,500; x 7,500 / 10,000 = 5,625, raised to
    5,630 (2025: 7,500 x 70% = 5,250); compensation = the taxpayer's 45,500 less the taxpayer's 7,500 IRA contribution
    = 38,000; deduction = min(5,630, 38,000, 7,500) = 5,630; 1,870 nondeductible. Schedule 1 line 20 = 5,630."""
    r = run(filing_status="mfj", taxpayer=you(), spouse=spouse(), w2s=[W2(owner="taxpayer", wages=45500, retirement_plan=True)],
            interest=[Interest(interest=199000)],
            ira_accounts=[trad(7500, 7500, owner="taxpayer"), trad(7500, 7500, owner="spouse")],
            prior_year=PriorYear(traditional_ira_basis=0, spouse_traditional_ira_basis=0))
    assert r.line(WS, "5") == 244500 and r.line(WS, "7a") == 0 and r.line(WS, "12a") == 0
    assert r.line(WS, "2b") == 252000 and r.line(WS, "6b") == 7500 and r.line(WS, "7b") == 5630 and r.line(WS, "10b") == 38000 and r.line(WS, "12b") == 5630
    assert r.line("sch_1", "20") == 5630
    assert lines(r, F) == {"1": 7500, "2": 0, "3": 7500, "14": 7500} and lines(r, "f8606[spouse]") == {"1": 1870, "2": 0, "3": 1870, "14": 1870}
    assert not r.blocking


def test_single_covered_filer_the_200_floor_and_the_catch_up():
    """IRA Deduction Worksheet line 7, single and covered: modified AGI 90,900 gives line 6 = 91,000 - 90,900 = 100, x 75%
    = 75, but not less than $200: deduction 200 of a 7,500 contribution, 7,300 nondeductible. At 50 (born 1976) the limit
    is 8,600 and the multiplier 86%: modified AGI 85,000 gives 6,000 x 0.86 = 5,160 (a multiple of $10); deduction 5,160."""
    floor = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=90900, retirement_plan=True)], ira_accounts=[trad(7500, 7500)],
                prior_year=PriorYear(traditional_ira_basis=0))
    assert floor.line(WS, "6a") == 100 and floor.line(WS, "7a") == 200 and floor.line("sch_1", "20") == 200 and floor.line(F, "1") == 7300
    fifty = run(filing_status="single", taxpayer=Person(first_name="Alex", last_name="Rivera", ssn="400-00-0001", dob=date(1976, 5, 1)),
                w2s=[W2(wages=85000, retirement_plan=True)], ira_accounts=[trad(8600, 8600)], prior_year=PriorYear(traditional_ira_basis=0))
    assert fifty.line(WS, "6a") == 6000 and fifty.line(WS, "7a") == 5160 and fifty.line("sch_1", "20") == 5160 and fifty.line(F, "1") == 3440
    assert not floor.blocking and not fifty.blocking


def test_not_covered_the_full_limit_and_compensation_cap():
    """Not covered by a plan: the deduction is the smaller of the 7,500 limit, taxable compensation and the contribution.
    Wages 50,000 and a 6,000 contribution deduct 6,000 (no Form 8606: nothing is nondeductible, the stated zero basis
    carries as zero). A 7,500 contribution on 5,000 of wages is an excess contribution (Form 5329), blocking."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], ira_accounts=[trad(6000, 6000)], prior_year=PriorYear(traditional_ira_basis=0))
    assert r.line(WS, "7a") == 7500 and r.line(WS, "10a") == 50000 and r.line(WS, "12a") == 6000 and r.line("sch_1", "20") == 6000
    assert F not in r.forms and r.carryforwards["traditional_ira_basis"] == 0 and not r.blocking
    excess = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=5000)], ira_accounts=[trad(7500, 7500)], prior_year=PriorYear(traditional_ira_basis=0))
    assert [d.code for d in excess.blocking] == ["form_5329_excess_ira_contributions"]


def test_pub_590a_appendix_b_social_security_recipient():
    """Pub. 590-A Appendix B comprehensive example with 2026 figures: joint return, the taxpayer 65 and covered by a 401(k),
    wages 125,000, Social Security benefits 12,000, an 8,600 contribution (the age-50 limit); the spouse did not work.
    Worksheet 1 (modified AGI with the benefits taxable before any IRA deduction): 1 = 125,000; 3 = 6,000; 6 = 131,000;
    8 = 131,000 - 32,000 = 99,000; 10 = 99,000 - 12,000 = 87,000; 13 = 6,000; 14 = 73,950; 15 = 79,950; 16 = 10,200;
    17 = 10,200; 19 = 135,200. Worksheet 2: 149,000 - 135,200 = 13,800; x 8,600 / 20,000 = 5,934, raised to 5,940;
    deduction = min(5,940, 125,000, 8,600) = 5,940; 2,660 nondeductible (Form 8606 line 1). Worksheet 3 (the benefits
    taxable with the deduction): 3 = 119,060; 8 = 125,060; 10 = 93,060; 12 = 81,060; 15 = 6,000; 16 = 68,901; 17 =
    74,901; 18 = 10,200; taxable benefits 10,200. AGI = 125,000 + 10,200 - 5,940 = 129,260."""
    tp = Person(first_name="Alex", last_name="Rivera", ssn="400-00-0001", dob=date(1961, 1, 1))
    r = run(filing_status="mfj", taxpayer=tp, spouse=spouse(), w2s=[W2(wages=125000, retirement_plan=True)],
            social_security=[SocialSecurity(net_benefits=12000)], ira_accounts=[trad(8600, 8600)], prior_year=PriorYear(traditional_ira_basis=0))
    ws1 = r.sheets.facts[WS]["appendix_b_worksheet_1"]
    assert (ws1["1"], ws1["2"], ws1["17"], ws1["19"]) == ("125000", "12000", "10200", "135200")
    assert r.line(WS, "3") == 135200 and r.line(WS, "5") == 135200 and r.line(WS, "6a") == 13800 and r.line(WS, "7a") == 5940
    assert r.line("sch_1", "20") == 5940 and r.line(F, "1") == 2660 and r.line(F, "14") == 2660
    assert r.line("f1040", "6b") == 10200 and r.line("f1040", "11a") == 129260 and not r.blocking


def test_the_nondeductible_election_and_the_deprecated_input():
    """IRA Deduction Worksheet line 12: a smaller deduction may be taken and the rest treated as nondeductible. Not covered,
    7,500 contributed, 2,000 elected nondeductible: deduction 5,500, Form 8606 line 1 = 2,000, line 14 = 2,000 + the stated
    basis of 1,000 = 3,000. The retired adjustments.ira_deduction input blocks."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], ira_accounts=[trad(7500, 7500)],
            prior_year=PriorYear(traditional_ira_basis=1000), ira_facts=[IRAFacts(nondeductible_election=2000)])
    assert r.line("sch_1", "20") == 5500 and lines(r, F) == {"1": 2000, "2": 1000, "3": 3000, "14": 3000}
    assert r.carryforwards["traditional_ira_basis"] == 3000 and not r.blocking
    old = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], adjustments=Adjustments(ira_deduction=6000))
    assert [d.code for d in old.blocking] == ["ira_deduction_deprecated_input"] and old.line("sch_1", "20") == 0


# ------------------------------------------------------------------------------------------------- Part I with distributions
def test_pub_590b_rose_green_worksheet_1_1():
    """Pub. 590-B Worksheet 1-1 illustrated (Rose Green) with 2026 figures: single, covered, basis 300 at the end of 2025,
    a 2,000 contribution for 2026 that may be partly nondeductible, 5,000 distributed and converted to a Roth IRA, IRAs
    worth 20,000 at year end. Worksheet 1-1: 3 = 2,300; 6 = 25,000; 7 = 0.092; 8 = 460 (nontaxable, Form 8606 lines 13
    and 17); 9 = 4,540; 10 = 4,540 (all allocable to the conversion, line 18); 11 = 0 (line 15a). Wages of 84,460 put
    modified AGI at 89,000 (84,460 + the 4,540 conversion income): line 6 = 2,000 x 75% = 1,500 deductible (the example's
    1,500), so line 1 = 500, line 3 = 800 and the basis carried, line 14 = 800 - 460 = 340. Form 1040: 4a = 5,000, 4b =
    4,540; Schedule 1 line 20 = 1,500."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=84460, retirement_plan=True)],
            ira_accounts=[trad(2000, 20000), IRAAccount(roth_conversion=5000, fmv=5000, account_type="roth")],
            retirement=[Retirement(gross_distribution=5000, ira_sep_simple=True, distribution_code="2")],
            prior_year=PriorYear(traditional_ira_basis=300), ira_facts=[IRAFacts(contributions_after_year_end=0)])
    ws = r.sheets.facts[F]["worksheet_1_1"]
    assert (ws["3"], ws["6"], ws["7"], ws["8"], ws["9"], ws["10"], ws["11"]) == ("2300", "25000", "0.092", "460", "4540", "4540", "0")
    assert lines(r, F) == {"1": 500, "2": 300, "3": 800, "7": 0, "8": 5000, "13": 460, "14": 340, "15a": 0, "15c": 0, "16": 5000, "17": 460, "18": 4540}
    assert r.line(WS, "5") == 89000 and r.line("sch_1", "20") == 1500
    assert r.line("f1040", "4a") == 5000 and r.line("f1040", "4b") == 4540 and r.line("f1040", "11a") == 84460 + 4540 - 1500
    assert r.carryforwards["traditional_ira_basis"] == 340 and not r.blocking


def test_form_8606_lines_4_to_15c_pro_rata_distribution():
    """Form 8606 Part I with no new contributions: basis 3,000 (line 2), a 10,000 distribution (line 7), IRAs worth 40,000
    (line 6): 5 = 3,000; 9 = 50,000; 10 = 3,000 / 50,000 = 0.060; 12 = 600; 13 = 600; 14 = 2,400; 15a = 15c = 9,400 on
    Form 1040 line 4b (4a = 10,000). The 2,400 basis carries to 2027."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], ira_accounts=[trad(None, 40000)],
            retirement=[Retirement(gross_distribution=10000, ira_sep_simple=True, distribution_code="7")], prior_year=PriorYear(traditional_ira_basis=3000))
    assert lines(r, F) == {"1": 0, "2": 3000, "3": 3000, "4": 0, "5": 3000, "6": 40000, "7": 10000, "8": 0, "9": 50000, "11": 0, "12": 600, "13": 600,
                           "14": 2400, "15a": 9400, "15c": 9400, "16": 0, "17": 0, "18": 0}
    assert r.sheets.facts[F]["10"] == "0.060" and r.line("f1040", "4a") == 10000 and r.line("f1040", "4b") == 9400
    assert r.carryforwards["traditional_ira_basis"] == 2400 and not r.blocking


def test_form_8606_instructions_line_4_example():
    """Form 8606 instructions, line 4 example, with a distribution: contributions of 2,000 in May 2026 and 2,000 in January
    2027 (4,000 on Form 5498 box 1), 3,000 deductible and 1,000 elected nondeductible from the May contribution, so line 1
    = 1,000 and line 4 = 0. Not covered, wages 50,000; a 2,000 distribution; IRAs worth 30,000: 5 = 1,000; 9 = 32,000;
    10 = 1,000 / 32,000 = 0.031; 12 = 62; 13 = 62; 14 = 938; 15c = 1,938 on line 4b. Schedule 1 line 20 = 3,000."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], ira_accounts=[trad(4000, 30000)],
            retirement=[Retirement(gross_distribution=2000, ira_sep_simple=True, distribution_code="7")], prior_year=PriorYear(traditional_ira_basis=0),
            ira_facts=[IRAFacts(nondeductible_election=1000, contributions_after_year_end=0)])
    assert r.line("sch_1", "20") == 3000
    assert lines(r, F) == {"1": 1000, "2": 0, "3": 1000, "4": 0, "5": 1000, "6": 30000, "7": 2000, "8": 0, "9": 32000, "11": 0, "12": 62, "13": 62,
                           "14": 938, "15a": 1938, "15c": 1938, "16": 0, "17": 0, "18": 0}
    assert r.sheets.facts[F]["10"] == "0.031" and r.line("f1040", "4b") == 1938 and not r.blocking


def test_no_basis_distribution_is_fully_taxable_without_a_warning():
    """The IRA box checked, basis stated as zero and no contributions: the whole distribution is taxable (box 2a when the
    payer determined it), no Form 8606 and no "taxable amount not determined" warning; code 1 owes the 10% tax."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)],
            retirement=[Retirement(gross_distribution=10000, ira_sep_simple=True, distribution_code="1", rollover_amount=4000)],
            prior_year=PriorYear(traditional_ira_basis=0))
    assert r.line("f1040", "4a") == 10000 and r.line("f1040", "4b") == 6000 and r.line("sch_2", "5") == 600
    assert F not in r.forms and not any(d.code == "1099r_taxable_not_determined" for d in r.diagnostics) and not r.blocking


# ------------------------------------------------------------------------------------------------- Part III
def test_pub_590b_amelia_ordering_rules():
    """Pub. 590-B, Ordering Rules for Distributions, example (Amelia) as a 2026 return whose 5-year period is not yet met:
    a 5,000 regular Roth contribution for the year (Form 5498 box 10), a 7,000 nonqualified distribution at age 60 (code T,
    5-year period not met), prior conversions of 80,000 (the basis in conversions). Part III: 19 = 7,000; 21 = 7,000; 22 =
    0 + 5,000; 23 = 2,000; 24 = 80,000; 25a = 25c = 0: the first 5,000 returns the contribution, the next 2,000 was
    included in income when converted. Form 1040: 4a = 7,000, 4b = 0. Carried: Roth basis 0, conversion basis 78,000."""
    tp = Person(first_name="Alex", last_name="Rivera", ssn="400-00-0001", dob=date(1966, 1, 15))
    r = run(filing_status="single", taxpayer=tp, w2s=[W2(wages=50000)],
            ira_accounts=[IRAAccount(roth_contributions=5000, fmv=120000, account_type="roth")],
            retirement=[Retirement(gross_distribution=7000, distribution_code="T")],
            prior_year=PriorYear(roth_ira_basis=0, roth_conversion_basis=80000), ira_facts=[IRAFacts(roth_five_year_period_met=False)])
    assert lines(r, F) == {"19": 7000, "20": 0, "21": 7000, "22": 5000, "23": 2000, "24": 80000, "25a": 0, "25c": 0}
    assert r.line("f1040", "4a") == 7000 and r.line("f1040", "4b") == 0 and r.line("sch_1", "20") == 0
    assert r.carryforwards["roth_ira_basis"] == 0 and r.carryforwards["roth_conversion_basis"] == 78000 and not r.blocking


def test_qualified_roth_distributions_skip_part_iii():
    """Code Q, or code T with the 5-year period met: a qualified distribution, 4a only; the Roth basis grows by the year's
    contributions and carries."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], ira_accounts=[IRAAccount(roth_contributions=3000, fmv=50000, account_type="roth")],
            retirement=[Retirement(gross_distribution=9000, distribution_code="Q")], prior_year=PriorYear(roth_ira_basis=20000, roth_conversion_basis=0))
    assert r.line("f1040", "4a") == 9000 and r.line("f1040", "4b") == 0 and F not in r.forms
    assert r.carryforwards["roth_ira_basis"] == 23000 and r.carryforwards["roth_conversion_basis"] == 0 and not r.blocking


def test_early_roth_distribution_of_earnings_needs_form_5329():
    """Code J beyond the regular contributions: 23 = 3,000 - 1,000 = 2,000, no conversions, 25c = 2,000 taxable on line 4b,
    and Form 5329 Part I (the 10% tax and any recapture) is required: a blocking diagnostic with a stable code."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], retirement=[Retirement(gross_distribution=3000, distribution_code="J")],
            prior_year=PriorYear(roth_ira_basis=1000, roth_conversion_basis=0))
    assert lines(r, F) == {"19": 3000, "20": 0, "21": 3000, "22": 1000, "23": 2000, "24": 0, "25a": 2000, "25c": 2000}
    assert r.line("f1040", "4b") == 2000 and [d.code for d in r.blocking] == ["form_5329_required"]


# ------------------------------------------------------------------------------------------------- Roth contribution limit
def test_maximum_roth_ira_contribution_worksheet():
    """Form 8606 instructions, Maximum Roth IRA Contribution Worksheet with 2026 figures, single: modified AGI 160,000 is in
    the 153,000-168,000 range: 6 = 168,000 - 160,000 = 8,000; 7 = 15,000; 8 = 0.533; 9 = 7,500 x 0.533 = 3,997.50, raised
    to 4,000; 10 = 4,000. A 4,000 contribution is allowed; 5,000 is an excess (Form 5329), blocking."""
    ok = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=160000)], ira_accounts=[IRAAccount(roth_contributions=4000, fmv=4000, account_type="roth")])
    w = "ws_roth_contribution"
    assert ok.line(w, "4a") == 168000 and ok.line(w, "5a") == 160000 and ok.line(w, "6a") == 8000 and ok.line(w, "7a") == 15000
    assert ok.sheets.facts[w]["8a"] == "0.533" and ok.line(w, "9a") == 4000 and ok.line(w, "10a") == 4000 and not ok.blocking
    over = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=160000)], ira_accounts=[IRAAccount(roth_contributions=5000, fmv=5000, account_type="roth")])
    assert [d.code for d in over.blocking] == ["form_5329_excess_roth_contributions"]


# ------------------------------------------------------------------------------------------------- blocking (Q19)
IRA_DIST = [Retirement(gross_distribution=10000, ira_sep_simple=True, distribution_code="7")]


@pytest.mark.parametrize("kw,code", [
    (dict(retirement=IRA_DIST, ira_accounts=[trad(None, 40000)]), "form_8606_basis_unknown"),
    (dict(retirement=IRA_DIST, ira_accounts=[trad(None, 40000)], prior_year=PriorYear(tax=1000)), "form_8606_basis_unknown"),
    (dict(ira_accounts=[trad(7500, 7500)], w2s=[W2(wages=50000, retirement_plan=True)], ira_facts=[IRAFacts(nondeductible_election=500)]),
     "form_8606_basis_unknown"),
    (dict(retirement=IRA_DIST, prior_year=PriorYear(traditional_ira_basis=3000)), "form_8606_fmv_unknown"),
    (dict(retirement=IRA_DIST, ira_accounts=[trad(4000, 30000)], prior_year=PriorYear(traditional_ira_basis=0), ira_facts=[IRAFacts(nondeductible_election=1000)]),
     "form_8606_late_contributions_unknown"),
    (dict(retirement=[Retirement(gross_distribution=7000, distribution_code="T")], prior_year=PriorYear(roth_ira_basis=0)), "form_8606_roth_five_year_unknown"),
    (dict(retirement=[Retirement(gross_distribution=7000, distribution_code="J")]), "form_8606_roth_basis_unknown"),
    (dict(retirement=[Retirement(gross_distribution=7000, distribution_code="J")], prior_year=PriorYear(roth_ira_basis=1000)), "form_8606_conversion_basis_unknown"),
    (dict(ira_accounts=[trad(7500, 7500, recharacterized_contributions=2000)], prior_year=PriorYear(traditional_ira_basis=0)), "form_8606_recharacterization"),
    (dict(retirement=[Retirement(gross_distribution=1073, ira_sep_simple=True, distribution_code="8")], prior_year=PriorYear(traditional_ira_basis=0)),
     "form_8606_corrective_distribution"),
    (dict(ira_accounts=[trad(9000, 9000)], prior_year=PriorYear(traditional_ira_basis=0)), "form_5329_excess_ira_contributions"),
    (dict(ira_accounts=[trad(7500, 7500)], w2s=[W2(wages=50000)], adjustments=Adjustments(ira_deduction=7500)), "ira_deduction_deprecated_input"),
], ids=["basis-none", "basis-none-other-prior-lines", "nondeductible-without-basis", "fmv-unknown", "late-contributions", "code-T-five-year",
        "roth-basis", "conversion-basis", "recharacterization", "code-8", "excess", "deprecated-ira_deduction"])
def test_unknown_basis_facts_and_scope_limits_block(kw, code):
    kw = {"filing_status": "single", "taxpayer": you(), "w2s": [W2(wages=50000)], **kw}
    r = run(**kw)
    assert code in [d.code for d in r.blocking], [(d.code, d.message) for d in r.diagnostics]


def test_blocked_basis_keeps_the_conservative_full_amount_while_blocked():
    """While the basis is unknown the whole IRA distribution shows on line 4b (the old default), but the return cannot move:
    the diagnostic is an error, not the former warning."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], retirement=IRA_DIST, ira_accounts=[trad(None, 40000)])
    assert r.line("f1040", "4b") == 10000 and [d.code for d in r.blocking] == ["form_8606_basis_unknown"]
    assert not any(d.code == "1099r_taxable_not_determined" for d in r.diagnostics)


def test_married_filing_separately_needs_the_spouses_coverage():
    """IRC §219(g)(1), (3)(B)(iii): living with the spouse, the 0-10,000 phase-out applies when either spouse is covered.
    The taxpayer is not covered; whether the spouse is must be stated. Stated covered, modified AGI 50,000 leaves nothing
    deductible; stated not covered, the full 7,500 is deductible."""
    r = run(filing_status="mfs", taxpayer=you(), spouse=spouse(), w2s=[W2(wages=50000)], ira_accounts=[trad(7500, 7500)], prior_year=PriorYear(traditional_ira_basis=0))
    assert [d.code for d in r.blocking] == ["ira_deduction_spouse_coverage_unknown"]
    covered = run(filing_status="mfs", taxpayer=you(), spouse=spouse(), w2s=[W2(wages=50000)], ira_accounts=[trad(7500, 7500)],
                  prior_year=PriorYear(traditional_ira_basis=0), ira_facts=[IRAFacts(owner="spouse", covered_by_employer_plan=True)])
    assert covered.line("sch_1", "20") == 0 and covered.line(F, "1") == 7500 and not covered.blocking
    free = run(filing_status="mfs", taxpayer=you(), spouse=spouse(), w2s=[W2(wages=50000)], ira_accounts=[trad(7500, 7500)],
               prior_year=PriorYear(traditional_ira_basis=0), ira_facts=[IRAFacts(owner="spouse", covered_by_employer_plan=False)])
    assert free.line("sch_1", "20") == 7500 and not free.blocking


def test_missing_published_figures_block_rather_than_guess(monkeypatch):
    c = ctx()
    original = c.try_param
    monkeypatch.setattr(c, "try_param", lambda rid, on: None if rid == "us_fed.individual.ira_deduction_phaseout" else original(rid, on))
    ret = IndividualReturn(tax_year=2026, filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], ira_accounts=[trad(6000, 6000)],
                           prior_year=PriorYear(traditional_ira_basis=0))
    r = compute_individual(c, ret)
    assert [d.code for d in r.blocking] == ["ira_rules_missing"] and r.line("sch_1", "20") == 0


def test_the_rule_values_for_2026():
    kb = ctx().kb
    lim = kb.resolve("us_fed.individual.ira_contribution_limit", date(2026, 6, 30))
    assert lim.value == {"limit": 7500, "catch_up": 1100} and "Notice 2025-67" in lim.source and lim.url == "https://www.irs.gov/pub/irs-drop/n-25-67.pdf"
    assert kb.resolve("us_fed.individual.ira_deduction_phaseout", date(2026, 6, 30)).value == {
        "single": [81000, 91000], "mfj_covered": [129000, 149000], "mfj_spouse_covered": [242000, 252000], "mfs": [0, 10000]}
    assert kb.resolve("us_fed.individual.roth_ira_phaseout", date(2026, 6, 30)).value == {"mfj": [242000, 252000], "single": [153000, 168000], "mfs": [0, 10000]}
    for rid in ("us_fed.individual.ira_contribution_limit", "us_fed.individual.ira_deduction_phaseout", "us_fed.individual.roth_ira_phaseout"):
        assert kb.try_resolve(rid, date(2027, 1, 1)) is None


@pytest.mark.parametrize("extra,key", [
    ({"ira_facts": [{"owner": "taxpayer", "basis": "100"}]}, "ira_facts[0].basis"),
    ({"prior_year": {"spouse_traditional_ira_basi": "100"}}, "prior_year.spouse_traditional_ira_basi"),
])
def test_unknown_keys_inside_ira_facts_and_the_prior_year_group_are_refused(extra, key):
    with pytest.raises(InputRejected) as e:
        _check_fields({**household(), **extra})
    assert key in str(e.value)


def test_an_ira_distribution_without_basis_facts_never_reaches_review(fam):  # noqa: F811
    """Q19: a 1099-R with the IRA box checked and a Form 5498 are on the return, but no prior-year basis is stated. The
    old silent default (whole amount taxable, a warning) is now a blocking diagnostic; stating a zero basis clears it."""
    add_doc(fam.conn, "d_1099r", "rivera", "1099-R", {"payer_name": "Vanguard", "recipient_tin_last4": "0001", "box1": "10000", "box7": "7",
                                                       "ira_sep_simple": "X"})
    add_doc(fam.conn, "d_5498", "rivera", "5498", {"payer_name": "Vanguard", "recipient_tin_last4": "0001", "box5": "40000", "box7": "IRA"})
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", "issued in error; the payer is sending a corrected 1099", "maya")
    R.confirm(rid, None, "maya")
    res = R.latest(rid)["result"]
    assert "form_8606_basis_unknown" in [d["code"] for d in res["diagnostics"] if d["severity"] == "error"] and res["forms"]["f1040"]["4b"] == "10000"
    _must_not_reach_review(R, rid, "IRA basis unknown", "blocking diagnostic(s)")
    v = R.latest(rid)
    R.save_inputs(rid, {**v["inputs"], "prior_year": {"traditional_ira_basis": "3000"}}, "maya")
    res = R.latest(rid)["result"]
    assert res["forms"]["f8606[taxpayer]"]["15c"] == "9400" and res["forms"]["f1040"]["4b"] == "9400"
    assert res["coverage"]["forms"]["f8606"] == "manual-assisted" and res["coverage"]["below_preparation"] == []
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.carryforwards(rid)["traditional_ira_basis"] == Decimal(2400)
