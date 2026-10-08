"""Bank and card feed import with categorization that learns from the books themselves.

Order of evidence: (1) how this client's own history categorized the same payee,
(2) well-known merchant patterns, (3) the local model given the chart of accounts.
Suggestions never post on their own; a person confirms, and that confirmation is
what the next import learns from.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

from ..ai.router import Router, Unavailable
from ..db import rows
from . import store

ACCOUNT_TREATMENT = {"6200": "meals", "6210": "entertainment", "6300": "travel", "6400": "vehicle", "6700": "fines_penalties",
                     "6610": "officer_life_insurance", "4910": "municipal_interest", "6500": "depreciation",
                     "7000": "federal_income_tax"}

MERCHANTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"SHELL|CHEVRON|EXXON|MOBIL|BP |SUNOCO|VALERO|PARKING|TOLL|E-?ZPASS", re.I), "6400"),
    (re.compile(r"UBER|LYFT|DELTA|UNITED AIR|AMERICAN AIR|SOUTHWEST|JETBLUE|MARRIOTT|HILTON|HYATT|AIRBNB|EXPEDIA", re.I), "6300"),
    (re.compile(r"RESTAURANT|CAFE|GRILL|DOORDASH|GRUBHUB|STARBUCKS|PANERA|CHIPOTLE|BISTRO|TST\*|DINER", re.I), "6200"),
    (re.compile(r"ADOBE|GOOGLE|MICROSOFT|AWS|AMAZON WEB|ZOOM|SLACK|DROPBOX|INTUIT|GITHUB|OPENAI|ANTHROPIC|STAPLES|OFFICE DEPOT", re.I), "6800"),
    (re.compile(r"GEICO|STATE FARM|PROGRESSIVE|ALLSTATE|INSURANCE|NATIONWIDE", re.I), "6600"),
    (re.compile(r"\bRENT\b|LEASE|PROPERTY MGMT", re.I), "6100"),
    (re.compile(r"GUSTO|ADP|PAYCHEX|PAYROLL", re.I), "6000"),
    (re.compile(r"\bCPA\b|LAW OFFICE|ATTORNEY|LEGAL|ACCOUNTING", re.I), "6900"),
]
NOISE = re.compile(r"^(POS|DEBIT CARD PURCHASE|CHECKCARD|PURCHASE|ACH|DEBIT|CREDIT|SQ \*|PAYPAL \*|ONLINE)\s+", re.I)


def payee_key(description: str) -> str:
    s = description.upper().strip()
    for _ in range(3):
        s = NOISE.sub("", s)
    s = re.sub(r"#?\d{3,}|\d{2}/\d{2}|\b[A-Z]{2}\s*$", " ", s)
    s = re.sub(r"[^A-Z& ]+", " ", s)
    return " ".join(s.split()[:3])


def parse_csv(text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text.strip()))
    cols = {c.lower().strip(): c for c in reader.fieldnames or []}

    def col(*names: str) -> str | None:
        return next((cols[n] for n in names if n in cols), None)

    c_date, c_desc = col("date", "posted date", "transaction date", "posting date"), col("description", "payee", "memo", "name")
    c_amt, c_debit, c_credit = col("amount"), col("debit", "withdrawal", "withdrawals"), col("credit", "deposit", "deposits")
    if not (c_date and c_desc and (c_amt or c_debit or c_credit)):
        raise ValueError(f"could not find date/description/amount columns in {reader.fieldnames}")
    out = []
    for r in reader:
        try:
            if c_amt:
                amt = Decimal(re.sub(r"[^\d.\-]", "", r[c_amt]) or "0")
            else:
                d = Decimal(re.sub(r"[^\d.]", "", r.get(c_debit) or "") or "0")
                c = Decimal(re.sub(r"[^\d.]", "", r.get(c_credit) or "") or "0")
                amt = c - d
        except InvalidOperation:
            continue
        raw = r[c_date].strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y"):
            try:
                when = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                when = None
        if when is None or amt == 0:
            continue
        out.append({"date": when.isoformat(), "description": r[c_desc].strip(), "amount": str(amt)})
    return out


class _Pick(BaseModel):
    index: int
    account: str = Field(description="account code from the chart")
    confidence: float


class _Batch(BaseModel):
    picks: list[_Pick]


def learned_map(conn: sqlite3.Connection, client_id: str) -> dict[str, Counter]:
    votes: dict[str, Counter] = {}
    # Learn from confirmed bank postings: the non-cash side of each entry is the category.
    for r in rows(conn, "SELECT e.memo, p.account_code FROM entries e JOIN postings p ON p.entry_id = e.id "
                        "WHERE e.client_id = ? AND e.source LIKE 'bank%' AND e.reverses IS NULL AND p.line = 1 "
                        "AND NOT EXISTS (SELECT 1 FROM entries r WHERE r.reverses = e.id)", client_id):
        votes.setdefault(payee_key(r["memo"]), Counter())[r["account_code"]] += 1
    return votes


def suggest(conn: sqlite3.Connection, router: Router | None, client_id: str, txns: list[dict[str, Any]],
            cash_account: str = "1000") -> list[dict[str, Any]]:
    accounts = store.accounts(conn, client_id)
    learned = learned_map(conn, client_id)
    existing = {(e["date"], e["memo"]) for e in rows(conn, "SELECT date, memo FROM entries WHERE client_id = ? AND source LIKE 'bank%'",
                                                    client_id)}
    out, unknown = [], []
    for i, t in enumerate(txns):
        key = payee_key(t["description"])
        s = {**t, "index": i, "payee": key, "cash_account": cash_account, "duplicate": (t["date"], t["description"]) in existing}
        inflow = Decimal(t["amount"]) > 0
        if key in learned and learned[key]:
            acct, n = learned[key].most_common(1)[0]
            s.update(account=acct, confidence=min(0.99, 0.8 + 0.05 * n), basis=f"your history: {n} prior {key} transaction(s)")
        elif inflow:
            s.update(account="4000", confidence=0.6, basis="deposit; confirm whether this is revenue, a loan or a transfer")
        else:
            hit = next((code for rx, code in MERCHANTS if rx.search(t["description"]) and code in accounts), None)
            if hit:
                s.update(account=hit, confidence=0.8, basis="known merchant pattern")
            else:
                s.update(account=None, confidence=0.0, basis="unknown payee")
                unknown.append(s)
        out.append(s)
    if unknown and router is not None:
        chart = "\n".join(f"{c} {a['name']} ({a['type']})" for c, a in sorted(accounts.items()) if a["type"] == "expense")
        listing = "\n".join(f"{s['index']}: {s['description']} {s['amount']}" for s in unknown)
        try:
            batch, by = router.structured("classify", system="Categorize bank transactions into the expense chart of accounts. "
                                          "Use only codes from the chart. Low confidence if unsure.",
                                          user=f"Chart:\n{chart}\n\nTransactions:\n{listing}", schema=_Batch, client_id=client_id)
            for p in batch.picks:
                if 0 <= p.index < len(out) and p.account in accounts and out[p.index]["account"] is None:
                    out[p.index].update(account=p.account, confidence=min(p.confidence, 0.75), basis=f"AI suggestion ({by})")
        except Unavailable:
            pass
    for s in out:
        s["tax_treatment"] = ACCOUNT_TREATMENT.get(s["account"] or "")
        s["account_name"] = accounts.get(s["account"] or "", {}).get("name")
    return out


def post_confirmed(conn: sqlite3.Connection, client_id: str, confirmed: list[dict[str, Any]], actor: str, role: str) -> list[int]:
    ids = []
    for t in confirmed:
        amt = Decimal(t["amount"])
        acct = t["account"]
        cash = t.get("cash_account", "1000")
        treatment = t.get("tax_treatment") or ACCOUNT_TREATMENT.get(acct)
        lines = [store.Line(cash, amt), store.Line(acct, -amt, treatment)]
        ids.append(store.post(conn, client_id, date.fromisoformat(t["date"]), t["description"], lines,
                              source=f"bank:{t.get('payee') or payee_key(t['description'])}", actor=actor, role=role))
    return ids
