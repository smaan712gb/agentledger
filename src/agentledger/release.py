"""Release classification: may this change set ship to production without a person? (config/release_policy.yaml)

Used by CI on the agents' pull request. The answer is "auto" only when every changed path belongs to an
auto-releasable category, every rule change in it was adopted by the auto-policy as routine, no protected path is
touched, and no deadline freeze is in force. Everything else is "review" with the reasons, for one approval click.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Decision:
    verdict: str                      # "auto" | "review"
    categories: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    approver: str | None = None


def _in_freeze(policy: dict[str, Any], today: date) -> str | None:
    for md in policy.get("freeze_before_deadlines", []):
        m, d = (int(x) for x in md.split("-"))
        deadline = date(today.year, m, d)
        if deadline - timedelta(days=3) <= today <= deadline:
            return f"deadline freeze before {deadline.isoformat()}"
    return None


def classify(root: Path, changed_paths: list[str], *, today: date | None = None) -> Decision:
    policy = yaml.safe_load((root / "config" / "release_policy.yaml").read_text(encoding="utf-8"))
    foundry = yaml.safe_load((root / "config" / "foundry.yaml").read_text(encoding="utf-8"))
    protected = foundry.get("protected_paths", [])
    reasons, cats = [], set()
    for path in changed_paths:
        p = path.replace("\\", "/")
        if any(p == x or p.startswith(x.rstrip("/") + "/") for x in protected):
            reasons.append(f"{p} is a protected path")
            continue
        hit = [c for c, prefixes in policy.get("auto_paths", {}).items() if any(p.startswith(x) for x in prefixes)]
        if not hit:
            reasons.append(f"{p} is outside every auto-releasable category")
            continue
        cats.update(hit)
    if "routine_indexed_value" in cats:
        reasons += _non_routine_rule_changes(root)
    freeze = _in_freeze(policy, today or date.today())
    if freeze:
        reasons.append(freeze)
    for c in sorted(cats):
        if not policy["categories"].get(c, {}).get("auto_release"):
            reasons.append(f"category {c} is not auto-releasable")
    if reasons:
        approvers = {policy["categories"].get(c, {}).get("approver") for c in cats} - {None}
        return Decision("review", sorted(cats), reasons, approver=sorted(approvers)[0] if approvers else "tax_content_owner")
    return Decision("auto", sorted(cats))


def _non_routine_rule_changes(root: Path) -> list[str]:
    """Every proposal adopted since the last release must be an auto-policy routine indexed value."""
    out = []
    for f in sorted((root / "state" / "proposals").glob("*.json")) if (root / "state" / "proposals").exists() else []:
        p = json.loads(f.read_text(encoding="utf-8"))
        if p.get("kind") != "rule_change" or p.get("status") != "adopted" or p.get("released"):
            continue
        mode = (p.get("decision") or {}).get("mode")
        if mode != "auto" or p.get("risk") not in ("none", "low"):
            out.append(f"rule change {p['id']} was not adopted as routine (mode {mode}, risk {p.get('risk')})")
    return out
