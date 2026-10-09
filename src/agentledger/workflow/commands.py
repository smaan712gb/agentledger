"""Domain commands: the contract between an orchestrator and the domain (ADR-0003, backlog F-08).

A command names what the domain should ensure, under an idempotency key. The domain runs it once, atomically with
its receipt (the `commands` table on SQLite, `app_commands` on PostgreSQL) and with everything the handler writes
(events, rows, outbox); a retry with the same key replays the recorded result and runs nothing. The receipt's hash
covers the payload, the actor and the role, so a replay by another principal, or with other data, is a conflict
rather than a silent reuse. Concurrent attempts at one command are serialized for the whole execution (a process
lock, and a session advisory lock on PostgreSQL), so the second one finds the first one's receipt.

A command whose effect leaves the system (a transmission) has a `prepare` phase that runs before the receipt
transaction: the two-phase activity in workflow/engine.py records that the action starts, commits that on its own,
calls the provider, and records the result. Were it inside the receipt transaction, a provider error would roll the
"started" marker back and a retry would send again. The handler then records the transition, atomically with the
receipt.

Refusals are not retried and not receipted: a `TransitionError` (the domain's guards said no) rolls back and
surfaces as it is; an orchestrator maps it to a non-retryable failure.

Authority stays with people. A command marked `human` (approving a release, reconciling an unknown transmission,
retransmitting, voiding) is refused for the role "system": the workflow may run only the system commands, and each
of those is an "ensure", not a "do": it first reads the aggregate and returns the recorded outcome when the
transition already happened, so a retry converges.

Handlers are registered by the domain packages (returns/filing.py); this module knows nothing about returns.
"""

from __future__ import annotations

import json
import threading
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from .. import audit
from ..db import CommandConflict, is_pg, payload_hash, unit_of_work

SYSTEM = "system"


class NotPermitted(Exception):
    """The principal may not run this command (a system principal on a person's decision)."""


@dataclass(frozen=True)
class Command:
    name: str
    idempotency_key: str
    payload: dict[str, Any]
    actor: str
    role: str

    def __post_init__(self) -> None:
        if not self.name or not self.idempotency_key:
            raise ValueError("a command needs a name and an idempotency key")
        if not isinstance(self.payload, dict):
            raise ValueError("a command payload is a JSON object")


@dataclass(frozen=True)
class CommandResult:
    result: dict[str, Any]
    replayed: bool
    at: str

    def to_dict(self) -> dict[str, Any]:
        return {"result": self.result, "replayed": self.replayed, "at": self.at}


@dataclass
class Context:
    """What a handler works with: the store, the domain services and the transmitter adapter (None when none is
    configured; the system transmit then refuses)."""

    conn: Any
    returns: Any
    provider: Any = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))


Handler = Callable[[Context, Command, Any], dict[str, Any]]
Prepare = Callable[[Context, Command], Any]
Scope = Callable[[Context, dict[str, Any]], str]


@dataclass(frozen=True)
class Spec:
    """A registered command: its handler, how to find the client its receipt is scoped to (KeyError when the
    aggregate does not exist), whether only a person may run it, and the optional prepare phase (see module doc)."""

    name: str
    handler: Handler
    scope: Scope
    human: bool = False
    prepare: Prepare | None = None


COMMANDS: dict[str, Spec] = {}


def register(spec: Spec) -> Spec:
    COMMANDS[spec.name] = spec
    return spec


def registry() -> dict[str, Spec]:
    """Every registered command. The return filing commands register when their module loads."""
    from ..returns import filing  # noqa: F401  (registers its commands)

    return COMMANDS


def system_commands() -> list[str]:
    return sorted(n for n, s in registry().items() if not s.human)


def receipt_table(conn: Any) -> str:
    return "app_commands" if is_pg(conn) else "commands"   # on PostgreSQL `commands` holds the ledger's own receipts


# ------------------------------------------------------------------------------------------------- serialization
_STRIPES = [threading.Lock() for _ in range(64)]


@contextmanager
def _held(conn: Any, key: str) -> Iterator[None]:
    """One attempt at a time per command: threads of this process queue on a striped lock; on PostgreSQL the session
    also takes an advisory lock keyed by schema, so attempts from other processes queue too."""
    with _STRIPES[zlib.crc32(f"{getattr(conn, 'location', id(conn))}:{key}".encode()) % len(_STRIPES)]:
        if not is_pg(conn):
            yield
            return
        conn.execute("SELECT pg_advisory_lock(hashtextextended(current_schema() || ':' || ?, 0))", (key,))
        try:
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(current_schema() || ':' || ?, 0))", (key,))


def _receipt(conn: Any, scope: str, command_id: str) -> Any:
    return conn.execute(f"SELECT payload_hash, result, created_at FROM {receipt_table(conn)} WHERE scope = ? AND command_id = ?",
                        (scope, command_id)).fetchone()


def execute(conn: Any, cmd: Command, spec: Spec, ctx: Context, *, client_id: str) -> CommandResult:
    """Run the command at most once per (client, command name, idempotency key), atomically with its receipt and with
    everything the handler writes. A replay returns the recorded result; the same key with another payload, actor or
    role is a CommandConflict; a refusal leaves nothing behind."""
    scope, command_id = f"client:{client_id}", f"{cmd.name}:{cmd.idempotency_key}"
    h = payload_hash({"name": cmd.name, "payload": cmd.payload, "actor": cmd.actor, "role": cmd.role})
    with _held(conn, f"command:{scope}:{command_id}"):
        row = _receipt(conn, scope, command_id)
        if row:
            if row["payload_hash"] != h:
                raise CommandConflict(f"{cmd.name} with key {cmd.idempotency_key} was already run with another payload or by "
                                      "another principal")
            return CommandResult(json.loads(row["result"]), True, str(row["created_at"]))
        prepared = spec.prepare(ctx, cmd) if spec.prepare else None   # durable on its own, before the receipt transaction
        with unit_of_work(conn) as c:
            result = spec.handler(ctx, cmd, prepared)
            if not isinstance(result, dict):
                raise TypeError(f"{cmd.name} must return a JSON object, not {type(result).__name__}")
            at = audit.now()
            c.execute(f"INSERT INTO {receipt_table(conn)} (scope, command_id, kind, payload_hash, result) VALUES (?, ?, ?, ?, ?)",
                      (scope, command_id, cmd.name, h, json.dumps(result, default=str)))
            return CommandResult(result, False, at)


def dispatch(ctx: Context, cmd: Command) -> CommandResult:
    """Look the command up, check who may run it, find its client and execute it with a receipt."""
    spec = registry().get(cmd.name)
    if spec is None:
        raise KeyError(f"unknown command {cmd.name}")
    if spec.human and cmd.role == SYSTEM:
        raise NotPermitted(f"{cmd.name} is a person's decision; the workflow may not run it")
    client_id = spec.scope(ctx, cmd.payload)
    return execute(ctx.conn, cmd, spec, ctx, client_id=client_id)
