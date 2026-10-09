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
# Form 1116 separate categories (IRC §904(d)(1); Form 1116, boxes a-g above Part I).
FTCCategory = Literal["passive", "general", "foreign_branch", "section_951a", "treaty", "lump_sum", "section_901j"]
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
    # Enrolled full time for some part of 5 calendar months of the year (Form 8880 instructions, "student"). None: not
    # stated; the saver's credit is never claimed for a person whose student status is unknown, and Form 8615's age test
    # for a child of 19 to 23 (IRC §1(g)(2)(A)(ii), §152(c)(3)(A)(ii)) is not decided.
    full_time_student: bool | None = None
    # Form 8615 (IRC §1(g)(2)(A)(ii)(II)): whether the person's earned income was more than half of their own support for
    # the year. Asked of a child who was 18, or a full-time student of 19 to 23, at the end of the year when the tax at the
    # parent's rate or the IRC §59(j) AMT exemption limit could apply. None: not stated; the return blocks while it is unknown.
    support_from_earned_income_over_half: bool | None = None
    # Form 8615 (IRC §1(g)(2)(B)): at least one of the person's parents was alive at the end of the year. None: not stated.
    has_living_parent: bool | None = None
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
    tipped_occupation_code: int | None = None  # Treasury Tipped Occupation Code (W-2 box 14b; prop. Reg. §1.224-1(f))
    employer_sstb: bool = False          # tips from an SSTB employer do not qualify (IRC §224(d)(2)(B))

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
    foreign_country: str = ""            # box 7, foreign country or U.S. territory ("RIC" for a mutual fund)
    tax_exempt_interest: Money = Z       # box 8
    private_activity_bond_interest: Money = Z  # box 9
    # Form 1116 facts the payer's supplemental statement carries (no 1099 box): the foreign-source part of boxes 1 and 3.
    # None: not stated, never zero. A credit beyond the §904(j) de minimis cannot be figured without it.
    foreign_source_income: Money | None = None
    category: FTCCategory = "passive"    # §904(d) separate category of the foreign-source income (interest is passive)
    accrued: bool = False                # the foreign tax is accrued rather than paid (Form 1116 Part II box (i); §905(a))


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
    foreign_country: str = ""            # box 8, foreign country or U.S. territory ("RIC" for a mutual fund)
    exempt_interest_dividends: Money = Z  # box 12
    private_activity_bond_dividends: Money = Z  # box 13
    # Form 1116 facts from the payer's supplemental statement (no 1099 box): the foreign-source part of box 1a. None: not
    # stated, never zero. A credit beyond the §904(j) de minimis cannot be figured without it.
    foreign_source_income: Money | None = None
    category: FTCCategory = "passive"    # §904(d) separate category of the foreign-source income (dividends are passive)
    accrued: bool = False                # the foreign tax is accrued rather than paid (Form 1116 Part II box (i); §905(a))


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


class IRAAccount(BaseModel):  # Form 5498, one per account
    """Amounts default to None, not zero: a box the trustee left blank is unknown until a person reads the form."""
    owner: Owner = "taxpayer"
    trustee: str = ""
    ira_contributions: Money | None = None        # box 1 (traditional IRA contributions, other than boxes 2-4, 8-10)
    rollover_contributions: Money | None = None   # box 2
    roth_conversion: Money | None = None          # box 3
    recharacterized_contributions: Money | None = None  # box 4
    fmv: Money | None = None                      # box 5, fair market value at year end (Form 8606 line 6)
    account_type: Literal["ira", "sep", "simple", "roth"] | None = None  # box 7 checkboxes
    sep_contributions: Money | None = None        # box 8
    simple_contributions: Money | None = None     # box 9
    roth_contributions: Money | None = None       # box 10
    rmd_required_next_year: bool | None = None    # box 11


class HSADistribution(BaseModel):  # Form 1099-SA
    owner: Owner = "taxpayer"
    trustee: str = ""
    gross_distribution: Money | None = None       # box 1
    earnings_on_excess: Money | None = None       # box 2
    distribution_code: str | None = None          # box 3: 1 normal, 2 excess contributions, 3 disability, 4 death (code 6 cases
    #                                               excepted), 5 prohibited transaction, 6 death, nonspouse beneficiary
    fmv_on_date_of_death: Money | None = None     # box 4
    account_type: Literal["hsa", "archer_msa", "ma_msa"] | None = None  # box 5 checkboxes


class HSAContribution(BaseModel):  # Form 5498-SA
    owner: Owner = "taxpayer"
    trustee: str = ""
    archer_msa_contributions: Money | None = None  # box 1
    total_contributions: Money | None = None       # box 2, contributions made in the year
    following_year_contributions: Money | None = None  # box 3, made in the following year for this year
    rollover_contributions: Money | None = None    # box 4
    fmv: Money | None = None                       # box 5
    account_type: Literal["hsa", "archer_msa", "ma_msa"] | None = None  # box 6 checkboxes


class MarketplaceCoverage(BaseModel):  # Form 1095-A
    """Part III columns A (enrollment premiums), B (SLCSP premium) and C (advance payment of the premium tax credit), one
    field per month so that every amount has its own provenance, plus the line 33 annual totals."""
    owner: Owner = "taxpayer"                     # recipient (Part I line 4)
    marketplace: str = ""                         # Part I line 1, marketplace identifier
    policy_number: str = ""                       # line 2
    issuer: str = ""                              # line 3
    covered_individuals: list[str] = []           # Part II, names as printed
    premium_01: Money | None = None
    premium_02: Money | None = None
    premium_03: Money | None = None
    premium_04: Money | None = None
    premium_05: Money | None = None
    premium_06: Money | None = None
    premium_07: Money | None = None
    premium_08: Money | None = None
    premium_09: Money | None = None
    premium_10: Money | None = None
    premium_11: Money | None = None
    premium_12: Money | None = None
    slcsp_01: Money | None = None
    slcsp_02: Money | None = None
    slcsp_03: Money | None = None
    slcsp_04: Money | None = None
    slcsp_05: Money | None = None
    slcsp_06: Money | None = None
    slcsp_07: Money | None = None
    slcsp_08: Money | None = None
    slcsp_09: Money | None = None
    slcsp_10: Money | None = None
    slcsp_11: Money | None = None
    slcsp_12: Money | None = None
    aptc_01: Money | None = None
    aptc_02: Money | None = None
    aptc_03: Money | None = None
    aptc_04: Money | None = None
    aptc_05: Money | None = None
    aptc_06: Money | None = None
    aptc_07: Money | None = None
    aptc_08: Money | None = None
    aptc_09: Money | None = None
    aptc_10: Money | None = None
    aptc_11: Money | None = None
    aptc_12: Money | None = None
    annual_premium: Money | None = None           # line 33, column A
    annual_slcsp: Money | None = None             # line 33, column B
    annual_aptc: Money | None = None              # line 33, column C


class ForeignTaxCarryover(BaseModel):  # Form 1116 Schedule B, unused foreign tax by separate category (IRC §904(c))
    """One year's unused foreign tax carried into this year (the prior-year Schedule B, line 8, one column). `from_year` is
    the year the tax was paid or accrued: the carryover can be used in the 10 years after it and expires with the tenth
    (§904(c)); a carryover whose year is not stated cannot be placed and blocks. `amt_carryover` is the same year's unused
    AMT foreign tax credit (the AMT Form 1116's Schedule B; the AMT credit has its own carryovers, §59(a)(1)); None: not
    stated, never zero."""
    category: FTCCategory
    from_year: int | None = None
    carryover: Money
    amt_carryover: Money | None = None


class Section1231Loss(BaseModel):  # Form 4797 line 8: nonrecaptured net section 1231 losses of the 5 preceding years
    tax_year: int
    nonrecaptured_loss: Money


class PriorYear(BaseModel):
    """Amounts carried from the prior-year return (a 2025 Form 1040 for a 2026 return), with the return as their
    document. None means not stated, never zero: a line the preparer or the document did not supply is unknown, and a
    computation that needs it blocks instead of assuming zero."""
    tax: Money | None = None                      # Form 1040 line 24, total tax (Form 2210 safe harbour)
    agi: Money | None = None                      # Form 1040 line 11
    filing_status: FilingStatus | None = None
    capital_loss_carryover_short: Money | None = None  # Capital Loss Carryover Worksheet line 8, to this year's Schedule D line 6
    capital_loss_carryover_long: Money | None = None   # worksheet line 13, to Schedule D line 14
    ftc_carryovers: list[ForeignTaxCarryover] = []
    # The prior year's excess limitation by category (its Form 1116 line 23 less line 14, if positive; 0 when it had unused
    # foreign tax or no foreign-source income): the room a carryback of this year's unused foreign tax would use first
    # (§904(c)). Absent: not stated; an excess this year then blocks instead of being carried forward in full. The AMT
    # credit's own excess limitation is stated separately (§59(a)(1)).
    ftc_excess_limitation: dict[FTCCategory, Money] = {}
    ftc_amt_excess_limitation: dict[FTCCategory, Money] = {}
    # Whether the §59(a)(3) simplified limitation was elected for the AMT foreign tax credit in an earlier year (binding
    # for every later year unless revoked with the IRS's consent). None: not stated.
    amt_ftc_simplified_election: bool | None = None
    nonrecaptured_1231_losses: list[Section1231Loss] = []
    # Form 8606 is per person: the unprefixed fields are the taxpayer's, the spouse_ fields the spouse's.
    traditional_ira_basis: Money | None = None    # Form 8606 line 14 of the prior year (basis in traditional IRAs)
    spouse_traditional_ira_basis: Money | None = None
    roth_ira_basis: Money | None = None           # basis in regular Roth IRA contributions (Form 8606 line 22 before this year)
    spouse_roth_ira_basis: Money | None = None
    roth_conversion_basis: Money | None = None    # basis in conversions and plan rollovers to Roth IRAs (Form 8606 line 24 before this year)
    spouse_roth_conversion_basis: Money | None = None
    # Form 8889 Part III: the prior year's HSA contributions over the Line 3 Limitation Chart amount, allowed only by the
    # last-month rule; income (and a 10% tax) this year if the person failed the testing period (Pub 969, Example 1).
    hsa_last_month_rule_excess: Money | None = None
    spouse_hsa_last_month_rule_excess: Money | None = None


class RetirementSavings(BaseModel):
    """Form 8880 facts for one person that no information return carries. Contributions on documents (W-2 box 12,
    Form 5498 boxes 1 and 10) are taken from those documents; a 2026 distribution from a 1099-R on this return too."""
    owner: Owner = "taxpayer"
    voluntary_after_tax_contributions: Money = Z  # line 2: voluntary employee contributions to a qualified plan (IRC §4974(c))
    able_contributions: Money = Z                 # line 1: contributions by the designated beneficiary to their ABLE account
    # Line 4, the part not on this return's 1099-Rs: distributions received in the two prior years and after the year's
    # end but before the return's due date (the testing period, IRC §25B(d)(2)). None: not stated; the credit is never
    # claimed while it is unknown. Enter 0 when there were none.
    testing_period_distributions: Money | None = None


HDHPCoverage = Literal["self_only", "family", "none"]


class HSAFacts(BaseModel):
    """Form 8889 facts for one HSA beneficiary that no information return carries. Contributions come from Form 5498-SA
    (boxes 2-4) and W-2 box 12 code W, distributions from Form 1099-SA; these are the eligibility and use facts."""
    owner: Owner = "taxpayer"
    # HDHP coverage on the first day of each month (Form 8889 line 1; Line 3 Limitation Chart): "self_only", "family",
    # or "none" when the person was not an eligible individual that month (no HDHP, other coverage, someone's
    # dependent). None: not stated; the deduction is never figured while a month is unknown.
    coverage_01: HDHPCoverage | None = None
    coverage_02: HDHPCoverage | None = None
    coverage_03: HDHPCoverage | None = None
    coverage_04: HDHPCoverage | None = None
    coverage_05: HDHPCoverage | None = None
    coverage_06: HDHPCoverage | None = None
    coverage_07: HDHPCoverage | None = None
    coverage_08: HDHPCoverage | None = None
    coverage_09: HDHPCoverage | None = None
    coverage_10: HDHPCoverage | None = None
    coverage_11: HDHPCoverage | None = None
    coverage_12: HDHPCoverage | None = None
    medicare_from_month: int | None = Field(None, ge=1, le=12)  # first month enrolled in Medicare (the limit is zero from then on)
    qualified_medical_expenses: Money | None = None  # line 15; None blocks when there are distributions (never taken as zero)
    rollovers_and_withdrawn_excess: Money = Z       # line 14b: distributions rolled over, and excess withdrawn by the due date
    qualified_funding_distribution: Money = Z       # line 10: a one-time IRA-to-HSA funding distribution
    contributions_for_last_year: Money = Z          # part of 5498-SA box 2 made this year for the prior year (not this year's line 2)
    family_limit_share: Money | None = None         # line 6: this spouse's agreed share of the family limit when both have HSAs; None = equal
    testing_period_failed: bool = False             # Part III: ceased to be eligible this year, within the prior year's testing period
    # Line 17a: the part of the taxable distributions (line 16) made after the beneficiary turned 65, or because of death or
    # disability, which escape the 20% tax. Known without being stated when the person was 65 all year, or under 65 all year
    # with only normal (code 1) distributions; otherwise None blocks.
    additional_tax_exception: Money | None = None


class IRAFacts(BaseModel):
    """IRA Deduction Worksheet and Form 8606 facts for one person that no information return carries. Contributions come
    from Form 5498 (boxes 1, 3, 10), distributions from Form 1099-R, plan coverage from W-2 box 13 and 5498 boxes 8-9."""
    owner: Owner = "taxpayer"
    covered_by_employer_plan: bool | None = None     # worksheet line 1, when stated it overrides what the documents show
    nondeductible_election: Money | None = None      # Form 8606 line 1: contributions the person elects to treat as nondeductible
    #                                                  although deductible ("you can deduct a smaller amount", IRA Deduction Worksheet line 12)
    contributions_after_year_end: Money | None = None  # Form 8606 line 4: this year's contributions made January 1 - April 15 of next
    #                                                  year (part of 5498 box 1); None blocks when lines 4-13 are needed (enter 0 if none)
    outstanding_rollovers: Money = Z                 # Form 8606 line 6: distributions after November 1 rolled over next year within 60 days
    roth_five_year_period_met: bool | None = None    # Part III: a code T distribution is qualified only after the 5-year period
    first_time_homebuyer_expenses: Money = Z         # Form 8606 line 20 (lifetime $10,000)


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
    tipped_occupation_code: int | None = None


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
    # Foreign items (Schedule K-1 box 21 / Schedule K-3 Parts II and III), for Form 1116. The category is never defaulted
    # for a pass-through (its foreign-source income may be general or passive): None blocks when foreign tax is present.
    foreign_tax_paid: Money = Z
    foreign_source_income: Money | None = None   # the partner's or shareholder's share of foreign-source gross income
    foreign_country: str = ""
    category: FTCCategory | None = None
    accrued: bool = False
    # Form 4797 items (Schedule K-1 (Form 1065) boxes 9c and 10; (Form 1120-S) boxes 8c and 9): the share of the entity's
    # net section 1231 gain or loss joins Part I (Form 4797 (2025) instructions, line 7); the unrecaptured section 1250 gain
    # is the part of that gain due to depreciation, for line 5 of the Unrecaptured Section 1250 Gain Worksheet (line 11 when
    # the K-1 is from an estate or trust). Both are informational splits of the same gain, never added twice.
    net_section_1231_gain: Money = Z           # box 10 (1065) / box 9 (1120-S); a loss is negative
    unrecaptured_1250_gain: Money = Z          # box 9c (1065) / box 8c (1120-S)


# Form 4797 Part III classes (IRC §1245(a)(3), §1250(c)): section_1245 is depreciable personal property (and the real
# property §1245(a)(3) lists), section_1250 depreciable real property that is not §1245 property, land is never
# depreciable (a §1231 asset with no recapture), other is §1231 property with no depreciation (a leasehold of land, an
# amortizable asset is section_1245).
PropertyClass = Literal["section_1245", "section_1250", "land", "other"]


class Disposition(BaseModel):
    """One sale or exchange of property used in a trade or business or held for the production of rents (Form 4797 line 2,
    10 or 19). Amounts default to None, not zero: a disposition whose price, basis or depreciation is not stated cannot be
    figured and blocks (never taken as zero; enter 0 for the depreciation of land). Column letters are Form 4797 (2025):
    (b) date acquired, (c) date sold, (d) gross sales price, (e) depreciation allowed or allowable since acquisition, (f)
    cost or other basis plus improvements and expense of sale, (g) gain or loss = (d) + (e) - (f)."""
    owner: Owner = "taxpayer"
    description: str
    acquired: date | Literal["inherited"] | None = None  # column (b); "INHERITED" is written in place of a date
    sold: date                                            # column (c)
    gross_sales_price: Money | None = None                # column (d) / line 20: money, FMV of property received, debt assumed
    cost_or_basis: Money | None = None                    # column (f) / line 21, before the expense of sale (below) and the
    #                                                       items of line 22; improvements included
    depreciation_allowed: Money | None = None             # column (e) / line 22: allowed or allowable, incl. the §179 expense
    #                                                       deduction and any special depreciation allowance (bonus)
    selling_expenses: Money = Z                           # expense of sale, added to column (f) / line 21
    property_class: PropertyClass | None = None           # None: not stated; blocks (Part III cannot be placed)
    holding_period: Literal["short", "long"] | None = None  # derived from the dates when omitted (§1231(b)(1): more than 1 year)
    # Section 1250 property only (Form 4797 line 26a; IRC §1250(b)(1)): depreciation (incl. any special depreciation
    # allowance) in excess of straight line. Enter 0 for MACRS residential rental and nonresidential real property
    # depreciated straight line with no special allowance (the instructions for line 26 require no computation for them).
    # None: not stated; a §1250 disposition at a gain blocks.
    additional_depreciation: Money | None = None
    low_income_housing: bool = False                      # §1250(a)(1)(B) applicable percentage under 100%: not modelled (blocks)
    # Activity the property was used in, one of the three (1-based index into businesses, rentals or k1s): decides Form 8960
    # line 5b (a non-passive trade or business is not a §1411 trade or business), the QBI attribution of ordinary amounts
    # (Reg. §1.199A-3(b)(2)(ii)(A)) and the §469(g) release Form 8582 will need.
    schedule_c: int | None = None
    rental: int | None = None
    k1: int | None = None
    # IRC §469(g)(1)(A): the taxpayer disposed of the entire interest in the passive activity in a fully taxable transaction
    # to an unrelated person this year. Recorded for Form 8582 (not computed yet): suspended losses of the activity are
    # then allowed in full.
    entire_interest_disposed: bool = False
    # Dispositions the engine does not model: each flag is a blocking diagnostic naming the form.
    installment_sale: bool = False                        # Form 6252
    like_kind_exchange: bool = False                      # Form 8824
    casualty_or_theft: bool = False                       # Form 4684
    partial_disposition: bool = False                     # Reg. §1.168(i)-8(d) partial disposition of a MACRS asset
    related_party: bool = False                           # IRC §1239 (ordinary gain), §267 (loss disallowed)
    # A registered asset (calc/federal.py Asset.id) whose cost and tax depreciation are the source of cost_or_basis and
    # depreciation_allowed: the stated amounts must agree with the register (a mismatch blocks).
    asset_id: str | None = None


class BusinessUseRecapture(BaseModel):
    """Form 4797 Part IV: property placed in service in an earlier year whose business use dropped to 50% or less this year,
    so that the section 179 expense deduction (column (a), IRC §179(d)(10); Reg. §1.179-1(e)) or the excess depreciation
    on listed property (column (b), IRC §280F(b)(2)) is recaptured as other income on the schedule that took the
    deduction. Amounts default to None: an item without its line 33 or line 34 amount blocks."""
    description: str
    kind: Literal["section_179", "section_280f"]
    deduction_claimed: Money | None = None                 # line 33: the §179 deduction claimed, or the depreciation allowable in
    #                                                        prior years plus any §179 deduction (column (b))
    recomputed_depreciation: Money | None = None           # line 34: the depreciation that would have been allowable (Pub. 946, 463)
    schedule_c: int | None = None                          # the Schedule C (1-based index into businesses) that took the deduction
    rental: int | None = None                              # a Schedule E property: not modelled (blocks)


class ForeignTaxCredit(BaseModel):
    """Form 1116 elections: facts no document carries.

    `de_minimis_election` (IRC §904(j)): claim the credit without Form 1116 when every foreign tax is on passive income
    shown on payee statements and totals $300 or less ($600 on a joint return). The limitation does not apply, and no
    foreign tax may be carried to or from the year (§904(j)(1)(B)). None: not stated; the engine applies the election and
    records it when nothing turns on the choice, and asks when a carryover or an excess would be affected.

    `amt_simplified_limitation` (IRC §59(a)(3)): figure the AMT foreign tax credit from the regular tax's foreign-source
    taxable income over alternative minimum taxable income. The election is made for the first year an AMT foreign tax
    credit is claimed and binds every later year unless revoked with the IRS's consent (§59(a)(3)(B)). None: not stated;
    an AMT credit is never figured while it is unknown."""
    de_minimis_election: bool | None = None
    amt_simplified_limitation: bool | None = None


# Which Parent's Return To Use (Form 8615 instructions): the parents' joint return (or the custodial parent's joint return
# with a stepparent); the return of the parent with the greater taxable income (married filing separately, never married
# and living together all year, the custodial parent and a stepparent filing separately); the custodial parent's return
# (divorced or separated, or married but not living together and considered unmarried).
ParentSelection = Literal["joint_return", "greater_taxable_income", "custodial_parent"]


class ParentFacts(BaseModel):
    """Form 8615 lines A-C, 6 and 10 and the capital gain items of the parent whose return the form uses, transcribed
    from that parent's Form 1040 by the preparer (hand-entered, with the preparer's provenance). A parent's return kept
    by the same firm is read instead through Form8615Facts.parent_return_id. None means not stated, never zero."""
    name: str = ""                                   # line A, the parent (or the person listed first on a joint return)
    ssn: str | None = None                           # line B, SSN or ITIN
    filing_status: FilingStatus | None = None        # line C
    taxable_income: Money | None = None              # line 6: the parent's Form 1040 line 15 (zero or less is entered as 0)
    tax: Money | None = None                         # line 10: the parent's Form 1040 line 16, without any tax from Form 4972 or 8814
    #                                                  or the recapture of an education credit
    qualified_dividends: Money = Z                   # the parent's Form 1040 line 3a, the qualified dividends included on line 6
    net_capital_gain: Money = Z                      # the smaller of the gains on the parent's Schedule D lines 15 and 16, or Form 1040 line 7a
    rate_28_gain: Money = Z                          # the parent's Schedule D line 18 (28% rate gain)
    unrecaptured_1250_gain: Money = Z                # the parent's Schedule D line 19


class Form8615Facts(BaseModel):
    """Form 8615 (tax for certain children who have unearned income, IRC §1(g)) facts that no document carries. The
    child's own facts (age, student status, support, a living parent) are on the taxpayer's Person."""
    parent: ParentFacts | None = None                # hand-entered from the parent's return
    parent_return_id: str | None = None              # or: a return of the same firm, whose latest computed result supplies the parent's lines
    # Which Parent's Return To Use: required when the parent's return is not a joint return (a joint return is used as
    # such); recorded on the form as the basis of the choice. None: not stated.
    which_parent: ParentSelection | None = None
    # The parent elected to report the child's interest and dividends on their own return (Form 8814): the child then
    # files no return for that income, so a child's return stating it is contradictory and blocks.
    form_8814_election: bool = False
    # Line 7: the total of line 5 of the Forms 8615 of the parent's other children, and the qualified dividends, net
    # capital gain, 28% rate gain and unrecaptured §1250 gain included in those amounts (from their Line 5 Worksheets and
    # the Worksheets for Line 11 of the Schedule D Tax Worksheet).
    other_children_net_unearned_income: Money = Z
    other_children_qualified_dividends: Money = Z
    other_children_net_capital_gain: Money = Z
    other_children_rate_28_gain: Money = Z
    other_children_unrecaptured_1250_gain: Money = Z
    # Line 2 when the child itemizes: the part of Schedule A line 17z directly connected with the production of the
    # unearned income on line 1 (custodian fees, investment counsel fees), and the part of that connected with the child's
    # qualified dividends and net capital gain (Line 5 Worksheets #2 and #3, line 5). None: not stated; asked only when
    # the child itemizes and Schedule A line 17z is not zero (enter 0 when nothing on it is directly connected).
    directly_connected_deductions: Money | None = None
    directly_connected_to_dividends_and_gains: Money | None = None


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
    # Deprecated: Form 8889 is computed from Forms 5498-SA, 1099-SA, W-2 code W and hsa_facts. Kept so that returns stored
    # with it still load; any entry is a blocking diagnostic (form_8889_deprecated_input).
    hsa_deduction: Money = Z
    self_employed_retirement: Money = Z  # SEP, SIMPLE, qualified plans
    self_employed_health_insurance: Money = Z
    alimony_paid: Money = Z              # pre-2019 instruments only
    alimony_recipient_ssn: str = ""
    # Deprecated: the IRA deduction is figured by the IRA Deduction Worksheet from Form 5498 and ira_facts; any entry is a
    # blocking diagnostic (ira_deduction_deprecated_input).
    ira_deduction: Money = Z
    student_loan_interest_paid: Money = Z
    other: Money = Z


class Payments(BaseModel):
    estimated_tax_payments: Money = Z
    prior_year_overpayment_applied: Money = Z
    extension_payment: Money = Z
    other_withholding: Money = Z
    apply_to_next_year: Money = Z


class DirectDeposit(BaseModel):
    """Where a Form 1040-X refund goes (the routing number, account number and account type under line 22). Only an
    electronically filed Form 1040-X is refunded by direct deposit; a paper one is refunded by check."""
    routing_number: str = ""
    account_number: str = ""
    account_type: Literal["checking", "savings"] | None = None


class PreviousAdjustment(BaseModel):
    """The IRS changed the return as filed (a math error notice, an examination, an agreed CP2000): Form 1040-X column A
    then shows the adjusted amounts, not the return's (Form 1040-X instructions, Column A). `lines` is keyed by the
    engine's Form 1040 line keys ("11a" adjusted gross income, "15" taxable income, "16" tax, "24c" total tax, "33" total
    payments, "34" overpayment, ...): each stated line replaces the as-filed figure; lines not stated stay as filed. The
    reason names the notice or transcript the amounts come from and is part of the approved package."""
    reason: str = ""
    lines: dict[str, Money] = {}


class Amendment(BaseModel):
    """Form 1040-X facts no document carries (returns/amendment.py). Column A comes from the original return as it was
    approved, signed and filed (the sealed version the approval pinned), column C from this return's inputs; these are the
    rest of the form.

    `explanation` is Part III ("Explanation of Changes"): required, at least 10 characters, a review blocker until stated.
    `paid_with_original_return` is the tax paid with the original return (line 16; 0 when nothing was paid): None is not
    stated and blocks when the original showed an amount owed. `paid_after_filing` is additional tax paid after the original
    was filed (notices, installments; line 16) and `last_payment_on` the date of the latest such payment (IRC §6511(a): a
    refund claim is timely within 2 years of a payment). `original_refund_received` and `original_overpayment_applied` are
    checked against the original's lines 35a and 36 (line 18 takes the original's overpayment as filed or as adjusted; a
    difference is a warning naming `as_previously_adjusted`). `superseding`: a return filed before the due date, extensions
    included, supersedes the original rather than amending it; None derives it from today's date and the IRC §7503 due date
    (`extension_filed` moves the due date to October 15). `original_filed_on` overrides the filing date on record (a paper
    return's postmark) for the §6511 and superseding dates. `apply_to_estimated_tax` is line 23, the part of the new
    overpayment (line 21) applied to next year's estimated tax; the original's election already stands in line 18."""
    explanation: str = ""
    as_previously_adjusted: PreviousAdjustment | None = None
    paid_with_original_return: Money | None = None
    paid_after_filing: Money = Z
    last_payment_on: date | None = None
    original_refund_received: Money | None = None
    original_overpayment_applied: Money | None = None
    superseding: bool | None = None
    extension_filed: bool = False
    original_filed_on: date | None = None
    apply_to_estimated_tax: Money = Z
    direct_deposit: DirectDeposit | None = None


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
    # Carryovers from the prior-year return belong in `prior_year`; these two are kept for returns stored before it
    # existed. Both given and different is a blocking diagnostic (capital_loss_carryover_conflict).
    capital_loss_carryover_short: Money = Z
    capital_loss_carryover_long: Money = Z
    prior_year: PriorYear | None = None
    retirement: list[Retirement] = []
    ira_accounts: list[IRAAccount] = []            # Form 5498
    hsa_distributions: list[HSADistribution] = []  # Form 1099-SA
    hsa_contributions: list[HSAContribution] = []  # Form 5498-SA
    hsa_facts: list[HSAFacts] = []                 # Form 8889 facts not on any document, one per HSA beneficiary
    ira_facts: list[IRAFacts] = []                 # IRA Deduction Worksheet and Form 8606 facts, one per person
    marketplace_coverage: list[MarketplaceCoverage] = []  # Form 1095-A
    social_security: list[SocialSecurity] = []
    unemployment: list[Unemployment] = []
    state_refund_taxable: Money = Z      # taxable portion under the tax benefit rule
    alimony_received: Money = Z
    businesses: list[Business] = []
    rentals: list[Rental] = []
    k1s: list[K1] = []
    dispositions: list[Disposition] = []                  # Form 4797 Parts I-III: sales of property used in a trade or business
    business_use_recaptures: list[BusinessUseRecapture] = []  # Form 4797 Part IV: §179 / §280F(b)(2) recapture
    other_income: dict[str, Money] = {}  # Schedule 1 line 8 items, keyed by line letter or label
    qbi_loss_carryforward: Money = Z
    reit_ptp_loss_carryforward: Money = Z
    adjustments: Adjustments = Adjustments()
    itemized: Itemized = Itemized()
    car_loans: list[CarLoan] = []
    tips_form_4137: Money = Z            # qualified tips reported on Form 4137
    tips_form_4137_occupation_code: int | None = None
    dependent_care_expenses: Money = Z
    dependent_care_qualifying_persons: int = 0
    students: list[Student] = []
    retirement_savings: list[RetirementSavings] = []  # Form 8880 facts not on any document
    # Deprecated: a hand total was never computed and is no longer accepted into the credit. Kept so that returns
    # stored with it still load; any entry is a blocking diagnostic (form_8880_deprecated_input).
    retirement_savings_contributions: dict[Owner, Money] = {}
    amt_adjustments: dict[str, Money] = {}  # Form 6251 preference items, e.g. "iso": bargain element
    foreign_tax_credit: ForeignTaxCredit = ForeignTaxCredit()  # Form 1116 elections
    form_8615: Form8615Facts = Form8615Facts()  # Form 8615 facts: the parent's return items, other children, the Form 8814 election
    excess_aptc_repayment: Money = Z     # from Form 8962
    net_premium_tax_credit: Money = Z    # from Form 8962
    household_employment_taxes: Money = Z
    payments: Payments = Payments()
    claim_eic: bool = True
    citizen_or_qualified_alien: bool = True  # Schedule 3-A Part II
    want_federal_public_benefit: bool = True
    # Form 1040-X facts (a return created by Returns.start_amendment); None on an original return.
    amendment: Amendment | None = None
