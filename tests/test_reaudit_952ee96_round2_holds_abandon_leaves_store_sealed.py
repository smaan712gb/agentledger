"""Second adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv2_holds_abandon_leaves_store_sealed.py):
abandon_firm sealed the store before provision.abandon refused a firm whose provisioning had finished (journal
'ready', the activation never ran), and nothing unsealed it, so once the provisioning worker activated the firm every
legal hold its CPAs placed was refused. Asserted now, on PostgreSQL (schema tenancy): abandon_firm refuses a 'ready'
journal with that reason before sealing anything, and when provision.abandon fails after the seal the store is
unsealed again; either way the firm stays 'provisioning' with its store and key, the worker activates it, and a CPA's
hold is placed and refuses the firm's deletion by name. A store that cannot be unsealed again (the outage that broke
the abandonment) must not go into use sealed either.
"""

from __future__ import annotations

import pytest

from agentledger import db
from agentledger.evidence import offboarding, records
from agentledger.ledger import store
from agentledger.security.crypto import CryptoError, Keyring
from agentledger.security.platform import AuthError, Platform

pytestmark = pytest.mark.skipif(db.backend() != "postgres",
                                reason="needs AGENTLEDGER_DATABASE=postgres (only there is a firm 'provisioning')")

FIRM, CLIENT = "halfway-cpa", "jordan-lee"
OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
REASON = "IRS examination notice dated 2026-09-30: preserve all 2026 records"


@pytest.fixture(autouse=True)
def _schema_tenancy(monkeypatch):
    monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")


def _holds_insertable(plat) -> bool:
    """Whether the store's runtime role may add legal holds (False while the store is sealed), read as the owner."""
    from agentledger import pg

    rec = plat.get(FIRM)
    owner = pg.connect(pg.with_database(pg.migration_url(), rec["database"]))
    try:
        return bool(owner.execute("SELECT has_table_privilege(%s, %s, 'INSERT')",
                                  (pg.runtime_role(rec["database"], rec["name"]), f'"{rec["name"]}".legal_holds')).fetchone()[0])
    finally:
        owner.close()


def _key_alive(plat) -> bool:
    try:
        Keyring(plat.conn, plat.keys.master).current_version(FIRM)
        return True
    except CryptoError:
        return False


def _activated_and_holds_work(plat) -> None:
    """The provisioning worker activates the firm; a CPA's hold is placed and stops the firm's deletion."""
    assert plat.provision_pending(by="provisioning-worker") == [{"firm": FIRM, "status": "active"}]
    conn = db.open_store(plat.tenant_dir(FIRM) / "state" / "agentledger.db")
    try:
        store.add_client(conn, id=CLIENT, name="Jordan Lee", kind="individual")
        hold = records.place_hold(conn, client_id=CLIENT, reason=REASON, actor="u_lee", role="cpa")
    finally:
        conn.close()
    with pytest.raises(AuthError, match=rf"active legal hold.*#{hold} \({CLIENT}\)"):
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    assert plat.firm(FIRM)["status"] == "active"


def _spy_seals(m) -> list:
    seals: list = []
    real = offboarding.seal

    def seal(ref, **kw):
        seals.append(ref)
        return real(ref, **kw)

    m.setattr(offboarding, "seal", seal)
    return seals


def test_abandon_of_a_provisioned_firm_is_refused_before_anything_is_sealed(tmp_path, monkeypatch):
    from agentledger.pg import provision

    plat = Platform(tmp_path, dev=True)
    real = provision.provision

    def provisioned_then_killed(firm_id, journal, **kw):
        real(firm_id, journal, **kw)           # the store is created and migrated, the journal says 'ready' ...
        raise RuntimeError("worker killed before the firm was activated")    # ... the status update never ran

    with monkeypatch.context() as m:
        m.setattr(provision, "provision", provisioned_then_killed)
        with pytest.raises(RuntimeError, match="worker killed"):
            plat.create_firm(FIRM, "Halfway CPA", by="ops")
    assert plat.firm(FIRM)["status"] == "provisioning" and plat.get(FIRM)["state"] == "ready"

    with monkeypatch.context() as m:
        seals = _spy_seals(m)
        with pytest.raises(AuthError, match="never finished provisioning: use abandon_firm"):
            plat.delete_firm(FIRM, by="ops", reason="operator removes the firm stuck in provisioning")
        with pytest.raises(AuthError, match="this firm's store is provisioned: let the provisioning worker activate the "
                                            "firm, then delete it"):
            plat.abandon_firm(FIRM, by="ops")
    assert seals == [] and _holds_insertable(plat)
    assert plat.firm(FIRM)["status"] == "provisioning" and plat.get(FIRM)["state"] == "ready" and _key_alive(plat)
    assert "firm_abandoned" not in [e["event"] for e in plat.events(FIRM)]

    _activated_and_holds_work(plat)


def test_abandon_that_fails_after_its_seal_unseals_the_store(tmp_path, monkeypatch):
    from agentledger.pg import provision

    plat = Platform(tmp_path, dev=True)
    real_migrate = provision._migrate_store

    def migrated_then_killed(url, schema, own_database):
        real_migrate(url, schema, own_database)
        raise RuntimeError("worker killed after migrating the store, before recording it")

    with monkeypatch.context() as m:
        m.setattr(provision, "_migrate_store", migrated_then_killed)
        with pytest.raises(RuntimeError, match="worker killed"):
            plat.create_firm(FIRM, "Halfway CPA", by="ops")
    assert plat.firm(FIRM)["status"] == "provisioning" and plat.get(FIRM)["state"] == "created"

    def removal_fails(resource, name, **kw):
        raise ConnectionError("injected: the database service is unreachable while dropping the schema")

    with monkeypatch.context() as m:
        seals = _spy_seals(m)
        m.setattr(provision, "_remove", removal_fails)
        with pytest.raises(ConnectionError, match="injected"):
            plat.abandon_firm(FIRM, by="ops")
    assert len(seals) == 1 and _holds_insertable(plat)                # sealed, then unsealed again
    assert plat.firm(FIRM)["status"] == "provisioning" and plat.get(FIRM)["state"] == "created" and _key_alive(plat)
    assert db.store_exists(plat.tenant_dir(FIRM) / "state" / "agentledger.db")
    assert "firm_abandoned" not in [e["event"] for e in plat.events(FIRM)]

    _activated_and_holds_work(plat)


def _removal_fails(m):
    from agentledger.pg import provision

    def removal_fails(resource, name, **kw):
        raise ConnectionError("injected: the database service is unreachable while dropping the schema")

    m.setattr(provision, "_remove", removal_fails)


def _seal_acknowledgement_lost(m):
    import psycopg

    real = offboarding._owner

    class Conn:
        def __init__(self, c):
            self._c = c

        def execute(self, q, *a):
            out = self._c.execute(q, *a)
            if q == "COMMIT":                          # the seal committed on the server; the reply never arrives
                self._c.close()
                raise psycopg.OperationalError("consuming input failed: server closed the connection unexpectedly")
            return out

        def __getattr__(self, name):
            return getattr(self._c, name)

    m.setattr(offboarding, "_owner", lambda database: Conn(real(database)))


@pytest.mark.parametrize("failure", [_removal_fails, _seal_acknowledgement_lost],
                         ids=["removal-fails", "seal-acknowledgement-lost"])
def test_abandon_whose_store_cannot_be_unsealed_does_not_let_the_firm_go_live_sealed(tmp_path, monkeypatch, failure):
    """The outage that stops the abandonment also stops the unseal: the store stays sealed, so the firm must not be
    put into use (or must be unsealed first) when the provisioning worker resumes it."""
    from agentledger.pg import provision

    plat = Platform(tmp_path, dev=True)
    real_migrate = provision._migrate_store

    def migrated_then_killed(url, schema, own_database):
        real_migrate(url, schema, own_database)
        raise RuntimeError("worker killed after migrating the store, before recording it")

    with monkeypatch.context() as m:
        m.setattr(provision, "_migrate_store", migrated_then_killed)
        with pytest.raises(RuntimeError, match="worker killed"):
            plat.create_firm(FIRM, "Halfway CPA", by="ops")

    def unseal(ref):
        raise ConnectionError("injected: the database service is unreachable while unsealing")

    with monkeypatch.context() as m:
        failure(m)
        m.setattr(offboarding, "unseal", unseal)
        with pytest.raises(Exception):
            plat.abandon_firm(FIRM, by="ops")
    sealed = not _holds_insertable(plat)

    worker = plat.provision_pending(by="provisioning-worker")
    conn = db.open_store(plat.tenant_dir(FIRM) / "state" / "agentledger.db")
    try:
        store.add_client(conn, id=CLIENT, name="Jordan Lee", kind="individual")
        try:
            outcome = f"placed #{records.place_hold(conn, client_id=CLIENT, reason=REASON, actor='u_lee', role='cpa')}"
        except Exception as exc:
            outcome = f"refused ({type(exc).__name__}: {exc})"
    finally:
        conn.close()
    status = plat.firm(FIRM)["status"]
    assert status != "active" or outcome.startswith("placed"), (
        f"after the failed abandonment the store was {'still sealed' if sealed else 'unsealed'}; the worker {worker}; "
        f"firm {status!r} and a CPA's hold was {outcome}; events {[e['event'] for e in reversed(plat.events(FIRM))]}")
