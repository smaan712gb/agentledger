"""Independent cross-check of a computed return against PolicyEngine US.

PolicyEngine is an open-source microsimulation model maintained separately from AgentLedger.
Agreement between two independently built engines is evidence the computation is right.
A disagreement is a finding for a preparer (or the Researcher agent) to resolve; it never
changes the return. PolicyEngine is optional: install it with `pip install policyengine-us`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .individual import Result
from .model import IndividualReturn

TOLERANCE = Decimal(5)  # whole-dollar rounding of intermediate lines
# The IRS requires the Tax Table and the EIC Table, which figure amounts at the midpoint of
# $25/$50 rows; PolicyEngine uses exact formulas. The widest possible gap is half a row times
# the steepest rate on that row (24% for the Tax Table, 45% EIC phase-in), plus rounding.
TABLE_METHOD_TOLERANCE = {"tax before credits incl. AMT": Decimal(7), "earned income credit": Decimal(12)}

# (label, PolicyEngine tax-unit variable, function of our result)
COMPARISONS: list[tuple[str, str, Any]] = [
    ("adjusted gross income", "adjusted_gross_income", lambda r: r.line("f1040", "11a")),
    ("taxable income", "taxable_income", lambda r: r.line("f1040", "15")),
    ("qualified business income deduction", "qualified_business_income_deduction", lambda r: r.line("f1040", "13b")),
    ("tax before credits incl. AMT", "income_tax_before_credits", lambda r: r.line("f1040", "18")),
    ("alternative minimum tax", "alternative_minimum_tax", lambda r: r.line("sch_2", "2")),
    ("self-employment tax", "self_employment_tax", lambda r: r.line("sch_2", "4")),
    ("earned income credit", "eitc", lambda r: r.line("f1040", "27a")),
    ("refundable child tax credit", "refundable_ctc", lambda r: r.line("f1040", "28")),
    ("qualified tips deduction", "tip_income_deduction", lambda r: r.line("sch_1a", "15")),
    ("qualified overtime deduction", "overtime_income_deduction", lambda r: r.line("sch_1a", "27")),
    ("vehicle loan interest deduction", "auto_loan_interest_deduction", lambda r: r.line("sch_1a", "36")),
    ("senior deduction", "additional_senior_deduction", lambda r: r.line("sch_1a", "43")),
    ("net investment income tax", "net_investment_income_tax", lambda r: r.line("sch_2", "6")),
    ("capital loss deduction", "limited_capital_loss", lambda r: -r.line("sch_d", "21")),
    ("saver's credit", "savers_credit", lambda r: r.line("sch_3", "4")),
    # PolicyEngine deducts traditional IRA contributions up to its combined limit; the comparison holds when nobody is an
    # active participant (no §219(g) phase-out) and the contributions are within its limit parameter.
    ("IRA deduction", "traditional_ira_contributions", lambda r: r.line("sch_1", "20")),
]
# Items PolicyEngine models only as a ceiling: ours must not exceed theirs. Its foreign_tax_credit is min(the foreign
# taxes it is given, income tax before credits), without the §904 limitation, the separate categories or carryovers.
UPPER_BOUNDS: list[tuple[str, str, Any]] = [
    ("foreign tax credit (upper bound)", "foreign_tax_credit", lambda r: r.line("sch_3", "1")),
]
# W-2 box 12 deferral codes PolicyEngine takes as inputs, and whether the deferral was pre-tax. Box 1 wages exclude a
# pre-tax deferral while PolicyEngine subtracts its retirement inputs from employment_income itself, so the deferral is
# added back to employment_income; a designated Roth contribution is in box 1 already.
PE_DEFERRALS = {"D": ("traditional_401k_contributions", True), "E": ("traditional_403b_contributions", True),
                "AA": ("roth_401k_contributions", False), "BB": ("roth_403b_contributions", False)}


@dataclass
class Discrepancy:
    item: str
    agentledger: Decimal
    policyengine: Decimal

    @property
    def difference(self) -> Decimal:
        return self.agentledger - self.policyengine


@dataclass
class CrossCheck:
    compared: dict[str, tuple[Decimal, Decimal]]
    discrepancies: list[Discrepancy]
    unmodelled: list[str]

    @property
    def agrees(self) -> bool:
        return not self.discrepancies


def _unmodelled(r: IndividualReturn) -> list[str]:
    """Inputs the adapter cannot express to PolicyEngine; comparisons are still run but flagged."""
    out = []
    if r.rentals or r.k1s:
        out.append("Schedule E activity")
    if r.amt_adjustments:
        out.append("AMT adjustments")
    if r.students:
        out.append("education credits")
    if r.dependent_care_expenses:
        out.append("dependent care")
    codes = {c.upper() for w in r.w2s for c in w.box12}
    if codes & {"F", "G", "H", "S", "EE"}:
        out.append("saver's credit deferrals with W-2 codes F, G, H, S or EE (SEP, 457(b), 501(c)(18), SIMPLE)")
    if any(x.voluntary_after_tax_contributions or x.able_contributions or x.testing_period_distributions for x in r.retirement_savings):
        out.append("saver's credit stated facts (voluntary after-tax or ABLE contributions, testing-period distributions)")
    saving = bool(r.retirement_savings) or any(a.ira_contributions or a.roth_contributions for a in r.ira_accounts) or bool(
        codes & {"D", "E", "F", "G", "H", "S", "AA", "BB", "EE"})
    if saving and r.retirement:
        out.append("saver's credit line 4 (PolicyEngine nets only this year's IRA and plan distributions)")
    trad = {a.owner: sum((x.ira_contributions or Decimal(0) for x in r.ira_accounts if x.owner == a.owner), Decimal(0)) for a in r.ira_accounts}
    if any(v > 0 for v in trad.values()):
        covered = any(w.retirement_plan for w in r.w2s) or any(x.covered_by_employer_plan for x in r.ira_facts) or any(
            (a.sep_contributions or a.simple_contributions) for a in r.ira_accounts)
        if covered:
            out.append("IRA deduction of an active participant (PolicyEngine deducts the contributions without the §219(g) phase-out)")
        if any(v > 7000 for v in trad.values()):
            out.append("traditional IRA contributions above $7,000 (PolicyEngine's IRA limit parameter is not yet updated for 2026)")
        if any(x.nondeductible_election for x in r.ira_facts):
            out.append("nondeductible IRA contributions elected (PolicyEngine deducts every traditional IRA contribution)")
    if r.hsa_contributions or r.hsa_facts or any(c.upper() == "W" for w in r.w2s for c in w.box12):
        out.append("HSA deduction (PolicyEngine takes the §223 deduction as an input, fed from Form 8889 line 13: only its effects on "
                   "AGI, the credits and taxable Social Security are cross-checked)")
    if r.hsa_distributions:
        out.append("taxable HSA distributions and the 20% additional tax (Form 8889 Part II: not in PolicyEngine)")
    py = r.prior_year
    basis = py is not None and any(getattr(py, k) for k in ("traditional_ira_basis", "spouse_traditional_ira_basis", "roth_ira_basis",
                                                             "spouse_roth_ira_basis", "roth_conversion_basis", "spouse_roth_conversion_basis"))
    if basis or any(set(x.distribution_code.upper()) & {"J", "Q", "T"} for x in r.retirement) or any(a.roth_conversion for a in r.ira_accounts):
        out.append("Form 8606 (PolicyEngine takes taxable IRA distributions as an input, fed from Form 1040 line 4b)")
    if _foreign_taxes(r):
        out.append("foreign tax credit limitation (PolicyEngine credits min(foreign taxes, tax before credits), without the §904 "
                   "limitation, the separate categories or carryovers: compared as an upper bound only)")
    if r.dispositions or r.business_use_recaptures or any(k.net_section_1231_gain or k.unrecaptured_1250_gain for k in r.k1s):
        out.append("Form 4797 (PolicyEngine takes other_net_gain and the capital gain as inputs: fed from Part II line 18b, Part I's gain to "
                   "Schedule D line 11 and Schedule D line 19; the section 1231 netting, the recapture, the §1231(c) lookback, the Part IV "
                   "recapture and Form 8960 line 5b are not cross-checked, only AGI and the tax on the amounts)")
    return out


def _foreign_taxes(r: IndividualReturn) -> Decimal:
    return (sum((i.foreign_tax_paid for i in r.interest), Decimal(0)) + sum((d.foreign_tax_paid for d in r.dividends), Decimal(0))
            + sum((k.foreign_tax_paid for k in r.k1s), Decimal(0)))


def _carryovers(r: IndividualReturn) -> tuple[Decimal, Decimal]:
    """Capital loss carryovers into the year, as the engine resolves them (prior_year first, then the legacy inputs)."""
    py = r.prior_year
    short = py.capital_loss_carryover_short if py and py.capital_loss_carryover_short is not None else r.capital_loss_carryover_short
    long = py.capital_loss_carryover_long if py and py.capital_loss_carryover_long is not None else r.capital_loss_carryover_long
    return short, long


def _prune(d: dict[str, Any], known: set[str] | None, skipped: set[str]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        if known is not None and k not in known and k != "members":
            if isinstance(v, dict) and any(x for x in v.values()):
                skipped.add(k)
            continue
        out[k] = v
    return out


def situation(r: IndividualReturn, known: set[str] | None = None, skipped: set[str] | None = None, *,
              ours: Result | None = None) -> dict[str, Any]:
    """PolicyEngine's situation for the return. `ours`, when given, supplies the two amounts PolicyEngine takes as inputs
    rather than figuring: the HSA deduction (Form 8889 line 13) and the taxable IRA distributions (Form 1040 line 4b,
    after Form 8606), so that their downstream effects are cross-checked while the items themselves are flagged in
    _unmodelled."""
    skipped = skipped if skipped is not None else set()
    y = r.tax_year
    yr = str(y)
    ira_taxable = ours.line("f1040", "4b") if ours is not None else None

    def per(owner: str) -> dict[str, Any]:
        p = r.taxpayer if owner == "taxpayer" else r.spouse
        wages = sum((w.wages for w in r.w2s if w.owner == owner), Decimal(0))
        se = sum((b.gross_receipts - b.returns_allowances - b.cost_of_goods_sold + b.other_income
                  - sum(b.expenses.values(), Decimal(0)) for b in r.businesses if b.owner == owner), Decimal(0))
        deferrals: dict[str, Decimal] = {}
        pretax = Decimal(0)
        for w in r.w2s:
            if w.owner != owner:
                continue
            for code, amt in w.box12.items():
                if code.upper() in PE_DEFERRALS:
                    var, pre = PE_DEFERRALS[code.upper()]
                    deferrals[var] = deferrals.get(var, Decimal(0)) + amt
                    pretax += amt if pre else Decimal(0)
        d: dict[str, Any] = {
            "age": {yr: y - p.dob.year if p and p.dob else 40},
            "employment_income": {yr: float(wages + pretax)},
            "self_employment_income": {yr: float(se)},
            **{var: {yr: float(amt)} for var, amt in deferrals.items()},
            "traditional_ira_contributions": {yr: float(sum((a.ira_contributions or Decimal(0) for a in r.ira_accounts
                                                             if a.owner == owner), Decimal(0)))},
            "roth_ira_contributions": {yr: float(sum((a.roth_contributions or Decimal(0) for a in r.ira_accounts
                                                      if a.owner == owner), Decimal(0)))},
            "is_full_time_student": {yr: bool(p and p.full_time_student)},
            "claimed_as_dependent_on_another_return": {yr: bool(p and p.can_be_claimed_as_dependent)},
            "tip_income": {yr: float(sum((w.tips() for w in r.w2s if w.owner == owner), Decimal(0)))},
            "fsla_overtime_premium": {yr: float(sum((w.overtime() for w in r.w2s if w.owner == owner), Decimal(0)))},
            "is_blind": {yr: bool(p and p.blind)},
            "treasury_tipped_occupation_code": {yr: next((w.tipped_occupation_code for w in r.w2s
                                                          if w.owner == owner and w.tipped_occupation_code), 0)},
            "social_security_retirement": {yr: float(sum((s.net_benefits for s in r.social_security if s.owner == owner), Decimal(0)))},
            "taxable_pension_income": {yr: float(sum((x.taxable_amount or x.gross_distribution for x in r.retirement
                                                       if x.owner == owner and not x.ira_sep_simple), Decimal(0)))},
            "taxable_ira_distributions": {yr: float(sum((x.taxable_amount or x.gross_distribution for x in r.retirement
                                                          if x.owner == owner and x.ira_sep_simple), Decimal(0)))},
        }
        if ira_taxable is not None:                       # the engine's line 4b (Form 8606 applied), all on the taxpayer
            d["taxable_ira_distributions"] = {yr: float(ira_taxable) if owner == "taxpayer" else 0.0}
        if owner == "taxpayer":
            st = lt = Decimal(0)
            for t in r.capital_transactions:
                g = t.proceeds - t.cost_basis + t.adjustment
                if t.term == "long" or (t.term is None and hasattr(t.acquired, "year") and (t.sold - t.acquired).days > 365):
                    lt += g
                else:
                    st += g
            lt += sum((x.capital_gain_distributions for x in r.dividends), Decimal(0))
            # Carryovers from the prior year net against this year's gains the way Schedule D lines 6 and 14 do;
            # PolicyEngine's long_term_capital_loss_carryover is a memo item for the 28% rate gain only.
            cf_short, cf_long = _carryovers(r)
            st -= cf_short
            lt -= cf_long
            if ours is not None:                           # Form 4797 Part I: the net section 1231 gain Schedule D line 11 takes
                lt += ours.line("sch_d", "11")
            d.update({
                "long_term_capital_loss_carryover": {yr: float(cf_long)},
                "taxable_interest_income": {yr: float(sum((i.interest + i.us_savings_bond_interest for i in r.interest), Decimal(0)))},
                "tax_exempt_interest_income": {yr: float(sum((i.tax_exempt_interest for i in r.interest), Decimal(0)))},
                "qualified_dividend_income": {yr: float(sum((x.qualified for x in r.dividends), Decimal(0)))},
                "non_qualified_dividend_income": {yr: float(sum((x.ordinary - x.qualified for x in r.dividends), Decimal(0)))},
                "short_term_capital_gains": {yr: float(st)},
                "long_term_capital_gains": {yr: float(lt)},
                "real_estate_taxes": {yr: float(r.itemized.real_estate_tax)},
                "charitable_cash_donations": {yr: float(r.itemized.charity_cash)},
                "medical_out_of_pocket_expenses": {yr: float(r.itemized.medical)},
                "qualified_passenger_vehicle_loan_interest": {yr: float(sum((c.interest_paid for c in r.car_loans if c.qualifies), Decimal(0)))},
            })
        return d

    people = {"you": _prune({**per("taxpayer"), "is_tax_unit_head": {yr: True}}, known, skipped)}
    members = ["you"]
    if r.filing_status == "mfj" and r.spouse:
        people["spouse"] = _prune({**per("spouse"), "is_tax_unit_spouse": {yr: True}}, known, skipped)
        members.append("spouse")
    for i, d in enumerate(r.dependents):
        key = f"dep{i}"
        people[key] = {"age": {yr: y - d.dob.year}, "is_tax_unit_dependent": {yr: True},
                       "is_full_time_student": {yr: d.full_time_student}, "is_disabled": {yr: d.permanently_disabled}}
        members.append(key)
    tax_unit: dict[str, Any] = {"members": members}
    if r.itemized.state_local_income_tax:
        tax_unit["state_and_local_sales_or_income_tax"] = {yr: float(r.itemized.state_local_income_tax)}
    if r.itemized.mortgage_interest_1098:
        tax_unit["deductible_mortgage_interest"] = {yr: float(r.itemized.mortgage_interest_1098)}
    if ours is not None and ours.line("sch_1", "13") > 0:
        tax_unit["health_savings_account_ald"] = {yr: float(ours.line("sch_1", "13"))}
    if ours is not None and ours.line("sch_1", "4"):             # Form 4797 Part II, line 18b: PolicyEngine's other_net_gain input
        tax_unit["other_net_gain"] = {yr: float(ours.line("sch_1", "4"))}
    if ours is not None and ours.line("sch_d", "19") > 0:        # Schedule D line 19, as the worksheet reduced it
        tax_unit["unrecaptured_section_1250_gain"] = {yr: float(ours.line("sch_d", "19"))}
    if _foreign_taxes(r):
        tax_unit["foreign_tax_credit_potential"] = {yr: float(_foreign_taxes(r))}
    return {
        "people": people,
        "tax_units": {"tu": _prune(tax_unit, known, skipped)},
        "families": {"f": {"members": members}},
        "spm_units": {"s": {"members": members}},
        "marital_units": {"m": {"members": members[:2] if r.filing_status == "mfj" else ["you"]},
                          **{f"m{i}": {"members": [m]} for i, m in enumerate(members[2 if r.filing_status == "mfj" else 1:])}},
        # Texas has no income tax, so PolicyEngine's state tax estimate cannot leak into SALT.
        "households": {"h": {"members": members, "state_name": {yr: "TX"}}},
    }


GROUPS = {"tax_unit": ("tax_units", "tu"), "household": ("households", "h"), "family": ("families", "f"),
          "spm_unit": ("spm_units", "s")}


def _route(sit: dict[str, Any], variables: dict[str, Any]) -> None:
    """Move each input onto the entity PolicyEngine defines it for (our situation has one group of each kind)."""
    moves: list[tuple[str, str, Any]] = []
    for pid, person in sit["people"].items():
        for k in list(person):
            entity = variables[k].entity.key
            if entity != "person" and entity in GROUPS:
                moves.append((entity, k, person.pop(k)))
    for gkey, (plural, gid) in GROUPS.items():
        group = sit[plural][gid]
        for k in [k for k in group if k != "members"]:
            entity = variables[k].entity.key
            if entity != gkey and entity in GROUPS:
                moves.append((entity, k, group.pop(k)))
    for entity, k, v in moves:
        plural, gid = GROUPS[entity]
        target = sit[plural][gid]
        if k in target:
            for year, val in v.items():
                target[k][year] = target[k].get(year, 0) + val
        else:
            target[k] = v


def crosscheck(ret: IndividualReturn, ours: Result, tolerance: Decimal = TOLERANCE) -> CrossCheck:
    from policyengine_us import Simulation
    from policyengine_us.system import system

    skipped: set[str] = set()
    sit = situation(ret, set(system.variables), skipped, ours=ours)
    _route(sit, system.variables)
    sim = Simulation(situation=sit)
    compared, bad = {}, []
    for label, var, fn in COMPARISONS:
        try:
            theirs = Decimal(str(round(float(sim.calculate(var, ret.tax_year).sum()), 2)))
        except Exception:  # variable missing in this PolicyEngine release
            continue
        mine = Decimal(fn(ours))
        compared[label] = (mine, theirs)
        if abs(mine - theirs) > max(tolerance, TABLE_METHOD_TOLERANCE.get(label, Decimal(0))):
            bad.append(Discrepancy(label, mine, theirs))
    for label, var, fn in UPPER_BOUNDS:
        try:
            theirs = Decimal(str(round(float(sim.calculate(var, ret.tax_year).sum()), 2)))
        except Exception:
            continue
        mine = Decimal(fn(ours))
        compared[label] = (mine, theirs)
        if mine - theirs > tolerance:
            bad.append(Discrepancy(label, mine, theirs))
    return CrossCheck(compared, bad, _unmodelled(ret) + sorted(f"input not in PolicyEngine: {k}" for k in skipped))
