"""Multi-firm SaaS mode: real sign-in with MFA, and isolation between firms."""

import base64
import importlib
import secrets
import time

import pytest

from agentledger.security import totp

PW = "correct horse battery staple"


@pytest.fixture
def api(home, monkeypatch):
    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))
    monkeypatch.setenv("AGENTLEDGER_AGENTS", "0")
    monkeypatch.delenv("AGENTLEDGER_DEV_AUTH", raising=False)
    monkeypatch.setenv("AGENTLEDGER_MASTER_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    import agentledger.api.app as api_mod

    importlib.reload(api_mod)
    from fastapi.testclient import TestClient

    return api_mod, TestClient(api_mod.app)


def enrol(c, challenge, secret):
    r = c.post("/api/auth/mfa", json={"challenge": challenge, "code": totp.code_at(secret, int(time.time() // 30))})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def accept(c, token, name):
    r = c.post("/api/auth/accept", json={"token": token, "name": name, "password": PW})
    assert r.status_code == 200, r.text
    return enrol(c, r.json()["challenge"], r.json()["secret"])


def test_two_firms_are_isolated(api):
    mod, c = api
    admin_id = mod.PLATFORM.bootstrap_admin("ops@agentledger.example", "Ops", PW)
    step = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    assert step["next"] == "enroll"
    ops = enrol(c, step["challenge"], step["secret"])

    assert c.get("/api/users").status_code == 404                      # no demo identity picker in production
    assert c.get("/api/me", headers={"Authorization": "Bearer dev-cpa"}).status_code == 401

    firms = {}
    for fid, email in (("rivera-cpa", "maya@rivera.example"), ("lake-tax", "lee@lake.example")):
        r = c.post("/api/platform/firms", json={"id": fid, "name": fid, "admin_email": email}, headers=ops)
        assert r.status_code == 200, r.text
        firms[fid] = accept(c, r.json()["admin_invite_token"], fid)

    a, b = firms["rivera-cpa"], firms["lake-tax"]
    assert c.post("/api/clients", json={"id": "ortiz-auto", "name": "Ortiz Auto", "domain": "auto_repair"}, headers=a).status_code == 200
    assert c.post("/api/clients", json={"id": "lakeside-fuel", "name": "Lakeside", "domain": "gas_station"}, headers=b).status_code == 200

    assert [x["id"] for x in c.get("/api/clients", headers=a).json()] == ["ortiz-auto"]
    assert [x["id"] for x in c.get("/api/clients", headers=b).json()] == ["lakeside-fuel"]
    assert c.get("/api/clients/lakeside-fuel", headers=a).status_code == 404   # not in firm A's database at all
    assert c.get("/api/clients/ortiz-auto", headers=ops).status_code == 403    # platform staff cannot read client data
    # The same client id can exist in two firms without collision.
    assert c.post("/api/clients", json={"id": "ortiz-auto", "name": "Other Ortiz"}, headers=b).status_code == 200
    assert c.get("/api/clients/ortiz-auto", headers=a).json()["client"]["name"] == "Ortiz Auto"

    # Each firm has its own store: a database file on SQLite, a schema (a database in production) on PostgreSQL.
    home = mod.ROOT
    from agentledger import db

    for firm in ("rivera-cpa", "lake-tax"):
        path = home / "tenants" / firm / "state" / "agentledger.db"
        if db.backend() == "postgres":
            from agentledger import pg

            owner = pg.connect(pg.dsn(direct=True))
            assert owner.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (db.schema_for(path),)).fetchone()
            owner.close()
        else:
            assert path.exists()

    # A firm admin invites a client user, who sees only their business.
    tok = c.post("/api/auth/invite", json={"email": "sam@ortiz.example", "role": "client", "client_id": "ortiz-auto"},
                 headers=a).json()["invite_token"]
    sam = accept(c, tok, "Sam Ortiz")
    assert [x["id"] for x in c.get("/api/clients", headers=sam).json()] == ["ortiz-auto"]
    assert c.get("/api/proposals", headers=sam).status_code == 403
    assert c.post("/api/auth/invite", json={"email": "x@y.example", "role": "cpa"}, headers=sam).status_code == 403

    # Regulation decisions are for platform reviewers, not a single firm.
    assert c.post("/api/proposals/nope/approve", headers=a).status_code == 403
    assert c.get("/api/proposals", headers=a).status_code == 200

    # Downloads use short-lived signed links, never the session token in the URL.
    path = "/api/clients/ortiz-auto/export/beancount_export"
    link = c.post("/api/links", json={"path": path}, headers=a).json()["url"]
    assert c.get(link).status_code == 200
    forged = link.replace("ortiz-auto", "lakeside-fuel")
    assert c.get(forged).status_code == 401
    assert c.get(path + "?token=anything").status_code == 401

    # Sign-in events are recorded per firm.
    events = {e["event"] for e in c.get("/api/auth/events", headers=a).json()}
    assert {"invite_accepted", "mfa_enrolled", "login"} <= events


def test_firm_deletion_locks_everyone_out(api):
    mod, c = api
    mod.PLATFORM.bootstrap_admin("ops@agentledger.example", "Ops", PW)
    step = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    ops = enrol(c, step["challenge"], step["secret"])
    r = c.post("/api/platform/firms", json={"id": "gone-cpa", "name": "Gone", "admin_email": "a@gone.example"}, headers=ops)
    admin = accept(c, r.json()["admin_invite_token"], "A")
    assert c.get("/api/clients", headers=admin).status_code == 200
    mod.PLATFORM.delete_firm("gone-cpa", by="test")
    assert c.get("/api/clients", headers=admin).status_code == 401


def test_documents_are_encrypted_at_rest(api):
    mod, c = api
    mod.PLATFORM.bootstrap_admin("ops@agentledger.example", "Ops", PW)
    step = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    ops = enrol(c, step["challenge"], step["secret"])
    heads = {}
    for fid, email in (("rivera-cpa", "maya@rivera.example"), ("lake-tax", "lee@lake.example")):
        r = c.post("/api/platform/firms", json={"id": fid, "name": fid, "admin_email": email}, headers=ops)
        heads[fid] = accept(c, r.json()["admin_invite_token"], fid)
    a, b = heads["rivera-cpa"], heads["lake-tax"]
    c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=a)
    secret_text = b"Form 1099-INT 2025 Payer: First Bank Recipient: Jordan Lee SSN 123-45-6789 Interest income 1,234.56"
    r = c.post("/api/documents/upload", files={"file": ("1099int.txt", secret_text, "text/plain")},
               data={"client_id": "jordan-lee"}, headers=a)
    assert r.status_code == 200, r.text
    doc = r.json()[0]
    stored = mod.firm_context("rivera-cpa").foundry.paths.vault / doc["vault_path"]
    raw = stored.read_bytes()
    assert raw[:3] == b"VX1" and b"123-45-6789" not in raw
    path = f"/api/documents/{doc['id']}/file"
    link = c.post("/api/links", json={"path": path}, headers=a).json()["url"]
    assert c.get(link).content == secret_text
    assert c.get(path, headers=b).status_code == 404
