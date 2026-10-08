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

from .db import GENESIS, chain_hash, rows


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
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        last = conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
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
        e["payload"] = json.loads(e["payload"])
    return out


def verify(conn: sqlite3.Connection) -> dict[str, Any]:
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
