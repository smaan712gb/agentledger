"""Restore drill (backlog F-13, Q33 "backup restore with active workflows").

A backup is worth what a restore from it proves. The drill restores a firm's store into a scratch copy and verifies
the copy the way an operator would after a real restore, while the live store keeps running:

1. export the live store's audit chain head, the anchors on record and the workflow instances in flight;
2. copy the store: SQLite with the online backup API (one consistent snapshot of the file); PostgreSQL as a fresh
   schema created from the current migrations with its own runtime role, loaded table by table from the firm's
   schema in foreign-key order with the owner connection (AGENTLEDGER_MIGRATION_URL; user triggers off while rows
   are copied, identity sequences reset afterwards), in the firm's own database, so no Neon API call is needed;
3. verify the copy: its audit chain recomputes, every anchor in the firm's object store still matches the copy
   (evidence/anchors.py: a copy that was rewritten or cut short fails here), and every parked workflow instance
   (`workflow_runs` running, sleeping or waiting) is present with the same step results and would resume
   (LocalRunner lists them from the copy; nothing is advanced);
4. record the drill in the live store's audit trail (`evidence.restore_drill`) and write the report under the
   firm's tenant directory (drills/<stamp>-<token>.json); the scratch copy is removed unless kept for inspection.

The copy is clearly scoped: a file under the tenant's drills/ directory, or a schema named drill_<token> that only
the drill's runtime role can use, dropped with that role when the drill ends.
"""

from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, cast

from .. import audit, db
from ..workflow.flows.submission import SubmissionFlow
from ..workflow.runner import DbRuns, LocalRunner, Run
from . import blobs
from .anchors import Anchors, iso, utcnow
from .offboarding import StoreRef

IN_FLIGHT = ("running", "sleeping", "waiting")
KNOWN_FLOWS = frozenset({SubmissionFlow.name})
SKIPPED_TABLES = frozenset({"schema_migrations"})      # the fresh schema records its own migrations


class DrillError(Exception):
    pass


@dataclass
class Copy:
    """A scratch copy of a firm store: how to read it, and how to remove it."""

    label: str
    conn: Any
    cleanup: Callable[[], None]
    path: Path | None = None          # SQLite: the copied file
    schema: str | None = None         # PostgreSQL: the scratch schema
    database: str | None = None


# ------------------------------------------------------------------------------------------------- copying
def copy_store(ref: StoreRef, *, scratch: Path) -> Copy:
    """A scratch copy of the store the platform's records name."""
    if ref.backend == "sqlite":
        assert ref.path is not None
        return _copy_sqlite(ref.path, scratch / "agentledger.db")
    return _copy_pg(ref)


def _copy_sqlite(path: Path, dest: Path) -> Copy:
    if not path.exists():
        raise DrillError(f"the firm's store {path} does not exist")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(path, timeout=120)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)                       # the online backup API: one consistent snapshot, WAL content included
        finally:
            dst.close()
    finally:
        src.close()
    conn = db.ThreadLocalConnection(dest)

    def cleanup() -> None:
        conn.close()
        shutil.rmtree(dest.parent, ignore_errors=True)

    return Copy(label=f"sqlite file {dest}", conn=conn, cleanup=cleanup, path=dest)


def _tables_in_fk_order(owner: Any, schema: str) -> list[str]:
    """Base tables of a schema, parents before children (self-references aside: a single INSERT ... SELECT checks
    its foreign keys when the statement ends, so rows that reference earlier rows of the same table load fine)."""
    tables = [r[0] for r in owner.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relkind IN ('r', 'p') ORDER BY c.relname", (schema,)).fetchall()]
    deps: dict[str, set[str]] = {t: set() for t in tables}
    for child, parent in owner.execute(
            "SELECT cl.relname, pr.relname FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid "
            "JOIN pg_class pr ON pr.oid = co.confrelid JOIN pg_namespace n ON n.oid = cl.relnamespace "
            "WHERE co.contype = 'f' AND n.nspname = %s", (schema,)).fetchall():
        if child != parent and child in deps and parent in deps:
            deps[child].add(parent)
    out: list[str] = []
    while deps:
        ready = sorted(t for t, d in deps.items() if not d - set(out))
        if not ready:
            raise DrillError(f"the foreign keys of {sorted(deps)} form a cycle; the drill cannot order the load")
        out += ready
        for t in ready:
            del deps[t]
    return out


def _columns(owner: Any, schema: str, table: str) -> list[tuple[str, bool]]:
    """(column, is an identity column) in table order; generated columns are left out (the copy recomputes them)."""
    return [(r[0], r[1] == "YES") for r in owner.execute(
        "SELECT column_name, is_identity FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
        "AND is_generated = 'NEVER' ORDER BY ordinal_position", (schema, table)).fetchall()]


def _drop_pg(owner_url: str, database: str, schema: str, role: str) -> None:
    from .. import pg

    owner = pg.connect(pg.with_database(owner_url, database))
    try:
        owner.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        pg.drop_role(owner, role)
    finally:
        owner.close()


def _copy_pg(ref: StoreRef) -> Copy:
    from psycopg import sql

    from .. import pg
    from ..pg.compat import PgStore

    assert ref.database and ref.schema
    owner_url, base = pg.migration_url(), pg.runtime_base_url()
    if not owner_url or not base:
        raise DrillError("the restore drill needs the owner connection (AGENTLEDGER_MIGRATION_URL): run it as an operations job")
    database, source = ref.database, ref.schema
    schema = db.pg_name(f"drill_{secrets.token_hex(4)}")
    role = pg.runtime_role(database, schema)
    owner = pg.connect(pg.with_database(owner_url, database))
    try:
        if not owner.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (source,)).fetchone():
            raise DrillError(f"schema {source} recorded for the firm does not exist in database {database}")
        pg.migrate(owner, schema)                 # the current migrations, in full, with the copy's own runtime role
        owner.execute("SELECT set_config('agentledger.clients', '*', false)")
        with owner.transaction():
            for table in _tables_in_fk_order(owner, source):
                if table in SKIPPED_TABLES or owner.execute("SELECT to_regclass(%s)", (f'"{schema}"."{table}"',)).fetchone()[0] is None:
                    continue                      # nothing in the current schema to load it into: reported by the row counts
                cols = _columns(owner, source, table)
                if not cols:
                    continue
                names = sql.SQL(", ").join(sql.Identifier(c) for c, _ in cols)
                identity = [c for c, is_identity in cols if is_identity]
                target = sql.Identifier(schema, table)
                owner.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER USER").format(target))
                owner.execute(sql.SQL("INSERT INTO {} ({}) {} SELECT {} FROM {}").format(
                    target, names, sql.SQL("OVERRIDING SYSTEM VALUE") if identity else sql.SQL(""), names, sql.Identifier(source, table)))
                owner.execute(sql.SQL("ALTER TABLE {} ENABLE TRIGGER USER").format(target))
                for col in identity:              # the copy's next ids continue where the firm's left off
                    owner.execute(sql.SQL("SELECT setval(pg_get_serial_sequence({}, {}), (SELECT COALESCE(MAX({}), 0) + 1 FROM {}), false)")
                                  .format(sql.Literal(f'"{schema}"."{table}"'), sql.Literal(col), sql.Identifier(col), target))
    except BaseException:
        owner.close()
        _drop_pg(owner_url, database, schema, role)
        raise
    owner.close()
    store = PgStore(schema, url=pg.with_database(base, database), migrate_on_open=False)

    def cleanup() -> None:
        store.close()
        _drop_pg(owner_url, database, schema, role)

    return Copy(label=f"schema {schema} in database {database}", conn=store, cleanup=cleanup, schema=schema, database=database)


# ------------------------------------------------------------------------------------------------- verification
def in_flight(conn: Any) -> list[Run]:
    return [r for r in DbRuns(conn).all() if r.status in IN_FLIGHT]


def _summary(run: Run) -> dict[str, Any]:
    return {"instance_id": run.instance_id, "flow": run.flow, "status": run.status, "wake_at": run.wake_at,
            "waiting_for": run.waiting_for, "steps": len(run.steps), "events": len(run.events)}


def not_resumable(run: Run) -> list[str]:
    """Why a parked instance could not resume on the restored store (empty: it can)."""
    problems = []
    if run.flow not in KNOWN_FLOWS:
        problems.append(f"unknown flow {run.flow!r}")
    if run.status in ("sleeping", "waiting"):
        if not run.wake_at:
            problems.append("parked without a wake time")
        else:
            try:
                datetime.fromisoformat(run.wake_at)
            except ValueError:
                problems.append(f"unreadable wake time {run.wake_at!r}")
    if run.status == "waiting" and not run.waiting_for:
        problems.append("waiting for no event")
    if not isinstance(run.steps, dict) or not isinstance(run.params, dict):
        problems.append("step results or parameters are not mappings")
    return problems


def verify_copy(copy: Copy, anchors: Anchors, source: Any) -> dict[str, Any]:
    """`anchors` is bound to the copy's connection and the firm's real anchor store: the anchors must still fit the
    copy. `source` is the live store, whose in-flight instances the copy must hold."""
    report = anchors.check()
    live = {r.instance_id: _summary(r) for r in in_flight(source)}
    runner = LocalRunner(cast(Any, None), [], runs=DbRuns(copy.conn))     # lists the copy's instances; nothing is advanced
    parked = [r for r in runner.runs.all() if r.status in IN_FLIGHT]
    restored = {r.instance_id: _summary(r) for r in parked}
    problems = {r.instance_id: not_resumable(r) for r in parked if not_resumable(r)}
    workflows = {"in_flight": len(restored), "matched": restored == live, "missing": sorted(set(live) - set(restored)),
                 "extra": sorted(set(restored) - set(live)), "resumable": sorted(i for i in restored if i not in problems),
                 "not_resumable": problems, "due_now": sorted(r.instance_id for r in runner.runnable()),
                 "instances": [restored[i] for i in sorted(restored)]}
    ok = bool(report["ok"]) and workflows["matched"] and not problems
    return {"ok": ok, "chain": report["chain"], "anchors": report, "workflow_runs": workflows}


# ------------------------------------------------------------------------------------------------- the drill
def run(plat: Any, firm_id: str, *, keep: bool = False, actor: str = "restore-drill",
        now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """The whole drill for one firm; returns the report (also written under the firm's tenant directory)."""
    clock = now or utcnow
    started = clock()
    plat.firm(firm_id)                                # AuthError for a firm the platform does not know
    ref = plat.store_ref(firm_id)
    tenant = Path(plat.tenant_dir(firm_id))
    if not db.store_exists(tenant / "state" / "agentledger.db"):
        raise DrillError(f"the store of firm {firm_id} ({ref.label}) does not exist; nothing to drill")
    source = db.open_store(tenant / "state" / "agentledger.db")
    source.set_scope(["*"])
    store, location = blobs.anchors_for_firm(tenant / blobs.ANCHORS, firm_id)
    keyring = plat.keys
    token = secrets.token_hex(4)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    try:
        live = Anchors(source, store, firm_id=firm_id, keyring=keyring, location=location, now=clock)
        export = {"audit_head": live.head(), "workflow_events": {k: v for k, v in live.workflow_heads().items() if k != "heads"},
                  "anchors": {"count": len(live.keys()), "latest": (live.latest_recorded() or {}).get("object_key")},
                  "workflow_runs_in_flight": [_summary(r) for r in in_flight(source)]}
        copy = copy_store(ref, scratch=tenant / "drills" / f"{stamp}-{token}")
        try:
            verification = verify_copy(copy, Anchors(copy.conn, store, firm_id=firm_id, keyring=keyring, location=location, now=clock), source)
        finally:
            if keep:
                copy.conn.close()
            else:
                copy.cleanup()
        finished = clock()
        report: dict[str, Any] = {"firm_id": firm_id, "ok": verification["ok"], "started_at": iso(started), "finished_at": iso(finished),
                                  "duration_s": round((finished - started).total_seconds(), 3),
                                  "source": {"store": ref.label, "anchors": location, **export},
                                  "copy": {"label": copy.label, "kept": keep}, **verification}
        audit.record(source, actor, "system", "evidence.restore_drill", {
            "ok": report["ok"], "copy": copy.label, "kept": keep, "audit_head": export["audit_head"],
            "anchors": verification["anchors"]["count"], "anchors_ok": verification["anchors"]["ok"],
            "chain_ok": bool((verification["chain"] or {}).get("ok")),
            "workflow_runs_in_flight": verification["workflow_runs"]["in_flight"],
            "workflow_runs_matched": verification["workflow_runs"]["matched"],
            "not_resumable": sorted(verification["workflow_runs"]["not_resumable"]), "duration_s": report["duration_s"]})
        path = tenant / "drills" / f"{stamp}-{token}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=1, sort_keys=True, default=str), encoding="utf-8")
        report["report_file"] = str(path)
        return report
    finally:
        source.close()
