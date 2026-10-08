"""Form 1040 and its schedules, computed line by line.

Line numbers follow the 2026 forms (IRS early-release drafts). Every statutory amount is
resolved from the knowledge base for the tax year, so the computation is traced to its
authorities. Anything the engine does not support is raised as a diagnostic rather than
silently skipped: an `error` diagnostic blocks filing, a `warning` needs preparer review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from ..calc.engine import D, Ctx
from . import tax as T
from .model import (SCHEDULE_C_LINES, Business, CapitalTransaction, Dependent, IndividualReturn, Owner, Person)
from .sheet import Sheets, pos, whole

SUPPORTED_YEARS = {2026}
Z = Decimal(0)
QC_RELATIONSHIPS = {"son", "daughter", "stepchild", "foster_child", "brother", "sister", "half_brother", "half_sister",
                    "stepbrother", "stepsister", "grandchild", "niece", "nephew"}
QR_RELATIONSHIPS = QC_RELATIONSHIPS | {"parent", "grandparent", "aunt", "uncle", "in_law"}


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
class Result:
    tax_year: int
    filing_status: str
    sheets: Sheets
    sources: list[dict[str, Any]] = field(default_factory=list)

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
            "sources": self.sources,
        }


def compute_individual(ctx: Ctx, r: IndividualReturn) -> Result:
    return _Individual(ctx, r).compute()


class _Individual:
    def __init__(self, ctx: Ctx, r: IndividualReturn):
        self.ctx, self.r = ctx, r
        self.y = r.tax_year
        self.on = date(r.tax_year, 12, 31)
        self.fs = r.filing_status
        self.joint = r.filing_status == "mfj"
        self.mfs = r.filing_status == "mfs"
        self.s = Sheets()
        self.deps: list[DependentStatus] = []
        self.se: dict[Owner, dict[str, Decimal]] = {}
        self.biz_net: list[tuple[Business, Decimal]] = []

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
        self._retirement()
        self._schedule_c()
        self._schedule_se()
        self._schedule_d()
        self._schedule_e_and_schedule_1()
        self._social_security()
        self._agi()
        self._deductions()
        self._tax()
        self._amt()
        self._credits()
        self._other_taxes()
        self._payments()
        self._unsupported_checks()
        self.s.drop_empty()
        return Result(self.y, self.fs, self.s, self.ctx.sources())

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

    def _retirement(self) -> None:
        ira_gross = ira_taxable = pen_gross = pen_taxable = early_tax = Z
        for d in self.r.retirement:
            if d.taxable_amount is None:
                taxable = d.gross_distribution
                self.s.diag("warning", "1099r_taxable_not_determined",
                            f"1099-R from {d.payer or 'payer'}: taxable amount not determined; the full distribution is "
                            "treated as taxable. Figure basis (Form 8606 or the Simplified Method).", "f1040", "4b")
            else:
                taxable = d.taxable_amount
            taxable = pos(taxable - d.rollover_amount - (d.qcd_amount if d.ira_sep_simple else Z))
            if d.ira_sep_simple:
                ira_gross += d.gross_distribution
                ira_taxable += taxable
            else:
                pen_gross += d.gross_distribution
                pen_taxable += taxable
            code = d.distribution_code.upper()
            if "1" in code or "S" in code:
                rate = Decimal("0.25") if "S" in code else Decimal("0.10")
                early_tax += pos(taxable - d.early_distribution_exception) * rate
                if d.early_distribution_exception > 0:
                    self.s.diag("info", "form_5329_exception", "An early-distribution exception was claimed: Form 5329 is required.")
        self.set("f1040", "4a", ira_gross)
        self.set("f1040", "4b", ira_taxable)
        self.set("f1040", "5a", pen_gross)
        self.set("f1040", "5b", pen_taxable)
        self._early_distribution_tax = early_tax

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
            self.set(f, "6", b.other_income)
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

    def _schedule_d(self) -> None:
        r = self.r
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
        self.set("sch_d", "5", k1_st)
        self.set("sch_d", "6", -r.capital_loss_carryover_short, "Short-term capital loss carryover")
        st = sum((self.g("sch_d", f"{line}h") for line in ("1a", "1b", "2", "3")), Z) + self.g("sch_d", "5") + self.g("sch_d", "6")
        self.set("sch_d", "7", st, "Net short-term capital gain or (loss)")
        self.set("sch_d", "12", k1_lt)
        self.set("sch_d", "13", cgd, "Capital gain distributions")
        self.set("sch_d", "14", -r.capital_loss_carryover_long, "Long-term capital loss carryover")
        lt = sum((self.g("sch_d", f"{line}h") for line in ("8a", "8b", "9", "10")), Z) + sum(
            (self.g("sch_d", x) for x in ("12", "13", "14")), Z)
        self.set("sch_d", "15", lt, "Net long-term capital gain or (loss)")
        l16 = self.set("sch_d", "16", st + lt)
        self._sch_d_18 = self._sch_d_19 = Z
        if l16 > 0 and lt > 0:
            rate28 = pos(collectibles + sum((d.collectibles_gain for d in r.dividends), Z))
            unrecap = pos(sum((d.unrecaptured_1250_gain for d in r.dividends), Z))
            self._sch_d_18 = self.set("sch_d", "18", rate28, "28% rate gain")
            self._sch_d_19 = self.set("sch_d", "19", unrecap, "Unrecaptured section 1250 gain")
            seven_a = l16
        elif l16 < 0:
            lim = self.p("us_fed.individual.capital_loss_limit")
            cap = D(lim["mfs"] if self.mfs else lim["other"])
            seven_a = self.set("sch_d", "21", max(l16, -cap), "Capital loss deduction limited (IRC §1211(b))")
        else:
            seven_a = l16
        only_distributions = not r.capital_transactions and not k1_st and not k1_lt and not r.capital_loss_carryover_short \
            and not r.capital_loss_carryover_long and not self._sch_d_18 and not self._sch_d_19
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
            magi = (self.g("f1040", "1z") + self.g("f1040", "2b") + self.g("f1040", "3b") + self.g("f1040", "4b")
                    + self.g("f1040", "5b") + self.g("f1040", "7a") + self.g("sch_1", "3") + nonpassive_rentals
                    + self._k1_nonpassive_income() + self._other_sch1_income() - (self._adj_pre - self.r.adjustments.ira_deduction))
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
        self.set("sch_1", "5", self.g("sch_e", "41"), "Schedule E, line 41")
        self.set("sch_1", "7", sum((u.amount for u in r.unemployment), Z), "Unemployment compensation (1099-G box 1)")
        other = Z
        for key, amount in r.other_income.items():
            line = key if key.startswith("8") and len(key) == 2 else "8z"
            sign = -1 if line in ("8a", "8d", "8s") else 1
            other += self.set("sch_1", line, self.g("sch_1", line) + sign * abs(amount))
        self.set("sch_1", "9", other, "Total other income")
        self.set("sch_1", "10", sum((self.g("sch_1", x) for x in ("1", "2a", "3", "4", "5", "6", "7", "9")), Z),
                 "Additional income")
        self.set("f1040", "8", self.g("sch_1", "10"))

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
        self.set("sch_1", "13", a.hsa_deduction, "Form 8889")
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
        if a.ira_deduction:
            self.s.diag("info", "ira_deduction_as_entered",
                        "IRA deduction taken as entered; confirm the IRA Deduction Worksheet (coverage and MAGI limits).", "sch_1", "20")
        self.set("sch_1", "20", a.ira_deduction)
        self.set("sch_1", "24z", a.other)
        self.set("sch_1", "25", a.other)
        self._adj_pre = sum((self.g("sch_1", x) for x in ("11", "12", "13", "14", "15", "16", "17", "18", "19a", "20", "23", "25")), Z)

    def _social_security(self) -> None:
        r = self.r
        benefits = sum((s.net_benefits for s in r.social_security), Z)
        self.set("f1040", "6a", benefits, "SSA-1099 / RRB-1099 box 5")
        if benefits <= 0:
            self.set("f1040", "6b", Z)
            return
        ss = self.p("us_fed.individual.social_security_taxability")
        w: dict[str, Decimal] = {}
        w["1"] = benefits
        w["2"] = benefits * Decimal("0.5")
        w["3"] = sum((self.g("f1040", x) for x in ("1z", "2b", "3b", "4b", "5b", "7a", "8")), Z)
        w["4"] = self.g("f1040", "2a")
        w["5"] = w["2"] + w["3"] + w["4"]
        w["6"] = self._adj_pre
        taxable = Z
        if w["6"] < w["5"]:
            w["7"] = w["5"] - w["6"]
            if self.mfs and not r.mfs_lived_apart_all_year:
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
        self.s.fact("ws_social_security", "lines", {k: str(whole(v)) for k, v in w.items()})
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
        r, it, agi = self.r, self.r.itemized, self.agi
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
                self.s.diag("error", "car_loan_vin", f"Vehicle loan interest requires the 17-character VIN (IRC §163(h)(4)(B)(iii)).", f, "28")
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
        for b, net in self.biz_net:
            share = (pos(net) / total_profit) if total_profit > 0 else Z
            qbi = net - (self._se_half + a.self_employed_health_insurance + a.self_employed_retirement) * share
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
        self.set("f1040", "15", pos(self.agi - self.g("f1040", "14")), "Taxable income")

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
        ftc = self._ftc_amount()
        self.set(f, "8", ftc if tmt > 0 else Z, "AMT foreign tax credit (simplified limitation election)")
        l9 = self.set(f, "9", pos(tmt - self.g(f, "8")), "Tentative minimum tax")
        l10 = self.set(f, "10", pos(self.g("f1040", "16") + self.g("sch_2", "1z") - ftc))
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

    # ----------------------------------------------------------------- credits
    def _ftc_amount(self) -> Decimal:
        if hasattr(self, "_ftc"):
            return self._ftc
        paid = sum((i.foreign_tax_paid for i in self.r.interest), Z) + sum((d.foreign_tax_paid for d in self.r.dividends), Z)
        self._ftc = Z
        if paid > 0:
            lim = self.p("us_fed.individual.foreign_tax_credit_simplified_limit")
            if paid <= D(lim["mfj"] if self.fs == "mfj" else lim["other"]):
                self._ftc = min(paid, self.g("f1040", "16"))
            else:
                self.s.diag("error", "form_1116_required", f"Foreign taxes of {whole(paid)} exceed the de minimis limit: "
                                                            "Form 1116 is required and is not yet supported.", "sch_3", "1")
        return self._ftc

    def _credits(self) -> None:
        r = self.r
        l18 = self.g("f1040", "18")
        ftc = min(self._ftc_amount(), l18)
        self.set("sch_3", "1", ftc, "Foreign tax credit")
        remaining = l18 - ftc
        dc = min(self._form_2441(), remaining)
        self.set("sch_3", "2", dc, "Form 2441, line 11")
        remaining -= dc
        edu_nonref, edu_ref = self._form_8863(remaining)
        self.set("sch_3", "3", edu_nonref, "Form 8863, line 19")
        remaining -= edu_nonref
        if r.retirement_savings_contributions:
            self.s.diag("warning", "form_8880_unsupported", "Retirement savings contributions credit (Form 8880) is not yet computed.", "sch_3", "4")
        self.set("sch_3", "4", Z)
        self.set("sch_3", "7", Z)
        self.set("sch_3", "8", sum((self.g("sch_3", x) for x in ("1", "2", "3", "4", "5a", "7")), Z))
        ctc, self._ctc_unused = self._schedule_8812_part_i(l18 - self.g("sch_3", "8"))
        self.set("f1040", "19", ctc, "Schedule 8812, line 14")
        self.set("f1040", "20", self.g("sch_3", "8"))
        self.set("f1040", "21", self.g("f1040", "19") + self.g("f1040", "20"))
        self.set("f1040", "22", pos(l18 - self.g("f1040", "21")))
        self._aotc_refundable = edu_ref

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
        self.set("sch_2", "14", Z)
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
        self.set(f, "5a", self.g("f1040", "7a"))
        self.set(f, "5d", self.g(f, "5a"))
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
        if r.capital_loss_carryover_short or r.capital_loss_carryover_long or self.g("sch_d", "21"):
            self.s.diag("info", "capital_loss_carryover", "Figure the capital loss carryover to 2027 with the Schedule D worksheet.")
