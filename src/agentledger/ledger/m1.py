"""Book-to-tax bridge (Form 1120 Schedule M-1) computed live from the ledger + KB."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from ..calc.engine import D, Ctx, cents
from ..calc.federal import book_depreciation, section_179_allowed, tax_depreciation
from . import store

# treatment -> (rule giving the deductible / excludable fraction, M-1 line)
PERMANENT_ADDITIONS = {
    "meals": ("us_fed.business.meals_deductible_pct", "5c"),
    "entertainment": ("us_fed.business.entertainment_deductible_pct", "5c"),
    "fines_penalties": ("us_fed.business.fines_penalties_deductible_pct", "5"),
    "officer_life_insurance": ("us_fed.business.officer_life_insurance_deductible_pct", "5"),
}
PERMANENT_SUBTRACTIONS = {"municipal_interest": ("us_fed.business.municipal_interest_exempt_pct", "7")}

# Rules whose change means an M-1 for the affected years must be recomputed.
M1_RULES = {r for r, _ in PERMANENT_ADDITIONS.values()} | {r for r, _ in PERMANENT_SUBTRACTIONS.values()} | {
    "us_fed.depreciation.section_179_limit",
    "us_fed.depreciation.section_179_phaseout_threshold",
    "us_fed.depreciation.bonus_phase_down_rate",
    "us_fed.depreciation.bonus_permanent_rate",
    "us_fed.depreciation.macrs_gds_half_year",
}


@dataclass
class M1Line:
    line: str
    label: str
    amount: Decimal
    detail: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class M1Result:
    client_id: str
    tax_year: int
    lines: list[M1Line]
    taxable_income: Decimal
    trace: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "tax_year": self.tax_year,
            "lines": [{"line": l.line, "label": l.label, "amount": str(l.amount), "detail": l.detail} for l in self.lines],
            "taxable_income": str(self.taxable_income),
            "trace": self.trace,
        }


def compute(conn: sqlite3.Connection, ctx: Ctx, client_id: str, tax_year: int) -> M1Result:
    start, end = date(tax_year, 1, 1), date(tax_year, 12, 31)
    postings = store.postings_in(conn, client_id, start, end)

    pl = [p for p in postings if p["account_type"] in ("revenue", "expense")]
    net_income = -sum((D(p["amount"]) for p in pl), Decimal(0))

    fit = sum((D(p["amount"]) for p in pl if p["tax_treatment"] == "federal_income_tax"), Decimal(0))

    adds: dict[str, M1Line] = {}
    for p in pl:
        t = p["tax_treatment"]
        if t in PERMANENT_ADDITIONS and p["account_type"] == "expense":
            rule, line = PERMANENT_ADDITIONS[t]
            nondeductible = cents(D(p["amount"]) * (1 - ctx.dec(rule, date.fromisoformat(p["date"]))))
            if nondeductible:
                l = adds.setdefault(line, M1Line(line, _label(line), Decimal(0)))
                l.amount += nondeductible
                l.detail.append({"entry_id": p["entry_id"], "treatment": t, "book": p["amount"],
                                 "nondeductible": str(nondeductible), "rule": rule})

    subs: dict[str, M1Line] = {}
    for p in pl:
        t = p["tax_treatment"]
        if t in PERMANENT_SUBTRACTIONS and p["account_type"] == "revenue":
            rule, line = PERMANENT_SUBTRACTIONS[t]
            excluded = cents(-D(p["amount"]) * ctx.dec(rule, date.fromisoformat(p["date"])))
            l = subs.setdefault(line, M1Line(line, _label(line), Decimal(0)))
            l.amount += excluded
            l.detail.append({"entry_id": p["entry_id"], "treatment": t, "book": p["amount"], "excluded": str(excluded), "rule": rule})

    # Depreciation: book per ledger vs tax per KB. §179 phase-out is applied across the
    # year's placed-in-service total and allocated pro rata to electing assets.
    book_dep = sum((D(p["amount"]) for p in pl if p["tax_treatment"] == "depreciation"), Decimal(0))
    all_assets = [a for a, _ in store.assets(conn, client_id)]
    placed = [a for a in all_assets if a.placed_in_service.year == tax_year]
    total_pis = sum((a.cost for a in placed), Decimal(0))
    elected = sum((a.sec179_elected for a in placed), Decimal(0))
    allowed = section_179_allowed(ctx, tax_year, elected, total_pis) if elected else Decimal(0)
    tax_dep = Decimal(0)
    dep_detail = []
    for a in all_assets:
        share = cents(allowed * a.sec179_elected / elected) if (elected and a in placed) else a.sec179_elected
        t = tax_depreciation(ctx, a, tax_year, share)
        if t or book_depreciation(a, tax_year):
            dep_detail.append({"asset": a.id, "tax": str(t), "book_policy": str(book_depreciation(a, tax_year))})
        tax_dep += t
    dep_diff = tax_dep - book_dep
    if dep_diff < 0:
        adds["5a"] = M1Line("5a", _label("5a"), -dep_diff, dep_detail)
    elif dep_diff > 0:
        subs["8a"] = M1Line("8a", _label("8a"), dep_diff, dep_detail)

    lines = [M1Line("1", _label("1"), net_income)]
    if fit:
        lines.append(M1Line("2", _label("2"), fit))
    lines += [adds[k] for k in sorted(adds)]
    line6 = net_income + fit + sum((l.amount for l in adds.values()), Decimal(0))
    lines.append(M1Line("6", _label("6"), line6))
    lines += [subs[k] for k in sorted(subs)]
    line9 = sum((l.amount for l in subs.values()), Decimal(0))
    lines.append(M1Line("9", _label("9"), line9))
    taxable = line6 - line9
    lines.append(M1Line("10", _label("10"), taxable))
    return M1Result(client_id, tax_year, lines, taxable, ctx.sources())


def _label(line: str) -> str:
    return {
        "1": "Net income (loss) per books",
        "2": "Federal income tax per books",
        "5": "Expenses on books not deducted (fines, life insurance)",
        "5a": "Depreciation: book exceeds tax",
        "5c": "Travel and entertainment / meals not deductible",
        "6": "Add lines 1 through 5",
        "7": "Income on books not included on return (tax-exempt interest)",
        "8a": "Depreciation: tax exceeds book",
        "9": "Add lines 7 and 8",
        "10": "Income (line 28, page 1) = line 6 less line 9",
    }[line]
