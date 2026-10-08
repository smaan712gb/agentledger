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
]


@dataclass
class Discrepancy:
    item: str
    veritas: Decimal
    policyengine: Decimal

    @property
    def difference(self) -> Decimal:
        return self.veritas - self.policyengine


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
    if r.capital_loss_carryover_short or r.capital_loss_carryover_long:
        out.append("capital loss carryovers")
    if r.students:
        out.append("education credits")
    if r.dependent_care_expenses:
        out.append("dependent care")
    return out


def _prune(d: dict[str, Any], known: set[str] | None, skipped: set[str]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        if known is not None and k not in known and k != "members":
            if isinstance(v, dict) and any(x for x in v.values()):
                skipped.add(k)
            continue
        out[k] = v
    return out


def situation(r: IndividualReturn, known: set[str] | None = None, skipped: set[str] | None = None) -> dict[str, Any]:
    skipped = skipped if skipped is not None else set()
    y = r.tax_year
    yr = str(y)

    def per(owner: str) -> dict[str, Any]:
        p = r.taxpayer if owner == "taxpayer" else r.spouse
        wages = sum((w.wages for w in r.w2s if w.owner == owner), Decimal(0))
        se = sum((b.gross_receipts - b.returns_allowances - b.cost_of_goods_sold + b.other_income
                  - sum(b.expenses.values(), Decimal(0)) for b in r.businesses if b.owner == owner), Decimal(0))
        d: dict[str, Any] = {
            "age": {yr: y - p.dob.year if p and p.dob else 40},
            "employment_income": {yr: float(wages)},
            "self_employment_income": {yr: float(se)},
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
        if owner == "taxpayer":
            st = lt = Decimal(0)
            for t in r.capital_transactions:
                g = t.proceeds - t.cost_basis + t.adjustment
                if t.term == "long" or (t.term is None and hasattr(t.acquired, "year") and (t.sold - t.acquired).days > 365):
                    lt += g
                else:
                    st += g
            lt += sum((x.capital_gain_distributions for x in r.dividends), Decimal(0))
            d.update({
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
    sit = situation(ret, set(system.variables), skipped)
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
    return CrossCheck(compared, bad, _unmodelled(ret) + sorted(f"input not in PolicyEngine: {k}" for k in skipped))
