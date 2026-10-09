"""GET /healthz and the deployment smoke principal (scripts/smoke.py): a release pipeline can prove a deployment
serves the right build and computes a return, and can reach nothing else."""

import base64
import importlib
import secrets

import pytest
from fastapi.routing import APIRoute

from agentledger import db

TOKEN = "smoke-" + secrets.token_urlsafe(32)
# scripts/smoke.py's sample: a single filer with one W-2; Form 1040 line 16 is the Rev. Proc. 2025-32 Tax Table value.
SAMPLE = {"tax_year": 2026, "filing_status": "single", "taxpayer": {"ssn": "400-00-0001", "dob": "1985-06-01"},
          "w2s": [{"wages": 60000, "federal_withholding": 6000}]}
SMOKE = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def api(home, monkeypatch):
    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))
    monkeypatch.setenv("AGENTLEDGER_AGENTS", "0")
    monkeypatch.delenv("AGENTLEDGER_DEV_AUTH", raising=False)
    monkeypatch.setenv("AGENTLEDGER_MASTER_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.setenv("AGENTLEDGER_SMOKE_TOKEN", TOKEN)
    import agentledger.api.app as api_mod

    importlib.reload(api_mod)
    from fastapi.testclient import TestClient

    return api_mod, TestClient(api_mod.app)


def test_healthz_reports_build_and_backend_without_auth(api, monkeypatch):
    mod, c = api
    r = c.get("/healthz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["backend"] == db.backend()
    assert body["build"] == "dev"                      # the repository's BUILD_SHA; the release writes the commit SHA
    monkeypatch.setenv("AGENTLEDGER_BUILD", "0123abcd")   # read per request, never cached at import
    assert c.get("/healthz").json()["build"] == "0123abcd"


def test_smoke_token_reads_coverage_and_computes_a_return(api):
    mod, c = api
    r = c.get("/api/coverage", headers=SMOKE)
    assert r.status_code == 200, r.text
    assert any(cap["id"] == "f1040" for cap in r.json()["capabilities"])
    r = c.post("/api/returns/individual", json=SAMPLE, headers=SMOKE)
    assert r.status_code == 200, r.text
    assert r.json()["forms"]["f1040"]["16"] == "5023"


def test_wrong_short_or_unset_token_is_refused(api, monkeypatch):
    mod, c = api
    assert c.get("/api/coverage").status_code == 401
    assert c.get("/api/coverage", headers={"Authorization": f"Bearer {TOKEN[:-1]}x"}).status_code == 401
    assert c.post("/api/returns/individual", json=SAMPLE, headers={"Authorization": "Bearer " + "x" * len(TOKEN)}).status_code == 401
    short = "short-token-0123456789"                   # under 32 characters: the principal is disabled, not weakened
    monkeypatch.setenv("AGENTLEDGER_SMOKE_TOKEN", short)
    assert c.get("/api/coverage", headers={"Authorization": f"Bearer {short}"}).status_code == 401
    monkeypatch.delenv("AGENTLEDGER_SMOKE_TOKEN")
    assert c.get("/api/coverage", headers=SMOKE).status_code == 401


def test_smoke_principal_is_refused_everywhere_else(api):
    mod, c = api
    # The routes a careless smoke principal would reach first: firm data, uploads, platform administration, identities.
    assert c.get("/api/clients", headers=SMOKE).status_code == 403
    assert c.post("/api/clients", json={"id": "x", "name": "X"}, headers=SMOKE).status_code == 403
    assert c.post("/api/documents/upload", files={"file": ("a.txt", b"hello", "text/plain")}, headers=SMOKE).status_code == 403
    assert c.get("/api/platform/firms", headers=SMOKE).status_code == 403
    assert c.post("/api/platform/firms", json={"id": "f", "name": "F", "admin_email": "a@f.example"}, headers=SMOKE).status_code == 403
    assert c.get("/api/users", headers=SMOKE).status_code == 404        # the demo picker does not exist outside dev
    assert c.get("/api/me", headers=SMOKE).status_code == 403
    assert c.get("/api/calculators", headers=SMOKE).status_code == 403  # stateless, but not on the smoke list
    assert c.get("/api/proposals", headers=SMOKE).status_code == 403
    assert c.get("/api/documents/1/file", headers=SMOKE).status_code == 403
    assert c.get("/api/clients/x/export/beancount_export", headers=SMOKE).status_code == 403
    # And every API route the app exposes: only the two stateless computations answer 200 to the smoke token.
    public = {"/", "/legacy", "/healthz", "/api/auth/config", "/api/auth/idp/start", "/api/auth/idp/callback", "/api/auth/login",
              "/api/auth/mfa", "/api/auth/accept", "/api/auth/logout", "/api/auth/step-up", "/api/hooks/{firm_id}/{client_id}"}
    allowed = {("GET", "/api/coverage"), ("POST", "/api/returns/individual")}
    seen = set()
    for route in mod.app.routes:
        if not isinstance(route, APIRoute) or route.path in public:
            continue
        for method in route.methods:
            if (method, route.path) in allowed:
                continue
            path = route.path.replace("{", "").replace("}", "")
            for name in route.param_convertors:
                path = path.replace(name, "1")
            r = c.request(method, path, headers=SMOKE)
            assert r.status_code in (403, 404), (method, route.path, r.status_code, r.text)
            seen.add((method, route.path))
    assert len(seen) > 80, "the sweep must cover the whole API"
    # And nothing was written on the smoke principal's behalf: no firm exists, no session, no sign-in event.
    assert mod.PLATFORM.firms() == []
    assert not mod.PLATFORM.conn.execute("SELECT 1 FROM sessions").fetchone()
    assert mod.PLATFORM.events() == []
