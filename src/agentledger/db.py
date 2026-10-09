"""Operational store. Ledger, audit and findings tables are append-only:
triggers reject UPDATE/DELETE so history cannot be rewritten through the app or
casually through the database. Each ledger entry and audit event is also hash
chained, so tampering below the app is detectable (see verify_chain).

Two backends share this interface (`open_store`): SQLite for the local demo profile and PostgreSQL
(`AGENTLEDGER_DATABASE=postgres`, see agentledger.pg), where the database itself enforces the posting rules.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import secrets
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('business','individual')),
    entity_type TEXT,
    formed_under TEXT NOT NULL DEFAULT 'domestic',
    tax_id_last4 TEXT,
    emails TEXT NOT NULL DEFAULT '[]',
    aliases TEXT NOT NULL DEFAULT '[]',
    consent_7216_at TEXT,
    closed_through TEXT,
    domain TEXT NOT NULL DEFAULT 'general',
    facts TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS accounts (
    client_id TEXT NOT NULL REFERENCES clients(id),
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('asset','liability','equity','revenue','expense')),
    PRIMARY KEY (client_id, code)
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL REFERENCES clients(id),
    date TEXT NOT NULL,
    memo TEXT NOT NULL,
    source TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reverses INTEGER REFERENCES entries(id),
    document_id TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS postings (
    entry_id INTEGER NOT NULL REFERENCES entries(id),
    line INTEGER NOT NULL,
    account_code TEXT NOT NULL,
    amount TEXT NOT NULL,
    tax_treatment TEXT,
    PRIMARY KEY (entry_id, line)
);

CREATE TABLE IF NOT EXISTS assets (
    client_id TEXT NOT NULL REFERENCES clients(id),
    id TEXT NOT NULL,
    description TEXT NOT NULL,
    cost TEXT NOT NULL,
    acquired TEXT NOT NULL,
    placed_in_service TEXT NOT NULL,
    recovery_years INTEGER NOT NULL,
    book_life_years INTEGER NOT NULL,
    sec179_elected TEXT NOT NULL DEFAULT '0',
    PRIMARY KEY (client_id, id)
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    client_id TEXT REFERENCES clients(id),
    sha256 TEXT NOT NULL UNIQUE,
    original_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    channel TEXT NOT NULL,
    received_at TEXT NOT NULL,
    sender TEXT,
    parent_id TEXT,
    doc_type TEXT,
    tax_year INTEGER,
    confidence REAL,
    status TEXT NOT NULL,
    vault_path TEXT,
    fields TEXT NOT NULL DEFAULT '{}',
    summary TEXT,
    classified_by TEXT,
    text_excerpt TEXT
);

CREATE TABLE IF NOT EXISTS document_versions (
    document_id TEXT NOT NULL REFERENCES documents(id),
    version INTEGER NOT NULL,
    locator TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (document_id, version)
);

CREATE TABLE IF NOT EXISTS legal_holds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL REFERENCES clients(id),
    document_id TEXT REFERENCES documents(id),
    reason TEXT NOT NULL,
    placed_by TEXT NOT NULL,
    placed_at TEXT NOT NULL,
    released_by TEXT,
    released_at TEXT,
    release_reason TEXT
);

CREATE TABLE IF NOT EXISTS deletion_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    client_id TEXT,
    sha256 TEXT NOT NULL,
    locators TEXT NOT NULL,
    retention_class TEXT,
    retain_until TEXT,
    deleted_by TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    reason TEXT NOT NULL
);

-- Write-ahead deletion (re-audit of 952ee96, finding 4): one row per stored object to delete, committed with the
-- receipt before any byte is touched, and one row per attempt's outcome. Pending = no 'deleted'/'kept_shared' outcome.
CREATE TABLE IF NOT EXISTS blob_deletions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL REFERENCES deletion_receipts(id),
    document_id TEXT NOT NULL,
    locator TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blob_deletion_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deletion_id INTEGER NOT NULL REFERENCES blob_deletions(id),
    outcome TEXT NOT NULL CHECK (outcome IN ('started', 'deleted', 'failed', 'kept_shared')),
    detail TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL
);

-- What retention is counted from (finding 5): filings, amendments, payments and confirmations that no return was
-- required, recorded by a CPA for returns filed outside AgentLedger. Returns filed through AgentLedger are read from
-- their workflow events.
CREATE TABLE IF NOT EXISTS tax_year_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL REFERENCES clients(id),
    tax_year INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('filed', 'amended', 'payment', 'not_required', 'owners_filed')),
    occurred_on TEXT NOT NULL,
    form TEXT NOT NULL,
    period TEXT CHECK (period IS NULL OR period IN ('Q1', 'Q2', 'Q3', 'Q4')),
    due_on TEXT,
    note TEXT NOT NULL,
    recorded_by TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

-- A filed document moved to another client, with the reason: holds on the client it left still cover it.
CREATE TABLE IF NOT EXISTS document_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id),
    from_client TEXT NOT NULL,
    to_client TEXT NOT NULL,
    reason TEXT NOT NULL,
    moved_by TEXT NOT NULL,
    moved_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS document_moves_document ON document_moves (document_id);

-- A basis record whose property was disposed of, released by a CPA: then kept as a record of the disposition year.
CREATE TABLE IF NOT EXISTS basis_releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id),
    disposed_tax_year INTEGER NOT NULL,
    note TEXT NOT NULL,
    released_by TEXT NOT NULL,
    released_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS legal_holds_no_delete BEFORE DELETE ON legal_holds
BEGIN SELECT RAISE(ABORT, 'legal holds are released, never deleted'); END;
CREATE TRIGGER IF NOT EXISTS legal_holds_release_only BEFORE UPDATE ON legal_holds
WHEN OLD.released_at IS NOT NULL OR NEW.client_id IS NOT OLD.client_id OR NEW.document_id IS NOT OLD.document_id
     OR NEW.reason IS NOT OLD.reason OR NEW.placed_by IS NOT OLD.placed_by OR NEW.placed_at IS NOT OLD.placed_at
BEGIN SELECT RAISE(ABORT, 'a legal hold can only be released, once (append-only)'); END;

CREATE TABLE IF NOT EXISTS entry_documents (
    entry_id INTEGER NOT NULL REFERENCES entries(id),
    document_id TEXT NOT NULL REFERENCES documents(id),
    linked_by TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (entry_id, document_id)
);

CREATE TABLE IF NOT EXISTS commands (
    scope TEXT NOT NULL,
    command_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (scope, command_id)
);

CREATE TABLE IF NOT EXISTS info_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL REFERENCES clients(id),
    document_id TEXT REFERENCES documents(id),
    form TEXT NOT NULL,
    tax_year INTEGER NOT NULL,
    payer TEXT,
    amount TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(id),
    check_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info','low','medium','high','critical')),
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '[]',
    citation TEXT,
    owner TEXT NOT NULL CHECK (owner IN ('client','cpa','both')),
    tax_year INTEGER,
    first_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finding_resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id TEXT NOT NULL REFERENCES findings(id),
    actor TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('explained','corrected','accepted_risk','reopened')),
    note TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS precedents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    situation TEXT NOT NULL,
    judgment TEXT NOT NULL,
    citations TEXT NOT NULL DEFAULT '[]',
    domain TEXT,
    author TEXT NOT NULL,
    client_id TEXT,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL REFERENCES clients(id),
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    role TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS engagements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL REFERENCES clients(id),
    type TEXT NOT NULL,
    tax_year INTEGER,
    stage TEXT NOT NULL,
    owner TEXT,
    due_date TEXT,
    fee TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT REFERENCES clients(id),
    engagement_id INTEGER REFERENCES engagements(id),
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    assignee TEXT NOT NULL CHECK (assignee IN ('cpa','client','agent')),
    due TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','cancelled')),
    source TEXT NOT NULL,
    dedupe_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    done_at TEXT,
    done_note TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT REFERENCES clients(id),
    direction TEXT NOT NULL CHECK (direction IN ('in','out')),
    channel TEXT NOT NULL,
    to_addr TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','sent','received','failed')),
    source TEXT NOT NULL,
    dedupe_key TEXT UNIQUE,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL REFERENCES clients(id),
    kind TEXT NOT NULL CHECK (kind IN ('customer','vendor','both')),
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    tin_last4 TEXT,
    entity_type TEXT,
    w9_on_file INTEGER NOT NULL DEFAULT 0,
    terms_days INTEGER NOT NULL DEFAULT 30,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL REFERENCES clients(id),
    party_id INTEGER REFERENCES parties(id),
    title TEXT NOT NULL,
    stage TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '0',
    expected_close TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL REFERENCES clients(id),
    party_id INTEGER NOT NULL REFERENCES parties(id),
    number TEXT NOT NULL,
    issued TEXT NOT NULL,
    due TEXT NOT NULL,
    amount TEXT NOT NULL,
    sales_tax TEXT NOT NULL DEFAULT '0',
    description TEXT NOT NULL,
    entry_id INTEGER REFERENCES entries(id),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','paid','void')),
    paid_entry_id INTEGER REFERENCES entries(id),
    UNIQUE (client_id, number)
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    role TEXT NOT NULL,
    client_id TEXT,
    action TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    tier TEXT NOT NULL,
    model TEXT NOT NULL,
    task TEXT NOT NULL,
    client_id TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    ok INTEGER NOT NULL,
    note TEXT
);
"""

APPEND_ONLY = ("commands", "entries", "postings", "audit", "finding_resolutions", "info_returns", "ai_usage", "entry_documents",
               "precedents", "document_versions", "deletion_receipts", "blob_deletions", "blob_deletion_results",
               "tax_year_events", "document_moves", "basis_releases")

# Columns added after a table first shipped: (table, column, type) for stores created before them.
UPGRADES = (("documents", "retention_class", "TEXT"), ("documents", "retain_until", "TEXT"), ("documents", "deleted_at", "TEXT"),
            ("documents", "retention_confirmed_by", "TEXT"), ("documents", "retention_confirmed_at", "TEXT"),
            ("tax_year_events", "period", "TEXT"))


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, col, kind in UPGRADES:
        if col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
    for table in APPEND_ONLY:
        for op in ("UPDATE", "DELETE"):
            conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
            )
    return conn


class ThreadLocalConnection:
    """Drop-in for a sqlite3.Connection that gives each thread its own connection.

    The web server, the agent scheduler and streaming answers run concurrently; SQLite is
    safest with one connection per thread, serialized for writes by BEGIN IMMEDIATE.
    """

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self.location = str(self._path.resolve())
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._closed = False
        connect(self._path).close()  # create schema once up front
        _OPEN.add(self)

    def _get(self) -> sqlite3.Connection:
        if self._closed:
            raise DatabaseError("this firm store has been closed")
        c = getattr(self._local, "conn", None)
        if c is None:
            c = connect(self._path)
            self._local.conn = c
            self._all.append(c)
        return c

    def set_scope(self, clients: Any) -> None:
        """Row-level client scope exists on PostgreSQL only; the SQLite demo profile is one trusted firm."""

    def close(self) -> None:
        """Close every thread's connection; the store cannot be used afterwards."""
        self._closed = True
        for c in self._all:
            c.close()
        self._all.clear()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)

    def __enter__(self) -> sqlite3.Connection:
        return self._get().__enter__()

    def __exit__(self, *exc: Any) -> Any:
        return self._get().__exit__(*exc)


class DatabaseError(sqlite3.DatabaseError):
    """A database error from either backend (sqlite3.DatabaseError handlers keep working)."""


class IntegrityError(DatabaseError, sqlite3.IntegrityError):
    """A constraint or append-only violation from either backend."""


def backend() -> str:
    return os.environ.get("AGENTLEDGER_DATABASE", "sqlite").strip().lower() or "sqlite"


def is_pg(conn: Any) -> bool:
    return getattr(conn, "dialect", "sqlite") == "postgres"


def firm_of(path: Path | str) -> str | None:
    """.../tenants/<firm>/state/agentledger.db -> <firm>; the single-firm (development) store has none. Read from the
    end of the path, so a directory named "tenants" above the platform root can never stand in for the firm's."""
    parts = Path(path).parts
    if len(parts) >= 4 and parts[-4] == "tenants" and parts[-2] == "state":
        return parts[-3]
    return None


def pg_name(name: str) -> str:
    """A PostgreSQL identifier for a store. AGENTLEDGER_PG_SCHEMA_PREFIX keeps test runs apart."""
    name = os.environ.get("AGENTLEDGER_PG_SCHEMA_PREFIX", "") + name
    return re.sub(r"[^a-z0-9_]", "_", name.lower())[:63]


def schema_for(path: Path | str) -> str:
    """The PostgreSQL schema for a firm store path in schema tenancy: tenants/<firm>/... -> firm_<firm>."""
    firm = firm_of(path)
    return pg_name(f"firm_{firm}" if firm else "agentledger")


# Every store opened in this process, so a firm's deletion can close its connections before removing the data.
_OPEN: "weakref.WeakSet[Any]" = weakref.WeakSet()


def open_store(path: Path | str) -> Any:
    """A firm's operational store on the configured backend."""
    if backend() == "postgres":
        from .pg import provision
        from .pg.compat import PgStore

        url, schema = provision.store_location(path)
        return PgStore(schema, url=url)
    return ThreadLocalConnection(path)


def close_stores(location: str) -> None:
    for store in list(_OPEN):
        if getattr(store, "location", None) == location:
            store.close()


def store_exists(path: Path | str) -> bool:
    """Whether a firm's store exists, without creating it (opening a store creates or migrates it)."""
    if backend() == "postgres":
        from .pg import provision

        return provision.store_exists(path)
    return Path(path).exists()


def destroy_store(path: Path | str) -> str:
    """Permanently remove a firm's operational store (ledger, CRM, returns, workflow history, audit trail).
    Open connections in this process are closed first. Returns a description of what was removed."""
    if backend() == "postgres":
        from .pg import provision

        return provision.destroy(path)
    path = Path(path)
    close_stores(str(path.resolve()))
    removed = []
    for f in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        if f.exists():
            f.unlink()
            removed.append(f.name)
    return f"sqlite file {path.name} removed" if removed else f"no sqlite file at {path.name}"


GENESIS = "0" * 64


def chain_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def rows(conn: sqlite3.Connection, sql: str, *args: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def count(conn: Any, sql: str, *args: Any) -> int:
    """The single number a COUNT/SUM query returns (0 when it returns no row or NULL)."""
    r = conn.execute(sql, args).fetchone()
    return int(r[0] or 0) if r else 0


def one(conn: sqlite3.Connection, sql: str, *args: Any) -> dict[str, Any] | None:
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ units of work and durable commands

class CommandConflict(Exception):
    """A command id was reused with a different payload."""


def _raw(conn: Any) -> sqlite3.Connection:
    return conn._get() if isinstance(conn, ThreadLocalConnection) or is_pg(conn) and hasattr(conn, "_get") else conn


def lock(conn: Any, key: str) -> None:
    """Serialize transactions that touch the same thing (a client's legal holds, a stored object), held until the
    enclosing unit_of_work ends. SQLite needs nothing more: every unit_of_work takes the database's write lock (BEGIN
    IMMEDIATE). PostgreSQL takes a transaction-scoped advisory lock, keyed by schema so firms never contend."""
    raw = _raw(conn)
    if not raw.in_transaction:
        raise RuntimeError("db.lock is only meaningful inside a unit_of_work")
    if is_pg(conn):
        raw.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':' || ?, 0))", (key,))


@contextmanager
def unit_of_work(conn: Any) -> Iterator[sqlite3.Connection]:
    """One atomic transaction for a whole business operation. Nested calls become savepoints, so a ledger posting
    inside an invoice, together with its audit record and command receipt, commits or rolls back as one."""
    raw = _raw(conn)
    if raw.in_transaction:
        sp = "sp_" + secrets.token_hex(6)
        raw.execute(f"SAVEPOINT {sp}")
        try:
            yield raw
        except BaseException:
            raw.execute(f"ROLLBACK TO {sp}")
            raw.execute(f"RELEASE {sp}")
            raise
        raw.execute(f"RELEASE {sp}")
    else:
        raw.execute("BEGIN IMMEDIATE")
        try:
            yield raw
        except BaseException:
            raw.execute("ROLLBACK")
            raise
        try:
            raw.execute("COMMIT")
        except BaseException:
            # A COMMIT can fail (a deferred constraint, a full disk, a lost connection). Nothing is durable then, and
            # the connection must not stay inside the dead transaction holding the write lock (SQLite keeps it open).
            if raw.in_transaction:
                raw.execute("ROLLBACK")
            raise


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def run_command(conn: Any, scope: str, command_id: str | None, kind: str, payload: Any, fn) -> Any:
    """Execute `fn` at most once per (scope, command_id), atomically with its receipt.

    Same id and payload: the original result is returned and nothing runs again. Same id, different payload:
    CommandConflict. No id: the operation still runs as one unit of work."""
    if not command_id:
        with unit_of_work(conn):
            return fn()
    h = payload_hash({"kind": kind, "payload": payload})
    table = "app_commands" if is_pg(conn) else "commands"  # on PostgreSQL `commands` holds the ledger's own receipts
    with unit_of_work(conn) as c:
        if is_pg(conn):  # serialize concurrent retries of one command, as BEGIN IMMEDIATE does on SQLite
            c.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"{scope}:{command_id}",))
        row = c.execute(f"SELECT kind, payload_hash, result FROM {table} WHERE scope = ? AND command_id = ?", (scope, command_id)).fetchone()
        if row:
            if row["payload_hash"] != h:
                raise CommandConflict(f"command {command_id} was already used for a different {row['kind']}")
            return json.loads(row["result"])
        result = fn()
        c.execute(f"INSERT INTO {table} (scope, command_id, kind, payload_hash, result) VALUES (?, ?, ?, ?, ?)",
                  (scope, command_id, kind, h, json.dumps(result, default=str)))
        return result
