"""Form 8615, tax for certain children who have unearned income, IRC §1(g) and §59(j) (T1-01, S7).

Expected values are worked by hand from the 2026 draft Form 8615 (May 7, 2026) and its 2026 draft instructions
(June 11, 2026), both read on 2026-10-09 (the 2025 final edition carries the same lines, worksheets and examples; the
2026 drafts already print the 2026 figures): Who Must File and the January 1 birthday chart; the Child's Unearned Income
Worksheet and its example (Amanda Black); the line 2 Examples 1 and 2 (Roger, Eleanor); the line 7 / line 12b example
(Paul and Jane Persimmon's children Sharon, Jerry and Mike); the Line 5 Worksheets #1 and #3; Using the Qualified
Dividends and Capital Gain Tax Worksheet for line 9 tax (the parent's filing status on lines 6, 13, 22 and 24) and for
line 15 tax (the child's); Rev. Proc. 2025-32 §4.02 ($1,350, the §1(g)(4)(A)(ii)(I) amount, twice it $2,700), §4.11
(the §59(j) AMT exemption of a child: earned income plus $9,750), §4.14(2) (dependent standard deduction $1,350 or earned
income plus $450), §4.01 (10% to 12,400 single and 24,800 joint, 12% to 50,400 / 100,800, 22% to 105,700 / 211,400, 24%
to 201,775 / 403,550), §4.03 (0% capital gain rate to 49,450 single and 98,900 joint), §4.10 (AMT exemption 90,100
single, 26% to 244,500); and the Tax Table midpoint convention ($25 rows under $3,000, $50 rows above, the tax at the
midpoint rounded half up). Nothing below was taken from the engine.
"""

import json
from datetime import date

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)
from test_returns_1040 import ctx, run
from test_returns_new_documents import _must_not_reach_review

from agentledger.ledger import store as ledger
from agentledger.returns.facts import InputRejected
from agentledger.returns.individual import compute_individual
from agentledger.returns.model import (CapitalTransaction, Dividends, Form8615Facts, IndividualReturn, Interest, Itemized, ParentFacts,
                                       Person, W2)
from agentledger.returns.store import Returns, _check_fields, carryovers

F = "f8615"
# A single custodial parent: taxable income 60,000, tax 7,918 (Tax Table midpoint 60,025: 5,800 + 22% x 9,625 = 7,917.50).
SINGLE_PARENT = ParentFacts(name="Alex Rivera", ssn="400-00-0001", filing_status="single", taxable_income=60000, tax=7918)
CUSTODIAL = dict(which_parent="custodial_parent")
# The parents' joint return: taxable income 120,000, tax 15,824 (Tax Computation Worksheet: 11,600 + 22% x 19,200).
JOINT_PARENTS = ParentFacts(name="Paul Persimmon", ssn="400-00-0003", filing_status="mfj", taxable_income=120000, tax=15824)


def child(dob=date(2014, 3, 15), **kw):
    """A child claimable as a dependent (standard deduction 1,350 without earned income) with a living parent."""
    kw.setdefault("can_be_claimed_as_dependent", True)
    kw.setdefault("has_living_parent", True)
    return Person(first_name="Casey", last_name="Rivera", ssn="400-00-0100", dob=dob, **kw)


def lines(r, form=F):
    return {k: int(v) for k, v in r.forms[form].items()}


def codes(r):
    return {d.code for d in r.blocking}


# --------------------------------------------------------------------------------------------- the form, worked
def test_interest_only_child_of_a_single_parent():
    """A 12-year-old with 8,000 of interest and nothing else: AGI 8,000, taxable income 6,650 (the 1,350 dependent
    standard deduction). Line 1 = 8,000 (no earned income: adjusted gross income), 2 = 2,700, 3 = 5,300, 4 = 6,650,
    5 = 5,300. The custodial parent files single with taxable income 60,000 and tax 7,918: line 6 = 60,000, 8 = 65,300,
    9 = tax on 65,300 at the parent's rates (Tax Table midpoint 65,325: 5,800 + 22% x 14,925 = 9,083.50 -> 9,084), 10 =
    7,918, 11 = 13 = 1,166 (no other children). Line 14 = 1,350, 15 = tax on 1,350 at the child's rate (midpoint 1,362.50
    x 10% = 136.25 -> 136), 16 = 1,302; 17 = tax on 6,650 (midpoint 6,675 x 10% = 667.50 -> 668); 18 = 1,302 to Form 1040
    line 16 (668 without the form). No AMT: AMTI 8,000 is under the 9,750 limit of §59(j)."""
    r = run(filing_status="single", taxpayer=child(), interest=[Interest(payer="Trust Bank", interest=8000)],
            form_8615=Form8615Facts(parent=SINGLE_PARENT, **CUSTODIAL))
    assert lines(r) == {"1": 8000, "2": 2700, "3": 5300, "4": 6650, "5": 5300, "6": 60000, "7": 0, "8": 65300, "9": 9084, "10": 7918,
                        "11": 1166, "13": 1166, "14": 1350, "15": 136, "16": 1302, "17": 668, "18": 1302}
    assert r.line("f1040", "15") == 6650 and r.line("f1040", "16") == 1302 and r.line("f1040", "18") == 1302
    assert r.sheets.notes["f1040"]["16"] == "Form 8615, line 18" and r.sheets.notes[F]["17"].startswith("Tax Table")
    facts = r.sheets.facts[F]
    assert facts["A"] == "Alex Rivera" and facts["B"] == "400-00-0001" and facts["C"] == "single" and facts["which_parent"] == "custodial_parent"
    assert facts["parent_source"] == {"source": "preparer"} and facts["who_must_file"]["age"] == "under 18 at the end of 2026"
    assert "f6251" not in r.forms and r.line("sch_2", "2") == 0
    assert not r.blocking
    assert {"us_fed.individual.dependent_standard_deduction", "us_fed.individual.kiddie_tax_amt_exemption"} <= {s["rule_id"] for s in r.sources}


def test_roger_line_2_example_1_itemizing_directly_connected_deductions():
    """Instructions, line 2, Example 1: Roger, age 12, has unearned income of 8,000, no other income, no adjustments, and
    itemized deductions of 300 directly connected with his unearned income (he elects to itemize, Schedule A line 19).
    AGI 8,000 goes on line 1; line 2 is 2,700 because that is more than 1,350 plus the 300 (1,650). Taxable income is
    7,700 (8,000 less the 300): line 3 = 5,300, 4 = 7,700, 5 = 5,300. With the single parent of the first case: lines 6-13
    as there (1,166); line 14 = 2,400, 15 = tax on 2,400 (midpoint 2,412.50 x 10% = 241.25 -> 241), 16 = 1,407; 17 = tax
    on 7,700 (midpoint 7,725 x 10% = 772.50 -> 773); 18 = 1,407."""
    r = run(filing_status="single", taxpayer=child(), interest=[Interest(payer="Trust Bank", interest=8000)],
            itemized=Itemized(other=300, force_itemize=True),
            form_8615=Form8615Facts(parent=SINGLE_PARENT, directly_connected_deductions=300, **CUSTODIAL))
    assert r.line("f1040", "12e") == 300 and r.line("f1040", "15") == 7700
    assert lines(r) == {"1": 8000, "2": 2700, "3": 5300, "4": 7700, "5": 5300, "6": 60000, "7": 0, "8": 65300, "9": 9084, "10": 7918,
                        "11": 1166, "13": 1166, "14": 2400, "15": 241, "16": 1407, "17": 773, "18": 1407}
    assert r.sheets.facts[F]["line_2"] == {"itemizes": True, "base_plus_directly_connected": "1650", "threshold": "2700"}
    assert r.line("f1040", "16") == 1407 and not r.blocking


def test_eleanor_line_2_example_2_directly_connected_deductions_over_the_threshold():
    """Instructions, line 2, Example 2: Eleanor, age 8, has unearned income of 16,000 and an early withdrawal penalty of
    100 (Schedule 1 line 18), no other income, and itemized deductions of 1,500 directly connected with the production of
    her unearned income (she itemizes: 1,500 exceeds her 1,350 standard deduction). Her AGI, entered on line 1, is 15,900;
    line 2 is 2,850, the larger of 1,350 + 1,500 and 2,700. Taxable income 14,400: line 3 = 13,050, 4 = 14,400, 5 = 13,050.
    Her parents file jointly with taxable income 120,000 and tax 15,824: line 8 = 133,050, 9 = 11,600 + 22% x 32,250 =
    18,695 (Tax Computation Worksheet, joint), 11 = 13 = 2,871; 14 = 1,350, 15 = 136, 16 = 3,007; 17 = tax on 14,400
    (midpoint 14,425: 1,240 + 12% x 2,025 = 1,483); 18 = 3,007."""
    r = run(filing_status="single", taxpayer=child(date(2018, 5, 2)), interest=[Interest(payer="Trust Bank", interest=16000, early_withdrawal_penalty=100)],
            itemized=Itemized(other=1500), form_8615=Form8615Facts(parent=JOINT_PARENTS, directly_connected_deductions=1500))
    assert r.line("f1040", "11a") == 15900 and r.line("sch_1", "18") == 100 and r.line("f1040", "15") == 14400
    assert lines(r) == {"1": 15900, "2": 2850, "3": 13050, "4": 14400, "5": 13050, "6": 120000, "7": 0, "8": 133050, "9": 18695, "10": 15824,
                        "11": 2871, "13": 2871, "14": 1350, "15": 136, "16": 3007, "17": 1483, "18": 3007}
    assert r.sheets.facts[F]["line_2"]["base_plus_directly_connected"] == "2850" and r.sheets.facts[F]["which_parent"] == "joint_return"
    assert r.sheets.notes[F]["9"].startswith("Tax Computation Worksheet") and not r.blocking


def test_amanda_black_unearned_income_worksheet_and_line_5_worksheet_1():
    """Instructions, line 1, Example (Amanda Black, age 13): dividends 1,300 (qualified, on stock from her grandparents),
    wages 2,100, taxable interest 1,400, tax-exempt interest 100, capital gains 300 and capital losses (200) (long-term
    here, so the 100 net gain is a net capital gain: Schedule D lines 15 and 16 are 100). Her unearned income is 2,800: the
    Child's Unearned Income Worksheet takes total income 4,900 (line 9) less earned income 2,100. The wages are earned
    income and the tax-exempt interest is not included. Standard deduction 2,550 (2,100 + 450), taxable income 2,350:
    line 2 = 2,700, 3 = 100, 4 = 2,350, 5 = 100. Line 5 Worksheet #1 (line 2 is 2,700 and lines 3 and 5 agree): 1 = 1,300,
    2 = 100, 3 = 2,800, 4 = 1,300 / 2,800 = 0.464, 5 = 100 / 2,800 = 0.036, 6 = 2,700 x 0.464 = 1,252.80 -> 1,253, 7 =
    2,700 x 0.036 = 97.20 -> 97, 8 = qualified dividends on line 5 = 1,300 - 1,253 = 47, 9 = net capital gain on line 5 =
    100 - 97 = 3. The single parent (taxable income 60,000, tax 7,918, no dividends or gains): line 8 = 60,100 including
    47 of qualified dividends and 3 of net capital gain, so line 9 is the Qualified Dividends and Capital Gain Tax
    Worksheet at the parent's filing status: line 5 = 60,050, the 50 of dividends and gain above the 49,450 0% breakpoint
    at 15% = 7.50 (line 18), line 22 = tax on 60,050 (midpoint 60,075: 5,800 + 22% x 9,675 = 7,928.50 -> 7,929), line 23
    = 7,936.50, line 24 = tax on 60,100 (midpoint 60,125: 7,939.50 -> 7,940), line 25 = 7,937. Line 10 = 7,918, 11 = 13 =
    19. Line 14 = 2,250 with 1,253 of qualified dividends and 97 of net capital gain (the parts not on line 5): the
    worksheet at the child's status taxes the 900 of other income (midpoint 912.50 x 10% = 91.25 -> 91) and the 1,350 of
    dividends and gain at 0%: line 15 = 91; line 16 = 110. Line 17, the tax on 2,350 the usual way: 950 of other income
    (midpoint 962.50 x 10% = 96.25 -> 96) plus 0% on 1,400 = 96. Line 18 = 110 to Form 1040 line 16."""
    r = run(filing_status="single", taxpayer=child(date(2013, 7, 4)), w2s=[W2(wages=2100)],
            dividends=[Dividends(payer="Fund", ordinary=1300, qualified=1300)], interest=[Interest(payer="Bank", interest=1400, tax_exempt_interest=100)],
            capital_transactions=[CapitalTransaction(description="A", acquired=date(2020, 1, 1), sold=date(2026, 6, 1), proceeds=1300, cost_basis=1000),
                                  CapitalTransaction(description="B", acquired=date(2020, 1, 1), sold=date(2026, 6, 1), proceeds=800, cost_basis=1000)],
            form_8615=Form8615Facts(parent=SINGLE_PARENT, **CUSTODIAL))
    assert r.line("f1040", "9") == 4900 and r.line("f1040", "12e") == 2550 and r.line("f1040", "15") == 2350
    assert lines(r) == {"1": 2800, "2": 2700, "3": 100, "4": 2350, "5": 100, "6": 60000, "7": 0, "8": 60100, "9": 7937, "10": 7918,
                        "11": 19, "13": 19, "14": 2250, "15": 91, "16": 110, "17": 96, "18": 110}
    facts = r.sheets.facts[F]
    assert facts["line_1_worksheet"] == {"name": "Child's Unearned Income Worksheet", "1": "4900", "2": "2100", "3": "2800"}
    assert facts["line_5_worksheet"] == {"worksheet": "#1", "1": "1300", "2": "100", "3": "2800", "4": "0.464", "5": "0.036", "6": "1253", "7": "97",
                                         "8": "47", "9": "3"}
    assert facts["line_8"] == {"qualified_dividends": "47", "net_capital_gain": "3", "rate_28_gain": "0", "unrecaptured_1250_gain": "0"}
    w9 = facts["line_9_worksheet"]
    assert w9["method"].startswith("Qualified Dividends") and (w9["lines"]["5"], w9["lines"]["18"], w9["lines"]["22"], w9["lines"]["24"]) == ("60050", "8", "7929", "7940")
    w15 = facts["line_15_worksheet"]
    assert (w15["qualified_dividends"], w15["net_capital_gain"], w15["lines"]["5"], w15["lines"]["22"]) == ("1253", "97", "900", "91")
    assert facts["who_must_file"]["earned_income"] == "2100" and r.line("f1040", "16") == 110 and not r.blocking


def test_persimmon_children_share_the_tentative_tax_on_lines_7_12a_12b_and_13():
    """Instructions, line 7 and lines 12a-12b, Example: Sharon's Form 8615 shows 3,000 on line 5 and the 6,000 of her
    brothers Jerry (2,800) and Mike (3,200) on line 7, line 12a = 9,000 and line 12b = 3,000 / 9,000 = 0.333. Sharon, age
    14, has 5,700 of interest (taxable income 4,350; line 1 = 5,700, 3 = 5 = 3,000). Paul and Jane file jointly with
    taxable income 150,000 and tax 22,424 (11,600 + 22% x 49,200): line 8 = 159,000, 9 = 11,600 + 22% x 58,200 = 24,404,
    11 = 1,980, 13 = 1,980 x 0.333 = 659.34 -> 659. Line 14 = 1,350, 15 = 136, 16 = 795; 17 = tax on 4,350 (midpoint
    4,375 x 10% = 437.50 -> 438); 18 = 795."""
    parents = ParentFacts(name="Paul Persimmon", ssn="400-00-0003", filing_status="mfj", taxable_income=150000, tax=22424)
    r = run(filing_status="single", taxpayer=child(date(2012, 2, 1)), interest=[Interest(payer="Bank", interest=5700)],
            form_8615=Form8615Facts(parent=parents, other_children_net_unearned_income=6000))
    assert lines(r) == {"1": 5700, "2": 2700, "3": 3000, "4": 4350, "5": 3000, "6": 150000, "7": 6000, "8": 159000, "9": 24404, "10": 22424,
                        "11": 1980, "12a": 9000, "13": 659, "14": 1350, "15": 136, "16": 795, "17": 438, "18": 795}
    assert r.sheets.facts[F]["12b"] == "0.333" and r.line("f1040", "16") == 795 and not r.blocking


def test_line_5_worksheet_3_when_taxable_income_limits_line_5():
    """A child with 5,000 of qualified dividends and 3,000 of itemized deductions none of which is directly connected
    (Schedule A line 17z; she itemizes, 3,000 over the 1,350 standard deduction): AGI 5,000, taxable income 2,000. Line 1 =
    5,000, 2 = 2,700 (1,350 + 0 is less), 3 = 2,300, 4 = 2,000, 5 = 2,000: line 5 is less than line 3, so Line 5 Worksheet
    #3 applies: 1 = 5,000, 2 = 0, 3 = 5,000, 4 = 1.000, 5 = 0 (no deductions connected with the dividends), 6-8 = 0, 9 =
    5,000, 10 = 0, 11 = 3,000 (the itemized deductions not directly connected), 12 = 3,000, 13 = 5,000 (AGI), 14 =
    1.000, 15 = 3,000, 16 = 3,000, 17 = 0, 18 = qualified dividends on line 5 = 5,000 - 3,000 = 2,000 (not more than line
    5), 19 = 0. The parents (joint, taxable income 300,000, tax 57,196 = 35,932 + 24% x 88,600, no dividends): line 8 =
    302,000 with 2,000 of qualified dividends; the worksheet at the joint status: tax on 300,000 = 57,196 plus 15% x 2,000 =
    300 (all above the 98,900 breakpoint) = 57,496, under the 57,676 on 302,000: line 9 = 57,496, 10 = 57,196, 11 = 13 =
    300. Lines 4 and 5 are the same: 14 = 15 = 0, 16 = 300. Line 17, the tax on 2,000 of taxable income made up entirely of
    qualified dividends, is 0 (0% rate); line 18 = 300."""
    parents = ParentFacts(name="P", ssn="400-00-0003", filing_status="mfj", taxable_income=300000, tax=57196)
    r = run(filing_status="single", taxpayer=child(date(2016, 1, 1)), dividends=[Dividends(payer="Fund", ordinary=5000, qualified=5000)],
            itemized=Itemized(other=3000), form_8615=Form8615Facts(parent=parents, directly_connected_deductions=0))
    assert r.line("f1040", "15") == 2000
    assert lines(r) == {"1": 5000, "2": 2700, "3": 2300, "4": 2000, "5": 2000, "6": 300000, "7": 0, "8": 302000, "9": 57496, "10": 57196,
                        "11": 300, "13": 300, "14": 0, "15": 0, "16": 300, "17": 0, "18": 300}
    ws = r.sheets.facts[F]["line_5_worksheet"]
    assert ws["worksheet"] == "#3" and (ws["4"], ws["11"], ws["14"], ws["15"], ws["18"], ws["19"]) == ("1.000", "3000", "1.000", "3000", "2000", "0")
    assert r.sheets.facts[F]["line_8"]["qualified_dividends"] == "2000" and r.line("f1040", "16") == 300 and not r.blocking


def test_schedule_d_tax_worksheet_variant_when_the_parent_has_unrecaptured_1250_gain():
    """Using the Schedule D Tax Worksheet for line 9 tax: the parents (joint) have taxable income 300,000 including a
    50,000 net capital gain of which 20,000 is unrecaptured section 1250 gain (Schedule D line 19), no qualified
    dividends; the child has 8,000 of interest (line 5 = 5,300, as in the first case). The parents' own tax: Schedule D
    Tax Worksheet line 13 = 30,000 (the gain other than the §1250 part), 14 = 21 = 270,000, the 30,000 at 15% (line 31 =
    4,500), the §1250 gain at the ordinary rate (line 39 = 0: 24% is below the 25% maximum), line 44 = tax on 270,000 =
    35,932 + 24% x 58,600 = 49,996, line 45 = 54,496 under line 46 = 57,196: 54,496 on line 10. Line 8 = 305,300 including
    the parents' 50,000 of gain and 20,000 of §1250 gain (the Worksheet 2 for Line 11 total): lines 9 = 50,000, 11 =
    20,000, 13 = 30,000, 14 = 21 = 275,300, 25 = 30 = 30,000, 31 = 4,500, 35 = 20,000 (the smaller of line 9 and the total
    of every Schedule D line 19), 36 = 325,300, 38 = 20,000, 39 = 0, 44 = tax on 275,300 = 35,932 + 24% x 63,900 = 51,268,
    45 = 55,768 under 46 = 58,468: line 9 = 55,768 (the 5,300 at the parents' 24%). Line 11 = 13 = 1,272, 14 = 1,350, 15 =
    136, 16 = 1,408; 17 = 668; 18 = 1,408."""
    parents = ParentFacts(name="P", ssn="400-00-0003", filing_status="mfj", taxable_income=300000, tax=54496, net_capital_gain=50000,
                          unrecaptured_1250_gain=20000)
    r = run(filing_status="single", taxpayer=child(), interest=[Interest(payer="Trust Bank", interest=8000)], form_8615=Form8615Facts(parent=parents))
    assert lines(r) == {"1": 8000, "2": 2700, "3": 5300, "4": 6650, "5": 5300, "6": 300000, "7": 0, "8": 305300, "9": 55768, "10": 54496,
                        "11": 1272, "13": 1272, "14": 1350, "15": 136, "16": 1408, "17": 668, "18": 1408}
    w9 = r.sheets.facts[F]["line_9_worksheet"]
    assert w9["method"] == "Schedule D Tax Worksheet"
    assert {k: w9["lines"][k] for k in ("9", "11", "13", "21", "31", "35", "38", "39", "44", "45", "46", "47")} == {
        "9": "50000", "11": "20000", "13": "30000", "21": "275300", "31": "4500", "35": "20000", "38": "20000", "39": "0", "44": "51268",
        "45": "55768", "46": "58468", "47": "55768"}
    assert r.sheets.facts[F]["line_8"] == {"qualified_dividends": "0", "net_capital_gain": "50000", "rate_28_gain": "0", "unrecaptured_1250_gain": "20000"}
    assert r.line("f1040", "16") == 1408 and not r.blocking


def test_the_childs_28_percent_rate_gain_is_shared_between_lines_5_and_14():
    """A child with a 10,000 long-term collectibles gain (Schedule D lines 15, 16 and 18 are 10,000) and nothing else:
    AGI 10,000, taxable income 8,650; line 1 = 10,000, 3 = 5 = 7,300. Line 5 Worksheet #1: 1 = 0, 2 = 10,000, 3 = 10,000,
    4 = 0.000, 5 = 1.000, 7 = 2,700, 9 = net capital gain on line 5 = 7,300. Worksheet 1 for Line 11: 7,300 / 10,000 =
    0.730 of the 10,000 of 28% rate gain, 7,300, is on line 5. The single parent (taxable income 60,000, tax 7,918): line
    8 = 67,300 including 7,300 of net capital gain, all 28% rate gain, so line 9 is the Schedule D Tax Worksheet at the
    parent's status: lines 9-12 = 7,300, 13 = 0, 14 = 21 = 67,300, 41 = 67,300, 42 = 43 = 0 (the gain is taxed at the
    parent's 22% ordinary rate, below the 28% maximum), 44 = 47 = tax on 67,300 (midpoint 67,325: 5,800 + 22% x 16,925 =
    9,523.50 -> 9,524): line 9 = 9,524, 11 = 13 = 1,606. Line 14 = 1,350 with the other 2,700 of gain (all 28% rate gain):
    the worksheet at the child's status, lines 1 and 16 both 1,350, gives the tax on 1,350 = 136 on line 15; 16 = 1,742.
    Line 17 is the child's own Schedule D Tax Worksheet on 8,650: 868 (midpoint 8,675 x 10%). Line 18 = 1,742."""
    coins = CapitalTransaction(description="Coins", acquired=date(2019, 1, 1), sold=date(2026, 6, 1), proceeds=12000, cost_basis=2000, collectible=True)
    r = run(filing_status="single", taxpayer=child(), capital_transactions=[coins], form_8615=Form8615Facts(parent=SINGLE_PARENT, **CUSTODIAL))
    assert r.line("sch_d", "18") == 10000 and r.line("f1040", "15") == 8650
    assert lines(r) == {"1": 10000, "2": 2700, "3": 7300, "4": 8650, "5": 7300, "6": 60000, "7": 0, "8": 67300, "9": 9524, "10": 7918,
                        "11": 1606, "13": 1606, "14": 1350, "15": 136, "16": 1742, "17": 868, "18": 1742}
    facts = r.sheets.facts[F]
    assert facts["line_5_worksheet"] == {"worksheet": "#1", "1": "0", "2": "10000", "3": "10000", "4": "0.000", "5": "1.000", "6": "0", "7": "2700",
                                         "8": "0", "9": "7300"}
    assert facts["line_11_worksheets"] == {"share_of_child_net_capital_gain_on_line_5": "0.730", "rate_28_on_line_5": "7300", "unrecaptured_1250_on_line_5": "0"}
    assert facts["line_8"]["rate_28_gain"] == "7300" and facts["line_9_worksheet"]["method"] == "Schedule D Tax Worksheet"
    assert (facts["line_9_worksheet"]["lines"]["41"], facts["line_9_worksheet"]["lines"]["43"], facts["line_9_worksheet"]["lines"]["44"]) == ("67300", "0", "9524")
    assert facts["line_15_worksheet"]["method"] == "Schedule D Tax Worksheet" and facts["line_15_worksheet"]["net_capital_gain"] == "2700"
    assert r.line("f1040", "16") == 1742 and not r.blocking


def test_the_parents_filing_separately_use_the_separate_rates_once_the_basis_is_stated():
    """Parents married filing separately: the return of the parent with the greater taxable income is used (Which Parent's
    Return To Use); the basis is a stated fact recorded on the form, and line 9 uses the separate-return brackets (the same
    as single below 384,350): the first case's 1,302 again."""
    parent = ParentFacts(name="Alex Rivera", ssn="400-00-0001", filing_status="mfs", taxable_income=60000, tax=7918)
    r = run(filing_status="single", taxpayer=child(), interest=[Interest(payer="Trust Bank", interest=8000)],
            form_8615=Form8615Facts(parent=parent, which_parent="greater_taxable_income"))
    assert lines(r)["18"] == 1302 and r.sheets.facts[F]["which_parent"] == "greater_taxable_income" and r.sheets.facts[F]["C"] == "mfs"
    assert not r.blocking


# --------------------------------------------------------------------------------------------- AMT, §59(j)
def test_the_amt_exemption_of_a_child_is_earned_income_plus_9750():
    """A 10-year-old with 30,000 of interest; the custodial parent is single with taxable income 30,000 and tax 3,355
    (midpoint 30,025: 1,240 + 12% x 17,625). Child: taxable income 28,650; line 1 = 30,000, 3 = 5 = 27,300, 8 = 57,300,
    9 = tax on 57,300 (midpoint 57,325: 5,800 + 22% x 6,925 = 7,323.50 -> 7,324), 11 = 13 = 3,969, 14 = 1,350, 15 = 136,
    16 = 4,105, 17 = tax on 28,650 (midpoint 28,675: 1,240 + 12% x 16,275 = 3,193), 18 = 4,105. Form 6251: line 1a =
    1,350 (Form 1040 line 14), 1b = 28,650, 2a = 1,350 (the standard deduction added back), AMTI line 4 = 30,000; the
    exemption may not exceed earned income 0 plus 9,750 (Rev. Proc. 2025-32 §4.11), so line 5 = 9,750 instead of 90,100,
    line 6 = 20,250, line 7 = 26% = 5,265, line 10 = 4,105 (the Form 8615 tax), line 11 = 1,160 of AMT; Form 1040 line 18
    = 5,265."""
    parent = ParentFacts(name="Alex Rivera", ssn="400-00-0001", filing_status="single", taxable_income=30000, tax=3355)
    r = run(filing_status="single", taxpayer=child(date(2016, 1, 1)), interest=[Interest(payer="Bank", interest=30000)],
            form_8615=Form8615Facts(parent=parent, **CUSTODIAL))
    assert lines(r) == {"1": 30000, "2": 2700, "3": 27300, "4": 28650, "5": 27300, "6": 30000, "7": 0, "8": 57300, "9": 7324, "10": 3355,
                        "11": 3969, "13": 3969, "14": 1350, "15": 136, "16": 4105, "17": 3193, "18": 4105}
    assert {k: int(v) for k, v in r.forms["f6251"].items() if k in ("1a", "1b", "2a", "4", "5", "6", "7", "10", "11")} == {
        "1a": 1350, "1b": 28650, "2a": 1350, "4": 30000, "5": 9750, "6": 20250, "7": 5265, "10": 4105, "11": 1160}
    assert r.sheets.facts["f6251"]["exemption_59j"] == {"earned_income": "0", "addition": "9750", "limit": "9750", "unlimited_exemption": "90100"}
    assert "59(j)" in r.sheets.notes["f6251"]["5"] and r.line("sch_2", "2") == 1160 and r.line("f1040", "18") == 5265 and not r.blocking


def test_the_amt_limit_applies_to_a_child_without_form_8615_and_asks_only_when_it_matters():
    """§59(j) limits the exemption of every child to whom §1(g) applies, Form 8615 or not: a 16-year-old with 20,000 of
    wages, no unearned income and a 30,000 ISO bargain element (Form 6251 line 2i). Taxable income 3,900 (standard
    deduction 16,100: 20,450 capped), tax 393 (midpoint 3,925 x 10%); AMTI = 3,900 + 16,100 + 30,000 = 50,000; the
    exemption is earned income 20,000 + 9,750 = 29,750, line 6 = 20,250, line 7 = 5,265, AMT = 4,872. Whether a parent is
    alive is asked only because the limit would create an AMT (26% of 50,000 - 29,750 exceeds the 393 of regular tax);
    with no ISO adjustment AMTI 20,000 is under the limit and nothing is asked."""
    teen = Person(first_name="Casey", last_name="Rivera", ssn="400-00-0100", dob=date(2010, 6, 1), can_be_claimed_as_dependent=True)
    r = run(filing_status="single", taxpayer=teen.model_copy(update={"has_living_parent": True}), w2s=[W2(wages=20000)], amt_adjustments={"iso": 30000})
    assert F not in r.forms and r.line("f1040", "16") == 393
    assert {k: int(v) for k, v in r.forms["f6251"].items() if k in ("4", "5", "6", "7", "10", "11")} == {"4": 50000, "5": 29750, "6": 20250, "7": 5265,
                                                                                                           "10": 393, "11": 4872}
    assert r.sheets.facts["f6251"]["exemption_59j"]["earned_income"] == "20000" and not r.blocking
    asked = run(filing_status="single", taxpayer=teen, w2s=[W2(wages=20000)], amt_adjustments={"iso": 30000})
    assert codes(asked) == {"form_8615_living_parent_unknown"} and asked.line("sch_2", "2") == 0
    [d] = asked.blocking
    assert (d.form, d.line) == ("f6251", "5") and "59(j)" in d.message
    orphan = run(filing_status="single", taxpayer=teen.model_copy(update={"has_living_parent": False}), w2s=[W2(wages=20000)], amt_adjustments={"iso": 30000})
    assert "f6251" not in orphan.forms and orphan.line("sch_2", "2") == 0 and not orphan.blocking   # the 90,100 exemption covers AMTI 50,000
    quiet = run(filing_status="single", taxpayer=teen, w2s=[W2(wages=20000)])
    assert not quiet.diagnostics and "f6251" not in quiet.forms


def test_a_missing_59j_figure_blocks_rather_than_guessing(monkeypatch):
    c = ctx()
    original = c.try_param
    monkeypatch.setattr(c, "try_param", lambda rid, on: None if rid == "us_fed.individual.kiddie_tax_amt_exemption" else original(rid, on))
    parent = ParentFacts(name="Alex Rivera", ssn="400-00-0001", filing_status="single", taxable_income=30000, tax=3355)
    ret = IndividualReturn(tax_year=2026, filing_status="single", taxpayer=child(date(2016, 1, 1)), interest=[Interest(payer="Bank", interest=30000)],
                           form_8615=Form8615Facts(parent=parent, **CUSTODIAL))
    r = compute_individual(c, ret)
    assert codes(r) == {"kiddie_tax_amt_exemption_rule_missing"} and r.line(F, "18") == 4105   # the Form 8615 tax itself stands
    assert "f6251" not in r.forms and r.line("sch_2", "2") == 0                                 # the unlimited 90,100 exemption: no AMT, and the return blocks


def test_the_rule_carries_its_citation_and_ends_with_2026():
    kb = ctx().kb
    rule = kb.resolve("us_fed.individual.kiddie_tax_amt_exemption", date(2026, 12, 31))
    assert rule.value == 9750 and "Rev. Proc. 2025-32 §4.11" in rule.source and rule.url == "https://www.irs.gov/pub/irs-drop/rp-25-32.pdf"
    assert kb.try_resolve("us_fed.individual.kiddie_tax_amt_exemption", date(2027, 1, 1)) is None
    assert "59(j)" in kb.get("us_fed.individual.kiddie_tax_amt_exemption").citation
    dsd = kb.resolve("us_fed.individual.dependent_standard_deduction", date(2026, 12, 31))
    assert dsd.value["minimum"] == 1350 and "§4.02" in dsd.source and "1(g)(4)(A)(ii)(I)" in kb.get("us_fed.individual.dependent_standard_deduction").citation


# --------------------------------------------------------------------------------------------- Who Must File
FIVE_K = [Interest(payer="Bank", interest=5000)]
SUBJECT_LINE_18 = 642    # 5,000 of interest: line 5 = 2,300, line 9 = tax on 62,300 (midpoint 62,325: 5,800 + 22% x 11,925 = 8,423.50 -> 8,424),
#                          line 11 = 506, line 15 = 136, line 16 = 642; line 17 = tax on 3,650 (midpoint 3,675 x 10% = 367.50 -> 368)
NORMAL_LINE_16 = 368


def eligibility(dob, **kw):
    return run(filing_status="single", taxpayer=child(dob, **kw), interest=FIVE_K, form_8615=Form8615Facts(parent=SINGLE_PARENT, **CUSTODIAL))


@pytest.mark.parametrize("dob,kw,outcome", [
    (date(2009, 1, 2), {}, "subject"),                                                          # under 18: no support question
    (date(2009, 1, 1), {}, "form_8615_support_unknown"),                                        # the chart: born January 1, 2009 is 18
    (date(2009, 1, 1), dict(support_from_earned_income_over_half=False), "subject"),
    (date(2009, 1, 1), dict(support_from_earned_income_over_half=True), "not subject"),
    (date(2008, 1, 1), {}, "form_8615_student_status_unknown"),                                 # the chart: born January 1, 2008 is 19
    (date(2006, 6, 1), dict(full_time_student=False), "not subject"),
    (date(2006, 6, 1), dict(full_time_student=True), "form_8615_support_unknown"),
    (date(2006, 6, 1), dict(full_time_student=True, support_from_earned_income_over_half=False), "subject"),
    (date(2006, 6, 1), dict(full_time_student=True, support_from_earned_income_over_half=True), "not subject"),
    (date(2003, 1, 1), dict(full_time_student=True, support_from_earned_income_over_half=False), "not subject"),   # the chart: 24
    (date(2014, 3, 15), dict(has_living_parent=None), "form_8615_living_parent_unknown"),
    (date(2014, 3, 15), dict(has_living_parent=False), "not subject"),
    (None, {}, "form_8615_age_unknown"),
], ids=["under-18", "jan-1-2009-is-18", "18-support-not-over-half", "18-support-over-half", "jan-1-2008-is-19", "20-not-a-student",
        "20-student-support-unknown", "20-student", "20-student-self-supporting", "jan-1-2003-is-24", "living-parent-unknown",
        "no-living-parent", "dob-unknown"])
def test_who_must_file_conditions_3_to_5(dob, kw, outcome):
    """Who Must File, conditions 3-5 and the January 1 birthday chart. A subject child pays 642 on Form 8615; one not
    subject pays the normal 368 and the reason is recorded; an unknown fact blocks, with the normal tax meanwhile."""
    r = eligibility(dob, **kw)
    if outcome == "subject":
        assert lines(r)["18"] == SUBJECT_LINE_18 and r.line("f1040", "16") == SUBJECT_LINE_18 and not r.blocking
    elif outcome == "not subject":
        assert F not in r.forms and r.line("f1040", "16") == NORMAL_LINE_16 and not r.blocking
        assert "condition" in r.sheets.facts["f1040"]["form_8615_not_required"]
    else:
        assert codes(r) == {outcome} and F not in r.forms and r.line("f1040", "16") == NORMAL_LINE_16


def test_a_joint_return_is_never_subject():
    young_spouse = Person(first_name="Sam", last_name="Rivera", ssn="400-00-0002", dob=date(2008, 2, 1))
    r = run(filing_status="mfj", taxpayer=child(date(2008, 6, 1), can_be_claimed_as_dependent=False), spouse=young_spouse,
            interest=FIVE_K, form_8615=Form8615Facts(parent=SINGLE_PARENT, **CUSTODIAL))
    assert F not in r.forms and "joint return" in r.sheets.facts["f1040"]["form_8615_not_required"] and not r.blocking


def test_unearned_income_within_the_threshold_asks_nothing():
    """Condition 1: 2,700 of interest is not more than the threshold, so no Form 8615 and no question about the child."""
    r = run(filing_status="single", taxpayer=child(date(2009, 1, 1), has_living_parent=None), interest=[Interest(payer="Bank", interest=2700)])
    assert F not in r.forms and not r.diagnostics and r.line("f1040", "16") == 136           # taxable income 1,350, midpoint 1,362.50 x 10% = 136.25


def test_directly_connected_deductions_can_stop_the_form_at_line_3():
    """Condition 1 is met (4,000 of unearned income) but an itemizer's directly connected deductions lift line 2 above
    line 1: line 3 is zero or less, the form is attached and the tax is figured in the normal manner."""
    r = run(filing_status="single", taxpayer=child(), interest=[Interest(payer="Bank", interest=4000)], itemized=Itemized(other=3000),
            form_8615=Form8615Facts(parent=SINGLE_PARENT, directly_connected_deductions=3000, **CUSTODIAL))
    assert lines(r) == {"1": 4000, "2": 4350, "3": -350} and "line 3" in r.sheets.facts[F]["stop"]
    assert r.line("f1040", "16") == 101 and not r.blocking                                    # taxable income 1,000, midpoint 1,012.50 x 10% = 101.25


def test_a_schedule_c_profit_is_earned_income_with_a_review_warning():
    """Instructions, Earned income: a sole proprietor's net profit is earned income under the general rule (Schedule 1
    line 3), the 30% allowance where capital is a material income-producing factor being the preparer's call. 4,000 of
    profit and 5,000 of interest: line 1 = 9,000 - 4,000 (the worksheet; self-employment tax halves are not deducted on
    the worksheet) = 5,000; the warning flags the treatment."""
    from agentledger.returns.model import Business

    r = run(filing_status="single", taxpayer=child(), interest=FIVE_K, businesses=[Business(name="Lawns", gross_receipts=4000)],
            form_8615=Form8615Facts(parent=SINGLE_PARENT, **CUSTODIAL))
    assert lines(r)["1"] == 5000 and r.sheets.facts[F]["who_must_file"]["earned_income"] == "4000"
    assert [d.code for d in r.diagnostics if d.severity == "warning"] == ["form_8615_business_income_as_earned"] and not r.blocking


# --------------------------------------------------------------------------------------------- scope limits block (Q19)
@pytest.mark.parametrize("kw,code", [
    (dict(form_8615=Form8615Facts()), "form_8615_parent_facts_unknown"),
    (dict(form_8615=Form8615Facts(parent=ParentFacts(name="Alex Rivera", filing_status="single", taxable_income=60000), **CUSTODIAL)),
     "form_8615_parent_facts_unknown"),
    (dict(form_8615=Form8615Facts(parent=SINGLE_PARENT)), "form_8615_parent_filing_status_unsupported"),
    (dict(form_8615=Form8615Facts(parent=SINGLE_PARENT, which_parent="joint_return")), "form_8615_parent_filing_status_unsupported"),
    (dict(form_8615=Form8615Facts(parent=SINGLE_PARENT, form_8814_election=True, **CUSTODIAL)), "form_8814_election"),
    (dict(form_8615=Form8615Facts(parent=SINGLE_PARENT, parent_return_id="r_parent", **CUSTODIAL)), "form_8615_parent_facts_conflict"),
    (dict(form_8615=Form8615Facts(parent_return_id="r_parent", **CUSTODIAL)), "form_8615_parent_return_unresolved"),
    (dict(form_8615=Form8615Facts(parent=SINGLE_PARENT, other_children_net_unearned_income=1000, other_children_qualified_dividends=1200, **CUSTODIAL)),
     "form_8615_facts_inconsistent"),
    (dict(form_8615=Form8615Facts(parent=SINGLE_PARENT, **CUSTODIAL), itemized=Itemized(other=1500)), "form_8615_directly_connected_unknown"),
    (dict(form_8615=Form8615Facts(parent=SINGLE_PARENT, directly_connected_deductions=2000, **CUSTODIAL), itemized=Itemized(other=1500)),
     "form_8615_facts_inconsistent"),
    (dict(form_8615=Form8615Facts(parent=ParentFacts(name="P", filing_status="mfj", taxable_income=60000, tax=7918, net_capital_gain=100,
                                                     unrecaptured_1250_gain=500))), "form_8615_facts_inconsistent"),
], ids=["no-parent", "parent-tax-missing", "single-parent-basis-unknown", "single-parent-basis-joint", "form-8814", "transcribed-and-linked",
        "linked-outside-the-store", "other-children", "directly-connected-unknown", "directly-connected-over-17z", "parent-gains"])
def test_scope_limits_block_with_stable_codes(kw, code):
    """Each limit is an error diagnostic; no Form 8615 is produced and the tax stays the normal one while it stands."""
    r = run(filing_status="single", taxpayer=child(), interest=FIVE_K, **kw)
    assert code in codes(r), [(d.code, d.message) for d in r.blocking]
    assert F not in r.forms and r.sheets.notes["f1040"]["16"] != "Form 8615, line 18"
    if "itemized" not in kw:
        assert r.line("f1040", "16") == NORMAL_LINE_16


def test_the_store_accepts_the_new_inputs_and_refuses_unknown_ones():
    inputs = {**household(), "taxpayer": {**household()["taxpayer"], "support_from_earned_income_over_half": False, "has_living_parent": True},
              "form_8615": {"which_parent": "custodial_parent", "other_children_net_unearned_income": "2800",
                            "parent": {"name": "Alex Rivera", "ssn": "400-00-0001", "filing_status": "single", "taxable_income": "60000", "tax": "7918"}}}
    _check_fields(inputs)
    assert carryovers(inputs) == []                                                            # nothing here is a carryover
    with pytest.raises(InputRejected) as e:
        _check_fields({**household(), "form_8615": {"parent": {"agi": "60000"}}})
    assert "form_8615.parent.agi" in str(e.value)


# --------------------------------------------------------------------------------------------- the store: review gates and a linked parent
CASEY = {"tax_year": 2026, "filing_status": "single",
         "taxpayer": {"first_name": "Casey", "last_name": "Rivera", "ssn": "400-00-0100", "dob": "2014-03-15", "can_be_claimed_as_dependent": True,
                      "has_living_parent": True}}


def _child_client(fam, doc_id="d_int_c"):  # noqa: F811
    ledger.add_client(fam.conn, id="casey", name="Casey Rivera", kind="individual", emails=[], tax_id_last4="0100", domain="general",
                      facts={"taxpayer_ssn_last4": "0100", "taxpayer_name": "Casey Rivera"})
    add_doc(fam.conn, doc_id, "casey", "1099-INT", {"payer_name": "Trust Bank", "recipient_tin_last4": "0100", "box1": "8000"})


def test_review_is_refused_until_the_parents_return_is_entered(fam):  # noqa: F811
    """Q19: a child's 1099-INT of 8,000 (over the 2,700 threshold) blocks review by form_8615_parent_facts_unknown; once the
    preparer transcribes the parent's lines (asserted with the preparer as their source) the 1,302 of tax is figured and
    the return reaches review with Form 8615 manual-assisted."""
    _child_client(fam)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("casey", 2026, "maya", CASEY)
    R.populate_from_documents(rid, "maya")
    R.confirm(rid, None, "maya")
    v = R.latest(rid)
    assert v["inputs"]["interest"][0]["interest"] == "8000" and v["result"]["forms"]["f1040"]["16"] == "668"
    assert "form_8615_parent_facts_unknown" in {d["code"] for d in v["result"]["diagnostics"] if d["severity"] == "error"}
    _must_not_reach_review(R, rid, "the parent's return is not entered", "blocking diagnostic(s)")
    stated = json.loads(json.dumps(v["inputs"]))
    stated["form_8615"] = {"which_parent": "custodial_parent",
                           "parent": {"name": "Alex Rivera", "ssn": "400-00-0001", "filing_status": "single", "taxable_income": "60000", "tax": "7918"}}
    R.save_inputs(rid, stated, "maya")
    v = R.latest(rid)
    assert v["result"]["forms"]["f8615"]["18"] == "1302" and v["result"]["forms"]["f1040"]["16"] == "1302"
    assert not any(d["severity"] == "error" for d in v["result"]["diagnostics"])
    assert v["result"]["coverage"]["forms"]["f8615"] == "manual-assisted" and v["result"]["coverage"]["below_preparation"] == []
    row = fam.conn.execute("SELECT source, asserted_by FROM fact_assertions WHERE return_id = ? AND path = 'form_8615.parent.taxable_income' "
                           "ORDER BY id DESC", (rid,)).fetchone()
    assert (row["source"], row["asserted_by"]) == ("preparer", "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"


def test_the_parents_return_of_the_firm_supplies_lines_a_to_c_6_and_10(fam):  # noqa: F811
    """The Riveras' joint return (W-2s of 52,000 and 41,000, 312.40 of interest: AGI 93,312, taxable income 61,112, tax
    6,839 (midpoint 61,125: 2,480 + 12% x 36,325)) is linked by its id: line 6 = 61,112, line 10 = 6,839, line 8 = 66,412,
    line 9 = tax at the joint rates (midpoint 66,425: 2,480 + 12% x 41,625 = 7,475), 11 = 13 = 636, 14 = 1,350, 15 = 136,
    16 = 772 against 17 = 668: line 18 = 772. The form records which return and version it relied on; the parents' return
    is still in preparation, so the child's return notes that it may change."""
    R = Returns(fam.conn, fam.kb)
    prid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(prid, "maya")
    pv = R.latest(prid)
    assert (pv["result"]["forms"]["f1040"]["15"], pv["result"]["forms"]["f1040"]["16"]) == ("61112", "6839")
    _child_client(fam)
    rid = R.create("casey", 2026, "maya", {**CASEY, "form_8615": {"parent_return_id": prid}})
    R.populate_from_documents(rid, "maya")
    res = R.latest(rid)["result"]
    assert {k: int(v) for k, v in res["forms"]["f8615"].items()} == {
        "1": 8000, "2": 2700, "3": 5300, "4": 6650, "5": 5300, "6": 61112, "7": 0, "8": 66412, "9": 7475, "10": 6839, "11": 636, "13": 636,
        "14": 1350, "15": 136, "16": 772, "17": 668, "18": 772}
    facts = res["facts"]["f8615"]
    assert facts["parent_source"] == {"source": "return", "return_id": prid, "version": pv["version"], "status": "preparing"}
    assert (facts["A"], facts["B"], facts["C"], facts["which_parent"]) == ("Alex Rivera", "400-00-0001", "mfj", "joint_return")
    assert [d["code"] for d in res["diagnostics"] if d["code"].startswith("form_8615")] == ["form_8615_parent_return_linked"]
    assert not any(d["severity"] == "error" for d in res["diagnostics"])
    # A parent return that cannot be used blocks: another year, an unknown id, the child's own return.
    other_year = R.create("rivera", 2025, "maya", {**household(), "tax_year": 2025})
    for pid, why in ((other_year, "2025 return"), ("r_nobody", "no return with that id"), (rid, "own return")):
        edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
        edited["form_8615"] = {"parent_return_id": pid}
        R.save_inputs(rid, edited, "maya")
        [d] = [d for d in R.latest(rid)["result"]["diagnostics"] if d["severity"] == "error"]
        assert d["code"] == "form_8615_parent_return_unresolved", (pid, d)
        full = [x for x in Returns(fam.conn, fam.kb)._calculate(rid, R.latest(rid))[1].diagnostics if x.code == d["code"]][0]
        assert why in full.message
