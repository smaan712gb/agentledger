"""Adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv_holds_unowned_database.py): delete_firm on a
firm still in 'provisioning' deleted al_<id>, a same-named database that provisioning had refused to adopt (no
journal record), because destruction derived the store from the firm id and read "no AgentLedger schema there" as "no
holds". Asserted now: delete_firm refuses a provisioning firm and points to abandon_firm, abandon_firm removes only
what the journal records (nothing here), and destroy_firm_data on the abandoned firm refuses for lack of a record;
the foreign database survives every step. Runs against the fake Neon API (no network, no database server).
"""

from __future__ import annotations

import httpx
import pytest

from agentledger.security.platform import AuthError, Platform


class _Catalog:
    """Catalog lookups against the fake Neon project: its databases exist; none has an AgentLedger schema."""

    def __init__(self, dbs):
        self.dbs = dbs

    def execute(self, sql, params=()):
        found = "pg_database" in sql and bool(params) and params[0] in self.dbs
        return type("Result", (), {"fetchone": lambda self: (1,) if found else None})()

    def close(self):
        pass


@pytest.fixture
def neon(tmp_path, monkeypatch):
    from test_firm_lifecycle import FakeNeon

    from agentledger.pg import provision

    monkeypatch.setenv("AGENTLEDGER_DATABASE", "postgres")
    monkeypatch.setenv("AGENTLEDGER_PLATFORM_DATABASE", "sqlite")      # the database URLs below are never reached
    monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "database")
    monkeypatch.setenv("AGENTLEDGER_MIGRATION_URL", "postgresql://owner:pw@ep-x.neon.tech/neondb")
    monkeypatch.setenv("AGENTLEDGER_RUNTIME_DATABASE_URL", "postgresql://ignored@ep-x.neon.tech/neondb")
    monkeypatch.setenv("NEON_BRANCH", "dev")
    monkeypatch.delenv("NEON_BRANCH_ID", raising=False)
    monkeypatch.delenv("AGENTLEDGER_PG_SCHEMA_PREFIX", raising=False)
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)
    fake = FakeNeon()
    client = httpx.Client(base_url=provision.NEON_API, transport=httpx.MockTransport(fake), headers={"Authorization": "Bearer k"})
    api = provision.Neon(key="k", project="p", client=client)
    monkeypatch.setattr(provision, "Neon", lambda: api)
    monkeypatch.setattr(provision, "drop_role", lambda owner, role: None)
    monkeypatch.setattr(provision, "connect", lambda url: _Catalog(fake.dbs))
    return Platform(tmp_path, dev=True), fake


def _deletes(fake) -> list[str]:
    return [p for m, p in fake.calls if m == "DELETE"]


def test_a_database_provisioning_refused_to_adopt_survives_delete_abandon_and_destroy(neon):
    plat, fake = neon
    fake.dbs.add("al_ortiz_auto")                                      # someone else's database, or a kept copy
    with pytest.raises(Exception, match="refusing to adopt"):
        plat.create_firm("ortiz-auto", "Ortiz Auto", by="ops")
    assert plat.firm("ortiz-auto")["status"] == "provisioning" and plat.get("ortiz-auto") is None

    with pytest.raises(AuthError, match="never finished provisioning: use abandon_firm"):
        plat.delete_firm("ortiz-auto", by="ops", reason="operator removes the firm stuck in provisioning")
    assert plat.firm("ortiz-auto")["status"] == "provisioning" and not plat._stages("ortiz-auto")
    assert "al_ortiz_auto" in fake.dbs and not _deletes(fake)

    assert plat.abandon_firm("ortiz-auto", by="ops") == "nothing recorded"
    assert plat.firm("ortiz-auto")["status"] == "deleted"
    assert "al_ortiz_auto" in fake.dbs and not _deletes(fake)

    with pytest.raises(AuthError, match="no provisioning record says where this firm's store is"):
        plat.destroy_firm_data("ortiz-auto", by="ops")
    assert "al_ortiz_auto" in fake.dbs and not _deletes(fake)
    with pytest.raises(AuthError, match="that firm id is taken"):
        plat.create_firm("ortiz-auto", "Ortiz Auto", by="ops")         # a retired id is never reused
    assert "al_ortiz_auto" in fake.dbs and not _deletes(fake)
