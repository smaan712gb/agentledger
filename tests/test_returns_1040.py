"""Form 1040 (TY2026) engine. Expected values are worked by hand from Rev. Proc. 2025-32,
the IRC as amended by P.L. 119-21, and the 2026 draft forms, independently of the engine."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from agentledger.calc.engine import Ctx
from agentledger.kb.store import KnowledgeBase
from agentledger.returns import tax as T
from agentledger.returns.individual import compute_individual
from agentledger.returns.model import (Business, CapitalTransaction, CarLoan, Dependent, Dividends, IndividualReturn,
                                   Interest, Itemized, Person, Retirement, SocialSecurity, Student, W2)

REPO = Path(__file__).resolve().parent.parent
KB = KnowledgeBase(REPO / "rules")


def ctx():
    return Ctx(KB)


def you(**kw):
    return Person(first_name="Alex", last_name="Rivera", ssn="400-00-0001", dob=date(1985, 6, 1), **kw)


def spouse(**kw):
    return Person(first_name="Sam", last_name="Rivera", ssn="400-00-0002", dob=date(1986, 3, 1), **kw)


def kid(name, born, **kw):
    return Dependent(first_name=name, last_name="Rivera", ssn="400-00-0100", dob=born, relationship="daughter", **kw)


def run(**kw):
    return compute_individual(ctx(), IndividualReturn(tax_year=2026, **kw))


# Rev. Proc. 2025-32 §4.01: "The Tax Is" base amount at the start of each bracket.
PUBLISHED_BASES = {
    "mfj": ["2480", "11600", "35932", "82048", "116896", "206583.50"],
    "hoh": ["1770", "7740", "16155", "39207", "56631", "191171"],
    "single": ["1240", "5800", "17966", "41024", "58448", "192979.25"],
    "mfs": ["1240", "5800", "17966", "41024", "58448", "103291.75"],
}


@pytest.mark.parametrize("status", sorted(PUBLISHED_BASES))
def test_rate_schedule_reproduces_published_tables(status):
    floors = [f for f, _ in T.brackets(ctx(), 2026, status)][1:]
    got = [T.schedule_tax(ctx(), f, 2026, status).normalize() for f in floors]
    assert got == [Decimal(x).normalize() for x in PUBLISHED_BASES[status]]


def test_tax_table_midpoints():
    assert T.table_midpoint(Decimal(4)) == 0
    assert T.table_midpoint(Decimal(12)) == 10
    assert T.table_midpoint(Decimal(30)) == Decimal("37.5")
    assert T.table_midpoint(Decimal(43900)) == 43925
    assert T.regular_tax(ctx(), Decimal(43900), 2026, "single") == 5023


def test_single_wage_earner_refund():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000, federal_withholding=6000)])
    assert r.line("f1040", "11a") == 60000
    assert r.line("f1040", "12e") == 16100
    assert r.line("f1040", "15") == 43900
    assert r.line("f1040", "16") == 5023
    assert r.refund == 977
    assert not r.blocking


def test_mfj_two_children_ctc():
    r = run(filing_status="mfj", taxpayer=you(), spouse=spouse(),
            dependents=[kid("Ana", date(2021, 2, 1)), kid("Bea", date(2018, 9, 1))],
            w2s=[W2(wages=85000, federal_withholding=2000)])
    assert r.line("f1040", "15") == 52800
    assert r.line("f1040", "16") == 5843
    assert r.line("sch_8812", "5") == 4400
    assert r.line("f1040", "19") == 4400
    assert r.line("f1040", "22") == 1443
    assert r.line("f1040", "27a") == 0  # above the EIC completed phase-out
    assert r.amount_owed == 0 and r.refund == 557


def test_hoh_eic_actc_and_schedule_3a():
    r = run(filing_status="hoh", taxpayer=you(), dependents=[kid("Ana", date(2022, 1, 15))],
            w2s=[W2(wages=25000, federal_withholding=1000, ss_wages=25000, ss_tax=1550,
                     medicare_wages=25000, medicare_tax=362.5)])
    assert r.line("f1040", "15") == 850
    assert r.line("f1040", "16") == 86
    assert r.line("f1040", "19") == 86
    assert r.line("f1040", "28") == 1700          # ACTC: min(2,114 unused, $1,700, 15% x 22,500)
    assert r.line("f1040", "27a") == 4246         # EIC table midpoint 25,025
    assert r.line("sch_3a", "6") == 5946          # refundable credits above income tax
    assert r.line("f1040", "32b") == 0            # citizen: keeps the federal public benefit
    assert r.refund == 6946


def test_schedule_3a_withholds_benefit_when_not_eligible():
    r = run(filing_status="hoh", taxpayer=you(), dependents=[kid("Ana", date(2022, 1, 15))],
            w2s=[W2(wages=25000, federal_withholding=1000)], citizen_or_qualified_alien=False)
    assert r.line("f1040", "32b") == 5946
    assert r.refund == 1000


def test_self_employed_se_tax_and_qbi():
    r = run(filing_status="single", taxpayer=you(),
            businesses=[Business(name="Rivera Design", gross_receipts=100000, expenses={"supplies": 20000})])
    assert r.line("sch_c[1]", "31") == 80000
    assert r.line("sch_se[taxpayer]", "4a") == 73880
    assert r.line("sch_se[taxpayer]", "12") == 11304
    assert r.line("sch_1", "15") == 5652
    assert r.line("f1040", "11a") == 74348
    assert r.line("f8995", "15") == 11650         # limited to 20% of taxable income before QBI
    assert r.line("f1040", "15") == 46598
    assert r.line("f1040", "16") == 5341
    assert r.line("f1040", "24a") == 5341 + 11304


def test_qualified_dividends_at_zero_rate():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)],
            dividends=[Dividends(ordinary=10000, qualified=10000)])
    assert r.line("f1040", "15") == 43900
    assert r.line("f1040", "16") == 3823


def test_seniors_social_security_and_senior_deduction():
    old_you = Person(first_name="Pat", ssn="400-00-0003", dob=date(1960, 5, 1))
    old_sp = Person(first_name="Lee", ssn="400-00-0004", dob=date(1959, 8, 1))
    r = run(filing_status="mfj", taxpayer=old_you, spouse=old_sp,
            social_security=[SocialSecurity(net_benefits=40000)],
            retirement=[Retirement(gross_distribution=50000, taxable_amount=50000)])
    assert r.line("f1040", "6b") == 28100
    assert r.line("f1040", "11a") == 78100
    assert r.line("f1040", "12e") == 35500        # 32,200 + 2 x 1,650
    assert r.line("sch_1a", "43") == 12000
    assert r.line("f1040", "15") == 30600
    assert r.line("f1040", "16") == 3179


def test_no_tax_on_tips():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=45000, box12={"TP": 12000}, tipped_occupation_code=101)])
    assert r.line("sch_1a", "15") == 12000
    assert r.line("f1040", "15") == 16900
    assert r.line("f1040", "16") == 1783


def test_tips_overtime_and_car_loan_phaseouts():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=180000, box12={"TP": 20000, "TT": 5000}, tipped_occupation_code=101)])
    assert r.line("sch_1a", "14") == 3000         # 30 full $1,000 steps x $100
    assert r.line("sch_1a", "15") == 17000
    assert r.line("sch_1a", "27") == 2000
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=105500)],
            car_loans=[CarLoan(vin="1HGCM82633A004352", interest_paid=8000)])
    assert r.line("sch_1a", "35") == 1200         # 5.5 -> 6 steps (or portion thereof) x $200
    assert r.line("sch_1a", "36") == 6800


def test_tips_not_allowed_married_separately():
    r = run(filing_status="mfs", taxpayer=you(), spouse=spouse(), w2s=[W2(wages=40000, box12={"TP": 5000}, tipped_occupation_code=101)])
    assert r.line("f1040", "13a") == 0
    assert any(d.code == "tips_mfs" for d in r.diagnostics)


def test_salt_phasedown():
    it = Itemized(state_local_income_tax=40000, real_estate_tax=10000, mortgage_interest_1098=30000)
    r = run(filing_status="mfj", taxpayer=you(), spouse=spouse(), w2s=[W2(wages=600000)], itemized=it)
    assert r.line("sch_a", "5e") == 11900         # 40,400 - 30% x 95,000
    r = run(filing_status="mfs", taxpayer=you(), spouse=spouse(), w2s=[W2(wages=300000)], itemized=it)
    assert r.line("sch_a", "5e") == 13075         # half of (40,400 - 30% x 47,500)


def test_amt_on_incentive_stock_options():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=200000)], amt_adjustments={"iso": 300000})
    assert r.line("f1040", "16") == 36734
    assert r.line("f6251", "4") == 500000
    assert r.line("f6251", "5") == 90100
    assert r.line("f6251", "7") == 109882
    assert r.line("sch_2", "2") == 73148


def test_dependent_care_credit_obbba_rate():
    r = run(filing_status="hoh", taxpayer=you(), dependents=[kid("Ana", date(2022, 1, 15))],
            w2s=[W2(wages=30000)], dependent_care_expenses=3000)
    assert r.sheets.facts["f2441"]["8"] == "0.42"  # 50% less 8 points (7.5 steps of $2,000 over $15,000)
    assert r.line("f2441", "9a") == 1260


def test_aotc_phaseout():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=85000)],
            students=[Student(name="Alex", qualified_expenses=4000)])
    assert r.line("f8863", "7") == 1250
    assert r.line("f1040", "29") == 500
    assert r.line("sch_3", "3") == 750


def test_capital_loss_limited():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)],
            capital_transactions=[CapitalTransaction(description="XYZ", acquired=date(2026, 1, 5), sold=date(2026, 3, 1),
                                                     proceeds=5000, cost_basis=15000)])
    assert r.line("sch_d", "7") == -10000
    assert r.line("f1040", "7a") == -3000
    assert r.line("f1040", "11a") == 47000


def test_niit_and_additional_medicare():
    r = run(filing_status="single", taxpayer=you(),
            w2s=[W2(wages=250000, medicare_wages=250000, medicare_tax=3625)], interest=[Interest(interest=50000)])
    assert r.line("f8960", "17") == 1900
    assert r.line("f8959", "7") == 450
    assert r.line("sch_2", "17b") == 450


def test_unsupported_items_block_filing():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)],
            dividends=[Dividends(ordinary=20000, qualified=20000, foreign_tax_paid=900)])
    assert any(d.code == "form_1116_required" for d in r.blocking)


def test_trace_cites_authorities():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)])
    used = {s["rule_id"] for s in r.sources}
    assert "us_fed.individual.tax_brackets" in used and "us_fed.individual.standard_deduction" in used
    assert all(s["source"] for s in r.sources)


def test_w2_box_mismatch_is_flagged():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=25000, medicare_tax=362.5)])
    assert any(d.code == "w2_box_mismatch" for d in r.diagnostics)


def test_tips_need_listed_occupation_and_not_sstb():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=45000, box12={"TP": 12000})])
    assert r.line("f1040", "13a") == 0
    assert any(d.code == "tips_occupation_code" for d in r.diagnostics)
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=45000, box12={"TP": 12000}, tipped_occupation_code=101,
                                                           employer_sstb=True)])
    assert r.line("f1040", "13a") == 0
