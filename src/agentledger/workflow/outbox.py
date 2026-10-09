"""The transactional outbox (ADR-0003, spec §15): domain events written in the transaction of the change they
describe, read by whatever orchestrates (the local runner, the Worker relay) and marked delivered one by one.

Both backends share the shape: `outbox` is append-only and `outbox_delivery` holds the delivery marks apart from the
event. On SQLite the application inserts; on PostgreSQL the application role has SELECT only on `outbox`, and inserts
through `emit_event` (migration 0006, SECURITY DEFINER), which also checks that the client is in the session's scope.
Payloads carry ids and hashes, never a value a person entered: they leave the store.
"""

from __future__ import annotations

import contextvars
import json
from typing import Any

from .. import audit
from ..db import is_pg, rows, unit_of_work

# Set per request by the API (a fresh list), appended to by `emit`: the response then carries X-AgentLedger-Outbox so
# the edge can run the relay at once instead of waiting for its cron. Threadpool workers share the list object.
EMITTED: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar("agentledger_outbox_emitted", default=None)


def emit(conn: Any, client_id: str, event_type: str, aggregate: str, aggregate_id: str, payload: dict[str, Any]) -> int:
    """Record an event in the caller's transaction (joined when there is one). Returns its id."""
    body = json.dumps(payload, sort_keys=True, default=str)
    with unit_of_work(conn):
        if is_pg(conn):
            row = conn.execute("SELECT emit_event(?, ?, ?, ?, ?::jsonb) AS id", (client_id, event_type, aggregate, aggregate_id, body)).fetchone()
            eid = int(row["id"])
        else:
            cur = conn.execute("INSERT INTO outbox (client_id, event_type, aggregate, aggregate_id, payload, recorded_at) "
                               "VALUES (?, ?, ?, ?, ?, ?)", (client_id, event_type, aggregate, aggregate_id, body, audit.now()))
            eid = int(cur.lastrowid or 0)
    seen = EMITTED.get()
    if seen is not None:
        seen.append(eid)
    return eid


def _decode(r: dict[str, Any]) -> dict[str, Any]:
    r["id"] = int(r["id"])
    if isinstance(r.get("payload"), str):
        r["payload"] = json.loads(r["payload"])
    return r


def undelivered(conn: Any, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    """Events not yet marked delivered, oldest first, with ids above `after_id` (a consumer's cursor; 0 for all)."""
    limit = max(1, min(int(limit), 1000))
    return [_decode(r) for r in rows(conn, "SELECT o.id, o.client_id, o.event_type, o.aggregate, o.aggregate_id, o.payload, "
                                           "o.recorded_at FROM outbox o LEFT JOIN outbox_delivery d ON d.outbox_id = o.id "
                                           "WHERE d.outbox_id IS NULL AND o.id > ? ORDER BY o.id LIMIT ?", int(after_id), limit)]


def mark_delivered(conn: Any, outbox_id: int) -> bool:
    """Mark one event delivered. True the first time, False when it already was (idempotent)."""
    with unit_of_work(conn):
        if not conn.execute("SELECT 1 FROM outbox WHERE id = ?", (int(outbox_id),)).fetchone():
            raise KeyError(f"no outbox event {outbox_id}")
        if is_pg(conn):
            row = conn.execute("SELECT mark_delivered(?) AS ok", (int(outbox_id),)).fetchone()
            return bool(row and row["ok"])
        cur = conn.execute("INSERT OR IGNORE INTO outbox_delivery (outbox_id, delivered_at) VALUES (?, ?)", (int(outbox_id), audit.now()))
        return cur.rowcount == 1
