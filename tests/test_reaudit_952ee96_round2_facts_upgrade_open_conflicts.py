"""Second adversarial review of the 952ee96 re-audit fixes, upgrading a 952ee96 store: it could hold two open conflicts
on one field (one per field and document), so the new one-open-conflict-per-field unique index failed: Returns() raised
IntegrityError on every request (SQLite) and migration 0005 aborted (PostgreSQL). Asserted now: the older open
conflicts are retired first (resolution 'superseded', resolved_by 'upgrade'), by Returns() on SQLite and by migration
0005 replayed on a store at 0004 on PostgreSQL; the newest stays open, other rows are untouched, and the index holds.
"""

from __future__ import annotations

import os
import shutil
import sqlite3

import pytest
from test_return_workflow import fam, household  # noqa: F401  (fam is a fixture)

from agentledger import db
from agentledger.returns.store import Returns

PG = db.backend() == "postgres"
ANCHOR = "itemized.mortgage_interest_1098"
OTHER = "w2s[d_w2a].wages"
UPGRADE = "one open conflict per field (upgrade): a newer conflict on this field is open"
COLUMNS = ("return_id, path, anchor, document_id, box, current_value, proposed_value, current_source, raised_by, raised_at, "
           "resolution, resolved_by, resolved_at, note")
# 952ee96's conflicts, oldest first: (anchor, document, proposed value, resolution)
STORED = [(ANCHOR, "d_1098a", "8800.00", "kept"),        # decided before the upgrade
          (ANCHOR, "d_1098a", "9100.00", None),          # open, one per (field, document) ...
          (ANCHOR, "d_1098b", "12300.00", None),         # ... so a second open conflict on the same field
          (OTHER, "d_w2a", "52000.00", None)]            # another field


def _row(rid, seal, anchor, doc, proposed, resolution):
    resolved = ("maya", "2026-03-02T00:00:00+00:00", "the preparer's figure stands") if resolution else (None, None, None)
    return (rid, anchor, anchor, doc, "box1", seal("9500.00"), seal(proposed), "preparer", "maya", "2026-03-01T00:00:00+00:00",
            resolution, *resolved)


def _check(rows):
    """rows: (document_id, anchor, resolution, resolved_by, note) in id order, after the upgrade."""
    assert rows == [("d_1098a", ANCHOR, "kept", "maya", "the preparer's figure stands"),
                    ("d_1098a", ANCHOR, "superseded", "upgrade", UPGRADE),
                    ("d_1098b", ANCHOR, None, None, None),
                    ("d_w2a", OTHER, None, None, None)]


@pytest.mark.skipif(PG, reason="on PostgreSQL the upgrade is migration 0005 (the next test)")
def test_a_952ee96_store_with_two_open_conflicts_on_one_field_still_opens(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    fam.conn.execute("DROP INDEX fact_conflicts_one_open")              # the schema as 952ee96 created it

    def seal(v):
        return R.sealer.seal({"v": v}, f"fact:{rid}")

    def insert(*conflict):
        fam.conn.execute(f"INSERT INTO fact_conflicts ({COLUMNS}) VALUES ({', '.join('?' * 14)})", _row(rid, seal, *conflict))

    for conflict in STORED:
        insert(*conflict)
    upgraded = Returns(fam.conn, fam.kb)                                # the next request after the upgrade
    query = "SELECT document_id, anchor, resolution, resolved_by, note FROM fact_conflicts WHERE return_id = ? ORDER BY id"
    _check([tuple(r) for r in fam.conn.execute(query, (rid,)).fetchall()])
    assert [(c["anchor"], c["document_id"], c["proposed_value"]) for c in upgraded.conflicts(rid)] == [
        (ANCHOR, "d_1098b", "12300.00"), (OTHER, "d_w2a", "52000.00")]
    with pytest.raises(sqlite3.IntegrityError, match=r"UNIQUE constraint failed: fact_conflicts\.return_id, fact_conflicts\.anchor"):
        insert(ANCHOR, "d_1098c", "4000.00", None)                      # the index is in place
    Returns(fam.conn, fam.kb)                                           # and the upgrade is done once
    _check([tuple(r) for r in fam.conn.execute(query, (rid,)).fetchall()])


@pytest.mark.skipif(not PG, reason="the PostgreSQL upgrade path (migration 0005); SQLite is the previous test")
def test_migration_0005_upgrades_a_store_with_two_open_conflicts_on_one_field(tmp_path, monkeypatch):
    import psycopg

    from agentledger import pg

    schema = os.environ["AGENTLEDGER_PG_SCHEMA_PREFIX"] + "upgrade"     # dropped with the test's other schemas
    at_952ee96 = tmp_path / "migrations"
    at_952ee96.mkdir()
    for path in sorted(pg.MIGRATIONS.glob("*.sql")):
        if path.name < "0005":
            shutil.copyfile(path, at_952ee96 / path.name)
    current = pg.MIGRATIONS
    owner = pg.connect(pg.dsn(direct=True))
    try:
        monkeypatch.setattr(pg, "MIGRATIONS", at_952ee96)
        assert pg.migrate(owner, schema) == ["0001_ledger_core.sql", "0002_firm_store.sql", "0003_evidence.sql", "0004_facts.sql"]
        owner.execute(f'SET search_path TO "{schema}", public')
        owner.execute("INSERT INTO clients (id, name, kind) VALUES ('rivera', 'Alex and Sam Rivera', 'individual')")
        owner.execute("INSERT INTO tax_returns (id, client_id, tax_year, form, created_at, created_by) "
                      "VALUES ('ret_952ee96', 'rivera', 2026, '1040', '2026-02-01T00:00:00+00:00', 'maya')")

        def insert(*conflict):
            owner.execute(f"INSERT INTO fact_conflicts ({COLUMNS}) VALUES ({', '.join(['%s'] * 14)})",
                          _row("ret_952ee96", lambda v: f'{{"v": "{v}"}}', *conflict))

        for conflict in STORED:
            insert(*conflict)
        monkeypatch.setattr(pg, "MIGRATIONS", current)
        assert pg.migrate(owner, schema) == ["0005_reaudit_952ee96.sql", "0006_filing.sql", "0007_carryforwards.sql",
                                             "0008_audit_anchors.sql"]   # every migration after 0004
        _check([tuple(r) for r in owner.execute("SELECT document_id, anchor, resolution, resolved_by, note FROM fact_conflicts "
                                                "ORDER BY id").fetchall()])
        assert owner.execute("SELECT resolved_at FROM fact_conflicts WHERE resolved_by = 'upgrade'").fetchone()[0]
        with pytest.raises(psycopg.errors.UniqueViolation) as e:        # the index is in place
            insert(ANCHOR, "d_1098c", "4000.00", None)
        assert e.value.diag.constraint_name == "fact_conflicts_one_open"
    finally:
        owner.close()
