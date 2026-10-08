"""A firm's store is created before the firm exists and removed when the firm is deleted (backlog F-04).

The Neon client is exercised against a fake Neon API here; `test_neon_database_per_firm_live` runs the real thing
against the Neon dev branch when AGENTLEDGER_TEST_NEON=1 (it creates and deletes a throwaway database).
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from agentledger import db, envfile
from agentledger.ledger import store
from agentledger.ledger.store import Line
from agentledger.security.platform import AuthError, Platform

REPO = Path(__file__).resolve().parents[1]


def test_deleting_a_firm_removes_its_store_and_files(tmp_path):
    plat = Platform(tmp_path, dev=True)
    plat.create_firm("short-lived", "Short Lived CPA", by="ops")
    tenant = plat.tenant_dir("short-lived")
    path = tenant / "state" / "agentledger.db"
    conn = db.open_store(path)
    store.add_client(conn, id="acme", name="Acme", kind="business")
    store.add_account(conn, "acme", "1000", "Cash", "asset")
    store.add_account(conn, "acme", "4000", "Sales", "revenue")
    store.post(conn, "acme", date(2026, 3, 1), "sale", [Line("1000", Decimal(5)), Line("4000", Decimal(-5))], source="t", actor="t")
    (tenant / "vault").mkdir(parents=True, exist_ok=True)
    (tenant / "vault" / "doc.bin").write_bytes(b"ciphertext")

    with pytest.raises(AuthError):
        plat.destroy_firm_data("short-lived", by="ops")       # an active firm's data is never destroyed
    plat.delete_firm("short-lived", by="ops")

    assert not tenant.exists()
    with pytest.raises(db.DatabaseError, match="closed"):
        conn.execute("SELECT 1")                               # this process's connections were closed first
    if db.backend() == "postgres":
        from agentledger import pg

        owner = pg.connect(pg.dsn(direct=True))
        assert not owner.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (db.schema_for(path),)).fetchone()
        owner.close()
    events = {e["event"]: e["detail"] for e in plat.events("short-lived")}
    detail = events["firm_data_destroyed"]
    assert ("sqlite file" in detail or "dropped" in detail) and "tenant directory removed" in detail
    assert plat.destroy_firm_data("short-lived", by="ops")     # retry-safe


class FakeNeon:
    """Just enough of the Neon API: branches, databases, operations that finish on the second poll."""

    def __init__(self):
        self.dbs = {"neondb"}
        self.ops: dict[str, int] = {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path.removeprefix("/api/v2"), request.method
        self.calls.append((method, path))
        assert request.headers["Authorization"] == "Bearer k"
        if path == "/projects/p/branches":
            return httpx.Response(200, json={"branches": [{"id": "br-main", "name": "main"}, {"id": "br-dev", "name": "dev"}]})
        if path == "/projects/p/branches/br-dev/databases" and method == "GET":
            return httpx.Response(200, json={"databases": [{"name": n} for n in sorted(self.dbs)]})
        if path == "/projects/p/branches/br-dev/databases" and method == "POST":
            body = json.loads(request.content)["database"]
            assert body["owner_name"] == "owner"
            self.dbs.add(body["name"])
            return httpx.Response(201, json={"operations": [self._op("apply_config")]})
        if path.startswith("/projects/p/branches/br-dev/databases/") and method == "DELETE":
            self.dbs.discard(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"operations": [self._op("apply_config")]})
        if path.startswith("/projects/p/operations/"):
            op = path.rsplit("/", 1)[1]
            self.ops[op] += 1
            return httpx.Response(200, json={"operation": {"status": "finished" if self.ops[op] > 1 else "running"}})
        return httpx.Response(404, json={"message": "not found"})

    def _op(self, action: str) -> dict:
        op = "op-" + secrets.token_hex(3)
        self.ops[op] = 0
        return {"id": op, "action": action}


def test_neon_client_resolves_branch_and_waits_for_operations(monkeypatch):
    from agentledger.pg import provision

    fake = FakeNeon()
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)
    client = httpx.Client(base_url=provision.NEON_API, transport=httpx.MockTransport(fake), headers={"Authorization": "Bearer k"})
    monkeypatch.delenv("NEON_BRANCH_ID", raising=False)
    monkeypatch.setenv("NEON_BRANCH", "dev")
    neon = provision.Neon(key="k", project="p", client=client)
    assert neon.branch == "br-dev"
    neon.create_database("al_rivera_cpa", "owner")
    assert "al_rivera_cpa" in neon.databases()
    neon.delete_database("al_rivera_cpa")
    assert "al_rivera_cpa" not in neon.databases()
    polls = [c for c in fake.calls if "/operations/" in c[1]]
    assert len(polls) == 4                                    # each operation polled until it finished


def test_database_tenancy_locates_each_firm_in_its_own_database(monkeypatch):
    from agentledger.pg import provision

    monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "database")
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql://owner:pw@ep-x.neon.tech/neondb?sslmode=require")
    monkeypatch.delenv("AGENTLEDGER_PG_SCHEMA_PREFIX", raising=False)
    url, schema = provision.store_location(Path("/srv/tenants/rivera-cpa/state/agentledger.db"))
    assert url == "postgresql://owner:pw@ep-x.neon.tech/al_rivera_cpa?sslmode=require" and schema == "agentledger"
    url, schema = provision.store_location(Path("/srv/state/agentledger.db"))       # the single-firm store
    assert url.endswith("/neondb?sslmode=require") and schema == "agentledger"
    monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "shared")
    with pytest.raises(provision.ProvisioningError):
        provision.tenancy()


@pytest.mark.skipif(os.environ.get("AGENTLEDGER_TEST_NEON") != "1", reason="live Neon test: set AGENTLEDGER_TEST_NEON=1")
def test_neon_database_per_firm_live(tmp_path, monkeypatch):
    """Against the real Neon dev branch: create a firm database, post into it, delete the firm, database gone."""
    pytest.importorskip("psycopg")
    envfile.load(REPO)
    monkeypatch.setenv("AGENTLEDGER_DATABASE", "postgres")
    monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "database")
    monkeypatch.setenv("AGENTLEDGER_PG_SCHEMA_PREFIX", "t" + secrets.token_hex(3) + "_")
    from agentledger.pg import provision

    neon = provision.Neon()
    plat = Platform(tmp_path, dev=True)
    plat.create_firm("live-check", "Live Check CPA", by="ops")
    name = provision.firm_database("live-check")
    try:
        assert name in neon.databases()
        conn = db.open_store(plat.tenant_dir("live-check") / "state" / "agentledger.db")
        store.add_client(conn, id="acme", name="Acme", kind="business")
        store.add_account(conn, "acme", "1000", "Cash", "asset")
        store.add_account(conn, "acme", "4000", "Sales", "revenue")
        store.post(conn, "acme", date(2026, 3, 1), "sale", [Line("1000", Decimal(5)), Line("4000", Decimal(-5))],
                   source="t", actor="t")
        assert store.verify_chain(conn, "acme")["ok"]
        plat.delete_firm("live-check", by="ops")
        assert name not in neon.databases()
    finally:
        if name in neon.databases():
            neon.delete_database(name)
