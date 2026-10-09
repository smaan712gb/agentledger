"""The PostgreSQL financial core (ADR-0002).

The database enforces the ledger's invariants itself (see migrations/0001_ledger_core.sql): balance at commit,
immutability, closed periods, one effect per command, outbox and audit in the same transaction, and per-client hash
chains. This module applies migrations and gives the application a narrow client that:

* connects as the store's own runtime login role (`rt_<database>_<schema>`), never as the owner: the role owns
  nothing, cannot become the owner (there is no SET ROLE to reset), can write tables only where the migrations grant
  it, and has privileges in its own store only, so it cannot read another firm's schema or database;
* sets the session's client scope (from engagement grants) for row-level security and the write functions;
* maps the database's error codes to the same exceptions the rest of the code already handles.

Credentials: migrations and provisioning use the owner connection (AGENTLEDGER_MIGRATION_URL, else
DATABASE_URL_UNPOOLED). Runtime connections take only the host and database from AGENTLEDGER_RUNTIME_DATABASE_URL
(else the same URL) and log in as the runtime role, whose password is derived from AGENTLEDGER_DB_ROLE_KEY. In
production the API's environment has the role key and no owner credentials; migrations run as a release step.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

from ..db import CommandConflict
from ..ledger.store import ClosedPeriod, LedgerError

MIGRATIONS = Path(__file__).parent / "migrations"
# The platform store (firms, users, sessions, wrapped keys; agentledger.security.platform): one schema in the admin
# database with its own migrations and runtime role. Kept in a subdirectory: `migrate` applies every *.sql of the
# directory it is given to one schema, so these never land in a firm store, nor the firm migrations in the platform.
PLATFORM_SCHEMA = "platform"
PLATFORM_MIGRATIONS = MIGRATIONS / "platform"
ROLE_TOKEN = "{{app_role}}"


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


def migration_url() -> str | None:
    """Owner credentials, for migrations and provisioning only."""
    return os.environ.get("AGENTLEDGER_MIGRATION_URL") or dsn(direct=True)


def runtime_base_url() -> str | None:
    """Where runtime connections go (host, database, options). Its credentials are replaced by the runtime role's."""
    return os.environ.get("AGENTLEDGER_RUNTIME_DATABASE_URL") or dsn(direct=True)


def database_of(url: str) -> str:
    return urlparse(url).path.lstrip("/") or "postgres"


def with_database(url: str, name: str) -> str:
    return urlunparse(urlparse(url)._replace(path="/" + name))


def runtime_role(database: str, schema: str) -> str:
    """The login role of one store: rt_<database>_<schema>, shortened with a hash when it would exceed 63 bytes."""
    name = re.sub(r"[^a-z0-9_]", "_", f"rt_{database}_{schema}".lower())
    if len(name) > 63:
        name = name[:50] + "_" + hashlib.sha256(name.encode()).hexdigest()[:12]
    return name


def runtime_password(role: str) -> str:
    """Derived per role from AGENTLEDGER_DB_ROLE_KEY, so no per-firm database password is stored anywhere.
    Development without the key derives it from the owner password in the local environment."""
    key = os.environ.get("AGENTLEDGER_DB_ROLE_KEY", "")
    if not key:
        owner = migration_url()
        key = (urlparse(owner).password or "") if owner else ""
        if not key:
            raise RuntimeError("set AGENTLEDGER_DB_ROLE_KEY (runtime database role passwords are derived from it)")
        key = "dev-only:" + key
    return hmac.new(key.encode(), role.encode(), hashlib.sha256).hexdigest()


def runtime_url(base: str, role: str) -> str:
    u = urlparse(base)
    host = u.hostname or ""
    netloc = f"{quote(role)}:{runtime_password(role)}@{host}" + (f":{u.port}" if u.port else "")
    return urlunparse(u._replace(netloc=netloc))


def ensure_role(owner, role: str, *, database: str | None = None) -> None:
    """Create (or reset) a store's runtime role: login, no inheritance, no role or database creation, no RLS bypass.
    With `database` (database tenancy), only this role may connect to that database."""
    from psycopg import sql

    exists = owner.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    stmt = "ALTER ROLE {} WITH" if exists else "CREATE ROLE {} WITH"
    owner.execute(sql.SQL(stmt + " LOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {}")
                  .format(sql.Identifier(role), sql.Literal(runtime_password(role))))
    if database:
        owner.execute(sql.SQL("REVOKE CONNECT, TEMPORARY ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
        owner.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(role)))


def drop_role(owner, role: str) -> None:
    from psycopg import sql

    # The role owns nothing; once its schema or database is gone, only grants outside the store remain. Revoking them
    # (rather than DROP OWNED, which needs the role's own privileges and fails on Neon, where the owner is not a
    # superuser) lets the drop succeed. Call after removing the store.
    if owner.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
        database = owner.execute("SELECT current_database()").fetchone()[0]
        owner.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(sql.Identifier(role)))
        owner.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(sql.Identifier(database), sql.Identifier(role)))
        owner.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


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
def migrate(conn, schema: str, role: str | None = None, *, own_database: bool = False,
            migrations: Path | None = None) -> list[str]:
    """Apply pending migrations to `schema` as the owner, each in its own transaction, recording name and checksum,
    and (re)create the store's runtime role first. An applied migration whose file has changed is an error:
    migrations are append-only like the ledger. The checksum covers the file as written, before the role name is
    substituted. `migrations` is the directory whose *.sql files make up the schema: a firm store's (MIGRATIONS, the
    default, read when called) or the platform store's (PLATFORM_MIGRATIONS)."""
    if not schema.replace("_", "").isalnum():
        raise ValueError(f"bad schema name {schema!r}")
    migrations = migrations or MIGRATIONS
    database = conn.execute("SELECT current_database()").fetchone()[0]
    role = role or runtime_role(database, schema)
    ensure_role(conn, role, database=database if own_database else None)
    applied = []
    with conn.transaction():
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{schema}".schema_migrations '
                     "(name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
    done = dict(conn.execute(f'SELECT name, sha256 FROM "{schema}".schema_migrations').fetchall())
    for path in sorted(migrations.glob("*.sql")):
        # read_text uses universal newlines, so a CRLF checkout hashes the same as an LF one
        sql = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode()).hexdigest()
        if path.name in done:
            if done[path.name] != digest:
                raise RuntimeError(f"migration {path.name} changed after it was applied; add a new migration instead")
            continue
        with conn.transaction():
            conn.execute(f'SET LOCAL search_path TO "{schema}", public')
            conn.execute(sql.replace(ROLE_TOKEN, f'"{role}"'))
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
    """One firm database (one schema in tests). `conn` is a runtime-role connection (see `runtime_url`); every call
    runs in a transaction scoped to the clients the caller is engaged on. `clients=["*"]` is for firm-wide jobs."""

    def __init__(self, conn, schema: str, *, clients: Sequence[str], actor: str, role: str):
        self.conn, self.schema = conn, schema
        self.clients, self.actor, self.role = list(clients), actor, role

    @contextmanager
    def tx(self) -> Iterator[Any]:
        try:
            with self.conn.transaction():
                self.conn.execute(f'SET LOCAL search_path TO "{self.schema}", public')
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
