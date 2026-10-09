"""Regulation-as-code data model.

Every number the platform uses for tax or compliance logic lives in a Rule: an
effective-dated series of values, each carrying the authority it came from. Code
never hardcodes a statutory figure; it resolves a rule for a date. That is what
lets the regulatory agent change behaviour by editing data, with a reviewable
diff, instead of shipping code.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ValueType = Literal["money", "rate", "number", "integer", "boolean", "text", "table"]


class Provenance(BaseModel):
    source: str  # human citation, e.g. "Rev. Proc. 2025-32 §4.24"
    url: str | None = None
    adopted_via: str | None = None  # regwatch proposal id
    adopted_at: str | None = None
    approved_by: str | None = None  # a person, or "auto-policy"
    verified_on: date | None = None  # the day the value was read back from the cited source document


class RuleValue(BaseModel):
    effective_from: date
    effective_to: date | None = None  # inclusive; None = open-ended
    value: Any
    provenance: Provenance

    def covers(self, on: date) -> bool:
        return self.effective_from <= on and (self.effective_to is None or on <= self.effective_to)


class Indexing(BaseModel):
    """A parameter that must receive a fresh value every period (inflation indexing etc.)."""

    cadence: Literal["annual"] = "annual"
    # Month-day in the *prior* year by which the next year's value is normally published.
    expected_by: str = Field(pattern=r"^\d{2}-\d{2}$")
    publication: str  # where it usually appears, used to steer targeted hunts
    search_hint: str | None = None


class Bounds(BaseModel):
    min: float | None = None
    max: float | None = None
    # Largest plausible year-over-year move for a routine update, as a fraction (0.1 = 10%).
    max_change_pct: float | None = None


class Rule(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
    title: str
    jurisdiction: str  # US-FED, US-CA, US-DE ...
    category: str
    value_type: ValueType
    unit: str | None = None
    citation: str  # statutory authority
    keys: list[str] | None = None  # required keys for table values
    indexed: Indexing | None = None
    bounds: Bounds | None = None
    tags: list[str] = []
    notes: str | None = None
    values: list[RuleValue]

    @model_validator(mode="after")
    def _check(self) -> "Rule":
        self.values.sort(key=lambda v: v.effective_from)
        for prev, cur in zip(self.values, self.values[1:]):
            if prev.effective_to is None or prev.effective_to >= cur.effective_from:
                raise ValueError(
                    f"{self.id}: value effective {prev.effective_from} overlaps value effective {cur.effective_from}"
                )
        for v in self.values:
            if v.effective_to is not None and v.effective_to < v.effective_from:
                raise ValueError(f"{self.id}: effective_to before effective_from")
            errs = validate_value(self, v.value)
            if errs:
                raise ValueError(f"{self.id} @ {v.effective_from}: {'; '.join(errs)}")
        return self

    def value_on(self, on: date) -> RuleValue | None:
        for v in self.values:
            if v.covers(on):
                return v
        return None

    def latest(self) -> RuleValue | None:
        return self.values[-1] if self.values else None


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_value(rule: Rule, value: Any) -> list[str]:
    """Type-check a candidate value against the rule's declared type and bounds."""
    t = rule.value_type
    errs: list[str] = []
    if t in ("money", "rate", "number"):
        if not _is_num(value):
            return [f"expected a number, got {value!r}"]
        if t == "rate" and not (0 <= value <= 1):
            errs.append(f"rate {value} outside [0, 1]")
    elif t == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"expected an integer, got {value!r}"]
    elif t == "boolean":
        if not isinstance(value, bool):
            return [f"expected a boolean, got {value!r}"]
    elif t == "text":
        if not isinstance(value, str):
            return [f"expected text, got {value!r}"]
    elif t == "table":
        if not isinstance(value, dict):
            return [f"expected a table (mapping), got {value!r}"]
        if rule.keys and set(value) != set(rule.keys):
            errs.append(f"table keys {sorted(value)} != required {sorted(rule.keys)}")
        for k, cell in value.items():
            cells = cell if isinstance(cell, list) else [cell]
            if not all(_is_num(c) for c in cells):
                errs.append(f"table cell {k!r} is not numeric")
    b = rule.bounds
    if b and not errs:
        for n in numeric_leaves(value):
            if b.min is not None and n < b.min:
                errs.append(f"{n} below minimum {b.min}")
            if b.max is not None and n > b.max:
                errs.append(f"{n} above maximum {b.max}")
    return errs


def numeric_leaves(value: Any) -> list[float]:
    if _is_num(value):
        return [value]
    if isinstance(value, dict):
        return [n for v in value.values() for n in numeric_leaves(v)]
    if isinstance(value, list):
        return [n for v in value for n in numeric_leaves(v)]
    return []
