"""Loads, resolves and persists the regulation knowledge base (one YAML file per rule)."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .model import Rule, RuleValue


class MissingValue(LookupError):
    """No value of a rule covers the requested date. Never guess: surface it."""


@dataclass(frozen=True)
class Resolved:
    rule_id: str
    value: Any
    effective_from: date
    effective_to: date | None
    source: str
    url: str | None


class KnowledgeBase:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.rules: dict[str, Rule] = {}
        self._paths: dict[str, Path] = {}
        self.reload()

    def reload(self) -> None:
        self.rules.clear()
        self._paths.clear()
        for path in sorted(self.root.rglob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            rule = Rule.model_validate(data)
            if rule.id in self.rules:
                raise ValueError(f"duplicate rule id {rule.id} in {path}")
            self.rules[rule.id] = rule
            self._paths[rule.id] = path

    def get(self, rule_id: str) -> Rule:
        try:
            return self.rules[rule_id]
        except KeyError:
            raise KeyError(f"unknown rule {rule_id}") from None

    def resolve(self, rule_id: str, on: date) -> Resolved:
        rule = self.get(rule_id)
        rv = rule.value_on(on)
        if rv is None:
            raise MissingValue(f"{rule_id} has no value effective on {on.isoformat()}")
        return Resolved(rule_id, rv.value, rv.effective_from, rv.effective_to, rv.provenance.source, rv.provenance.url)

    def try_resolve(self, rule_id: str, on: date) -> Resolved | None:
        try:
            return self.resolve(rule_id, on)
        except MissingValue:
            return None

    def search(self, text: str, limit: int = 20) -> list[Rule]:
        words = [w for w in text.lower().split() if len(w) > 2]
        scored = []
        for rule in self.rules.values():
            hay = " ".join([rule.id, rule.title, rule.category, rule.citation, *rule.tags]).lower()
            score = sum(hay.count(w) for w in words)
            if score:
                scored.append((score, rule.id, rule))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [r for _, _, r in scored[:limit]]

    def version(self) -> str:
        h = hashlib.sha256()
        for rid in sorted(self._paths):
            h.update(rid.encode())
            h.update(self._paths[rid].read_bytes())
        return h.hexdigest()[:12]

    def path_for(self, rule_id: str) -> Path:
        if rule_id in self._paths:
            return self._paths[rule_id]
        rule = self.rules[rule_id]
        return self.root / rule.jurisdiction.lower() / rule.category / f"{rule_id}.yaml"

    def save_rule(self, rule: Rule) -> Path:
        Rule.model_validate(rule.model_dump())  # re-run invariants before touching disk
        self.rules[rule.id] = rule
        path = self.path_for(rule.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_rule(rule), encoding="utf-8")
        self._paths[rule.id] = path
        return path

    def sandbox(self, dest: Path) -> "KnowledgeBase":
        """An isolated copy for what-if evaluation of a proposed change."""
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(self.root, dest)
        return KnowledgeBase(dest)

    def catalog(self, on: date) -> list[dict[str, Any]]:
        """Compact listing of every rule and its value on a date, for LLM context."""
        out = []
        for rule in sorted(self.rules.values(), key=lambda r: r.id):
            cur = rule.value_on(on)
            latest = rule.latest()
            out.append(
                {
                    "id": rule.id,
                    "title": rule.title,
                    "type": rule.value_type,
                    "unit": rule.unit,
                    "keys": rule.keys,
                    "citation": rule.citation,
                    "value_on_date": cur.value if cur else None,
                    "latest_value": latest.value if latest else None,
                    "latest_effective": [
                        latest.effective_from.isoformat() if latest else None,
                        latest.effective_to.isoformat() if latest and latest.effective_to else None,
                    ],
                    "indexed": bool(rule.indexed),
                }
            )
        return out


def dump_rule(rule: Rule) -> str:
    data = rule.model_dump(mode="json", exclude_none=True)
    # Keep provenance fields that are explicitly None out of the file, but keep the
    # key order that reads naturally for a reviewer.
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def snapshot(rule: Rule | None) -> dict[str, Any] | None:
    return None if rule is None else rule.model_dump(mode="json", exclude_none=True)


def restore(data: dict[str, Any] | None) -> Rule | None:
    return None if data is None else Rule.model_validate(data)


def tax_year_span(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def new_value(value: Any, start: date, end: date | None, source: str, url: str | None = None, **prov: Any) -> RuleValue:
    from .model import Provenance

    return RuleValue(effective_from=start, effective_to=end, value=value, provenance=Provenance(source=source, url=url, **prov))
