"""A firm store on PostgreSQL that speaks the small sqlite3 interface the modules use.

The modules were written against `sqlite3.Connection`: `?` placeholders, `cursor.lastrowid`, rows readable by name
and by position, ISO-text dates and text amounts. `PgStore` provides exactly that on psycopg, so each module runs
unchanged on either backend while the financial rules move into the database:

* every connection logs in as the store's own runtime role (SELECT everywhere, INSERT/UPDATE on operational
  tables, EXECUTE on the ledger functions, no writes to entries, postings, receipts or audit, nothing in any other
  store); owner credentials are used only to migrate;
* the session's client scope is set on connect and per request (`set_scope`): firm-wide for agents and firm staff
  until engagement grants arrive (backlog F-05), one client for a client user;
* database errors surface as the exceptions the code already handles (`db.IntegrityError`, `ClosedPeriod`, ...).

Connections use the direct (unpooled) endpoint: the scope is a session setting, which a transaction-mode pooler
would not keep.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterator, Sequence

from .. import db
from . import connect, database_of, migrate, migration_url, runtime_base_url, runtime_role, runtime_url, translate, with_database

# Tables whose integer key the database assigns: an INSERT returns it as `lastrowid`.
IDENTITY = {"info_returns": "id", "finding_resolutions": "id", "precedents": "id", "contacts": "id", "engagements": "id",
            "tasks": "id", "messages": "id", "parties": "id", "deals": "id", "invoices": "id", "ai_usage": "id",
            "entries": "id", "outbox": "id", "audit": "seq", "legal_holds": "id", "deletion_receipts": "id",
            "fact_assertions": "id", "fact_conflicts": "id"}
_INSERT = re.compile(r"^\s*INSERT\s+INTO\s+\"?(\w+)\"?", re.I)
_DDL = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.I | re.M)


def translate_sql(sql: str, params: bool = True) -> str:
    """`?` placeholders to `%s` outside quoted strings and identifiers. With parameters psycopg interpolates, so a
    literal `%` (LIKE 'bank%') is doubled; without parameters the text is sent as is."""
    if sql.strip().upper() == "BEGIN IMMEDIATE":
        return "BEGIN"
    if not params:
        return sql
    out, quote = [], None
    for ch in sql:
        if quote:
            out.append("%%" if ch == "%" else ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


def _plain(v: Any) -> Any:
    """Values as the SQLite store returned them: dates as ISO text, amounts as text, JSON as text."""
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


class Row:
    """Readable by column name and by position, and `dict(row)` works, like sqlite3.Row."""

    __slots__ = ("_names", "_values")

    def __init__(self, names: Sequence[str], values: Sequence[Any]):
        self._names = tuple(names)
        self._values = tuple(_plain(v) for v in values)

    def keys(self) -> list[str]:
        return list(self._names)

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, (int, slice)):
            return self._values[key]
        try:
            return self._values[self._names.index(key)]
        except ValueError:
            raise IndexError(f"no column {key}") from None

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._names, self._values))})"


class Cursor:
    def __init__(self, cur, lastrowid: int | None = None, prefetched: list[Row] | None = None):
        self._cur = cur
        self.lastrowid = lastrowid
        self.rowcount = cur.rowcount if cur is not None else -1
        self._rows = prefetched

    def _all(self) -> list[Row]:
        if self._rows is None:
            if self._cur is None or self._cur.description is None:
                self._rows = []
            else:
                names = [d.name for d in self._cur.description]
                self._rows = [Row(names, r) for r in self._cur.fetchall()]
        return self._rows

    def fetchone(self) -> Row | None:
        rows = self._all()
        return rows.pop(0) if rows else None

    def fetchall(self) -> list[Row]:
        rows, self._rows = self._all(), []
        return rows

    def __iter__(self) -> Iterator[Row]:
        return iter(self.fetchall())


class PgConnection:
    dialect = "postgres"

    def __init__(self, raw, schema: str, scope: str):
        self.raw = raw
        self.schema = schema
        raw.execute(f'SET search_path TO "{schema}", public')
        self.set_scope(scope)

    def set_scope(self, scope: str) -> None:
        self.raw.execute("SELECT set_config('agentledger.clients', %s, false)", (scope,))

    @property
    def in_transaction(self) -> bool:
        from psycopg import pq

        return self.raw.info.transaction_status != pq.TransactionStatus.IDLE

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        import psycopg

        text = translate_sql(sql, bool(params))
        m = _INSERT.match(sql)
        key = IDENTITY.get(m.group(1).lower()) if m else None
        if key and not re.search(r"\bRETURNING\b", sql, re.I):
            text += f" RETURNING {key}"
        try:
            cur = self.raw.execute(text, tuple(params) if params else None)
        except psycopg.Error as exc:
            raise _error(exc) from exc
        if key and cur.description is not None:
            got = cur.fetchone()
            return Cursor(cur, lastrowid=int(got[0]) if got else None, prefetched=[])
        return Cursor(cur)

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> None:
        for params in seq:
            self.execute(sql, params)

    def executescript(self, script: str) -> None:
        """Module schemas are SQLite DDL; on PostgreSQL the schema comes from the migrations instead."""
        if _DDL.search(script):
            raise NotImplementedError("executescript on PostgreSQL only accepts schema scripts (handled by migrations)")

    def commit(self) -> None:
        if self.in_transaction:
            self.raw.execute("COMMIT")

    def rollback(self) -> None:
        if self.in_transaction:
            self.raw.execute("ROLLBACK")

    def close(self) -> None:
        self.raw.close()


def _error(exc: Exception) -> Exception:
    import psycopg

    code = getattr(exc, "sqlstate", None) or ""
    message = getattr(getattr(exc, "diag", None), "message_primary", None) or str(exc)
    if code == "AL006" or isinstance(exc, psycopg.IntegrityError):
        return db.IntegrityError(message)
    if code.startswith("AL"):
        return translate(exc)
    return db.DatabaseError(message)


def location(url: str, schema: str) -> str:
    """Identifies a store across processes: database name and schema (never the credentials)."""
    return f"{database_of(url)}/{schema}"


def close_all(prefix: str = "") -> None:
    """Close every open store whose schema starts with `prefix` (test teardown, process shutdown)."""
    for store in list(db._OPEN):
        if isinstance(store, PgStore) and store.schema.startswith(prefix):
            store.close()


class PgStore:
    """One firm's store: a schema (development, tests) or a database (production), one connection per thread."""

    dialect = "postgres"

    def __init__(self, schema: str, *, url: str | None = None, scope: str = "*", migrate_on_open: bool = True,
                 own_database: bool = False):
        """`url` names the server and database (its credentials are ignored); the store connects as its runtime role.
        With owner credentials in the environment, pending migrations are applied first (development); production
        runs them as a release step and the API never holds owner credentials."""
        self.schema, self.scope = schema, scope
        base = url or runtime_base_url()
        if not base:
            raise RuntimeError("AGENTLEDGER_DATABASE=postgres needs DATABASE_URL_UNPOOLED (or DATABASE_URL)")
        self.database = database_of(base)
        self.role = runtime_role(self.database, schema)
        self.location = location(base, schema)
        self._runtime = runtime_url(base, self.role)
        self._local = threading.local()
        self._all: list[PgConnection] = []
        self._closed = False
        owner_url = migration_url()
        if migrate_on_open and owner_url:
            owner = connect(with_database(owner_url, self.database))
            try:
                migrate(owner, schema, self.role, own_database=own_database)
            finally:
                owner.close()
        db._OPEN.add(self)

    def _get(self) -> PgConnection:
        if self._closed:
            raise db.DatabaseError("this firm store has been closed")
        c = getattr(self._local, "conn", None)
        if c is None or c.raw.closed:
            c = PgConnection(connect(self._runtime), self.schema, self.scope)
            self._local.conn = c
            self._all.append(c)
        return c

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)

    def set_scope(self, clients: Sequence[str]) -> None:
        """Scope this thread's connection (one request) to `clients`; ["*"] is firm-wide."""
        self._get().set_scope(",".join(clients) or "-")

    def close(self) -> None:
        """Close every thread's connection; the store cannot be used afterwards."""
        self._closed = True
        for c in self._all:
            if not c.raw.closed:
                c.raw.close()
        self._all.clear()
