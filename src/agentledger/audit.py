"""Tamper-evident audit trail shared by CPA, client and the AI.

Everything that changes what the numbers say is recorded here: AI answers, CPA
overrides, regulatory adoptions, finding resolutions, document filings. Both the
client and the CPA see the same trail, which is what keeps everyone honest.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .db import GENESIS, chain_hash, is_pg, rows, unit_of_work


_CLOCK: datetime | None = None


def now() -> str:
    return (_CLOCK or datetime.now(timezone.utc)).isoformat(timespec="seconds")


@contextmanager
def clock(at: datetime) -> Iterator[None]:
    """Simulate the wall clock (used only to seed realistic historical demo data)."""
    global _CLOCK
    prev, _CLOCK = _CLOCK, at
    try:
        yield
    finally:
        _CLOCK = prev


def record(conn: sqlite3.Connection, actor: str, role: str, action: str, payload: dict[str, Any],
           client_id: str | None = None) -> int:
    if is_pg(conn):  # the database stamps, chains and stores the record; the app role cannot write the table
        r = conn.execute("SELECT record_audit(?, ?, ?, ?, ?::jsonb) AS seq",
                         (actor, role, client_id, action, json.dumps(payload, default=str))).fetchone()
        return int(r["seq"])
    with unit_of_work(conn):  # joins the caller's transaction, so the record commits with the change it describes
        last =conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev = last["hash"] if last else GENESIS
        at = now()
        body = {"at": at, "actor": actor, "role": role, "client_id": client_id, "action": action, "payload": payload}
        h = chain_hash(prev, body)
        cur = conn.execute(
            "INSERT INTO audit (at, actor, role, client_id, action, payload, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?)",
            (at, actor, role, client_id, action, json.dumps(payload, default=str), prev, h),
        )
        return int(cur.lastrowid)


def events(conn: sqlite3.Connection, client_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    if client_id:
        out = rows(conn, "SELECT * FROM audit WHERE client_id = ? ORDER BY seq DESC LIMIT ?", client_id, limit)
    else:
        out = rows(conn, "SELECT * FROM audit ORDER BY seq DESC LIMIT ?", limit)
    for e in out:
        if isinstance(e["payload"], str):  # text on SQLite, already decoded JSON on PostgreSQL
            e["payload"] = json.loads(e["payload"])
    return out


def verify(conn: sqlite3.Connection) -> dict[str, Any]:
    if is_pg(conn):
        r = conn.execute("SELECT ok, checked, broken_at, head FROM verify_audit()").fetchone()
        return {"ok": r["ok"], "checked": r["checked"], "head": r["head"]} if r["ok"] else \
            {"ok": False, "broken_at": r["broken_at"], "checked": r["checked"]}
    prev = GENESIS
    n = 0
    for e in conn.execute("SELECT * FROM audit ORDER BY seq"):
        body = {"at": e["at"], "actor": e["actor"], "role": e["role"], "client_id": e["client_id"],
                "action": e["action"], "payload": json.loads(e["payload"])}
        if e["prev_hash"] != prev or chain_hash(prev, body) != e["hash"]:
            return {"ok": False, "broken_at": e["seq"], "checked": n}
        prev = e["hash"]
        n += 1
    return {"ok": True, "checked": n, "head": prev}
