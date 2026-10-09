"""Restore drill with active workflows (backlog F-13, Q33).

A firm's store is restored into a scratch copy (SQLite: the file through the backup API; PostgreSQL: a fresh schema
migrated and loaded from the firm's schema with the owner connection), the copy's audit chain is verified against
the anchors in the firm's object store, the parked workflow instances are found in the copy and judged resumable,
the drill is recorded and reported, and the copy is removed. A copy that was rewritten, cut short or lost an instance
fails the drill. Runs on both backends (PostgreSQL through the local container, schema tenancy).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from agentledger import audit, db
from agentledger.evidence import blobs as blobstore
from agentledger.evidence import drill
from agentledger.evidence.anchors import Anchors
from agentledger.ledger import store
from agentledger.ledger.store import Line
from agentledger.security.platform import Platform
from agentledger.workflow.runner import DbRuns, Run

FIRM = "drill-cpa"
FAR = "2099-01-01T00:00:00+00:00"


def _firm(root):
    """A firm with a little history, three workflow instances (two parked, one finished) and one anchor."""
    plat = Platform(root, dev=True)
    plat.create_firm(FIRM, "Drill CPA", by="ops")
    conn = db.open_store(plat.tenant_dir(FIRM) / "state" / "agentledger.db")
    conn.set_scope(["*"])
    store.add_client(conn, id="acme", name="Acme", kind="business")
    store.add_account(conn, "acme", "1000", "Cash", "asset")
    store.add_account(conn, "acme", "4000", "Sales", "revenue")
    store.post(conn, "acme", date(2026, 3, 1), "sale", [Line("1000", Decimal(5)), Line("4000", Decimal(-5))], source="t", actor="t")
    store.close_period(conn, "acme", date(2026, 3, 31), actor="maya", role="cpa")      # a closed period the copy must load through
    now = audit.now()
    runs = DbRuns(conn)
    params = {"firm_id": FIRM, "return_id": "ret_1", "submission_id": None}
    runs.put(Run("filing-s1-1", "submission", {**params, "submission_id": "s1"},
                 steps={"transmit": {"state": "done", "attempts": 1, "result": {"status": "transmitted"}},
                        "poll-wait-1": {"state": "sleeping", "until": FAR}},
                 status="sleeping", wake_at=FAR, created_at=now, updated_at=now))
    runs.put(Run("filing-s2-1", "submission", {**params, "submission_id": "s2"},
                 steps={"transmit": {"state": "done", "attempts": 2, "result": {"status": "unknown"}},
                        "notify-unknown": {"state": "done", "attempts": 1, "result": {"task_id": 7}},
                        "wait-reconciled-1": {"state": "waiting", "until": FAR, "type": "submission-reconciled"}},
                 status="waiting", wake_at=FAR, waiting_for="submission-reconciled", created_at=now, updated_at=now))
    runs.put(Run("filing-s0-1", "submission", {**params, "submission_id": "s0"}, steps={"transmit": {"state": "done", "attempts": 1, "result": {}}},
                 status="complete", result={"status": "accepted"}, created_at=now, updated_at=now))
    blobs, location = blobstore.anchors_for_firm(plat.tenant_dir(FIRM) / "anchors", FIRM)
    anchors = Anchors(conn, blobs, firm_id=FIRM, keyring=plat.keys, location=location)
    assert anchors.anchor()["anchored"]
    return plat, conn, anchors


def _tamper_copy(copy, table, sql, *params):
    """What a bad restore does to the copy: on SQLite through the file with the append-only triggers dropped, on
    PostgreSQL as the owner of the scratch schema with its immutability triggers off."""
    if copy.schema:
        from agentledger import pg

        owner = pg.connect(pg.with_database(pg.migration_url(), copy.database))
        try:
            owner.execute(f'SET search_path TO "{copy.schema}", public')
            owner.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
            owner.execute(sql.replace("?", "%s"), params)
            owner.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")
        finally:
            owner.close()
        return
    raw = sqlite3.connect(copy.path)
    try:
        for op in ("update", "delete"):
            raw.execute(f"DROP TRIGGER IF EXISTS {table}_no_{op}")
        raw.execute(sql, params)
        raw.commit()
    finally:
        raw.close()


def _drill_schemas():
    from agentledger import pg

    owner = pg.connect(pg.dsn(direct=True))
    try:
        prefix = os.environ.get("AGENTLEDGER_PG_SCHEMA_PREFIX", "")
        schemas = [r[0] for r in owner.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE %s", (prefix + "drill\\_%",)).fetchall()]
        roles = [r[0] for r in owner.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE %s", ("rt\\_%" + prefix + "drill\\_%",)).fetchall()]
        return schemas, roles
    finally:
        owner.close()


def test_the_drill_restores_a_copy_verifies_it_against_the_anchors_and_lists_parked_workflows(tmp_path):
    plat, conn, anchors = _firm(tmp_path)
    head = anchors.head()
    report = drill.run(plat, FIRM, actor="ops")
    assert report["ok"] is True, json.dumps(report, default=str)
    assert report["firm_id"] == FIRM and report["source"]["audit_head"] == head and report["source"]["anchors"]["count"] == 1
    assert report["source"]["workflow_events"] == {"streams": 0, "events": 0}
    assert [r["instance_id"] for r in report["source"]["workflow_runs_in_flight"]] == ["filing-s1-1", "filing-s2-1"]
    assert report["copy"]["label"].startswith("sqlite file " if db.backend() != "postgres" else "schema ") and report["copy"]["kept"] is False
    assert report["chain"]["ok"] and report["chain"]["checked"] == head["count"]
    a = report["anchors"]
    assert a["ok"] and a["signed"] and a["count"] == 1 and a["latest"]["key"] == report["source"]["anchors"]["latest"]
    assert a["chain_rewritten"] == [] and a["chain_truncated"] == [] and a["anchor_missing"] is False
    wf = report["workflow_runs"]
    assert wf["in_flight"] == 2 and wf["matched"] is True and wf["missing"] == [] and wf["extra"] == []
    assert wf["resumable"] == ["filing-s1-1", "filing-s2-1"] and wf["not_resumable"] == {} and wf["due_now"] == []
    assert {i["instance_id"]: (i["status"], i["waiting_for"], i["steps"]) for i in wf["instances"]} == {
        "filing-s1-1": ("sleeping", None, 2), "filing-s2-1": ("waiting", "submission-reconciled", 3)}
    # The drill is recorded in the live chain and reported under the tenant directory; the copy is gone.
    [rec] = [e for e in audit.events(conn, limit=50) if e["action"] == "evidence.restore_drill"]
    assert rec["actor"] == "ops" and rec["payload"]["ok"] is True and rec["payload"]["workflow_runs_in_flight"] == 2
    assert rec["payload"]["audit_head"] == head and rec["payload"]["anchors"] == 1 and rec["payload"]["chain_ok"] is True
    path = Path(report["report_file"])
    assert path.parent == plat.tenant_dir(FIRM) / "drills" and json.loads(path.read_text(encoding="utf-8"))["ok"] is True
    assert list((plat.tenant_dir(FIRM) / "drills").glob("*/agentledger.db")) == []
    if db.backend() == "postgres":
        assert _drill_schemas() == ([], [])
    # The live store is untouched: the anchors still fit it and the instances are still parked.
    assert anchors.check()["ok"] and len(drill.in_flight(conn)) == 2 and anchors.head()["seq"] == head["seq"] + 1
    assert anchors.anchor()["anchored"]                                      # the drill record is activity worth anchoring
    assert drill.run(plat, FIRM)["ok"] is True                              # and the next drill verifies both anchors


def test_a_kept_copy_stays_for_inspection(tmp_path):
    plat, conn, anchors = _firm(tmp_path)
    report = drill.run(plat, FIRM, keep=True)
    assert report["ok"] and report["copy"]["kept"] is True
    if db.backend() == "postgres":
        from agentledger import pg

        schema, database = report["copy"]["label"].split()[1], report["copy"]["label"].split()[-1]
        schemas, roles = _drill_schemas()
        assert schemas == [schema] and roles == [pg.runtime_role(database, schema)]
        owner = pg.connect(pg.with_database(pg.migration_url(), database))
        try:
            assert owner.execute(f'SELECT COUNT(*) FROM "{schema}".workflow_runs').fetchone()[0] == 3
            assert owner.execute(f'SELECT COUNT(*) FROM "{schema}".audit').fetchone()[0] == anchors.head()["count"] - 1   # before the drill record
            assert owner.execute(f'SELECT COUNT(*) FROM "{schema}".postings').fetchone()[0] == 2
        finally:
            owner.close()
        drill._drop_pg(pg.migration_url(), database, schema, pg.runtime_role(database, schema))
        assert _drill_schemas() == ([], [])
    else:
        [copy] = (plat.tenant_dir(FIRM) / "drills").glob("*/agentledger.db")
        raw = sqlite3.connect(copy)
        try:
            assert raw.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0] == 3
            assert raw.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == anchors.head()["count"] - 1
        finally:
            raw.close()


def test_a_copy_that_was_rewritten_cut_short_or_lost_an_instance_fails_the_drill(tmp_path):
    plat, conn, anchors = _firm(tmp_path)
    latest = anchors.latest_recorded()
    blobs, location = blobstore.anchors_for_firm(plat.tenant_dir(FIRM) / "anchors", FIRM)
    copy = drill.copy_store(plat.store_ref(FIRM), scratch=plat.tenant_dir(FIRM) / "drills" / "tampered")
    try:
        assert db.count(copy.conn, "SELECT COUNT(*) FROM audit") == anchors.head()["count"]
        assert db.count(copy.conn, "SELECT COUNT(*) FROM postings") == 2 and db.count(copy.conn, "SELECT COUNT(*) FROM audit_anchors") == 1
        assert db.one(copy.conn, "SELECT closed_through FROM clients WHERE id = 'acme'")["closed_through"] == "2026-03-31"

        def verify():
            return drill.verify_copy(copy, Anchors(copy.conn, blobs, firm_id=FIRM, keyring=plat.keys, location=location), conn)

        assert verify()["ok"] is True
        _tamper_copy(copy, "audit", "UPDATE audit SET hash = ? WHERE seq = ?", "f" * 64, latest["seq"])
        v = verify()
        assert v["ok"] is False and not v["chain"]["ok"]
        assert [(x["anchor"], x["seq"]) for x in v["anchors"]["chain_rewritten"]] == [(latest["object_key"], latest["seq"])]
        _tamper_copy(copy, "audit", "UPDATE audit SET hash = ? WHERE seq = ?", latest["head_hash"], latest["seq"])
        assert verify()["ok"] is True
        _tamper_copy(copy, "audit", "DELETE FROM audit WHERE seq >= ?", latest["seq"])
        v = verify()
        assert v["ok"] is False and v["chain"]["ok"] and v["anchors"]["chain_truncated"][0]["anchor"] == latest["object_key"]
        _tamper_copy(copy, "workflow_runs", "DELETE FROM workflow_runs WHERE instance_id = ?", "filing-s2-1")
        _tamper_copy(copy, "workflow_runs", "UPDATE workflow_runs SET flow = 'unknown-flow' WHERE instance_id = ?", "filing-s1-1")
        v = verify()
        wf = v["workflow_runs"]
        assert wf["matched"] is False and wf["missing"] == ["filing-s2-1"] and wf["in_flight"] == 1
        assert wf["not_resumable"] == {"filing-s1-1": ["unknown flow 'unknown-flow'"]} and wf["resumable"] == []
    finally:
        copy.cleanup()
    if db.backend() == "postgres":
        assert _drill_schemas() == ([], [])
    assert not (plat.tenant_dir(FIRM) / "drills" / "tampered").exists() or db.backend() == "postgres"


def test_the_command_line_runs_the_drill(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from agentledger import cli

    plat, conn, anchors = _firm(tmp_path)
    monkeypatch.setenv("AGENTLEDGER_HOME", str(tmp_path))
    monkeypatch.setenv("AGENTLEDGER_DEV_AUTH", "1")                 # the development master key file this platform created
    res = CliRunner().invoke(cli.app, ["evidence", "restore-drill", FIRM, "--by", "cli-operator"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["ok"] is True and out["workflow_runs"]["in_flight"] == 2 and out["anchors"]["count"] == 1
    assert [e["actor"] for e in audit.events(conn, limit=50) if e["action"] == "evidence.restore_drill"] == ["cli-operator"]
    res = CliRunner().invoke(cli.app, ["evidence", "restore-drill", "no-such-firm"])
    assert res.exit_code == 2 and "firm not found" in res.output
    assert not (tmp_path / "tenants" / "no-such-firm").exists()          # nothing is created for a firm that does not exist
    res = CliRunner().invoke(cli.app, ["evidence", "anchor", "--firm", "no-such-firm"])
    assert res.exit_code == 2 and "firm not found" in res.output
    res = CliRunner().invoke(cli.app, ["evidence", "anchor", "--firm", FIRM])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)[FIRM]["anchored"] is True                # the drill's own record is activity


@pytest.mark.skipif(db.backend() != "postgres", reason="the PostgreSQL copy needs the owner connection")
def test_the_postgres_copy_needs_the_owner_connection(tmp_path, monkeypatch):
    plat, conn, anchors = _firm(tmp_path)
    ref = plat.store_ref(FIRM)
    monkeypatch.setenv("AGENTLEDGER_MIGRATION_URL", "")
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "")
    monkeypatch.setenv("DATABASE_URL", "")
    with pytest.raises(drill.DrillError, match="owner connection"):
        drill.copy_store(ref, scratch=tmp_path / "scratch")
