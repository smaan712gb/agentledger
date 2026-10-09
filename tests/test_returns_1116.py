"""Form 1116, foreign tax credit, IRC §901 and §904 (T1-01, S3).

Expected values are worked by hand from Form 1116 (2025) and its instructions, read on 2026-10-09 (the 2026 edition is
not posted; nothing on the form is indexed): Part I lines 1a-7 (line 3a the standard deduction, line 3b the Schedule 1
Part II adjustments other than interest, line 3f "round off the result to at least four decimal places", line 4a the
$5,000 rule for interest expense), Part III lines 9-24 (line 18 Form 1040 line 11 less line 14 plus the Schedule 1-A
senior deduction; line 20 Form 1040 line 16 plus Schedule 2 line 1z), Part IV, line 10 and Schedule B ("apply it to the
earliest year to which it may be carried"), the adjustment exception (Qualified Dividends and Capital Gain Tax
Worksheet line 5 within the 24% bracket and foreign qualified dividends under $20,000) and the election to claim the
credit without Form 1116 (IRC §904(j)); Form 6251 (2025) instructions, line 8 (the AMT Form 1116 under the IRC
§59(a)(3) simplified limitation election: line 17 as for the regular tax, line 18 Form 6251 line 4, line 20 Form 6251
line 7); Pub. 514 (2025), Carryback and Carryover, Example 1 (the carryback comes first); and the 2026 figures of
Rev. Proc. 2025-32 (standard deduction 16,100 single and 32,200 joint; 10% to 12,400 / 24,800, 12% to 50,400 / 100,800,
22% to 105,700 / 211,400, 24% to 201,775 / 403,550; AMT exemption 90,100 phased out from 500,000 at 50%, 28% above
244,500; 0% capital gain rate to 98,900 joint) and the Tax Table midpoint convention. Nothing below was taken from the
engine.
"""

from datetime import date
from decimal import Decimal

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)
from test_returns_1040 import ctx, run, spouse, you

from agentledger.returns.facts import InputRejected
from agentledger.returns.model import (Dividends, ForeignTaxCarryover, ForeignTaxCredit, Interest, Itemized, K1, PriorYear, W2)
from agentledger.returns.store import Returns, _check_fields, carryovers
from agentledger.workflow.engine import TransitionError

F = "f1116[passive]"
ROOM_0 = PriorYear(ftc_excess_limitation={"passive": 0})                 # 2025 had no excess limitation: nothing carries back
GERMAN_BANK = dict(payer="Hanse Bank", interest=10000, foreign_tax_paid=1500, foreign_source_income=10000, foreign_country="Germany")


def bank(**kw):
    return Interest(**{**GERMAN_BANK, **kw})


def lines(r, form=F):
    return {k: int(v) for k, v in r.forms[form].items()}


def codes(r):
    return {d.code for d in r.blocking}


# --------------------------------------------------------------------------------------------- the limitation, worked
def test_single_filer_foreign_interest_limited_by_the_fraction():
    """Wages 60,000; a foreign bank's 1099-INT: 10,000 of interest, all foreign source, 1,500 of foreign tax (passive).
    Part I: 1a 10,000; 3a 16,100 (standard deduction); 3d 10,000; 3e 70,000 (gross income from all sources); 3f
    10,000 / 70,000 = 0.1429; 3g 16,100 x 0.1429 = 2,300.69 -> 2,301; 6 = 2,301; 7 = 7,699. Part III: 8 = 9 = 11 = 14 =
    1,500; 15 = 17 = 7,699; 18 = 53,900 (70,000 - 16,100); 19 = 7,699 / 53,900 = 0.1428; 20 = tax on 53,900 (Tax Table
    midpoint 53,925: 5,800 + 22% x 3,525 = 6,575.50 -> 6,576); 21 = 6,576 x 0.1428 = 939.05 -> 939; 24 = smaller of 1,500
    and 939 = 939. Part IV: 27 = 32 = 33 = 35 = 939 to Schedule 3 line 1; Form 1040 line 22 = 5,637. The 561 of unused tax
    carries to 2027 (IRC §904(c)) because 2025 had no excess limitation to absorb a carryback."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], interest=[bank()], prior_year=ROOM_0)
    assert lines(r) == {"1a": 10000, "2": 0, "3a": 16100, "3b": 0, "3c": 16100, "3d": 10000, "3e": 70000, "3g": 2301, "4a": 0, "4b": 0,
                        "5": 0, "6": 2301, "7": 7699, "8": 1500, "9": 1500, "10": 0, "11": 1500, "12": 0, "13": 0, "14": 1500,
                        "15": 7699, "16": 0, "17": 7699, "18": 53900, "20": 6576, "21": 939, "22": 0, "23": 939, "24": 939,
                        "27": 939, "32": 939, "33": 939, "34": 0, "35": 939}
    facts = r.sheets.facts[F]
    assert facts["3f"] == "0.1429" and facts["19"] == "0.1428" and facts["category"] == "passive" and facts["paid_or_accrued"] == "paid"
    assert facts["columns"] == {"Germany": {"gross_income": "10000", "taxes_dividends": "0", "taxes_interest": "1500", "taxes_other": "0",
                                            "taxes_total": "1500"}}
    assert facts["schedule_b"] == [{"from_year": 2026, "generated": "561", "carryback": "0", "carryover_out": "561"}]
    assert r.line("sch_3", "1") == 939 and r.line("f1040", "20") == 939 and r.line("f1040", "22") == 5637
    assert r.carryforwards["ftc_carryover_passive_2026"] == 561 and "ftc_carryover_passive_2025" not in r.carryforwards
    assert not r.blocking
    used = {s["rule_id"] for s in r.sources}
    assert {"us_fed.individual.ftc_carryover_years", "us_fed.individual.foreign_tax_credit_simplified_limit", "us_fed.individual.tax_rates"} <= used


def test_joint_return_qualified_dividends_within_the_adjustment_exception():
    """Joint, wages 150,000; a fund's 1099-DIV: 20,000 of dividends, all qualified, 12,000 of it foreign source, 1,800 of
    foreign tax, country RIC. Taxable income 137,800; the Qualified Dividends and Capital Gain Tax Worksheet: line 5 =
    117,800 (ordinary income), tax = 11,600 + 22% x 17,000 = 15,340 on it plus 15% x 20,000 = 3,000 on the dividends
    (all above the 98,900 0% breakpoint) = 18,340. Adjustment exception: line 5 117,800 is not over 403,550 and foreign
    qualified dividends (at most 12,000) are under 20,000, so lines 1a and 18 are not adjusted. Form 1116: 1a 12,000;
    3a 32,200; 3f = 12,000 / 170,000 = 0.0706; 3g = 2,273.32 -> 2,273; 7 = 17 = 9,727; 18 = 137,800; 19 = 0.0706;
    20 = 18,340; 21 = 1,294.80 -> 1,295; 24 = 1,295 (the 505 excess carries to 2027). Form 6251 line 7 (5,548) is not over
    line 10 (17,045), so no AMT credit is claimed and the AMT carryover is only noted."""
    r = run(filing_status="mfj", taxpayer=you(), spouse=spouse(), w2s=[W2(wages=150000)],
            dividends=[Dividends(payer="Global Fund", ordinary=20000, qualified=20000, foreign_tax_paid=1800, foreign_source_income=12000,
                                 foreign_country="RIC")], prior_year=ROOM_0)
    assert r.line("f1040", "15") == 137800 and r.line("f1040", "16") == 18340
    got = lines(r)
    assert {k: got[k] for k in ("1a", "3a", "3d", "3e", "3g", "7", "14", "17", "18", "20", "21", "24", "35")} == {
        "1a": 12000, "3a": 32200, "3d": 12000, "3e": 170000, "3g": 2273, "7": 9727, "14": 1800, "17": 9727, "18": 137800, "20": 18340,
        "21": 1295, "24": 1295, "35": 1295}
    facts = r.sheets.facts[F]
    assert facts["3f"] == "0.0706" and facts["19"] == "0.0706"
    assert facts["adjustment_exception"] == {"qdcg_worksheet_line_5": "117800", "not_over": "403550", "foreign_qualified_dividends_at_most": "12000",
                                             "under": "20000", "elected_by_not_adjusting": True}
    assert r.line("sch_3", "1") == 1295 and r.carryforwards["ftc_carryover_passive_2026"] == 505
    assert r.line("f6251", "7") == 5548 and r.line("f6251", "8") == 0 and r.line("sch_2", "2") == 0
    assert not r.blocking and [d.code for d in r.diagnostics if d.severity == "warning"] == ["form_1116_amt_carryover_not_tracked"]


def test_interest_expense_stays_us_source_within_the_five_thousand_rule():
    """Wages 60,000, 1,000 of student loan interest (Schedule 1 line 21, deductible in full: MAGI 64,000 is under 85,000),
    a foreign bank's 4,000 of interest with 600 of foreign tax. Interest is line 4b, not 3b (instructions, line 4b lists
    deductible student loan interest), and gross foreign-source income of 4,000 is within the $5,000 rule of line 4a, so
    all of it is allocated to U.S. income: 3b = 1,000 - 1,000 = 0; 3f = 4,000 / 64,000 = 0.0625; 3g = 1,006.25 -> 1,006;
    7 = 2,994; 18 = 46,900 (63,000 - 16,100); 19 = 2,994 / 46,900 = 0.0638; 20 = tax on 46,900 (midpoint 46,925: 1,240 +
    12% x 34,525 = 5,383); 21 = 343.43 -> 343; 24 = 343; 257 carries to 2027."""
    from agentledger.returns.model import Adjustments

    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], adjustments=Adjustments(student_loan_interest_paid=1000),
            interest=[bank(interest=4000, foreign_tax_paid=600, foreign_source_income=4000)], prior_year=ROOM_0)
    assert r.line("sch_1", "21") == 1000 and r.line("f1040", "11a") == 63000 and r.line("f1040", "16") == 5383
    got = lines(r)
    assert {k: got[k] for k in ("1a", "3a", "3b", "3e", "3g", "4a", "4b", "7", "18", "20", "21", "24")} == {
        "1a": 4000, "3a": 16100, "3b": 0, "3e": 64000, "3g": 1006, "4a": 0, "4b": 0, "7": 2994, "18": 46900, "20": 5383, "21": 343, "24": 343}
    assert r.sheets.facts[F]["3f"] == "0.0625" and r.sheets.facts[F]["19"] == "0.0638"
    assert r.sheets.facts[F]["interest_expense_us_source"]["amount"] == "1000"
    assert r.carryforwards["ftc_carryover_passive_2026"] == 257 and not r.blocking


def test_senior_deduction_is_added_back_on_line_18():
    """Form 1116 (2025) instructions, line 18: Form 1040 line 11 less line 14 plus the Schedule 1-A senior deduction, which
    is also kept out of line 3b. Single, born 1958 (over 65), pension 60,000 and the foreign bank: AGI 70,000; the 6,000
    senior deduction (MAGI 70,000 is under 75,000); taxable income 70,000 - 16,100 - 2,050 (the additional standard
    deduction for age 65) - 6,000 = 45,850; line 18 = 45,850 + 6,000 = 51,850; 3a = 18,150 (the standard deduction incl.
    the age-65 addition); 3f = 0.1429; 3g = 2,593.63 -> 2,594; 7 = 7,406; 19 = 7,406 / 51,850 = 0.1428."""
    from agentledger.returns.model import Person, Retirement

    senior = Person(first_name="Alex", last_name="Rivera", ssn="400-00-0001", dob=date(1958, 6, 1))
    r = run(filing_status="single", taxpayer=senior, retirement=[Retirement(gross_distribution=60000, taxable_amount=60000)],
            interest=[bank()], prior_year=ROOM_0)
    assert r.line("sch_1a", "43") == 6000 and r.line("f1040", "12e") == 18150 and r.line("f1040", "15") == 45850
    got = lines(r)
    assert got["3a"] == 18150 and got["3b"] == 0 and got["3g"] == 2594 and got["7"] == 7406 and got["18"] == 51850
    assert r.sheets.facts[F]["19"] == "0.1428" and not r.blocking


# --------------------------------------------------------------------------------------------- carryovers, §904(c)
def test_carryovers_are_used_after_this_years_taxes_earliest_year_first():
    """The limitation of the first case (939) with 500 of foreign tax this year and carryovers of 300 from 2016 (its
    tenth and last year) and 400 from 2024. Line 10 = 700, 11 = 14 = 1,200, 24 = 939. This year's 500 is credited first;
    the 439 of room then absorbs the 2016 carryover (300) and 139 of 2024's; 261 of 2024 carries to 2027 (Form 1116
    instructions, line 10: "apply it to the earliest year to which it may be carried"; Pub. 514, Carryback and
    Carryover). Nothing of this year's tax is unused, so no carryback question arises."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], interest=[bank(foreign_tax_paid=500)],
            prior_year=PriorYear(ftc_carryovers=[ForeignTaxCarryover(category="passive", from_year=2024, carryover=400),
                                                 ForeignTaxCarryover(category="passive", from_year=2016, carryover=300)]))
    got = lines(r)
    assert (got["8"], got["10"], got["11"], got["14"], got["21"], got["24"], got["35"]) == (500, 700, 1200, 1200, 939, 939, 939)
    assert r.sheets.facts[F]["schedule_b"] == [
        {"from_year": 2016, "carryover_in": "300", "used": "300", "expired": "0", "carryover_out": "0"},
        {"from_year": 2024, "carryover_in": "400", "used": "139", "expired": "0", "carryover_out": "261"},
        {"from_year": 2026, "generated": "0", "carryback": "0", "carryover_out": "0"}]
    assert {k: v for k, v in r.carryforwards.items() if k.startswith("ftc")} == {"ftc_carryover_passive_2024": Decimal(261)}
    assert not r.blocking


def test_the_tenth_year_remainder_expires():
    """As above with 600 from 2016: 439 is used, the 161 left expires after 2026 (IRC §904(c): ten succeeding years) and
    the 400 of 2024 carries on in full."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], interest=[bank(foreign_tax_paid=500)],
            prior_year=PriorYear(ftc_carryovers=[ForeignTaxCarryover(category="passive", from_year=2016, carryover=600),
                                                 ForeignTaxCarryover(category="passive", from_year=2024, carryover=400)]))
    assert r.sheets.facts[F]["schedule_b"][:2] == [
        {"from_year": 2016, "carryover_in": "600", "used": "439", "expired": "161", "carryover_out": "0"},
        {"from_year": 2024, "carryover_in": "400", "used": "0", "expired": "0", "carryover_out": "400"}]
    assert r.carryforwards["ftc_carryover_passive_2024"] == 400 and "ftc_carryover_passive_2016" not in r.carryforwards


def test_unused_tax_goes_back_to_the_prior_year_first():
    """Pub. 514 Example 1: the unused tax of the year is carried back to the preceding year up to that year's excess
    limitation, and only the rest carries forward. First case (561 unused): with a 2025 excess limitation of 100, 100 is
    deemed paid in 2025 and 461 carries to 2027; the carryback is a refund claim on an amended 2025 return, out of scope,
    so it blocks. Unknown 2025 room blocks too, and records no 2026 carryover. A carryover from 2025 on the return shows
    2025 had unused tax, so its room is zero without being stated."""
    back = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], interest=[bank()],
               prior_year=PriorYear(ftc_excess_limitation={"passive": 100}))
    [d] = back.blocking
    assert d.code == "form_1116_carryback" and "100" in d.message and "amended 2025" in d.message
    assert back.sheets.facts[F]["schedule_b"][-1] == {"from_year": 2026, "generated": "561", "carryback": "100", "carryover_out": "461"}
    assert back.carryforwards["ftc_carryover_passive_2026"] == 461 and back.line("sch_3", "1") == 939
    unknown = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], interest=[bank()])
    assert codes(unknown) == {"form_1116_carryback_unknown"} and "ftc_carryover_passive_2026" not in unknown.carryforwards
    assert unknown.sheets.facts[F]["schedule_b"][-1]["carryover_out"] == "unknown" and unknown.line("sch_3", "1") == 939
    inferred = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], interest=[bank()],
                   prior_year=PriorYear(ftc_carryovers=[ForeignTaxCarryover(category="passive", from_year=2025, carryover=400)]))
    assert not inferred.blocking
    assert {k: v for k, v in inferred.carryforwards.items() if k.startswith("ftc")} == {"ftc_carryover_passive_2025": Decimal(400),
                                                                                         "ftc_carryover_passive_2026": Decimal(561)}


@pytest.mark.parametrize("entry,code", [
    (ForeignTaxCarryover(category="passive", carryover=100), "form_1116_carryover_year_unknown"),
    (ForeignTaxCarryover(category="passive", from_year=2027, carryover=100), "form_1116_carryover_year_invalid"),
    (ForeignTaxCarryover(category="passive", from_year=2015, carryover=100), "form_1116_carryover_year_invalid"),
    (ForeignTaxCarryover(category="general", from_year=2024, carryover=100), "form_1116_general_category"),
], ids=["no-year", "carryback-into-this-year", "expired", "general"])
def test_carryovers_that_cannot_be_placed_block(entry, code):
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], interest=[bank()], prior_year=PriorYear(ftc_carryovers=[entry]))
    assert code in codes(r) and F not in r.forms and r.line("sch_3", "1") == 0


# --------------------------------------------------------------------------------------------- AMT, §59(a)
AMT_RETURN = dict(filing_status="single", w2s=[W2(wages=200000)], amt_adjustments={"iso": 300000}, interest=[bank()])


def test_amt_foreign_tax_credit_under_the_simplified_limitation_election():
    """Wages 200,000, a 300,000 ISO bargain element and the foreign bank. Regular tax: taxable income 193,900, tax 39,134
    (schedule); Form 1116: 3f = 10,000 / 210,000 = 0.0476; 3g = 766; 7 = 17 = 9,234; 19 = 9,234 / 193,900 = 0.0476;
    21 = 1,862.78 -> 1,863; 24 = 1,500 (all of the tax). Form 6251: AMTI (line 4) = 193,900 + 16,100 + 300,000 = 510,000;
    exemption 90,100 - 50% x 10,000 = 85,100; line 6 = 424,900; line 7 = 28% x 424,900 - 2% x 244,500 = 114,082. AMT
    Form 1116 under the election: 17 = 9,234; 18 = 510,000; 19 = 0.0181; 20 = 114,082; 21 = 2,064.88 -> 2,065; 24 = 33 =
    1,500 = Form 6251 line 8; line 9 = 112,582; line 10 = 39,134 - 1,500 = 37,634; line 11 (AMT) = 74,948. The election
    is made with this return and recorded as binding."""
    r = run(taxpayer=you(), foreign_tax_credit=ForeignTaxCredit(amt_simplified_limitation=True), **AMT_RETURN)
    assert lines(r)["24"] == 1500 and r.line("sch_3", "1") == 1500 and r.line("f1040", "16") == 39134
    assert {k: int(v) for k, v in r.forms["f6251"].items() if k in ("4", "5", "6", "7", "8", "9", "10", "11")} == {
        "4": 510000, "5": 85100, "6": 424900, "7": 114082, "8": 1500, "9": 112582, "10": 37634, "11": 74948}
    assert r.sheets.facts[F]["amt"] == {"14": "1500", "17": "9234", "18": "510000", "19": "0.0181", "20": "114082", "21": "2065", "24": "1500",
                                        "33": "1500", "claimed": True}
    assert r.sheets.facts["f6251"]["simplified_limitation_election"]["first_year"] == 2026
    assert any(d.code == "form_1116_amt_election_recorded" for d in r.diagnostics) and not r.blocking
    earlier = run(taxpayer=you(), prior_year=PriorYear(amt_ftc_simplified_election=True), **AMT_RETURN)
    assert earlier.line("f6251", "8") == 1500 and earlier.sheets.facts["f6251"]["simplified_limitation_election"]["first_year"] == "earlier"
    assert not earlier.blocking and not any(d.code == "form_1116_amt_election_recorded" for d in earlier.diagnostics)


@pytest.mark.parametrize("kw,code", [
    (dict(), "form_1116_amt_election_unknown"),
    (dict(foreign_tax_credit=ForeignTaxCredit(amt_simplified_limitation=False)), "form_1116_amt_election_not_made"),
    (dict(foreign_tax_credit=ForeignTaxCredit(amt_simplified_limitation=False), prior_year=PriorYear(amt_ftc_simplified_election=True)),
     "form_1116_amt_election_revoked"),
], ids=["unknown", "not-made", "revoked"])
def test_the_amt_credit_is_never_figured_without_the_election(kw, code):
    """IRC §59(a)(3)(B): made for the first year an AMT credit is claimed, binding afterwards. Line 8 stays 0 (the AMT
    is 76,448 instead of 74,948) and the return blocks."""
    r = run(taxpayer=you(), **AMT_RETURN, **kw)
    assert codes(r) == {code} and r.line("f6251", "8") == 0 and r.line("f6251", "11") == 76448 and r.line("sch_3", "1") == 1500


def test_the_amt_credit_has_its_own_carryovers():
    """A 2024 carryover of 200 (regular) and 300 (AMT). Regular: 14 = 1,700, 21 = 1,863, 24 = 1,700 (this year's 1,500
    first, then the 200). AMT: 14 = 1,800, 21 = 2,065, 33 = 1,800 = Form 6251 line 8; line 9 = 112,282; line 10 =
    39,134 - 1,700 = 37,434; line 11 = 74,848. Without the AMT carryover stated the credit is not figured."""
    r = run(taxpayer=you(), foreign_tax_credit=ForeignTaxCredit(amt_simplified_limitation=True),
            prior_year=PriorYear(ftc_carryovers=[ForeignTaxCarryover(category="passive", from_year=2024, carryover=200, amt_carryover=300)]), **AMT_RETURN)
    assert (lines(r)["10"], lines(r)["14"], lines(r)["24"]) == (200, 1700, 1700) and r.line("sch_3", "1") == 1700
    assert r.sheets.facts[F]["amt"]["14"] == "1800" and r.line("f6251", "8") == 1800 and r.line("f6251", "11") == 74848
    assert r.sheets.facts[F]["schedule_b_amt"][0] == {"from_year": 2024, "carryover_in": "300", "used": "300", "expired": "0", "carryover_out": "0"}
    assert not r.blocking and not any(k.startswith("ftc") for k in r.carryforwards)
    missing = run(taxpayer=you(), foreign_tax_credit=ForeignTaxCredit(amt_simplified_limitation=True),
                  prior_year=PriorYear(ftc_carryovers=[ForeignTaxCarryover(category="passive", from_year=2024, carryover=200)]), **AMT_RETURN)
    assert codes(missing) == {"form_1116_amt_carryover_unknown"} and missing.line("f6251", "8") == 0


def test_without_an_amt_the_amt_carryover_is_kept_when_its_facts_are_stated():
    """First case: Form 6251 line 7 is 0, so no AMT credit is claimed; the AMT limitation of 0 leaves the whole 1,500 as an
    AMT carryover to 2027 (IRC §59(a)(1)) once the election and the 2025 AMT room are stated. Unstated, it is a warning,
    not a block: this year's tax does not depend on it."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], interest=[bank()],
            foreign_tax_credit=ForeignTaxCredit(amt_simplified_limitation=True),
            prior_year=PriorYear(ftc_excess_limitation={"passive": 0}, ftc_amt_excess_limitation={"passive": 0}))
    assert r.sheets.facts[F]["amt"]["33"] == "0" and r.sheets.facts[F]["amt"]["claimed"] is False
    assert r.carryforwards["ftc_amt_carryover_passive_2026"] == 1500 and r.carryforwards["ftc_carryover_passive_2026"] == 561
    assert not r.blocking and not any(d.severity == "warning" for d in r.diagnostics)
    untracked = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], interest=[bank()], prior_year=ROOM_0)
    [w] = [d for d in untracked.diagnostics if d.severity == "warning"]
    assert w.code == "form_1116_amt_carryover_not_tracked" and "amt_simplified_limitation" in w.message
    assert not any(k.startswith("ftc_amt") for k in untracked.carryforwards) and not untracked.blocking


# --------------------------------------------------------------------------------------------- §904(j), the de minimis election
def test_de_minimis_foreign_tax_is_credited_without_the_form_and_recorded():
    """85 of foreign tax on a 1099-DIV (passive, a payee statement) within the 300 limit of IRC §904(j): the credit is 85
    with no Form 1116 and no foreign-source income needed; the election is recorded on Schedule 3 and noted."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], dividends=[Dividends(ordinary=2400, qualified=2000, foreign_tax_paid=85)])
    assert r.line("sch_3", "1") == 85 and F not in r.forms and not r.blocking
    assert r.sheets.facts["sch_3"]["foreign_tax_credit"] == {"election": "IRC §904(j)", "stated": False, "form_1116": False, "foreign_taxes": "85",
                                                             "not_creditable": "0"}
    [d] = [d for d in r.diagnostics if d.code == "foreign_tax_credit_904j"]
    assert d.severity == "info" and "no foreign tax is carried" in d.message.lower()


def test_declining_the_de_minimis_election_files_the_form():
    """The same 85 with the election declined: Form 1116 on 2,400 of foreign-source dividends. Gross income 52,400; 3f =
    2,400 / 52,400 = 0.0458; 3g = 737.38 -> 737; 7 = 1,663; 18 = 36,300; 19 = 0.0458; 20 = tax 3,871 (taxable income
    36,300 with 2,000 of qualified dividends at 0%: 34,300 of ordinary income, midpoint 34,325: 1,240 + 12% x 21,925 =
    3,871); 21 = 3,871 x 0.0458 = 177.29 -> 177; 24 = smaller of 85 and 177 = 85. The adjustment exception applies (line 5
    of the worksheet 34,300; foreign qualified dividends at most 2,000)."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)],
            dividends=[Dividends(payer="Fund", ordinary=2400, qualified=2000, foreign_tax_paid=85, foreign_source_income=2400, foreign_country="RIC")],
            foreign_tax_credit=ForeignTaxCredit(de_minimis_election=False))
    got = lines(r)
    assert (got["1a"], got["3e"], got["3g"], got["7"], got["18"], got["20"], got["21"], got["24"]) == (2400, 52400, 737, 1663, 36300, 3871, 177, 85)
    assert r.sheets.facts[F]["de_minimis_election"] is False and r.line("sch_3", "1") == 85 and not r.blocking


def test_the_de_minimis_election_is_asked_when_the_choice_matters():
    """With a carryover from 2024 the election would leave it unused this year, and with tax below the foreign tax the
    excess could not be carried forward (IRC §904(j)(1)(B)): both block until the election is stated. Stated true, the
    carryover passes through unaffected and the excess over the tax is simply not creditable."""
    div = Dividends(ordinary=2400, qualified=2000, foreign_tax_paid=85)
    carry = PriorYear(ftc_carryovers=[ForeignTaxCarryover(category="passive", from_year=2024, carryover=100, amt_carryover=100)])
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], dividends=[div], prior_year=carry)
    assert codes(r) == {"form_1116_de_minimis_election_unknown"} and r.line("sch_3", "1") == 0
    elected = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], dividends=[div], prior_year=carry,
                  foreign_tax_credit=ForeignTaxCredit(de_minimis_election=True))
    assert elected.line("sch_3", "1") == 85 and not elected.blocking
    assert {k: v for k, v in elected.carryforwards.items() if k.startswith("ftc")} == {"ftc_carryover_passive_2024": Decimal(100),
                                                                                        "ftc_amt_carryover_passive_2024": Decimal(100)}
    low = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=5000)], dividends=[div])
    assert codes(low) == {"form_1116_de_minimis_election_unknown"}
    low_elected = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=5000)], dividends=[div], foreign_tax_credit=ForeignTaxCredit(de_minimis_election=True))
    assert low_elected.line("sch_3", "1") == 0 and not low_elected.blocking
    assert low_elected.sheets.facts["sch_3"]["foreign_tax_credit"]["not_creditable"] == "85"


# --------------------------------------------------------------------------------------------- scope limits block (Q19)
def four_banks():
    return [bank(payer=c, interest=1000, foreign_tax_paid=200, foreign_source_income=1000, foreign_country=c) for c in ("Germany", "France", "Japan", "Spain")]


@pytest.mark.parametrize("kw,code", [
    (dict(dividends=[Dividends(ordinary=20000, qualified=20000, foreign_tax_paid=900)]), "form_1116_foreign_source_income_unknown"),
    (dict(dividends=[Dividends(ordinary=20000, foreign_tax_paid=900, foreign_source_income=20000)]), "form_1116_country_unknown"),
    (dict(interest=[bank(foreign_source_income=12000)]), "form_1116_foreign_source_exceeds_income"),
    (dict(interest=[bank(category="general")]), "form_1116_general_category"),
    (dict(interest=[bank(category="foreign_branch")]), "form_1116_category_unsupported"),
    (dict(interest=[bank(foreign_tax_paid=4000)]), "form_1116_high_tax_kickout"),
    (dict(k1s=[K1(entity_name="Fund LP", passive=True, ordinary_dividends=5000, foreign_tax_paid=700, foreign_source_income=5000, foreign_country="Various")]),
     "form_1116_category_unknown"),
    (dict(k1s=[K1(entity_name="Ops LP", ordinary_income=10000, ordinary_dividends=2000, foreign_tax_paid=400, foreign_source_income=2000,
                  foreign_country="Various", category="passive")]), "form_1116_gross_income_unknown"),
    (dict(interest=four_banks()), "form_1116_countries_exceed_columns"),
    (dict(interest=[bank(), bank(payer="Other", accrued=True)]), "form_1116_accrued_mixed"),
    (dict(interest=[bank()], itemized=Itemized(state_local_income_tax=12000, real_estate_tax=8000)), "form_1116_deductions_unsupported"),
    (dict(interest=[bank()], itemized=Itemized(state_local_income_tax=12000, real_estate_tax=8000, mortgage_interest_1098=9000)), "form_1116_interest_expense"),
    (dict(dividends=[Dividends(payer="Fund", ordinary=25000, qualified=25000, foreign_tax_paid=3750, foreign_source_income=25000, foreign_country="RIC")]),
     "form_1116_qualified_dividend_adjustment"),
], ids=["income-unknown", "country-unknown", "income-over-box", "general", "other-category", "high-tax-kickout", "k1-category-unknown",
        "k1-gross-unknown", "four-countries", "paid-and-accrued", "state-income-tax-itemizer", "interest-expense-over-5000",
        "qualified-dividends-20000"])
def test_scope_limits_block_with_stable_codes(kw, code):
    """Each limit of the slice is an error diagnostic; no Form 1116 and no credit are produced while one stands."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], **kw)
    assert code in codes(r), [(d.code, d.message) for d in r.blocking]
    assert F not in r.forms and r.line("sch_3", "1") == 0


def test_the_adjustment_exception_fails_above_the_24_percent_bracket():
    """Single, wages 250,000 and 5,000 of foreign qualified dividends: line 5 of the worksheet (233,900) is over 201,775."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=250000)],
            dividends=[Dividends(payer="Fund", ordinary=5000, qualified=5000, foreign_tax_paid=750, foreign_source_income=5000, foreign_country="RIC")])
    [d] = [d for d in r.blocking if d.code == "form_1116_qualified_dividend_adjustment"]
    assert "233900" in d.message and "201775" in d.message


def test_two_countries_fill_two_columns():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], prior_year=ROOM_0,
            interest=[bank(), bank(payer="Banque", interest=2000, foreign_tax_paid=300, foreign_source_income=2000, foreign_country="France")])
    assert set(r.sheets.facts[F]["columns"]) == {"Germany", "France"} and lines(r)["1a"] == 12000 and lines(r)["8"] == 1800 and not r.blocking


def test_review_is_refused_while_the_foreign_source_income_is_unknown(fam):  # noqa: F811
    """Q19: a 1099-DIV with 900 of foreign tax (over the de minimis amount) and no supplemental statement facts blocks
    review by its diagnostic; once the preparer states the foreign-source income and country, the credit (900, within
    the 1,631 limitation) is figured and the return reaches review."""
    add_doc(fam.conn, "d_div", "rivera", "1099-DIV", {"payer_name": "Global Fund", "recipient_tin_last4": "0001", "box1a": "20000", "box7": "900",
                                                      "box8": "Germany"})
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", "issued in error; the payer is sending a corrected 1099", "maya")
    R.confirm(rid, None, "maya")
    v = R.latest(rid)
    assert v["inputs"]["dividends"][0]["foreign_country"] == "Germany"                   # 1099-DIV box 8, with its provenance
    prov = v["provenance"]["dividends[0].foreign_country"]
    assert (prov["document_id"], prov["box"], prov["value"], prov["confirmed"]) == ("d_div", "box8", "Germany", True)
    assert "form_1116_foreign_source_income_unknown" in {d["code"] for d in v["result"]["diagnostics"]}
    with pytest.raises(TransitionError, match="blocking diagnostic"):
        R.submit_for_review(rid, "maya")
    assert R.status(rid).status == "preparing"
    inputs = R.latest(rid)["inputs"]
    inputs["dividends"][0]["foreign_source_income"] = "20000"
    R.save_inputs(rid, inputs, "maya")
    v = R.latest(rid)
    assert v["result"]["forms"]["f1116[passive]"]["24"] == "900" and v["result"]["forms"]["sch_3"]["1"] == "900"
    assert not any(d["severity"] == "error" for d in v["result"]["diagnostics"])
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert fam.conn.execute("SELECT detail FROM return_retention_facts WHERE return_id = ? AND kind = 'foreign_tax' ORDER BY version DESC",
                            (rid,)).fetchone()[0] == "dividends[0],schedule 3 line 1"


def test_documents_populate_the_country_boxes(fam):  # noqa: F811
    add_doc(fam.conn, "d_int2", "rivera", "1099-INT", {"payer_name": "Hanse Bank", "recipient_tin_last4": "0001", "box1": "400", "box6": "60", "box7": "Germany"})
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    v = R.latest(rid)
    [it] = [i for i in v["inputs"]["interest"] if i.get("source_document") == "d_int2"]
    assert it["foreign_tax_paid"] == "60" and it["foreign_country"] == "Germany"
    assert v["provenance"]["interest[1].foreign_country"] == {"document_id": "d_int2", "box": "box7", "value": "Germany", "confirmed": False}
    assert v["result"]["forms"]["sch_3"]["1"] == "60"                                     # within §904(j): credited without the form


def test_the_store_accepts_the_new_inputs_and_records_both_carryovers():
    inputs = {**household(), "foreign_tax_credit": {"de_minimis_election": False, "amt_simplified_limitation": True},
              "prior_year": {"ftc_carryovers": [{"category": "passive", "from_year": 2024, "carryover": "400", "amt_carryover": "300"}],
                             "ftc_excess_limitation": {"passive": "0"}, "ftc_amt_excess_limitation": {"passive": "0"},
                             "amt_ftc_simplified_election": True}}
    _check_fields(inputs)
    assert carryovers(inputs) == ["prior_year.ftc_carryovers[0].carryover", "prior_year.ftc_carryovers[0].amt_carryover"]
    with pytest.raises(InputRejected) as e:
        _check_fields({**household(), "foreign_tax_credit": {"elect": True}})
    assert "foreign_tax_credit.elect" in str(e.value)


def test_the_rules_carry_their_citations():
    kb = ctx().kb
    years = kb.resolve("us_fed.individual.ftc_carryover_years", date(2026, 12, 31))
    assert years.value == {"back": 1, "forward": 10} and "904(c)" in kb.get("us_fed.individual.ftc_carryover_years").citation
    assert kb.try_resolve("us_fed.individual.ftc_carryover_years", date(2004, 12, 31)) is None       # 2 back / 5 forward then: not encoded
    assert kb.resolve("us_fed.individual.ftc_qualified_dividend_adjustment_exception", date(2026, 12, 31)).value == 20000
    assert kb.resolve("us_fed.individual.ftc_interest_expense_de_minimis", date(2026, 12, 31)).value == 5000
    assert kb.resolve("us_fed.individual.foreign_tax_credit_simplified_limit", date(2026, 12, 31)).value == {"other": 300, "mfj": 600}
