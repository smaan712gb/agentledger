"""Federal tax and compliance calculators. All statutory inputs come from the KB."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from .engine import D, Ctx, cents, dollars

FILING_STATUSES = ("single", "mfj", "mfs", "hoh")


def standard_deduction(ctx: Ctx, tax_year: int, filing_status: str) -> Decimal:
    if filing_status not in FILING_STATUSES:
        raise ValueError(f"filing_status must be one of {FILING_STATUSES}")
    table = ctx.param("us_fed.individual.standard_deduction", date(tax_year, 12, 31))
    return D(table[filing_status])


def meals_deduction(ctx: Ctx, amount: Any, on: date) -> Decimal:
    return cents(D(amount) * ctx.dec("us_fed.business.meals_deductible_pct", on))


def mileage_deduction(ctx: Ctx, miles: Any, tax_year: int) -> Decimal:
    return cents(D(miles) * ctx.dec("us_fed.business.standard_mileage_rate", date(tax_year, 12, 31)))


def section_179_allowed(ctx: Ctx, tax_year: int, elected: Any, total_placed_in_service: Any) -> Decimal:
    """Dollar limit reduced dollar-for-dollar above the phase-out threshold (IRC §179(b)(1)-(2)).

    The taxable-income limitation of §179(b)(3) is applied separately by the caller.
    """
    on = date(tax_year, 12, 31)
    limit = ctx.dec("us_fed.depreciation.section_179_limit", on)
    threshold = ctx.dec("us_fed.depreciation.section_179_phaseout_threshold", on)
    reduced = max(Decimal(0), limit - max(Decimal(0), D(total_placed_in_service) - threshold))
    return min(D(elected), reduced)


def bonus_rate(ctx: Ctx, acquired: date, placed_in_service: date) -> Decimal:
    if placed_in_service < acquired:
        raise ValueError("placed_in_service precedes acquisition")
    permanent = ctx.try_param("us_fed.depreciation.bonus_permanent_rate", acquired)
    if permanent is not None:
        return D(permanent)
    return ctx.dec("us_fed.depreciation.bonus_phase_down_rate", placed_in_service)


@dataclass(frozen=True)
class Asset:
    id: str
    cost: Decimal
    acquired: date
    placed_in_service: date
    recovery_years: int  # MACRS class: 3, 5 or 7
    book_life_years: int
    sec179_elected: Decimal = Decimal(0)


def tax_depreciation(ctx: Ctx, asset: Asset, tax_year: int, sec179_allowed: Decimal | None = None) -> Decimal:
    """Year-N tax depreciation: §179, then bonus on the remainder, then MACRS on what is left."""
    first = asset.placed_in_service.year
    if tax_year < first:
        return Decimal(0)
    s179 = asset.sec179_elected if sec179_allowed is None else sec179_allowed
    s179 = min(s179, asset.cost)
    remaining = asset.cost - s179
    bonus = cents(remaining * bonus_rate(ctx, asset.acquired, asset.placed_in_service))
    macrs_basis = remaining - bonus
    table = ctx.param("us_fed.depreciation.macrs_gds_half_year", date(first, 12, 31))
    rates = table[str(asset.recovery_years)]
    idx = tax_year - first
    macrs = cents(macrs_basis * D(rates[idx])) if idx < len(rates) else Decimal(0)
    return (s179 + bonus + macrs) if idx == 0 else macrs


def book_depreciation(asset: Asset, tax_year: int) -> Decimal:
    """Straight-line, half-year convention (book policy, not law: lives in code on purpose)."""
    first = asset.placed_in_service.year
    idx = tax_year - first
    if idx < 0 or idx > asset.book_life_years:
        return Decimal(0)
    annual = asset.cost / asset.book_life_years
    return cents(annual / 2 if idx in (0, asset.book_life_years) else annual)


def form_1099_nec_required(ctx: Ctx, total_paid: Any, calendar_year: int) -> bool:
    return D(total_paid) >= ctx.dec("us_fed.info_returns.form_1099_nec_threshold", date(calendar_year, 12, 31))


def form_1099_k_required(ctx: Ctx, gross: Any, transactions: int, calendar_year: int) -> bool:
    t = ctx.param("us_fed.info_returns.form_1099_k_threshold", date(calendar_year, 12, 31))
    return D(gross) > D(t["gross_amount"]) and transactions > int(t["transactions"])


def boi_report_required(ctx: Ctx, formed_under: str, on: date) -> bool:
    """Whether an entity must file a FinCEN BOI report as of a date."""
    if formed_under == "foreign":
        return True
    return bool(ctx.param("us_fincen.boi.domestic_reporting_required", on))


def fuel_excise(ctx: Ctx, gallons: Any, fuel: str, on: date) -> Decimal:
    rates = ctx.param("us_fed.excise.motor_fuel_rate", on)
    if fuel not in rates:
        raise ValueError(f"fuel must be one of {sorted(rates)}")
    return cents(D(gallons) * D(rates[fuel]))


def corporate_tax(ctx: Ctx, taxable_income: Any, tax_year: int) -> Decimal:
    return dollars(max(Decimal(0), D(taxable_income)) * ctx.dec("us_fed.corporate.income_tax_rate", date(tax_year, 12, 31)))


def salt_cap(ctx: Ctx, tax_year: int) -> Decimal:
    return ctx.dec("us_fed.individual.salt_deduction_cap", date(tax_year, 12, 31))


def _d(s: Any) -> date:
    return s if isinstance(s, date) else date.fromisoformat(str(s))


# Registry used by the API, the Ask agent and golden regression scenarios. Each entry
# adapts plain JSON inputs to the typed calculator.
CALCULATORS: dict[str, tuple[str, Callable[[Ctx, dict[str, Any]], Any]]] = {
    "standard_deduction": ("Standard deduction for a tax year and filing status",
                           lambda c, i: standard_deduction(c, int(i["tax_year"]), i["filing_status"])),
    "meals_deduction": ("Deductible portion of a business meal", lambda c, i: meals_deduction(c, i["amount"], _d(i["date"]))),
    "mileage_deduction": ("Business mileage deduction at the standard rate",
                          lambda c, i: mileage_deduction(c, i["miles"], int(i["tax_year"]))),
    "section_179": ("Section 179 deduction allowed after phase-out",
                    lambda c, i: section_179_allowed(c, int(i["tax_year"]), i["elected"], i["total_placed_in_service"])),
    "bonus_rate": ("Bonus depreciation rate for an asset", lambda c, i: bonus_rate(c, _d(i["acquired"]), _d(i["placed_in_service"]))),
    "form_1099_nec_required": ("Whether a 1099-NEC is required for total payments to a payee",
                               lambda c, i: form_1099_nec_required(c, i["total_paid"], int(i["calendar_year"]))),
    "form_1099_k_required": ("Whether a 1099-K is required", lambda c, i: form_1099_k_required(
        c, i["gross"], int(i["transactions"]), int(i["calendar_year"]))),
    "boi_report_required": ("Whether a FinCEN BOI report is required",
                            lambda c, i: boi_report_required(c, i["formed_under"], _d(i["date"]))),
    "fuel_excise": ("Federal excise tax on gallons of motor fuel", lambda c, i: fuel_excise(c, i["gallons"], i["fuel"], _d(i["date"]))),
    "corporate_tax": ("Federal C-corp income tax", lambda c, i: corporate_tax(c, i["taxable_income"], int(i["tax_year"]))),
    "salt_cap": ("SALT itemized deduction cap", lambda c, i: salt_cap(c, int(i["tax_year"]))),
}


def _form_1040_line(c: Ctx, i: dict[str, Any]) -> Any:
    from ..returns.individual import compute_individual
    from ..returns.model import IndividualReturn

    result = compute_individual(c, IndividualReturn.model_validate(i["return"]))
    return result.line(i.get("form", "f1040"), str(i["line"]))


CALCULATORS["form_1040_line"] = ("One line of a computed individual return (inputs: return, form, line)", _form_1040_line)


def run_calc(ctx: Ctx, name: str, inputs: dict[str, Any]) -> Any:
    if name not in CALCULATORS:
        raise KeyError(f"unknown calculator {name}; available: {sorted(CALCULATORS)}")
    return CALCULATORS[name][1](ctx, inputs)
