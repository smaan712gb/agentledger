"""Applying domain packs to clients: onboarding, template postings, domain integrity rules, KPIs."""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any

from ..integrity.checks import Finding
from ..kb.store import KnowledgeBase
from ..ledger import store
from .packs import DomainCheck, Packs, evaluate_kpis, normalized_balances, render


def onboard(conn: sqlite3.Connection, packs: Packs, client_id: str, pack_id: str) -> int:
    """Instantiate the pack's chart of accounts for a client (idempotent)."""
    existing = store.accounts(conn, client_id)
    n = 0
    for code, a in packs.accounts(pack_id).items():
        if code not in existing:
            store.add_account(conn, client_id, code, a.name, a.type)
            n += 1
    conn.execute("UPDATE clients SET domain = ? WHERE id = ?", (pack_id, client_id))
    return n


def post_template(conn: sqlite3.Connection, packs: Packs, kb: KnowledgeBase, client_id: str, template_id: str,
                  inputs: dict[str, Any], on: date, *, actor: str, role: str = "cpa", document_id: str | None = None,
                  source: str = "template") -> int:
    client = store.get_client(conn, client_id)
    templates = packs.templates(client["domain"])
    if template_id not in templates:
        raise KeyError(f"template {template_id} not in {client['domain']} pack")
    memo, lines = render(templates[template_id], inputs, kb, on)
    return store.post(conn, client_id, on, memo, [store.Line(a, amt, tt) for a, amt, tt in lines],
                      source=f"{source}:{template_id}", actor=actor, role=role, document_id=document_id)


def domain_findings(conn: sqlite3.Connection, packs: Packs, client_id: str, year: int) -> list[Finding]:
    client = store.get_client(conn, client_id)
    if client["domain"] not in packs.packs:
        return []
    start, end = date(year, 1, 1), date(year, 12, 31)
    bal_rows = store.balances(conn, client_id, start, end)
    bal = normalized_balances(bal_rows)
    postings = store.postings_in(conn, client_id, start, end)
    out: list[Finding] = []
    for c in packs.checks(client["domain"]):
        out += _run(c, bal, postings, year)
    return out


def _run(c: DomainCheck, bal: dict[str, Decimal], postings: list[dict[str, Any]], year: int) -> list[Finding]:
    p = c.params
    cid = f"domain.{c.id}"

    def finding(detail: str, evidence: list[dict[str, Any]] | None = None, key: str = "") -> Finding:
        return Finding(cid, c.severity, c.title, f"{detail} {c.detail}".strip(), c.owner, evidence or [], c.citation,
                       key=key or str(year))

    if c.type == "min_balance":
        v = bal.get(p["account"], Decimal(0))
        if v < Decimal(str(p.get("min", 0))):
            return [finding(f"Account {p['account']} balance is {v:,.2f} for {year}.")]
    elif c.type == "ratio_max":
        num, den = bal.get(p["numerator"], Decimal(0)), bal.get(p["denominator"], Decimal(0))
        if den and num / den > Decimal(str(p["max"])):
            return [finding(f"Ratio {p['numerator']}/{p['denominator']} is {num / den:.2%} (limit {Decimal(str(p['max'])):.2%}).")]
    elif c.type == "coverage":
        assets = sum((bal.get(a, Decimal(0)) for a in p["assets"]), Decimal(0))
        liabs = sum((bal.get(l, Decimal(0)) for l in p["liabilities"]), Decimal(0))
        if assets < liabs:
            return [finding(f"Covering balances {assets:,.2f} are short of obligations {liabs:,.2f} by {liabs - assets:,.2f}.")]
    elif c.type in ("memo_pattern", "memo_account"):
        rx = re.compile(p["pattern"], re.I)
        hits = []
        for x in postings:
            if not rx.search(x["memo"] or ""):
                continue
            if c.type == "memo_account":
                in_scope = (x["account_code"] in p.get("accounts", [])) or (
                    p.get("account_type") == x["account_type"] and x["account_code"] not in p.get("except", []))
                if not in_scope:
                    continue
            hits.append(x)
        return [finding(f"Entry #{h['entry_id']} “{h['memo']}” posted to {h['account_code']} {h['account_name']}.",
                        [{"entry_id": h["entry_id"]}], key=f"{h['entry_id']}:{h['line']}") for h in hits]
    return []


def kpis(conn: sqlite3.Connection, packs: Packs, client_id: str, year: int) -> list[dict[str, Any]]:
    client = store.get_client(conn, client_id)
    if client["domain"] not in packs.packs:
        return []
    bal = store.balances(conn, client_id, date(year, 1, 1), date(year, 12, 31))
    return evaluate_kpis(packs.kpis(client["domain"]), bal, client["facts"])
