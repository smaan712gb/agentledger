"""Automation engine: the audit trail is the event bus.

Every meaningful event is already appended to the hash-chained audit log, so automations
simply consume it from a cursor: when an event matches a rule's trigger and condition, its
actions run. Actions are a small, safe vocabulary; anything that leaves the firm (email) is
drafted, never sent, unless explicitly configured.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from .. import audit
from ..db import one, rows
from ..expr import ExprError, evaluate, names_used
from . import core as crm

TRIGGERS = {
    "document.received": "A document arrived (any channel)", "finding.raised": "An integrity finding was raised",
    "proposal.submitted": "An agent proposed a change", "proposal.adopted": "A change was adopted",
    "ai.answer": "The AI answered a question", "task.done": "A task or client request was completed",
    "client.created": "A new client was added", "engagement.stage": "An engagement moved stage",
    "ledger.posted": "A journal entry was posted", "schedule.daily": "Once a day",
}
ACTIONS = {"create_task", "draft_message", "run_agent", "deadline_scan", "request_missing_facts", "webhook"}


class Action(BaseModel):
    type: str
    assignee: Literal["cpa", "client", "agent"] | None = None
    title: str | None = None
    detail: str | None = None
    due_in_days: int | None = None
    subject: str | None = None
    body: str | None = None
    agent: str | None = None
    within_days: int | None = None
    url_env: str | None = None  # webhook destination comes from an env var, never from config


class Automation(BaseModel):
    id: str
    title: str
    trigger: str
    when: str | None = None
    actions: list[Action]
    enabled: bool = True


def load(path: Path) -> list[Automation]:
    if not path.exists():
        return []
    return [Automation.model_validate(a) for a in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("automations", [])]


def validate(a: Automation) -> list[dict[str, Any]]:
    """Mechanical checks for an (AI-drafted) automation before a human approves it."""
    out = [{"name": "trigger_known", "ok": a.trigger in TRIGGERS, "detail": a.trigger}]
    for i, act in enumerate(a.actions):
        out.append({"name": f"action{i}_known", "ok": act.type in ACTIONS, "detail": act.type})
        if act.type == "create_task":
            out.append({"name": f"action{i}_fields", "ok": bool(act.title and act.assignee), "detail": "title and assignee required"})
    if a.when:
        try:
            names = names_used(a.when)
            allowed = {"action", "actor", "role", "client_name", "client_domain", "client_kind"}
            bad = [n for n in names if not (n in allowed or n.startswith("payload_"))]
            out.append({"name": "condition_names", "ok": not bad, "detail": f"unknown names {bad}" if bad else "ok"})
        except SyntaxError as e:
            out.append({"name": "condition_parses", "ok": False, "detail": str(e)})
    return out


def _env(conn, event: dict[str, Any]) -> dict[str, Any]:
    env: dict[str, Any] = {"action": event["action"], "actor": event["actor"], "role": event["role"]}
    for k, v in event["payload"].items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            env[f"payload_{k}"] = v
    if event.get("client_id"):
        c = one(conn, "SELECT name, domain, kind FROM clients WHERE id = ?", event["client_id"])
        if c:
            env.update(client_name=c["name"], client_domain=c["domain"], client_kind=c["kind"])
    return env


def _fmt(template: str | None, env: dict[str, Any]) -> str:
    return (template or "").format_map(defaultdict(str, env))


def run(conn: sqlite3.Connection, path: Path, *, foundry: Any = None, deadlines_path: Path | None = None,
        limit: int = 500) -> dict[str, Any]:
    autos = [a for a in load(path) if a.enabled]
    cursor = int((one(conn, "SELECT value FROM kv WHERE key = 'automation_cursor'") or {"value": "0"})["value"])
    events = rows(conn, "SELECT * FROM audit WHERE seq > ? ORDER BY seq LIMIT ?", cursor, limit)
    fired: list[dict[str, Any]] = []
    for e in events:
        e["payload"] = json.loads(e["payload"])
        if e["actor"].startswith("automation:"):
            cursor = e["seq"]
            continue  # never trigger on our own actions (no loops)
        fid = e["payload"].get("finding_id")
        if e["action"] == "finding.raised" and fid and one(conn, "SELECT 1 FROM finding_resolutions WHERE finding_id = ?", fid):
            cursor = e["seq"]
            continue  # already resolved before we got to it (e.g. the receipt arrived): nothing to chase
        for a in autos:
            if a.trigger != e["action"]:
                continue
            env = _env(conn, e)
            try:
                if a.when and not evaluate(a.when, env):
                    continue
            except ExprError:
                continue
            fired += _do(conn, a, env, e.get("client_id"), f"{a.id}:{e['seq']}", foundry, deadlines_path)
        cursor = e["seq"]
    # schedule.daily fires once per calendar day
    today = date.today().isoformat()
    last_daily = (one(conn, "SELECT value FROM kv WHERE key = 'automation_daily'") or {"value": ""})["value"]
    if last_daily != today:
        for a in autos:
            if a.trigger == "schedule.daily":
                fired += _do(conn, a, {"action": "schedule.daily"}, None, f"{a.id}:{today}", foundry, deadlines_path)
        conn.execute("INSERT INTO kv (key, value) VALUES ('automation_daily', ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (today,))
    conn.execute("INSERT INTO kv (key, value) VALUES ('automation_cursor', ?) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (str(cursor),))
    return {"events": len(events), "fired": fired, "cursor": cursor}


def _do(conn, a: Automation, env: dict[str, Any], client_id: str | None, key: str, foundry: Any,
        deadlines_path: Path | None) -> list[dict[str, Any]]:
    src = f"automation:{a.id}"
    out = []
    for i, act in enumerate(a.actions):
        k = f"{key}:{i}"
        if act.type == "create_task":
            due = (date.today() + timedelta(days=act.due_in_days)).isoformat() if act.due_in_days is not None else None
            tid = crm.create_task(conn, title=_fmt(act.title, env), detail=_fmt(act.detail, env), assignee=act.assignee or "cpa",
                                  source=src, client_id=client_id, due=due, dedupe_key=k)
            out.append({"automation": a.id, "action": "create_task", "task_id": tid})
        elif act.type == "draft_message":
            mid = crm.draft_message(conn, client_id=client_id, subject=_fmt(act.subject, env), body=_fmt(act.body, env),
                                    source=src, dedupe_key=k)
            out.append({"automation": a.id, "action": "draft_message", "message_id": mid})
        elif act.type == "run_agent" and foundry is not None and act.agent:
            rec = foundry.run(act.agent)
            out.append({"automation": a.id, "action": "run_agent", "agent": act.agent, "ok": rec["ok"]})
        elif act.type == "deadline_scan" and deadlines_path is not None:
            n = 0
            for c in rows(conn, "SELECT id FROM clients"):
                for d in crm.deadlines(conn, deadlines_path, c["id"], horizon_days=act.within_days or 30):
                    if crm.create_task(conn, title=f"{d['title']} due {d['due']}", detail=d.get("citation") or "",
                                       assignee="cpa", source=src, client_id=c["id"], due=d["due"],
                                       dedupe_key=f"deadline:{c['id']}:{d['id']}:{d['due']}"):
                        n += 1
            out.append({"automation": a.id, "action": "deadline_scan", "tasks_created": n})
        elif act.type == "webhook" and act.url_env:
            import os

            import httpx

            url = os.environ.get(act.url_env)
            if not url:
                out.append({"automation": a.id, "action": "webhook", "skipped": f"{act.url_env} not set"})
                continue
            body = {"text": _fmt(act.title or a.title, env), "event": env.get("action"), "client": env.get("client_name"),
                    "detail": _fmt(act.detail, env)}
            try:
                r = httpx.post(url, json=body, timeout=15)
                out.append({"automation": a.id, "action": "webhook", "status": r.status_code})
            except httpx.HTTPError as e:
                out.append({"automation": a.id, "action": "webhook", "error": str(e)[:200]})
        elif act.type == "request_missing_facts" and foundry is not None and client_id:
            from ..brain.playbooks import Brain

            brain = Brain(foundry.paths.root / "playbooks", foundry.kb)
            for o in brain.scan(conn, client_id):
                if o["status"] == "needs_facts" and o["missing_fact"]:
                    crm.create_task(conn, title=f"Tell us: {o['missing_fact'].replace('_', ' ')}",
                                    detail=f"Needed to evaluate “{o['title']}”.", assignee="client", source=src,
                                    client_id=client_id, dedupe_key=f"fact:{client_id}:{o['missing_fact']}")
            out.append({"automation": a.id, "action": "request_missing_facts"})
    if out:
        audit.record(conn, src, "agent", "automation.fired", {"automation": a.id, "results": out}, client_id=client_id)
    return out
