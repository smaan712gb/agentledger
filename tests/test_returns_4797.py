"""Form 4797, Sales of Business Property: IRC §1231(a)-(c), §1245(a), §1250(a)-(b), §1(h)(6) (T1-01, S4).

Expected values are worked by hand from Form 4797 (2025) and its instructions, read on 2026-10-09 (the 2026 form is not
posted; nothing on it is indexed): Part I lines 2-9 and the line 8 example (net section 1231 losses of 4,000 and 6,000 in
2020 and 2021, gains of 3,000 and 2,000 in 2024 and 2025: 7,000 on line 8, the whole 2,000 ordinary, 5,000 of 2021 left),
Part II lines 10-18b, Part III lines 19-32 (line 25b the smaller of line 24 or 25a; line 26b the applicable percentage of
the smaller of line 24 or 26a, 100% here), Part IV lines 33-35; Pub. 544 (2025), chapter 3: the Section 1245 Property
example (a 10,000 light-duty truck, depreciation 2,000 + 3,200 + 960 = 6,160, sold for 7,000: adjusted basis 3,840, gain
3,160, all ordinary), Nonrecaptured section 1231 losses and its example (a 2,500 loss, an 1,800 gain, 700 remaining: a
2,000 gain is 700 ordinary and 1,300 long-term capital gain), Section 1250 Property and Additional Depreciation (straight-
line MACRS real property has none); the Schedule D (2025) instructions' Unrecaptured Section 1250 Gain Worksheet (lines
1-18) and Schedule D Tax Worksheet (lines 1-47); the Form 8960 (2025) instructions, lines 5a and 5b and the Lines 5a-5d
worksheet; the Form 6251 (2025) instructions, lines 2k and 2l; Pub. 946 Table A-1 (5-year property, half-year convention:
20%, 32%, 19.2%) with the 60% special depreciation allowance of 2024 (IRC §168(k)(6)(A)) for the asset-register case; and
the 2026 figures of Rev. Proc. 2025-32 (standard deduction 16,100 single; 10% to 12,400, 12% to 50,400, 22% to 105,700,
24% to 201,775, 32% to 256,225, 35% above; 0% capital gain rate to 49,450, 15% to 545,500; AMT exemption 90,100, 26% to
244,500) and the Tax Table midpoint convention. Nothing below was taken from the engine.
"""

from datetime import date
from decimal import Decimal

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)
from test_returns_1040 import ctx, run, you
from test_returns_new_documents import _must_not_reach_review

from agentledger.calc.federal import Asset
from agentledger.ledger import store as ledger
from agentledger.returns.facts import InputRejected
from agentledger.returns.individual import compute_individual, held_long_term
from agentledger.returns.model import (K1, W2, Business, BusinessUseRecapture, Disposition, Dividends, IndividualReturn, Interest,
                                       PriorYear, Rental, Section1231Loss)
from agentledger.returns.store import ENGINE_VERSION, Returns, _check_fields, carryforward_name, carryforward_row, carryovers

F = "f4797"
WS = "ws_unrecaptured_1250"
NONE = PriorYear()                     # the prior-year group stated with no nonrecaptured section 1231 losses


def lines(r, form=F):
    return {k: int(v) for k, v in r.forms[form].items()}


def codes(r, severity="error"):
    return [d.code for d in r.diagnostics if d.severity == severity]


def machine(**kw):
    """Pub. 544-style section 1245 property held more than 1 year: cost 20,000, depreciation 12,000, sold for 24,000 (adjusted
    basis 8,000, gain 16,000: 12,000 ordinary, 4,000 section 1231 gain)."""
    d = dict(description="Machine", acquired=date(2022, 3, 1), sold=date(2026, 6, 1), gross_sales_price=24000, cost_or_basis=20000,
             depreciation_allowed=12000, property_class="section_1245")
    return Disposition(**{**d, **kw})


def lot(price, **kw):
    """Land used in a business, bought in 2020 for 10,000 and sold in 2026 for `price`."""
    d = dict(description="Lot", acquired=date(2020, 1, 1), sold=date(2026, 3, 1), gross_sales_price=price, cost_or_basis=10000,
             depreciation_allowed=0, property_class="land")
    return Disposition(**{**d, **kw})


def building(**kw):
    """A rental building (section 1250 property, straight-line MACRS so no additional depreciation): cost 500,000, depreciation
    150,000, sold for 650,000 (gain 300,000), and its land (cost 100,000, sold for 120,000)."""
    b = dict(description="Apartment building", acquired=date(2015, 1, 10), sold=date(2026, 7, 1), gross_sales_price=650000, cost_or_basis=500000,
             depreciation_allowed=150000, property_class="section_1250", additional_depreciation=0, rental=1)
    land = dict(description="Land under the building", acquired=date(2015, 1, 10), sold=date(2026, 7, 1), gross_sales_price=120000,
                cost_or_basis=100000, depreciation_allowed=0, property_class="land", rental=1)
    return [Disposition(**{**b, **kw}), Disposition(**land)]


# --------------------------------------------------------------------------------------------- Part III, section 1245
def test_pub_544_truck_section_1245_recapture_is_all_ordinary():
    """Pub. 544 (2025), Section 1245 Property, Gain Treated as Ordinary Income, example (dates one year on): a light-duty
    truck bought in February 2024 for 10,000, MACRS deductions of 2,000 + 3,200 + 960 (half of 1,920 in the year of sale) =
    6,160, sold in May 2026 for 7,000. Part III: 20 = 7,000; 21 = 10,000; 22 = 6,160; 23 = 3,840; 24 = 3,160; 25a = 6,160;
    25b = the smaller, 3,160; 30 = 3,160; 31 = 3,160; 32 = 0. Part II: 13 = 3,160; 17 = 18b = 3,160 to Schedule 1 line 4.
    Nothing reaches Part I or Schedule D. Wages 50,000: AGI 53,160."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], prior_year=NONE,
            dispositions=[Disposition(description="Light-duty truck", acquired=date(2024, 2, 1), sold=date(2026, 5, 15), gross_sales_price=7000,
                                      cost_or_basis=10000, depreciation_allowed=6160, property_class="section_1245")])
    got = lines(r)
    assert {k: got[k] for k in ("20A", "21A", "22A", "23A", "24A", "25aA", "25bA", "30", "31", "32")} == {
        "20A": 7000, "21A": 10000, "22A": 6160, "23A": 3840, "24A": 3160, "25aA": 6160, "25bA": 3160, "30": 3160, "31": 3160, "32": 0}
    assert {k: got[k] for k in ("2", "6", "7", "10", "11", "12", "13", "17", "18b")} == {
        "2": 0, "6": 0, "7": 0, "10": 0, "11": 0, "12": 0, "13": 3160, "17": 3160, "18b": 3160}
    assert r.line("sch_1", "4") == 3160 and r.line("f1040", "8") == 3160 and r.line("f1040", "11a") == 53160 and "sch_d" not in r.forms
    assert r.sheets.facts[F]["19A"] == {"description": "Light-duty truck", "acquired": "2024-02-01", "sold": "2026-05-15", "class": "section_1245",
                                        "source": "stated"}
    assert not r.blocking and codes(r, "warning") == ["form_6251_disposition_adjustment_unverified"]
    assert not any(k.startswith("nonrecaptured_1231_loss") for k in r.carryforwards)
    assert "us_fed.individual.section_1231_lookback_years" in {s["rule_id"] for s in r.sources}


def test_section_1245_gain_over_the_depreciation_splits_into_ordinary_and_section_1231_gain():
    """The machine: 24 = 16,000; 25a = 12,000; 25b = 12,000 (ordinary, IRC §1245(a)(1)); 31 = 12,000 to line 13; 32 = 4,000 to
    line 6; line 7 = 4,000, no prior losses: a long-term capital gain on Schedule D line 11 (§1231(a)(1)). Wages 60,000: AGI
    76,000; taxable income 59,900; the Qualified Dividends and Capital Gain Tax Worksheet taxes 55,900 at ordinary rates
    (Tax Table midpoint 55,925: 5,800 + 22% x 5,525 = 7,015.50 -> 7,016) and 4,000 at 15% (above the 49,450 breakpoint) =
    600: line 16 = 7,616."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], prior_year=NONE, dispositions=[machine()])
    got = lines(r)
    assert {k: got[k] for k in ("24A", "25aA", "25bA", "30", "31", "32", "2", "6", "7", "12", "13", "17", "18b")} == {
        "24A": 16000, "25aA": 12000, "25bA": 12000, "30": 16000, "31": 12000, "32": 4000, "2": 0, "6": 4000, "7": 4000, "12": 0, "13": 12000,
        "17": 12000, "18b": 12000}
    assert "8" not in got and "9" not in got                                     # skipped: no nonrecaptured losses
    assert lines(r, "sch_d") == {"5": 0, "6": 0, "7": 0, "11": 4000, "12": 0, "13": 0, "14": 0, "15": 4000, "16": 4000, "18": 0, "19": 0}
    assert r.line("sch_1", "4") == 12000 and r.line("f1040", "7a") == 4000 and r.line("f1040", "11a") == 76000 and r.line("f1040", "15") == 59900
    qdcg = r.sheets.facts["ws_qdcg"]["lines"]
    assert (qdcg["3"], qdcg["5"], qdcg["18"], qdcg["22"], qdcg["25"]) == ("4000", "55900", "600", "7016", "7616") and r.line("f1040", "16") == 7616
    assert not r.blocking


# --------------------------------------------------------------------------------------------- Part III, section 1250
def test_section_1250_building_unrecaptured_gain_reaches_the_25_percent_part_of_the_schedule_d_tax_worksheet():
    """Wages 150,000 and the rental building and land. Part III (building): 20 = 650,000; 21 = 500,000; 22 = 150,000; 23 =
    350,000; 24 = 300,000; 26a = 0 (straight line); 26b = 100% x 0 = 0; 26c = 300,000; 26g = 0; 31 = 0; 32 = 300,000 to line 6.
    Part I: line 2 = 20,000 (land); 7 = 320,000, no prior losses: Schedule D line 11 = 320,000. Unrecaptured Section 1250
    Gain Worksheet: 1 = smaller of 22 or 24 = 150,000; 2 = 0; 3 = 150,000; 6 = 150,000; 7 = smaller of 150,000 or line 7 =
    150,000; 8 = 0; 9 = 13 = 150,000; 14-17 = 0; 18 = 150,000 to Schedule D line 19. AGI 470,000; taxable income 453,900.
    Schedule D Tax Worksheet: 7 = 9 = 10 = 320,000; 11 = 12 = 150,000; 13 = 170,000; 14 = 283,900; 16 = 17 = 49,450; 18 =
    133,900; 19 = 20 = 21 = 201,775; 22 = 0; 23 = 25 = 170,000; 27 = 453,900; 28 = 201,775; 29 = 252,125; 30 = 170,000; 31 =
    25,500 (15%); 33 = 34 = 0; 35 = 150,000; 36 = 521,775; 38 = 67,875; 39 = 82,125; 40 = 20,531.25 (25%); 44 = tax on 201,775
    = 41,024; 45 = 87,055.25; 46 = 127,634.25; 47 = 87,055. NIIT: the rental is a section 1411 activity, so line 5b = 0; 8 = 12 =
    320,000; 15 = 16 = 270,000; 17 = 10,260. AMT Part III: 12 = 379,900; 13 = 170,000; 14 = 150,000; 15 = 16 = 320,000; 17 =
    59,900; 18 = 15,574; 22 = 24 = 30 = 170,000; 31 = 25,500; 35 = 229,900; 36 = 150,000; 37 = 37,500; 38 = 78,574 < 87,055:
    no AMT. Total tax 97,315."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=150000)], prior_year=NONE, rentals=[Rental(address="12 Elm St")],
            dispositions=building())
    got = lines(r)
    assert {k: got[k] for k in ("20A", "21A", "22A", "23A", "24A", "26aA", "26bA", "26cA", "26gA", "30", "31", "32", "2", "6", "7", "13", "18b")} == {
        "20A": 650000, "21A": 500000, "22A": 150000, "23A": 350000, "24A": 300000, "26aA": 0, "26bA": 0, "26cA": 300000, "26gA": 0, "30": 300000,
        "31": 0, "32": 300000, "2": 20000, "6": 300000, "7": 320000, "13": 0, "18b": 0}
    assert r.sheets.facts[F]["part_i"][0]["description"] == "Land under the building" and r.sheets.facts[F]["19A"]["class"] == "section_1250"
    assert lines(r, WS) == {"3": 150000, "4": 0, "5": 0, "6": 150000, "7": 150000, "8": 0, "9": 150000, "10": 0, "11": 0, "12": 0, "13": 150000,
                            "14": 0, "15": 0, "16": 0, "17": 0, "18": 150000}
    assert r.sheets.facts[WS]["properties"] == [{"description": "Apartment building", "1": "150000", "2": "0", "3": "150000"}]
    assert r.line("sch_d", "11") == 320000 and r.line("sch_d", "19") == 150000 and r.line("f1040", "11a") == 470000 and r.line("f1040", "15") == 453900
    w = r.sheets.facts["ws_sch_d_tax"]["lines"]
    assert {k: w[k] for k in ("7", "11", "13", "14", "21", "30", "31", "35", "36", "38", "39", "40", "44", "45", "46", "47")} == {
        "7": "320000", "11": "150000", "13": "170000", "14": "283900", "21": "201775", "30": "170000", "31": "25500", "35": "150000",
        "36": "521775", "38": "67875", "39": "82125", "40": "20531", "44": "41024", "45": "87055", "46": "127634", "47": "87055"}
    assert r.line("f1040", "16") == 87055
    f8960 = lines(r, "f8960")
    assert {k: f8960[k] for k in ("5a", "5b", "5d", "8", "12", "15", "16", "17")} == {
        "5a": 320000, "5b": 0, "5d": 320000, "8": 320000, "12": 320000, "15": 270000, "16": 270000, "17": 10260}
    assert r.line("f6251", "7") == 78574 and r.line("f6251", "36") == 150000 and r.line("sch_2", "2") == 0
    assert r.line("f1040", "24c") == 97315 and not r.blocking
    assert "us_fed.individual.section_1250_applicable_percentage" in {s["rule_id"] for s in r.sources}


def test_additional_depreciation_on_section_1250_property_is_recaptured_at_100_percent():
    """Section 1250 property with 30,000 of depreciation in excess of straight line (a special allowance on qualified
    improvement property): 24 = 300,000; 26a = 30,000; 26b = 100% x smaller of 300,000 or 30,000 = 30,000 (IRC §1250(a)(1));
    26g = 30,000 to line 31 and Part II line 13; 32 = 270,000 to Part I. Worksheet lines 1-3: 150,000 - 30,000 = 120,000."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=150000)], prior_year=NONE, rentals=[Rental(address="12 Elm St")],
            dispositions=building(additional_depreciation=30000))
    got = lines(r)
    assert (got["26aA"], got["26bA"], got["26cA"], got["26gA"], got["31"], got["32"], got["7"], got["13"], got["18b"]) == (
        30000, 30000, 270000, 30000, 30000, 270000, 290000, 30000, 30000)
    assert lines(r, WS)["3"] == 120000 and r.line("sch_d", "19") == 120000 and r.line("sch_d", "11") == 290000 and r.line("sch_1", "4") == 30000


# --------------------------------------------------------------------------------------------- Part I, section 1231(c)
def test_form_4797_line_8_example_recaptures_the_prior_losses_earliest_first():
    """Form 4797 (2025) instructions, line 8 example, one year on: net section 1231 losses of 4,000 (2021) and 6,000 (2022),
    a 3,000 gain in 2025 applied against the 2021 loss; so the prior-year group states 1,000 of 2021 and 6,000 of 2022 left.
    This year's net section 1231 gain of 2,000 (a lot bought for 10,000, sold for 12,000) is on line 7; line 8 = 7,000; line 9
    = 0; the whole 2,000 is ordinary on line 12 (to 17, 18b, Schedule 1 line 4) and nothing goes to Schedule D. Recordkeeping:
    the 2021 loss is fully recaptured (1,000), 5,000 of 2022 is left and carries to 2027 (2022 is still within its 5 years)."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], dispositions=[lot(12000)],
            prior_year=PriorYear(nonrecaptured_1231_losses=[Section1231Loss(tax_year=2021, nonrecaptured_loss=1000),
                                                            Section1231Loss(tax_year=2022, nonrecaptured_loss=6000)]))
    assert lines(r) == {"2": 2000, "6": 0, "7": 2000, "8": 7000, "9": 0, "10": 0, "11": 0, "12": 2000, "13": 0, "17": 2000, "18b": 2000}
    assert "sch_d" not in r.forms and r.line("sch_1", "4") == 2000 and r.line("f1040", "7a") == 0
    assert r.sheets.facts[F]["nonrecaptured_losses"] == [
        {"year": "2021", "loss": "1000", "recaptured": "1000", "remaining": "0", "carried_to_next_year": "False"},
        {"year": "2022", "loss": "6000", "recaptured": "1000", "remaining": "5000", "carried_to_next_year": "True"}]
    assert r.carryforwards == {"nonrecaptured_1231_loss_2022": Decimal(5000), "capital_loss_carryover_short": Decimal(0),
                               "capital_loss_carryover_long": Decimal(0)}
    assert not r.blocking


def test_pub_544_nonrecaptured_loss_example_recharacterises_part_of_the_gain():
    """Pub. 544 (2025), Nonrecaptured section 1231 losses, example, one year on: a 2,500 loss (2023) net of an 1,800 gain
    (2025) leaves 700; this year's 2,000 net section 1231 gain is 700 ordinary income (line 8 = 700, line 9 = 1,300, line 12
    = 700) and 1,300 long-term capital gain (Schedule D line 11). Nothing is left to carry."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], dispositions=[lot(12000)],
            prior_year=PriorYear(nonrecaptured_1231_losses=[Section1231Loss(tax_year=2023, nonrecaptured_loss=700)]))
    got = lines(r)
    assert (got["7"], got["8"], got["9"], got["12"], got["17"], got["18b"]) == (2000, 700, 1300, 700, 700, 700)
    assert r.line("sch_d", "11") == 1300 and r.line("f1040", "7a") == 1300 and r.line("sch_1", "4") == 700
    assert not any(k.startswith("nonrecaptured_1231_loss") for k in r.carryforwards) and not r.blocking


def test_a_net_section_1231_loss_is_ordinary_and_carries_forward_with_the_older_losses():
    """A lot sold at a 2,000 loss: line 7 = -2,000 is entered on line 11 (ordinary, IRC §1231(a)(2)); 17 = 18b = -2,000 to
    Schedule 1 line 4; AGI 48,000. This year's net loss carries as nonrecaptured_1231_loss_2026, and a stated 2024 loss of 500,
    unapplied, carries too (2024 is within 2027's 5 preceding years)."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], dispositions=[lot(8000)],
            prior_year=PriorYear(nonrecaptured_1231_losses=[Section1231Loss(tax_year=2024, nonrecaptured_loss=500)]))
    assert lines(r) == {"2": -2000, "6": 0, "7": -2000, "10": 0, "11": -2000, "12": 0, "13": 0, "17": -2000, "18b": -2000}
    assert r.line("sch_1", "4") == -2000 and r.line("f1040", "11a") == 48000 and "sch_d" not in r.forms
    assert r.carryforwards["nonrecaptured_1231_loss_2026"] == 2000 and r.carryforwards["nonrecaptured_1231_loss_2024"] == 500
    assert not r.blocking
    alone = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], dispositions=[lot(8000)])   # no prior-year group at all
    assert alone.carryforwards["nonrecaptured_1231_loss_2026"] == 2000 and not alone.blocking           # a loss needs no lookback


def test_the_five_year_window_expires_losses_and_stops_carrying_the_oldest():
    """Losses of 300 (2020, outside the 5 preceding years of 2026: not counted, noted), 400 (2021) and 500 (2024); a 100
    gain. Line 8 = 900; line 9 = 0; line 12 = 100. Earliest first: 100 of 2021 is recaptured, 300 of 2021 remains but 2021
    is outside 2027's window and is not carried; 500 of 2024 carries."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], dispositions=[lot(10100)],
            prior_year=PriorYear(nonrecaptured_1231_losses=[Section1231Loss(tax_year=2020, nonrecaptured_loss=300),
                                                            Section1231Loss(tax_year=2021, nonrecaptured_loss=400),
                                                            Section1231Loss(tax_year=2024, nonrecaptured_loss=500)]))
    got = lines(r)
    assert (got["7"], got["8"], got["9"], got["12"]) == (100, 900, 0, 100)
    assert codes(r, "info") == ["form_4797_nonrecaptured_loss_expired", "form_2210"]
    assert {k: v for k, v in r.carryforwards.items() if k.startswith("nonrecaptured")} == {"nonrecaptured_1231_loss_2024": Decimal(500)}


# --------------------------------------------------------------------------------------------- Parts I and II, placement
def test_property_held_one_year_or_less_is_ordinary_whatever_its_class():
    """A building bought in January and sold in November 2026 at a 30,000 gain: Part II line 10 (the chart Where To Make
    First Entry: held 1 year or less -> Part II), no Part III and no Schedule D; the day-after rule makes a sale on the first
    anniversary short-term."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], prior_year=NONE,
            dispositions=[Disposition(description="Flipped building", acquired=date(2026, 1, 10), sold=date(2026, 11, 1), gross_sales_price=330000,
                                      cost_or_basis=300000, depreciation_allowed=0, property_class="section_1250")])
    assert lines(r) == {"2": 0, "6": 0, "7": 0, "10": 30000, "11": 0, "12": 0, "13": 0, "17": 30000, "18b": 30000}
    assert r.sheets.facts[F]["part_ii"][0]["gain"] == "30000" and "sch_d" not in r.forms and r.line("sch_1", "4") == 30000 and not r.blocking
    assert not held_long_term(date(2025, 3, 1), date(2026, 3, 1)) and held_long_term(date(2025, 3, 1), date(2026, 3, 2))
    assert held_long_term(date(2024, 2, 29), date(2025, 3, 2)) and not held_long_term(date(2024, 2, 29), date(2025, 3, 1))


def test_k1_boxes_10_and_9c_feed_part_i_and_the_unrecaptured_worksheet():
    """A passive partnership K-1 with 5,000 of net section 1231 gain (box 10) and 2,000 of unrecaptured section 1250 gain
    (box 9c): line 2 = 7 = 5,000 to Schedule D line 11; worksheet 5 = 2,000; 6 = 7 = 9 = 13 = 18 = 2,000 to Schedule D line
    19. Wages 60,000: taxable income 48,900; Schedule D Tax Worksheet: 7 = 10 = 5,000; 11 = 12 = 2,000; 13 = 3,000; 14 =
    45,900; 16 = 48,900; 17 = 45,900; 18 = 43,900; 21 = 45,900 (the 2,000 is taxed at ordinary rates, below 25%); 22 = 3,000
    at 0%; 44 = tax on 45,900 (midpoint 45,925: 1,240 + 12% x 33,525 = 5,263); 47 = 5,263."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], prior_year=NONE,
            k1s=[K1(entity_name="Oak Partners", passive=True, net_section_1231_gain=5000, unrecaptured_1250_gain=2000)])
    assert (r.line(F, "2"), r.line(F, "7"), r.line(F, "18b")) == (5000, 5000, 0)
    assert r.sheets.facts[F]["part_i"] == [{"description": "Schedule K-1 Oak Partners, net section 1231 gain or (loss)", "gain": "5000"}]
    assert lines(r, WS) == {"3": 0, "4": 0, "5": 2000, "6": 2000, "7": 2000, "8": 0, "9": 2000, "10": 0, "11": 0, "12": 0, "13": 2000, "14": 0,
                            "15": 0, "16": 0, "17": 0, "18": 2000}
    assert r.line("sch_d", "11") == 5000 and r.line("sch_d", "19") == 2000 and r.line("f1040", "15") == 48900
    w = r.sheets.facts["ws_sch_d_tax"]["lines"]
    assert (w["13"], w["21"], w["22"], w["44"], w["47"]) == ("3000", "45900", "3000", "5263", "5263") and r.line("f1040", "16") == 5263
    assert not r.blocking


def test_the_worksheet_nets_the_short_term_loss_and_carryover_against_dividend_box_2b():
    """Only 1099-DIV amounts: 3,000 of capital gain distributions of which 1,000 is unrecaptured section 1250 gain (box 2b),
    a 400 short-term loss and a 300 long-term carryover from 2025. Form 4797 is not filed (lines 1-9 skipped); 11 = 13 =
    1,000; 15 = (400); 16 = (300); 17 = 700; 18 = 300 on Schedule D line 19 (the old engine took the whole 1,000)."""
    from test_returns_capital_loss_carryover import short_loss

    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], capital_transactions=[short_loss(400)],
            dividends=[Dividends(ordinary=100, capital_gain_distributions=3000, unrecaptured_1250_gain=1000)],
            prior_year=PriorYear(capital_loss_carryover_long=300))
    assert F not in r.forms
    assert lines(r, WS) == {"10": 0, "11": 1000, "12": 0, "13": 1000, "14": 0, "15": -400, "16": -300, "17": 700, "18": 300}
    assert r.line("sch_d", "19") == 300 and r.line("sch_d", "16") == 2300 and not r.blocking


# --------------------------------------------------------------------------------------------- interactions
def test_form_8960_line_5b_excludes_the_gains_of_a_non_passive_trade_or_business():
    """Single, wages 230,000, interest 10,000, a materially participating Schedule C that sells the machine: Form 1040 line
    7a = 4,000 and Schedule 1 line 4 = 12,000, AGI 256,000. Form 8960: 1 = 10,000; 5a = 16,000; 5b = (16,000), the gain on
    property of a non-section 1411 trade or business (Reg. §1.1411-4(d)(4)(i)); 5d = 0; 8 = 12 = 10,000; 15 = 56,000; 16 =
    10,000; 17 = 380. Without material participation the business is passive: 5b = 0; 8 = 16 = 26,000; 17 = 988. With no
    activity named the line cannot be decided and the return blocks."""
    base = dict(filing_status="single", taxpayer=you(), w2s=[W2(wages=230000)], interest=[Interest(interest=10000)], prior_year=NONE)
    active = run(**base, businesses=[Business(name="Shop")], dispositions=[machine(schedule_c=1)])
    got = lines(active, "f8960")
    assert {k: got[k] for k in ("1", "5a", "5b", "5d", "8", "12", "15", "16", "17")} == {
        "1": 10000, "5a": 16000, "5b": -16000, "5d": 0, "8": 10000, "12": 10000, "15": 56000, "16": 10000, "17": 380}
    assert active.line("f1040", "11a") == 256000 and active.line("sch_2", "6") == 380 and not active.blocking
    passive = run(**base, businesses=[Business(name="Shop", materially_participates=False)], dispositions=[machine(schedule_c=1)])
    assert (passive.line("f8960", "5b"), passive.line("f8960", "8"), passive.line("f8960", "17")) == (0, 26000, 988) and not passive.blocking
    unplaced = run(**base, dispositions=[machine()])
    assert codes(unplaced) == ["form_8960_disposition_activity_unknown"] and unplaced.line("f8960", "5b") == 0
    k1 = run(**base, k1s=[K1(entity_name="Oak LLC", passive=False, net_section_1231_gain=16000)])
    assert (k1.line("f8960", "5a"), k1.line("f8960", "5b"), k1.line("f8960", "17")) == (16000, -16000, 380) and not k1.blocking


def test_qbi_takes_the_ordinary_part_of_the_business_property_gain_but_not_the_section_1231_gain():
    """A Schedule C with 50,000 of receipts and no expenses sells the machine. Schedule SE: 46,175 of net earnings; 5,726 +
    1,339 = 7,065 of tax; one-half 3,532.50 -> 3,533. QBI = 50,000 - 3,533 + 12,000 of recapture (ordinary, Reg.
    §1.199A-3(b)(2)(ii)(A)) = 58,467; the 4,000 section 1231 gain is capital gain and excluded. Form 8995: 2 = 58,467; 5 =
    11,693; taxable income before the deduction 62,467 - 16,100 = 46,367 less net capital gain 4,000 = 42,367; 14 = 8,473; 15 =
    8,473."""
    r = run(filing_status="single", taxpayer=you(), prior_year=NONE, businesses=[Business(name="Shop", gross_receipts=50000)],
            dispositions=[machine(schedule_c=1)])
    assert r.line("sch_se[taxpayer]", "13") == 3533 and r.line("f1040", "11a") == 62467
    assert (r.line("f8995", "2"), r.line("f8995", "5"), r.line("f8995", "13"), r.line("f8995", "15")) == (58467, 11693, 42367, 8473)
    assert r.sheets.facts["f8995"]["businesses"] == [{"name": "Shop", "ein": "", "qbi": "58467"}] and not r.blocking
    # A net section 1231 loss of the business's property is ordinary and reduces its QBI.
    loss = run(filing_status="single", taxpayer=you(), prior_year=NONE, businesses=[Business(name="Shop", gross_receipts=50000)],
               dispositions=[lot(8000, schedule_c=1)])
    assert loss.line("f8995", "2") == 50000 - 3533 - 2000


def test_part_iv_recapture_is_other_income_of_the_schedule_c_that_took_the_deduction():
    """Business use of a 10,000 section 179 asset dropped to 50% or less: line 33(a) = 10,000; 34(a) = 3,000 (the depreciation
    that would have been allowable); 35(a) = 7,000. A listed vehicle: 33(b) = 8,000; 34(b) = 5,000; 35(b) = 3,000. Both go to
    Schedule C line 6 (other income, 10,000; the instructions for line 35), so gross income 60,000 and net profit 60,000 feed
    self-employment tax."""
    r = run(filing_status="single", taxpayer=you(), businesses=[Business(name="Shop", gross_receipts=50000)],
            business_use_recaptures=[BusinessUseRecapture(description="Lathe", kind="section_179", deduction_claimed=10000, recomputed_depreciation=3000, schedule_c=1),
                                     BusinessUseRecapture(description="Van", kind="section_280f", deduction_claimed=8000, recomputed_depreciation=5000, schedule_c=1)])
    got = lines(r)
    assert {k: v for k, v in got.items() if k[:2] in ("33", "34", "35")} == {"33a": 10000, "34a": 3000, "35a": 7000, "33b": 8000, "34b": 5000, "35b": 3000}
    assert got["18b"] == 0 and "sch_d" not in r.forms                          # Parts I-III carry nothing
    assert r.sheets.facts[F]["part_iv"] == [
        {"description": "Lathe", "kind": "section_179", "33": "10000", "34": "3000", "35": "7000", "reported_on": "sch_c[1] line 6"},
        {"description": "Van", "kind": "section_280f", "33": "8000", "34": "5000", "35": "3000", "reported_on": "sch_c[1] line 6"}]
    assert r.line("sch_c[1]", "6") == 10000 and r.line("sch_c[1]", "7") == 60000 and r.line("sch_c[1]", "31") == 60000 and r.line("sch_1", "3") == 60000
    assert r.line("sch_se[taxpayer]", "2") == 60000 and not r.blocking
    assert codes(r, "warning") == ["form_6251_disposition_adjustment_unverified"]


def test_the_asset_register_supplies_cost_and_depreciation_through_the_year_of_sale():
    """A registered 5-year asset (cost 10,000, acquired and placed in service March 2024, no section 179): 2024 = 60% special
    allowance 6,000 + 20% x 4,000 = 800 -> 6,800 (IRC §168(k)(6)(A); Pub. 946 Table A-1); 2025 = 32% x 4,000 = 1,280; 2026,
    the year of sale, half of 19.2% x 4,000 = 384 (half-year convention). Depreciation allowed 8,464; adjusted basis 1,536;
    sold for 7,000: gain 5,464, all ordinary (section 1245). The disposition names the asset and states nothing else; a stated
    amount that disagrees with the register, an unknown asset and an asset sold in its first year block."""
    truck = Asset("truck-01", Decimal(10000), date(2024, 3, 1), date(2024, 3, 1), 5, 5)
    base = dict(tax_year=2026, filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], prior_year=NONE)
    ret = IndividualReturn(**base, dispositions=[Disposition(description="Truck", sold=date(2026, 5, 15), gross_sales_price=7000, asset_id="truck-01")])
    r = compute_individual(ctx(), ret, [truck])
    got = lines(r)
    assert (got["20A"], got["21A"], got["22A"], got["23A"], got["24A"], got["25bA"], got["31"], got["18b"]) == (7000, 10000, 8464, 1536, 5464, 5464, 5464, 5464)
    assert r.sheets.facts[F]["19A"] == {"description": "Truck", "acquired": "2024-03-01", "sold": "2026-05-15", "class": "section_1245",
                                        "source": "asset register truck-01"}
    assert not r.blocking
    conflict = compute_individual(ctx(), IndividualReturn(**base, dispositions=[Disposition(
        description="Truck", sold=date(2026, 5, 15), gross_sales_price=7000, depreciation_allowed=8000, asset_id="truck-01")]), [truck])
    assert codes(conflict) == ["form_4797_asset_register_conflict"] and F not in conflict.forms
    unknown = compute_individual(ctx(), ret, [])
    assert "form_4797_asset_unknown" in codes(unknown) and F not in unknown.forms   # and nothing else is stated: those block too
    first_year = compute_individual(ctx(), IndividualReturn(**base, dispositions=[Disposition(
        description="Truck", sold=date(2026, 5, 15), gross_sales_price=7000, asset_id="truck-01")]), [Asset("truck-01", Decimal(10000), date(2026, 3, 1), date(2026, 3, 1), 5, 5)])
    assert codes(first_year) == ["form_4797_asset_disposed_in_service_year"]


def test_the_entire_interest_disposition_is_recorded_for_form_8582():
    """IRC §469(g)(1)(A): the rental (a passive activity) was disposed of entirely; the fact is recorded for Form 8582 and a
    warning says the release of suspended losses is not computed. A materially participating Schedule C is not passive: the
    fact is recorded without the warning."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], prior_year=NONE, rentals=[Rental(address="12 Elm St")],
            dispositions=[Disposition(description="Duplex", acquired=date(2015, 1, 10), sold=date(2026, 7, 1), gross_sales_price=300000, cost_or_basis=250000,
                                      depreciation_allowed=60000, property_class="section_1250", additional_depreciation=0, rental=1,
                                      entire_interest_disposed=True)])
    assert r.sheets.facts[F]["section_469g_dispositions"] == [{"description": "Duplex", "activity": "rental[1]", "passive": True}]
    assert set(codes(r, "warning")) == {"form_6251_disposition_adjustment_unverified", "form_8582_release_not_computed"} and not r.blocking
    active = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], prior_year=NONE, businesses=[Business(name="Shop")],
                 dispositions=[machine(schedule_c=1, entire_interest_disposed=True)])
    assert active.sheets.facts[F]["section_469g_dispositions"] == [{"description": "Machine", "activity": "schedule_c[1]", "passive": False}]
    assert "form_8582_release_not_computed" not in codes(active, "warning")


def test_the_amt_warning_is_silenced_by_a_stated_adjustment_and_absent_for_land():
    stated = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], prior_year=NONE, dispositions=[machine()], amt_adjustments={"disposition": 0})
    assert "form_6251_disposition_adjustment_unverified" not in codes(stated, "warning") and not stated.blocking
    land = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], prior_year=NONE, dispositions=[lot(12000)])
    assert "form_6251_disposition_adjustment_unverified" not in codes(land, "warning")


def test_the_rule_values():
    kb = ctx().kb
    pct = kb.resolve("us_fed.individual.section_1250_applicable_percentage", date(2026, 6, 30))
    assert pct.value == 1.0 and "§1250(a)(1)(B)(v)" in pct.source and pct.url == "https://www.irs.gov/instructions/i4797"
    years = kb.resolve("us_fed.individual.section_1231_lookback_years", date(2026, 6, 30))
    assert years.value == 5 and "§1231(c)(2)(A)" in years.source
    assert kb.try_resolve("us_fed.individual.section_1231_lookback_years", date(1984, 6, 30)) is None   # the §1231(c) lookback did not exist


# --------------------------------------------------------------------------------------------- scope limits block (Q19)
BLOCKED = [
    (dict(dispositions=[machine(gross_sales_price=None)]), "form_4797_sales_price_unknown"),
    (dict(dispositions=[machine(cost_or_basis=None)]), "form_4797_basis_unknown"),
    (dict(dispositions=[machine(depreciation_allowed=None)]), "form_4797_depreciation_unknown"),
    (dict(dispositions=[machine(property_class=None)]), "form_4797_property_class_unknown"),
    (dict(dispositions=[machine(installment_sale=True)]), "form_4797_installment_sale"),
    (dict(dispositions=[machine(like_kind_exchange=True)]), "form_4797_like_kind_exchange"),
    (dict(dispositions=[machine(casualty_or_theft=True)]), "form_4797_casualty_or_theft"),
    (dict(dispositions=[machine(partial_disposition=True)]), "form_4797_partial_disposition"),
    (dict(dispositions=[machine(related_party=True)]), "form_4797_related_party"),
    (dict(rentals=[Rental(address="x")], dispositions=building(low_income_housing=True)), "form_4797_low_income_housing"),
    (dict(rentals=[Rental(address="x")], dispositions=building(additional_depreciation=None)), "form_4797_additional_depreciation_unknown"),
    (dict(dispositions=[machine(acquired=None)]), "form_4797_holding_period_unknown"),
    (dict(dispositions=[lot(12000, depreciation_allowed=1000)]), "form_4797_land_depreciated"),
    (dict(rentals=[Rental(address="x")], dispositions=building(acquired=date(1975, 6, 1))), "form_4797_pre_1976_property"),
    (dict(dispositions=[machine(schedule_c=3)]), "form_4797_activity_unknown"),
    (dict(businesses=[Business(name="Shop")], rentals=[Rental(address="x")], dispositions=[machine(schedule_c=1, rental=1)]), "form_4797_activity_ambiguous"),
    (dict(dispositions=[machine(sold=date(2025, 12, 31))]), "form_4797_sale_date_outside_year"),
    (dict(dispositions=[machine()], prior_year=None), "form_4797_nonrecaptured_losses_unknown"),
    (dict(dispositions=[lot(12000)], prior_year=PriorYear(nonrecaptured_1231_losses=[Section1231Loss(tax_year=2026, nonrecaptured_loss=100)])),
     "form_4797_nonrecaptured_loss_year_invalid"),
    (dict(dispositions=[lot(12000)], prior_year=PriorYear(nonrecaptured_1231_losses=[Section1231Loss(tax_year=2024, nonrecaptured_loss=-100)])),
     "form_4797_nonrecaptured_loss_sign"),
    (dict(dispositions=[machine(entire_interest_disposed=True)]), "form_4797_activity_unknown"),
    (dict(business_use_recaptures=[BusinessUseRecapture(description="x", kind="section_179", recomputed_depreciation=0, schedule_c=1)],
          businesses=[Business(name="Shop")]), "form_4797_part_iv_deduction_unknown"),
    (dict(business_use_recaptures=[BusinessUseRecapture(description="x", kind="section_179", deduction_claimed=1000, schedule_c=1)],
          businesses=[Business(name="Shop")]), "form_4797_part_iv_recomputed_unknown"),
    (dict(business_use_recaptures=[BusinessUseRecapture(description="x", kind="section_179", deduction_claimed=1000, recomputed_depreciation=0, rental=1)],
          rentals=[Rental(address="x")]), "form_4797_part_iv_schedule_unsupported"),
    (dict(business_use_recaptures=[BusinessUseRecapture(description="x", kind="section_179", deduction_claimed=1000, recomputed_depreciation=0, schedule_c=2)],
          businesses=[Business(name="Shop")]), "form_4797_activity_unknown"),
    (dict(dispositions=[machine(asset_id="nope")]), "form_4797_asset_unknown"),
]


@pytest.mark.parametrize("kw,code", BLOCKED, ids=[c + ("-" + str(i) if [x[1] for x in BLOCKED].count(c) > 1 else "") for i, (_, c) in enumerate(BLOCKED)])
def test_scope_limits_and_unknown_facts_block(kw, code):
    kw = {"filing_status": "single", "taxpayer": you(), "w2s": [W2(wages=60000)], "prior_year": NONE, **kw}
    r = run(**kw)
    assert code in codes(r), [(d.code, d.message) for d in r.diagnostics]


def test_a_blocked_disposition_is_left_out_rather_than_taken_as_zero():
    """While the sales price is unknown the disposition is reported in no part of the form and nothing of it reaches Schedule
    1 or Schedule D; the other disposition is figured."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], prior_year=NONE, dispositions=[machine(gross_sales_price=None), lot(12000)])
    assert codes(r) == ["form_4797_sales_price_unknown"] and lines(r)["7"] == 2000 and lines(r)["13"] == 0 and r.line("sch_1", "4") == 0


@pytest.mark.parametrize("extra,key", [
    ({"dispositions": [{"description": "x", "sold": "2026-01-01", "price": "1"}]}, "dispositions[0].price"),
    ({"k1s": [{"entity_name": "x", "box_10": "1"}]}, "k1s[0].box_10"),
    ({"business_use_recaptures": [{"description": "x", "kind": "section_179", "line_33": "1"}]}, "business_use_recaptures[0].line_33"),
])
def test_unknown_keys_inside_dispositions_are_refused(extra, key):
    with pytest.raises(InputRejected) as e:
        _check_fields({**household(), **extra})
    assert key in str(e.value)


# --------------------------------------------------------------------------------------------- the store
MACHINE = {"description": "Machine", "acquired": "2022-03-01", "sold": "2026-06-01", "cost_or_basis": "20000", "depreciation_allowed": "12000",
           "property_class": "section_1245"}


def test_a_disposition_without_its_sales_price_never_reaches_review(fam):  # noqa: F811
    """Q19: the disposition is on the return without its gross sales price; the engine blocks (never zero) until it is stated."""
    inputs = {**household(), "w2s": [{"owner": "taxpayer", "wages": "60000"}], "prior_year": {}, "dispositions": [MACHINE]}
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", inputs)
    R.compute(rid, "maya")
    res = R.latest(rid)["result"]
    assert [d["code"] for d in res["diagnostics"] if d["severity"] == "error"] == ["form_4797_sales_price_unknown"]
    assert "f4797" not in res["forms"] and res["forms"].get("sch_1", {}).get("4", "0") == "0"   # an all-zero Schedule 1 is dropped
    _must_not_reach_review(R, rid, "sales price unknown", "blocking diagnostic(s)")
    R.save_inputs(rid, {**inputs, "dispositions": [{**MACHINE, "gross_sales_price": "24000"}]}, "maya")
    res = R.latest(rid)["result"]
    assert res["forms"]["f4797"]["18b"] == "12000" and res["forms"]["sch_d"]["11"] == "4000" and res["forms"]["sch_1"]["4"] == "12000"
    assert not [d for d in res["diagnostics"] if d["severity"] == "error"]
    assert res["coverage"]["forms"]["f4797"] == "manual-assisted" and "ws_unrecaptured_1250" not in res["coverage"]["forms"]
    assert res["pinned"]["engine"] == ENGINE_VERSION == "1040-2026.4"


def test_the_store_records_the_nonrecaptured_loss_per_year_and_rolls_it_forward(fam):  # noqa: F811
    """A 2,000 net section 1231 loss this year and an unapplied 500 loss of 2024: one sealed return_carryforwards row per loss
    year (kind nonrecaptured_1231_loss, detail the year); the roll-forward lists them in the 2027 return's
    prior_year.nonrecaptured_1231_losses, each with provenance, and the invariant that every carried amount is a recorded
    carryover holds."""
    inputs = {**household(), "w2s": [{"owner": "taxpayer", "wages": "60000"}],
              "prior_year": {"nonrecaptured_1231_losses": [{"tax_year": 2024, "nonrecaptured_loss": "500"}]},
              "dispositions": [{"description": "Lot", "acquired": "2020-01-01", "sold": "2026-03-01", "gross_sales_price": "8000", "cost_or_basis": "10000",
                                "depreciation_allowed": "0", "property_class": "land"}]}
    assert carryovers(inputs) == ["prior_year.nonrecaptured_1231_losses[0].nonrecaptured_loss"]
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", inputs)
    R.compute(rid, "maya")
    v = R.latest(rid)
    assert v["result"]["forms"]["f4797"]["11"] == "-2000" and v["result"]["carryforwards"] == {
        "nonrecaptured_1231_loss_2024": "500", "nonrecaptured_1231_loss_2026": "2000", "capital_loss_carryover_short": "0", "capital_loss_carryover_long": "0"}
    rows = [tuple(x) for x in fam.conn.execute("SELECT kind, detail FROM return_carryforwards WHERE return_id = ? AND version = ? ORDER BY kind, detail",
                                               (rid, v["version"])).fetchall()]
    assert rows == [("capital_loss_carryover_long", ""), ("capital_loss_carryover_short", ""), ("nonrecaptured_1231_loss", "2024"), ("nonrecaptured_1231_loss", "2026")]
    assert R.carryforwards(rid) == {"capital_loss_carryover_long": Decimal(0), "capital_loss_carryover_short": Decimal(0),
                                    "nonrecaptured_1231_loss_2024": Decimal(500), "nonrecaptured_1231_loss_2026": Decimal(2000)}
    out = R.roll_forward(rid, require_filed=False)
    assert out["prior_year"]["nonrecaptured_1231_losses"] == [{"tax_year": 2024, "nonrecaptured_loss": "500"}, {"tax_year": 2026, "nonrecaptured_loss": "2000"}]
    assert "nonrecaptured_1231_loss_2026" not in out["prior_year"]
    assert out["provenance"]["prior_year.nonrecaptured_1231_losses[1].nonrecaptured_loss"] == {
        "source": "return", "return_id": rid, "version": v["version"], "box": "nonrecaptured_1231_loss_2026", "value": "2000", "confirmed": False}
    IndividualReturn.model_validate({**household(), "tax_year": 2027, "prior_year": out["prior_year"]})
    assert carryovers({"prior_year": out["prior_year"]}) == ["prior_year.nonrecaptured_1231_losses[0].nonrecaptured_loss",
                                                             "prior_year.nonrecaptured_1231_losses[1].nonrecaptured_loss"]


def test_kind_and_year_round_trip():
    assert carryforward_row("nonrecaptured_1231_loss_2026") == ("nonrecaptured_1231_loss", "2026")
    assert carryforward_name("nonrecaptured_1231_loss", "2026") == "nonrecaptured_1231_loss_2026"
    assert carryforward_row("ftc_carryover_passive_2026") == ("ftc_carryover_passive_2026", "")      # not a per-year kind (yet)
    assert carryforward_row("capital_loss_carryover_long") == ("capital_loss_carryover_long", "")


def test_the_store_reads_the_clients_asset_register(fam):  # noqa: F811
    """A disposition naming a registered asset is figured from the register through Returns.compute (ledger assets table)."""
    ledger.add_asset(fam.conn, "rivera", Asset("truck-01", Decimal(10000), date(2024, 3, 1), date(2024, 3, 1), 5, 5), "Delivery truck")
    inputs = {**household(), "w2s": [{"owner": "taxpayer", "wages": "60000"}], "prior_year": {},
              "dispositions": [{"description": "Truck", "sold": "2026-05-15", "gross_sales_price": "7000", "asset_id": "truck-01"}]}
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", inputs)
    R.compute(rid, "maya")
    res = R.latest(rid)["result"]
    assert res["forms"]["f4797"]["22A"] == "8464" and res["forms"]["f4797"]["18b"] == "5464" and res["facts"]["f4797"]["19A"]["source"] == "asset register truck-01"
    assert not [d for d in res["diagnostics"] if d["severity"] == "error"]
