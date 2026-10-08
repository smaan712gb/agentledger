"""A return under construction: form lines, their explanations, and diagnostics.

Lines are kept in whole dollars (amounts of 50 cents or more round up), the way the
IRS expects them on a return. Every line can carry a short explanation so a preparer
can see how it was derived without reading code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]


def whole(x: Any) -> Decimal:
    d = x if isinstance(x, Decimal) else Decimal(str(x))
    return d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def pos(x: Decimal) -> Decimal:
    return x if x > 0 else Decimal(0)


@dataclass
class Diagnostic:
    severity: Severity
    code: str
    message: str
    form: str | None = None
    line: str | None = None


@dataclass
class Sheets:
    forms: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    notes: dict[str, dict[str, str]] = field(default_factory=dict)
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)  # non-numeric entries: checkboxes, counts, names
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def set(self, form: str, line: str, value: Any, note: str = "") -> Decimal:
        v = whole(value)
        self.forms.setdefault(form, {})[line] = v
        if note:
            self.notes.setdefault(form, {})[line] = note
        return v

    def fact(self, form: str, key: str, value: Any) -> None:
        self.facts.setdefault(form, {})[key] = value

    def get(self, form: str, line: str) -> Decimal:
        return self.forms.get(form, {}).get(line, Decimal(0))

    def has(self, form: str) -> bool:
        return any(v != 0 for v in self.forms.get(form, {}).values()) or bool(self.facts.get(form))

    def diag(self, severity: Severity, code: str, message: str, form: str | None = None, line: str | None = None) -> None:
        self.diagnostics.append(Diagnostic(severity, code, message, form, line))

    def drop_empty(self) -> None:
        for name in [f for f in self.forms if not self.has(f)]:
            self.forms.pop(name, None)
            self.notes.pop(name, None)
