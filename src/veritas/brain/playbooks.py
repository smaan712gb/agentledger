"""The CPA second brain: expert playbooks, firm precedents and proactive opportunity scans.

Playbooks capture how the best practitioners approach a planning or compliance issue:
when it applies (a machine-checkable condition over client facts), what to do, what must
genuinely be true for it to hold up (substance), and which live rules it depends on. When
any of those rules change, the playbook is automatically flagged for re-review.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from .. import audit
from ..db import rows
from ..expr import ExprError, evaluate
from ..kb.store import KnowledgeBase
from ..ledger import store


class Playbook(BaseModel):
    id: str
    title: str
    category: Literal["planning", "compliance", "review", "risk"]
    summary: str
    applies_when: str  # expression over client facts
    domains: list[str] = ["*"]
    rule_refs: list[str] = []
    steps: list[str]
    substance: str  # what must genuinely be true; the honesty guardrail
    aggressiveness: Literal["conservative", "moderate", "aggressive"] = "conservative"
    citations: list[str]
    status: Literal["seed_unreviewed", "approved", "retired"] = "seed_unreviewed"
    last_reviewed: date | None = None
    reviewed_by: str | None = None
    review_by: date


class Brain:
    def __init__(self, root: Path, kb: KnowledgeBase):
        self.root = Path(root)
        self.kb = kb
        self.playbooks: dict[str, Playbook] = {}
        for p in sorted(self.root.glob("*.yaml")):
            pb = Playbook.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
            self.playbooks[pb.id] = pb

    def save(self, pb: Playbook) -> Path:
        path = self.root / f"{pb.id}.yaml"
        path.write_text(yaml.safe_dump(pb.model_dump(mode="json"), sort_keys=False, width=110), encoding="utf-8")
        self.playbooks[pb.id] = pb
        return path

    def freshness(self, pb: Playbook, today: date | None = None) -> dict[str, Any]:
        """Is this playbook still trustworthy given today's rules?"""
        today = today or date.today()
        reasons = []
        if pb.status == "seed_unreviewed":
            reasons.append("seed content not yet reviewed by a CPA at this firm")
        if pb.review_by < today:
            reasons.append(f"scheduled review date {pb.review_by} has passed")
        for rid in pb.rule_refs:
            rule = self.kb.rules.get(rid)
            if rule is None:
                reasons.append(f"references unknown rule {rid}")
                continue
            for v in rule.values:
                changed = v.provenance.adopted_at[:10] if v.provenance.adopted_at else None
                if changed and (pb.last_reviewed is None or date.fromisoformat(changed) > pb.last_reviewed):
                    reasons.append(f"rule {rid} changed on {changed} after the last review")
        return {"fresh": not reasons, "reasons": reasons}

    def facts_for(self, conn: sqlite3.Connection, client_id: str) -> dict[str, Any]:
        c = store.get_client(conn, client_id)
        y = date.today().year
        facts: dict[str, Any] = {"kind": c["kind"], "entity_type": c["entity_type"] or "", "formed_under": c["formed_under"],
                                 "domain": c["domain"], **c["facts"]}
        for label, year in (("ytd", y), ("prior", y - 1)):
            bal = store.balances(conn, client_id, date(year, 1, 1), date(year, 12, 31))
            rev = -sum((Decimal(b["balance"]) for b in bal if b["type"] == "revenue"), Decimal(0))
            exp = sum((Decimal(b["balance"]) for b in bal if b["type"] == "expense"), Decimal(0))
            facts[f"revenue_{label}"] = rev
            facts[f"net_income_{label}"] = rev - exp
        facts["assets_placed_this_year"] = any(a.placed_in_service.year == y for a, _ in store.assets(conn, client_id))
        return facts

    def scan(self, conn: sqlite3.Connection, client_id: str) -> list[dict[str, Any]]:
        """Run every playbook against a client: applies / does not apply / need facts."""
        facts = self.facts_for(conn, client_id)
        out = []
        for pb in self.playbooks.values():
            if pb.status == "retired" or ("*" not in pb.domains and facts["domain"] not in pb.domains):
                continue
            try:
                applies = bool(evaluate(pb.applies_when, facts))
                status, missing = ("applies" if applies else "not_applicable"), None
            except ExprError as e:
                msg = str(e)
                status = "needs_facts"
                missing = msg.split("'")[1] if msg.startswith("unknown name") else msg
            if status == "not_applicable":
                continue
            out.append({"playbook_id": pb.id, "title": pb.title, "category": pb.category, "status": status,
                        "missing_fact": missing, "summary": pb.summary, "aggressiveness": pb.aggressiveness,
                        "freshness": self.freshness(pb)})
        order = {"applies": 0, "needs_facts": 1}
        return sorted(out, key=lambda o: (order[o["status"]], o["category"] != "risk"))

    def relevant(self, question: str, limit: int = 4) -> list[Playbook]:
        words = {w for w in question.lower().split() if len(w) > 3}
        scored = []
        for pb in self.playbooks.values():
            hay = f"{pb.title} {pb.summary} {' '.join(pb.citations)}".lower()
            s = sum(1 for w in words if w in hay)
            if s:
                scored.append((s, pb.id, pb))
        return [pb for _, _, pb in sorted(scored, reverse=True)[:limit]]


# -- firm precedents: the firm's own best judgment, compounding --------------------------------

def add_precedent(conn: sqlite3.Connection, *, topic: str, situation: str, judgment: str, citations: list[str],
                  author: str, domain: str | None = None, client_id: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO precedents (topic, situation, judgment, citations, domain, author, client_id, at) VALUES (?,?,?,?,?,?,?,?)",
        (topic, situation, judgment, json.dumps(citations), domain, author, client_id, audit.now()))
    audit.record(conn, author, "cpa", "precedent.added", {"precedent_id": cur.lastrowid, "topic": topic}, client_id=client_id)
    return int(cur.lastrowid)


def search_precedents(conn: sqlite3.Connection, question: str, limit: int = 4) -> list[dict[str, Any]]:
    words = [w for w in question.lower().split() if len(w) > 3]
    scored = []
    for p in rows(conn, "SELECT * FROM precedents ORDER BY id DESC LIMIT 2000"):
        hay = f"{p['topic']} {p['situation']} {p['judgment']}".lower()
        s = sum(1 for w in words if w in hay)
        if s:
            p["citations"] = json.loads(p["citations"])
            scored.append((s, p["id"], p))
    return [p for _, _, p in sorted(scored, reverse=True)[:limit]]
