"""Form 1040 and its schedules, computed line by line.

Line numbers follow the 2026 forms (IRS early-release drafts). Every statutory amount is
resolved from the knowledge base for the tax year, so the computation is traced to its
authorities. Anything the engine does not support is raised as a diagnostic rather than
silently skipped: an `error` diagnostic blocks filing, a `warning` needs preparer review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from ..calc.engine import D, Ctx
from ..calc.federal import Asset, tax_depreciation
from . import tax as T
from .model import (SCHEDULE_C_LINES, Business, CapitalTransaction, Dependent, Disposition, HSAFacts, IndividualReturn, IRAAccount,
                    IRAFacts, Owner, Person, Retirement)
from .sheet import Sheets, pos, whole

SUPPORTED_YEARS = {2026}
Z = Decimal(0)
QC_RELATIONSHIPS = {"son", "daughter", "stepchild", "foster_child", "brother", "sister", "half_brother", "half_sister",
                    "stepbrother", "stepsister", "grandchild", "niece", "nephew"}
QR_RELATIONSHIPS = QC_RELATIONSHIPS | {"parent", "grandparent", "aunt", "uncle", "in_law"}
# W-2 box 12 codes that are elective deferrals or designated Roth contributions for Form 8880 line 2 (Form 8880 (2025)
# instructions, Line 2; General Instructions for Forms W-2 and W-3, box 12 codes): D 401(k), E 403(b), F 408(k)(6) SEP,
# G 457(b), H 501(c)(18)(D), S 408(p) SIMPLE, AA Roth 401(k), BB Roth 403(b), EE Roth governmental 457(b).
SAVERS_CREDIT_W2_CODES = frozenset({"D", "E", "F", "G", "H", "S", "AA", "BB", "EE"})
# 1099-R box 7 codes of distributions that are rollovers in full and so never reduce Form 8880 line 3 (instructions,
# Line 4: "distributions not taxable as the result of a rollover or a trustee-to-trustee transfer").
ROLLOVER_DISTRIBUTION_CODES = frozenset({"G", "H"})
# 1099-R box 7 codes of Roth IRA distributions (Form 8606 Part III): J early with no known exception, Q qualified, T an
# exception applies but the payer does not know whether the 5-year period is met.
ROTH_DISTRIBUTION_CODES = frozenset({"J", "Q", "T"})
# Corrective distributions the engine does not model: 8 and P return a contribution with its earnings, R and N are
# recharacterizations (Form 8606 instructions, "Recharacterizations" and "Return of IRA Contributions").
CORRECTIVE_DISTRIBUTION_CODES = frozenset({"8", "P", "R", "N"})
MONTHS = [f"{m:02d}" for m in range(1, 13)]
# Result.carryforwards keys that exist per person: the spouse's is stored under the spouse_ prefix of the next year's
# prior_year group (model.PriorYear).
PER_OWNER_CARRYFORWARDS = ("traditional_ira_basis", "roth_ira_basis", "roth_conversion_basis", "hsa_last_month_rule_excess")
# Result.carryforwards keys that exist per year, keyed `<kind>_<year>`: stored as (kind, detail = the year) and listed by the
# next year's prior_year group per year (the nonrecaptured net section 1231 losses of Form 4797 line 8, model.Section1231Loss).
PER_YEAR_CARRYFORWARDS = ("nonrecaptured_1231_loss",)
TWENTY_PERCENT = Decimal("0.20")
TEN_PERCENT = Decimal("0.10")
# Form 4797 dispositions the engine does not model, each a blocking diagnostic naming the form that would be needed.
UNSUPPORTED_DISPOSITIONS = (
    ("installment_sale", "form_4797_installment_sale", "an installment sale is reported on Form 6252 (Form 4797 lines 4 and 15), not supported"),
    ("like_kind_exchange", "form_4797_like_kind_exchange", "a like-kind exchange is reported on Form 8824 (Form 4797 lines 5 and 16), not supported"),
    ("casualty_or_theft", "form_4797_casualty_or_theft", "a casualty or theft is reported on Form 4684 (Form 4797 lines 3 and 14), not supported"),
    ("partial_disposition", "form_4797_partial_disposition", "a partial disposition of a MACRS asset (Reg. §1.168(i)-8(d); lines 1b, 1c) is not supported"),
    ("related_party", "form_4797_related_party", "a sale to a related person (IRC §1239 ordinary gain, §267 loss disallowance) is not supported"),
    ("low_income_housing", "form_4797_low_income_housing", "the §1250(a)(1)(B) applicable percentage of low-income housing (line 26b) is not supported"),
)


def carryforward_key(kind: str, owner: Owner) -> str:
    """The prior_year input name a per-person carryforward is stored under next year."""
    return kind if owner == "taxpayer" else f"spouse_{kind}"


def held_long_term(acquired: date, sold: date) -> bool:
    """Held more than 1 year (IRC §1222(3), §1231(b)(1)): the holding period starts the day after acquisition and includes
    the day of disposition (Form 4797 (2025) instructions, Part I), so a sale on the first anniversary is short-term."""
    try:
        anniversary = acquired.replace(year=acquired.year + 1)
    except ValueError:  # 29 February
        anniversary = date(acquired.year + 1, 3, 1)
    return sold > anniversary


def part_iii_column(index: int) -> str:
    """Form 4797 Part III property columns A-D; a fifth property starts another form (E, F, ... here)."""
    return chr(ord("A") + index)


def round_up_10(amount: Decimal) -> Decimal:
    """Rounded up to the next multiple of $10 (IRA Deduction Worksheet line 7, Maximum Roth IRA Contribution Worksheet
    line 9: IRC §219(g)(2)(B) rounds the reduction down to the next $10, so the deductible amount rounds up)."""
    return (amount / 10).to_integral_value(rounding=ROUND_CEILING) * 10


def ratio3(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Form 8606 line 10: a decimal rounded to 3 places, 1.000 when 1.000 or more (or when nothing is in the IRAs)."""
    if denominator <= 0:
        return Decimal("1.000")
    return min(Decimal("1.000"), (numerator / denominator).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
# Form 1116 Parts I and II have one column per country (A, B, C); income from more countries needs additional forms.
FTC_COLUMNS = 3
FTC_RATIO_PLACES = Decimal("0.0001")  # lines 3f and 19: "round off the result to at least four decimal places"


def steps(amount: Decimal, step: Decimal, *, round_up: bool) -> Decimal:
    """Number of whole steps in `amount` (or steps 'or fraction thereof' when round_up)."""
    if amount <= 0:
        return Z
    return (amount / step).to_integral_value(rounding=ROUND_CEILING if round_up else ROUND_FLOOR)


@dataclass
class DependentStatus:
    dep: Dependent
    qualifying_child: bool
    qualifying_relative: bool
    ctc: bool
    odc: bool
    eic: bool
    under_13: bool

    @property
    def dependent(self) -> bool:
        return self.qualifying_child or self.qualifying_relative


@dataclass
class _IRA:
    """One person's IRA items for the year (Form 8606 and the IRA Deduction Worksheet), gathered before the deduction is
    known: the distributions' taxable parts first (Pub. 590-B Worksheet 1-1 breaks the circularity between the taxable
    part and the deduction), the deduction and the basis carried forward once total income is known."""
    contributions: Decimal = Z            # Form 5498 box 1, traditional IRA contributions for the year (any date)
    roth_contributions: Decimal = Z       # box 10
    conversions: Decimal = Z              # box 3, net amount converted to Roth IRAs (Form 8606 lines 8 and 16)
    distributions: Decimal = Z            # traditional IRA distributions net of rollovers and QCDs, conversions included
    taxable_trad: Decimal = Z             # Form 8606 line 15c, or the whole amount when the IRAs have no basis
    taxable_conversion: Decimal = Z       # line 18
    taxable_roth: Decimal = Z             # line 25c
    early_base: Decimal = Z               # taxable part of code 1 distributions (10% additional tax, IRC §72(t))
    basis_prior: Decimal | None = None    # prior-year Form 8606 line 14 (prior_year group); None: not stated
    covered: bool = False                 # active participant in an employer plan (W-2 box 13, 5498 boxes 8-9, or stated)
    worksheet_1_1: bool = False           # the taxable part came from Pub. 590-B Worksheet 1-1 (contributions may be limited)
    part_i: bool = False                  # Form 8606 Part I lines 4-15c apply (a distribution or conversion with basis)
    required: bool = False                # Form 8606 is part of the return
    lines: dict[str, Decimal] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    roth_basis_next: Decimal | None = None        # basis in regular Roth contributions carried to next year
    conversion_basis_next: Decimal | None = None  # basis in conversions carried to next year
    deduction: Decimal = Z                # Schedule 1 line 20, this person's column
    nondeductible: Decimal = Z            # Form 8606 line 1


@dataclass
class _F4797:
    """What Form 4797 hands to the rest of the return."""
    filed: bool = False                   # a Form 4797 is part of the return
    line_7: Decimal = Z                   # Part I net section 1231 gain or (loss)
    line_8: Decimal = Z                   # nonrecaptured net section 1231 losses of the 5 preceding years
    to_schedule_d: Decimal = Z            # the long-term capital gain of line 7 or line 9, Schedule D line 11
    ordinary: Decimal = Z                 # line 18b, Schedule 1 line 4
    unrecaptured_1250: Decimal = Z        # Unrecaptured Section 1250 Gain Worksheet line 3 (total over the §1250 properties of Part III)
    unrecaptured_rows: list[dict[str, str]] = field(default_factory=list)  # the worksheet's lines 1-3 per property
    niit_excluded: Decimal = Z            # Form 8960 line 5b: gains (negative) and losses (positive) of non-§1411 trades or businesses
    niit_unplaced: int = 0                # dispositions with no activity link: Form 8960 cannot place them (blocks when it applies)
    qbi_ordinary: dict[int, Decimal] = field(default_factory=dict)      # ordinary amounts attributable to each Schedule C (1-based)
    sch_c_other_income: dict[int, Decimal] = field(default_factory=dict)  # Part IV recapture to Schedule C line 6 (1-based)
    depreciable: bool = False             # a depreciable asset was disposed of (the AMT basis may differ, Form 6251 lines 2k, 2l)


@dataclass
class Result:
    tax_year: int
    filing_status: str
    sheets: Sheets
    sources: list[dict[str, Any]] = field(default_factory=list)
    # What this return carries to the next tax year, keyed by the next return's `prior_year` input names (whole dollars,
    # positive amounts; a per-person amount of the spouse is keyed spouse_*; unused foreign tax, which
    # `prior_year.ftc_carryovers` lists per year, is keyed `ftc_carryover_<category>_<year paid>` and, for the AMT
    # credit, `ftc_amt_carryover_<category>_<year paid>`). Computed, never entered; persisted per version as
    # return_carryforwards (returns/store.py) and rolled forward by Returns.roll_forward.
    carryforwards: dict[str, Decimal] = field(default_factory=dict)

    @property
    def forms(self) -> dict[str, dict[str, Decimal]]:
        return self.sheets.forms

    @property
    def diagnostics(self):
        return self.sheets.diagnostics

    def line(self, form: str, line: str) -> Decimal:
        return self.sheets.get(form, line)

    @property
    def refund(self) -> Decimal:
        return self.line("f1040", "35a")

    @property
    def amount_owed(self) -> Decimal:
        return self.line("f1040", "37")

    @property
    def blocking(self) -> list:
        return [d for d in self.diagnostics if d.severity == "error"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tax_year": self.tax_year,
            "filing_status": self.filing_status,
            "forms": {f: {k: str(v) for k, v in lines.items()} for f, lines in self.sheets.forms.items()},
            "facts": self.sheets.facts,
            "notes": self.sheets.notes,
            "diagnostics": [d.__dict__ for d in self.diagnostics],
            "summary": {"agi": str(self.line("f1040", "11a")), "taxable_income": str(self.line("f1040", "15")),
                        "total_tax": str(self.line("f1040", "24c")), "payments": str(self.line("f1040", "33")),
                        "refund": str(self.refund), "amount_owed": str(self.amount_owed)},
            "carryforwards": {k: str(v) for k, v in self.carryforwards.items()},
            "sources": self.sources,
        }


def compute_individual(ctx: Ctx, r: IndividualReturn, assets: list[Asset] | None = None) -> Result:
    """`assets` is the client's asset register (calc/federal.py Asset, from the ledger): a Form 4797 disposition that names
    an `asset_id` takes its cost and tax depreciation from it instead of stated amounts."""
    return _Individual(ctx, r, assets).compute()


class _Individual:
    _ftc: Decimal

    def __init__(self, ctx: Ctx, r: IndividualReturn, assets: list[Asset] | None = None):
        self.ctx, self.r = ctx, r
        self.assets: dict[str, Asset] = {a.id: a for a in assets or []}
        self._f4797 = _F4797()                  # Form 4797 results (Schedule D line 11, Schedule 1 line 4, Form 8960 line 5b, ...)
        self.y = r.tax_year
        self.on = date(r.tax_year, 12, 31)
        self.fs = r.filing_status
        self.joint = r.filing_status == "mfj"
        self.mfs = r.filing_status == "mfs"
        self.married = r.filing_status in ("mfj", "mfs")
        self.s = Sheets()
        self.deps: list[DependentStatus] = []
        self.se: dict[Owner, dict[str, Decimal]] = {}
        self.biz_net: list[tuple[Business, Decimal]] = []
        self._ti_unfloored = Z                  # Form 1040 line 15 as it would be if it could be negative (§1212(b)(2))
        self._carryforwards: dict[str, Decimal] = {}
        self._ira: dict[Owner, _IRA] = {}       # Form 8606 and IRA Deduction Worksheet state per person
        self._hsa: dict[Owner, dict[str, Decimal]] = {}  # Form 8889 lines 13, 16, 17b, 20 and 21 per HSA beneficiary
        self._ftc = Z                           # Form 1116 line 35 (or the §904(j) credit), Schedule 3 line 1
        self._f1116: dict[str, Any] = {}        # what the AMT foreign tax credit reuses (Form 6251 line 8)

    # ----------------------------------------------------------------- helpers
    def p(self, rule_id: str) -> Any:
        return self.ctx.param(rule_id, self.on)

    def dec(self, rule_id: str) -> Decimal:
        return self.ctx.dec(rule_id, self.on)

    def set(self, form: str, line: str, value: Any, note: str = "") -> Decimal:
        return self.s.set(form, line, value, note)

    def g(self, form: str, line: str) -> Decimal:
        return self.s.get(form, line)

    def person(self, owner: Owner) -> Person | None:
        return self.r.taxpayer if owner == "taxpayer" else self.r.spouse

    def owners(self) -> list[Owner]:
        return ["taxpayer", "spouse"] if self.joint else ["taxpayer"]

    def age65(self, person: Person | None) -> bool:
        # Born before January 2 of (year - 64): a person attains an age the day before the birthday.
        return bool(person and person.dob and person.dob < date(self.y - 64, 1, 2))

    def age_at_year_end(self, person: Person | None, years: int) -> bool:
        """Whether the person is `years` or older at the end of the tax year (born on or before December 31 of year - years):
        the Form 8889 age-55 and the IRA age-50 tests."""
        return bool(person and person.dob and person.dob <= date(self.y - years, 12, 31))

    def joint_or(self, mfj: Any, other: Any) -> Any:
        return mfj if self.fs in ("mfj", "qss") else other

    # ----------------------------------------------------------------- driver
    def compute(self) -> Result:
        if self.y not in SUPPORTED_YEARS:
            raise ValueError(f"tax year {self.y} is not supported (supported: {sorted(SUPPORTED_YEARS)})")
        self._validate()
        self._dependents()
        self._wages_and_dependent_care_benefits()
        self._interest_dividends()
        self._form_8606_distributions()
        self._retirement()
        self._form_8889()
        self._form_4797()
        self._schedule_c()
        self._schedule_se()
        self._schedule_d()
        self._schedule_e_and_schedule_1()
        self._social_security()
        self._agi()
        self._deductions()
        self._capital_loss_carryover()
        self._tax()
        self._form_1116()
        self._amt()
        self._credits()
        self._other_taxes()
        self._payments()
        self._unsupported_checks()
        self.s.drop_empty()
        return Result(self.y, self.fs, self.s, self.ctx.sources(), self._carryforwards)

    # ----------------------------------------------------------------- validation and dependents
    def _validate(self) -> None:
        r = self.r
        if self.fs in ("mfj", "mfs") and r.spouse is None:
            self.s.diag("error", "spouse_missing", "A spouse is required for married filing jointly or separately.")
        if r.taxpayer.can_be_claimed_as_dependent and r.dependents:
            self.s.diag("error", "dependent_claims_dependents",
                        "Someone who can be claimed as a dependent cannot claim dependents (IRC §152(b)(1)).")
        if not r.taxpayer.ssn:
            self.s.diag("error", "taxpayer_ssn_missing", "The taxpayer's SSN or ITIN is required.")
        if self.joint and r.spouse is not None and not r.spouse.ssn:
            self.s.diag("error", "spouse_ssn_missing", "The spouse's SSN or ITIN is required on a joint return.")
        for w in r.w2s:
            if (w.ss_tax and not w.ss_wages) or (w.medicare_tax and not w.medicare_wages):
                self.s.diag("warning", "w2_box_mismatch", f"W-2 from {w.employer_name or 'employer'}: tax withheld in box 4 or 6 "
                                                          "without wages in box 3 or 5. Check the entry.", "f1040", "1a")
        self.s.fact("f1040", "filing_status", self.fs)
        self.s.fact("f1040", "taxpayer", {"first_name": r.taxpayer.first_name, "last_name": r.taxpayer.last_name})
        if r.spouse is not None:
            self.s.fact("f1040", "spouse", {"first_name": r.spouse.first_name, "last_name": r.spouse.last_name})

    def _dependents(self) -> None:
        r, y = self.r, self.y
        limit = self.dec("us_fed.individual.qualifying_relative_income_limit")
        claimable = not r.taxpayer.can_be_claimed_as_dependent
        taxpayer_ctc_ok = r.taxpayer.ssn_valid_for_employment or bool(
            self.joint and r.spouse and r.spouse.ssn_valid_for_employment)
        rows = []
        for d in r.dependents:
            rel_ok = d.relationship in QC_RELATIONSHIPS
            residence_ok = d.months_in_home > 6
            under_19 = d.dob > date(y - 19, 12, 31)
            under_24 = d.dob > date(y - 24, 12, 31)
            age_ok = under_19 or (d.full_time_student and under_24) or d.permanently_disabled
            elders = [p.dob for p in (r.taxpayer, r.spouse if self.joint else None) if p and p.dob]
            if elders and not d.permanently_disabled and all(d.dob <= e for e in elders):
                age_ok = False
            qc_core = rel_ok and residence_ok and age_ok and not d.files_joint_return
            qc = claimable and qc_core and not d.provided_over_half_own_support and not d.qualifying_child_of_another_taxpayer
            qr = (claimable and not qc and not d.qualifying_child_of_another_taxpayer
                  and (d.relationship in QR_RELATIONSHIPS or (d.relationship == "other_household_member" and d.months_in_home == 12))
                  and d.gross_income < limit and d.taxpayer_provided_over_half_support)
            has_tin = d.tin_type != "none" and bool(d.ssn)
            ctc = (qc and taxpayer_ctc_ok and d.dob > date(y - 17, 12, 31) and d.tin_type == "ssn"
                   and d.ssn_valid_for_employment and bool(d.ssn))
            odc = (qc or qr) and not ctc and has_tin
            eic = (qc_core and d.lived_in_us_over_half_year and d.tin_type == "ssn" and d.ssn_valid_for_employment
                   and not d.qualifying_child_of_another_taxpayer)
            st = DependentStatus(d, qc, qr, ctc, odc, eic, d.dob > date(y - 13, 12, 31) or d.permanently_disabled)
            self.deps.append(st)
            if not st.dependent:
                self.s.diag("warning", "not_a_dependent",
                            f"{d.first_name} does not meet the qualifying child or qualifying relative tests and is not claimed.")
                continue
            rows.append({"first_name": d.first_name, "last_name": d.last_name, "ssn": d.ssn, "relationship": d.relationship,
                         "lived_with_over_half": residence_ok, "in_us": d.lived_in_us_over_half_year,
                         "full_time_student": d.full_time_student, "disabled": d.permanently_disabled,
                         "child_tax_credit": ctc, "other_dependent_credit": odc})
        if rows:
            self.s.fact("f1040", "dependents", rows)
        if self.fs == "hoh" and not any(s.qualifying_child or s.qualifying_relative for s in self.deps):
            self.s.diag("error", "hoh_without_qualifying_person", "Head of household requires a qualifying person (IRC §2(b)).")
        if self.fs == "qss" and not any(s.qualifying_child for s in self.deps):
            self.s.diag("error", "qss_without_child", "Qualifying surviving spouse requires a dependent child (IRC §2(a)).")
        if not taxpayer_ctc_ok and any(s.qualifying_child for s in self.deps):
            self.s.diag("info", "ctc_taxpayer_ssn",
                        "No child tax credit: neither spouse has an SSN valid for employment (IRC §24(h)(7)). "
                        "Qualifying children are treated as other dependents.")

    # ----------------------------------------------------------------- income
    def _wages_and_dependent_care_benefits(self) -> None:
        r = self.r
        self.set("f1040", "1a", sum((w.wages for w in r.w2s), Z), "Form(s) W-2, box 1")
        benefits = sum((w.dependent_care_benefits for w in r.w2s), Z)
        taxable_benefits = Z
        if benefits > 0:
            excl = self.p("us_fed.individual.dependent_care_benefit_exclusion")
            cap = D(excl["mfs"] if self.mfs else excl["other"])
            earned = [self._earned_income_of(o) for o in self.owners()]
            limit = min([cap, *earned]) if earned else cap
            used = min(benefits, self.r.dependent_care_expenses, limit)
            taxable_benefits = pos(benefits - used)
            self.set("f2441", "12", benefits, "Dependent care benefits, W-2 box 10")
            self.set("f2441", "26", taxable_benefits, "Taxable dependent care benefits")
            self._dc_excluded = used
        else:
            self._dc_excluded = Z
        self.set("f1040", "1e", taxable_benefits)
        self.set("f1040", "1z", self.g("f1040", "1a") + self.g("f1040", "1e"))

    def _earned_income_of(self, owner: Owner) -> Decimal:
        wages = sum((w.wages for w in self.r.w2s if w.owner == owner), Z)
        biz = sum((self._sch_c_profit_estimate(b) for b in self.r.businesses if b.owner == owner), Z)
        return pos(wages + biz)

    def _sch_c_profit_estimate(self, b: Business) -> Decimal:
        return b.gross_receipts - b.returns_allowances - b.cost_of_goods_sold + b.other_income - sum(b.expenses.values(), Z)

    def _interest_dividends(self) -> None:
        r = self.r
        k1_int = sum((k.interest for k in r.k1s), Z)
        k1_div = sum((k.ordinary_dividends for k in r.k1s), Z)
        k1_qd = sum((k.qualified_dividends for k in r.k1s), Z)
        taxable_int = sum((i.interest + i.us_savings_bond_interest for i in r.interest), Z) + k1_int
        exempt = sum((i.tax_exempt_interest for i in r.interest), Z) + sum((d.exempt_interest_dividends for d in r.dividends), Z)
        ordinary = sum((d.ordinary for d in r.dividends), Z) + k1_div
        qualified = sum((d.qualified for d in r.dividends), Z) + k1_qd
        if qualified > ordinary:
            self.s.diag("error", "qualified_exceeds_ordinary", "Qualified dividends cannot exceed ordinary dividends.", "f1040", "3a")
        self.set("f1040", "2a", exempt, "Tax-exempt interest (1099-INT box 8, 1099-DIV box 12)")
        self.set("f1040", "2b", taxable_int, "Taxable interest (1099-INT boxes 1 and 3, K-1)")
        self.set("f1040", "3a", qualified, "Qualified dividends (1099-DIV box 1b, K-1)")
        self.set("f1040", "3b", ordinary, "Ordinary dividends (1099-DIV box 1a, K-1)")
        if taxable_int > 1500 or ordinary > 1500:
            payers = [{"payer": i.payer, "amount": str(whole(i.interest + i.us_savings_bond_interest))} for i in r.interest]
            payers += [{"payer": k.entity_name, "amount": str(whole(k.interest))} for k in r.k1s if k.interest]
            self.s.fact("sch_b", "interest_payers", payers)
            self.set("sch_b", "2", taxable_int)
            self.set("sch_b", "4", taxable_int)
            self.s.fact("sch_b", "dividend_payers",
                        [{"payer": d.payer, "amount": str(whole(d.ordinary))} for d in r.dividends]
                        + [{"payer": k.entity_name, "amount": str(whole(k.ordinary_dividends))} for k in r.k1s if k.ordinary_dividends])
            self.set("sch_b", "6", ordinary)

    # ----------------------------------------------------------------- IRAs (Form 8606) and HSAs (Form 8889)
    @staticmethod
    def _is_roth_distribution(d: Retirement) -> bool:
        return bool(set(d.distribution_code.upper()) & ROTH_DISTRIBUTION_CODES)

    @staticmethod
    def _is_roth_account(a: IRAAccount) -> bool:
        """A Form 5498 of a Roth IRA: box 7 says so, or (box 7 unread) it reports Roth contributions or a conversion received."""
        return a.account_type == "roth" or (a.account_type is None and bool((a.roth_contributions or Z) or (a.roth_conversion or Z)))

    def _covered(self, owner: Owner, hand: IRAFacts | None, accounts: list[IRAAccount]) -> bool:
        """Active participant in an employer plan (IRA Deduction Worksheet line 1): the stated fact when there is one, else
        W-2 box 13 or SEP/SIMPLE contributions on a Form 5498 (Pub. 590-A, "Are You Covered by an Employer Plan?")."""
        if hand is not None and hand.covered_by_employer_plan is not None:
            return hand.covered_by_employer_plan
        return any(w.retirement_plan for w in self.r.w2s if w.owner == owner) or any(
            (a.sep_contributions or Z) > 0 or (a.simple_contributions or Z) > 0 for a in accounts)

    def _form_8606_distributions(self) -> None:
        """Form 8606 Parts I-III per person, as far as they can be figured before the IRA deduction is known: the taxable
        parts of traditional IRA distributions (§72(e)(8), §408(d)(2)), of conversions (Part II) and of Roth IRA
        distributions (Part III, the §408A(d)(4) ordering). A traditional IRA distribution with the basis in the IRAs not
        stated blocks: the taxable amount is never defaulted to the whole distribution."""
        r, py = self.r, self.r.prior_year
        f8606 = "f8606"
        if r.adjustments.ira_deduction:
            self.s.diag("error", "ira_deduction_deprecated_input",
                        "adjustments.ira_deduction is no longer an input: the IRA deduction is figured by the IRA Deduction Worksheet "
                        "from Form 5498 box 1, W-2 box 13 and the ira_facts. Remove it.", "sch_1", "20")
        stated = {x.owner: x for x in r.ira_facts}
        for owner in self.owners():
            f = f"{f8606}[{owner}]"
            hand = stated.get(owner)
            accounts = [a for a in r.ira_accounts if a.owner == owner]
            trad_accounts = [a for a in accounts if not self._is_roth_account(a)]
            st = _IRA()
            st.contributions = sum((a.ira_contributions or Z for a in accounts), Z)
            st.roth_contributions = sum((a.roth_contributions or Z for a in accounts), Z)
            st.conversions = sum((a.roth_conversion or Z for a in accounts), Z)
            st.covered = self._covered(owner, hand, accounts)
            st.basis_prior = getattr(py, carryforward_key("traditional_ira_basis", owner)) if py is not None else None
            mine = [d for d in r.retirement if d.owner == owner]
            for d in mine:
                if set(d.distribution_code.upper()) & CORRECTIVE_DISTRIBUTION_CODES:
                    self.s.diag("error", "form_8606_corrective_distribution",
                                f"1099-R from {d.payer or 'payer'} (code {d.distribution_code}): a returned contribution or a "
                                "recharacterization is not supported; its earnings and the statement it needs are not figured.", f)
            if any((a.recharacterized_contributions or Z) > 0 for a in accounts):
                self.s.diag("error", "form_8606_recharacterization",
                            f"Form 5498 box 4 for the {owner}: a recharacterized contribution is not supported; Form 8606 treats the "
                            "contribution as made to the second IRA and needs a statement.", f)
            trad = [d for d in mine if d.ira_sep_simple and not self._is_roth_distribution(d)
                    and not set(d.distribution_code.upper()) & ROLLOVER_DISTRIBUTION_CODES]
            roth = [d for d in mine if self._is_roth_distribution(d)]
            stated_basis = py is not None and any(getattr(py, carryforward_key(k, owner)) is not None
                                                  for k in ("traditional_ira_basis", "roth_ira_basis", "roth_conversion_basis"))
            if not (accounts or trad or roth or hand or stated_basis):
                continue
            self._ira[owner] = st
            self._form_8606_part_i(owner, st, hand, trad, trad_accounts, f)
            self._form_8606_part_iii(owner, st, hand, roth, f)

    def _form_8606_part_i(self, owner: Owner, st: _IRA, hand: IRAFacts | None, trad: list[Retirement],
                          trad_accounts: list[IRAAccount], f: str) -> None:
        y = self.y
        net = sum((pos(d.gross_distribution - d.rollover_amount - d.qcd_amount) for d in trad), Z)
        qcd = sum((d.qcd_amount for d in trad), Z)
        st.distributions = net
        if net == 0 and st.conversions == 0:
            return                                        # no distribution this year: lines 1-3 and 14 follow the deduction
        if st.conversions > net:
            self.s.diag("error", "form_8606_conversion_exceeds_distributions",
                        f"Form 8606 line 8 for the {owner}: Form 5498 box 3 shows {whole(st.conversions)} converted to Roth IRAs but the "
                        f"1099-Rs of traditional IRAs show {whole(net)} distributed (net of rollovers and QCDs). Check the documents.", f, "8")
        l7, l8 = pos(net - st.conversions), min(st.conversions, net)
        code1 = sum((pos(d.gross_distribution - d.rollover_amount - d.qcd_amount) for d in trad if "1" in d.distribution_code), Z)
        if st.basis_prior is None:
            self.s.diag("error", "form_8606_basis_unknown",
                        f"Form 8606 line 2 for the {owner}: the basis in traditional IRAs at the end of {y - 1} is not stated "
                        "(prior_year.traditional_ira_basis; enter 0 if no nondeductible contribution was ever made). The taxable amount "
                        "of the IRA distributions is not figured while it is unknown.", f, "2")
            st.taxable_trad = net
            st.early_base = code1
            return
        spouse_covered = self._spouse_covered(owner)
        limited_possible = st.contributions > 0 and (st.covered or spouse_covered)
        election = min(hand.nondeductible_election, st.contributions) if hand and hand.nondeductible_election else Z
        if st.basis_prior == 0 and not limited_possible and election == 0:
            # No basis and nothing that can become basis: the whole amount is taxable (box 2a when the payer determined it).
            st.taxable_trad = sum((pos((d.taxable_amount if d.taxable_amount is not None else d.gross_distribution)
                                       - d.rollover_amount - d.qcd_amount) for d in trad), Z)
            st.early_base = st.taxable_trad * (code1 / net) if net > 0 else Z
            return
        st.part_i = st.required = True
        if qcd > 0:
            self.s.diag("error", "form_8606_qcd_with_basis",
                        f"Form 8606 for the {owner}: a qualified charitable distribution from an IRA with basis is not supported "
                        "(the QCD is treated as made from the taxable part first, Pub. 590-B).", f, "7")
        if not trad_accounts:
            self.s.diag("error", "form_8606_fmv_unknown",
                        f"Form 8606 line 6 for the {owner}: no Form 5498 of a traditional IRA is on the return, so the value of all "
                        f"traditional IRAs on December 31, {y} is unknown; the nontaxable part cannot be figured.", f, "6")
        fmv = sum((a.fmv or Z for a in trad_accounts), Z) + (hand.outstanding_rollovers if hand else Z)
        if limited_possible:
            # Pub. 590-B Worksheet 1-1 (contributions for the year may not be fully deductible): the taxable part is figured
            # with all contributions in the basis; lines 13 and 17 take its line 8 and lines 6-12 are not completed.
            st.worksheet_1_1 = True
            w1 = st.basis_prior
            w2 = st.contributions
            w3 = w1 + w2
            w4 = fmv
            w5 = net
            w6 = w4 + w5
            w7 = ratio3(w3, w6)
            w8 = whole(w5 * w7)
            w9 = w5 - w8
            w10 = whole(w9 * (l8 / w5)) if w5 > 0 else Z
            w11 = w9 - w10
            st.facts["worksheet_1_1"] = {"1": str(whole(w1)), "2": str(whole(w2)), "3": str(whole(w3)), "4": str(whole(w4)),
                                         "5": str(whole(w5)), "6": str(whole(w6)), "7": str(w7), "8": str(w8), "9": str(whole(w9)),
                                         "10": str(w10), "11": str(whole(w11))}
            st.lines.update({"7": l7, "8": l8, "13": w8, "15a": w11, "15c": w11, "16": l8, "17": w8, "18": w10})
            st.taxable_trad, st.taxable_conversion = w11, w10
        else:
            l1 = election
            l2 = st.basis_prior
            l3 = l1 + l2
            l4 = Z
            if l1 > 0:
                if hand is None or hand.contributions_after_year_end is None:
                    self.s.diag("error", "form_8606_late_contributions_unknown",
                                f"Form 8606 line 4 for the {owner}: the part of the {y} contributions made from January 1 through "
                                f"April 15, {y + 1} is not stated (ira_facts[].contributions_after_year_end; enter 0 if none).", f, "4")
                else:
                    l4 = min(hand.contributions_after_year_end, l1)
            l5 = l3 - l4
            l6 = fmv
            l9 = l6 + l7 + l8
            l10 = ratio3(l5, l9)
            l11 = whole(l8 * l10)
            l12 = whole(l7 * l10)
            l13 = l11 + l12
            st.facts["10"] = str(l10)
            st.lines.update({"1": l1, "2": l2, "3": l3, "4": l4, "5": l5, "6": l6, "7": l7, "8": l8, "9": l9, "11": l11, "12": l12,
                             "13": l13, "14": l3 - l13, "15a": l7 - l12, "15c": l7 - l12, "16": l8, "17": l11, "18": l8 - l11})
            st.taxable_trad, st.taxable_conversion = l7 - l12, l8 - l11
        st.early_base = st.taxable_trad * (code1 / l7) if l7 > 0 else Z

    def _spouse_covered(self, owner: Owner) -> bool:
        """Whether the other spouse is an active participant: from their documents on a joint return, else from their
        stated fact (ira_facts with owner spouse on a separate return)."""
        if not self.married:
            return False
        other: Owner = "spouse" if owner == "taxpayer" else "taxpayer"
        hand = next((x for x in self.r.ira_facts if x.owner == other), None)
        return self._covered(other, hand, [a for a in self.r.ira_accounts if a.owner == other])

    def _form_8606_part_iii(self, owner: Owner, st: _IRA, hand: IRAFacts | None, roth: list[Retirement], f: str) -> None:
        """Part III: nonqualified Roth IRA distributions come first from regular contributions, then conversions, then
        earnings (IRC §408A(d)(4); Pub. 590-B, Ordering Rules for Distributions)."""
        py = self.r.prior_year
        basis_prior = getattr(py, carryforward_key("roth_ira_basis", owner)) if py is not None else None
        conv_prior = getattr(py, carryforward_key("roth_conversion_basis", owner)) if py is not None else None
        five_year = hand.roth_five_year_period_met if hand else None
        nonqualified: list[Retirement] = []
        for d in roth:
            code = d.distribution_code.upper()
            if "Q" in code or ("T" in code and five_year is True):
                continue
            if "T" in code and five_year is None:
                self.s.diag("error", "form_8606_roth_five_year_unknown",
                            f"1099-R from {d.payer or 'payer'} (code T) for the {owner}: whether the 5-year period of the Roth IRA is met "
                            "is not stated (ira_facts[].roth_five_year_period_met); a qualified distribution is tax free, any other "
                            "goes through Form 8606 Part III.", f, "19")
                continue
            nonqualified.append(d)
        if not nonqualified:
            st.roth_basis_next = None if basis_prior is None else basis_prior + st.roth_contributions
            st.conversion_basis_next = None if conv_prior is None else conv_prior + st.conversions
            return
        st.required = True
        l19 = sum((pos(d.gross_distribution - d.rollover_amount) for d in nonqualified), Z)
        l20 = min(hand.first_time_homebuyer_expenses if hand else Z, Decimal(10000))
        l21 = pos(l19 - l20)
        st.lines.update({"19": l19, "20": l20, "21": l21})
        if basis_prior is None:
            self.s.diag("error", "form_8606_roth_basis_unknown",
                        f"Form 8606 line 22 for the {owner}: the basis in regular Roth IRA contributions before {self.y} is not stated "
                        "(prior_year.roth_ira_basis; enter 0 if none). The taxable part of the Roth IRA distribution is not figured "
                        "while it is unknown.", f, "22")
            st.taxable_roth = l21
            return
        l22 = basis_prior + st.roth_contributions
        l23 = pos(l21 - l22)
        st.lines.update({"22": l22, "23": l23})
        st.roth_basis_next = pos(l22 - l21)
        if l23 <= 0:
            st.conversion_basis_next = None if conv_prior is None else conv_prior + st.conversions
            return
        if conv_prior is None:
            self.s.diag("error", "form_8606_conversion_basis_unknown",
                        f"Form 8606 line 24 for the {owner}: the basis in conversions and plan rollovers to Roth IRAs before {self.y} is "
                        "not stated (prior_year.roth_conversion_basis; enter 0 if none).", f, "24")
            st.taxable_roth = l23
            return
        l24 = conv_prior + st.conversions
        l25a = pos(l23 - l24)
        st.lines.update({"24": l24, "25a": l25a, "25c": l25a})
        st.conversion_basis_next = pos(l24 - l23)
        st.taxable_roth = l25a
        if any("J" in d.distribution_code.upper() for d in nonqualified):
            self.s.diag("error", "form_5329_required",
                        f"Form 8606 line 23 for the {owner} is {whole(l23)}: an early Roth IRA distribution beyond the regular "
                        "contributions needs Form 5329 Part I (the 10% additional tax, and the recapture of conversions within 5 "
                        "years), which is not supported.", f, "23")

    def _retirement(self) -> None:
        """Form 1040 lines 4a-5b. IRA and Roth IRA distributions of a person whose Form 8606 state is known take their
        taxable amounts from it; pensions use box 2a, or the whole amount with a warning when the payer did not
        determine it (the Simplified Method is not computed)."""
        ira_gross = ira_taxable = pen_gross = pen_taxable = early_tax = Z
        for d in self.r.retirement:
            code = d.distribution_code.upper()
            is_ira = d.ira_sep_simple or self._is_roth_distribution(d)
            if is_ira and d.owner in self._ira:
                ira_gross += d.gross_distribution
                continue
            if d.taxable_amount is None:
                taxable = d.gross_distribution
                self.s.diag("warning", "1099r_taxable_not_determined",
                            f"1099-R from {d.payer or 'payer'}: taxable amount not determined; the full distribution is "
                            "treated as taxable. Figure basis (Form 8606 or the Simplified Method).", "f1040", "4b")
            else:
                taxable = d.taxable_amount
            taxable = pos(taxable - d.rollover_amount - (d.qcd_amount if d.ira_sep_simple else Z))
            if is_ira:
                ira_gross += d.gross_distribution
                ira_taxable += taxable
            else:
                pen_gross += d.gross_distribution
                pen_taxable += taxable
            if "1" in code or "S" in code:
                rate = Decimal("0.25") if "S" in code else TEN_PERCENT
                early_tax += pos(taxable - d.early_distribution_exception) * rate
                if d.early_distribution_exception > 0:
                    self.s.diag("info", "form_5329_exception", "An early-distribution exception was claimed: Form 5329 is required.")
        for st in self._ira.values():
            ira_taxable += st.taxable_trad + st.taxable_conversion + st.taxable_roth
            early_tax += st.early_base * TEN_PERCENT
        self.set("f1040", "4a", ira_gross)
        self.set("f1040", "4b", ira_taxable)
        self.set("f1040", "5a", pen_gross)
        self.set("f1040", "5b", pen_taxable)
        self._early_distribution_tax = early_tax

    def _employer_hsa_contributions(self, owner: Owner) -> Decimal:
        """Form 8889 line 9: employer contributions (including payroll contributions through a cafeteria plan), W-2 box 12 code W."""
        return sum((amt for w in self.r.w2s if w.owner == owner for code, amt in w.box12.items() if code.upper() == "W"), Z)

    def _form_8889(self) -> None:
        """Form 8889 per HSA beneficiary: Part I (the deduction, Schedule 1 line 13), Part II (taxable distributions,
        Schedule 1 line 8f, and the 20% tax, Schedule 2 line 13c), Part III (the prior year's last-month rule failing its
        testing period, Schedule 1 line 8f and the 10% tax, Schedule 2 line 13d). Contributions come from Form 5498-SA and
        W-2 code W, distributions from Form 1099-SA; coverage, use and allocation facts from hsa_facts."""
        r = self.r
        if r.adjustments.hsa_deduction:
            self.s.diag("error", "form_8889_deprecated_input",
                        "adjustments.hsa_deduction is no longer an input: the HSA deduction is figured on Form 8889 from Forms 5498-SA "
                        "and 1099-SA, W-2 box 12 code W and the hsa_facts. Remove it.", "sch_1", "13")
        stated = {x.owner: x for x in r.hsa_facts}
        owners = [o for o in self.owners() if o in stated or any(c.owner == o for c in r.hsa_contributions)
                  or any(d.owner == o for d in r.hsa_distributions) or self._employer_hsa_contributions(o) > 0]
        if not owners:
            return
        lim = self.ctx.try_param("us_fed.individual.hsa_contribution_limit", self.on)
        if lim is None:
            self.s.diag("error", "hsa_contribution_limit_rule_missing",
                        f"No published HSA contribution limits cover {self.on.isoformat()} (us_fed.individual.hsa_contribution_limit): "
                        "Form 8889 is not figured.", "sch_1", "13")
            return
        # Spouses who both have HSAs are both treated as having family coverage when either has it (Form 8889 instructions,
        # "How To Complete Part I"; Pub. 969, "Rules for married people").
        both = self.joint and all(any(c.owner == o for c in r.hsa_contributions) or self._employer_hsa_contributions(o) > 0
                                  for o in ("taxpayer", "spouse"))
        family_any = any(getattr(stated[o], f"coverage_{m}") == "family" for o in owners if o in stated for m in MONTHS)
        for owner in owners:
            self._form_8889_owner(owner, stated.get(owner), stated, lim, treat_family=both and family_any)

    def _form_8889_owner(self, owner: Owner, hf: HSAFacts | None, stated: dict[Owner, HSAFacts], lim: dict[str, Any], *,
                         treat_family: bool) -> None:
        r, y = self.r, self.y
        f = f"f8889[{owner}]"
        p = self.person(owner)
        contribs = [c for c in r.hsa_contributions if c.owner == owner]
        dists = [d for d in r.hsa_distributions if d.owner == owner]
        if any(c.account_type in ("archer_msa", "ma_msa") or (c.archer_msa_contributions or Z) > 0 for c in contribs) or any(
                d.account_type in ("archer_msa", "ma_msa") for d in dists):
            self.s.diag("error", "form_8853_required",
                        f"An Archer MSA or Medicare Advantage MSA of the {owner} needs Form 8853, which is not supported.", f)
            return
        employer = self._employer_hsa_contributions(owner)
        funding = hf.qualified_funding_distribution if hf else Z
        for_last_year = hf.contributions_for_last_year if hf else Z
        box_total = sum(((c.total_contributions or Z) + (c.following_year_contributions or Z) for c in contribs), Z)
        has_contributions = bool(contribs) or employer > 0 or funding > 0
        problems: list[tuple[str, str, str | None]] = []
        self_limit, family_limit, catch = D(lim["self_only"]), D(lim["family"]), D(lim["catch_up"])
        lines: dict[str, Decimal] = {}
        facts: dict[str, Any] = {}
        carry = Z
        # ---------------------------------------------------------------- Part I
        if has_contributions:
            if not contribs:
                problems.append(("form_8889_contributions_unknown",
                                 f"Form 8889 line 2 for the {owner}: W-2 box 12 code W shows employer contributions of {whole(employer)} but "
                                 "no Form 5498-SA is on the return, so the year's total HSA contributions are unknown.", "2"))
            coverage = [getattr(hf, f"coverage_{m}") if hf else None for m in MONTHS]
            if any(c is None for c in coverage):
                problems.append(("form_8889_coverage_unknown",
                                 f"Form 8889 line 3 for the {owner}: the HDHP coverage on the first day of each month of {y} is not stated "
                                 "(hsa_facts[].coverage_01 to coverage_12: self_only, family or none). The deduction is not figured "
                                 "while a month is unknown.", "3"))
            if p is None or p.dob is None:
                problems.append(("form_8889_age_unknown",
                                 f"Form 8889 for the {owner}: the date of birth is needed for the age-55 additional contribution and the "
                                 "age-65 exception.", None))
            if not problems and hf is not None and p is not None:
                cov: list[str] = [str(c) for c in coverage]
                if treat_family:
                    cov = ["family" if c != "none" else "none" for c in cov]
                dependent = p.can_be_claimed_as_dependent
                medicare = hf.medicare_from_month
                eligible = [c != "none" and not dependent and not (medicare is not None and i + 1 >= medicare) for i, c in enumerate(cov)]
                age55 = self.age_at_year_end(p, 55)
                married_family_any = self.married and any(c == "family" for c, e in zip(cov, eligible) if e)
                chart: list[Decimal] = []
                for c, e in zip(cov, eligible):
                    if not e:
                        chart.append(Z)
                    elif c == "self_only":
                        chart.append(self_limit + (catch if age55 and not married_family_any else Z))
                    else:
                        chart.append(family_limit + (catch if age55 and not self.married else Z))
                chart_total = sum(chart, Z)
                chart_limit = chart_total / 12
                facts["chart"] = {m: str(whole(a)) for m, a in zip(MONTHS, chart)}
                facts["chart_total"] = str(whole(chart_total))
                if eligible[11]:
                    # Last-month rule (§223(b)(8)): eligible on December 1, so treated as eligible all year with that coverage.
                    full = family_limit if cov[11] == "family" else self_limit
                    if age55 and not married_family_any:
                        full += catch
                    l3 = max(chart_limit, full)
                    facts["last_month_rule"] = l3 > chart_limit
                    facts["1"] = cov[11]
                else:
                    l3 = chart_limit
                    facts["last_month_rule"] = False
                    kinds = [c for c, e in zip(cov, eligible) if e]
                    facts["1"] = "family" if kinds.count("family") > kinds.count("self_only") else "self_only"
                n_eligible = sum(1 for e in eligible if e)
                l7 = catch * n_eligible / 12 if age55 and married_family_any else Z
                l5 = l3                                         # line 4 (Archer MSA contributions) is zero: Form 8853 is refused above
                l6 = l5
                if treat_family and n_eligible > 0:
                    if n_eligible < 12:
                        problems.append(("form_8889_split_coverage_spouses",
                                         f"Form 8889 line 6 for the {owner}: spouses who both have HSAs and were not treated as having "
                                         "family coverage for every month must refigure the limit for the family months (instructions, "
                                         "line 6, Steps 1-4), which is not supported.", "6"))
                    other: Owner = "spouse" if owner == "taxpayer" else "taxpayer"
                    mine, theirs = hf.family_limit_share, (stated[other].family_limit_share if other in stated else None)
                    if mine is None and theirs is None:
                        l6 = l5 / 2
                    elif mine is not None:
                        l6 = mine
                        if theirs is not None and mine + theirs != l5:
                            problems.append(("form_8889_family_allocation_mismatch",
                                             f"Form 8889 line 6: the spouses' agreed shares of the family limit ({whole(mine)} and "
                                             f"{whole(theirs)}) do not add up to the limit of {whole(l5)}.", "6"))
                    else:
                        l6 = pos(l5 - theirs) if theirs is not None else l5
                elif self.mfs and any(c == "family" for c, e in zip(cov, eligible) if e):
                    if hf.family_limit_share is None:
                        problems.append(("form_8889_family_allocation_unknown",
                                         f"Form 8889 line 6 for the {owner}: married filing separately with family HDHP coverage, the share "
                                         "of the family limit allocated to this spouse is not stated (hsa_facts[].family_limit_share; the "
                                         "whole limit if the other spouse has no HSA).", "6"))
                    else:
                        l6 = hf.family_limit_share
                l8 = l6 + l7
                l9 = employer
                l10 = funding
                l11 = l9 + l10
                l12 = pos(l8 - l11)
                l2 = box_total - employer - funding - for_last_year
                if l2 < 0:
                    problems.append(("form_8889_contributions_inconsistent",
                                     f"Form 8889 line 2 for the {owner}: Form 5498-SA boxes 2 and 3 total {whole(box_total)}, less than the "
                                     f"employer contributions ({whole(employer)}), funding distribution and prior-year contributions "
                                     "taken out of it. Check the documents.", "2"))
                    l2 = Z
                l13 = min(l2, l12)
                if l2 > l13:
                    problems.append(("form_5329_excess_hsa_contributions",
                                     f"Form 8889 for the {owner}: contributions of {whole(l2)} exceed the deductible limit of {whole(l12)}; "
                                     "the excess owes the 6% tax of Form 5329 (not supported) unless withdrawn with its earnings by the "
                                     "due date, after which the 5498-SA amounts and hsa_facts are entered as corrected.", "13"))
                if employer > pos(l8 - l10):
                    problems.append(("form_8889_excess_employer_contributions",
                                     f"Form 8889 for the {owner}: employer contributions of {whole(employer)} exceed the limit of "
                                     f"{whole(pos(l8 - l10))}; the excess is other income (instructions, Excess Employer Contributions), "
                                     "which is not supported.", "9"))
                lines.update({"2": l2, "3": l3, "5": l5, "6": l6, "7": l7, "8": l8, "9": l9, "10": l10, "11": l11, "12": l12, "13": l13})
                contributed = l2 + l9 + l10
                if facts["last_month_rule"]:
                    carry = pos(min(contributed, l8) - chart_limit - l7)
        # ---------------------------------------------------------------- Part II
        if dists:
            codes = {str(d.distribution_code) for d in dists}
            if codes & {"2", "5"}:
                problems.append(("form_8889_distribution_code_unsupported",
                                 f"Form 1099-SA box 3 code {sorted(codes & {'2', '5'})[0]} for the {owner}: a returned excess contribution or "
                                 "a prohibited-transaction deemed distribution is not supported (its earnings or value are other income).", "14a"))
            if codes & {"4", "6"}:
                problems.append(("form_8889_death_distribution",
                                 f"Form 1099-SA box 3 code 4 or 6 for the {owner}: a distribution on the account beneficiary's death is "
                                 "reported on the beneficiary's own Form 8889 (fair market value at death), which is not supported.", "14a"))
            l14a = sum((d.gross_distribution or Z for d in dists), Z)
            l14b = hf.rollovers_and_withdrawn_excess if hf else Z
            l14c = pos(l14a - l14b)
            l15 = l16 = Z
            if l14c > 0:
                if hf is None or hf.qualified_medical_expenses is None:
                    problems.append(("form_8889_medical_expenses_unknown",
                                     f"Form 8889 line 15 for the {owner}: the distributions used for qualified medical expenses are not stated "
                                     "(hsa_facts[].qualified_medical_expenses; enter 0 if none). The taxable distributions are not figured "
                                     "while it is unknown.", "15"))
                else:
                    l15 = min(hf.qualified_medical_expenses, l14c)
                    l16 = l14c - l15
            l17b = Z
            if l16 > 0 and p is not None:
                exempt: Decimal | None
                if codes <= {"3"} or (p.dob is not None and p.dob <= date(y - 65, 1, 1)):
                    exempt = l16                                  # disability, or 65 before the year began: no 20% tax
                elif hf is not None and hf.additional_tax_exception is not None:
                    exempt = min(hf.additional_tax_exception, l16)
                elif p.dob is None or p.dob <= date(y - 65, 12, 31) or "3" in codes:
                    exempt = None
                    problems.append(("form_8889_exception_unknown",
                                     f"Form 8889 line 17a for the {owner}: the part of the taxable distributions made after turning 65, or "
                                     "because of disability or death, is not stated (hsa_facts[].additional_tax_exception; enter 0 if none).", "17a"))
                else:
                    exempt = Z
                if exempt is not None:
                    facts["17a"] = exempt > 0
                    l17b = (l16 - exempt) * TWENTY_PERCENT
            lines.update({"14a": l14a, "14b": l14b, "14c": l14c, "15": l15, "16": l16, "17b": l17b})
        # ---------------------------------------------------------------- Part III
        if hf is not None and hf.testing_period_failed:
            py = r.prior_year
            prior = getattr(py, carryforward_key("hsa_last_month_rule_excess", owner)) if py is not None else None
            if prior is None:
                problems.append(("form_8889_last_month_rule_excess_unknown",
                                 f"Form 8889 line 18 for the {owner}: the {y - 1} contributions allowed only by the last-month rule are not "
                                 f"stated (prior_year.{carryforward_key('hsa_last_month_rule_excess', owner)}, from the {y - 1} Form 8889's "
                                 "Line 3 Limitation Chart).", "18"))
            else:
                l18 = prior
                l20 = l18                                         # line 19 (a failed qualified HSA funding distribution) is not modelled
                lines.update({"18": l18, "19": Z, "20": l20, "21": l20 * TEN_PERCENT})
        if problems:
            for code, message, line in problems:
                self.s.diag("error", code, message, f, line)
            return
        for k, v in lines.items():
            self.set(f, k, v)
        for k, v in facts.items():
            self.s.fact(f, k, v)
        if has_contributions:
            self._carryforwards[carryforward_key("hsa_last_month_rule_excess", owner)] = whole(carry)
        self._hsa[owner] = {k: self.g(f, k) for k in ("13", "16", "17b", "20", "21")}

    # ----------------------------------------------------------------- Form 4797
    def _form_4797(self) -> None:
        """Form 4797, Sales of Business Property (the 2025 form and instructions; the 2026 form is not posted and nothing on
        it is indexed). Part I nets the section 1231 gains and losses of property used in a trade or business and held more
        than 1 year (IRC §1231(a), (b)(1)) after the recapture of Part III: a net gain is long-term capital gain (Schedule D
        line 11), a net loss ordinary (line 11), and a net gain is ordinary to the extent of the nonrecaptured net section
        1231 losses of the 5 preceding years (§1231(c); lines 8, 9 and 12), applied earliest loss first (Pub. 544 (2025),
        Nonrecaptured section 1231 losses). Part II collects the ordinary gains and losses (property held 1 year or less on
        line 10, lines 11-13) to Schedule 1 line 4 (line 18b). Part III figures the recapture: section 1245 property is
        ordinary to the extent of the depreciation allowed or allowable (§1245(a)(1); lines 25a-25b), section 1250 property
        to the extent of the applicable percentage of the additional depreciation over straight line (§1250(a)(1), (b)(1);
        lines 26a-26g; 100% for an individual's property other than low-income housing, us_fed.individual.section_1250_
        applicable_percentage; the 20% of §291(a)(1) on line 26f is a C corporation's); the rest of the gain (line 32) joins
        Part I (line 6), and the part of it due to depreciation is unrecaptured section 1250 gain (§1(h)(6)) for the
        Schedule D line 19 worksheet (_unrecaptured_1250_worksheet). Part IV recaptures the §179 deduction (§179(d)(10)) or
        the §280F(b)(2) excess depreciation when business use drops to 50% or less, as other income of the Schedule C that
        took the deduction. For the AMT the gain or loss may differ (§56(a)(1), (6); Form 6251 lines 2k and 2l): AMT
        depreciation is not computed, so a depreciable disposition raises a warning until the adjustment is stated."""
        r = self.r
        st = self._f4797
        f = "f4797"
        items: list[dict[str, Any]] = []
        for n, d in enumerate(r.dispositions, 1):
            item = self._disposition(n, d)
            if item is not None:
                items.append(item)
        k1_rows: list[dict[str, Any]] = []
        for i, k in enumerate(r.k1s, 1):
            if k.net_section_1231_gain:
                k1_rows.append({"description": f"Schedule K-1 {k.entity_name}, net section 1231 gain or (loss)", "gain": k.net_section_1231_gain,
                                "activity": ("k1", i), "entire_interest": False, "ordinary": Z, "section_1231": k.net_section_1231_gain})
        part_iv = self._form_4797_part_iv()
        if not items and not k1_rows and not part_iv:
            return
        st.filed = True
        rows_i: list[dict[str, Any]] = []
        rows_ii: list[dict[str, Any]] = []
        cols = 0
        for item in items:
            if not item["long"]:                                  # held 1 year or less: Part II line 10, whatever the class
                item["part"], item["ordinary"] = "II", item["gain"]
                rows_ii.append(item)
            elif item["gain"] <= 0 or item["class"] in ("land", "other"):   # a §1231 loss, or a gain with nothing to recapture
                item["part"], item["section_1231"] = "I", item["gain"]
                rows_i.append(item)
            else:
                self._form_4797_part_iii(item, part_iii_column(cols))
                cols += 1
        if cols > 4:
            self.s.diag("info", "form_4797_additional_part_iii", f"{cols} properties in Part III: columns beyond D go on an additional Form 4797.", f)

        def describe(row: dict[str, Any]) -> dict[str, str]:
            return {"description": row["description"], "acquired": str(row["acquired"]), "sold": str(row["sold"]),
                    "sales_price": str(whole(row["price"])), "depreciation": str(whole(row["dep"])), "basis_and_expenses": str(whole(row["basis"])),
                    "gain": str(whole(row["gain"])), "class": row["class"], "source": row["source"]}

        # Part I: line 2 rows (the Schedule K-1 net section 1231 amounts are entered in Part I, instructions for line 7).
        self.s.fact(f, "part_i", [describe(x) for x in rows_i] + [{"description": x["description"], "gain": str(whole(x["gain"]))} for x in k1_rows])
        line_2 = self.set(f, "2", sum((x["gain"] for x in rows_i), Z) + sum((x["gain"] for x in k1_rows), Z),
                          "Section 1231 gains and losses not reported in Part III (and Schedule K-1 net section 1231 amounts)")
        self.set(f, "6", self.g(f, "32"), "Gain from line 32, from other than casualty or theft")
        line_7 = self.set(f, "7", line_2 + self.g(f, "6"), "Net section 1231 gain or (loss)")
        st.line_7 = line_7
        line_12 = Z
        if line_7 > 0:
            line_8 = self._section_1231_lookback(line_7)
            st.line_8 = line_8
            if line_8 > 0:
                self.set(f, "8", line_8, "Nonrecaptured net section 1231 losses from prior years (IRC §1231(c))")
                line_9 = self.set(f, "9", pos(line_7 - line_8))
                line_12 = line_7 if line_9 == 0 else line_8
                st.to_schedule_d = line_9
            else:
                st.to_schedule_d = line_7
        else:
            self._section_1231_lookback(line_7)                   # prior losses stay unapplied; this year's loss joins them
        # Part II.
        self.s.fact(f, "part_ii", [describe(x) for x in rows_ii])
        self.set(f, "10", sum((x["gain"] for x in rows_ii), Z), "Ordinary gains and losses, property held 1 year or less")
        self.set(f, "11", min(line_7, Z), "Loss, if any, from line 7")
        self.set(f, "12", line_12, "Gain from line 7 treated as ordinary income (IRC §1231(c)), or the amount from line 8")
        self.set(f, "13", self.g(f, "31"), "Gain from line 31 (recapture)")
        line_17 = self.set(f, "17", sum((self.g(f, x) for x in ("10", "11", "12", "13", "14", "15", "16")), Z))
        self.set(f, "18b", line_17, "To Schedule 1, line 4")
        st.ordinary = line_17
        # Where the gains belong: Form 8960 line 5b, the QBI of a Schedule C, the §469(g) release of Form 8582.
        ordinary_share = Decimal(1) if line_7 <= 0 else (line_12 / line_7 if line_12 else Z)
        released: list[dict[str, Any]] = []
        for item in items + k1_rows:
            activity = item["activity"]
            if activity is None:
                st.niit_unplaced += 1
            else:
                kind, idx = activity
                if self._non_section_1411(kind, idx):
                    st.niit_excluded -= item["gain"]
                if kind == "schedule_c":
                    st.qbi_ordinary[idx] = st.qbi_ordinary.get(idx, Z) + item["ordinary"] + item["section_1231"] * ordinary_share
            if item["entire_interest"]:
                if activity is None:
                    self.s.diag("error", "form_4797_activity_unknown",
                                f"{item['description']}: the entire interest in an activity was disposed of, but no activity is named "
                                "(schedule_c, rental or k1).", f)
                    continue
                passive = self._passive_activity(*activity)
                released.append({"description": item["description"], "activity": f"{activity[0]}[{activity[1]}]", "passive": passive})
                if passive:
                    self.s.diag("warning", "form_8582_release_not_computed",
                                f"{item['description']}: the entire interest in passive activity {activity[0]}[{activity[1]}] was disposed of in a "
                                "fully taxable transaction (IRC §469(g)(1)(A)); its suspended losses are allowed in full on Form 8582, "
                                "which is not computed yet.", "f8582")
        if released:
            self.s.fact(f, "section_469g_dispositions", released)
        if st.depreciable and "disposition" not in r.amt_adjustments:
            self.s.diag("warning", "form_6251_disposition_adjustment_unverified",
                        "A depreciable asset was disposed of: its AMT basis may differ from the regular tax basis (property depreciated 200% "
                        "declining balance after 1998, section 1250 property not depreciated straight line), so the AMT gain or loss may "
                        "differ (IRC §56(a)(1), (6); Form 6251 lines 2k and 2l). AMT depreciation is not computed: state amt_adjustments "
                        "'disposition' (and 'depreciation'), 0 when the bases are the same.", "f6251", "2k")

    def _disposition(self, n: int, d: Disposition) -> dict[str, Any] | None:
        """One Form 4797 row with every fact it needs, or None when it cannot be figured (each reason is a blocking
        diagnostic; nothing is taken as zero). The gain is column (g): (d) gross sales price plus (e) depreciation less (f)
        cost or other basis plus the expense of sale, the same as line 24 (line 20 less the adjusted basis of line 23)."""
        f = "f4797"
        where = f"dispositions[{n}] {d.description}"
        bad = False
        for attr, code, message in UNSUPPORTED_DISPOSITIONS:
            if getattr(d, attr):
                self.s.diag("error", code, f"{where}: {message}.", f)
                bad = True
        links = [(k, getattr(d, k)) for k in ("schedule_c", "rental", "k1") if getattr(d, k) is not None]
        activity: tuple[str, int] | None = None
        if len(links) > 1:
            self.s.diag("error", "form_4797_activity_ambiguous", f"{where}: name one activity (schedule_c, rental or k1), not several.", f)
            bad = True
        elif links:
            kind, idx = links[0]
            count = {"schedule_c": len(self.r.businesses), "rental": len(self.r.rentals), "k1": len(self.r.k1s)}[kind]
            if not 1 <= idx <= count:
                self.s.diag("error", "form_4797_activity_unknown", f"{where}: {kind} {idx} does not exist on this return ({count} on the return).", f)
                bad = True
            else:
                activity = (kind, idx)
        price, basis, dep, cls = d.gross_sales_price, d.cost_or_basis, d.depreciation_allowed, d.property_class
        acquired: Any = d.acquired
        source = "stated"
        register_failed = False                              # the register knows the asset but could not figure its depreciation
        if d.asset_id is not None:
            a = self.assets.get(d.asset_id)
            if a is None:
                self.s.diag("error", "form_4797_asset_unknown", f"{where}: asset {d.asset_id!r} is not in the client's asset register.", f)
                bad = True
            else:
                reg_dep = self._register_depreciation(a, d.sold, where)
                register_failed = reg_dep is None
                for stated, reg, name in ((basis, a.cost, "cost_or_basis"), (dep, reg_dep, "depreciation_allowed")):
                    if stated is not None and reg is not None and whole(stated) != whole(reg):
                        self.s.diag("error", "form_4797_asset_register_conflict",
                                    f"{where}: {name} is stated as {whole(stated)} but the asset register gives {whole(reg)} for {a.id}.", f)
                        bad = True
                if cls not in (None, "section_1245"):
                    self.s.diag("error", "form_4797_asset_register_conflict",
                                f"{where}: a registered MACRS {a.recovery_years}-year asset is section 1245 property, not {cls}.", f)
                    bad = True
                basis = a.cost if basis is None else basis
                dep = reg_dep if dep is None else dep
                cls = cls or "section_1245"
                acquired = acquired or a.acquired
                source = f"asset register {a.id}"
        for value, code, name in ((price, "form_4797_sales_price_unknown", "gross sales price (column (d), line 20)"),
                                  (basis, "form_4797_basis_unknown", "cost or other basis (column (f), line 21)"),
                                  (dep, "form_4797_depreciation_unknown", "depreciation allowed or allowable (column (e), line 22; 0 for land)")):
            if value is None and not (register_failed and code == "form_4797_depreciation_unknown"):
                self.s.diag("error", code, f"{where}: the {name} is not stated; it is never taken as zero.", f)
                bad = True
        bad = bad or register_failed
        if cls is None:
            self.s.diag("error", "form_4797_property_class_unknown",
                        f"{where}: state the property class (section_1245, section_1250, land or other); Part III cannot be placed without it.", f)
            bad = True
        if d.sold.year != self.y:
            self.s.diag("error", "form_4797_sale_date_outside_year", f"{where}: sold {d.sold}, outside tax year {self.y}.", f)
            bad = True
        if bad or price is None or basis is None or dep is None or cls is None:
            return None
        if d.holding_period is not None:
            long = d.holding_period == "long"
        elif acquired == "inherited":
            long = True
        elif isinstance(acquired, date):
            long = held_long_term(acquired, d.sold)
        else:
            self.s.diag("error", "form_4797_holding_period_unknown", f"{where}: state the date acquired or the holding period.", f)
            return None
        if cls in ("land", "other") and dep > 0:
            self.s.diag("error", "form_4797_land_depreciated",
                        f"{where}: {cls} carries no depreciation ({whole(dep)} stated); a depreciable asset is section_1245 or section_1250 property.", f)
            return None
        if cls == "section_1250" and isinstance(acquired, date) and acquired < date(1976, 1, 1):
            self.s.diag("error", "form_4797_pre_1976_property",
                        f"{where}: section 1250 property acquired before 1976 needs the additional depreciation of 1970-1975 (line 26d), not supported.", f)
            return None
        gross_basis = basis + d.selling_expenses
        gain = price + dep - gross_basis
        if cls == "section_1250" and long and gain > 0 and d.additional_depreciation is None:
            self.s.diag("error", "form_4797_additional_depreciation_unknown",
                        f"{where}: state the additional depreciation (depreciation over straight line, line 26a; 0 for straight-line MACRS real "
                        "property): the section 1250 recapture cannot be figured without it.", f)
            return None
        if dep > 0 and cls in ("section_1245", "section_1250"):
            self._f4797.depreciable = True
        return {"n": n, "description": d.description, "acquired": acquired, "sold": d.sold, "price": price, "dep": dep, "basis": gross_basis,
                "gain": gain, "long": long, "class": cls, "activity": activity, "ordinary": Z, "section_1231": Z, "source": source,
                "entire_interest": d.entire_interest_disposed, "additional_depreciation": d.additional_depreciation}

    def _register_depreciation(self, a: Asset, sold: date, where: str) -> Decimal | None:
        """Depreciation allowed or allowable on a registered asset through the year of sale (calc/federal.py tax_depreciation:
        §179, bonus, then MACRS from the half-year table): half of the table amount in the year of disposition (half-year
        convention, Pub. 946; Pub. 544 (2025) Section 1245 example: "$960 (1/2 of $1,920)" in the year of sale). An asset
        disposed of in the year it was placed in service gets no depreciation and is not modelled here."""
        first = a.placed_in_service.year
        if sold.year <= first:
            self.s.diag("error", "form_4797_asset_disposed_in_service_year",
                        f"{where}: asset {a.id} was placed in service in {first} and sold in {sold.year}; no depreciation is allowed for property "
                        "placed in service and disposed of in the same year (Pub. 946), not supported.", "f4797")
            return None
        total = Z
        for year in range(first, sold.year + 1):
            amount = tax_depreciation(self.ctx, a, year)
            total += amount / 2 if year == sold.year else amount
        return total

    def _form_4797_part_iii(self, item: dict[str, Any], col: str) -> None:
        """One Part III column (lines 19-26) for a depreciable asset held more than 1 year and sold at a gain; the recapture
        goes to line 31 (Part II line 13), the rest to line 32 (Part I line 6)."""
        f = "f4797"
        item["part"] = f"III-{col}"
        self.s.fact(f, f"19{col}", {"description": item["description"], "acquired": str(item["acquired"]), "sold": str(item["sold"]),
                                     "class": item["class"], "source": item["source"]})
        self.set(f, f"20{col}", item["price"], "Gross sales price")
        l21 = self.set(f, f"21{col}", item["basis"], "Cost or other basis plus expense of sale")
        l22 = self.set(f, f"22{col}", item["dep"], "Depreciation allowed or allowable")
        l23 = self.set(f, f"23{col}", l21 - l22, "Adjusted basis")
        l24 = self.set(f, f"24{col}", self.g(f, f"20{col}") - l23, "Total gain")
        if item["class"] == "section_1245":
            l25a = self.set(f, f"25a{col}", l22)
            recapture = self.set(f, f"25b{col}", min(l24, l25a), "Ordinary income: depreciation recapture (IRC §1245(a)(1))")
        else:
            pct = self.dec("us_fed.individual.section_1250_applicable_percentage")
            l26a = self.set(f, f"26a{col}", item["additional_depreciation"], "Additional depreciation after 1975 (IRC §1250(b)(1))")
            l26b = self.set(f, f"26b{col}", pct * min(l24, l26a), f"Applicable percentage {pct:.0%} of the smaller of line 24 or 26a (IRC §1250(a)(1))")
            self.set(f, f"26c{col}", pos(l24 - l26a))
            self.set(f, f"26e{col}", Z, "No additional depreciation of 1970-1975 (line 26d)")
            self.set(f, f"26f{col}", Z, "Section 291 amount: corporations only")
            recapture = self.set(f, f"26g{col}", l26b + self.g(f, f"26e{col}") + self.g(f, f"26f{col}"))
            line_3 = min(l22, l24) - recapture
            self._f4797.unrecaptured_1250 += line_3
            self._f4797.unrecaptured_rows.append({"description": item["description"], "1": str(min(l22, l24)), "2": str(recapture), "3": str(line_3)})
        item["ordinary"] = recapture
        item["section_1231"] = l24 - recapture
        self.set(f, "30", self.g(f, "30") + l24, "Total gains for all properties, line 24")
        self.set(f, "31", self.g(f, "31") + recapture, "Recapture, lines 25b and 26g; to line 13")
        self.set(f, "32", self.g(f, "30") - self.g(f, "31"), "To line 6 (other than casualty or theft)")

    def _section_1231_lookback(self, line_7: Decimal) -> Decimal:
        """Form 4797 line 8: the net section 1231 losses of the 5 preceding years not yet applied against a net section 1231
        gain (IRC §1231(c)(2); us_fed.individual.section_1231_lookback_years), from prior_year.nonrecaptured_1231_losses. The
        gain on line 7 recaptures them earliest year first (Pub. 544); what remains, and this year's net loss, carry to the
        next year as `nonrecaptured_1231_loss_<year>` while the year stays inside the window."""
        f = "f4797"
        py = self.r.prior_year
        window = int(self.p("us_fed.individual.section_1231_lookback_years"))
        if py is None and line_7 > 0:
            self.s.diag("error", "form_4797_nonrecaptured_losses_unknown",
                        "A net section 1231 gain is ordinary income to the extent of the nonrecaptured net section 1231 losses of the 5 "
                        "preceding years (IRC §1231(c)): state prior_year.nonrecaptured_1231_losses (an empty prior-year group states none).",
                        f, "8")
        counted: list[tuple[int, Decimal]] = []
        for e in py.nonrecaptured_1231_losses if py is not None else []:
            if e.tax_year >= self.y:
                self.s.diag("error", "form_4797_nonrecaptured_loss_year_invalid",
                            f"prior_year.nonrecaptured_1231_losses: {e.tax_year} is not a preceding year of {self.y}.", f, "8")
            elif e.nonrecaptured_loss < 0:
                self.s.diag("error", "form_4797_nonrecaptured_loss_sign",
                            f"prior_year.nonrecaptured_1231_losses: the {e.tax_year} loss is entered as a positive amount (got {whole(e.nonrecaptured_loss)}).", f, "8")
            elif e.tax_year < self.y - window:
                self.s.diag("info", "form_4797_nonrecaptured_loss_expired",
                            f"The {e.tax_year} net section 1231 loss is outside the {window} preceding years of {self.y} and is not recaptured.", f, "8")
            else:
                counted.append((e.tax_year, e.nonrecaptured_loss))
        counted.sort()
        schedule: list[dict[str, str]] = []
        to_apply = max(line_7, Z)
        for year, loss in counted:
            applied = min(loss, to_apply)
            to_apply -= applied
            remaining = loss - applied
            carried = remaining > 0 and year >= self.y + 1 - window
            schedule.append({"year": str(year), "loss": str(whole(loss)), "recaptured": str(whole(applied)), "remaining": str(whole(remaining)),
                             "carried_to_next_year": str(carried)})
            if carried:
                self._carryforwards[f"nonrecaptured_1231_loss_{year}"] = whole(remaining)
        if line_7 < 0:
            self._carryforwards[f"nonrecaptured_1231_loss_{self.y}"] = whole(-line_7)
            schedule.append({"year": str(self.y), "loss": str(whole(-line_7)), "recaptured": "0", "remaining": str(whole(-line_7)),
                             "carried_to_next_year": "True"})
        if schedule:
            self.s.fact(f, "nonrecaptured_losses", schedule)
        return sum((loss for _, loss in counted), Z)

    def _form_4797_part_iv(self) -> bool:
        """Part IV, lines 33-35: the recapture of the §179 deduction (column (a)) or of the §280F(b)(2) excess depreciation
        (column (b)) when business use drops to 50% or less, reported as other income on the Schedule C that took the
        deduction (and so in its self-employment earnings, as the instructions for line 35 note)."""
        f = "f4797"
        rows: list[dict[str, str]] = []
        for n, x in enumerate(self.r.business_use_recaptures, 1):
            where = f"business_use_recaptures[{n}] {x.description}"
            bad = False
            if x.deduction_claimed is None:
                self.s.diag("error", "form_4797_part_iv_deduction_unknown", f"{where}: the line 33 amount is not stated.", f, "33")
                bad = True
            if x.recomputed_depreciation is None:
                self.s.diag("error", "form_4797_part_iv_recomputed_unknown", f"{where}: the recomputed depreciation of line 34 is not stated.", f, "34")
                bad = True
            if x.schedule_c is None or x.rental is not None:
                self.s.diag("error", "form_4797_part_iv_schedule_unsupported",
                            f"{where}: the recapture is other income of the schedule that took the deduction; only a Schedule C business is supported.", f, "35")
                bad = True
            elif not 1 <= x.schedule_c <= len(self.r.businesses):
                self.s.diag("error", "form_4797_activity_unknown", f"{where}: schedule_c {x.schedule_c} does not exist on this return.", f, "35")
                bad = True
            if bad or x.deduction_claimed is None or x.recomputed_depreciation is None or x.schedule_c is None:
                continue
            col = "a" if x.kind == "section_179" else "b"
            l33 = self.set(f, f"33{col}", self.g(f, f"33{col}") + x.deduction_claimed,
                           "Section 179 expense deduction" if col == "a" else "Depreciation allowable in prior years (IRC §280F(b)(2))")
            l34 = self.set(f, f"34{col}", self.g(f, f"34{col}") + x.recomputed_depreciation, "Recomputed depreciation")
            self.set(f, f"35{col}", pos(l33 - l34), "Recapture amount: other income of the schedule that took the deduction")
            amount = pos(whole(x.deduction_claimed) - whole(x.recomputed_depreciation))
            st = self._f4797
            st.sch_c_other_income[x.schedule_c] = st.sch_c_other_income.get(x.schedule_c, Z) + amount
            st.depreciable = True
            rows.append({"description": x.description, "kind": x.kind, "33": str(whole(x.deduction_claimed)), "34": str(whole(x.recomputed_depreciation)),
                         "35": str(amount), "reported_on": f"sch_c[{x.schedule_c}] line 6"})
        if rows:
            self.s.fact(f, "part_iv", rows)
        return bool(rows)

    def _non_section_1411(self, kind: str, idx: int) -> bool:
        """Whether the activity is a trade or business that is not a section 1411 trade or business (Reg. §1.1411-5: neither
        passive to the taxpayer nor trading), so that gains and losses on its property are excluded from net investment
        income (Form 8960 line 5b). A rental is never one here (rents are investment income unless a real estate
        professional's trade or business, which Form 8960 line 4b flags)."""
        if kind == "schedule_c":
            return self.r.businesses[idx - 1].materially_participates
        if kind == "k1":
            return not self.r.k1s[idx - 1].passive
        return False

    def _passive_activity(self, kind: str, idx: int) -> bool:
        if kind == "schedule_c":
            return not self.r.businesses[idx - 1].materially_participates
        if kind == "rental":
            return not self.r.rentals[idx - 1].real_estate_professional
        return self.r.k1s[idx - 1].passive

    def _unrecaptured_1250_worksheet(self, collectibles_and_1202: Decimal, sch_d_7: Decimal, carryover_long: Decimal) -> Decimal:
        """Unrecaptured Section 1250 Gain Worksheet (Schedule D instructions, line 19; IRC §1(h)(6)): lines 1-3 per section
        1250 property of Form 4797 Part III that made an entry in Part I (the smaller of line 22 or 24, less line 26g; the
        line 3 amounts totalled), line 5 the Schedule K-1 amounts of partnerships and S corporations, lines 7-9 limited to
        the Form 4797 line 7 gain less its line 8, line 11 the 1099-DIV box 2b and estate or trust K-1 amounts, lines 14-17
        the collectibles and §1202 items, the Schedule D line 7 loss and the long-term carryover that reduce it, line 18 to
        Schedule D line 19. Returns 0 (and writes nothing) when no amount feeds it."""
        r, st = self.r, self._f4797
        k1_5 = sum((k.unrecaptured_1250_gain for k in r.k1s if k.entity_type != "estate_trust"), Z)
        k1_11 = sum((k.unrecaptured_1250_gain for k in r.k1s if k.entity_type == "estate_trust"), Z)
        div_11 = sum((d.unrecaptured_1250_gain for d in r.dividends), Z)
        if not (st.unrecaptured_1250 or k1_5 or k1_11 or div_11):
            return Z
        f = "ws_unrecaptured_1250"
        l9 = Z
        if st.line_7 > 0:
            if st.unrecaptured_rows:
                self.s.fact(f, "properties", st.unrecaptured_rows)
            l3 = self.set(f, "3", st.unrecaptured_1250, "Form 4797 Part III section 1250 properties: smaller of line 22 or 24, less line 26g (total)")
            l4 = self.set(f, "4", Z, "Installment sales (Form 6252): none")
            l5 = self.set(f, "5", k1_5, "Schedule K-1 unrecaptured section 1250 gain (partnerships, S corporations)")
            l6 = self.set(f, "6", l3 + l4 + l5)
            l7 = self.set(f, "7", min(l6, st.line_7), "Smaller of line 6 or the gain on Form 4797 line 7")
            l8 = self.set(f, "8", st.line_8, "Form 4797 line 8")
            l9 = self.set(f, "9", pos(l7 - l8))
        l10 = self.set(f, "10", Z, "Sale of a partnership interest: none")
        l11 = self.set(f, "11", k1_11 + div_11, "Form 1099-DIV box 2b and estate or trust Schedule K-1 amounts")
        l12 = self.set(f, "12", Z, "Section 1250 property not entered in Part I of Form 4797: none")
        l13 = self.set(f, "13", l9 + l10 + l11 + l12)
        l14 = self.set(f, "14", collectibles_and_1202, "28% Rate Gain Worksheet lines 1-4 (collectibles and section 1202 gain or loss)")
        l15 = self.set(f, "15", min(sch_d_7, Z), "Schedule D line 7 loss")
        l16 = self.set(f, "16", -carryover_long, "Long-term capital loss carryover (Schedule D line 14)")
        combined = l14 + l15 + l16
        l17 = self.set(f, "17", -combined if combined < 0 else Z, "Lines 14-16 combined, a loss as a positive amount")
        return self.set(f, "18", pos(l13 - l17), "Unrecaptured section 1250 gain, to Schedule D line 19")

    def _schedule_c(self) -> None:
        meals_pct = self.dec("us_fed.business.meals_deductible_pct")
        ho = self.p("us_fed.business.home_office_simplified")
        for i, b in enumerate(self.r.businesses, 1):
            f = f"sch_c[{i}]"
            self.s.fact(f, "business", {"name": b.name, "ein": b.ein, "code": b.principal_business_code,
                                         "owner": b.owner, "method": b.accounting_method,
                                         "materially_participates": b.materially_participates})
            self.set(f, "1", b.gross_receipts)
            self.set(f, "2", b.returns_allowances)
            self.set(f, "3", self.g(f, "1") - self.g(f, "2"))
            self.set(f, "4", b.cost_of_goods_sold)
            self.set(f, "5", self.g(f, "3") - self.g(f, "4"))
            recapture = self._f4797.sch_c_other_income.get(i, Z)
            self.set(f, "6", b.other_income + recapture, "Other income, incl. Form 4797 line 35 recapture" if recapture else "")
            self.set(f, "7", self.g(f, "5") + self.g(f, "6"), "Gross income")
            total = Z
            for key, amount in b.expenses.items():
                line = SCHEDULE_C_LINES.get(key)
                if line is None:
                    self.s.diag("error", "sch_c_unknown_expense", f"Unknown Schedule C expense category {key!r}.", f)
                    continue
                total += self.set(f, line, self.g(f, line) + amount)
            if b.meals:
                total += self.set(f, "24b", b.meals * meals_pct, f"Business meals at {meals_pct * 100}% (IRC §274(n))")
            self.set(f, "28", total, "Total expenses")
            self.set(f, "29", self.g(f, "7") - total, "Tentative profit or (loss)")
            if b.home_office_sqft:
                sqft = min(b.home_office_sqft, int(ho["max_sqft"]))
                allowed = min(D(ho["rate_per_sqft"]) * sqft, pos(self.g(f, "29")))
                self.set(f, "30", allowed, "Home office, simplified method (Rev. Proc. 2013-13)")
            net = self.set(f, "31", self.g(f, "29") - self.g(f, "30"), "Net profit or (loss)")
            if net < 0:
                self.s.diag("warning", "sch_c_loss",
                            f"{b.name}: net loss. Confirm at-risk (Form 6198) and excess business loss (Form 461) limits.", f, "31")
            self.biz_net.append((b, net))
        self.set("sch_1", "3", sum((n for _, n in self.biz_net), Z), "Schedule C, line 31")

    def _schedule_se(self) -> None:
        se = self.p("us_fed.individual.self_employment_tax")
        wage_base = self.dec("us_fed.payroll.social_security_wage_base")
        total_tax = total_half = Z
        for owner in ("taxpayer", "spouse"):
            profit = sum((n for b, n in self.biz_net if b.owner == owner), Z)
            profit += sum((k.self_employment_earnings for k in self.r.k1s if k.owner == owner), Z)
            if not profit and not any(b.owner == owner for b, _ in self.biz_net):
                continue
            f = f"sch_se[{owner}]"
            self.set(f, "2", profit, "Schedule C line 31 and K-1 box 14 code A")
            self.set(f, "3", profit)
            l4a = self.set(f, "4a", profit * D(se["net_earnings_factor"]) if profit > 0 else profit)
            l4c = self.set(f, "4c", l4a)
            if l4c < D(se["minimum_net_earnings"]):
                self.se[owner] = {"net_earnings": pos(l4c), "tax": Z, "half": Z, "profit": profit}
                continue
            l6 = self.set(f, "6", l4c)
            self.set(f, "7", wage_base, "Social security wage base")
            l8a = self.set(f, "8a", sum((w.ss_wages + w.ss_tips for w in self.r.w2s if w.owner == owner), Z))
            self.set(f, "8d", l8a)
            l9 = self.set(f, "9", pos(wage_base - l8a))
            l10 = self.set(f, "10", min(l6, l9) * D(se["oasdi_rate"]))
            l11 = self.set(f, "11", l6 * D(se["hi_rate"]))
            l12 = self.set(f, "12", l10 + l11, "Self-employment tax")
            l13 = self.set(f, "13", l12 * Decimal("0.5"), "Deduction for one-half of self-employment tax")
            self.se[owner] = {"net_earnings": l6, "tax": l12, "half": l13, "profit": profit}
            total_tax += l12
            total_half += l13
        self._se_tax, self._se_half = total_tax, total_half

    def _holding(self, t: CapitalTransaction) -> str:
        if t.term:
            return t.term
        if t.acquired == "inherited":
            return "long"
        if not isinstance(t.acquired, date):
            self.s.diag("error", "holding_period_unknown",
                        f"{t.description}: acquisition date is '{t.acquired}'; state the holding period.", "f8949")
            return "short"
        a = t.acquired
        try:
            anniversary = a.replace(year=a.year + 1)
        except ValueError:  # 29 February
            anniversary = date(a.year + 1, 3, 1)
        return "long" if t.sold > anniversary else "short"

    def _prior_carryovers(self) -> tuple[Decimal, Decimal]:
        """Capital loss carryovers into this year (positive amounts): the prior-year group when it states them, else
        the legacy top-level inputs. Stated in both places with different amounts is a blocking diagnostic."""
        r, py = self.r, self.r.prior_year
        out: list[Decimal] = []
        for kind, legacy, line in (("short", r.capital_loss_carryover_short, "6"), ("long", r.capital_loss_carryover_long, "14")):
            stated: Decimal | None = getattr(py, f"capital_loss_carryover_{kind}") if py is not None else None
            if stated is not None and legacy and stated != legacy:
                self.s.diag("error", "capital_loss_carryover_conflict",
                            f"The {kind}-term capital loss carryover is stated twice and differs: prior_year says {whole(stated)}, "
                            f"capital_loss_carryover_{kind} says {whole(legacy)}. Remove the entry that is wrong.", "sch_d", line)
            amount = stated if stated is not None else legacy
            if amount < 0:
                self.s.diag("error", "capital_loss_carryover_sign",
                            f"A {kind}-term capital loss carryover is entered as a positive amount (got {whole(amount)}).", "sch_d", line)
                amount = Z
            out.append(amount)
        return out[0], out[1]

    def _schedule_d(self) -> None:
        r = self.r
        cf_short, cf_long = self._prior_carryovers()
        boxes: dict[str, list[CapitalTransaction]] = {}
        collectibles = Z
        for t in r.capital_transactions:
            term = self._holding(t)
            if term == "short":
                key = ("1a" if t.basis_reported_to_irs and not t.adjustment else "A") if t.form_1099_received and t.basis_reported_to_irs \
                    else ("B" if t.form_1099_received else "C")
            else:
                key = ("8a" if t.basis_reported_to_irs and not t.adjustment else "D") if t.form_1099_received and t.basis_reported_to_irs \
                    else ("E" if t.form_1099_received else "F")
            boxes.setdefault(key, []).append(t)
            if term == "long" and t.collectible:
                collectibles += t.proceeds - t.cost_basis + t.adjustment
        sched_line = {"1a": "1a", "A": "1b", "B": "2", "C": "3", "8a": "8a", "D": "8b", "E": "9", "F": "10"}
        for key, rows in boxes.items():
            line = sched_line[key]
            proceeds = sum((t.proceeds for t in rows), Z)
            basis = sum((t.cost_basis for t in rows), Z)
            adj = sum((t.adjustment for t in rows), Z)
            self.set("sch_d", f"{line}d", proceeds)
            self.set("sch_d", f"{line}e", basis)
            self.set("sch_d", f"{line}g", adj)
            self.set("sch_d", f"{line}h", proceeds - basis + adj)
            if key not in ("1a", "8a"):
                part = "I" if key in "ABC" else "II"
                self.s.fact("f8949", f"part_{part}_box_{key}", [
                    {"description": t.description, "acquired": str(t.acquired), "sold": str(t.sold),
                     "proceeds": str(whole(t.proceeds)), "basis": str(whole(t.cost_basis)), "code": t.adjustment_codes,
                     "adjustment": str(whole(t.adjustment)), "gain": str(whole(t.proceeds - t.cost_basis + t.adjustment))}
                    for t in rows])
        k1_st = sum((k.net_short_term_gain for k in r.k1s), Z)
        k1_lt = sum((k.net_long_term_gain for k in r.k1s), Z)
        cgd = sum((d.capital_gain_distributions for d in r.dividends), Z)
        f4797_lt = self._f4797.to_schedule_d
        self.set("sch_d", "5", k1_st)
        self.set("sch_d", "6", -cf_short, "Short-term capital loss carryover (prior-year Capital Loss Carryover Worksheet, line 8)")
        st = sum((self.g("sch_d", f"{line}h") for line in ("1a", "1b", "2", "3")), Z) + self.g("sch_d", "5") + self.g("sch_d", "6")
        self.set("sch_d", "7", st, "Net short-term capital gain or (loss)")
        self.set("sch_d", "11", f4797_lt, "Gain from Form 4797, Part I (line 7, or line 9 after the IRC §1231(c) recapture)")
        self.set("sch_d", "12", k1_lt)
        self.set("sch_d", "13", cgd, "Capital gain distributions")
        self.set("sch_d", "14", -cf_long, "Long-term capital loss carryover (prior-year Capital Loss Carryover Worksheet, line 13)")
        lt = sum((self.g("sch_d", f"{line}h") for line in ("8a", "8b", "9", "10")), Z) + sum(
            (self.g("sch_d", x) for x in ("11", "12", "13", "14")), Z)
        self.set("sch_d", "15", lt, "Net long-term capital gain or (loss)")
        l16 = self.set("sch_d", "16", st + lt)
        self._sch_d_18 = self._sch_d_19 = Z
        if l16 > 0 and lt > 0:
            collectibles_and_1202 = collectibles + sum((d.collectibles_gain for d in r.dividends), Z)
            rate28 = pos(collectibles_and_1202)
            unrecap = self._unrecaptured_1250_worksheet(collectibles_and_1202, st, cf_long)
            self._sch_d_18 = self.set("sch_d", "18", rate28, "28% rate gain")
            self._sch_d_19 = self.set("sch_d", "19", unrecap, "Unrecaptured section 1250 gain (Unrecaptured Section 1250 Gain Worksheet, line 18)")
            seven_a = l16
        elif l16 < 0:
            lim = self.p("us_fed.individual.capital_loss_limit")
            cap = D(lim["mfs"] if self.mfs else lim["other"])
            seven_a = self.set("sch_d", "21", max(l16, -cap), "Capital loss deduction limited (IRC §1211(b))")
        else:
            seven_a = l16
        only_distributions = not r.capital_transactions and not k1_st and not k1_lt and not cf_short \
            and not cf_long and not self._sch_d_18 and not self._sch_d_19 and not f4797_lt
        self.s.fact("f1040", "schedule_d_not_required", bool(only_distributions and cgd > 0))
        self.set("f1040", "7a", seven_a, "Capital gain or (loss)")
        if only_distributions:
            self.s.forms.pop("sch_d", None)
        self._net_cg_for_rates = pos(min(st + lt, lt)) if not only_distributions else pos(cgd)

    def _schedule_e_and_schedule_1(self) -> None:
        r = self.r
        rental_net: list[tuple[Any, Decimal]] = []
        for i, p in enumerate(r.rentals, 1):
            f = f"sch_e[{i}]"
            income = p.rents + p.royalties
            expenses = sum(p.expenses.values(), Z) + p.depreciation
            self.set(f, "3", p.rents)
            self.set(f, "4", p.royalties)
            self.set(f, "18", p.depreciation)
            self.set(f, "20", expenses)
            net = self.set(f, "21", income - expenses)
            self.s.fact(f, "property", {"address": p.address, "type": p.property_type, "fair_rental_days": p.fair_rental_days,
                                         "personal_use_days": p.personal_use_days})
            if p.personal_use_days > max(14, p.fair_rental_days // 10):
                self.s.diag("warning", "vacation_home",
                            f"{p.address}: personal use exceeds 14 days or 10% of rental days; expenses are limited (IRC §280A).", f)
            rental_net.append((p, net - p.prior_year_unallowed_loss))
        # Adjustments known before the passive loss limit (Schedule 1, lines 11-20 except 20 and 21).
        self._adjustments_before_student_loan()
        nonpassive_rentals = sum((n for p, n in rental_net if p.real_estate_professional), Z)
        passive_rentals = sum((n for p, n in rental_net if not p.real_estate_professional), Z)
        allowed_rental = passive_rentals
        if passive_rentals < 0:
            ra = self.p("us_fed.individual.rental_loss_allowance")
            allowance, start = D(ra["allowance"]), D(ra["phaseout_start"])
            if self.mfs:
                if self.r.mfs_lived_apart_all_year:
                    allowance, start = allowance / 2, start / 2
                else:
                    allowance = Z
            # _adj_pre does not yet hold the IRA deduction (figured after total income is known), as §469(i)(3)(F) requires.
            magi = (self.g("f1040", "1z") + self.g("f1040", "2b") + self.g("f1040", "3b") + self.g("f1040", "4b")
                    + self.g("f1040", "5b") + self.g("f1040", "7a") + self.g("sch_1", "3") + self._f4797.ordinary + nonpassive_rentals
                    + self._k1_nonpassive_income() + self._other_sch1_income() - self._adj_pre)
            allowance = pos(allowance - pos(magi - start) * D(ra["phaseout_rate"]))
            if not all(p.active_participation for p, n in rental_net if not p.real_estate_professional and n < 0):
                allowance = Z
            allowed_rental = -min(-passive_rentals, allowance)
            if allowed_rental != passive_rentals:
                self.s.diag("warning", "passive_loss_limited",
                            f"Rental losses limited to {whole(-allowed_rental)} under IRC §469(i); "
                            f"{whole(allowed_rental - passive_rentals)} carries forward (Form 8582). "
                            "MAGI here excludes the IRA deduction and taxable Social Security.", "sch_e")
        self.set("sch_e", "26", allowed_rental + nonpassive_rentals, "Total rental real estate and royalty income or (loss)")
        k1_total = Z
        for i, k in enumerate(r.k1s, 1):
            amt = k.ordinary_income + k.net_rental_income + k.guaranteed_payments - k.section_179
            if k.passive and amt < 0:
                self.s.diag("warning", "passive_k1_loss",
                            f"{k.entity_name}: passive loss of {whole(-amt)} needs Form 8582; it is suspended here.", "sch_e")
                amt = Z
            self.s.fact("sch_e", f"k1_{i}", {"name": k.entity_name, "ein": k.entity_ein, "type": k.entity_type,
                                              "passive": k.passive, "amount": str(whole(amt))})
            k1_total += amt
        self.set("sch_e", "32", k1_total, "Total partnership and S corporation income or (loss)")
        self.set("sch_e", "41", self.g("sch_e", "26") + self.g("sch_e", "32"), "Total income or (loss)")
        self.set("sch_1", "1", r.state_refund_taxable, "Taxable state and local refunds (tax benefit rule)")
        self.set("sch_1", "2a", r.alimony_received)
        self.set("sch_1", "4", self._f4797.ordinary, "Other gains or (losses), Form 4797 line 18b")
        self.set("sch_1", "5", self.g("sch_e", "41"), "Schedule E, line 41")
        self.set("sch_1", "7", sum((u.amount for u in r.unemployment), Z), "Unemployment compensation (1099-G box 1)")
        other = self.set("sch_1", "8f", sum((h["16"] + h["20"] for h in self._hsa.values()), Z), "Form 8889, lines 16 and 20")
        for key, amount in r.other_income.items():
            line = key if key.startswith("8") and len(key) == 2 else "8z"
            sign = -1 if line in ("8a", "8d", "8s") else 1
            other += self.set("sch_1", line, self.g("sch_1", line) + sign * abs(amount))
        self.set("sch_1", "9", other, "Total other income")
        self.set("sch_1", "10", sum((self.g("sch_1", x) for x in ("1", "2a", "3", "4", "5", "6", "7", "9")), Z),
                 "Additional income")
        self.set("f1040", "8", self.g("sch_1", "10"))
        self._ira_deduction()

    def _k1_nonpassive_income(self) -> Decimal:
        return sum((k.ordinary_income + k.net_rental_income + k.guaranteed_payments for k in self.r.k1s if not k.passive), Z)

    def _other_sch1_income(self) -> Decimal:
        return self.r.state_refund_taxable + self.r.alimony_received + sum((u.amount for u in self.r.unemployment), Z) + sum(
            (v if not k in ("8a", "8d", "8s") else -abs(v) for k, v in self.r.other_income.items()), Z)

    def _adjustments_before_student_loan(self) -> None:
        a = self.r.adjustments
        edu_limit = self.dec("us_fed.individual.educator_expense_limit")
        educator = min(a.educator_expenses_taxpayer, edu_limit) + (min(a.educator_expenses_spouse, edu_limit) if self.joint else Z)
        if a.educator_expenses_taxpayer > edu_limit or (self.joint and a.educator_expenses_spouse > edu_limit):
            self.s.diag("info", "educator_limit", f"Educator expenses limited to {edu_limit} per eligible educator (IRC §62(a)(2)(D)).")
        self.set("sch_1", "11", educator)
        self.set("sch_1", "13", sum((h["13"] for h in self._hsa.values()), Z), "Form 8889, line 13")
        self.set("sch_1", "15", self._se_half, "Schedule SE, line 13")
        self.set("sch_1", "16", a.self_employed_retirement)
        se_profit = pos(sum((v["profit"] for v in self.se.values()), Z) - self._se_half - a.self_employed_retirement)
        if a.self_employed_health_insurance > se_profit:
            self.s.diag("warning", "se_health_limited",
                        f"Self-employed health insurance limited to {whole(se_profit)} of net self-employment earnings (IRC §162(l)(2)(A)).",
                        "sch_1", "17")
        self.set("sch_1", "17", min(a.self_employed_health_insurance, se_profit))
        self.set("sch_1", "18", sum((i.early_withdrawal_penalty for i in self.r.interest), Z))
        self.set("sch_1", "19a", a.alimony_paid)
        self.set("sch_1", "20", Z)                      # the IRA Deduction Worksheet runs once total income is known (_ira_deduction)
        self.set("sch_1", "24z", a.other)
        self.set("sch_1", "25", a.other)
        self._adj_pre = sum((self.g("sch_1", x) for x in ("11", "12", "13", "14", "15", "16", "17", "18", "19a", "20", "23", "25")), Z)

    def _compensation(self, owner: Owner) -> Decimal:
        """Taxable compensation for IRA purposes (IRA Deduction Worksheet lines 8-9; Pub. 590-A, "What Is Compensation?"):
        wages, alimony received (attributed to the taxpayer) and net self-employment earnings less the deductible part of
        self-employment tax and the self-employed plan deduction (shared by positive profit)."""
        wages = sum((w.wages for w in self.r.w2s if w.owner == owner), Z)
        alimony = self.r.alimony_received if owner == "taxpayer" else Z
        se = self.se.get(owner)
        se_comp = Z
        if se is not None:
            total_profit = sum((pos(v["profit"]) for v in self.se.values()), Z)
            share = pos(se["profit"]) / total_profit if total_profit > 0 else Z
            se_comp = pos(se["profit"] - se["half"] - self.r.adjustments.self_employed_retirement * share)
        return wages + alimony + se_comp

    def _ira_deduction(self) -> None:
        """IRA Deduction Worksheet (Schedule 1 line 20; IRC §219(b), (g)) per person, the Maximum Roth IRA Contribution
        Worksheet (Form 8606 instructions; §408A(c)(3)), then Form 8606 lines 1-3 and 14 and the basis carried forward.
        Modified AGI is total income less the adjustments other than this deduction and student loan interest; for a
        Social Security recipient the taxable benefits in it are figured without the IRA deduction (Pub. 590-A Appendix B,
        Worksheet 1), and the return's taxable benefits are then refigured with it (Worksheet 3, in _social_security)."""
        r = self.r
        active = {o: st for o, st in self._ira.items() if st.contributions or st.roth_contributions or st.required or st.basis_prior is not None
                  or st.roth_basis_next is not None or st.conversion_basis_next is not None}
        if not active:
            return
        f = "ws_ira_deduction"
        lim = self.ctx.try_param("us_fed.individual.ira_contribution_limit", self.on)
        ph = self.ctx.try_param("us_fed.individual.ira_deduction_phaseout", self.on)
        rph = self.ctx.try_param("us_fed.individual.roth_ira_phaseout", self.on)
        if any(st.contributions or st.roth_contributions for st in active.values()) and (lim is None or ph is None or rph is None):
            self.s.diag("error", "ira_rules_missing",
                        f"No published IRA limits cover {self.on.isoformat()} (us_fed.individual.ira_contribution_limit, "
                        "ira_deduction_phaseout, roth_ira_phaseout): the IRA deduction is not figured.", "sch_1", "20")
            return
        income_wo_ss = sum((self.g("f1040", x) for x in ("1z", "2b", "3b", "4b", "5b", "7a", "8")), Z)
        benefits = sum((s.net_benefits for s in r.social_security), Z)
        taxable_ss, ss_lines = self._ss_worksheet(benefits, income_wo_ss, self.g("f1040", "2a"), self._adj_pre)
        magi = income_wo_ss + taxable_ss - self._adj_pre
        self.set(f, "3", income_wo_ss + taxable_ss, "Form 1040 line 9, with Social Security benefits taxable before the IRA deduction")
        self.set(f, "4", self._adj_pre, "Schedule 1 lines 11 through 19a, 23 and 25")
        self.set(f, "5", magi, "Modified AGI for the IRA deduction")
        if benefits > 0:
            self.s.fact(f, "appendix_b_worksheet_1", {"1": str(whole(income_wo_ss - self._adj_pre)), "2": str(whole(benefits)),
                                                      "17": str(whole(taxable_ss)), "19": str(whole(magi)), "lines": ss_lines})
        lived_apart = self.mfs and r.mfs_lived_apart_all_year
        single_rules = self.fs in ("single", "hoh") or lived_apart
        conversions_taxable = sum((st.taxable_conversion for st in active.values()), Z)
        total = Z
        col = {"taxpayer": "a", "spouse": "b"}
        for owner, st in active.items():
            c = col[owner]
            p = self.person(owner)
            hand = next((x for x in r.ira_facts if x.owner == owner), None)
            if st.contributions == 0 and st.roth_contributions == 0:
                self._finish_8606(owner, st)
                continue
            assert lim is not None and ph is not None and rph is not None
            age50 = self.age_at_year_end(p, 50)
            limit = D(lim["limit"]) + (D(lim["catch_up"]) if age50 else Z)
            spouse_covered = self._spouse_covered(owner)
            other_hand = next((x for x in r.ira_facts if x.owner != owner), None)
            if (self.mfs and not lived_apart and not st.covered and st.contributions > 0
                    and (other_hand is None or other_hand.covered_by_employer_plan is None)):
                self.s.diag("error", "ira_deduction_spouse_coverage_unknown",
                            f"IRA Deduction Worksheet for the {owner}: married filing separately and living with the spouse, whether the "
                            "spouse is covered by an employer plan decides the $0-$10,000 phase-out (IRC §219(g)(1), (3)(B)(iii)); state "
                            "it in ira_facts for the spouse (covered_by_employer_plan).", f, "1b")
            self.s.fact(f, f"1{c}", st.covered)
            rng: list[Any] | None
            if single_rules:
                rng = ph["single"] if st.covered else None
            elif self.fs in ("mfj", "qss"):
                rng = ph["mfj_covered"] if st.covered else (ph["mfj_spouse_covered"] if spouse_covered else None)
            else:
                rng = ph["mfs"] if (st.covered or spouse_covered) else None
            if rng is None:
                l7 = limit
            else:
                start, end = D(rng[0]), D(rng[1])
                self.set(f, f"2{c}", end)
                if magi >= end:
                    l7 = Z
                else:
                    l6 = self.set(f, f"6{c}", end - magi)
                    width = end - start
                    l7 = limit if l6 >= width else max(Decimal(200), round_up_10(l6 * limit / width))
            self.set(f, f"7{c}", l7)
            comp = self._compensation(owner)
            if self.joint:
                other: Owner = "spouse" if owner == "taxpayer" else "taxpayer"
                other_comp = self._compensation(other)
                if comp < other_comp:                        # Pub. 590-A Worksheet 1-2 line 5 (Kay Bailey Hutchison spousal IRA)
                    other_st = self._ira.get(other)
                    comp += pos(other_comp - ((other_st.contributions + other_st.roth_contributions) if other_st else Z))
            self.set(f, f"10{c}", comp, "Taxable compensation")
            l11 = self.set(f, f"11{c}", st.contributions)
            if st.contributions + st.roth_contributions > limit:
                self.s.diag("error", "form_5329_excess_ira_contributions",
                            f"IRA contributions of the {owner} ({whole(st.contributions + st.roth_contributions)} traditional and Roth) exceed "
                            f"the {self.y} limit of {whole(limit)}; the excess owes the 6% tax of Form 5329 (not supported) unless withdrawn "
                            "with its earnings by the due date.", f, f"11{c}")
            elif st.contributions + st.roth_contributions > comp:
                self.s.diag("error", "form_5329_excess_ira_contributions",
                            f"IRA contributions of the {owner} ({whole(st.contributions + st.roth_contributions)}) exceed taxable compensation "
                            f"of {whole(comp)}; the excess owes the 6% tax of Form 5329 (not supported) unless withdrawn by the due date.",
                            f, f"11{c}")
            l12 = min(l7, comp, l11)
            if hand is not None and hand.nondeductible_election:
                l12 = max(Z, min(l12, l11 - hand.nondeductible_election))
            l12 = self.set(f, f"12{c}", l12, "IRA deduction")
            st.deduction, st.nondeductible = l12, pos(l11 - l12)
            total += l12
            if st.roth_contributions > 0:
                self._roth_contribution_limit(owner, st, limit, comp, magi - conversions_taxable, rph, single_rules)
            self._finish_8606(owner, st)
        if total == 0 and not any(st.contributions for st in active.values()):
            self.s.forms.pop(f, None)
        self.set("sch_1", "20", total, "IRA Deduction Worksheet, line 12")
        self._adj_pre += total

    def _roth_contribution_limit(self, owner: Owner, st: _IRA, limit: Decimal, comp: Decimal, magi_roth: Decimal,
                                 rph: dict[str, Any], single_rules: bool) -> None:
        """Maximum Roth IRA Contribution Worksheet (Form 8606 instructions; IRC §408A(c)(2), (3)): contributions over the
        maximum are excess contributions (Form 5329, 6%), a blocking diagnostic."""
        f = "ws_roth_contribution"
        c = {"taxpayer": "a", "spouse": "b"}[owner]
        l1 = self.set(f, f"1{c}", limit if self.joint else min(limit, comp))
        l2 = self.set(f, f"2{c}", st.contributions)
        l3 = self.set(f, f"3{c}", pos(l1 - l2))
        rng = rph["mfj"] if self.fs in ("mfj", "qss") else rph["single"] if single_rules else rph["mfs"]
        start, end = D(rng[0]), D(rng[1])
        l4 = self.set(f, f"4{c}", end)
        l5 = self.set(f, f"5{c}", magi_roth, "Modified AGI for Roth IRA purposes")
        l6 = self.set(f, f"6{c}", l4 - l5)
        if l6 <= 0:
            allowed = Z
        else:
            l7 = self.set(f, f"7{c}", end - start)
            if l6 >= l7:
                allowed = l3
            else:
                l8 = (l6 / l7).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                self.s.fact(f, f"8{c}", str(l8))
                l9 = self.set(f, f"9{c}", max(Decimal(200), round_up_10(l1 * l8)))
                allowed = min(l3, l9)
        l10 = self.set(f, f"10{c}", allowed, "Maximum Roth IRA contribution")
        if st.roth_contributions > l10:
            self.s.diag("error", "form_5329_excess_roth_contributions",
                        f"Roth IRA contributions of the {owner} ({whole(st.roth_contributions)}, Form 5498 box 10) exceed the maximum of "
                        f"{whole(l10)} for modified AGI {whole(magi_roth)}; the excess owes the 6% tax of Form 5329 (not supported) unless "
                        "withdrawn with its earnings by the due date, or recharacterized.", f, f"10{c}")

    def _finish_8606(self, owner: Owner, st: _IRA) -> None:
        """Form 8606 lines 1-3 and 14 once the deduction is known, the form's lines on the sheet, and the basis carried to
        next year (line 14; the Roth bases of Part III)."""
        f = f"f8606[{owner}]"
        if st.nondeductible > 0:
            st.required = True
        if st.nondeductible > 0 and st.basis_prior is None:
            self.s.diag("error", "form_8606_basis_unknown",
                        f"Form 8606 line 2 for the {owner}: nondeductible contributions of {whole(st.nondeductible)} add to a basis that is not "
                        "stated (prior_year.traditional_ira_basis; enter 0 if no Form 8606 was filed before).", f, "2")
        if st.required and st.basis_prior is not None:
            if st.worksheet_1_1 or not st.part_i:
                l1 = st.nondeductible
                l3 = l1 + st.basis_prior
                st.lines.update({"1": l1, "2": st.basis_prior, "3": l3, "14": l3 - st.lines.get("13", Z)})
        if st.required:
            for k, v in st.lines.items():
                self.set(f, k, v, "Form 8606")
            for k, v in st.facts.items():
                self.s.fact(f, k, v)
        if st.basis_prior is not None:
            basis_next = st.lines["14"] if "14" in st.lines else st.basis_prior + st.nondeductible
            self._carryforwards[carryforward_key("traditional_ira_basis", owner)] = whole(basis_next)
        if st.roth_basis_next is not None:
            self._carryforwards[carryforward_key("roth_ira_basis", owner)] = whole(st.roth_basis_next)
        if st.conversion_basis_next is not None:
            self._carryforwards[carryforward_key("roth_conversion_basis", owner)] = whole(st.conversion_basis_next)

    def _ss_worksheet(self, benefits: Decimal, other_income: Decimal, exempt_interest: Decimal,
                      adjustments: Decimal) -> tuple[Decimal, dict[str, str]]:
        """Social Security Benefits Worksheet (Form 1040 instructions, lines 1-18) for the given income and adjustments:
        the taxable benefits and the worksheet lines."""
        if benefits <= 0:
            return Z, {}
        ss = self.p("us_fed.individual.social_security_taxability")
        w: dict[str, Decimal] = {}
        w["1"] = benefits
        w["2"] = benefits * Decimal("0.5")
        w["3"] = other_income
        w["4"] = exempt_interest
        w["5"] = w["2"] + w["3"] + w["4"]
        w["6"] = adjustments
        taxable = Z
        if w["6"] < w["5"]:
            w["7"] = w["5"] - w["6"]
            if self.mfs and not self.r.mfs_lived_apart_all_year:
                w["16"] = w["7"] * Decimal("0.85")
            else:
                base = D(ss["base_mfj"] if self.joint else ss["base_other"])
                adjusted = D(ss["adjusted_mfj"] if self.joint else ss["adjusted_other"])
                w["8"] = base
                if w["8"] < w["7"]:
                    w["9"] = w["7"] - w["8"]
                    w["10"] = adjusted - base
                    w["11"] = pos(w["9"] - w["10"])
                    w["12"] = min(w["9"], w["10"])
                    w["13"] = w["12"] / 2
                    w["14"] = min(w["2"], w["13"])
                    w["15"] = w["11"] * Decimal("0.85")
                    w["16"] = w["14"] + w["15"]
            if "16" in w:
                w["17"] = w["1"] * Decimal("0.85")
                taxable = min(w["16"], w["17"])
        return taxable, {k: str(whole(v)) for k, v in w.items()}

    def _social_security(self) -> None:
        r = self.r
        benefits = sum((s.net_benefits for s in r.social_security), Z)
        self.set("f1040", "6a", benefits, "SSA-1099 / RRB-1099 box 5")
        if benefits <= 0:
            self.set("f1040", "6b", Z)
            return
        other = sum((self.g("f1040", x) for x in ("1z", "2b", "3b", "4b", "5b", "7a", "8")), Z)
        taxable, lines = self._ss_worksheet(benefits, other, self.g("f1040", "2a"), self._adj_pre)
        self.s.fact("ws_social_security", "lines", lines)
        self.set("f1040", "6b", taxable, "Social Security Benefits Worksheet")
        if self.mfs and r.mfs_lived_apart_all_year:
            self.s.fact("f1040", "6d", True)

    def _agi(self) -> None:
        total = sum((self.g("f1040", x) for x in ("1z", "2b", "3b", "4b", "5b", "6b", "7a", "8")), Z)
        self.set("f1040", "9", total, "Total income")
        # Student loan interest (Schedule 1 line 21): MAGI is AGI figured without this deduction.
        a = self.r.adjustments
        sli = Z
        if a.student_loan_interest_paid > 0:
            if self.mfs or self.r.taxpayer.can_be_claimed_as_dependent:
                self.s.diag("info", "student_loan_not_allowed",
                            "No student loan interest deduction for married filing separately or a dependent (IRC §221(b)(2), (e)).")
            else:
                sl = self.p("us_fed.individual.student_loan_interest")
                start = D(self.joint_or(sl["phaseout_start_mfj"], sl["phaseout_start"]))
                end = D(self.joint_or(sl["phaseout_end_mfj"], sl["phaseout_end"]))
                base = min(a.student_loan_interest_paid, D(sl["max_deduction"]))
                magi = total - self._adj_pre
                if magi > start:
                    ratio = min(Decimal(1), ((magi - start) / (end - start)).quantize(Decimal("0.001")))
                    base = base - base * ratio
                sli = base
        self.set("sch_1", "21", sli, "Student Loan Interest Deduction Worksheet")
        self.set("sch_1", "26", self._adj_pre + self.g("sch_1", "21"), "Adjustments to income")
        self.set("f1040", "10", self.g("sch_1", "26"))
        agi = self.set("f1040", "11a", total - self.g("f1040", "10"), "Adjusted gross income")
        self.set("f1040", "11b", agi)
        self.agi = agi

    # ----------------------------------------------------------------- deductions
    def _standard_deduction(self) -> Decimal:
        r = self.r
        if self.mfs and r.mfs_spouse_itemizes:
            self.s.fact("f1040", "12b", True)
            return Z
        base = D(self.p("us_fed.individual.standard_deduction")[T.status_key(self.fs)])
        if r.taxpayer.can_be_claimed_as_dependent:
            dsd = self.p("us_fed.individual.dependent_standard_deduction")
            earned = self._earned_income_of("taxpayer")
            base = min(base, max(D(dsd["minimum"]), earned + D(dsd["earned_income_addon"])))
            self.s.fact("f1040", "12a_you", True)
        add = self.p("us_fed.individual.additional_standard_deduction")
        unmarried = self.fs in ("single", "hoh")
        per = D(add["unmarried"] if unmarried else add["married"])
        boxes = 0
        if self.age65(r.taxpayer):
            boxes += 1
            self.s.fact("f1040", "12d_you_65", True)
        if r.taxpayer.blind:
            boxes += 1
            self.s.fact("f1040", "12d_you_blind", True)
        if self.joint and r.spouse:
            if self.age65(r.spouse):
                boxes += 1
                self.s.fact("f1040", "12d_spouse_65", True)
            if r.spouse.blind:
                boxes += 1
                self.s.fact("f1040", "12d_spouse_blind", True)
        return base + per * boxes

    def _schedule_a(self) -> Decimal:
        it, agi = self.r.itemized, self.agi
        f = "sch_a"
        self.set(f, "1", it.medical)
        self.set(f, "2", agi)
        self.set(f, "3", agi * self.dec("us_fed.individual.medical_expense_floor"))
        self.set(f, "4", pos(self.g(f, "1") - self.g(f, "3")), "Medical and dental (over 7.5% of AGI)")
        if it.use_sales_tax:
            self.s.fact(f, "5a_sales_tax", True)
        self.set(f, "5a", it.general_sales_tax if it.use_sales_tax else it.state_local_income_tax)
        self.set(f, "5b", it.real_estate_tax)
        self.set(f, "5c", it.personal_property_tax)
        l5d = self.set(f, "5d", self.g(f, "5a") + self.g(f, "5b") + self.g(f, "5c"))
        cap = self.dec("us_fed.individual.salt_deduction_cap")
        ph = self.ctx.try_param("us_fed.individual.salt_phasedown", self.on)
        if ph:
            threshold = D(ph["threshold"])
            if self.mfs:
                threshold = threshold / 2
            cap = max(D(ph["floor"]), cap - D(ph["rate"]) * pos(agi - threshold))
        if self.mfs:
            cap = cap / 2
        self.set(f, "5e", min(l5d, cap), f"SALT limited to {whole(cap)} (IRC §164(b)(6)-(7))")
        self.set(f, "6", it.other_taxes)
        self.set(f, "7", self.g(f, "5e") + self.g(f, "6"))
        self.set(f, "8a", it.mortgage_interest_1098)
        self.set(f, "8b", it.mortgage_interest_other)
        self.set(f, "8c", it.points_not_on_1098)
        mip = it.mortgage_insurance_premiums
        if mip > 0:
            m = self.ctx.try_param("us_fed.individual.mortgage_insurance_premiums", self.on)
            if m is None:
                mip = Z
            else:
                thr, step = (D(m["threshold_mfs"]), D(m["step_mfs"])) if self.mfs else (D(m["threshold"]), D(m["step"]))
                cut = steps(agi - thr, step, round_up=True) * D(m["reduction_pct"])
                mip = pos(mip - mip * min(cut, Decimal(1)))
        self.set(f, "8d", mip, "Mortgage insurance premiums (IRC §163(h)(3)(E))")
        self.set(f, "8e", sum((self.g(f, x) for x in ("8a", "8b", "8c", "8d")), Z))
        if it.mortgage_interest_1098 or it.mortgage_interest_other:
            self.s.diag("info", "mortgage_limit", "Confirm acquisition debt is within $750,000 ($375,000 MFS) or grandfathered "
                                                  "(IRC §163(h)(3)(F)); otherwise use the Pub. 936 worksheet.", f, "8a")
        self.set(f, "9", it.investment_interest)
        if it.investment_interest:
            self.s.diag("warning", "form_4952", "Investment interest is limited to net investment income (Form 4952).", f, "9")
        self.set(f, "10", self.g(f, "8e") + self.g(f, "9"))
        self.set(f, "11", it.charity_cash)
        self.set(f, "12", it.charity_noncash)
        cash_allowed = min(it.charity_cash, agi * Decimal("0.60"))
        noncash_allowed = min(it.charity_noncash, pos(agi * Decimal("0.50") - cash_allowed), agi * Decimal("0.30"))
        allowed = cash_allowed + noncash_allowed
        floor_pct = self.ctx.try_param("us_fed.individual.charitable_floor_pct", self.on)
        floor = agi * D(floor_pct) if floor_pct is not None else Z
        if cash_allowed + noncash_allowed < it.charity_cash + it.charity_noncash:
            self.s.diag("warning", "charity_limited", "Charitable contributions exceed AGI limits (IRC §170(b)); the excess "
                                                      "carries forward five years. Confirm property types and organizations.", f, "13")
        self.set(f, "13", pos(allowed - floor), "Charitable Contribution Limitation Worksheet (0.5% floor, IRC §170(b)(1)(I))")
        self.set(f, "14", it.charity_carryover)
        self.set(f, "15", self.g(f, "13") + self.g(f, "14"))
        self.set(f, "16", it.casualty_loss)
        self.set(f, "17z", it.other)
        return sum((self.g(f, x) for x in ("4", "7", "10", "15", "16", "17z")), Z)

    def _limit_itemized(self, itemized: Decimal, sch_1a: Decimal, qbi: Decimal) -> Decimal:
        """IRC §68 (2026+): reduce itemized deductions by 2/37 of the lesser of the deductions or the
        amount by which taxable income (before this limit, plus itemized deductions) exceeds the 37% bracket."""
        lim = self.ctx.try_param("us_fed.individual.itemized_limitation", self.on)
        if lim is None:
            return Z
        b37 = T.brackets(self.ctx, self.y, self.fs)[-1][0]
        excess = pos(self.agi - sch_1a - qbi - b37)
        if excess <= 0:
            return Z
        reduction = min(itemized, excess) * D(lim["numerator"]) / D(lim["denominator"])
        self.s.diag("info", "itemized_limited",
                    "Itemized deductions reduced under IRC §68 (2/37 rule). Computed from the statute; the 2026 Itemized "
                    "Deductions Worksheet is not yet final.", "sch_a", "18")
        return reduction

    def _schedule_1a(self) -> Decimal:
        r, magi = self.r, self.agi
        f = "sch_1a"
        self.set(f, "1", self.agi)
        self.set(f, "3", magi, "Modified AGI")
        married_separate = self.mfs
        total = Z

        def valid(owner: Owner) -> bool:
            p = self.person(owner)
            return bool(p and p.ssn and p.ssn_valid_for_employment)

        tips_rule = self.ctx.try_param("us_fed.individual.qualified_tips_deduction", self.on)
        def occupation_ok(code: int | None, sstb: bool, amount: Decimal, source: str) -> bool:
            if not amount:
                return False
            if sstb:
                self.s.diag("info", "tips_sstb", f"{source}: tips received in a specified service trade or business do not "
                                                 "qualify (IRC §224(d)(2)(B)).", f, "4")
                return False
            if not code:
                self.s.diag("warning", "tips_occupation_code",
                            f"{source}: {whole(amount)} of tips excluded until the Treasury Tipped Occupation Code is entered "
                            "(IRC §224(d)(1); prop. Reg. §1.224-1(f)).", f, "4")
                return False
            return True

        tips_emp = sum((w.tips() for w in r.w2s if valid(w.owner)
                        and occupation_ok(w.tipped_occupation_code, w.employer_sstb, w.tips(), w.employer_name or "W-2")), Z)
        if r.tips_form_4137 and valid("taxpayer") and occupation_ok(r.tips_form_4137_occupation_code, False, r.tips_form_4137, "Form 4137"):
            tips_emp = max(tips_emp, r.tips_form_4137)
        tips_biz = sum((min(b.qualified_tips, pos(n)) for b, n in self.biz_net if valid(b.owner)
                        and occupation_ok(b.tipped_occupation_code, b.sstb, b.qualified_tips, b.name)), Z)
        if tips_rule and (tips_emp or tips_biz):
            if married_separate:
                self.s.diag("info", "tips_mfs", "No qualified tips deduction when married filing separately (IRC §224(f)).")
            else:
                self.set(f, "5", tips_emp)
                self.set(f, "7", tips_biz)
                self.set(f, "8", tips_emp + tips_biz)
                l9 = self.set(f, "9", min(tips_emp + tips_biz, D(tips_rule["max_deduction"])))
                thr = D(tips_rule["threshold_mfj"] if self.fs == "mfj" else tips_rule["threshold"])
                self.set(f, "10", magi)
                self.set(f, "11", thr)
                l12 = self.set(f, "12", pos(magi - thr))
                l13 = steps(l12, D(tips_rule["step"]), round_up=False)
                self.s.fact(f, "13", str(l13))
                l14 = self.set(f, "14", l13 * D(tips_rule["reduction_per_step"]))
                total += self.set(f, "15", pos(l9 - l14), "Qualified tips deduction (IRC §224)")
        ot_rule = self.ctx.try_param("us_fed.individual.qualified_overtime_deduction", self.on)
        overtime = sum((w.overtime() for w in r.w2s if valid(w.owner)), Z)
        if ot_rule and overtime:
            if married_separate:
                self.s.diag("info", "overtime_mfs", "No qualified overtime deduction when married filing separately (IRC §225(e)).")
            else:
                self.set(f, "17", overtime)
                self.set(f, "20", overtime)
                cap = D(ot_rule["max_deduction_mfj"] if self.fs == "mfj" else ot_rule["max_deduction"])
                l21 = self.set(f, "21", min(overtime, cap))
                thr = D(ot_rule["threshold_mfj"] if self.fs == "mfj" else ot_rule["threshold"])
                self.set(f, "22", magi)
                self.set(f, "23", thr)
                l24 = self.set(f, "24", pos(magi - thr))
                l25 = steps(l24, D(ot_rule["step"]), round_up=False)
                self.s.fact(f, "25", str(l25))
                l26 = self.set(f, "26", l25 * D(ot_rule["reduction_per_step"]))
                total += self.set(f, "27", pos(l21 - l26), "Qualified overtime deduction (IRC §225)")
        car_rule = self.ctx.try_param("us_fed.individual.car_loan_interest_deduction", self.on)
        loans = [c for c in r.car_loans if c.qualifies]
        for c in r.car_loans:
            if not c.vin or len(c.vin) != 17:
                self.s.diag("error", "car_loan_vin", "Vehicle loan interest requires the 17-character VIN (IRC §163(h)(4)(B)(iii)).", f, "28")
        if car_rule and loans:
            self.s.fact(f, "28", [{"vin": c.vin, "interest": str(whole(c.interest_paid))} for c in loans])
            l29 = self.set(f, "29", sum((c.interest_paid for c in loans if c.vin and len(c.vin) == 17), Z))
            l30 = self.set(f, "30", min(l29, D(car_rule["max_deduction"])))
            thr = D(car_rule["threshold_mfj"] if self.fs == "mfj" else car_rule["threshold"])
            self.set(f, "31", magi)
            self.set(f, "32", thr)
            l33 = self.set(f, "33", pos(magi - thr))
            l34 = steps(l33, D(car_rule["step"]), round_up=True)
            self.s.fact(f, "34", str(l34))
            l35 = self.set(f, "35", l34 * D(car_rule["reduction_per_step"]))
            total += self.set(f, "36", pos(l30 - l35), "Qualified passenger vehicle loan interest (IRC §163(h)(4))")
        senior_rule = self.ctx.try_param("us_fed.individual.senior_deduction", self.on)
        tp65, sp65 = self.age65(r.taxpayer), self.joint and self.age65(r.spouse)
        if senior_rule and (tp65 or sp65):
            if married_separate:
                self.s.diag("info", "senior_mfs", "No senior deduction when married filing separately (IRC §151(d)(5)(C)(v)).")
            else:
                amount = D(senior_rule["amount"])
                thr = D(senior_rule["threshold_mfj"] if self.fs == "mfj" else senior_rule["threshold"])
                self.set(f, "37", magi)
                self.set(f, "38", thr)
                l39 = self.set(f, "39", pos(magi - thr))
                l40 = self.set(f, "40", l39 * D(senior_rule["phaseout_rate"]))
                l41 = self.set(f, "41", pos(amount - l40))
                l42a = self.set(f, "42a", l41 if tp65 and valid("taxpayer") else Z)
                l42b = self.set(f, "42b", l41 if sp65 and valid("spouse") else Z)
                total += self.set(f, "43", l42a + l42b, "Enhanced deduction for seniors (IRC §151(d)(5)(C))")
        self.set(f, "44", total)
        if total == 0:
            self.s.forms.pop(f, None)
        return total

    def _qbi(self, ti_before_qbi: Decimal) -> Decimal:
        r = self.r
        items: list[dict[str, Any]] = []
        total_profit = sum((pos(v["profit"]) for v in self.se.values()), Z)
        a = r.adjustments
        for n, (b, net) in enumerate(self.biz_net, 1):
            share = (pos(net) / total_profit) if total_profit > 0 else Z
            # Form 4797 ordinary amounts of the business's property (recapture, short-term gains, a net section 1231 loss, the
            # §1231(c) amount) are QBI; its section 1231 gain treated as capital gain is not (Reg. §1.199A-3(b)(2)(ii)(A)).
            qbi = net - (self._se_half + a.self_employed_health_insurance + a.self_employed_retirement) * share + self._f4797.qbi_ordinary.get(n, Z)
            items.append({"name": b.name, "ein": b.ein, "qbi": qbi, "w2": b.w2_wages, "ubia": b.ubia, "sstb": b.sstb,
                          "active": b.materially_participates})
        for k in r.k1s:
            qbi = k.qbi if k.qbi is not None else k.ordinary_income + k.net_rental_income - k.section_179
            if qbi or k.w2_wages:
                items.append({"name": k.entity_name, "ein": k.entity_ein, "qbi": qbi, "w2": k.w2_wages, "ubia": k.ubia,
                              "sstb": k.sstb, "active": not k.passive})
        reit = sum((d.section_199a_dividends for d in r.dividends), Z)
        if not items and not reit and not r.qbi_loss_carryforward:
            return Z
        thr, end = (D(x) for x in self.p("us_fed.individual.qbi_thresholds")[T.status_key(self.fs)])
        rate = self.dec("us_fed.business.qbi_deduction_rate")
        ncg = self.g("f1040", "3a") + self._net_cg_for_rates
        total_qbi = sum((i["qbi"] for i in items), Z) - r.qbi_loss_carryforward
        if ti_before_qbi <= thr:
            f = "f8995"
            self.s.fact(f, "businesses", [{"name": i["name"], "ein": i["ein"], "qbi": str(whole(i["qbi"]))} for i in items])
            self.set(f, "2", sum((i["qbi"] for i in items), Z))
            self.set(f, "3", -r.qbi_loss_carryforward)
            l4 = self.set(f, "4", self.g(f, "2") + self.g(f, "3"))
            l5 = self.set(f, "5", pos(l4) * rate)
            self.set(f, "6", reit)
            self.set(f, "7", -r.reit_ptp_loss_carryforward)
            l8 = self.set(f, "8", self.g(f, "6") + self.g(f, "7"))
            l9 = self.set(f, "9", pos(l8) * rate)
            l10 = self.set(f, "10", l5 + l9)
            self.set(f, "11", ti_before_qbi)
            self.set(f, "12", ncg)
            l13 = self.set(f, "13", pos(ti_before_qbi - ncg))
            l14 = self.set(f, "14", l13 * rate)
            deduction = self.set(f, "15", min(l10, l14), "Qualified business income deduction")
            self.set(f, "16", min(Z, l4))
            self.set(f, "17", min(Z, l8))
        else:
            f = "f8995a"
            frac = min(Decimal(1), (ti_before_qbi - thr) / (end - thr))
            positives = [i for i in items if i["qbi"] > 0]
            negative = -sum((i["qbi"] for i in items if i["qbi"] < 0), Z) + r.qbi_loss_carryforward
            pos_total = sum((i["qbi"] for i in positives), Z)
            component = Z
            rows = []
            for i in positives:
                qbi, w2, ubia = i["qbi"], i["w2"], i["ubia"]
                if i["sstb"]:
                    if ti_before_qbi >= end:
                        rows.append({"name": i["name"], "sstb": True, "deduction": "0"})
                        continue
                    applicable = 1 - frac
                    qbi, w2, ubia = qbi * applicable, w2 * applicable, ubia * applicable
                qbi -= negative * (i["qbi"] / pos_total) if pos_total > 0 else Z
                qbi = pos(qbi)
                tentative = qbi * rate
                wage_limit = max(w2 * Decimal("0.5"), w2 * Decimal("0.25") + ubia * Decimal("0.025"))
                if ti_before_qbi >= end:
                    allowed = min(tentative, wage_limit)
                elif wage_limit < tentative:
                    allowed = tentative - (tentative - wage_limit) * frac
                else:
                    allowed = tentative
                component += allowed
                rows.append({"name": i["name"], "sstb": i["sstb"], "qbi": str(whole(qbi)), "w2": str(whole(w2)),
                             "ubia": str(whole(ubia)), "deduction": str(whole(allowed))})
            self.s.fact(f, "businesses", rows)
            self.set(f, "27", component, "Total qualified business income component")
            reit_component = pos(reit - r.reit_ptp_loss_carryforward) * rate
            self.set(f, "31", reit_component)
            self.set(f, "32", component + reit_component)
            self.set(f, "33", ti_before_qbi)
            self.set(f, "34", ncg)
            l35 = self.set(f, "35", pos(ti_before_qbi - ncg))
            l36 = self.set(f, "36", l35 * rate)
            deduction = self.set(f, "37", min(component + reit_component, l36))
            self.set(f, "39", deduction, "Qualified business income deduction")
            if total_qbi < 0:
                self.set(f, "40", total_qbi)
        mins = self.ctx.try_param("us_fed.individual.qbi_minimum_deduction", self.on)
        active_qbi = sum((i["qbi"] for i in items if i["active"]), Z)
        if mins and active_qbi >= D(mins["active_qbi_floor"]) and deduction < D(mins["minimum_deduction"]):
            deduction = D(mins["minimum_deduction"])
            self.s.diag("info", "qbi_minimum", "QBI deduction raised to the $400 minimum for active QBI of $1,000 or more "
                                               "(IRC §199A(i)). Confirm placement on the final 2026 Form 8995.", f)
        if total_qbi < 0:
            self.s.diag("info", "qbi_loss_carryforward", f"Net QBI loss of {whole(-total_qbi)} carries forward (IRC §199A(c)(2)).", f)
        return deduction

    def _deductions(self) -> None:
        r = self.r
        standard = self._standard_deduction()
        has_itemized = any(v for k, v in r.itemized.model_dump(exclude={"force_itemize", "use_sales_tax"}).items())
        itemized = self._schedule_a() if has_itemized else Z
        sch_1a = self._schedule_1a()
        itemize = (itemized > standard) or r.itemized.force_itemize or (self.mfs and r.mfs_spouse_itemizes)
        nonitemizer_charity = Z
        rule = self.ctx.try_param("us_fed.individual.charitable_nonitemizer_deduction", self.on)
        if not itemize and rule and r.itemized.charity_cash > 0:
            nonitemizer_charity = min(r.itemized.charity_cash, D(rule["mfj"] if self.fs == "mfj" else rule["other"]))
        chosen = itemized if itemize else standard
        qbi = self._qbi(pos(self.agi - chosen - (Z if itemize else nonitemizer_charity) - sch_1a))
        if itemize:
            reduction = self._limit_itemized(itemized, sch_1a, qbi)
            if reduction:
                itemized -= reduction
                self.set("sch_a", "18", itemized, "Total itemized deductions after the IRC §68 limitation")
                if itemized < standard and not r.itemized.force_itemize and not (self.mfs and r.mfs_spouse_itemizes):
                    itemize = False
                    if rule and r.itemized.charity_cash > 0:
                        nonitemizer_charity = min(r.itemized.charity_cash, D(rule["mfj"] if self.fs == "mfj" else rule["other"]))
            else:
                self.set("sch_a", "18", itemized, "Total itemized deductions")
        if itemize:
            if r.itemized.force_itemize and itemized < standard:
                self.s.fact("sch_a", "19_elect_itemize", True)
            nonitemizer_charity = Z
            self.set("f1040", "12e", itemized, "Itemized deductions (Schedule A)")
        else:
            self.s.forms.pop("sch_a", None)
            self.set("f1040", "12e", standard, "Standard deduction")
        self.itemizing = itemize
        self.set("f1040", "12f", nonitemizer_charity, "Charitable deduction for non-itemizers (IRC §170(p))")
        self.set("f1040", "13a", sch_1a, "Schedule 1-A, line 44")
        self.set("f1040", "13b", qbi, "Qualified business income deduction")
        self.set("f1040", "14", sum((self.g("f1040", x) for x in ("12e", "12f", "13a", "13b")), Z))
        self._ti_unfloored = whole(self.agi - self.g("f1040", "14"))
        self.set("f1040", "15", pos(self._ti_unfloored), "Taxable income")

    def _capital_loss_carryover(self) -> None:
        """Capital Loss Carryover Worksheet (Schedule D instructions, lines 1-13): the part of this year's net capital
        loss that the §1211(b) deduction did not use and that carries to next year (IRC §1212(b)(1)). Short-term losses
        are used first, and taxable income is taken as it would be if line 15 could be negative (§1212(b)(2)), so a
        loss that only deepened a negative taxable income carries over in full."""
        cf = {"capital_loss_carryover_short": Z, "capital_loss_carryover_long": Z}
        l21 = self.g("sch_d", "21")
        if "sch_d" not in self.s.forms or l21 >= 0:          # no net capital loss: nothing carries
            self._carryforwards.update(cf)
            return
        f = "ws_capital_loss_carryover"
        sd7, sd15 = self.g("sch_d", "7"), self.g("sch_d", "15")
        l1 = self.set(f, "1", self._ti_unfloored, "Form 1040 line 15 as it would be if a negative amount could be entered")
        l2 = self.set(f, "2", -l21, "Schedule D line 21 as a positive amount")
        l3 = self.set(f, "3", pos(l1 + l2))
        l4 = self.set(f, "4", min(l2, l3), "The part of the loss deduction that reduced taxable income")
        l5 = self.set(f, "5", -sd7 if sd7 < 0 else Z, "Schedule D line 7 loss as a positive amount")
        if sd7 < 0:
            l6 = self.set(f, "6", pos(sd15), "Schedule D line 15 gain")
            l7 = self.set(f, "7", l4 + l6)
            cf["capital_loss_carryover_short"] = self.set(f, "8", pos(l5 - l7),
                                                          f"Short-term capital loss carryover to {self.y + 1} (Schedule D line 6)")
        if sd15 < 0:
            l9 = self.set(f, "9", -sd15, "Schedule D line 15 loss as a positive amount")
            l10 = self.set(f, "10", pos(sd7), "Schedule D line 7 gain")
            l11 = self.set(f, "11", pos(l4 - l5))
            l12 = self.set(f, "12", l10 + l11)
            cf["capital_loss_carryover_long"] = self.set(f, "13", pos(l9 - l12),
                                                         f"Long-term capital loss carryover to {self.y + 1} (Schedule D line 14)")
        self.s.fact(f, "carries_to", self.y + 1)
        self._carryforwards.update(cf)

    # ----------------------------------------------------------------- tax
    def _tax(self) -> None:
        ti = self.g("f1040", "15")
        qd = self.g("f1040", "3a")
        self._qdcg_lines: dict[str, Decimal] = {}
        self._sdtw_lines: dict[str, Decimal] = {}
        has_sched_d = "sch_d" in self.s.forms
        l15, l16 = (self.g("sch_d", "15"), self.g("sch_d", "16")) if has_sched_d else (self._net_cg_for_rates, self._net_cg_for_rates)
        if ti <= 0:
            tax = Z
            method = "No taxable income"
        elif has_sched_d and l15 > 0 and l16 > 0 and (self._sch_d_18 > 0 or self._sch_d_19 > 0):
            tax, self._sdtw_lines = T.schedule_d_tax(self.ctx, self.y, self.fs, ti, qd, l15, l16, self._sch_d_18, self._sch_d_19)
            method = "Schedule D Tax Worksheet"
        elif qd > 0 or (l15 > 0 and l16 > 0):
            tax, self._qdcg_lines = T.qdcg_tax(self.ctx, self.y, self.fs, ti, qd, pos(min(l15, l16)))
            method = "Qualified Dividends and Capital Gain Tax Worksheet"
        else:
            tax = T.regular_tax(self.ctx, ti, self.y, self.fs)
            method = "Tax Table" if ti < self.dec("us_fed.individual.tax_table_ceiling") else "Tax Computation Worksheet"
        self.set("f1040", "16", tax, method)
        if self._qdcg_lines:
            self.s.fact("ws_qdcg", "lines", {k: str(whole(v)) for k, v in self._qdcg_lines.items()})
        if self._sdtw_lines:
            self.s.fact("ws_sch_d_tax", "lines", {k: str(whole(v)) for k, v in self._sdtw_lines.items()})
        self.set("sch_2", "1a", self.r.excess_aptc_repayment, "Form 8962")
        self.set("sch_2", "1z", self.g("sch_2", "1a"))

    def _amt(self) -> None:
        amt = self.p("us_fed.individual.amt")
        r = self.r
        f = "f6251"
        senior = self.g("sch_1a", "43")
        self.set(f, "1a", self.g("f1040", "14") - senior)
        l1b = self.set(f, "1b", self.agi - self.g(f, "1a"))
        self.set(f, "2a", self.g("sch_a", "7") if self.itemizing else self.g("f1040", "12e"))
        self.set(f, "2b", -self.g("sch_1", "1") if self.itemizing else Z)
        pab = sum((i.private_activity_bond_interest for i in r.interest), Z) + sum(
            (d.private_activity_bond_dividends for d in r.dividends), Z)
        self.set(f, "2g", pab, "Specified private activity bond interest")
        letters = {"investment_interest": "2c", "depletion": "2d", "nol": "2e", "atnold": "2f", "qsbs": "2h", "iso": "2i",
                   "estates_trusts": "2j", "disposition": "2k", "depreciation": "2l", "passive": "2m", "loss_limitations": "2n",
                   "circulation": "2o", "long_term_contracts": "2p", "mining": "2q", "research": "2r", "installment": "2s",
                   "idc": "2t", "other": "3"}
        for key, amount in r.amt_adjustments.items():
            line = letters.get(key)
            if line is None:
                self.s.diag("error", "amt_unknown_adjustment", f"Unknown AMT adjustment {key!r}.", f)
                continue
            self.set(f, line, self.g(f, line) + amount)
        l4 = self.set(f, "4", l1b + sum((self.g(f, x) for x in
                                          ["2a", "2b", "2c", "2d", "2e", "2f", "2g", "2h", "2i", "2j", "2k", "2l", "2m", "2n",
                                           "2o", "2p", "2q", "2r", "2s", "2t", "3"]), Z), "Alternative minimum taxable income")
        key = {"mfj": "mfj", "qss": "mfj", "mfs": "mfs"}.get(self.fs, "single")
        exemption, threshold = D(amt[f"exemption_{key}"]), D(amt[f"phaseout_{key}"])
        if self.mfs:
            zero_at = threshold + exemption / D(amt["phaseout_rate"])
            if l4 > zero_at:
                l4 = self.set(f, "4", l4 + min((l4 - zero_at) * D(amt["phaseout_rate"]), exemption),
                              "AMTI increased for married filing separately (IRC §55(d)(2))")
                self.s.diag("warning", "amt_mfs_increase", "MFS AMTI adjustment applied; verify against the final 2026 Form 6251 instructions.", f, "4")
        l5 = self.set(f, "5", pos(exemption - pos(l4 - threshold) * D(amt["phaseout_rate"])), "Exemption")
        l6 = self.set(f, "6", pos(l4 - l5))
        breakpoint_ = D(amt["rate_breakpoint_mfs"] if self.mfs else amt["rate_breakpoint"])
        low, high = D(amt["low_rate"]), D(amt["high_rate"])

        def flat(x: Decimal) -> Decimal:
            return x * low if x <= breakpoint_ else x * high - breakpoint_ * (high - low)

        if l6 <= 0:
            self.set(f, "7", Z)
            tmt = Z
        elif self._qdcg_lines or self._sdtw_lines:
            tmt = self.set(f, "7", self._amt_part_iii(l6, flat), "Part III, line 40")
        else:
            tmt = self.set(f, "7", flat(l6))
        self.set(f, "8", self._amt_ftc(l4, tmt), "AMT foreign tax credit (AMT Form 1116, simplified limitation election)")
        l9 = self.set(f, "9", pos(tmt - self.g(f, "8")), "Tentative minimum tax")
        l10 = self.set(f, "10", pos(self.g("f1040", "16") + self.g("sch_2", "1z") - self._ftc),
                       "Form 1040 line 16 plus Schedule 2 line 1z, less Schedule 3 line 1")
        l11 = self.set(f, "11", pos(l9 - l10), "Alternative minimum tax")
        if l11 <= 0 and l4 <= exemption:
            self.s.forms.pop(f, None)
        self.set("sch_2", "2", l11, "Form 6251")
        self.set("sch_2", "3", self.g("sch_2", "1z") + l11)
        self.set("f1040", "17", self.g("sch_2", "3"))
        self.set("f1040", "18", self.g("f1040", "16") + self.g("f1040", "17"))

    def _amt_part_iii(self, l6: Decimal, flat) -> Decimal:
        f = "f6251"
        zero_max, fifteen_max = T._cg_breakpoints(self.ctx, self.y, self.fs)
        q, s = self._qdcg_lines, self._sdtw_lines
        l12 = self.set(f, "12", l6)
        l13 = self.set(f, "13", q.get("4", Z) if q else s.get("13", Z))
        l14 = self.set(f, "14", self._sch_d_19 if s else Z)
        l15 = self.set(f, "15", min(l13 + l14, s.get("10", Z)) if s else l13)
        l16 = self.set(f, "16", min(l12, l15))
        l17 = self.set(f, "17", l12 - l16)
        l18 = self.set(f, "18", flat(l17))
        l19 = self.set(f, "19", zero_max)
        l20 = self.set(f, "20", q.get("5", Z) if q else s.get("14", Z))
        l21 = self.set(f, "21", pos(l19 - l20))
        l22 = self.set(f, "22", min(l12, l13))
        l23 = self.set(f, "23", min(l21, l22))
        l24 = self.set(f, "24", l22 - l23)
        l25 = self.set(f, "25", fifteen_max)
        l26 = self.set(f, "26", l21)
        l27 = self.set(f, "27", q.get("5", Z) if q else s.get("21", Z))
        l28 = self.set(f, "28", l26 + l27)
        l29 = self.set(f, "29", pos(l25 - l28))
        l30 = self.set(f, "30", min(l24, l29))
        l31 = self.set(f, "31", l30 * Decimal("0.15"))
        l32 = self.set(f, "32", l23 + l30)
        l33 = self.set(f, "33", l22 - l32)
        l34 = self.set(f, "34", l33 * Decimal("0.20"))
        l35 = self.set(f, "35", l17 + l32 + l33)
        l36 = self.set(f, "36", l12 - l35)
        l37 = self.set(f, "37", l36 * Decimal("0.25"))
        l38 = self.set(f, "38", l18 + l31 + l34 + l37)
        l39 = self.set(f, "39", flat(l12))
        return self.set(f, "40", min(l38, l39))

    # ----------------------------------------------------------------- foreign tax credit
    def _foreign_items(self) -> list[dict[str, Any]]:
        """Every payer item carrying foreign tax or foreign-source income (1099-INT box 6, 1099-DIV box 7, Schedule K-1
        box 21 with its Schedule K-3 items), with the facts Form 1116 needs. `gross` is the item's gross income on the
        return, the most its foreign-source part can be; `qualified` the most of that part that can be qualified
        dividends (used only to test the adjustment exception, an upper bound)."""
        out: list[dict[str, Any]] = []
        for j, i in enumerate(self.r.interest):
            if i.foreign_tax_paid or i.foreign_source_income:
                out.append({"ref": f"interest[{j}]", "name": i.payer or "1099-INT", "tax": i.foreign_tax_paid,
                            "income": i.foreign_source_income, "country": i.foreign_country.strip(), "category": i.category,
                            "accrued": i.accrued, "gross": i.interest + i.us_savings_bond_interest, "qualified": Z, "kind": "interest"})
        for j, d in enumerate(self.r.dividends):
            if d.foreign_tax_paid or d.foreign_source_income:
                out.append({"ref": f"dividends[{j}]", "name": d.payer or "1099-DIV", "tax": d.foreign_tax_paid,
                            "income": d.foreign_source_income, "country": d.foreign_country.strip(), "category": d.category,
                            "accrued": d.accrued, "gross": d.ordinary, "qualified": min(d.foreign_source_income or Z, d.qualified),
                            "kind": "dividends"})
        for j, k in enumerate(self.r.k1s):
            if k.foreign_tax_paid or k.foreign_source_income:
                out.append({"ref": f"k1s[{j}]", "name": k.entity_name, "tax": k.foreign_tax_paid, "income": k.foreign_source_income,
                            "country": k.foreign_country.strip(), "category": k.category, "accrued": k.accrued,
                            "gross": k.interest + k.ordinary_dividends + pos(k.net_short_term_gain) + pos(k.net_long_term_gain),
                            "qualified": min(k.foreign_source_income or Z, k.qualified_dividends), "kind": "other"})
        return out

    @staticmethod
    def _ftc_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
        """Form 1116 lines 3f and 19: a ratio rounded to four decimal places, never more than 1, zero when either side is."""
        if numerator <= 0 or denominator <= 0:
            return Z
        return min(Decimal(1), (numerator / denominator).quantize(FTC_RATIO_PLACES, rounding=ROUND_HALF_UP))

    def _form_1116(self) -> None:
        """Form 1116, foreign tax credit (IRC §901; the §904(a) limitation per separate category, §904(d)).

        Lines follow Form 1116 (2025) and its instructions, read on 2026-10-09 (the 2026 form is not posted; nothing on
        it is indexed). Passive category only. Part I: gross foreign-source income (line 1a) less a ratable share
        (line 3f, gross foreign-source income over gross income from all sources, four decimal places) of the deductions
        not definitely related to any income: the standard deduction, or Schedule A medical, general sales, real estate
        and personal property taxes (line 3a), and Schedule 1 Part II adjustments other than interest (line 3b);
        interest expense (lines 4a, 4b) is allocated to U.S. income when gross foreign-source income is within the
        instructions' $5,000 rule and blocks otherwise. Part III: this year's taxes plus carryovers (line 14) against the
        tax on line 20 (Form 1040 line 16 plus Schedule 2 line 1z) times net foreign-source taxable income over taxable
        income before the senior deduction (lines 17-19, 21); Part IV (completed even for one form) carries line 24 to
        Schedule 3 line 1. Unused taxes go back one year, then forward ten (§904(c), Reg. §1.904-2;
        us_fed.individual.ftc_carryover_years): this year's taxes are used before the carryovers, the earliest carryover
        first; what the prior year's excess limitation would absorb is a claim on that year and blocks. The §904(j)
        election (us_fed.individual.foreign_tax_credit_simplified_limit) takes a de minimis credit without the form and
        bars every carryover to or from the year. Everything else the form needs and the return does not state, and every
        case outside this scope (general and other categories, high-taxed income of §904(d)(2)(F), the qualified dividend
        adjustment when the adjustment exception fails, deductions allocated by other rules), is a blocking diagnostic."""
        r = self.r
        self._ftc, self._f1116 = Z, {}
        items = self._foreign_items()
        if not items:
            return
        f = "f1116[passive]"
        problems: list[tuple[str, str, str | None]] = []

        def problem(code: str, message: str, line: str | None = None) -> None:
            problems.append((code, message, line))

        years = self.p("us_fed.individual.ftc_carryover_years")
        back, forward = int(years["back"]), int(years["forward"])
        paid = sum((i["tax"] for i in items), Z)
        for i in items:
            if i["category"] is None:
                problem("form_1116_category_unknown", f"{i['name']}: state the separate category of its foreign-source income "
                                                      f"({i['ref']}.category, from Schedule K-3); a pass-through's is never defaulted.")
            elif i["category"] == "general":
                problem("form_1116_general_category", f"{i['name']}: general category income (IRC §904(d)(1)(D)) is not yet supported on Form 1116.")
            elif i["category"] != "passive":
                problem("form_1116_category_unsupported", f"{i['name']}: the {i['category']} category (IRC §904(d)(1), (6)) is not supported on Form 1116.")
        # Carryovers into this year: each is placed by the year its tax was paid (Schedule B), used within the ten years
        # after it (§904(c)) and expires with the tenth. A carryover from a later year is a carryback into this year.
        entries: list[tuple[int, Decimal, Decimal | None]] = []
        for c in (r.prior_year.ftc_carryovers if r.prior_year is not None else []):
            if c.category != "passive":
                problem("form_1116_general_category" if c.category == "general" else "form_1116_category_unsupported",
                        f"A {c.category} category foreign tax carryover of {whole(c.carryover)} is not supported on Form 1116.", "10")
            elif c.from_year is None:
                problem("form_1116_carryover_year_unknown", f"A foreign tax carryover of {whole(c.carryover)} has no year "
                        "(prior_year.ftc_carryovers[].from_year): Schedule B (Form 1116) places it by year and its expiry cannot be checked.", "10")
            elif c.from_year >= self.y:
                problem("form_1116_carryover_year_invalid", f"A foreign tax carryover from {c.from_year} into {self.y} is a carryback "
                        f"(IRC §904(c): {back} year back): it is claimed on an amended {self.y} return once {c.from_year} is filed, which is out of scope.", "10")
            elif c.from_year < self.y - forward:
                problem("form_1116_carryover_year_invalid", f"The foreign tax carryover of {whole(c.carryover)} from {c.from_year} expired after "
                        f"{c.from_year + forward} (IRC §904(c): {forward} years forward): remove it.", "10")
            elif c.carryover < 0 or (c.amt_carryover is not None and c.amt_carryover < 0):
                problem("form_1116_carryover_year_invalid", f"The foreign tax carryover from {c.from_year} is negative.", "10")
            else:
                entries.append((c.from_year, c.carryover, c.amt_carryover))
        entries.sort()
        regular_tax = self.g("f1040", "16") + self.g("sch_2", "1z")       # line 20: the tax the credit is taken against
        # §904(j): the credit without Form 1116 when every foreign tax is on passive income shown on a payee statement and
        # the total is de minimis. No limitation, and no foreign tax carried to or from the year (§904(j)(1)(B)).
        lim = self.p("us_fed.individual.foreign_tax_credit_simplified_limit")
        de_minimis = D(lim["mfj"] if self.fs == "mfj" else lim["other"])
        election = r.foreign_tax_credit.de_minimis_election
        qualifies = Z < paid <= de_minimis and all(i["category"] == "passive" for i in items)
        if qualifies and election is not False:
            if election is None and (entries or paid > regular_tax):
                why = ("the carryovers from earlier years could not be used this year" if entries else
                       f"{whole(paid - regular_tax)} of the foreign tax exceeds the tax and could not be carried forward")
                problem("form_1116_de_minimis_election_unknown",
                        f"Foreign taxes of {whole(paid)} are within the IRC §904(j) de minimis amount of {whole(de_minimis)}, but under that "
                        f"election {why} (§904(j)(1)(B)). State foreign_tax_credit.de_minimis_election: true to claim "
                        f"{whole(min(paid, regular_tax))} without Form 1116, false to file Form 1116.", "1")
            if problems:
                for code, message, line in problems:
                    self.s.diag("error", code, message, f, line)
                return
            self._ftc = min(paid, regular_tax)
            lost = paid - self._ftc
            self.s.fact("sch_3", "foreign_tax_credit", {"election": "IRC §904(j)", "stated": election is True, "form_1116": False,
                                                        "foreign_taxes": str(whole(paid)), "not_creditable": str(whole(lost))})
            self.s.diag("info", "foreign_tax_credit_904j",
                        f"Foreign tax credit of {whole(self._ftc)} claimed without Form 1116 under the IRC §904(j) election "
                        f"({'stated' if election is True else 'applied and recorded'}: foreign taxes of {whole(paid)} within {whole(de_minimis)}, "
                        f"passive income on payee statements). No foreign tax is carried to or from {self.y}"
                        + (f"; {whole(lost)} exceeds the tax and is not creditable" if lost > 0 else "") + ".", "sch_3", "1")
            self._f1116 = {"904j": paid}
            self._ftc_carry_through(entries, forward)
            return
        if qualifies:
            self.s.fact(f, "de_minimis_election", False)
        # Facts the form needs from the payer statements and the preparer.
        for i in items:
            if i["income"] is None:
                problem("form_1116_foreign_source_income_unknown",
                        f"{i['name']}: foreign tax of {whole(i['tax'])} but the foreign-source income is not stated ({i['ref']}.foreign_source_income, "
                        "from the payer's supplemental statement; enter 0 if none). Form 1116 line 1a cannot be figured while it is unknown.", "1a")
            elif i["income"] > i["gross"]:
                problem("form_1116_foreign_source_exceeds_income", f"{i['name']}: foreign-source income of {whole(i['income'])} exceeds the "
                                                                   f"item's income on the return ({whole(i['gross'])}).", "1a")
            if not i["country"]:
                problem("form_1116_country_unknown", f"{i['name']}: state the foreign country or U.S. territory ({i['ref']}.foreign_country; "
                                                     "'RIC' for a mutual fund), Form 1116 Part I line g.", "g")
        if len({i["accrued"] for i in items}) > 1:
            problem("form_1116_accrued_mixed", "Foreign taxes are stated partly as paid and partly as accrued: Form 1116 Part II claims "
                                               "the credit on one basis for the year (IRC §905(a)).", "8")
        names = sorted({i["country"] for i in items if i["country"]})
        if len(names) > FTC_COLUMNS:
            problem("form_1116_countries_exceed_columns", f"Foreign-source income from {len(names)} countries ({', '.join(names)}): Form 1116 "
                                                          f"Part I has {FTC_COLUMNS} columns and additional forms are not supported.", "g")
        # Line 3e, gross income from all sources: every item of gross income on the return before deductions (Schedule C
        # line 7, gross rents and royalties, gains before losses), excluding exempt income. A pass-through's business income
        # is net of its expenses, so its gross income is not on the return.
        gross_all = sum((self.g("f1040", x) for x in ("1z", "2b", "3b", "4b", "5b", "6b")), Z)
        gross_all += sum((pos(t.proceeds - t.cost_basis + t.adjustment) for t in r.capital_transactions), Z)
        gross_all += sum((pos(k.net_short_term_gain) + pos(k.net_long_term_gain) + k.guaranteed_payments for k in r.k1s), Z)
        gross_all += sum((d.capital_gain_distributions for d in r.dividends), Z)
        gross_all += self.g("sch_1", "1") + self.g("sch_1", "2a") + self.g("sch_1", "7")
        gross_all += sum((self.g(f"sch_c[{n}]", "7") for n in range(1, len(r.businesses) + 1)), Z)
        gross_all += sum((p.rents + p.royalties for p in r.rentals), Z)
        gross_all += sum((abs(v) for k, v in r.other_income.items() if k not in ("8a", "8d", "8s")), Z)
        for k in r.k1s:
            if k.ordinary_income or k.net_rental_income:
                problem("form_1116_gross_income_unknown", f"{k.entity_name}: a pass-through's ordinary business or rental income is net of its "
                                                          "expenses; the gross income Form 1116 line 3e needs is not on the return.", "3e")
        # Lines 3a and 3b, deductions not definitely related to any income (instructions, lines 3a, 3b); interest is line 4b.
        senior = self.g("sch_1a", "43")
        if self.itemizing:
            a = "sch_a"
            l3a = self.g(a, "4") + (self.g(a, "5a") if r.itemized.use_sales_tax else Z) + self.g(a, "5b") + self.g(a, "5c")
            unsupported = []
            if self.g(a, "5a") and not r.itemized.use_sales_tax:
                unsupported.append("state and local income taxes (Schedule A line 5a; Pub. 514, State income taxes)")
            if self.g(a, "6"):
                unsupported.append("other taxes (Schedule A line 6)")
            if self.g(a, "16"):
                unsupported.append("casualty and theft losses (Schedule A line 16)")
            if self.g(a, "17z"):
                unsupported.append("other itemized deductions (Schedule A line 17z)")
            if self.g(a, "18") != sum((self.g(a, x) for x in ("4", "7", "10", "15", "16", "17z")), Z):
                unsupported.append("the IRC §68 limitation on itemized deductions")
            if unsupported:
                problem("form_1116_deductions_unsupported", "Form 1116 lines 3a-4b: the allocation of " + "; ".join(unsupported)
                        + " between U.S. and foreign-source income is not modelled.", "3a")
            interest_expense = self.g(a, "8e") + self.g(a, "9")
        else:
            l3a = self.g("f1040", "12e")
            interest_expense = Z
        student_loan = self.g("sch_1", "21")
        l3b = self.g("sch_1", "26") - student_loan
        interest_expense += student_loan + self.g("sch_1a", "36")
        gross_cat = sum((i["income"] or Z for i in items), Z)
        if interest_expense > 0:
            small = self.dec("us_fed.individual.ftc_interest_expense_de_minimis")
            if gross_cat <= small:
                self.s.fact(f, "interest_expense_us_source", {"amount": str(whole(interest_expense)),
                                                              "rule": f"gross foreign-source income not over {whole(small)} (instructions, line 4a)"})
            else:
                problem("form_1116_interest_expense", f"Interest expense of {whole(interest_expense)} (home mortgage, investment, student loan or "
                        f"vehicle loan interest) is apportioned on Form 1116 lines 4a and 4b when gross foreign-source income exceeds {whole(small)}: "
                        "not modelled.", "4a")
        # Foreign qualified dividends: the adjustment exception (instructions, Foreign Qualified Dividends and Capital
        # Gains (Losses)): ordinary income on line 5 of the Qualified Dividends and Capital Gain Tax Worksheet within the 24%
        # bracket, and foreign-source qualified dividends plus capital gain distributions under the de minimis amount; the
        # election not to adjust is made by not adjusting. Otherwise lines 1a and 18 need the 0.4054/0.5405 adjustment.
        qualified_foreign = sum((i["qualified"] for i in items), Z)
        if self._sdtw_lines:
            problem("form_1116_qualified_dividend_adjustment", "The Schedule D Tax Worksheet was used (28% rate or unrecaptured §1250 gain): "
                                                                "the Form 1116 adjustment exception for that worksheet is not modelled.", "1a")
        elif self._qdcg_lines:
            exc = self.dec("us_fed.individual.ftc_qualified_dividend_adjustment_exception")
            ceiling = T.brackets(self.ctx, self.y, self.fs)[4][0]      # the 24% bracket ends where the 32% bracket begins
            l5 = self._qdcg_lines.get("5", Z)
            if qualified_foreign < exc and l5 <= ceiling:
                self.s.fact(f, "adjustment_exception", {"qdcg_worksheet_line_5": str(whole(l5)), "not_over": str(whole(ceiling)),
                                                        "foreign_qualified_dividends_at_most": str(whole(qualified_foreign)),
                                                        "under": str(whole(exc)), "elected_by_not_adjusting": True})
            else:
                problem("form_1116_qualified_dividend_adjustment",
                        f"The adjustment exception does not apply (Qualified Dividends and Capital Gain Tax Worksheet line 5 {whole(l5)} against "
                        f"{whole(ceiling)}; foreign-source qualified dividends of up to {whole(qualified_foreign)} against {whole(exc)}): the "
                        "0.4054/0.5405 adjustment of Form 1116 lines 1a and 18 is not modelled.", "1a")
        # Line 3g for the category, and the high tax kickout screened per payer item on its net income (§904(d)(2)(F);
        # Reg. §1.904-4(c)): foreign tax over the top §1 rate times the income makes it general category income.
        top_rate = D(self.p("us_fed.individual.tax_rates")["rates"][-1])
        l3c = l3a + l3b
        ratio = self._ftc_ratio(gross_cat, gross_all)
        l3g = whole(l3c * ratio)
        for i in items:
            if i["income"] is None:
                continue
            income = i["income"]
            net = pos(income - (l3g * income / gross_cat if gross_cat > 0 else Z))
            if i["tax"] > top_rate * net:
                problem("form_1116_high_tax_kickout", f"{i['name']}: foreign tax of {whole(i['tax'])} exceeds {top_rate * 100}% of its net "
                        f"foreign-source income of {whole(net)}: high-taxed income is general category income (IRC §904(d)(2)(B)(iii)(II), (F); "
                        "Reg. §1.904-4(c); Form 1116 line 13) and is not supported.", "13")
        if problems:
            for code, message, line in problems:
                self.s.diag("error", code, message, f, line)
            return
        columns: dict[str, dict[str, Decimal]] = {}
        for i in items:
            col = columns.setdefault(i["country"], {"gross_income": Z, "taxes_dividends": Z, "taxes_interest": Z, "taxes_other": Z, "taxes_total": Z})
            col["gross_income"] += i["income"] or Z
            col[f"taxes_{i['kind']}"] += i["tax"]
            col["taxes_total"] += i["tax"]
        self.s.fact(f, "category", "passive")
        self.s.fact(f, "columns", {c: {k: str(whole(v)) for k, v in d.items()} for c, d in columns.items()})
        self.s.fact(f, "paid_or_accrued", "accrued" if items[0]["accrued"] else "paid")
        # Part I
        self.set(f, "1a", gross_cat, "Gross foreign-source income (passive category, payer statements)")
        self.set(f, "2", Z, "Expenses definitely related to the income on line 1a")
        self.set(f, "3a", l3a, "Schedule A lines 4, 5a (general sales taxes), 5b and 5c" if self.itemizing else "Standard deduction")
        self.set(f, "3b", l3b, "Schedule 1 Part II adjustments other than interest (instructions, line 3b)")
        self.set(f, "3c", l3c)
        self.set(f, "3d", gross_cat, "Gross foreign-source income")
        self.set(f, "3e", gross_all, "Gross income from all sources")
        self.s.fact(f, "3f", str(ratio))
        self.set(f, "3g", l3g, "Pro rata share of deductions not definitely related")
        self.set(f, "4a", Z, "Home mortgage interest")
        self.set(f, "4b", Z, "Other interest expense")
        self.set(f, "5", Z, "Losses from foreign sources")
        l6 = self.set(f, "6", l3g)
        l7 = self.set(f, "7", gross_cat - l6, "Taxable income from sources outside the United States")
        # Part II
        l8 = self.set(f, "8", paid, "Foreign taxes paid or accrued (1099-INT box 6, 1099-DIV box 7, Schedule K-1)")
        # Part III
        self.set(f, "9", l8)
        l10 = self.set(f, "10", sum((reg for _, reg, _ in entries), Z), "Carryover from prior years (Schedule B)")
        l11 = self.set(f, "11", l8 + l10)
        self.set(f, "12", Z, "Reduction in foreign taxes")
        self.set(f, "13", Z, "Taxes reclassified under high tax kickout")
        l14 = self.set(f, "14", l11, "Total foreign taxes available for credit")
        l15 = self.set(f, "15", l7)
        self.set(f, "16", Z, "Adjustments to line 15")
        l17 = self.set(f, "17", l15, "Net foreign-source taxable income")
        l18 = self.set(f, "18", self._ti_unfloored + senior, "Form 1040 line 11b less line 14, plus the Schedule 1-A senior deduction")
        l19 = self._ftc_ratio(l17, l18)
        self.s.fact(f, "19", str(l19))
        l20 = self.set(f, "20", regular_tax, "Form 1040 line 16 plus Schedule 2 line 1z")
        l21 = self.set(f, "21", l20 * l19, "Maximum amount of credit")
        self.set(f, "22", Z, "Increase in limitation (IRC §960(c))")
        l23 = self.set(f, "23", l21)
        l24 = self.set(f, "24", min(l14, l23))
        # Part IV (completed even when only one Form 1116 is filed: instructions (2025), Part IV)
        l27 = self.set(f, "27", l24, "Credit for taxes on passive category income")
        l32 = self.set(f, "32", l27)
        l33 = self.set(f, "33", min(l20, l32))
        self.set(f, "34", Z, "Reduction of credit for international boycott operations")
        self._ftc = self.set(f, "35", l33, "Foreign tax credit, to Schedule 3 line 1")
        # Schedule B: this year's taxes are used first, then the carryovers, earliest year first (§904(c); Pub. 514,
        # Carryback and Carryover); the tenth-year remainder expires; this year's unused tax goes back before it goes forward.
        stated = r.prior_year.ftc_excess_limitation if r.prior_year is not None else {}
        rows, carry, trouble = self._ftc_schedule(l8, [(yr, reg) for yr, reg, _ in entries], l24,
                                                  self._ftc_room(stated.get("passive"), [(yr, reg) for yr, reg, _ in entries]), forward, amt=False)
        self.s.fact(f, "schedule_b", rows)
        self._carryforwards.update(carry)
        for code, message in trouble:
            self.s.diag("error", code, message, f, "10")
        self._f1116["passive"] = {"form": f, "taxes": l8, "foreign_ti": l17, "entries": entries, "forward": forward}

    def _ftc_room(self, stated: Decimal | None, entries: list[tuple[int, Decimal]]) -> Decimal | None:
        """The prior year's excess limitation, the room a carryback would use: zero when that year itself had unused tax
        (a carryover from it is on this return), else as stated; None when it is not known."""
        if any(yr == self.y - 1 and amount > 0 for yr, amount in entries):
            return Z
        return stated

    def _ftc_schedule(self, taxes_now: Decimal, entries: list[tuple[int, Decimal]], used: Decimal, room: Decimal | None,
                      forward: int, *, amt: bool) -> tuple[list[dict[str, Any]], dict[str, Decimal], list[tuple[str, str]]]:
        """Form 1116 Schedule B for one category: how this year's credit (`used`) absorbs this year's taxes and then the
        carryovers (earliest first), what expires, and what carries to next year. `room` is the prior year's excess
        limitation that a carryback of this year's unused tax uses first (§904(c)); None blocks the carryforward."""
        key = f"ftc_{'amt_' if amt else ''}carryover_passive"
        label = "AMT " if amt else ""
        used_now = min(taxes_now, used)
        left = used - used_now
        rows: list[dict[str, Any]] = []
        carry: dict[str, Decimal] = {}
        trouble: list[tuple[str, str]] = []
        for yr, amount in entries:
            take = min(amount, left)
            left -= take
            rest = amount - take
            expired = rest if yr == self.y - forward else Z
            out = rest - expired
            rows.append({"from_year": yr, "carryover_in": str(whole(amount)), "used": str(whole(take)), "expired": str(whole(expired)),
                         "carryover_out": str(whole(out))})
            if out > 0:
                carry[f"{key}_{yr}"] = whole(out)
        unused = taxes_now - used_now
        carryback = Z
        if unused > 0:
            where = f"prior_year.{'ftc_amt_excess_limitation' if amt else 'ftc_excess_limitation'}.passive"
            if room is None:
                trouble.append((f"form_1116_{'amt_' if amt else ''}carryback_unknown",
                                f"{whole(unused)} of this year's {label}foreign tax is unused (IRC §904(c)): it is deemed paid in {self.y - 1} first, up to "
                                f"that year's excess limitation, which is not stated ({where}; enter 0 if {self.y - 1} had unused foreign tax or no "
                                f"foreign-source income). The carryover to {self.y + 1} cannot be figured while it is unknown."))
            else:
                carryback = min(unused, room)
                if carryback > 0:
                    trouble.append((f"form_1116_{'amt_' if amt else ''}carryback",
                                    f"{whole(carryback)} of this year's {label}foreign tax is deemed paid in {self.y - 1} (IRC §904(c); that year's excess "
                                    f"limitation of {whole(room)}) and is claimed on an amended {self.y - 1} return, which is out of scope; "
                                    f"{whole(unused - carryback)} carries to {self.y + 1}."))
        known = room is not None or unused == 0
        rows.append({"from_year": self.y, "generated": str(whole(unused)), "carryback": str(whole(carryback)),
                     "carryover_out": str(whole(unused - carryback)) if known else "unknown"})
        if known and unused - carryback > 0:
            carry[f"{key}_{self.y}"] = whole(unused - carryback)
        return rows, carry, trouble

    def _ftc_carry_through(self, entries: list[tuple[int, Decimal, Decimal | None]], forward: int) -> None:
        """In a §904(j) election year no carryover is used; those from other years pass through unaffected (instructions,
        Election To Claim the Foreign Tax Credit Without Filing Form 1116), the tenth-year ones expiring."""
        rows = []
        for yr, reg, amt in entries:
            expired = yr == self.y - forward
            rows.append({"from_year": yr, "carryover_in": str(whole(reg)), "used": "0", "expired": str(whole(reg if expired else Z)),
                         "carryover_out": str(whole(Z if expired else reg)), "amt_carryover_out": str(whole(amt)) if amt is not None and not expired else None})
            if not expired:
                self._carryforwards[f"ftc_carryover_passive_{yr}"] = whole(reg)
                if amt is not None:
                    self._carryforwards[f"ftc_amt_carryover_passive_{yr}"] = whole(amt)
        if rows:
            self.s.fact("sch_3", "ftc_carryovers_unaffected", rows)

    def _amt_ftc(self, amti: Decimal, tmt: Decimal) -> Decimal:
        """Form 6251 line 8, the AMT foreign tax credit (IRC §59(a)), under the simplified limitation election of
        §59(a)(3): the AMT Form 1116 takes the regular form's net foreign-source taxable income (line 17) over alternative
        minimum taxable income (Form 6251 line 4, on line 18) and the tentative minimum tax before the credit (Form 6251
        line 7, on line 20); the taxes available are this year's plus the AMT credit's own carryovers (Form 6251
        instructions, line 8). The election is made for the first year an AMT credit is claimed and binds every later
        year unless revoked with consent (§59(a)(3)(B)); it is never assumed. Under §904(j) the credit is the same
        de minimis amount. The credit is claimed, and its facts required, when Form 6251 must be filed (line 7 over
        line 10); otherwise the AMT Form 1116 only keeps the AMT credit's own carryover schedule, recorded when its facts
        are stated and noted as untracked otherwise."""
        r = self.r
        if not self._f1116:
            return Z
        if "904j" in self._f1116:
            return min(self._f1116["904j"], tmt) if tmt > 0 else Z
        cat = self._f1116["passive"]
        f, forward = cat["form"], cat["forward"]
        entries: list[tuple[int, Decimal, Decimal | None]] = cat["entries"]
        stated = r.prior_year.ftc_amt_excess_limitation if r.prior_year is not None else {}
        amt_entries = [(yr, a) for yr, _, a in entries if a is not None]
        missing = [yr for yr, _, a in entries if a is None]
        election = r.foreign_tax_credit.amt_simplified_limitation
        prior = r.prior_year.amt_ftc_simplified_election if r.prior_year is not None else None
        claimed = tmt > pos(self.g("f1040", "16") + self.g("sch_2", "1z") - self._ftc)       # Form 6251 line 7 over line 10
        if claimed:
            if prior is True and election is False:
                self.s.diag("error", "form_1116_amt_election_revoked", "The simplified limitation election for the AMT foreign tax credit was made in "
                            "an earlier year and applies to every later year unless revoked with the IRS's consent (IRC §59(a)(3)(B)(ii)); it "
                            "cannot be stated as not made.", "f6251", "8")
                return Z
            if election is False:
                self.s.diag("error", "form_1116_amt_election_not_made", "Without the IRC §59(a)(3) simplified limitation election the AMT Form 1116 "
                            "is figured on foreign-source alternative minimum taxable income (§59(a)(1)(B)), which is not modelled. State "
                            "foreign_tax_credit.amt_simplified_limitation: true if the election was or is being made.", "f6251", "8")
                return Z
            if election is None and prior is not True:
                self.s.diag("error", "form_1116_amt_election_unknown", f"Tentative minimum tax of {whole(tmt)} and foreign taxes of {whole(cat['taxes'])}: "
                            "Form 6251 line 8 needs the AMT Form 1116. State foreign_tax_credit.amt_simplified_limitation (IRC §59(a)(3): the election "
                            "is made for the first year an AMT foreign tax credit is claimed, binds every later year, and is recorded on this return); "
                            "the credit is not figured while it is unknown.", "f6251", "8")
                return Z
            self.s.fact("f6251", "simplified_limitation_election",
                        {"elected": True, "first_year": "earlier" if prior else self.y, "binding": "IRC §59(a)(3)(B): every later year unless revoked with consent"})
            if not prior:
                self.s.diag("info", "form_1116_amt_election_recorded", f"The IRC §59(a)(3) simplified limitation election for the AMT foreign tax "
                            f"credit is made with this {self.y} return and binds every later year (prior_year.amt_ftc_simplified_election: true on them).",
                            "f6251", "8")
            if missing:
                self.s.diag("error", "form_1116_amt_carryover_unknown", f"The AMT foreign tax credit has its own carryovers (IRC §59(a)(1)): state the "
                            f"AMT carryover of {', '.join(str(y) for y in missing)} (prior_year.ftc_carryovers[].amt_carryover; enter 0 if none).", "f6251", "8")
                return Z
        else:
            reasons = []
            if not (election is True or prior is True):
                reasons.append("the IRC §59(a)(3) simplified limitation election is not stated (foreign_tax_credit.amt_simplified_limitation)")
            if missing:
                reasons.append(f"the AMT carryover of {', '.join(str(y) for y in missing)} is not stated (prior_year.ftc_carryovers[].amt_carryover)")
            if reasons:
                self._amt_untracked(f, reasons)
                return Z
        l14 = cat["taxes"] + sum((a for _, a in amt_entries), Z)
        l17 = cat["foreign_ti"]
        l19 = self._ftc_ratio(l17, amti)
        l21 = whole(tmt * l19)
        l24 = min(l14, l21)
        l33 = min(tmt, l24)
        self.s.fact(f, "amt", {"14": str(whole(l14)), "17": str(whole(l17)), "18": str(whole(amti)), "19": str(l19), "20": str(whole(tmt)),
                               "21": str(l21), "24": str(whole(l24)), "33": str(whole(l33)), "claimed": claimed})
        rows, carry, trouble = self._ftc_schedule(cat["taxes"], amt_entries, l33, self._ftc_room(stated.get("passive"), amt_entries), forward, amt=True)
        if trouble and not claimed:
            self._amt_untracked(f, [m for _, m in trouble])
            return l33
        self.s.fact(f, "schedule_b_amt", rows)
        self._carryforwards.update(carry)
        for code, message in trouble:
            self.s.diag("error", code, message, "f6251", "8")
        return l33

    def _amt_untracked(self, form: str, reasons: list[str]) -> None:
        self.s.diag("warning", "form_1116_amt_carryover_not_tracked",
                    "No AMT foreign tax credit is claimed this year (Form 6251 line 7 is not over line 10). This year's foreign taxes and any AMT "
                    "carryovers carry to next year under the AMT credit's own schedule (IRC §59(a)(1)), which is not recorded: " + "; ".join(reasons) + ".",
                    form)

    # ----------------------------------------------------------------- credits
    def _credits(self) -> None:
        l18 = self.g("f1040", "18")
        ftc = min(self._ftc, l18)
        self.set("sch_3", "1", ftc, "Foreign tax credit")
        remaining = l18 - ftc
        dc = min(self._form_2441(), remaining)
        self.set("sch_3", "2", dc, "Form 2441, line 11")
        remaining -= dc
        edu_nonref, edu_ref = self._form_8863(remaining)
        self.set("sch_3", "3", edu_nonref, "Form 8863, line 19")
        remaining -= edu_nonref
        self.set("sch_3", "4", self._form_8880(pos(remaining)), "Form 8880, line 12")
        self.set("sch_3", "7", Z)
        self.set("sch_3", "8", sum((self.g("sch_3", x) for x in ("1", "2", "3", "4", "5a", "7")), Z))
        ctc, self._ctc_unused = self._schedule_8812_part_i(l18 - self.g("sch_3", "8"))
        self.set("f1040", "19", ctc, "Schedule 8812, line 14")
        self.set("f1040", "20", self.g("sch_3", "8"))
        self.set("f1040", "21", self.g("f1040", "19") + self.g("f1040", "20"))
        self.set("f1040", "22", pos(l18 - self.g("f1040", "21")))
        self._aotc_refundable = edu_ref

    def _form_8880(self, limit: Decimal) -> Decimal:
        """Form 8880, credit for qualified retirement savings contributions (IRC §25B). `limit` is the Credit Limit
        Worksheet amount: Form 1040 line 18 less Schedule 3 lines 1 through 3 (lines 6d and 6l are not computed).
        Contributions come from documents (W-2 box 12, Form 5498 boxes 1 and 10) and stated facts; the credit is never
        claimed for a person whose eligibility or testing-period distributions are unknown."""
        r = self.r
        f = "f8880"
        if r.retirement_savings_contributions:
            self.s.diag("error", "form_8880_deprecated_input",
                        "retirement_savings_contributions is no longer an input: the saver's credit is figured from W-2 box 12, "
                        "Form 5498 and the retirement_savings facts. Remove it.", "sch_3", "4")
        stated = {x.owner: x for x in r.retirement_savings}
        lines: dict[Owner, dict[str, Decimal]] = {}
        for owner in self.owners():
            hand = stated.get(owner)
            ira = sum((v for a in r.ira_accounts if a.owner == owner for v in (a.ira_contributions, a.roth_contributions) if v), Z)
            deferrals = sum((amt for w in r.w2s if w.owner == owner for code, amt in w.box12.items()
                             if code.upper() in SAVERS_CREDIT_W2_CODES), Z)
            lines[owner] = {"1": ira + (hand.able_contributions if hand else Z),
                            "2": deferrals + (hand.voluntary_after_tax_contributions if hand else Z)}
        if not any(v["1"] + v["2"] > 0 for v in lines.values()):
            return Z
        rule = self.ctx.try_param("us_fed.individual.savers_credit", self.on)
        agi_limits = self.ctx.try_param("us_fed.individual.savers_credit_agi_limits", self.on)
        if rule is None or agi_limits is None:
            self.s.diag("error", "savers_credit_rule_missing",
                        f"No published saver's credit figures cover {self.on.isoformat()} (us_fed.individual.savers_credit, "
                        "us_fed.individual.savers_credit_agi_limits): the credit is not figured.", "sch_3", "4")
            return Z
        # Line 9: the applicable percentage by filing status and AGI. A qualifying surviving spouse uses the "all other"
        # column (Form 8880 line 9 table), not the joint one.
        bands = agi_limits["mfj" if self.fs == "mfj" else "hoh" if self.fs == "hoh" else "other"]
        rates = (D(rule["rate_50"]), D(rule["rate_20"]), D(rule["rate_10"]))
        rate = next((rt for top, rt in zip(bands, rates) if self.agi <= D(top)), Z)
        could_matter = rate > 0 and limit > 0
        # Line 4: distributions in the testing period (IRC §25B(d)(2)). This year's come from the 1099-Rs on the return
        # (less rollovers); the two prior years and the months before the due date are a stated fact per person.
        distributions: dict[Owner, Decimal] = {}
        unknown: list[str] = []
        for owner in self.owners():
            this_year = sum((pos(d.gross_distribution - d.rollover_amount) for d in r.retirement
                             if d.owner == owner and not set(d.distribution_code.upper()) & ROLLOVER_DISTRIBUTION_CODES), Z)
            hand = stated.get(owner)
            earlier = hand.testing_period_distributions if hand else None
            if earlier is None:
                unknown.append(owner)
            distributions[owner] = this_year + (earlier or Z)

        def not_filed() -> Decimal:
            """The form is not part of the return: no credit, and no partial Form 8880 left in the package."""
            self.s.forms.pop(f, None)
            self.s.notes.pop(f, None)
            self.s.facts.pop(f, None)
            return Z

        # Every unknown fact the credit depends on is reported at once; the credit is claimed only when all are stated.
        problems: list[tuple[str, str, str | None]] = []
        if unknown and could_matter:
            problems.append(("form_8880_testing_period_unknown",
                             f"Form 8880 line 4: the distributions received in {self.y - 2}, {self.y - 1} and before the {self.y} return's "
                             f"due date are not stated for {', '.join(unknown)} (retirement_savings[].testing_period_distributions; enter "
                             "0 if none). The credit is not claimed while they are unknown.", "4"))
        total = sum(distributions.values(), Z)                 # joint: both spouses' amounts in both columns
        col = {"taxpayer": "a", "spouse": "b"}
        l7 = Z
        eligible: dict[str, Any] = {}
        for owner in self.owners():
            c = col[owner]
            v = lines[owner]
            l3 = self.set(f, f"3{c}", self.set(f, f"1{c}", v["1"]) + self.set(f, f"2{c}", v["2"]))
            if l3 <= 0:
                continue
            l4 = self.set(f, f"4{c}", total if self.joint else distributions[owner])
            l5 = self.set(f, f"5{c}", pos(l3 - l4))
            l6 = min(l5, D(rule["contribution_cap"]))
            p = self.person(owner)
            reason = None
            if p is None or p.can_be_claimed_as_dependent:
                reason = "can be claimed as a dependent on someone else's return"
            elif p.dob is None:
                if l6 > 0 and could_matter:
                    problems.append(("form_8880_age_unknown",
                                     f"Form 8880: the {owner}'s date of birth is needed to show they were 18 at the end of {self.y}.", None))
                reason = "date of birth not stated"
            elif p.dob > date(self.y - 17, 1, 1):          # born after January 1 of the year they would turn 17
                reason = f"under 18 at the end of {self.y} (born after {date(self.y - 17, 1, 1).isoformat()})"
            elif p.full_time_student is None:
                if l6 > 0 and could_matter:
                    problems.append(("form_8880_student_status_unknown",
                                     f"Form 8880: state whether the {owner} was a full-time student during any part of 5 months of "
                                     f"{self.y} (full_time_student). The credit is not claimed while it is unknown.", None))
                reason = "student status not stated"
            elif p.full_time_student:
                reason = "a full-time student"
            eligible[owner] = reason or "eligible"
            if reason:
                l6 = Z
            l7 += self.set(f, f"6{c}", l6)
        if problems:
            for code, message, line in problems:
                self.s.diag("error", code, message, f, line)
            return not_filed()
        self.s.fact(f, "eligible", eligible)
        self.set(f, "7", l7)
        self.set(f, "8", self.agi)
        self.s.fact(f, "9", str(rate))
        l10 = self.set(f, "10", l7 * rate)
        l11 = self.set(f, "11", limit, "Credit Limit Worksheet: Form 1040 line 18 less Schedule 3 lines 1-3")
        credit = self.set(f, "12", min(l10, l11), "Credit for qualified retirement savings contributions")
        if credit <= 0:
            why = ("adjusted gross income is above the limit" if rate == 0 else "no tax remains after the preceding credits"
                   if limit <= 0 else "no eligible contributions remain after the testing-period distributions and eligibility tests")
            self.s.diag("info", "form_8880_no_credit", f"No saver's credit: {why}.", "sch_3", "4")
            return not_filed()
        return credit

    def _form_2441(self) -> Decimal:
        r = self.r
        n = r.dependent_care_qualifying_persons or sum(1 for s in self.deps if s.under_13 and s.dependent)
        expenses = r.dependent_care_expenses
        if n == 0 or expenses <= 0:
            return Z
        if self.mfs and not r.mfs_lived_apart_all_year:
            self.s.diag("info", "2441_mfs", "No dependent care credit for married filing separately unless living apart (IRC §21(e)(2), (4)).")
            return Z
        dc = self.p("us_fed.individual.dependent_care_credit")
        limit = D(dc["expense_limit_one"] if n == 1 else dc["expense_limit_two"])
        f = "f2441"
        self.set(f, "2", expenses)
        limit = pos(limit - self._dc_excluded)
        earned = [self._earned_income_of(o) for o in self.owners()]
        qualified = min([expenses, limit, *earned])
        self.set(f, "3", min(expenses, limit))
        self.set(f, "4", earned[0])
        if self.joint:
            self.set(f, "5", earned[1])
        self.set(f, "6", qualified)
        self.set(f, "7", self.agi)
        pct = D(dc["max_pct"]) - steps(self.agi - D(dc["first_threshold"]), D(dc["first_step"]), round_up=True) / 100
        pct = max(pct, D(dc["first_floor_pct"]))
        second_thr = D(dc["second_threshold_mfj"] if self.fs == "mfj" else dc["second_threshold"])
        second_step = D(dc["second_step_mfj"] if self.fs == "mfj" else dc["second_step"])
        pct = max(D(dc["second_floor_pct"]), pct - steps(self.agi - second_thr, second_step, round_up=True) / 100)
        self.s.fact(f, "8", str(pct))
        tentative = self.set(f, "9a", qualified * pct)
        self.set(f, "10", pos(self.g("f1040", "18") - self.g("sch_3", "1")), "Credit limit")
        return self.set(f, "11", min(tentative, self.g(f, "10")), "Credit for child and dependent care expenses")

    def _form_8863(self, limit: Decimal) -> tuple[Decimal, Decimal]:
        r = self.r
        if not r.students:
            return Z, Z
        if self.mfs or r.taxpayer.can_be_claimed_as_dependent:
            self.s.diag("info", "education_credit_not_allowed", "No education credits for married filing separately or a dependent (IRC §25A(g)).")
            return Z, Z
        e = self.p("us_fed.individual.education_credits")
        start = D(e["phaseout_start_mfj"] if self.fs == "mfj" else e["phaseout_start"])
        width = D(e["phaseout_range_mfj"] if self.fs == "mfj" else e["phaseout_range"])
        factor = Decimal(1) if self.agi <= start else max(Z, ((start + width - self.agi) / width).quantize(Decimal("0.001")))
        aotc = llc_exp = Z
        rows = []
        for st in r.students:
            if st.aotc_eligible and st.aotc_years_claimed < 4:
                first = min(st.qualified_expenses, D(e["aotc_first_tier"]))
                second = min(pos(st.qualified_expenses - D(e["aotc_first_tier"])), D(e["aotc_second_tier"]))
                credit = first + second * D(e["aotc_second_rate"])
                aotc += credit
                rows.append({"student": st.name, "credit": "aotc", "amount": str(whole(credit))})
            else:
                llc_exp += st.qualified_expenses
                rows.append({"student": st.name, "credit": "llc", "expenses": str(whole(st.qualified_expenses))})
        f = "f8863"
        self.s.fact(f, "students", rows)
        self.set(f, "1", aotc)
        self.s.fact(f, "6", str(factor))
        l7 = self.set(f, "7", aotc * factor)
        refundable = self.set(f, "8", l7 * D(e["aotc_refundable_pct"]), "Refundable American opportunity credit")
        self.set(f, "9", l7 - refundable)
        llc = min(llc_exp, D(e["llc_expense_limit"])) * D(e["llc_rate"]) * factor
        self.set(f, "10", min(llc_exp, D(e["llc_expense_limit"])))
        self.set(f, "18", llc)
        nonref = self.set(f, "19", min(self.g(f, "9") + llc, limit), "Nonrefundable education credits")
        if refundable:
            self.s.diag("info", "aotc_refundable_age", "Confirm the refundable AOTC is allowed: not if the taxpayer is under 24 "
                                                        "and the IRC §25A(i)(6) conditions apply.", f, "8")
        return nonref, refundable

    def _earned_income(self) -> Decimal:
        """Earned income for the EIC and the additional child tax credit (wages plus net self-employment earnings)."""
        wages = self.g("f1040", "1z")
        se_net = sum((v["profit"] for v in self.se.values()), Z) - self._se_half
        return wages + se_net

    def _schedule_8812_part_i(self, limit: Decimal) -> tuple[Decimal, Decimal]:
        ctc = self.p("us_fed.individual.child_tax_credit")
        n_ctc = sum(1 for s in self.deps if s.ctc)
        n_odc = sum(1 for s in self.deps if s.odc)
        if n_ctc + n_odc == 0:
            return Z, Z
        f = "sch_8812"
        self.set(f, "1", self.agi)
        l3 = self.set(f, "3", self.agi)
        self.s.fact(f, "4", n_ctc)
        self.set(f, "5", n_ctc * D(ctc["per_child"]))
        self.s.fact(f, "6", n_odc)
        self.set(f, "7", n_odc * D(ctc["other_dependent"]))
        l8 = self.set(f, "8", self.g(f, "5") + self.g(f, "7"))
        thr = D(ctc["threshold_mfj"] if self.fs == "mfj" else ctc["threshold_other"])
        self.set(f, "9", thr)
        l10 = self.set(f, "10", steps(l3 - thr, D(ctc["step"]), round_up=True) * D(ctc["step"]))
        l11 = self.set(f, "11", l10 * D(ctc["reduction_per_step"]) / D(ctc["step"]))
        l12 = self.set(f, "12", pos(l8 - l11))
        l13 = self.set(f, "13", pos(limit), "Credit Limit Worksheet A")
        l14 = self.set(f, "14", min(l12, l13), "Child tax credit and credit for other dependents")
        self._n_ctc = n_ctc
        return l14, l12 - l14

    def _schedule_8812_part_ii(self, eic: Decimal) -> Decimal:
        ctc = self.p("us_fed.individual.child_tax_credit")
        n = getattr(self, "_n_ctc", 0)
        f = "sch_8812"
        if n == 0 or self._ctc_unused <= 0:
            return Z
        l16a = self.set(f, "16a", self._ctc_unused)
        l16b = self.set(f, "16b", n * D(ctc["refundable_per_child"]))
        l17 = self.set(f, "17", min(l16a, l16b))
        l18a = self.set(f, "18a", self._earned_income())
        l19 = self.set(f, "19", pos(l18a - D(ctc["earned_income_floor"])))
        l20 = self.set(f, "20", l19 * D(ctc["earned_income_rate"]))
        if n >= 3:
            withheld = sum((w.ss_tax + w.medicare_tax for w in self.r.w2s), Z) + self.g("f8959", "24")
            l21 = self.set(f, "21", withheld)
            l22 = self.set(f, "22", self.g("sch_1", "15"))
            l23 = self.set(f, "23", l21 + l22)
            l24 = self.set(f, "24", eic + self.g("sch_3", "11"))
            l25 = self.set(f, "25", pos(l23 - l24))
            l26 = self.set(f, "26", max(l20, l25))
        else:
            l26 = l20
        return self.set(f, "27", min(l17, l26), "Additional child tax credit")

    # ----------------------------------------------------------------- other taxes
    def _other_taxes(self) -> None:
        r = self.r
        self.set("sch_2", "4", self._se_tax, "Schedule SE")
        self.set("sch_2", "5", self._early_distribution_tax, "Additional tax on early distributions (IRC §72(t))")
        self.set("sch_2", "6", self._form_8960(), "Form 8960")
        part_i, part_ii = self._form_8959()
        self.set("sch_2", "11", part_ii, "Form 8959, Part II")
        self.set("sch_2", "13c", sum((h["17b"] for h in self._hsa.values()), Z), "Additional 20% tax on HSA distributions, Form 8889 line 17b")
        self.set("sch_2", "13d", sum((h["21"] for h in self._hsa.values()), Z),
                 "Additional 10% tax for failure to remain an eligible individual, Form 8889 line 21")
        self.set("sch_2", "14", self.g("sch_2", "13c") + self.g("sch_2", "13d"), "Total additional income taxes, lines 13a through 13z")
        self.set("sch_2", "15", sum((self.g("sch_2", x) for x in ("4", "5", "6", "7", "8", "9", "10", "11", "12", "14")), Z))
        self.set("sch_2", "17a", r.household_employment_taxes, "Schedule H")
        self.set("sch_2", "17b", part_i, "Form 8959, Part I")
        self.set("sch_2", "17d", self.g("sch_2", "17a") + self.g("sch_2", "17b"))
        self.set("sch_2", "20", self.g("sch_2", "16c") + self.g("sch_2", "17d") + self.g("sch_2", "18") + self.g("sch_2", "19c"))
        self.set("sch_2", "21", self.g("sch_2", "15") + self.g("sch_2", "20"), "Total additional taxes")
        self.set("f1040", "23", self.g("sch_2", "21"))
        self.set("f1040", "24a", self.g("f1040", "22") + self.g("f1040", "23"), "Total tax")
        self.set("f1040", "24c", self.g("f1040", "24a"))

    def _threshold(self, rule: dict) -> Decimal:
        return D(rule["threshold_mfj"] if self.fs == "mfj" else rule["threshold_mfs"] if self.mfs else rule["threshold_other"])

    def _form_8959(self) -> tuple[Decimal, Decimal]:
        am = self.p("us_fed.individual.additional_medicare_tax")
        rate, thr = D(am["rate"]), self._threshold(am)
        f = "f8959"
        medicare_wages = sum((w.medicare_wages for w in self.r.w2s), Z)
        se_income = sum((pos(v["net_earnings"]) for v in self.se.values()), Z)
        withheld = sum((w.medicare_tax for w in self.r.w2s), Z)
        if medicare_wages <= thr and se_income + medicare_wages <= thr and withheld <= medicare_wages * Decimal("0.0145"):
            return Z, Z
        self.set(f, "1", medicare_wages)
        l4 = self.set(f, "4", medicare_wages)
        self.set(f, "5", thr)
        l6 = self.set(f, "6", pos(l4 - thr))
        l7 = self.set(f, "7", l6 * rate)
        l8 = self.set(f, "8", se_income)
        self.set(f, "9", thr)
        self.set(f, "10", l4)
        l11 = self.set(f, "11", pos(thr - l4))
        l12 = self.set(f, "12", pos(l8 - l11))
        l13 = self.set(f, "13", l12 * rate)
        self.set(f, "18", l7 + l13)
        hi = D(self.p("us_fed.payroll.employee_tax_rates")["hi"])
        self.set(f, "19", withheld)
        self.set(f, "20", medicare_wages)
        self.set(f, "21", medicare_wages * hi)
        l22 = self.set(f, "22", pos(withheld - self.g(f, "21")))
        self.set(f, "24", l22, "Additional Medicare Tax withholding")
        return l7, l13

    def _form_8960(self) -> Decimal:
        n = self.p("us_fed.individual.net_investment_income_tax")
        thr = self._threshold(n)
        if self.agi <= thr:
            return Z
        f = "f8960"
        self.set(f, "1", self.g("f1040", "2b"))
        self.set(f, "2", self.g("f1040", "3b"))
        rentals = self.g("sch_e", "26")
        passive_k1 = sum((k.ordinary_income + k.net_rental_income for k in self.r.k1s if k.passive), Z)
        self.set(f, "4a", rentals + passive_k1)
        if any(p.real_estate_professional for p in self.r.rentals):
            self.s.diag("warning", "niit_reps", "Real estate professional rentals may be excluded from NIIT only with a trade or "
                                                "business finding (Reg. §1.1411-4(g)(7)); review line 4b.", f, "4b")
        self.set(f, "4c", self.g(f, "4a"))
        # Line 5a: Form 1040 line 7a and Schedule 1 line 4 (Form 8960 (2025) instructions); line 5b excludes the gains and losses
        # on property held in a trade or business that is not a section 1411 trade or business (Reg. §1.1411-4(d)(4)(i)), as
        # the Lines 5a-5d worksheet lines 2(a) and 2(b): gains as negative amounts, losses as positive amounts.
        self.set(f, "5a", self.g("f1040", "7a") + self.g("sch_1", "4"), "Form 1040 line 7a and Schedule 1 line 4")
        st = self._f4797
        if st.niit_unplaced:
            self.s.diag("error", "form_8960_disposition_activity_unknown",
                        f"{st.niit_unplaced} Form 4797 disposition(s) name no activity (schedule_c, rental or k1): whether the gain or loss is "
                        "net investment income (Form 8960 line 5b) cannot be decided.", f, "5b")
        self.set(f, "5b", st.niit_excluded, "Gain or loss on property held in a non-section 1411 trade or business (Form 4797), excluded from NII")
        self.set(f, "5d", self.g(f, "5a") + self.g(f, "5b") + self.g(f, "5c"))
        l8 = self.set(f, "8", sum((self.g(f, x) for x in ("1", "2", "3", "4c", "5d", "6", "7")), Z), "Total investment income")
        self.set(f, "11", Z)
        l12 = self.set(f, "12", pos(l8 - self.g(f, "11")), "Net investment income")
        self.set(f, "13", self.agi)
        self.set(f, "14", thr)
        l15 = self.set(f, "15", pos(self.agi - thr))
        l16 = self.set(f, "16", min(l12, l15))
        return self.set(f, "17", l16 * D(n["rate"]), "Net investment income tax")

    # ----------------------------------------------------------------- payments
    def _excess_social_security(self) -> Decimal:
        rates = self.p("us_fed.payroll.employee_tax_rates")
        cap = whole(D(rates["oasdi"]) * self.dec("us_fed.payroll.social_security_wage_base"))
        total = Z
        for owner in self.owners():
            ws = [w for w in self.r.w2s if w.owner == owner]
            if len(ws) < 2:
                if ws and ws[0].ss_tax > cap:
                    self.s.diag("warning", "employer_over_withheld", "A single employer withheld too much social security tax; "
                                                                      "the employer must refund it (not claimable on the return).")
                continue
            withheld = sum((w.ss_tax for w in ws), Z)
            own_excess = sum((pos(w.ss_tax - cap) for w in ws), Z)
            total += pos(withheld - cap - own_excess)
        return total

    def _eic(self) -> Decimal:
        r = self.r
        if not r.claim_eic:
            self.s.fact("f1040", "27c", True)
            return Z
        children = min(3, sum(1 for s in self.deps if s.eic))
        if r.taxpayer.can_be_claimed_as_dependent:
            return Z
        if self.mfs and not (r.mfs_lived_apart_all_year and children):
            return Z
        if not r.taxpayer.ssn_valid_for_employment or (self.joint and r.spouse and not r.spouse.ssn_valid_for_employment):
            return Z
        inv = (self.g("f1040", "2b") + self.g("f1040", "2a") + self.g("f1040", "3b") + pos(self.g("f1040", "7a"))
               + pos(self.g("sch_e", "26")))
        if inv > self.dec("us_fed.individual.eitc_investment_income_limit"):
            self.s.diag("info", "eic_investment_income", "No EIC: investment income exceeds the limit (IRC §32(i)).")
            return Z
        if children == 0:
            def in_age(p: Person | None) -> bool:
                return bool(p and p.dob and date(self.y - 64, 1, 2) <= p.dob < date(self.y - 24, 1, 2))
            if not (in_age(r.taxpayer) or (self.joint and in_age(r.spouse))):
                return Z
        t = self.p("us_fed.individual.eitc_parameters")
        rates = self.p("us_fed.individual.eitc_rates")
        i = children
        joint = self.fs == "mfj"
        start = D(t["phaseout_start_mfj"][i] if joint else t["phaseout_start"][i])
        end = D(t["phaseout_end_mfj"][i] if joint else t["phaseout_end"][i])
        max_credit, credit_pct, phase_pct = D(t["max_credit"][i]), D(rates["credit_pct"][i]), D(rates["phaseout_pct"][i])

        def credit_at(x: Decimal) -> Decimal:
            if x <= 0 or x >= end:
                return Z
            mid = (x // 50) * 50 + 25  # EIC Table rows are $50 wide; amounts are figured at the midpoint
            return pos(whole(min(max_credit, mid * credit_pct) - pos(mid - start) * phase_pct))

        earned = self._earned_income()
        eic = credit_at(earned)
        if self.agi >= start and self.agi != earned:
            eic = min(eic, credit_at(self.agi))
        if eic and children:
            self.s.fact("sch_eic", "children", [
                {"name": f"{s.dep.first_name} {s.dep.last_name}".strip(), "ssn": s.dep.ssn, "dob": str(s.dep.dob),
                 "relationship": s.dep.relationship, "months": s.dep.months_in_home} for s in self.deps if s.eic][:3])
        return eic

    def _payments(self) -> None:
        r = self.r
        self.set("f1040", "25a", sum((w.federal_withholding for w in r.w2s), Z), "Form(s) W-2, box 2")
        k1099 = (sum((i.federal_withholding for i in r.interest), Z) + sum((d.federal_withholding for d in r.dividends), Z)
                 + sum((x.federal_withholding for x in r.retirement), Z) + sum((s.federal_withholding for s in r.social_security), Z)
                 + sum((u.federal_withholding for u in r.unemployment), Z) + sum((t.federal_withholding for t in r.capital_transactions), Z)
                 + sum((b.federal_withholding for b in r.businesses), Z))
        self.set("f1040", "25b", k1099, "Form(s) 1099")
        self.set("f1040", "25c", self.g("f8959", "24") + r.payments.other_withholding, "Other forms (Form 8959, line 24)")
        self.set("f1040", "25d", self.g("f1040", "25a") + self.g("f1040", "25b") + self.g("f1040", "25c"))
        self.set("f1040", "26", r.payments.estimated_tax_payments + r.payments.prior_year_overpayment_applied)
        self.set("sch_3", "9", r.net_premium_tax_credit, "Form 8962")
        self.set("sch_3", "10", r.payments.extension_payment)
        self.set("sch_3", "11", self._excess_social_security(), "Excess social security tax withheld")
        self.set("sch_3", "15", sum((self.g("sch_3", x) for x in ("9", "10", "11", "12", "14")), Z))
        eic = self.set("f1040", "27a", self._eic(), "Earned income credit")
        actc = self.set("f1040", "28", self._schedule_8812_part_ii(eic), "Schedule 8812, line 27")
        self.set("f1040", "29", self._aotc_refundable, "Form 8863, line 8")
        self.set("f1040", "31", self.g("sch_3", "15"))
        l32a = self.set("f1040", "32a", sum((self.g("f1040", x) for x in ("27a", "28", "29", "30", "31")), Z))
        self.set("f1040", "32b", self._schedule_3a(l32a) if (eic or actc or self._aotc_refundable) else Z, "Schedule 3-A")
        self.set("f1040", "32c", l32a - self.g("f1040", "32b"))
        total = self.set("f1040", "33", self.g("f1040", "25d") + self.g("f1040", "26") + self.g("f1040", "32c"), "Total payments")
        tax = self.g("f1040", "24c")
        if total > tax:
            over = self.set("f1040", "34", total - tax, "Overpaid")
            applied = min(r.payments.apply_to_next_year, over)
            self.set("f1040", "36", applied)
            self.set("f1040", "35a", over - applied, "Refund")
        else:
            owed = self.set("f1040", "37", tax - total, "Amount you owe")
            if owed >= 1000:
                self.s.diag("info", "form_2210", "Balance due of $1,000 or more: check the estimated tax penalty (Form 2210).", "f1040", "38")

    def _schedule_3a(self, l32a: Decimal) -> Decimal:
        r = self.r
        f = "sch_3a"
        self.set(f, "1a", l32a)
        self.set(f, "1b", self.g("f1040", "31"))
        l2 = self.set(f, "2", self.g(f, "1a") - self.g(f, "1b"))
        self.set(f, "3", self.g("f1040", "24a"))
        self.set(f, "4", self.g("sch_2", "20"))
        l5 = self.set(f, "5", self.g(f, "3") - self.g(f, "4"))
        l6 = self.set(f, "6", l2 - l5 if l2 > l5 else Z, "Federal public benefit")
        if l6 <= 0:
            return Z
        self.s.fact(f, "7_wants_benefit", r.want_federal_public_benefit)
        if not r.want_federal_public_benefit:
            return l6
        self.s.fact(f, "8_citizen_or_qualified_alien", r.citizen_or_qualified_alien)
        return self.set(f, "8", Z if r.citizen_or_qualified_alien else l6)

    # ----------------------------------------------------------------- coverage checks
    def _unsupported_checks(self) -> None:
        r = self.r
        unearned = self.g("f1040", "2b") + self.g("f1040", "3b") + pos(self.g("f1040", "7a"))
        kiddie = 2 * D(self.p("us_fed.individual.dependent_standard_deduction")["minimum"])
        if r.taxpayer.can_be_claimed_as_dependent and unearned > kiddie:
            self.s.diag("error", "form_8615_required", "Unearned income of a dependent child may be taxed at the parent's rate "
                                                       "(Form 8615): not yet supported.")
        if any(not p.active_participation and not p.real_estate_professional for p in r.rentals):
            self.s.diag("warning", "passive_rental", "Rentals without active participation need Form 8582.")
