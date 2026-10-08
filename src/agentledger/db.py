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
               "precedents")


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
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
        self._local = threading.local()
        connect(self._path).close()  # create schema once up front

    def _get(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = connect(self._path)
            self._local.conn = c
        return c

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


def schema_for(path: Path | str) -> str:
    """The PostgreSQL schema for a firm store path: tenants/<firm>/state/agentledger.db -> firm_<firm>.
    AGENTLEDGER_PG_SCHEMA_PREFIX keeps test runs apart."""
    path = Path(path)
    parts = path.parts
    name = f"firm_{parts[parts.index('tenants') + 1]}" if "tenants" in parts[:-1] else "agentledger"
    name = os.environ.get("AGENTLEDGER_PG_SCHEMA_PREFIX", "") + name
    return re.sub(r"[^a-z0-9_]", "_", name.lower())[:63]


def open_store(path: Path | str) -> Any:
    """A firm's operational store on the configured backend."""
    if backend() == "postgres":
        from .pg.compat import PgStore

        return PgStore(schema_for(path))
    return ThreadLocalConnection(path)


GENESIS = "0" * 64


def chain_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def rows(conn: sqlite3.Connection, sql: str, *args: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def one(conn: sqlite3.Connection, sql: str, *args: Any) -> dict[str, Any] | None:
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ units of work and durable commands

class CommandConflict(Exception):
    """A command id was reused with a different payload."""


def _raw(conn: Any) -> sqlite3.Connection:
    return conn._get() if isinstance(conn, ThreadLocalConnection) or is_pg(conn) and hasattr(conn, "_get") else conn


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
        raw.execute("COMMIT")


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
