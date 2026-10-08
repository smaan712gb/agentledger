"""Deterministic calculation context.

Calculators never embed statutory numbers. They ask the context for a rule value
on a date, and the context records which value (and which authority) was used.
Every result therefore carries an audit trace, and a regulation change flows into
every calculation the moment the knowledge base is updated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..kb.store import KnowledgeBase


@dataclass(frozen=True)
class TraceItem:
    rule_id: str
    on: str
    value: Any
    effective_from: str
    source: str


@dataclass
class Ctx:
    kb: KnowledgeBase
    trace: list[TraceItem] = field(default_factory=list)

    def param(self, rule_id: str, on: date) -> Any:
        r = self.kb.resolve(rule_id, on)
        item = TraceItem(rule_id, on.isoformat(), r.value, r.effective_from.isoformat(), r.source)
        if item not in self.trace:
            self.trace.append(item)
        return r.value

    def try_param(self, rule_id: str, on: date) -> Any | None:
        if self.kb.try_resolve(rule_id, on) is None:
            return None
        return self.param(rule_id, on)

    def dec(self, rule_id: str, on: date) -> Decimal:
        return D(self.param(rule_id, on))

    def sources(self) -> list[dict[str, Any]]:
        return [t.__dict__ for t in self.trace]


def D(x: Any) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def cents(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def dollars(x: Decimal) -> Decimal:
    return x.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
