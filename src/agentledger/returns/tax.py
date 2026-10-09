"""Regular income tax: rate schedules, the IRS Tax Table, and the capital gain worksheets.

All dollar thresholds come from the knowledge base. The worksheet line numbers in the
comments follow the 2026 Form 1040 and Schedule D instructions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..calc.engine import D, Ctx
from .sheet import pos, whole

Z = Decimal(0)


def status_key(filing_status: str) -> str:
    """Rate tables are published for four statuses; a qualifying surviving spouse uses the joint table."""
    return "mfj" if filing_status == "qss" else filing_status


def brackets(ctx: Ctx, year: int, filing_status: str) -> list[tuple[Decimal, Decimal]]:
    on = date(year, 12, 31)
    floors = ctx.param("us_fed.individual.tax_brackets", on)[status_key(filing_status)]
    rates = ctx.param("us_fed.individual.tax_rates", on)["rates"]
    if len(floors) != len(rates):
        raise ValueError("bracket floors and rates differ in length")
    return [(D(f), D(r)) for f, r in zip(floors, rates)]


def schedule_tax(ctx: Ctx, income: Decimal, year: int, filing_status: str) -> Decimal:
    """Exact tax from the §1 rate schedule (Tax Computation Worksheet), in cents."""
    income = pos(D(income))
    tax = Z
    bs = brackets(ctx, year, filing_status)
    for i, (floor, rate) in enumerate(bs):
        top = bs[i + 1][0] if i + 1 < len(bs) else None
        if income <= floor:
            break
        span = (min(income, top) if top is not None else income) - floor
        tax += span * rate
    return tax


def table_midpoint(income: Decimal) -> Decimal:
    """Tax Table rows: $5 and $10 rows under $25, $25 rows under $3,000, then $50 rows."""
    if income < 5:
        return Z
    if income < 15:
        return Decimal(10)
    if income < 25:
        return Decimal(20)
    width = Decimal(25) if income < 3000 else Decimal(50)
    return (income // width) * width + width / 2


def regular_tax(ctx: Ctx, income: Decimal, year: int, filing_status: str) -> Decimal:
    """Tax on an amount the way Form 1040 line 16 requires: Tax Table under the ceiling, else the schedule."""
    income = pos(whole(income))
    if income == 0:
        return Z
    ceiling = ctx.dec("us_fed.individual.tax_table_ceiling", date(year, 12, 31))
    if income < ceiling:
        mid = table_midpoint(income)
        return whole(schedule_tax(ctx, mid, year, filing_status)) if mid else Z
    return whole(schedule_tax(ctx, income, year, filing_status))


def _cg_breakpoints(ctx: Ctx, year: int, filing_status: str) -> tuple[Decimal, Decimal]:
    zero_max, fifteen_max = ctx.param("us_fed.individual.capital_gains_breakpoints", date(year, 12, 31))[
        status_key(filing_status)]
    return D(zero_max), D(fifteen_max)


def qdcg_tax(ctx: Ctx, year: int, filing_status: str, taxable_income: Decimal, qualified_dividends: Decimal,
             net_capital_gain: Decimal) -> tuple[Decimal, dict[str, Decimal]]:
    """Qualified Dividends and Capital Gain Tax Worksheet. `net_capital_gain` is the smaller of
    Schedule D lines 15 and 16 (or Form 1040 line 7a when Schedule D is not filed), floored at zero."""
    zero_max, fifteen_max = _cg_breakpoints(ctx, year, filing_status)
    w: dict[str, Decimal] = {}
    w["1"] = l1 = pos(taxable_income)
    w["2"] = l2 = pos(qualified_dividends)
    w["3"] = l3 = pos(net_capital_gain)
    w["4"] = l4 = l2 + l3
    w["5"] = l5 = pos(l1 - l4)
    w["6"] = l6 = zero_max
    w["7"] = l7 = min(l1, l6)
    w["8"] = l8 = min(l5, l7)
    w["9"] = l9 = l7 - l8
    w["10"] = l10 = min(l1, l4)
    w["11"] = l11 = l9
    w["12"] = l12 = l10 - l11
    w["13"] = l13 = fifteen_max
    w["14"] = l14 = min(l1, l13)
    w["15"] = l15 = l5 + l9
    w["16"] = l16 = pos(l14 - l15)
    w["17"] = l17 = min(l12, l16)
    w["18"] = l18 = l17 * Decimal("0.15")
    w["19"] = l19 = l9 + l17
    w["20"] = l20 = l10 - l19
    w["21"] = l21 = l20 * Decimal("0.20")
    w["22"] = l22 = regular_tax(ctx, l5, year, filing_status)
    w["23"] = l23 = l18 + l21 + l22
    w["24"] = l24 = regular_tax(ctx, l1, year, filing_status)
    w["25"] = l25 = whole(min(l23, l24))
    return l25, w


def schedule_d_tax(ctx: Ctx, year: int, filing_status: str, taxable_income: Decimal, qualified_dividends: Decimal,
                   sch_d_15: Decimal, sch_d_16: Decimal, sch_d_18: Decimal, sch_d_19: Decimal,
                   f4952_4g: Decimal = Z, f4952_4e: Decimal = Z, *,
                   unrecaptured_total: Decimal | None = None) -> tuple[Decimal, dict[str, Decimal]]:
    """Schedule D Tax Worksheet (28% rate gain and unrecaptured §1250 gain). `unrecaptured_total` is what line 35 is
    limited to when it is not Schedule D line 19 itself: on the Form 8615 line 9 variant, the total of the Schedule D
    line 19 amounts of the child, the parent and the other children, while `sch_d_19` is the part included on Form 8615
    line 8 (Form 8615 instructions, Using the Schedule D Tax Worksheet for line 9 tax, step 13)."""
    zero_max, fifteen_max = _cg_breakpoints(ctx, year, filing_status)
    line_35_cap = sch_d_19 if unrecaptured_total is None else unrecaptured_total
    bracket_32 = brackets(ctx, year, filing_status)[4][0]
    w: dict[str, Decimal] = {}
    w["1"] = l1 = pos(taxable_income)
    w["2"] = l2 = pos(qualified_dividends)
    w["3"] = l3 = pos(f4952_4g)
    w["4"] = l4 = pos(f4952_4e)
    w["5"] = l5 = pos(l3 - l4)
    w["6"] = l6 = pos(l2 - l5)
    w["7"] = l7 = min(sch_d_15, sch_d_16)
    w["8"] = l8 = min(l3, l4)
    w["9"] = l9 = pos(l7 - l8)
    w["10"] = l10 = l6 + l9
    w["11"] = l11 = pos(sch_d_18) + pos(sch_d_19)
    w["12"] = l12 = min(l9, l11)
    w["13"] = l13 = l10 - l12
    w["14"] = l14 = pos(l1 - l13)
    w["15"] = l15 = zero_max
    w["16"] = l16 = min(l1, l15)
    w["17"] = l17 = min(l14, l16)
    w["18"] = l18 = pos(l1 - l10)
    w["19"] = l19 = min(l1, bracket_32)
    w["20"] = l20 = min(l14, l19)
    w["21"] = l21 = max(l18, l20)
    w["22"] = l22 = l16 - l17
    l31 = l34 = l40 = l43 = Z
    if l1 != l16:
        w["23"] = l23 = min(l1, l13)
        w["24"] = l24 = l22
        w["25"] = l25 = pos(l23 - l24)
        w["26"] = l26 = fifteen_max
        w["27"] = l27 = min(l1, l26)
        w["28"] = l28 = l21 + l22
        w["29"] = l29 = pos(l27 - l28)
        w["30"] = l30 = min(l25, l29)
        w["31"] = l31 = l30 * Decimal("0.15")
        w["32"] = l32 = l24 + l30
        if l1 != l32:
            w["33"] = l33 = l23 - l32
            w["34"] = l34 = l33 * Decimal("0.20")
            l39 = Z
            if sch_d_19 > 0:
                w["35"] = l35 = min(l9, line_35_cap)
                w["36"] = l36 = l10 + l21
                w["37"] = l37 = l1
                w["38"] = l38 = pos(l36 - l37)
                w["39"] = l39 = pos(l35 - l38)
                w["40"] = l40 = l39 * Decimal("0.25")
            if sch_d_18 > 0:
                w["41"] = l41 = l21 + l22 + l30 + l33 + l39
                w["42"] = l42 = l1 - l41
                w["43"] = l43 = l42 * Decimal("0.28")
    w["44"] = l44 = regular_tax(ctx, l21, year, filing_status)
    w["45"] = l45 = l31 + l34 + l40 + l43 + l44
    w["46"] = l46 = regular_tax(ctx, l1, year, filing_status)
    w["47"] = l47 = whole(min(l45, l46))
    return l47, w
