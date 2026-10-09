"""Two-engine verification: AgentLedger returns against PolicyEngine US (skipped if not installed)."""

from datetime import date

import pytest

pytest.importorskip("policyengine_us")

from test_returns_1040 import ctx, kid, spouse, you  # noqa: E402

from agentledger.returns.individual import compute_individual  # noqa: E402
from agentledger.returns.model import (Business, CapitalTransaction, CarLoan, Dividends, IndividualReturn, Interest,  # noqa: E402
                                   IRAAccount, Person, PriorYear, Retirement, RetirementSavings, SocialSecurity, W2)
from agentledger.returns.oracle import crosscheck  # noqa: E402

CASES = {
    "single_wages": dict(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000, federal_withholding=6000)]),
    "mfj_two_children": dict(filing_status="mfj", taxpayer=you(), spouse=spouse(),
                             dependents=[kid("Ana", date(2021, 2, 1)), kid("Bea", date(2018, 9, 1))], w2s=[W2(wages=85000)]),
    "hoh_eic": dict(filing_status="hoh", taxpayer=you(), dependents=[kid("Ana", date(2022, 1, 15))],
                    w2s=[W2(wages=25000, ss_wages=25000, medicare_wages=25000)]),
    "mfj_eic_three_children": dict(filing_status="mfj", taxpayer=you(), spouse=spouse(),
                                   dependents=[kid("A", date(2016, 1, 1)), kid("B", date(2019, 1, 1)), kid("C", date(2023, 1, 1))],
                                   w2s=[W2(wages=38000, ss_wages=38000, medicare_wages=38000)]),
    "self_employed": dict(filing_status="single", taxpayer=you(),
                          businesses=[Business(name="D", gross_receipts=100000, expenses={"supplies": 20000})]),
    "qualified_dividends": dict(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)],
                                dividends=[Dividends(ordinary=10000, qualified=10000)]),
    "seniors": dict(filing_status="mfj", taxpayer=Person(ssn="1", dob=date(1960, 5, 1)), spouse=Person(ssn="2", dob=date(1959, 8, 1)),
                    social_security=[SocialSecurity(net_benefits=40000)],
                    retirement=[Retirement(gross_distribution=50000, taxable_amount=50000)]),
    "tips_and_overtime": dict(filing_status="single", taxpayer=you(),
                              w2s=[W2(wages=180000, box12={"TP": 20000, "TT": 5000}, tipped_occupation_code=101)]),
    "vehicle_loan": dict(filing_status="single", taxpayer=you(), w2s=[W2(wages=105500)],
                         car_loans=[CarLoan(vin="1HGCM82633A004352", interest_paid=8000)]),
    "niit": dict(filing_status="single", taxpayer=you(), w2s=[W2(wages=250000, medicare_wages=250000)],
                 interest=[Interest(interest=50000)]),
    "long_term_gain": dict(filing_status="mfj", taxpayer=you(), spouse=spouse(), w2s=[W2(wages=300000, medicare_wages=300000)],
                           capital_transactions=[CapitalTransaction(description="ABC", acquired=date(2020, 1, 1),
                                                                    sold=date(2026, 5, 1), proceeds=400000, cost_basis=100000)]),
    # Capital loss carryovers from 2025 (prior_year group): 2,000 short-term and 10,000 long-term net to a 12,000 loss,
    # 3,000 of which is deducted (Schedule D line 21; PolicyEngine limited_capital_loss).
    "capital_loss_carryover": dict(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)],
                                   prior_year=PriorYear(capital_loss_carryover_short=2000, capital_loss_carryover_long=10000)),
    # Form 8880: 3,000 of pre-tax 401(k) deferrals (code D), single, AGI 30,000 in the 10% band: a 200 credit.
    "savers_credit": dict(filing_status="single", taxpayer=you(full_time_student=False), w2s=[W2(wages=30000, box12={"D": 3000})],
                          retirement_savings=[RetirementSavings(owner="taxpayer", testing_period_distributions=0)]),
    # Joint, Roth deferrals (code AA) and Roth IRA contributions (5498 box 10) for the spouse, AGI 60,000 in the 10% band:
    # 2,000 capped per person, a 400 credit.
    "savers_credit_joint_roth": dict(filing_status="mfj", taxpayer=you(full_time_student=False), spouse=spouse(full_time_student=False),
                                     w2s=[W2(owner="taxpayer", wages=40000, box12={"AA": 4000}), W2(owner="spouse", wages=20000)],
                                     ira_accounts=[IRAAccount(owner="spouse", roth_contributions=3000, fmv=20000)],
                                     retirement_savings=[RetirementSavings(owner="taxpayer", testing_period_distributions=0),
                                                         RetirementSavings(owner="spouse", testing_period_distributions=0)]),
    # Form 1116: wages 60,000, a foreign bank's 10,000 of interest with 1,500 of foreign tax; the §904(a) limitation allows 939.
    # PolicyEngine credits min(1,500, tax before credits) = 1,500: an upper bound the engine must stay under.
    "foreign_tax_credit": dict(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)],
                               interest=[Interest(payer="Hanse Bank", interest=10000, foreign_tax_paid=1500, foreign_source_income=10000,
                                                  foreign_country="Germany")], prior_year=PriorYear(ftc_excess_limitation={"passive": 0})),
    # 85 of foreign tax within the §904(j) de minimis amount: credited in full without Form 1116, as PolicyEngine does.
    "foreign_tax_credit_de_minimis": dict(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)],
                                          dividends=[Dividends(ordinary=2400, qualified=2000, foreign_tax_paid=85)]),
}
# What the engine must show for the new items before the cross-check counts as evidence (hand-worked, see
# tests/test_returns_capital_loss_carryover.py, tests/test_returns_8880.py and tests/test_returns_1116.py).
EXPECTED = {"capital_loss_carryover": ("sch_d", "21", -3000), "savers_credit": ("sch_3", "4", 200), "savers_credit_joint_roth": ("sch_3", "4", 400),
            "foreign_tax_credit": ("sch_3", "1", 939), "foreign_tax_credit_de_minimis": ("sch_3", "1", 85)}
LABELS = {"capital_loss_carryover": "capital loss deduction", "savers_credit": "saver's credit", "savers_credit_joint_roth": "saver's credit",
          "foreign_tax_credit": "foreign tax credit (upper bound)", "foreign_tax_credit_de_minimis": "foreign tax credit (upper bound)"}


@pytest.mark.parametrize("name", sorted(CASES))
def test_agrees_with_policyengine(name):
    ret = IndividualReturn(tax_year=2026, **CASES[name])
    ours = compute_individual(ctx(), ret)
    if name in EXPECTED:
        form, line, value = EXPECTED[name]
        assert ours.line(form, line) == value
    cc = crosscheck(ret, ours)
    assert len(cc.compared) >= 10
    assert cc.agrees, [(d.item, str(d.agentledger), str(d.policyengine)) for d in cc.discrepancies]
    if name in EXPECTED:
        label = LABELS[name]
        assert label in cc.compared and cc.compared[label][0] != 0            # the item itself was compared, not just AGI
        assert not any("capital loss carryovers" in u for u in cc.unmodelled)
    if name.startswith("foreign_tax_credit"):
        mine, theirs = cc.compared["foreign tax credit (upper bound)"]
        assert mine <= theirs and any("foreign tax credit limitation" in u for u in cc.unmodelled)
