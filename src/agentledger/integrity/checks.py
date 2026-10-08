"""Integrity checks that keep both the client and the CPA honest.

Deterministic, explainable and cited. A finding can be explained, corrected or
accepted as a risk, but never deleted; every resolution is attributed and lands
in the shared audit trail, visible to both sides.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from .. import audit
from ..calc.engine import D, Ctx
from ..db import one, rows
from ..kb.store import KnowledgeBase
from ..ledger import store


@dataclass
class Finding:
    check_id: str
    severity: str
    title: str
    detail: str
    owner: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    citation: str | None = None
    key: str = ""  # stable identity so re-running a check doesn't duplicate a finding

    def id(self, client_id: str) -> str:
        return hashlib.sha256(f"{client_id}|{self.check_id}|{self.key}".encode()).hexdigest()[:16]


Check = Callable[[sqlite3.Connection, Ctx, str, int], list[Finding]]
CHECKS: dict[str, tuple[str, Check]] = {}


def check(check_id: str, description: str):
    def deco(fn: Check) -> Check:
        CHECKS[check_id] = (description, fn)
        return fn
    return deco


def _year(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


@check("ledger.chain", "Ledger hash chain is intact (no rewritten history)")
def ledger_chain(conn, ctx, client_id, year):
    v = store.verify_chain(conn, client_id)
    if v["ok"]:
        return []
    return [Finding("ledger.chain", "critical", "Ledger history has been altered",
                    f"Hash chain breaks at entry #{v['broken_at']}. Entries were modified outside the application.",
                    "cpa", [{"entry_id": v["broken_at"]}], key=str(v["broken_at"]))]


@check("ledger.closed_period", "Entries dated inside a closed period, or posted long after the fact")
def closed_period(conn, ctx, client_id, year):
    c = store.get_client(conn, client_id)
    out = []
    start, end = _year(year)
    for e in store.entries(conn, client_id, start, end):
        created = datetime.fromisoformat(e["created_at"]).date()
        entry_date = date.fromisoformat(e["date"])
        if c["closed_through"] and entry_date <= date.fromisoformat(c["closed_through"]) and \
                created > date.fromisoformat(c["closed_through"]):
            out.append(Finding("ledger.closed_period", "high", f"Entry #{e['id']} posted into a closed period",
                               f"Dated {e['date']} but recorded {created} after books were closed through {c['closed_through']}.",
                               "cpa", [{"entry_id": e["id"]}], key=str(e["id"])))
        elif (created - entry_date).days > 90 and e["source"] != "reversal":
            out.append(Finding("ledger.backdated", "medium", f"Entry #{e['id']} recorded {(created - entry_date).days} days after its date",
                               "Late, backdated entries change already-reported results. Confirm the support and the reason.",
                               "cpa", [{"entry_id": e["id"]}], key=str(e["id"])))
    return out


@check("income.info_return_mismatch", "Income reported to the IRS on 1099s exceeds income recorded on the books")
def info_return_mismatch(conn, ctx, client_id, year):
    reported = rows(conn, "SELECT * FROM info_returns WHERE client_id = ? AND tax_year = ?", client_id, year)
    if not reported:
        return []
    total_reported = sum((D(r["amount"]) for r in reported), Decimal(0))
    start, end = _year(year)
    revenue = -sum((D(p["amount"]) for p in store.postings_in(conn, client_id, start, end)
                    if p["account_type"] == "revenue" and p["tax_treatment"] != "municipal_interest"), Decimal(0))
    gap = total_reported - revenue
    if gap <= Decimal("1.00"):
        return []
    return [Finding(
        "income.info_return_mismatch", "high",
        f"${gap:,.2f} reported on information returns is not on the books",
        f"Payers reported ${total_reported:,.2f} on {len(reported)} information return(s) for {year}; "
        f"the ledger shows ${revenue:,.2f} of revenue. The IRS matches these automatically (AUR / CP2000).",
        "both", [{"document_id": r["document_id"], "form": r["form"], "payer": r["payer"], "amount": r["amount"]} for r in reported],
        citation="IRC §6041; IRC §6050W", key=str(year))]


@check("substantiation.missing_receipt", "Travel, meals and vehicle expenses at or above the receipt threshold need a receipt")
def missing_receipt(conn, ctx, client_id, year):
    start, end = _year(year)
    out = []
    for p in store.postings_in(conn, client_id, start, end):
        if p["account_type"] != "expense" or p["tax_treatment"] not in ("meals", "travel", "entertainment", "vehicle"):
            continue
        threshold = ctx.dec("us_fed.substantiation.receipt_threshold", date.fromisoformat(p["date"]))
        if D(p["amount"]) >= threshold and not p["document_id"]:
            out.append(Finding("substantiation.missing_receipt", "medium",
                               f"No receipt for ${D(p['amount']):,.2f} {p['tax_treatment']} expense",
                               f"Entry #{p['entry_id']} “{p['memo']}” has no supporting document attached. "
                               f"Without one the deduction is at risk on exam.",
                               "client", [{"entry_id": p["entry_id"]}],
                               citation="IRC §274(d); Treas. Reg. §1.274-5(c)(2)(iii)", key=f"{p['entry_id']}:{p['line']}"))
    return out


ENTERTAINMENT_HINTS = re.compile(r"\b(golf|tickets?|concert|game|stadium|suite|club dues|country club|theater|theatre|show)\b", re.I)
PERSONAL_HINTS = re.compile(r"\b(netflix|spotify|grocer(y|ies)|vacation|disney|personal|gym|daycare|tuition|jewel(ry|lery))\b", re.I)


@check("classification.entertainment_as_meals", "Entertainment recorded as 50%-deductible meals")
def entertainment_as_meals(conn, ctx, client_id, year):
    start, end = _year(year)
    return [Finding("classification.entertainment_as_meals", "medium",
                    f"Entry #{p['entry_id']} looks like entertainment, recorded as meals",
                    f"“{p['memo']}” is tagged as a meal (50% deductible) but reads like entertainment (0% deductible).",
                    "cpa", [{"entry_id": p["entry_id"]}], citation="IRC §274(a)", key=f"{p['entry_id']}:{p['line']}")
            for p in store.postings_in(conn, client_id, start, end)
            if p["tax_treatment"] == "meals" and ENTERTAINMENT_HINTS.search(p["memo"] or "")]


@check("classification.personal_expense", "Possible personal expense recorded as a business deduction")
def personal_expense(conn, ctx, client_id, year):
    start, end = _year(year)
    return [Finding("classification.personal_expense", "high",
                    f"Entry #{p['entry_id']} may be a personal expense",
                    f"“{p['memo']}” (${D(p['amount']):,.2f}) is deducted as a business expense. "
                    "Personal, living or family expenses are not deductible.",
                    "client", [{"entry_id": p["entry_id"]}], citation="IRC §262", key=f"{p['entry_id']}:{p['line']}")
            for p in store.postings_in(conn, client_id, start, end)
            if p["account_type"] == "expense" and PERSONAL_HINTS.search(p["memo"] or "")]


@check("pattern.round_numbers", "Unusually many large round-dollar expenses (estimates instead of actuals)")
def round_numbers(conn, ctx, client_id, year):
    start, end = _year(year)
    exp = [p for p in store.postings_in(conn, client_id, start, end) if p["account_type"] == "expense" and D(p["amount"]) >= 500]
    if len(exp) < 5:
        return []
    rounds = [p for p in exp if D(p["amount"]) % 100 == 0 and not p["document_id"]]
    share = len(rounds) / len(exp)
    if share < 0.4:
        return []
    return [Finding("pattern.round_numbers", "low", f"{len(rounds)} of {len(exp)} large expenses are unsupported round numbers",
                    f"{share:.0%} of expenses ≥ $500 are exact hundreds with no document. Round figures often signal estimates.",
                    "client", [{"entry_id": p["entry_id"]} for p in rounds[:20]], key=str(year))]


@check("compliance.boi", "Beneficial ownership (FinCEN BOI) filing obligation under current rules")
def boi(conn, ctx, client_id, year):
    from ..calc.federal import boi_report_required

    c = store.get_client(conn, client_id)
    if c["kind"] != "business":
        return []
    today = date.today()
    if boi_report_required(ctx, c["formed_under"], today):
        return [Finding("compliance.boi", "medium", "BOI report required",
                        "This entity is a reporting company under current FinCEN rules; confirm the BOI report is filed and current.",
                        "cpa", citation="31 CFR 1010.380", key=today.isoformat()[:7])]
    return []


def run_all(conn: sqlite3.Connection, kb: KnowledgeBase, client_id: str, year: int, actor: str = "integrity-sweeper",
            packs: Any = None) -> list[dict[str, Any]]:
    """Run built-in checks plus the client's domain-pack rules; persist new findings."""
    found: list[Finding] = []
    for check_id, (_, fn) in CHECKS.items():
        found += fn(conn, Ctx(kb), client_id, year)
    if packs is not None:
        from ..domains.service import domain_findings

        found += domain_findings(conn, packs, client_id, year)
    seen_ids = set()
    for f in found:
        fid = f.id(client_id)
        seen_ids.add(fid)
        if one(conn, "SELECT id FROM findings WHERE id = ?", fid):
            continue
        conn.execute(
            "INSERT INTO findings (id, client_id, check_id, severity, title, detail, evidence, citation, owner, tax_year, first_seen) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (fid, client_id, f.check_id, f.severity, f.title, f.detail, json.dumps(f.evidence), f.citation, f.owner, year, audit.now()),
        )
        audit.record(conn, actor, "agent", "finding.raised", {"finding_id": fid, "check": f.check_id, "severity": f.severity,
                                                             "title": f.title}, client_id=client_id)
    # A condition that no longer reproduces (receipt attached, entry reclassified...) is closed by
    # the sweeper with an attributed note. History is kept; nothing is deleted.
    for f in list_findings(conn, client_id):
        if f["status"] == "open" and f["tax_year"] == year and f["id"] not in seen_ids:
            resolve(conn, f["id"], actor, "agent", "corrected", "Condition no longer detected on re-check; cleared automatically.")
    return list_findings(conn, client_id)


def list_findings(conn: sqlite3.Connection, client_id: str) -> list[dict[str, Any]]:
    out = rows(conn, "SELECT * FROM findings WHERE client_id = ? ORDER BY first_seen DESC", client_id)
    for f in out:
        f["evidence"] = json.loads(f["evidence"])
        f["resolutions"] = rows(conn, "SELECT * FROM finding_resolutions WHERE finding_id = ? ORDER BY id", f["id"])
        last = f["resolutions"][-1]["action"] if f["resolutions"] else None
        f["status"] = "open" if last in (None, "reopened") else "resolved"
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    out.sort(key=lambda f: (f["status"] != "open", order[f["severity"]]))
    return out


def resolve(conn: sqlite3.Connection, finding_id: str, actor: str, role: str, action: str, note: str) -> None:
    f = one(conn, "SELECT * FROM findings WHERE id = ?", finding_id)
    if not f:
        raise KeyError(finding_id)
    if len(note.strip()) < 10:
        raise ValueError("a resolution needs a real explanation (at least 10 characters)")
    if action == "accepted_risk" and role != "cpa":  # the sweeper ("agent") may only record that a condition cleared
        raise PermissionError("only the CPA can accept a risk; clients explain or correct")
    conn.execute("INSERT INTO finding_resolutions (finding_id, actor, role, action, note, at) VALUES (?,?,?,?,?,?)",
                 (finding_id, actor, role, action, note.strip(), audit.now()))
    # Requests and tasks spawned by this finding are no longer needed once it is resolved.
    conn.execute("UPDATE tasks SET status = 'done', done_at = ?, done_note = ? WHERE client_id = ? AND status = 'open' "
                 "AND instr(title, ?) > 0", (audit.now(), f"finding resolved ({action}) by {actor}", f["client_id"], f["title"]))
    audit.record(conn, actor, role, f"finding.{action}", {"finding_id": finding_id, "title": f["title"], "note": note.strip()},
                 client_id=f["client_id"])


def integrity_score(findings: list[dict[str, Any]]) -> int:
    weights = {"critical": 40, "high": 15, "medium": 6, "low": 2, "info": 0}
    penalty = sum(weights[f["severity"]] for f in findings if f["status"] == "open")
    return max(0, 100 - penalty)
