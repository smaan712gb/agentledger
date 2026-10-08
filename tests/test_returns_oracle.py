"""Two-engine verification: AgentLedger returns against PolicyEngine US (skipped if not installed)."""

from datetime import date

import pytest

pytest.importorskip("policyengine_us")

from test_returns_1040 import ctx, kid, spouse, you  # noqa: E402

from agentledger.returns.individual import compute_individual  # noqa: E402
from agentledger.returns.model import (Business, CapitalTransaction, CarLoan, Dividends, IndividualReturn, Interest,  # noqa: E402
                                   Person, Retirement, SocialSecurity, W2)
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
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_agrees_with_policyengine(name):
    ret = IndividualReturn(tax_year=2026, **CASES[name])
    cc = crosscheck(ret, compute_individual(ctx(), ret))
    assert len(cc.compared) >= 10
    assert cc.agrees, [(d.item, str(d.agentledger), str(d.policyengine)) for d in cc.discrepancies]
