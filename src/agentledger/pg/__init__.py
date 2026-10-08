"""The PostgreSQL financial core (ADR-0002).

The database enforces the ledger's invariants itself (see migrations/0001_ledger_core.sql): balance at commit,
immutability, closed periods, one effect per command, outbox and audit in the same transaction, and per-client hash
chains. This module applies migrations and gives the application a narrow client that:

* runs every request as the `agentledger_app` role, which can read but never write tables directly;
* sets the session's client scope (from engagement grants) for row-level security and the write functions;
* maps the database's error codes to the same exceptions the rest of the code already handles.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..db import CommandConflict
from ..ledger.store import ClosedPeriod, LedgerError

MIGRATIONS = Path(__file__).parent / "migrations"
APP_ROLE = "agentledger_app"


class Unbalanced(LedgerError):
    pass


class OutOfScope(PermissionError):
    pass


class NotAuthorized(PermissionError):
    pass


class AppendOnly(LedgerError):
    pass


ERRORS: dict[str, type[Exception]] = {
    "AL001": Unbalanced, "AL002": ClosedPeriod, "AL003": CommandConflict, "AL004": OutOfScope,
    "AL005": LedgerError, "AL006": AppendOnly, "AL007": NotAuthorized,
}


def _psycopg():
    import psycopg  # imported lazily: the SQLite build runs without the driver

    return psycopg


def dsn(*, direct: bool = False) -> str | None:
    """The connection string from the environment. Migrations use the direct (unpooled) endpoint."""
    if direct:
        return os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL")
    return os.environ.get("DATABASE_URL")


def connect(url: str | None = None, *, direct: bool = False):
    url = url or dsn(direct=direct)
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return _psycopg().connect(url, autocommit=True)


def translate(exc: Exception) -> Exception:
    code = getattr(exc, "sqlstate", None)
    kind = ERRORS.get(code or "")
    if kind is None:
        return exc
    diag = getattr(exc, "diag", None)
    return kind(getattr(diag, "message_primary", None) or str(exc))


# ------------------------------------------------------------------------------------------------- migrations
def migrate(conn, schema: str) -> list[str]:
    """Apply pending migrations to `schema`, each in its own transaction, recording name and checksum.
    An applied migration whose file has changed is an error: migrations are append-only like the ledger."""
    if not schema.replace("_", "").isalnum():
        raise ValueError(f"bad schema name {schema!r}")
    applied = []
    with conn.transaction():
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{schema}".schema_migrations '
                     "(name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
    done = dict(conn.execute(f'SELECT name, sha256 FROM "{schema}".schema_migrations').fetchall())
    for path in sorted(MIGRATIONS.glob("*.sql")):
        # read_text uses universal newlines, so a CRLF checkout hashes the same as an LF one
        sql = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode()).hexdigest()
        if path.name in done:
            if done[path.name] != digest:
                raise RuntimeError(f"migration {path.name} changed after it was applied; add a new migration instead")
            continue
        with conn.transaction():
            conn.execute(f'SET LOCAL search_path TO "{schema}", public')
            conn.execute(sql)
            conn.execute(f'INSERT INTO "{schema}".schema_migrations (name, sha256) VALUES (%s, %s)', (path.name, digest))
        applied.append(path.name)
    return applied


# ------------------------------------------------------------------------------------------------- the ledger client
@dataclass
class Line:
    account: str
    amount: Decimal
    tax_treatment: str | None = None


def _lines(lines: Sequence[Line | dict[str, Any]]) -> str:
    out = []
    for ln in lines:
        d = ln if isinstance(ln, dict) else {"account": ln.account, "amount": ln.amount, "tax_treatment": ln.tax_treatment}
        out.append({"account": d["account"], "amount": str(Decimal(str(d["amount"]))), "tax_treatment": d.get("tax_treatment")})
    return json.dumps(out)


class Ledger:
    """One firm database (one schema in tests). Every call runs in a transaction as the app role, scoped to the
    clients the caller is engaged on. `clients=["*"]` is for firm-wide jobs only."""

    def __init__(self, conn, schema: str, *, clients: Sequence[str], actor: str, role: str):
        self.conn, self.schema = conn, schema
        self.clients, self.actor, self.role = list(clients), actor, role

    @contextmanager
    def tx(self) -> Iterator[Any]:
        try:
            with self.conn.transaction():
                self.conn.execute(f'SET LOCAL search_path TO "{self.schema}", public')
                self.conn.execute(f"SET LOCAL ROLE {APP_ROLE}")
                self.conn.execute("SELECT set_config('agentledger.clients', %s, true)", (",".join(self.clients),))
                yield self.conn
        except _psycopg().Error as exc:
            raise translate(exc) from exc

    def _one(self, sql: str, params: Sequence[Any] = ()) -> Any:
        with self.tx() as c:
            row = c.execute(sql, params).fetchone()
            return row[0] if row else None

    def add_client(self, id: str, name: str, kind: str) -> None:
        self._one("SELECT add_client(%s, %s, %s, %s, %s)", (id, name, kind, self.actor, self.role))

    def add_account(self, client: str, code: str, name: str, type: str) -> None:
        self._one("SELECT add_account(%s, %s, %s, %s, %s, %s)", (client, code, name, type, self.actor, self.role))

    def post(self, client: str, on: date, memo: str, lines: Sequence[Line | dict[str, Any]], *, source: str = "manual",
             command_id: str | None = None) -> int:
        return self._one("SELECT post_journal(%s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
                         (client, on, memo, source, self.actor, self.role, _lines(lines), command_id))

    def reverse(self, client: str, entry_id: int, on: date, reason: str, *, command_id: str | None = None) -> int:
        return self._one("SELECT reverse_journal(%s, %s, %s, %s, %s, %s, %s)",
                         (client, entry_id, on, reason, self.actor, self.role, command_id))

    def close_period(self, client: str, through: date) -> None:
        self._one("SELECT close_period(%s, %s, %s, %s)", (client, through, self.actor, self.role))

    def reopen_period(self, client: str, back_to: date, reason: str) -> None:
        self._one("SELECT reopen_period(%s, %s, %s, %s, %s)", (client, back_to, self.actor, self.role, reason))

    def verify_chain(self, client: str) -> dict[str, Any]:
        with self.tx() as c:
            ok, checked, broken = c.execute("SELECT * FROM verify_chain(%s)", (client,)).fetchone()
        return {"ok": ok, "checked": checked, "broken_at": broken}

    def balances(self, client: str) -> dict[str, Decimal]:
        with self.tx() as c:
            rows = c.execute("SELECT account_code, sum(amount) FROM postings WHERE client_id = %s GROUP BY 1 ORDER BY 1",
                             (client,)).fetchall()
        return {code: total for code, total in rows}

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        """Read-only access as the app role (RLS applies)."""
        with self.tx() as c:
            return c.execute(sql, params).fetchall()
