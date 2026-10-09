"""The PostgreSQL ledger core enforces the financial invariants itself (ADR-0002, backlog F-04).

Each test session gets a throwaway schema on the database in DATABASE_URL_UNPOOLED (the Neon dev branch locally, a
postgres:17 service in CI) and drops it afterwards. Without a database URL these tests are skipped.

Q01 an unbalanced journal is rejected on every route, including a direct insert by the table owner
Q02 a retried command has one effect; a reused command id with a different payload conflicts
Q03 concurrent retries of the same command still post once
Q04 closed periods are enforced by the database; reopening is CPA-only, reasoned and audited
Q05 corrections are reversals: posted rows never change, and tampering below the application is detected
"""

from __future__ import annotations

import os
import secrets
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from agentledger import envfile

envfile.load(Path(__file__).resolve().parents[1])
psycopg = pytest.importorskip("psycopg")

from agentledger import pg  # noqa: E402
from agentledger.db import CommandConflict  # noqa: E402
from agentledger.ledger.store import ClosedPeriod  # noqa: E402

URL = pg.dsn(direct=True)
REQUIRED = bool(os.environ.get("AGENTLEDGER_REQUIRE_PG"))       # set in CI: a missing database is a failure
pytestmark = pytest.mark.skipif(not URL and not REQUIRED, reason="no DATABASE_URL: PostgreSQL tests need a database")

SALE = [{"account": "1100", "amount": "250.00"}, {"account": "4000", "amount": "-250.00"}]


@pytest.fixture(scope="module")
def db():
    owner = pg.connect(URL)
    schema = "al_test_" + secrets.token_hex(4)
    try:
        assert pg.migrate(owner, schema) == ["0001_ledger_core.sql", "0002_firm_store.sql", "0003_evidence.sql", "0004_facts.sql",
                                             "0005_reaudit_952ee96.sql", "0006_filing.sql", "0007_carryforwards.sql"]
        assert pg.migrate(owner, schema) == []                     # idempotent
        yield owner, schema
    finally:
        owner.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        pg.drop_role(owner, rt_role(schema))
        owner.close()


def rt_role(schema):
    return pg.runtime_role(pg.database_of(URL), schema)


def rt_url(schema):
    """The store's runtime login: never the owner."""
    return pg.runtime_url(URL, rt_role(schema))


@pytest.fixture(scope="module")
def runtime(db):
    conn = pg.connect(rt_url(db[1]))
    yield conn
    conn.close()


@pytest.fixture
def books(db, runtime):
    """A fresh client with a small chart of accounts, and a CPA-scoped ledger client for it."""
    owner, schema = db
    cid = "c" + secrets.token_hex(4)
    led = pg.Ledger(runtime, schema, clients=[cid], actor="maya", role="cpa")
    led.add_client(cid, "Rivera Plumbing", "business")
    for code, name, kind in [("1000", "Cash", "asset"), ("1100", "Accounts receivable", "asset"),
                             ("4000", "Sales", "revenue"), ("6000", "Supplies", "expense")]:
        led.add_account(cid, code, name, kind)
    return led, cid


def owner_tx(db, sql, params=()):
    owner, schema = db
    with owner.transaction():
        owner.execute(f'SET LOCAL search_path TO "{schema}", public')
        owner.execute(sql, params)


def test_q01_unbalanced_journal_rejected_on_every_route(db, books):
    led, cid = books
    with pytest.raises(pg.Unbalanced):
        led.post(cid, date(2026, 3, 1), "off by a cent", [{"account": "1100", "amount": "250.00"},
                                                           {"account": "4000", "amount": "-249.99"}])
    with pytest.raises(pg.Unbalanced):
        led.post(cid, date(2026, 3, 1), "one line", [{"account": "1100", "amount": "250.00"}])
    with pytest.raises(pg.LedgerError):
        led.post(cid, date(2026, 3, 1), "fractional cents", [{"account": "1100", "amount": "0.005"},
                                                              {"account": "4000", "amount": "-0.005"}])
    # The table owner bypasses the function; the deferred trigger still refuses at commit.
    with pytest.raises(psycopg.Error) as exc:
        owner_tx(db, """WITH e AS (INSERT INTO entries (client_id, date, memo, source, created_by, prev_hash, hash)
                                   VALUES (%s, '2026-03-01', 'direct', 'sql', 'owner', 'x', 'x') RETURNING id)
                        INSERT INTO postings (entry_id, client_id, line, account_code, amount, currency)
                        SELECT id, %s, 0, '1100', 100, 'USD' FROM e""", (cid, cid))
    assert exc.value.sqlstate == "AL001"
    # The runtime role cannot write ledger tables at all.
    _, schema = db
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with led.conn.transaction():
            led.conn.execute(f'SET LOCAL search_path TO "{schema}", public')
            led.conn.execute("INSERT INTO outbox (client_id, event_type, aggregate, aggregate_id, payload) "
                             "VALUES (%s, 'x', 'x', 'x', '{}')", (cid,))
    assert led.balances(cid) == {}


def test_q02_retry_has_one_effect_and_reuse_conflicts(books):
    led, cid = books
    first = led.post(cid, date(2026, 3, 2), "Invoice 1001", SALE, command_id="inv-1001")
    again = led.post(cid, date(2026, 3, 2), "Invoice 1001", SALE, command_id="inv-1001")
    assert first == again
    assert led.balances(cid) == {"1100": Decimal("250.00"), "4000": Decimal("-250.00")}
    with pytest.raises(CommandConflict):
        led.post(cid, date(2026, 3, 2), "Invoice 1001", [{"account": "1100", "amount": "999.00"},
                                                         {"account": "4000", "amount": "-999.00"}], command_id="inv-1001")
    # Posting, outbox event, audit record and receipt were written together, once.
    assert led.query("SELECT count(*) FROM outbox WHERE aggregate_id = %s", (str(first),)) == [(1,)]
    assert led.query("SELECT count(*) FROM audit WHERE action = 'ledger.posted' AND payload->>'entry_id' = %s",
                     (str(first),)) == [(1,)]
    assert led.query("SELECT count(*) FROM commands WHERE client_id = %s", (cid,)) == [(1,)]


def test_q03_concurrent_retries_post_once(db, books):
    _, schema = db
    led, cid = books
    barrier, results, errors = threading.Barrier(4), [], []

    def worker():
        conn = pg.connect(rt_url(schema))
        try:
            mine = pg.Ledger(conn, schema, clients=[cid], actor="maya", role="cpa")
            barrier.wait()
            results.append(mine.post(cid, date(2026, 3, 3), "Payment 77", SALE, command_id="pay-77"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    assert len(set(results)) == 1 and len(results) == 4
    assert led.query("SELECT count(*) FROM entries WHERE client_id = %s", (cid,)) == [(1,)]


def test_q04_closed_period_enforced_by_the_database(db, books):
    led, cid = books
    led.post(cid, date(2026, 1, 15), "January sale", SALE)
    staff = pg.Ledger(led.conn, led.schema, clients=[cid], actor="sam", role="staff")
    with pytest.raises(pg.NotAuthorized):
        staff.close_period(cid, date(2026, 1, 31))
    led.close_period(cid, date(2026, 1, 31))
    with pytest.raises(ClosedPeriod):
        led.post(cid, date(2026, 1, 20), "late January entry", SALE)
    with pytest.raises(psycopg.Error) as exc:                       # the owner cannot slip one in either
        owner_tx(db, "INSERT INTO entries (client_id, date, memo, source, created_by, prev_hash, hash) "
                     "VALUES (%s, '2026-01-20', 'direct', 'sql', 'owner', 'x', 'x')", (cid,))
    assert exc.value.sqlstate == "AL002"
    led.post(cid, date(2026, 2, 1), "February is open", SALE)
    with pytest.raises(pg.NotAuthorized):
        staff.reopen_period(cid, date(2025, 12, 31), "fix")
    with pytest.raises(pg.LedgerError):
        led.reopen_period(cid, date(2025, 12, 31), "  ")
    led.reopen_period(cid, date(2025, 12, 31), "client found a missing January invoice")
    led.post(cid, date(2026, 1, 20), "late January entry", SALE)
    actions = [a for (a,) in led.query("SELECT action FROM audit WHERE client_id = %s ORDER BY seq", (cid,))]
    assert actions.count("period.closed") == 1 and actions.count("period.reopened") == 1


def test_q05_reversal_not_edit_and_tamper_detected(db, books):
    led, cid = books
    eid = led.post(cid, date(2026, 4, 1), "Supplies", [{"account": "6000", "amount": "80.00"},
                                                      {"account": "1000", "amount": "-80.00"}])
    rid = led.reverse(cid, eid, date(2026, 4, 2), "posted to the wrong client")
    assert rid != eid and set(led.balances(cid).values()) == {Decimal("0.00")}
    with pytest.raises(pg.LedgerError):
        led.reverse(cid, eid, date(2026, 4, 3), "twice")
    for sql in ("UPDATE entries SET memo = 'edited' WHERE id = %s", "DELETE FROM postings WHERE entry_id = %s"):
        with pytest.raises(psycopg.Error) as exc:
            owner_tx(db, sql, (eid,))
        assert exc.value.sqlstate == "AL006"
    assert led.verify_chain(cid) == {"ok": True, "checked": 2, "broken_at": None}
    # Someone with owner rights disables the guard and edits history: the chain shows where.
    owner_tx(db, "ALTER TABLE entries DISABLE TRIGGER entries_immutable; "
                 "UPDATE entries SET memo = 'Office party' WHERE id = %s; "
                 "ALTER TABLE entries ENABLE TRIGGER entries_immutable" % eid)
    assert led.verify_chain(cid) == {"ok": False, "checked": 0, "broken_at": eid}


def test_session_scope_limits_reads_and_writes(db, books):
    led, cid = books
    led.post(cid, date(2026, 5, 1), "scoped", SALE)
    other = pg.Ledger(led.conn, led.schema, clients=["someone-else"], actor="sam", role="cpa")
    assert other.query("SELECT count(*) FROM entries") == [(0,)]
    assert other.query("SELECT count(*) FROM clients") == [(0,)]
    with pytest.raises(pg.OutOfScope):
        other.post(cid, date(2026, 5, 2), "not my client", SALE)
    with pytest.raises(pg.OutOfScope):
        other.verify_chain(cid)


def test_changed_migration_is_refused(db, tmp_path, monkeypatch):
    owner, schema = db
    fake = tmp_path / "migrations"
    fake.mkdir()
    (fake / "0001_ledger_core.sql").write_text("-- edited after it was applied\n", encoding="utf-8")
    monkeypatch.setattr(pg, "MIGRATIONS", fake)
    with pytest.raises(RuntimeError, match="changed after it was applied"):
        pg.migrate(owner, schema)
