"""Built-in CRM: contacts, engagements, tasks / client requests, messages and the deadline calendar."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from .. import audit
from ..db import one, rows
from ..expr import ExprError, evaluate

STAGES = ["lead", "proposal", "engaged", "in_progress", "client_review", "filed", "closed"]


# -- contacts & engagements --------------------------------------------------------------------

def add_contact(conn, client_id: str, name: str, email: str | None = None, phone: str | None = None,
                role: str | None = None, primary: bool = False) -> int:
    cur = conn.execute("INSERT INTO contacts (client_id, name, email, phone, role, is_primary) VALUES (?,?,?,?,?,?)",
                       (client_id, name, email, phone, role, int(primary)))
    return int(cur.lastrowid)


def add_engagement(conn, client_id: str, type: str, tax_year: int | None, owner: str | None, due_date: str | None = None,
                   fee: str | None = None, stage: str = "engaged", actor: str = "cpa") -> int:
    cur = conn.execute("INSERT INTO engagements (client_id, type, tax_year, stage, owner, due_date, fee, created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (client_id, type, tax_year, stage, owner, due_date, fee, audit.now()))
    audit.record(conn, actor, "cpa", "engagement.created", {"engagement_id": cur.lastrowid, "type": type, "tax_year": tax_year},
                 client_id=client_id)
    return int(cur.lastrowid)


def move_engagement(conn, engagement_id: int, stage: str, actor: str, role: str = "cpa") -> None:
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}")
    e = one(conn, "SELECT * FROM engagements WHERE id = ?", engagement_id)
    if not e:
        raise KeyError(engagement_id)
    conn.execute("UPDATE engagements SET stage = ? WHERE id = ?", (stage, engagement_id))
    audit.record(conn, actor, role, "engagement.stage", {"engagement_id": engagement_id, "from": e["stage"], "to": stage},
                 client_id=e["client_id"])


def pipeline(conn) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in STAGES}
    for e in rows(conn, "SELECT e.*, c.name AS client_name, "
                        "(SELECT COUNT(*) FROM tasks t WHERE t.engagement_id = e.id AND t.status = 'open') AS open_tasks "
                        "FROM engagements e JOIN clients c ON c.id = e.client_id ORDER BY e.due_date"):
        out.setdefault(e["stage"], []).append(e)
    return out


# -- tasks & client requests -------------------------------------------------------------------

def create_task(conn, *, title: str, assignee: str, source: str, client_id: str | None = None, detail: str = "",
                due: str | None = None, engagement_id: int | None = None, dedupe_key: str | None = None) -> int | None:
    if dedupe_key and one(conn, "SELECT id FROM tasks WHERE dedupe_key = ?", dedupe_key):
        return None
    cur = conn.execute(
        "INSERT INTO tasks (client_id, engagement_id, title, detail, assignee, due, source, dedupe_key, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (client_id, engagement_id, title, detail, assignee, due, source, dedupe_key, audit.now()))
    audit.record(conn, source, "agent" if source != "cpa" else "cpa", "task.created",
                 {"task_id": cur.lastrowid, "title": title, "assignee": assignee, "due": due}, client_id=client_id)
    return int(cur.lastrowid)


def complete_task(conn, task_id: int, actor: str, role: str, note: str = "") -> None:
    t = one(conn, "SELECT * FROM tasks WHERE id = ?", task_id)
    if not t:
        raise KeyError(task_id)
    if role == "client" and t["assignee"] != "client":
        raise PermissionError("clients can only complete their own requests")
    conn.execute("UPDATE tasks SET status = 'done', done_at = ?, done_note = ? WHERE id = ?", (audit.now(), note, task_id))
    audit.record(conn, actor, role, "task.done", {"task_id": task_id, "title": t["title"], "note": note}, client_id=t["client_id"])


def tasks(conn, client_id: str | None = None, assignee: str | None = None, status: str = "open") -> list[dict[str, Any]]:
    sql = "SELECT t.*, c.name AS client_name FROM tasks t LEFT JOIN clients c ON c.id = t.client_id WHERE t.status = ?"
    args: list[Any] = [status]
    if client_id:
        sql += " AND t.client_id = ?"
        args.append(client_id)
    if assignee:
        sql += " AND t.assignee = ?"
        args.append(assignee)
    return rows(conn, sql + " ORDER BY COALESCE(t.due, '9999'), t.id", *args)


# -- messages ----------------------------------------------------------------------------------

def draft_message(conn, *, client_id: str | None, subject: str, body: str, source: str, to_addr: str | None = None,
                  dedupe_key: str | None = None) -> int | None:
    if dedupe_key and one(conn, "SELECT id FROM messages WHERE dedupe_key = ?", dedupe_key):
        return None
    if to_addr is None and client_id:
        c = one(conn, "SELECT email FROM contacts WHERE client_id = ? ORDER BY is_primary DESC LIMIT 1", client_id)
        to_addr = c["email"] if c else None
    cur = conn.execute("INSERT INTO messages (client_id, direction, channel, to_addr, subject, body, status, source, dedupe_key, at) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (client_id, "out", "email", to_addr, subject, body, "draft", source, dedupe_key, audit.now()))
    audit.record(conn, source, "agent", "message.drafted", {"message_id": cur.lastrowid, "subject": subject}, client_id=client_id)
    return int(cur.lastrowid)


def send_message(conn, message_id: int, actor: str, role: str = "cpa") -> dict[str, Any]:
    """Sends through SMTP when configured (AGENTLEDGER_SMTP_*); otherwise stays a draft. Never silent."""
    import os
    import smtplib
    from email.message import EmailMessage

    m = one(conn, "SELECT * FROM messages WHERE id = ?", message_id)
    if not m or m["status"] != "draft":
        raise ValueError("only drafts can be sent")
    host = os.environ.get("AGENTLEDGER_SMTP_HOST")
    if not host or not m["to_addr"]:
        return {"sent": False, "reason": "SMTP not configured or no recipient; message remains a draft"}
    msg = EmailMessage()
    msg["From"] = os.environ.get("AGENTLEDGER_SMTP_FROM", os.environ.get("AGENTLEDGER_SMTP_USER", ""))
    msg["To"], msg["Subject"] = m["to_addr"], m["subject"]
    msg.set_content(m["body"])
    with smtplib.SMTP(host, int(os.environ.get("AGENTLEDGER_SMTP_PORT", "587"))) as s:
        s.starttls()
        if os.environ.get("AGENTLEDGER_SMTP_USER"):
            s.login(os.environ["AGENTLEDGER_SMTP_USER"], os.environ.get("AGENTLEDGER_SMTP_PASSWORD", ""))
        s.send_message(msg)
    conn.execute("UPDATE messages SET status = 'sent', at = ? WHERE id = ?", (audit.now(), message_id))
    audit.record(conn, actor, role, "message.sent", {"message_id": message_id, "to": m["to_addr"]}, client_id=m["client_id"])
    return {"sent": True}


# -- compliance deadline calendar ----------------------------------------------------------------

def _roll(d: date) -> date:
    """IRC §7503: a deadline falling on a weekend moves to the next business day (holidays: see notes)."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def deadlines(conn, catalog_path: Path, client_id: str, today: date | None = None, horizon_days: int = 120) -> list[dict[str, Any]]:
    from ..ledger.store import get_client

    today = today or date.today()
    c = get_client(conn, client_id)
    facts = {"kind": c["kind"], "entity_type": c["entity_type"] or "", "formed_under": c["formed_under"], "domain": c["domain"],
             **c["facts"]}
    out = []
    for d in yaml.safe_load(catalog_path.read_text(encoding="utf-8")).get("deadlines", []):
        try:
            if not evaluate(d["applies_when"], facts):
                continue
        except ExprError:
            continue
        for year in (today.year, today.year + 1):
            for mmdd in d["dates"]:
                mm, dd = (int(x) for x in mmdd.split("-"))
                due = _roll(date(year, mm, dd))
                if today <= due <= today + timedelta(days=horizon_days):
                    out.append({"id": d["id"], "title": d["title"], "due": due.isoformat(), "days": (due - today).days,
                                "form": d.get("form"), "citation": d.get("citation"), "client_id": client_id})
    return sorted(out, key=lambda x: x["due"])
