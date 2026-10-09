"""The API changes the web app (apps/web) needed after its first slice (docs/WEB.md, section 4): typed response and
request models with operation ids, /legacy, the sandboxed inline file route, the paged documents list, the firm on
GET /api/me, the industry packs, client id validation, fact removal, and the non-interactive bootstrap-admin."""

from __future__ import annotations

import base64
import importlib.util
import json
import secrets
from pathlib import Path

import httpx
from test_engagements import firm  # noqa: F401  (fixture)
from test_tenancy import api  # noqa: F401  (fixture that firm depends on)

REPO = Path(__file__).resolve().parent.parent
PW = "correct horse battery staple"
# Real signatures (what the inline route checks), the smallest bodies the vault will store.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
PDF = (b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
       b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n")
HTML = b"<!doctype html><html><body><script>alert(document.cookie)</script></body></html>"
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect width="1" height="1"/></svg>'


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upload(c, headers, name: str, data: bytes, client_id: str = "ortiz-auto") -> dict:
    r = c.post("/api/documents/upload", files={"file": (name, data, "application/octet-stream")}, data={"client_id": client_id},
               headers=headers)
    assert r.status_code == 200, r.text
    return r.json()[0]


def statement(i: int) -> bytes:
    return f"Form 1099-INT 2025 Payer: First Bank {i} Recipient: Ortiz Auto Interest income {i + 1},234.56".encode()


# ------------------------------------------------------------------------------ contract

def test_legacy_serves_the_previous_interface(api):  # noqa: F811
    mod, c = api
    r = c.get("/legacy")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.text == c.get("/").text and 'src="/static/app.js"' in r.text     # absolute asset URLs: works from /legacy too
    assert c.get("/static/app.js").status_code == 200


def test_operation_ids_are_handler_names_and_routes_are_typed(api):  # noqa: F811
    mod, c = api
    doc = mod.app.openapi()
    ids = [op["operationId"] for methods in doc["paths"].values() for op in methods.values()]
    assert len(ids) == len(set(ids)), "operation ids must be unique: the generated client is keyed by them"
    assert doc["paths"]["/api/me"]["get"]["operationId"] == "get_me"
    assert doc["paths"]["/api/clients/{client_id}"]["get"]["operationId"] == "client_detail"
    assert doc["paths"]["/api/me"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Me"}
    assert doc["paths"]["/api/clients"]["post"]["requestBody"]["content"]["application/json"]["schema"] == \
        {"$ref": "#/components/schemas/CreateClientRequest"}
    login = doc["paths"]["/api/auth/login"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert login["anyOf"] == [{"$ref": "#/components/schemas/MfaStep"}, {"$ref": "#/components/schemas/EnrolStep"}]
    assert {"Me", "FirmRef", "Client", "ClientDetail", "ClientFacts", "Dashboard", "IngestedDocument", "DuplicateDocument", "ReviewDocument",
            "LinkResult", "FirmUser", "SessionUser", "InviteResult", "AuthConfig", "MfaResult", "DocumentPage", "Pack",
            "CreateClientRequest", "LoginRequest", "InviteRequest"} <= set(doc["components"]["schemas"])
    me = doc["components"]["schemas"]["Me"]
    assert me["properties"]["firm"]["anyOf"][0] == {"$ref": "#/components/schemas/FirmRef"}
    assert me["additionalProperties"] is True and "engaged" not in me["required"]   # staff only: optional in the contract
    # scripts/export_openapi.py renders exactly this document: keys sorted, two-space indent, LF, one trailing newline.
    text = _script("export_openapi").render(doc)
    assert json.loads(text) == doc and "\r" not in text and text.endswith("}\n") and text.startswith('{\n  "components"')


# ------------------------------------------------------------------------------ me, firms, users

def test_me_names_the_firm_and_keeps_its_key_set(firm):  # noqa: F811
    mod, c, p, ids = firm
    me = c.get("/api/me", headers=p["lee"]).json()
    assert me["firm"] == {"id": "rivera-cpa", "name": "Rivera CPA", "status": "active"}
    assert me["role"] == "cpa" and me["base_role"] == "cpa" and me["reviewer"] is True and me["disabled"] is False
    assert "engaged" not in me and "token" not in me                 # staff only; never the session token
    assert {"id", "firm_id", "email", "name", "role", "base_role", "client_id", "mfa_enrolled_at", "disabled", "last_login_at",
            "reviewer", "session_id", "auth_method", "fresh_at", "firm"} <= set(me)
    sam = c.get("/api/me", headers=p["sam"]).json()
    assert sam["base_role"] == "staff" and sam["role"] == "cpa" and sam["engaged"] == []
    ops = c.get("/api/me", headers=p["ops"]).json()
    assert ops["firm"] is None and ops["role"] == ops["base_role"] == "platform_admin"
    # Platform administrators read a firm by id for a selected-firm context; firm members do not.
    assert c.get("/api/platform/firms/rivera-cpa", headers=p["ops"]).json()["name"] == "Rivera CPA"
    assert c.get("/api/platform/firms/no-such-firm", headers=p["ops"]).status_code == 404
    assert c.get("/api/platform/firms/rivera-cpa", headers=p["lee"]).status_code == 403
    users = c.get("/api/auth/users", headers=p["admin"]).json()
    assert users and all(isinstance(u["disabled"], bool) and isinstance(u["reviewer"], bool) for u in users)
    assert all(("engaged" in u) == (u["role"] == "staff") for u in users)


def test_sign_in_steps_and_bodies_are_validated(firm):  # noqa: F811
    mod, c, p, ids = firm
    r = c.post("/api/auth/login", json={"email": "lee@rivera.example"})          # a missing field names itself
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "password"]
    assert c.post("/api/auth/login", json={"email": "lee@rivera.example", "password": "wrong-" + PW}).status_code == 401
    step = c.post("/api/auth/login", json={"email": "lee@rivera.example", "password": PW}).json()
    assert step == {"next": "mfa", "challenge": step["challenge"]}                # the enrolled account's step, nothing else
    assert c.post("/api/auth/mfa", json={"challenge": step["challenge"]}).status_code == 422
    assert c.post("/api/auth/invite", json={"email": "x@rivera.example", "role": "owner"}, headers=p["admin"]).status_code == 422
    assert c.post("/api/auth/invite", json={"email": "x@rivera.example"}, headers=p["admin"]).status_code == 422
    assert c.post("/api/links", json={}, headers=p["lee"]).status_code == 422
    assert c.post("/api/links", json={"path": "/api/clients"}, headers=p["lee"]).status_code == 400
    assert c.post(f"/api/auth/users/{ids['sam']}/disable", headers=p["admin"]).json() == {"ok": True}   # no body: disabled
    assert c.post(f"/api/auth/users/{ids['sam']}/disable", json={"disabled": False}, headers=p["admin"]).json() == {"ok": True}
    assert c.post("/api/platform/firms", json={"id": "x"}, headers=p["ops"]).status_code == 422


# ------------------------------------------------------------------------------ clients

def test_client_detail_dashboard_and_list_keep_their_key_sets(firm):  # noqa: F811
    mod, c, p, ids = firm
    lee = p["lee"]
    business = c.get("/api/clients/ortiz-auto", headers=lee).json()
    assert {"client", "year", "pack", "balances", "kpis", "findings", "documents", "tasks", "deadlines", "opportunities", "chain",
            "integrity", "m1", "ar", "vendors_1099", "deals"} <= set(business)
    assert business["chain"] == {"ok": True, "checked": 0, "head": "genesis"} or business["chain"]["ok"] is True
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=lee).status_code == 200
    individual = c.get("/api/clients/jordan-lee", headers=lee).json()
    assert not {"m1", "ar", "vendors_1099", "deals"} & set(individual)        # business-only parts stay absent, not null
    assert individual["client"]["facts"] == {} and individual["pack"]["id"] == "general"
    dash = c.get("/api/dashboard", headers=lee).json()
    assert {"clients", "today", "pending", "adopted", "staleness", "review_queue", "tasks", "runs", "ai", "kb", "ai_usage"} <= set(dash)
    assert {x["id"] for x in dash["clients"]} == {"ortiz-auto", "lakeside-fuel", "jordan-lee"}
    listed = c.get("/api/clients", headers=lee).json()
    assert len(listed) == 3 and all({"id", "name", "kind", "entity_type", "formed_under", "tax_id_last4", "emails", "aliases",
                                     "consent_7216_at", "closed_through", "domain", "facts", "created_at"} <= set(x) for x in listed)


def test_client_ids_are_validated_and_duplicates_are_conflicts(firm):  # noqa: F811
    mod, c, p, ids = firm
    lee = p["lee"]
    r = c.post("/api/clients", json={"id": "Bad Id", "name": "X"}, headers=lee)
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "id"]
    assert c.post("/api/clients", json={"id": "with.dot", "name": "X"}, headers=lee).status_code == 422    # the database's rule
    assert c.post("/api/clients", json={"id": "a", "name": "A"}, headers=lee).status_code == 200            # one character, as before
    assert c.post("/api/clients", json={"id": "snake_case-9", "name": "S"}, headers=lee).status_code == 200
    r = c.post("/api/clients", json={"id": "ortiz-auto", "name": "Again"}, headers=lee)
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    assert c.post("/api/clients", json={"name": "No id"}, headers=lee).status_code == 422
    assert c.post("/api/clients", json={"id": "k", "name": "K", "kind": "trust"}, headers=lee).status_code == 422
    assert c.post("/api/clients", json={"id": "k", "name": "K", "facts": {"fleet_size": 3}}, headers=lee).status_code == 200
    assert c.get("/api/clients/k", headers=lee).json()["client"]["facts"] == {"fleet_size": 3}


def test_facts_patch_merges_and_null_removes(firm):  # noqa: F811
    mod, c, p, ids = firm
    lee = p["lee"]
    r = c.patch("/api/clients/ortiz-auto/facts", json={"accounting_basis": "cash", "state": "TX", "fleet_size": 4}, headers=lee)
    assert r.status_code == 200 and r.json() == {"accounting_basis": "cash", "state": "TX", "fleet_size": 4}
    r = c.patch("/api/clients/ortiz-auto/facts", json={"state": None, "employees": 7}, headers=lee)
    assert r.json() == {"accounting_basis": "cash", "fleet_size": 4, "employees": 7}
    facts = c.get("/api/clients/ortiz-auto", headers=lee).json()["client"]["facts"]
    assert facts == {"accounting_basis": "cash", "fleet_size": 4, "employees": 7}
    r = c.patch("/api/clients/ortiz-auto/facts", json={"fleet_size": None, "employees": None, "accounting_basis": None}, headers=lee)
    assert r.status_code == 200 and r.json() == {}


def test_packs_list_the_industry_packs(firm):  # noqa: F811
    mod, c, p, ids = firm
    assert c.get("/api/packs").status_code == 401
    packs = c.get("/api/packs", headers=p["lee"]).json()
    assert {"general", "auto_repair", "gas_station"} <= {x["id"] for x in packs}
    assert all({"id", "title", "description", "status", "facts"} == set(x) for x in packs)
    assert c.get("/api/packs", headers=p["ops"]).status_code == 200          # shared by every firm, readable by the platform too


# ------------------------------------------------------------------------------ documents

def test_documents_are_paged_newest_first(firm):  # noqa: F811
    mod, c, p, ids = firm
    lee = p["lee"]
    docs = [upload(c, lee, f"1099int-{i}.txt", statement(i)) for i in range(3)]
    assert all(d["status"] == "filed" and d["client_id"] == "ortiz-auto" for d in docs), docs
    page = c.get("/api/clients/ortiz-auto/documents?limit=2", headers=lee).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["next_cursor"]
    rest = c.get(f"/api/clients/ortiz-auto/documents?limit=2&cursor={page['next_cursor']}", headers=lee).json()
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None and rest["total"] == 3
    seen = [d["id"] for d in page["items"] + rest["items"]]
    assert sorted(seen) == sorted(d["id"] for d in docs)
    stamps = [(d["received_at"], d["id"]) for d in page["items"] + rest["items"]]
    assert stamps == sorted(stamps, reverse=True)                              # newest first, ids break ties
    detail = c.get("/api/clients/ortiz-auto", headers=lee).json()["documents"]
    assert set(page["items"][0]) == set(detail[0]) and page["items"][0] == detail[0]   # the fields the detail embeds
    assert c.get("/api/clients/ortiz-auto/documents?cursor=nonsense", headers=lee).status_code == 400
    assert c.get("/api/clients/ortiz-auto/documents?limit=0", headers=lee).status_code == 422
    assert c.get("/api/clients/ortiz-auto/documents?limit=201", headers=lee).status_code == 422
    assert c.get("/api/clients/ortiz-auto/documents", headers=p["sam"]).status_code == 403      # not engaged
    assert c.get("/api/clients/ortiz-auto/documents", headers=p["ops"]).status_code == 403      # no client data for the platform
    assert c.get("/api/clients/no-such/documents", headers=lee).status_code == 404
    # A document deleted under retention leaves the list and the count.
    mod.firm_context("rivera-cpa").conn.execute("UPDATE documents SET deleted_at = ? WHERE id = ?", ("2026-10-09T00:00:00", docs[0]["id"]))
    page = c.get("/api/clients/ortiz-auto/documents", headers=lee).json()
    assert page["total"] == 2 and docs[0]["id"] not in {d["id"] for d in page["items"]}


def test_upload_results_distinguish_new_from_duplicate(firm):  # noqa: F811
    mod, c, p, ids = firm
    first = upload(c, p["lee"], "1099int.txt", statement(9))
    assert "duplicate" not in first
    assert set(first) == {"id", "client_id", "status", "doc_type", "tax_year", "confidence", "vault_path", "retention_class", "match",
                          "name", "suggested_client"}
    again = upload(c, p["lee"], "copy-of-1099int.txt", statement(9))
    assert again["duplicate"] is True and again["id"] == first["id"]
    assert set(again) == {"duplicate", "id", "client_id", "status", "vault_path", "name"}


def test_inline_file_route_renders_only_pdf_png_jpeg_in_a_sandbox(firm):  # noqa: F811
    mod, c, p, ids = firm
    lee = p["lee"]
    cases = {"scan.png": (PNG, "image/png"), "photo.jpg": (JPEG, "image/jpeg"), "photo2.JPEG": (JPEG + b"\x00", "image/jpeg"),
             "return.pdf": (PDF, "application/pdf")}
    # Distinct bytes per name: the same bytes under a second name would be the first document again (a duplicate).
    never = {"page.html": HTML, "logo.svg": SVG, "fake.png": HTML + b"<!-- named .png -->", "fake.pdf": SVG + b"<!-- named .pdf -->",
             "fake.jpg": PNG + b"\x00", "noext": PDF + b"%% no extension\n"}
    uploaded = {name: upload(c, lee, name, data)["id"] for name, data in {**{k: v[0] for k, v in cases.items()}, **never}.items()}
    for name, (data, media_type) in cases.items():
        r = c.get(f"/api/documents/{uploaded[name]}/file?inline=1", headers=lee)
        assert r.status_code == 200 and r.content == data, (name, r.status_code, r.text[:200])
        assert r.headers["content-type"] == media_type
        assert r.headers["content-disposition"] == f'inline; filename="{name}"'
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["content-security-policy"] == "sandbox; default-src 'none'"
        assert r.headers["cache-control"] == "private, no-store"
        plain = c.get(f"/api/documents/{uploaded[name]}/file", headers=lee)             # without inline=1: a download, as before
        assert plain.headers["content-disposition"] == f'attachment; filename="{name}"'
        assert plain.headers["content-type"] == "application/octet-stream" and plain.content == data
    for name in never:
        r = c.get(f"/api/documents/{uploaded[name]}/file?inline=1", headers=lee)
        assert r.status_code == 200, (name, r.text[:200])
        assert r.headers["content-disposition"] == f'attachment; filename="{name}"', name
        assert r.headers["content-type"] == "application/octet-stream" and r.headers["x-content-type-options"] == "nosniff"
        assert "content-security-policy" not in r.headers
    # Through a signed link too: the token signs the path, the query carries the request.
    link = c.post("/api/links", json={"path": f"/api/documents/{uploaded['scan.png']}/file"}, headers=lee).json()["url"]
    r = c.get(link + "&inline=1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.headers["content-disposition"].startswith("inline")
    link = c.post("/api/links", json={"path": f"/api/documents/{uploaded['page.html']}/file"}, headers=lee).json()["url"]
    r = c.get(link + "&inline=1")
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")
    assert c.get(f"/api/documents/{uploaded['scan.png']}/file?inline=1").status_code == 401     # no session, no link


def test_file_names_cannot_escape_the_disposition_header(firm):  # noqa: F811
    mod, c, p, ids = firm
    content_disposition = mod.content_disposition
    assert content_disposition("attachment", 'a"b\r\nX-Evil: 1.pdf') == 'attachment; filename="a_b__X-Evil: 1.pdf"'
    assert content_disposition("inline", "reçu.png") == "inline; filename=\"re?u.png\"; filename*=UTF-8''re%C3%A7u.png"
    assert mod.inline_media_type("x.pdf", PDF) == "application/pdf" and mod.inline_media_type("x.pdf", HTML) is None
    assert mod.inline_media_type("x.png", PNG) == "image/png" and mod.inline_media_type("x.svg", SVG) is None
    assert mod.inline_media_type("x.html", HTML) is None and mod.inline_media_type("x", PNG) is None


# ------------------------------------------------------------------------------ operators and the release

def test_bootstrap_admin_reads_the_password_without_a_terminal(home, monkeypatch):
    from typer.testing import CliRunner

    from agentledger.cli import app as cli
    from agentledger.security.platform import Platform

    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))
    monkeypatch.setenv("AGENTLEDGER_MASTER_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.delenv("AGENTLEDGER_DEV_AUTH", raising=False)
    runner = CliRunner()
    base = ["platform", "bootstrap-admin", "--email", "ops@agentledger.example", "--name", "Ops"]
    r = runner.invoke(cli, [*base, "--password-env", "E2E_ADMIN_PASSWORD"], env={"E2E_ADMIN_PASSWORD": ""})
    assert r.exit_code == 2 and "E2E_ADMIN_PASSWORD" in r.output                   # unset or empty: refused, nothing created
    r = runner.invoke(cli, [*base, "--password-env", "E2E_ADMIN_PASSWORD"], env={"E2E_ADMIN_PASSWORD": PW})
    assert r.exit_code == 0, r.output
    plat = Platform(home, dev=False)
    try:
        assert "enroll" in plat.login("ops@agentledger.example", PW)              # the password from the variable works
    finally:
        plat.close()
    r = runner.invoke(cli, [*base, "--password-stdin"], input=PW + "\n")
    assert r.exit_code == 1 and "already exists" in r.output                          # stdin is read; the platform rule holds
    r = runner.invoke(cli, [*base, "--password-stdin"], input="")
    assert r.exit_code == 2


def test_smoke_checks_the_web_app_at_the_root(api, monkeypatch):  # noqa: F811
    """scripts/smoke.py: GET / must be the built web app at the expected build; the previous interface passes only with
    SMOKE_DEV=1 (a --dev API without the assets in front)."""
    smoke = _script("smoke")
    pages = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body, content_type = pages[request.url.path]
        return httpx.Response(200, content=body, headers={"content-type": content_type})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.example")
    app_page = b'<!doctype html><html><head><meta name="agentledger-build" content="abc123"><title>AgentLedger</title></head></html>'
    pages["/"] = (app_page, "text/html; charset=utf-8")
    assert smoke.check_web_app(client, "abc123", dev=False) == "web app build abc123"
    assert smoke.check_web_app(client, None, dev=False) == "web app build abc123"
    try:
        smoke.check_web_app(client, "def456", dev=False)
    except smoke.Failed as e:
        assert "out of step" in str(e)
    else:
        raise AssertionError("a build mismatch must fail")
    mod, c = api
    pages["/"] = (c.get("/").content, "text/html; charset=utf-8")                   # the previous interface: no build meta
    try:
        smoke.check_web_app(client, "abc123", dev=False)
    except smoke.Failed as e:
        assert "not the built web app" in str(e)
    else:
        raise AssertionError("the previous interface at / must fail outside SMOKE_DEV")
    assert "previous interface" in smoke.check_web_app(client, "abc123", dev=True)
    pages["/"] = (b'{"ok": true}', "application/json")
    try:
        smoke.check_web_app(client, None, dev=True)
    except smoke.Failed as e:
        assert "not an HTML page" in str(e)
    else:
        raise AssertionError("JSON at / must fail")
