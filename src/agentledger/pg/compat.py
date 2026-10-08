"""A firm store on PostgreSQL that speaks the small sqlite3 interface the modules use.

The modules were written against `sqlite3.Connection`: `?` placeholders, `cursor.lastrowid`, rows readable by name
and by position, ISO-text dates and text amounts. `PgStore` provides exactly that on psycopg, so each module runs
unchanged on either backend while the financial rules move into the database:

* every connection runs as `agentledger_app` (SELECT everywhere, INSERT/UPDATE on operational tables, EXECUTE on
  the ledger functions, no writes to entries, postings, receipts or audit);
* the session's client scope is set on connect (`*` = firm-wide until engagement grants arrive, backlog F-05);
* database errors surface as the exceptions the code already handles (`db.IntegrityError`, `ClosedPeriod`, ...).

Connections use the direct (unpooled) endpoint: the role and scope are session settings, which a transaction-mode
pooler would not keep.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterator, Sequence

from .. import db
from . import APP_ROLE, connect, dsn, migrate, translate

# Tables whose integer key the database assigns: an INSERT returns it as `lastrowid`.
IDENTITY = {"info_returns": "id", "finding_resolutions": "id", "precedents": "id", "contacts": "id", "engagements": "id",
            "tasks": "id", "messages": "id", "parties": "id", "deals": "id", "invoices": "id", "ai_usage": "id",
            "entries": "id", "outbox": "id", "audit": "seq"}
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
        raw.execute(f"SET ROLE {APP_ROLE}")
        raw.execute("SELECT set_config('agentledger.clients', %s, false)", (scope,))

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
    from urllib.parse import urlparse

    return f"{urlparse(url).path.lstrip('/')}/{schema}"


def close_all(prefix: str = "") -> None:
    """Close every open store whose schema starts with `prefix` (test teardown, process shutdown)."""
    for store in list(db._OPEN):
        if isinstance(store, PgStore) and store.schema.startswith(prefix):
            store.close()


class PgStore:
    """One firm's store: a schema (development, tests) or a database (production), one connection per thread."""

    dialect = "postgres"

    def __init__(self, schema: str, *, url: str | None = None, scope: str = "*", migrate_on_open: bool = True):
        self.schema, self.scope = schema, scope
        self.url = url or dsn(direct=True)
        if not self.url:
            raise RuntimeError("AGENTLEDGER_DATABASE=postgres needs DATABASE_URL_UNPOOLED (or DATABASE_URL)")
        self.location = location(self.url, schema)
        self._local = threading.local()
        self._all: list[PgConnection] = []
        self._closed = False
        if migrate_on_open:
            owner = connect(self.url)
            try:
                migrate(owner, schema)
            finally:
                owner.close()
        db._OPEN.add(self)

    def _get(self) -> PgConnection:
        if self._closed:
            raise db.DatabaseError("this firm store has been closed")
        c = getattr(self._local, "conn", None)
        if c is None or c.raw.closed:
            c = PgConnection(connect(self.url), self.schema, self.scope)
            self._local.conn = c
            self._all.append(c)
        return c

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)

    def close(self) -> None:
        """Close every thread's connection; the store cannot be used afterwards."""
        self._closed = True
        for c in self._all:
            if not c.raw.closed:
                c.raw.close()
        self._all.clear()
