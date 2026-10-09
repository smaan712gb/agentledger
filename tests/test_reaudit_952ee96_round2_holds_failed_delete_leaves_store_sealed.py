"""Second adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv2_holds_failed_delete_leaves_store_sealed.py):
a delete_firm that failed after its seal committed but before the 'sealed' stage was recorded (the platform store
busy; on PostgreSQL the seal's COMMIT acknowledgement lost) put the firm back in use with its store still sealed, so
every legal hold its CPAs placed was refused. Asserted now: a failure before the removal backs out, unsealing the
store whatever the seal's outcome was, before the firm goes back into use, so the hold is placed; the refusal says
why (an unreadable store is named as such), nothing is staged or recorded; and if the store cannot be unsealed the
firm stays out of use with firm_offboarding_stuck recorded, until cancel_offboarding unseals it and puts it back.
"""

from __future__ import annotations

import sqlite3

import pytest

from agentledger import db
from agentledger.evidence import offboarding, records
from agentledger.ledger import store
from agentledger.security.platform import AuthError, Platform

FIRM, CLIENT = "relapse-cpa", "jordan-lee"
OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
CANCEL = "the firm's owner withdrew the offboarding request on 2026-10-02"
REASON = "IRS examination notice dated 2026-09-30: preserve all 2026 records"
# How the store itself refuses a hold while it is sealed: the SQLite trigger, or the revoked INSERT on PostgreSQL.
SEALED = "being offboarded" if db.backend() != "postgres" else "permission denied for table legal_holds"


@pytest.fixture(autouse=True)
def _schema_tenancy(monkeypatch):
    if db.backend() == "postgres":
        monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")


def _firm(tmp_path):
    plat = Platform(tmp_path, dev=True)
    plat.create_firm(FIRM, "Relapse CPA", by="ops")
    path = plat.tenant_dir(FIRM) / "state" / "agentledger.db"
    conn = db.open_store(path)
    try:
        store.add_client(conn, id=CLIENT, name="Jordan Lee", kind="individual")
    finally:
        conn.close()
    return plat, path


def _try_hold(path) -> str:
    conn = db.open_store(path)
    try:
        return f"placed #{records.place_hold(conn, client_id=CLIENT, reason=REASON, actor='u_lee', role='cpa')}"
    except Exception as exc:
        return f"refused ({type(exc).__name__}: {exc})"
    finally:
        conn.close()


def _events(plat) -> list[dict]:
    return list(reversed(plat.events(FIRM)))


# Each failure lands after the seal committed and before the 'sealed' stage is recorded. Returns (exception, message).
def _platform_store_busy(m):
    real = Platform._stage

    def stage(self, firm_id, name, detail=""):
        if name == "sealed":
            raise sqlite3.OperationalError("database is locked")
        return real(self, firm_id, name, detail)

    m.setattr(Platform, "_stage", stage)
    return sqlite3.OperationalError, "database is locked"


def _commit_acknowledgement_lost(m):
    if db.backend() != "postgres":
        pytest.skip("the seal's COMMIT acknowledgement exists on PostgreSQL")
    import psycopg

    real = offboarding._owner

    class Conn:
        def __init__(self, c):
            self._c = c

        def execute(self, q, *a):
            out = self._c.execute(q, *a)
            if q == "COMMIT":                          # committed on the server; the reply never arrives
                self._c.close()
                raise psycopg.OperationalError("consuming input failed: server closed the connection unexpectedly")
            return out

        def __getattr__(self, name):
            return getattr(self._c, name)

    m.setattr(offboarding, "_owner", lambda database: Conn(real(database)))
    return AuthError, "could not be read to check legal holds; refusing to delete the firm"


@pytest.mark.parametrize("failure", [_platform_store_busy, _commit_acknowledgement_lost],
                         ids=["platform-store-busy", "commit-acknowledgement-lost"])
def test_failed_delete_unseals_the_store_before_the_firm_goes_back_into_use(tmp_path, monkeypatch, failure):
    plat, path = _firm(tmp_path)
    with monkeypatch.context() as m:
        raised, message = failure(m)
        with pytest.raises(raised, match=message):
            plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)

    f = plat.firm(FIRM)
    assert (f["status"], f["offboarding_from"]) == ("active", None)
    assert not plat._stages(FIRM) and plat.offboarding_records(FIRM) == []
    events = _events(plat)
    stopped = [e["detail"] for e in events if e["event"] == "firm_offboarding_stopped"]
    assert len(stopped) == 1 and message in stopped[0]
    assert "firm_offboarding_stuck" not in [e["event"] for e in events]
    if failure is _commit_acknowledgement_lost:
        assert any(e["event"] == "firm_delete_refused" and "store unreadable" in e["detail"] for e in events)

    attempt = _try_hold(path)                                         # unsealed: the CPA's hold is placed
    assert attempt.startswith("placed #"), attempt
    hold = int(attempt.removeprefix("placed #"))
    with pytest.raises(AuthError, match=rf"active legal hold.*#{hold} \({CLIENT}\)"):
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    assert plat.firm(FIRM)["status"] == "active"


def test_firm_stays_out_of_use_while_its_store_cannot_be_unsealed(tmp_path, monkeypatch):
    plat, path = _firm(tmp_path)
    real_unseal = offboarding.unseal

    def unseal(ref):
        raise ConnectionError("injected: the database service is unreachable while unsealing")

    with monkeypatch.context() as m:
        _platform_store_busy(m)
        m.setattr(offboarding, "unseal", unseal)
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)

    assert plat.firm(FIRM)["status"] == "offboarding"                 # nobody is told a hold went in while it cannot
    attempt = _try_hold(path)
    assert attempt.startswith("refused") and SEALED in attempt, attempt
    events = _events(plat)
    stuck = [e["detail"] for e in events if e["event"] == "firm_offboarding_stuck"]
    assert stuck == ["could not unseal: ConnectionError: injected: the database service is unreachable while unsealing"]
    assert "firm_offboarding_stopped" not in [e["event"] for e in events]

    assert offboarding.unseal is real_unseal                         # the database service is back
    assert plat.cancel_offboarding(FIRM, by="ops", reason=CANCEL) == "cancelled"
    f = plat.firm(FIRM)
    assert (f["status"], f["offboarding_from"]) == ("active", None) and not plat._stages(FIRM)
    attempt = _try_hold(path)
    assert attempt.startswith("placed #"), attempt
