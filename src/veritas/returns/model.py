"""Inputs to an individual income tax return.

These are facts and source documents, never computed amounts: the engine derives
every line. Field names follow the source document (W-2 box 1 is `wages`, 1099-INT
box 1 is `interest`) so intake can map extracted documents onto them mechanically.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Money = Decimal
Owner = Literal["taxpayer", "spouse"]
FilingStatus = Literal["single", "mfj", "mfs", "hoh", "qss"]
Z = Decimal(0)


class Person(BaseModel):
    first_name: str = ""
    last_name: str = ""
    ssn: str | None = None
    # Valid for employment and issued before the return due date (IRC §24(h)(7)).
    ssn_valid_for_employment: bool = True
    dob: date | None = None
    blind: bool = False
    can_be_claimed_as_dependent: bool = False
    occupation: str = ""


class Dependent(BaseModel):
    first_name: str
    last_name: str = ""
    ssn: str | None = None
    tin_type: Literal["ssn", "itin", "atin", "none"] = "ssn"
    ssn_valid_for_employment: bool = True
    dob: date
    relationship: Literal[
        "son", "daughter", "stepchild", "foster_child", "brother", "sister", "half_brother", "half_sister",
        "stepbrother", "stepsister", "grandchild", "niece", "nephew", "parent", "grandparent", "aunt", "uncle",
        "in_law", "other_household_member"]
    months_in_home: int = Field(12, ge=0, le=12)
    lived_in_us_over_half_year: bool = True
    full_time_student: bool = False
    permanently_disabled: bool = False
    provided_over_half_own_support: bool = False
    files_joint_return: bool = False
    gross_income: Money = Z  # qualifying-relative test
    taxpayer_provided_over_half_support: bool = True  # qualifying-relative test
    qualifying_child_of_another_taxpayer: bool = False


class W2(BaseModel):
    owner: Owner = "taxpayer"
    employer_name: str = ""
    employer_ein: str = ""
    wages: Money = Z                     # box 1
    federal_withholding: Money = Z       # box 2
    ss_wages: Money = Z                  # box 3
    ss_tax: Money = Z                    # box 4
    medicare_wages: Money = Z            # box 5
    medicare_tax: Money = Z              # box 6
    ss_tips: Money = Z                   # box 7
    dependent_care_benefits: Money = Z   # box 10
    box12: dict[str, Money] = {}         # code -> amount (e.g. "D": 401(k) deferral, "TP": qualified tips, "TT": overtime)
    retirement_plan: bool = False        # box 13
    statutory_employee: bool = False     # box 13
    qualified_tips: Money | None = None  # box 12 code TP, if not in box12
    qualified_overtime: Money | None = None  # box 12 code TT, if not in box12

    def tips(self) -> Money:
        return self.qualified_tips if self.qualified_tips is not None else self.box12.get("TP", Z)

    def overtime(self) -> Money:
        return self.qualified_overtime if self.qualified_overtime is not None else self.box12.get("TT", Z)


class Interest(BaseModel):  # 1099-INT
    owner: Owner = "taxpayer"
    payer: str = ""
    interest: Money = Z                  # box 1
    early_withdrawal_penalty: Money = Z  # box 2
    us_savings_bond_interest: Money = Z  # box 3
    federal_withholding: Money = Z       # box 4
    foreign_tax_paid: Money = Z          # box 6
    tax_exempt_interest: Money = Z       # box 8
    private_activity_bond_interest: Money = Z  # box 9


class Dividends(BaseModel):  # 1099-DIV
    owner: Owner = "taxpayer"
    payer: str = ""
    ordinary: Money = Z                  # box 1a
    qualified: Money = Z                 # box 1b
    capital_gain_distributions: Money = Z  # box 2a
    unrecaptured_1250_gain: Money = Z    # box 2b
    section_1202_gain: Money = Z         # box 2c
    collectibles_gain: Money = Z         # box 2d
    nondividend_distributions: Money = Z  # box 3
    federal_withholding: Money = Z       # box 4
    section_199a_dividends: Money = Z    # box 5
    foreign_tax_paid: Money = Z          # box 7
    exempt_interest_dividends: Money = Z  # box 12
    private_activity_bond_dividends: Money = Z  # box 13


class CapitalTransaction(BaseModel):  # 1099-B / 1099-DA / 8949 row
    owner: Owner = "taxpayer"
    description: str
    acquired: date | Literal["various", "inherited"] | None = None
    sold: date
    proceeds: Money
    cost_basis: Money
    adjustment: Money = Z                # column (g); positive increases gain (e.g. wash sale)
    adjustment_codes: str = ""
    term: Literal["short", "long"] | None = None  # derived from dates when omitted
    basis_reported_to_irs: bool = True
    form_1099_received: bool = True
    collectible: bool = False
    federal_withholding: Money = Z


class Retirement(BaseModel):  # 1099-R
    owner: Owner = "taxpayer"
    payer: str = ""
    gross_distribution: Money = Z        # box 1
    taxable_amount: Money | None = None  # box 2a (None = not determined)
    federal_withholding: Money = Z       # box 4
    distribution_code: str = "7"         # box 7
    ira_sep_simple: bool = False
    rollover_amount: Money = Z           # portion rolled over within 60 days
    qcd_amount: Money = Z                # qualified charitable distribution
    early_distribution_exception: Money = Z  # amount covered by a §72(t) exception


class SocialSecurity(BaseModel):  # SSA-1099 / RRB-1099
    owner: Owner = "taxpayer"
    net_benefits: Money = Z              # box 5
    federal_withholding: Money = Z       # box 6


class Unemployment(BaseModel):  # 1099-G box 1
    owner: Owner = "taxpayer"
    amount: Money = Z
    federal_withholding: Money = Z


class Business(BaseModel):  # Schedule C
    owner: Owner = "taxpayer"
    name: str
    ein: str = ""
    principal_business_code: str = ""
    accounting_method: Literal["cash", "accrual", "other"] = "cash"
    gross_receipts: Money = Z
    returns_allowances: Money = Z
    cost_of_goods_sold: Money = Z
    other_income: Money = Z
    expenses: dict[str, Money] = {}      # Schedule C Part II categories, see SCHEDULE_C_LINES
    meals: Money = Z                     # business meals before the 50% limit
    home_office_sqft: int = 0            # simplified method
    materially_participates: bool = True
    sstb: bool = False                   # specified service trade or business (§199A(d)(2))
    w2_wages: Money = Z                  # for §199A wage limitation
    ubia: Money = Z                      # unadjusted basis of qualified property
    federal_withholding: Money = Z       # backup withholding on 1099-NEC/K
    qualified_tips: Money = Z            # tips received in the business (Schedule 1-A Part II)


SCHEDULE_C_LINES = {
    "advertising": "8", "car_truck": "9", "commissions_fees": "10", "contract_labor": "11", "depletion": "12",
    "depreciation": "13", "employee_benefits": "14", "insurance": "15", "mortgage_interest": "16a",
    "other_interest": "16b", "legal_professional": "17", "office": "18", "pension_profit_sharing": "19",
    "rent_vehicles_equipment": "20a", "rent_other": "20b", "repairs": "21", "supplies": "22",
    "taxes_licenses": "23", "travel": "24a", "utilities": "25", "wages": "26", "energy_efficient_buildings": "27a",
    "other": "27b",
}


class Rental(BaseModel):  # Schedule E Part I
    owner: Owner = "taxpayer"
    address: str
    property_type: Literal["single_family", "multi_family", "vacation", "commercial", "land", "royalties", "self_rental",
                           "short_term", "other"] = "single_family"
    fair_rental_days: int = 365
    personal_use_days: int = 0
    rents: Money = Z
    royalties: Money = Z
    expenses: dict[str, Money] = {}
    depreciation: Money = Z
    active_participation: bool = True
    real_estate_professional: bool = False
    prior_year_unallowed_loss: Money = Z


class K1(BaseModel):  # Schedule E Part II
    owner: Owner = "taxpayer"
    entity_name: str
    entity_ein: str = ""
    entity_type: Literal["partnership", "s_corporation", "estate_trust"] = "partnership"
    passive: bool = False
    ordinary_income: Money = Z
    net_rental_income: Money = Z
    interest: Money = Z
    ordinary_dividends: Money = Z
    qualified_dividends: Money = Z
    net_short_term_gain: Money = Z
    net_long_term_gain: Money = Z
    guaranteed_payments: Money = Z
    section_179: Money = Z
    self_employment_earnings: Money = Z
    qbi: Money | None = None             # defaults to ordinary income + net rental + guaranteed payments excluded
    w2_wages: Money = Z
    ubia: Money = Z
    sstb: bool = False


class CarLoan(BaseModel):
    vin: str
    interest_paid: Money
    qualifies: bool = True               # new, US final assembly, personal use, first lien, loan after 2024


class Student(BaseModel):
    name: str
    ssn: str | None = None
    qualified_expenses: Money = Z        # net of tax-free assistance
    aotc_eligible: bool = True           # first 4 years, at least half-time, no felony drug conviction
    aotc_years_claimed: int = 0


class Itemized(BaseModel):
    medical: Money = Z
    state_local_income_tax: Money = Z
    general_sales_tax: Money = Z
    use_sales_tax: bool = False
    real_estate_tax: Money = Z
    personal_property_tax: Money = Z
    other_taxes: Money = Z
    mortgage_interest_1098: Money = Z
    mortgage_interest_other: Money = Z
    points_not_on_1098: Money = Z
    mortgage_insurance_premiums: Money = Z
    investment_interest: Money = Z
    charity_cash: Money = Z
    charity_noncash: Money = Z
    charity_carryover: Money = Z
    casualty_loss: Money = Z
    other: Money = Z
    force_itemize: bool = False


class Adjustments(BaseModel):
    educator_expenses_taxpayer: Money = Z
    educator_expenses_spouse: Money = Z
    hsa_deduction: Money = Z             # from Form 8889 line 13
    self_employed_retirement: Money = Z  # SEP, SIMPLE, qualified plans
    self_employed_health_insurance: Money = Z
    alimony_paid: Money = Z              # pre-2019 instruments only
    alimony_recipient_ssn: str = ""
    ira_deduction: Money = Z
    student_loan_interest_paid: Money = Z
    other: Money = Z


class Payments(BaseModel):
    estimated_tax_payments: Money = Z
    prior_year_overpayment_applied: Money = Z
    extension_payment: Money = Z
    other_withholding: Money = Z
    apply_to_next_year: Money = Z


class IndividualReturn(BaseModel):
    tax_year: int
    filing_status: FilingStatus
    taxpayer: Person
    spouse: Person | None = None
    mfs_lived_apart_all_year: bool = False
    mfs_spouse_itemizes: bool = False
    dependents: list[Dependent] = []
    w2s: list[W2] = []
    interest: list[Interest] = []
    dividends: list[Dividends] = []
    capital_transactions: list[CapitalTransaction] = []
    capital_loss_carryover_short: Money = Z
    capital_loss_carryover_long: Money = Z
    retirement: list[Retirement] = []
    social_security: list[SocialSecurity] = []
    unemployment: list[Unemployment] = []
    state_refund_taxable: Money = Z      # taxable portion under the tax benefit rule
    alimony_received: Money = Z
    businesses: list[Business] = []
    rentals: list[Rental] = []
    k1s: list[K1] = []
    other_income: dict[str, Money] = {}  # Schedule 1 line 8 items, keyed by line letter or label
    qbi_loss_carryforward: Money = Z
    reit_ptp_loss_carryforward: Money = Z
    adjustments: Adjustments = Adjustments()
    itemized: Itemized = Itemized()
    car_loans: list[CarLoan] = []
    tips_form_4137: Money = Z            # qualified tips reported on Form 4137
    dependent_care_expenses: Money = Z
    dependent_care_qualifying_persons: int = 0
    students: list[Student] = []
    retirement_savings_contributions: dict[Owner, Money] = {}
    amt_adjustments: dict[str, Money] = {}  # Form 6251 preference items, e.g. "iso": bargain element
    excess_aptc_repayment: Money = Z     # from Form 8962
    net_premium_tax_credit: Money = Z    # from Form 8962
    household_employment_taxes: Money = Z
    payments: Payments = Payments()
    claim_eic: bool = True
    citizen_or_qualified_alien: bool = True  # Schedule 3-A Part II
    want_federal_public_benefit: bool = True
