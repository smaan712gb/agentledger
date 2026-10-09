"""Firm offboarding (re-audit of 952ee96, finding 3, and its adversarial review): where a firm's store is, what it
holds, and sealing it against new legal holds before anything is destroyed.

The store is located from the platform's own records (the provisioning journal on PostgreSQL, the tenant directory on
SQLite), never from the environment of whoever runs the offboarding: a store that should exist and is not found where
the records say is refused (`NotFound`), never read as "no holds".

`seal` takes the store's legal-hold table exclusively (PostgreSQL: LOCK TABLE ... IN ACCESS EXCLUSIVE MODE, which waits
for every hold still being placed to commit or roll back; SQLite: BEGIN IMMEDIATE), reads the holds and a summary of the
store in that same transaction and, only if no hold is active, removes the right to add holds (PostgreSQL: REVOKE
INSERT from the store's runtime role; SQLite: a trigger refusing inserts). A hold committed before the seal is seen and
stops the offboarding (`Held`); any later attempt is refused by the database. `unseal` gives the right back when an
offboarding is cancelled. `destroy` removes exactly the recorded store.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HOLD_COLUMNS = ("id", "client_id", "document_id", "reason", "placed_by", "placed_at", "released_by", "released_at",
                "release_reason")
SEALED = "legal_holds_sealed"


class Held(Exception):
    def __init__(self, holds: list[dict[str, Any]]):
        self.holds = holds
        ids = ", ".join(f"#{h['id']} ({h['client_id']})" for h in holds)
        super().__init__(f"{len(holds)} active legal hold(s) {ids}")


class NotFound(Exception):
    """The store is not where the platform's records say it is: nothing can be concluded about its holds."""


class NotSealed(Exception):
    """A store about to be removed is no longer sealed (someone gave back the right to add holds): nothing is removed."""


@dataclass(frozen=True)
class StoreRef:
    backend: str                       # "sqlite" or "postgres"
    path: Path | None = None           # the SQLite store
    resource: str | None = None        # PostgreSQL: "schema" or "database", as provisioning recorded it
    database: str | None = None
    schema: str | None = None

    @property
    def label(self) -> str:
        if self.backend == "sqlite":
            return str(self.path)
        return f"{self.resource} {self.schema if self.resource == 'schema' else self.database}"


def _summary(holds: list[dict[str, Any]], documents: int, receipts: int, head: Any) -> dict[str, Any]:
    return {"holds": holds, "documents": documents, "documents_deleted_under_retention": receipts,
            "audit_head": {"seq": head[0], "hash": head[1]} if head else None}


# ------------------------------------------------------------------------------------------------- SQLite
def _seal_sqlite(path: Path, allow_missing: bool) -> dict[str, Any] | None:
    if not path.exists():
        if allow_missing:
            return None
        raise NotFound(f"the firm's store {path} does not exist")
    c = sqlite3.connect(path, timeout=120, isolation_level=None)
    try:
        c.execute("BEGIN IMMEDIATE")                       # in-flight holds finish first; later ones wait, then fail
        try:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'legal_holds'").fetchone():
                if allow_missing:
                    c.execute("ROLLBACK")
                    return None
                raise NotFound(f"the firm's store {path} has no legal-hold records")
            holds = [dict(zip(HOLD_COLUMNS, r)) for r in c.execute(f"SELECT {', '.join(HOLD_COLUMNS)} FROM legal_holds ORDER BY id")]
            active = [h for h in holds if h["released_at"] is None]
            if active:
                raise Held(active)
            summary = _summary(holds, c.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                               c.execute("SELECT COUNT(*) FROM deletion_receipts").fetchone()[0],
                               c.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone())
            c.execute(f"CREATE TRIGGER IF NOT EXISTS {SEALED} BEFORE INSERT ON legal_holds "
                      "BEGIN SELECT RAISE(ABORT, 'this firm is being offboarded: no new legal holds'); END")
            c.execute("COMMIT")
            return summary
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------- PostgreSQL
def _owner(database: str) -> Any:
    from ..pg import migration_url, provision, with_database

    url = migration_url()
    if not url:
        raise RuntimeError("offboarding needs the owner connection (AGENTLEDGER_MIGRATION_URL): run it as an operations job")
    return provision.connect(with_database(url, database))      # provisioning's connection (one place to stand in for it)


def _regclass(owner: Any, name: str) -> bool:
    row = owner.execute("SELECT to_regclass(%s)", (name,)).fetchone()
    return bool(row and row[0] is not None)


def _database_exists(database: str) -> bool:
    from ..pg import database_of, migration_url

    admin = _owner(database_of(migration_url() or ""))
    try:
        return bool(admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone())
    finally:
        admin.close()


def _seal_pg(ref: StoreRef, allow_missing: bool) -> dict[str, Any] | None:
    from psycopg import sql

    from ..pg import runtime_role

    assert ref.database and ref.schema
    if not _database_exists(ref.database):
        if allow_missing:
            return None
        raise NotFound(f"database {ref.database} recorded for the firm does not exist")
    owner = _owner(ref.database)
    schema = sql.Identifier(ref.schema)
    try:
        if not _regclass(owner, f'"{ref.schema}".legal_holds'):
            if allow_missing:
                return None                               # provisioning never got that far: no hold can exist
            raise NotFound(f"{ref.label} recorded for the firm has no legal-hold records")
        owner.execute("BEGIN")
        try:
            owner.execute("SELECT set_config('agentledger.clients', '*', true)")
            owner.execute("SET LOCAL lock_timeout = '120s'")
            owner.execute(sql.SQL("LOCK TABLE {}.legal_holds IN ACCESS EXCLUSIVE MODE").format(schema))
            holds = [dict(zip(HOLD_COLUMNS, r)) for r in owner.execute(
                sql.SQL("SELECT {} FROM {}.legal_holds ORDER BY id").format(
                    sql.SQL(", ").join(sql.Identifier(c) for c in HOLD_COLUMNS), schema)).fetchall()]
            active = [h for h in holds if h["released_at"] is None]
            if active:
                raise Held(active)
            summary = _summary(holds, owner.execute(sql.SQL("SELECT COUNT(*) FROM {}.documents").format(schema)).fetchone()[0],
                               owner.execute(sql.SQL("SELECT COUNT(*) FROM {}.deletion_receipts").format(schema)).fetchone()[0],
                               owner.execute(sql.SQL("SELECT seq, hash FROM {}.audit ORDER BY seq DESC LIMIT 1").format(schema)).fetchone())
            owner.execute(sql.SQL("REVOKE INSERT ON {}.legal_holds FROM {}").format(
                schema, sql.Identifier(runtime_role(ref.database, ref.schema))))
            owner.execute("COMMIT")
            return summary
        except BaseException:
            owner.execute("ROLLBACK")
            raise
    finally:
        owner.close()


# ------------------------------------------------------------------------------------------------- both
def seal(ref: StoreRef, *, allow_missing: bool = False) -> dict[str, Any] | None:
    """Read what the store holds and seal it against new legal holds, in one locked step. Raises Held if a hold is
    active (nothing is sealed then), NotFound if the store is not where the records say (unless `allow_missing`, for a
    store provisioning never finished: then None)."""
    if ref.backend == "sqlite":
        assert ref.path is not None
        return _seal_sqlite(ref.path, allow_missing)
    return _seal_pg(ref, allow_missing)


def intact(ref: StoreRef) -> bool:
    """Whether the store is there in full (its legal-hold table readable): after a removal attempt that failed, an
    intact store can be put back into use; anything else can only be finished."""
    if ref.backend == "sqlite":
        assert ref.path is not None
        if not ref.path.exists():
            return False
        c = sqlite3.connect(ref.path, timeout=120, isolation_level=None)
        try:
            return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'legal_holds'").fetchone())
        finally:
            c.close()
    assert ref.database and ref.schema
    if not _database_exists(ref.database):
        return False
    owner = _owner(ref.database)
    try:
        return _regclass(owner, f'"{ref.schema}".legal_holds')
    finally:
        owner.close()


def sealed(ref: StoreRef) -> bool:
    """Whether the store is sealed against new legal holds (left so by an offboarding or abandonment that never
    finished). A store that is not there is not sealed."""
    if ref.backend == "sqlite":
        assert ref.path is not None
        if not ref.path.exists():
            return False
        c = sqlite3.connect(ref.path, timeout=120, isolation_level=None)
        try:
            return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?", (SEALED,)).fetchone())
        finally:
            c.close()
    from ..pg import runtime_role

    assert ref.database and ref.schema
    if not _database_exists(ref.database):
        return False
    owner = _owner(ref.database)
    try:
        if not _regclass(owner, f'"{ref.schema}".legal_holds'):
            return False
        role = runtime_role(ref.database, ref.schema)
        if not owner.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            return False
        row = owner.execute("SELECT has_table_privilege(%s, %s, 'INSERT')", (role, f'"{ref.schema}".legal_holds')).fetchone()
        return not (row and row[0])
    finally:
        owner.close()


def unseal(ref: StoreRef) -> None:
    """An offboarding was cancelled: holds can be placed again."""
    if ref.backend == "sqlite":
        assert ref.path is not None
        if ref.path.exists():
            c = sqlite3.connect(ref.path, timeout=120, isolation_level=None)
            try:
                c.execute(f"DROP TRIGGER IF EXISTS {SEALED}")
            finally:
                c.close()
        return
    from psycopg import sql

    from ..pg import runtime_role

    assert ref.database and ref.schema
    if not _database_exists(ref.database):
        return
    owner = _owner(ref.database)
    try:
        if _regclass(owner, f'"{ref.schema}".legal_holds'):
            owner.execute(sql.SQL("GRANT INSERT ON {}.legal_holds TO {}").format(
                sql.Identifier(ref.schema), sql.Identifier(runtime_role(ref.database, ref.schema))))
    finally:
        owner.close()


def _verify_sqlite(path: Path) -> None:
    c = sqlite3.connect(path, timeout=120, isolation_level=None)
    try:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?", (SEALED,)).fetchone():
            raise NotSealed(f"the store {path} is not sealed")
        active = [dict(zip(HOLD_COLUMNS, r)) for r in c.execute(
            f"SELECT {', '.join(HOLD_COLUMNS)} FROM legal_holds WHERE released_at IS NULL ORDER BY id")]
        if active:
            raise Held(active)
    finally:
        c.close()


def _verify_pg_locked(owner: Any, ref: StoreRef) -> bool:
    """Inside the caller's transaction: lock the hold table, then check the seal still stands and no hold is active.
    False when the store has no hold table any more (already removed)."""
    from psycopg import sql

    from ..pg import runtime_role

    assert ref.database and ref.schema
    if not _regclass(owner, f'"{ref.schema}".legal_holds'):
        return False
    schema = sql.Identifier(ref.schema)
    owner.execute("SELECT set_config('agentledger.clients', '*', true)")
    owner.execute("SET LOCAL lock_timeout = '120s'")
    owner.execute(sql.SQL("LOCK TABLE {}.legal_holds IN ACCESS EXCLUSIVE MODE").format(schema))
    role = runtime_role(ref.database, ref.schema)
    if owner.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
        row = owner.execute("SELECT has_table_privilege(%s, %s, 'INSERT')", (role, f'"{ref.schema}".legal_holds')).fetchone()
        if row and row[0]:
            raise NotSealed(f"{ref.label} is not sealed: its runtime role can add legal holds")
    active = [dict(zip(HOLD_COLUMNS, r)) for r in owner.execute(
        sql.SQL("SELECT {} FROM {}.legal_holds WHERE released_at IS NULL ORDER BY id").format(
            sql.SQL(", ").join(sql.Identifier(c) for c in HOLD_COLUMNS), schema)).fetchall()]
    if active:
        raise Held(active)
    return True


def destroy(ref: StoreRef, *, neon: Any = None) -> str:
    """Remove exactly the recorded store (and its runtime role), after verifying, in the same locked step where the
    backend allows, that it is still sealed and holds nothing (NotSealed, Held). Returns what was removed; a store
    already gone is reported, so an interrupted removal can be finished."""
    from .. import db

    if ref.backend == "sqlite":
        assert ref.path is not None
        if not ref.path.exists():
            return f"no store at {ref.path} (already removed)"
        _verify_sqlite(ref.path)
        return db.destroy_store(ref.path)
    from psycopg import sql

    from ..pg import compat, database_of, drop_role, migration_url, runtime_base_url, runtime_role, with_database

    assert ref.database and ref.schema
    base = runtime_base_url() or ""
    db.close_stores(compat.location(with_database(base, ref.database), ref.schema))
    role = runtime_role(ref.database, ref.schema)
    if ref.resource == "database":
        from ..pg.provision import Neon

        if _database_exists(ref.database):                # verified sealed and empty of holds, then removed
            owner = _owner(ref.database)
            try:
                owner.execute("BEGIN")
                try:
                    _verify_pg_locked(owner, ref)
                    owner.execute("COMMIT")
                except BaseException:
                    owner.execute("ROLLBACK")
                    raise
            finally:
                owner.close()
        neon = neon or Neon()
        if ref.database in neon.databases():
            neon.delete_database(ref.database)
            done = f"Neon database {ref.database} deleted"
        else:
            done = f"no Neon database {ref.database}"
    else:
        owner = _owner(ref.database)
        try:
            existed = owner.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (ref.schema,)).fetchone()
            owner.execute("BEGIN")
            try:
                _verify_pg_locked(owner, ref)              # the check and the DROP in one transaction
                owner.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(ref.schema)))
                owner.execute("COMMIT")
            except BaseException:
                owner.execute("ROLLBACK")
                raise
            drop_role(owner, role)                         # its remaining grants are in this database
        finally:
            owner.close()
        done = f"schema {ref.schema} dropped from {ref.database}" if existed else f"no schema {ref.schema} in {ref.database}"
        return done + f"; runtime role {role} dropped"
    admin = _owner(database_of(migration_url() or ""))
    try:
        drop_role(admin, role)
    finally:
        admin.close()
    return done + f"; runtime role {role} dropped"
