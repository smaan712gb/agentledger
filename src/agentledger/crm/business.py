"""The small business's own CRM: customers, vendors, deals, invoices, AR, and 1099 readiness.

Everything here posts through the same ledger, so sales, receivables and vendor spend are
always in the books the CPA sees — no sync, no re-keying.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .. import audit
from ..calc.engine import Ctx
from ..calc.federal import form_1099_nec_required
from ..db import one, rows, run_command
from ..kb.store import KnowledgeBase
from ..ledger import store

DEAL_STAGES = ["lead", "qualified", "proposal", "won", "lost"]
CORPORATE = {"c_corp", "s_corp", "corporation"}  # payments to corporations are generally exempt from 1099-NEC


def add_party(conn, client_id: str, kind: str, name: str, *, email: str | None = None, phone: str | None = None,
              entity_type: str | None = None, tin_last4: str | None = None, w9_on_file: bool = False, terms_days: int = 30,
              actor: str = "client", role: str = "client") -> int:
    cur = conn.execute("INSERT INTO parties (client_id, kind, name, email, phone, tin_last4, entity_type, w9_on_file, terms_days, created_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (client_id, kind, name, email, phone, tin_last4, entity_type, int(w9_on_file), terms_days, audit.now()))
    audit.record(conn, actor, role, "party.created", {"party_id": cur.lastrowid, "kind": kind, "name": name}, client_id=client_id)
    return int(cur.lastrowid)


def parties(conn, client_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    if kind:
        return rows(conn, "SELECT * FROM parties WHERE client_id = ? AND kind IN (?, 'both') ORDER BY name", client_id, kind)
    return rows(conn, "SELECT * FROM parties WHERE client_id = ? ORDER BY name", client_id)


def _own_party(conn, client_id: str, party_id: int | None, kinds: tuple[str, ...]) -> dict[str, Any] | None:
    """A referenced party must belong to the same business (never trust a caller-supplied id)."""
    if party_id is None:
        return None
    party = one(conn, "SELECT * FROM parties WHERE id = ? AND client_id = ?", party_id, client_id)
    if not party or party["kind"] not in (*kinds, "both"):
        raise KeyError(f"{' or '.join(kinds)} {party_id} not found for this business")
    return party


def add_deal(conn, client_id: str, title: str, value: Any, party_id: int | None = None, stage: str = "lead",
             expected_close: str | None = None, actor: str = "client", role: str = "client") -> int:
    if stage not in DEAL_STAGES:
        raise ValueError(f"stage must be one of {DEAL_STAGES}")
    _own_party(conn, client_id, party_id, ("customer",))
    cur = conn.execute("INSERT INTO deals (client_id, party_id, title, stage, value, expected_close, created_at) VALUES (?,?,?,?,?,?,?)",
                       (client_id, party_id, title, stage, str(value), expected_close, audit.now()))
    audit.record(conn, actor, role, "deal.created", {"deal_id": cur.lastrowid, "title": title, "value": str(value)}, client_id=client_id)
    return int(cur.lastrowid)


def move_deal(conn, deal_id: int, stage: str, actor: str, role: str = "client") -> None:
    if stage not in DEAL_STAGES:
        raise ValueError(f"stage must be one of {DEAL_STAGES}")
    d = one(conn, "SELECT * FROM deals WHERE id = ?", deal_id)
    conn.execute("UPDATE deals SET stage = ? WHERE id = ?", (stage, deal_id))
    audit.record(conn, actor, role, "deal.stage", {"deal_id": deal_id, "from": d["stage"], "to": stage}, client_id=d["client_id"])


def deal_board(conn, client_id: str) -> dict[str, Any]:
    board = {s: [] for s in DEAL_STAGES}
    for d in rows(conn, "SELECT d.*, p.name AS party_name FROM deals d LEFT JOIN parties p ON p.id = d.party_id "
                        "WHERE d.client_id = ? ORDER BY d.expected_close", client_id):
        board[d["stage"]].append(d)
    weights = {"lead": Decimal("0.1"), "qualified": Decimal("0.3"), "proposal": Decimal("0.6"), "won": Decimal(1), "lost": Decimal(0)}
    forecast = sum((Decimal(d["value"]) * weights[s] for s, ds in board.items() for d in ds if s != "won"), Decimal(0))
    return {"stages": board, "weighted_open_pipeline": str(forecast.quantize(Decimal("0.01")))}


def create_invoice(conn, client_id: str, party_id: int, number: str, amount: Any, description: str, *,
                   sales_tax: Any = 0, issued: date | None = None, actor: str = "client", role: str = "client",
                   command_id: str | None = None) -> int:
    """Invoice, receivable posting and audit record commit together or not at all. A retry with the same
    command_id returns the original invoice instead of posting again."""
    issued = issued or date.today()
    amt, tax = Decimal(str(amount)), Decimal(str(sales_tax))
    payload = {"party_id": party_id, "number": number, "amount": str(amt), "sales_tax": str(tax), "description": description,
               "issued": issued.isoformat()}

    def do() -> int:
        party = _own_party(conn, client_id, party_id, ("customer",))
        if one(conn, "SELECT id FROM invoices WHERE client_id = ? AND number = ?", client_id, number):
            raise ValueError(f"invoice number {number} already exists for this business")
        lines = [store.Line("1100", amt + tax), store.Line("4000", -amt)]
        if tax:
            lines.append(store.Line("2200", -tax))
        entry_id = store.post(conn, client_id, issued, f"Invoice {number} to {party['name']}: {description}", lines,
                              source=f"invoice:{number}", actor=actor, role=role)
        due = issued + timedelta(days=party["terms_days"])
        cur = conn.execute("INSERT INTO invoices (client_id, party_id, number, issued, due, amount, sales_tax, description, entry_id) "
                           "VALUES (?,?,?,?,?,?,?,?,?)",
                           (client_id, party_id, number, issued.isoformat(), due.isoformat(), str(amt), str(tax), description, entry_id))
        audit.record(conn, actor, role, "invoice.created", {"invoice_id": cur.lastrowid, "number": number, "entry_id": entry_id},
                     client_id=client_id)
        return int(cur.lastrowid)

    return run_command(conn, f"client:{client_id}", command_id, "invoice.create", payload, do)


def record_payment(conn, client_id: str, invoice_id: int, on: date | None = None, actor: str = "client", role: str = "client",
                   command_id: str | None = None) -> int:
    on = on or date.today()

    def do() -> int:
        inv = one(conn, "SELECT * FROM invoices WHERE id = ? AND client_id = ?", invoice_id, client_id)
        if not inv or inv["status"] != "open":
            raise ValueError("invoice is not open")
        total = Decimal(inv["amount"]) + Decimal(inv["sales_tax"])
        entry_id = store.post(conn, client_id, on, f"Payment received invoice {inv['number']}",
                              [store.Line("1000", total), store.Line("1100", -total)], source=f"payment:{inv['number']}",
                              actor=actor, role=role)
        cur = conn.execute("UPDATE invoices SET status = 'paid', paid_entry_id = ? WHERE id = ? AND status = 'open'",
                           (entry_id, invoice_id))
        if cur.rowcount != 1:
            raise ValueError("invoice was paid concurrently")
        return entry_id

    return run_command(conn, f"client:{client_id}", command_id, "invoice.pay", {"invoice_id": invoice_id, "on": on.isoformat()}, do)


def ar_aging(conn, client_id: str, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    buckets = {"current": Decimal(0), "1-30": Decimal(0), "31-60": Decimal(0), "61-90": Decimal(0), "90+": Decimal(0)}
    items = []
    for inv in rows(conn, "SELECT i.*, p.name AS party_name, p.email FROM invoices i JOIN parties p ON p.id = i.party_id "
                          "WHERE i.client_id = ? AND i.status = 'open' ORDER BY i.due", client_id):
        late = (today - date.fromisoformat(inv["due"])).days
        b = "current" if late <= 0 else "1-30" if late <= 30 else "31-60" if late <= 60 else "61-90" if late <= 90 else "90+"
        total = Decimal(inv["amount"]) + Decimal(inv["sales_tax"])
        buckets[b] += total
        items.append({**inv, "days_late": max(0, late), "bucket": b, "total": str(total)})
    return {"buckets": {k: str(v) for k, v in buckets.items()}, "invoices": items}


def pay_vendor(conn, client_id: str, party_id: int, amount: Any, expense_account: str, memo: str, *, on: date | None = None,
               tax_treatment: str | None = None, actor: str = "client", role: str = "client", command_id: str | None = None) -> int:
    amt = Decimal(str(amount))
    on = on or date.today()

    def do() -> int:
        party = _own_party(conn, client_id, party_id, ("vendor",))
        return store.post(conn, client_id, on, f"{party['name']}: {memo}",
                          [store.Line(expense_account, amt, tax_treatment), store.Line("1000", -amt)],
                          source=f"vendor:{party_id}", actor=actor, role=role)

    return run_command(conn, f"client:{client_id}", command_id, "vendor.pay",
                       {"party_id": party_id, "amount": str(amt), "account": expense_account, "memo": memo, "on": on.isoformat()}, do)


def vendor_1099_status(conn, kb: KnowledgeBase, client_id: str, year: int) -> list[dict[str, Any]]:
    """Which vendors will need a 1099-NEC under the threshold in force, and who is missing a W-9."""
    out = []
    for v in parties(conn, client_id, "vendor"):
        paid = sum((Decimal(r["amount"]) for r in rows(
            conn, "SELECT p.amount FROM entries e JOIN postings p ON p.entry_id = e.id JOIN accounts a "
                  "ON a.client_id = e.client_id AND a.code = p.account_code "
                  "WHERE e.client_id = ? AND e.source = ? AND a.type = 'expense' AND e.date BETWEEN ? AND ?",
            client_id, f"vendor:{v['id']}", f"{year}-01-01", f"{year}-12-31")), Decimal(0))
        ctx = Ctx(kb)
        exempt = (v["entity_type"] or "").lower() in CORPORATE
        required = (not exempt) and form_1099_nec_required(ctx, paid, year)
        out.append({"party_id": v["id"], "name": v["name"], "paid": str(paid), "exempt_corporation": exempt,
                    "requires_1099": required, "w9_on_file": bool(v["w9_on_file"]),
                    "action": ("Collect W-9 now" if required and not v["w9_on_file"] else "File 1099-NEC by Jan 31" if required else ""),
                    "threshold_rule": ctx.sources()[0] if ctx.sources() else None})
    return sorted(out, key=lambda x: (not x["requires_1099"], x["name"]))
