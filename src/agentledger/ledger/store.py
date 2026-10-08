"""Append-only double-entry ledger with dual (book + tax) attributes per posting."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from .. import audit
from ..calc.engine import D
from ..calc.federal import Asset
from ..db import unit_of_work, GENESIS, chain_hash, count, is_pg, one, rows

# Tax treatments a posting can carry. The M-1 engine maps each to KB rules.
TREATMENTS = {
    "meals": "Business meals (IRC §274(n))",
    "entertainment": "Entertainment (IRC §274(a))",
    "fines_penalties": "Government fines and penalties (IRC §162(f))",
    "officer_life_insurance": "Officer life insurance premiums (IRC §264)",
    "municipal_interest": "Tax-exempt municipal bond interest (IRC §103)",
    "depreciation": "Book depreciation",
    "federal_income_tax": "Federal income tax expense per books",
    "travel": "Travel (substantiation under IRC §274(d))",
    "vehicle": "Vehicle expense",
}


DEFAULT_TREATMENT = {
    "meals": "meals", "entertainment": "entertainment", "travel": "travel", "vehicle": "vehicle",
    "penalties and fines": "fines_penalties", "officer life insurance": "officer_life_insurance",
    "tax-exempt interest income": "municipal_interest", "depreciation expense": "depreciation",
    "federal income tax expense": "federal_income_tax",
}


class LedgerError(ValueError):
    pass


class ClosedPeriod(LedgerError):
    """A posting dated inside a closed period."""


@dataclass(frozen=True)
class Line:
    account: str
    amount: Decimal  # debit positive, credit negative
    tax_treatment: str | None = None


def add_client(conn: sqlite3.Connection, *, id: str, name: str, kind: str, entity_type: str | None = None,
               formed_under: str = "domestic", tax_id_last4: str | None = None, emails: Iterable[str] = (),
               aliases: Iterable[str] = (), consent_7216_at: str | None = None, domain: str = "general",
               facts: dict[str, Any] | None = None, actor: str = "system") -> None:
    conn.execute(
        "INSERT INTO clients (id, name, kind, entity_type, formed_under, tax_id_last4, emails, aliases, consent_7216_at, domain, facts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (id, name, kind, entity_type, formed_under, tax_id_last4, json.dumps([e.lower() for e in emails]),
         json.dumps(list(aliases)), consent_7216_at, domain, json.dumps(facts or {})),
    )
    audit.record(conn, actor, "cpa", "client.created", {"name": name, "kind": kind, "domain": domain,
                                                        "consent_7216": bool(consent_7216_at)}, client_id=id)


def get_client(conn: sqlite3.Connection, client_id: str) -> dict[str, Any]:
    c = one(conn, "SELECT * FROM clients WHERE id = ?", client_id)
    if not c:
        raise LedgerError(f"unknown client {client_id}")
    c["emails"] = json.loads(c["emails"])
    c["aliases"] = json.loads(c["aliases"])
    c["facts"] = json.loads(c["facts"])
    return c


def list_clients(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out = rows(conn, "SELECT * FROM clients ORDER BY name")
    for c in out:
        c["emails"] = json.loads(c["emails"])
        c["aliases"] = json.loads(c["aliases"])
        c["facts"] = json.loads(c["facts"])
    return out


def add_account(conn: sqlite3.Connection, client_id: str, code: str, name: str, type: str) -> None:
    conn.execute("INSERT INTO accounts (client_id, code, name, type) VALUES (?,?,?,?)", (client_id, code, name, type))


def accounts(conn: sqlite3.Connection, client_id: str) -> dict[str, dict[str, Any]]:
    return {a["code"]: a for a in rows(conn, "SELECT * FROM accounts WHERE client_id = ?", client_id)}


def post(conn: sqlite3.Connection, client_id: str, on: date, memo: str, lines: list[Line], *, source: str,
         actor: str, role: str = "cpa", reverses: int | None = None, document_id: str | None = None,
         created_at: str | None = None) -> int:
    if len(lines) < 2:
        raise LedgerError("an entry needs at least two postings")
    total = sum((D(l.amount) for l in lines), Decimal(0))
    if total != 0:
        raise LedgerError(f"entry does not balance (off by {total})")
    known = accounts(conn, client_id)
    for l in lines:
        if l.account not in known:
            raise LedgerError(f"unknown account {l.account} for {client_id}")
        if l.tax_treatment and l.tax_treatment not in TREATMENTS:
            raise LedgerError(f"unknown tax treatment {l.tax_treatment}")
    # Accounts with an unambiguous tax character tag their postings by default, so the book-to-tax
    # bridge never depends on someone remembering to tag a fine or a meal.
    lines = [l if l.tax_treatment else Line(l.account, l.amount, DEFAULT_TREATMENT.get(known[l.account]["name"].lower()))
             for l in lines]
    if is_pg(conn):
        # The database posts: balance at commit, open period, hash chain, outbox and audit in one transaction.
        lines_json = json.dumps([{"account": l.account, "amount": str(D(l.amount)), "tax_treatment": l.tax_treatment} for l in lines])
        with unit_of_work(conn):
            return count(conn, "SELECT post_journal(?, ?, ?, ?, ?, ?, ?::jsonb, NULL, ?, ?) AS id",
                         client_id, on.isoformat(), memo, source, actor, role, lines_json, reverses, document_id)
    created_at = created_at or audit.now()
    with unit_of_work(conn):
        # Closed periods are enforced inside the posting transaction, not detected afterwards.
        closed = conn.execute("SELECT closed_through FROM clients WHERE id = ?", (client_id,)).fetchone()
        if closed and closed["closed_through"] and on <= date.fromisoformat(closed["closed_through"]):
            raise ClosedPeriod(f"{client_id}: books are closed through {closed['closed_through']}; "
                               f"an entry dated {on.isoformat()} needs an authorized reopen first")
        last = conn.execute("SELECT hash FROM entries WHERE client_id = ? ORDER BY id DESC LIMIT 1", (client_id,)).fetchone()
        prev = last["hash"] if last else GENESIS
        body = _entry_body(client_id, on.isoformat(), memo, source, actor, created_at, reverses, document_id,
                           [(l.account, str(D(l.amount)), l.tax_treatment) for l in lines])
        h = chain_hash(prev, body)
        cur = conn.execute(
            "INSERT INTO entries (client_id, date, memo, source, created_by, created_at, reverses, document_id, prev_hash, hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (client_id, on.isoformat(), memo, source, actor, created_at, reverses, document_id, prev, h),
        )
        entry_id = int(cur.lastrowid or 0)
        for i, l in enumerate(lines):
            conn.execute(
                "INSERT INTO postings (entry_id, line, account_code, amount, tax_treatment) VALUES (?,?,?,?,?)",
                (entry_id, i, l.account, str(D(l.amount)), l.tax_treatment),
            )
        audit.record(conn, actor, role, "ledger.posted", {"entry_id": entry_id, "memo": memo, "source": source,
                                                         "amount": str(sum(D(l.amount) for l in lines if D(l.amount) > 0))},
                     client_id=client_id)
    return entry_id


CLOSE_ROLES = ("cpa",)


def close_period(conn: sqlite3.Connection, client_id: str, through: date, *, actor: str, role: str) -> None:
    if role not in CLOSE_ROLES:
        raise PermissionError("closing the books needs a CPA")
    with unit_of_work(conn):
        prev = get_client(conn, client_id)["closed_through"]
        if prev and through < date.fromisoformat(prev):
            raise LedgerError("closing to an earlier date is a reopen; use reopen_period")
        if is_pg(conn):  # the function checks the role again, updates, audits and emits the event
            conn.execute("SELECT close_period(?, ?, ?, ?)", (client_id, through.isoformat(), actor, role))
            return
        conn.execute("UPDATE clients SET closed_through = ? WHERE id = ?", (through.isoformat(), client_id))
        audit.record(conn, actor, role, "period.closed", {"through": through.isoformat(), "previous": prev}, client_id=client_id)


def reopen_period(conn: sqlite3.Connection, client_id: str, back_to: date | None, *, actor: str, role: str, reason: str) -> None:
    """Controlled reopen: a CPA moves the close date back, with a reason, in the audit trail."""
    if role not in CLOSE_ROLES:
        raise PermissionError("reopening closed books needs a CPA")
    if not reason.strip():
        raise LedgerError("a reopen needs a reason")
    with unit_of_work(conn):
        if is_pg(conn):
            conn.execute("SELECT reopen_period(?, ?, ?, ?, ?)",
                         (client_id, back_to.isoformat() if back_to else None, actor, role, reason))
            return
        prev = get_client(conn, client_id)["closed_through"]
        conn.execute("UPDATE clients SET closed_through = ? WHERE id = ?", (back_to.isoformat() if back_to else None, client_id))
        audit.record(conn, actor, role, "period.reopened", {"from": prev, "to": back_to.isoformat() if back_to else None,
                                                            "reason": reason}, client_id=client_id)


def reverse(conn: sqlite3.Connection, client_id: str, entry_id: int, on: date, reason: str, actor: str, role: str = "cpa") -> int:
    """Corrections are new entries that reverse old ones; nothing is edited in place."""
    original = entry(conn, client_id, entry_id)
    if original["reversed_by"]:
        raise LedgerError(f"entry {entry_id} already reversed by {original['reversed_by']}")
    lines = [Line(p["account_code"], -D(p["amount"]), p["tax_treatment"]) for p in original["postings"]]
    return post(conn, client_id, on, f"Reversal of #{entry_id}: {reason}", lines, source="reversal", actor=actor,
                role=role, reverses=entry_id)


def _entry_body(client_id, on, memo, source, actor, created_at, reverses, document_id, lines) -> dict[str, Any]:
    return {"client_id": client_id, "date": on, "memo": memo, "source": source, "created_by": actor,
            "created_at": created_at, "reverses": reverses, "document_id": document_id, "lines": lines}


def entry(conn: sqlite3.Connection, client_id: str, entry_id: int) -> dict[str, Any]:
    e = one(conn, "SELECT * FROM entries WHERE id = ? AND client_id = ?", entry_id, client_id)
    if not e:
        raise LedgerError(f"no entry {entry_id} for {client_id}")
    e["postings"] = rows(conn, "SELECT * FROM postings WHERE entry_id = ? ORDER BY line", entry_id)
    rev = one(conn, "SELECT id FROM entries WHERE reverses = ?", entry_id)
    e["reversed_by"] = rev["id"] if rev else None
    return e


def entries(conn: sqlite3.Connection, client_id: str, start: date | None = None, end: date | None = None,
            limit: int = 1000) -> list[dict[str, Any]]:
    sql = "SELECT * FROM entries WHERE client_id = ?"
    args: list[Any] = [client_id]
    if start:
        sql += " AND date >= ?"
        args.append(start.isoformat())
    if end:
        sql += " AND date <= ?"
        args.append(end.isoformat())
    sql += " ORDER BY date, id LIMIT ?"
    args.append(limit)
    out = rows(conn, sql, *args)
    if out:
        ids = [e["id"] for e in out]
        marks = ",".join("?" * len(ids))
        by_entry: dict[int, list] = {}
        for p in rows(conn, f"SELECT * FROM postings WHERE entry_id IN ({marks}) ORDER BY entry_id, line", *ids):
            by_entry.setdefault(p["entry_id"], []).append(p)
        for e in out:
            e["postings"] = by_entry.get(e["id"], [])
    return out


def postings_in(conn: sqlite3.Connection, client_id: str, start: date, end: date) -> list[dict[str, Any]]:
    return rows(
        conn,
        "SELECT p.*, e.date, e.memo, e.created_at, e.id AS entry_id, a.type AS account_type, a.name AS account_name, "
        "COALESCE(e.document_id, (SELECT ed.document_id FROM entry_documents ed WHERE ed.entry_id = e.id LIMIT 1)) AS document_id "
        "FROM postings p JOIN entries e ON e.id = p.entry_id "
        "JOIN accounts a ON a.client_id = e.client_id AND a.code = p.account_code "
        "WHERE e.client_id = ? AND e.date BETWEEN ? AND ? ORDER BY e.date, e.id, p.line",
        client_id, start.isoformat(), end.isoformat(),
    )


def balances(conn: sqlite3.Connection, client_id: str, start: date, end: date) -> list[dict[str, Any]]:
    acc = accounts(conn, client_id)
    sums: dict[str, Decimal] = {}
    for p in postings_in(conn, client_id, start, end):
        sums[p["account_code"]] = sums.get(p["account_code"], Decimal(0)) + D(p["amount"])
    return [{"code": c, "name": acc[c]["name"], "type": acc[c]["type"], "balance": str(sums[c])} for c in sorted(sums)]


def verify_chain(conn: sqlite3.Connection, client_id: str) -> dict[str, Any]:
    if is_pg(conn):  # recomputed inside the database from the stored rows
        r = conn.execute("SELECT ok, checked, broken_at FROM verify_chain(?)", (client_id,)).fetchone()
        head = conn.execute("SELECT hash FROM entries WHERE client_id = ? ORDER BY id DESC LIMIT 1", (client_id,)).fetchone()
        out = {"ok": r["ok"], "checked": r["checked"]}
        return {**out, "broken_at": r["broken_at"]} if not r["ok"] else {**out, "head": head["hash"] if head else "genesis"}
    prev = GENESIS
    n = 0
    for e in rows(conn, "SELECT * FROM entries WHERE client_id = ? ORDER BY id", client_id):
        lines = [(p["account_code"], p["amount"], p["tax_treatment"])
                 for p in rows(conn, "SELECT * FROM postings WHERE entry_id = ? ORDER BY line", e["id"])]
        body = _entry_body(client_id, e["date"], e["memo"], e["source"], e["created_by"], e["created_at"],
                           e["reverses"], e["document_id"], lines)
        if e["prev_hash"] != prev or chain_hash(prev, body) != e["hash"]:
            return {"ok": False, "broken_at": e["id"], "checked": n}
        prev = e["hash"]
        n += 1
    return {"ok": True, "checked": n, "head": prev}


def add_asset(conn: sqlite3.Connection, client_id: str, a: Asset, description: str) -> None:
    conn.execute(
        "INSERT INTO assets (client_id, id, description, cost, acquired, placed_in_service, recovery_years, book_life_years, sec179_elected) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (client_id, a.id, description, str(a.cost), a.acquired.isoformat(), a.placed_in_service.isoformat(),
         a.recovery_years, a.book_life_years, str(a.sec179_elected)),
    )


def assets(conn: sqlite3.Connection, client_id: str) -> list[tuple[Asset, str]]:
    out = []
    for r in rows(conn, "SELECT * FROM assets WHERE client_id = ? ORDER BY placed_in_service", client_id):
        out.append((Asset(r["id"], D(r["cost"]), date.fromisoformat(r["acquired"]), date.fromisoformat(r["placed_in_service"]),
                          r["recovery_years"], r["book_life_years"], D(r["sec179_elected"])), r["description"]))
    return out


def to_beancount(conn: sqlite3.Connection, client_id: str) -> str:
    """Export to Beancount (open-source plain-text ledger) for interoperability."""
    prefix = {"asset": "Assets", "liability": "Liabilities", "equity": "Equity", "revenue": "Income", "expense": "Expenses"}
    acc = accounts(conn, client_id)

    def name(code: str) -> str:
        a = acc[code]
        words = "".join(w.capitalize() for w in "".join(ch if ch.isalnum() else " " for ch in a["name"]).split())
        return f"{prefix[a['type']]}:{words or code}-{code}"

    out = [f'option "title" "{get_client(conn, client_id)["name"]}"', 'option "operating_currency" "USD"', ""]
    for code in sorted(acc):
        out.append(f"2000-01-01 open {name(code)}")
    out.append("")
    for e in entries(conn, client_id, limit=100000):
        out.append(f'{e["date"]} * "{e["memo"].replace(chr(34), chr(39))}"')
        out.append(f'  agentledger-id: "{e["id"]}"')
        out.append(f'  hash: "{e["hash"]}"')
        for p in e["postings"]:
            meta = f'  ; tax: {p["tax_treatment"]}' if p["tax_treatment"] else ""
            out.append(f"  {name(p['account_code'])}  {D(p['amount']):.2f} USD{meta}")
        out.append("")
    return "\n".join(out)
