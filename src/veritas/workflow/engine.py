"""Durable, event-sourced workflows.

A workflow's state is never stored as a mutable field: it is the result of replaying its
append-only, hash-chained event log. That gives the guarantees BL.md asks of Temporal:

- Durable pause: a workflow waiting on the world (a signature, an IRS acknowledgement, a
  client document) is just a state with no pending work; it costs nothing and survives
  restarts, container moves and weeks of waiting.
- Deterministic resume: replaying the same events always yields the same state.
- Exactly-once activities: an activity's result is recorded as an event under an
  idempotency key, so a retry after a crash reuses the recorded result instead of running
  the side effect (for example a transmission) twice.
- Guards are deterministic functions of the state and the facts supplied with the event;
  no model output can move a workflow.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_events (
    workflow_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    event TEXT NOT NULL,
    data TEXT NOT NULL,
    actor TEXT NOT NULL,
    at TEXT NOT NULL,
    idempotency_key TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    PRIMARY KEY (workflow_id, seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS workflow_idem ON workflow_events (workflow_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS workflow_events_no_update BEFORE UPDATE ON workflow_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS workflow_events_no_delete BEFORE DELETE ON workflow_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""


class TransitionError(Exception):
    """The event is not allowed from the current state, or a guard refused it."""


Guard = Callable[["State", dict[str, Any]], list[str]]  # returns reasons it is refused (empty = allowed)


@dataclass(frozen=True)
class Transition:
    event: str
    sources: tuple[str, ...]  # "*" = any state
    target: str
    guard: Guard | None = None
    roles: tuple[str, ...] = ()  # roles allowed to send it; empty = any


@dataclass
class Definition:
    kind: str
    initial: str
    transitions: list[Transition]
    waiting: dict[str, str] = field(default_factory=dict)  # state -> what the world must supply

    def find(self, event: str, state: str) -> Transition | None:
        for t in self.transitions:
            if t.event == event and ("*" in t.sources or state in t.sources):
                return t
        return None

    def allowed(self, state: str) -> list[str]:
        return sorted({t.event for t in self.transitions if "*" in t.sources or state in t.sources})


@dataclass
class State:
    workflow_id: str
    kind: str
    status: str
    seq: int
    facts: dict[str, Any]
    history: list[dict[str, Any]]


def _hash(prev: str, row: dict[str, Any]) -> str:
    return hashlib.sha256((prev + json.dumps(row, sort_keys=True, default=str)).encode()).hexdigest()


class Engine:
    def __init__(self, conn: sqlite3.Connection, definitions: dict[str, Definition]):
        self.conn = conn
        self.defs = definitions
        conn.executescript(SCHEMA)

    def _events(self, workflow_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY seq", (workflow_id,)))

    def state(self, workflow_id: str) -> State:
        events = self._events(workflow_id)
        if not events:
            raise KeyError(f"no workflow {workflow_id}")
        d = self.defs[events[0]["kind"]]
        status, facts, history = d.initial, {}, []
        for e in events:
            data = json.loads(e["data"])
            if e["event"] != "started":
                t = d.find(e["event"], status)
                if t is None and not e["event"].startswith("activity:"):
                    raise TransitionError(f"corrupt history: {e['event']} from {status}")
                if t is not None:
                    status = t.target
            facts.update(data.get("facts", {}))
            history.append({"seq": e["seq"], "event": e["event"], "actor": e["actor"], "at": e["at"], "status": status,
                            "note": data.get("note", "")})
        return State(workflow_id, d.kind, status, events[-1]["seq"], facts, history)

    def exists(self, workflow_id: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM workflow_events WHERE workflow_id = ? LIMIT 1", (workflow_id,)).fetchone())

    def _append(self, workflow_id: str, kind: str, event: str, data: dict[str, Any], actor: str,
                idempotency_key: str | None = None) -> int:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            last = self.conn.execute("SELECT seq, hash FROM workflow_events WHERE workflow_id = ? ORDER BY seq DESC LIMIT 1",
                                     (workflow_id,)).fetchone()
            seq = (last["seq"] + 1) if last else 1
            prev = last["hash"] if last else "genesis"
            at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            row = {"workflow_id": workflow_id, "seq": seq, "kind": kind, "event": event, "data": data, "actor": actor, "at": at}
            self.conn.execute(
                "INSERT INTO workflow_events (workflow_id, seq, kind, event, data, actor, at, idempotency_key, prev_hash, hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (workflow_id, seq, kind, event, json.dumps(data, default=str), actor, at, idempotency_key, prev, _hash(prev, row)))
            self.conn.execute("COMMIT")
            return seq
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def start(self, workflow_id: str, kind: str, actor: str, facts: dict[str, Any] | None = None) -> State:
        if self.exists(workflow_id):
            raise TransitionError(f"workflow {workflow_id} already exists")
        self._append(workflow_id, kind, "started", {"facts": facts or {}}, actor)
        return self.state(workflow_id)

    def send(self, workflow_id: str, event: str, actor: str, *, role: str = "", facts: dict[str, Any] | None = None,
             note: str = "", context: dict[str, Any] | None = None) -> State:
        """Move the workflow. `context` feeds guards but is not stored; `facts` are stored and folded into state."""
        st = self.state(workflow_id)
        d = self.defs[st.kind]
        t = d.find(event, st.status)
        if t is None:
            raise TransitionError(f"'{event}' is not allowed while the workflow is '{st.status}' "
                                  f"(allowed: {', '.join(d.allowed(st.status)) or 'none'})")
        if t.roles and role not in t.roles:
            raise TransitionError(f"'{event}' requires one of: {', '.join(t.roles)}")
        if t.guard:
            reasons = t.guard(st, {**(context or {}), **(facts or {}), "actor": actor, "role": role})
            if reasons:
                raise TransitionError("; ".join(reasons))
        self._append(workflow_id, st.kind, event, {"facts": facts or {}, "note": note}, actor)
        return self.state(workflow_id)

    def activity(self, workflow_id: str, name: str, key: str, fn: Callable[[], dict[str, Any]], actor: str = "system") -> dict[str, Any]:
        """Run a side effect at most once per idempotency key; a repeat returns the recorded result."""
        row = self.conn.execute("SELECT data FROM workflow_events WHERE workflow_id = ? AND idempotency_key = ?",
                                (workflow_id, f"{name}:{key}")).fetchone()
        if row:
            return json.loads(row["data"])["result"]
        result = fn()
        st = self.state(workflow_id)
        self._append(workflow_id, st.kind, f"activity:{name}", {"result": result, "facts": result.get("facts", {})}, actor,
                     idempotency_key=f"{name}:{key}")
        return result

    def verify(self, workflow_id: str) -> bool:
        prev = "genesis"
        for e in self._events(workflow_id):
            row = {"workflow_id": e["workflow_id"], "seq": e["seq"], "kind": e["kind"], "event": e["event"],
                   "data": json.loads(e["data"]), "actor": e["actor"], "at": e["at"]}
            if e["prev_hash"] != prev or e["hash"] != _hash(prev, row):
                return False
            prev = e["hash"]
        return True

    def waiting(self, kind: str) -> list[State]:
        """Workflows durably paused on the outside world (for reminders and pollers)."""
        d = self.defs[kind]
        ids = [r[0] for r in self.conn.execute("SELECT DISTINCT workflow_id FROM workflow_events WHERE kind = ?", (kind,))]
        out = []
        for wid in ids:
            st = self.state(wid)
            if st.status in d.waiting:
                out.append(st)
        return out
