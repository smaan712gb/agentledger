"""Sentinel: deterministic verification of rule changes, plus golden regression.

Nothing here calls a model. It decides whether an AI draft is grounded, well-typed,
plausible and safe, and how risky it is to adopt without a human.
"""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from ..ai.grounding import number_supported, quote_in
from ..calc.engine import Ctx
from ..calc.federal import run_calc
from ..kb.model import Rule, RuleValue, numeric_leaves, validate_value
from ..kb.store import KnowledgeBase, new_value
from .core import Check


class Conflict(ValueError):
    pass


def place_value(rule: Rule, nv: RuleValue) -> tuple[Rule, str]:
    """Insert a value into a rule's timeline. Returns the updated rule and how it was placed."""
    values = [v.model_copy() for v in rule.values]
    same = [v for v in values if v.effective_from == nv.effective_from]
    if same:
        values.remove(same[0])
        mode = "amend"
    else:
        mode = "new_period"
        for v in values:
            if v.covers(nv.effective_from):
                if v.effective_to is None:
                    v.effective_to = nv.effective_from - timedelta(days=1)
                    mode = "supersede_open"
                else:
                    raise Conflict(f"{rule.id}: {nv.effective_from} falls inside existing value "
                                   f"{v.effective_from}..{v.effective_to}; needs a human edit")
        if nv.effective_to is not None:
            later = [v for v in values if v.effective_from > nv.effective_from]
            if later and later[0].effective_from <= nv.effective_to:
                raise Conflict(f"{rule.id}: new value overlaps the value starting {later[0].effective_from}")
        elif any(v.effective_from > nv.effective_from for v in values):
            raise Conflict(f"{rule.id}: open-ended value would overlap later values")
    values.append(nv)
    data = rule.model_dump()
    data["values"] = [v.model_dump() for v in values]
    return Rule.model_validate(data), mode


def load_golden(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or [] if path.exists() else []


def run_golden(kb: KnowledgeBase, scenarios: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for s in scenarios:
        try:
            got = run_calc(Ctx(kb), s["calc"], s["inputs"])
            got_s = str(got).lower() if isinstance(got, bool) else str(got)
            exp_s = str(s["expect"]).lower() if isinstance(s["expect"], bool) else str(s["expect"])
            try:
                ok = float(got_s) == float(exp_s)
            except ValueError:
                ok = got_s == exp_s
            out[s["id"]] = {"ok": ok, "got": got_s, "expect": exp_s}
        except Exception as e:
            out[s["id"]] = {"ok": False, "got": None, "expect": str(s["expect"]), "error": f"{type(e).__name__}: {e}"}
    return out


def verify_rule_change(kb: KnowledgeBase, payload: dict[str, Any], document_text: str, source_url: str,
                       official_domains: list[str], golden: list[dict[str, Any]], today: date | None = None,
                       truncated: bool = False) -> tuple[list[Check], str, list[str], dict[str, Any]]:
    """Returns (checks, risk, risk_reasons, impact)."""
    from ..regwatch.documents import is_official

    today = today or date.today()
    checks: list[Check] = []
    reasons: list[str] = []
    risk = 0  # 0 low, 1 medium, 2 high, 3 critical

    def bump(level: int, why: str) -> None:
        nonlocal risk
        risk = max(risk, level)
        reasons.append(why)

    official = is_official(source_url, official_domains)
    checks.append(Check(name="official_source", ok=official, detail=source_url))
    if not official:
        bump(3, "source is not an official government domain")
    if truncated:
        bump(1, "document was truncated before drafting")

    new_rules = {r["id"]: r for r in payload.get("new_rules", [])}
    if new_rules:
        bump(2, f"creates new rule(s): {', '.join(new_rules)}")

    with tempfile.TemporaryDirectory() as tmp:
        sandbox = kb.sandbox(Path(tmp) / "rules")
        for change in payload.get("changes", []):
            rid = change["rule_id"]
            label = f"{rid}@{change['effective_from']}"
            try:
                if rid in sandbox.rules:
                    rule = sandbox.get(rid)
                elif rid in new_rules:
                    spec = dict(new_rules[rid])
                    rule = Rule.model_validate({**spec, "values": []})
                else:
                    raise KeyError(f"unknown rule {rid}")
                value = change["value"]
                errs = validate_value(rule, value)
                checks.append(Check(name=f"type:{label}", ok=not errs, detail="; ".join(errs) or rule.value_type))
                quotes = change.get("evidence_quotes", [])
                missing = [q for q in quotes if not quote_in(q, document_text)]
                checks.append(Check(name=f"quotes_verbatim:{label}", ok=bool(quotes) and not missing,
                                    detail="no evidence quotes" if not quotes else
                                    (f"{len(missing)} quote(s) not found in document" if missing else f"{len(quotes)} quote(s) found")))
                leaves = numeric_leaves(value) if not isinstance(value, bool) else []
                unsupported = [n for n in leaves if not number_supported(n, quotes)]
                checks.append(Check(name=f"numbers_grounded:{label}", ok=not unsupported,
                                    detail=f"not in quotes: {unsupported}" if unsupported else f"{len(leaves)} number(s) grounded"))
                start = date.fromisoformat(change["effective_from"])
                end = date.fromisoformat(change["effective_to"]) if change.get("effective_to") else None
                nv = new_value(value, start, end, change.get("citation") or "", source_url)
                prior = rule.value_on(start - timedelta(days=1)) or (rule.values[-1] if rule.values else None)
                updated, mode = place_value(rule, nv)
                checks.append(Check(name=f"timeline:{label}", ok=True, detail=mode))
                if mode == "amend":
                    bump(2, f"{rid}: amends a value already on record for {start}")
                elif mode == "supersede_open":
                    bump(1, f"{rid}: ends an open-ended value")
                if start < date(today.year, 1, 1) and mode != "new_period":
                    bump(2, f"{rid}: retroactive change to a past period")
                if not rule.indexed:
                    bump(1, f"{rid}: not a routinely indexed parameter")
                if prior is not None and rule.bounds and rule.bounds.max_change_pct is not None:
                    jump = _max_change(prior.value, value)
                    if jump is not None and jump > rule.bounds.max_change_pct:
                        bump(2, f"{rid}: changes {jump:.1%} vs prior (routine limit {rule.bounds.max_change_pct:.0%})")
                sandbox.save_rule(updated)
            except Exception as e:  # any structural problem is a failed check, not a crash
                checks.append(Check(name=f"apply:{label}", ok=False, detail=f"{type(e).__name__}: {e}"))
        if not payload.get("changes"):
            checks.append(Check(name="has_changes", ok=False, detail="draft proposes no values"))

        base = run_golden(kb, golden)
        cand = run_golden(sandbox, golden)
        errors = [k for k, v in cand.items() if v.get("error") and not base[k].get("error")]
        changed = {k: {"before": base[k]["got"], "after": cand[k]["got"]} for k in cand if cand[k]["got"] != base[k]["got"]}
        checks.append(Check(name="golden_no_new_errors", ok=not errors, detail=", ".join(errors) or f"{len(golden)} scenarios ran"))
        if changed:
            bump(2, f"changes {len(changed)} golden scenario result(s)")
        impact = {"golden_changed": changed}

    if not all(c.ok for c in checks):
        bump(3, "one or more verification checks failed")
    level = ["low", "medium", "high", "critical"][risk]
    return checks, level, reasons or ["routine update: grounded, new period, within normal range"], impact


def _max_change(prev: Any, new: Any) -> float | None:
    if isinstance(prev, dict) and isinstance(new, dict):
        jumps = [j for j in (_max_change(prev.get(k), new.get(k)) for k in new) if j is not None]
        return max(jumps) if jumps else None
    if isinstance(prev, (int, float)) and isinstance(new, (int, float)) and not isinstance(prev, bool) and prev:
        return abs(new - prev) / abs(prev)
    return None
