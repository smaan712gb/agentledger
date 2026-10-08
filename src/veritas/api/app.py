"""HTTP API + web app. Every client-scoped route goes through `scope()`, which is where client
segregation is enforced: a client user can only ever reach their own client's data."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import audit
from ..app_context import AppContext
from ..ask.engine import ask_stream
from ..brain.playbooks import add_precedent
from ..calc.engine import Ctx
from ..calc.federal import CALCULATORS, run_calc
from ..crm import automations as autos
from ..crm import business, core as crm
from ..db import one, rows
from ..domains import service as domains
from ..foundry.agents import builders
from ..foundry.agents.staleness import scan as staleness_scan
from ..integrity.checks import integrity_score, list_findings, resolve, run_all
from ..intake.pipeline import assign as assign_document, ingest
from ..ledger import bankfeed, m1, store
from ..plugins.registry import discover, run_plugin
from ..security.platform import PLATFORM_FIRM, AuthError, Platform
from ..security.vault import Vault
from ..workflow.engine import TransitionError

ROOT = Path(os.environ.get("VERITAS_HOME", Path.cwd())).resolve()
WEB = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="AgentLedger", version="0.1.0")
# Dev mode keeps the single-firm layout and the demo identities in config/users.yaml. It must never be
# enabled on a deployment that holds real taxpayer data.
DEV = os.environ.get("VERITAS_DEV_AUTH") == "1"
DEV_FIRM = "dev"
APP: AppContext = AppContext.open(ROOT, scope="all" if DEV else "platform")
PLATFORM = Platform(ROOT, dev=DEV)
_TENANTS: dict[str, AppContext] = {}
_TENANT_LOCK = threading.Lock()
LINK_TTL = 120
FIRM_ROLES = ("firm_admin", "cpa", "staff")


# ------------------------------------------------------------------------------ identity & scope

def users() -> dict[str, dict[str, Any]]:
    if not DEV:
        return {}
    data = yaml.safe_load((ROOT / "config" / "users.yaml").read_text(encoding="utf-8"))
    return {u["token"]: {**u, "firm_id": DEV_FIRM} for u in data["users"]}


def _api_user(u: dict[str, Any]) -> dict[str, Any]:
    # The rest of the API speaks in two firm roles: firm staff share the CPA view; clients see their own business.
    return {**u, "base_role": u["role"], "role": "cpa" if u["role"] in FIRM_ROLES else u["role"]}


def _user_for_token(token: str) -> dict[str, Any] | None:
    if DEV and token in users():
        return users()[token]
    u = PLATFORM.session_user(token)
    return _api_user(u) if u else None


def me(authorization: str = Header(default="")) -> dict[str, Any]:
    u = _user_for_token(authorization.removeprefix("Bearer ").strip())
    if not u:
        raise HTTPException(401, "sign in required")
    return u


def A(user: dict[str, Any]) -> AppContext:
    """The data context of the signed-in user's firm. Each firm has its own database, vault and key."""
    firm = user.get("firm_id")
    if firm == DEV_FIRM and DEV:
        return APP
    if not firm or firm == PLATFORM_FIRM:
        raise HTTPException(403, "platform administrators manage firms; client data is only reachable from inside a firm")
    return firm_context(firm)


def firm_context(firm_id: str) -> AppContext:
    try:
        firm = PLATFORM.firm(firm_id)
    except AuthError:
        raise HTTPException(404, "firm not found")
    if firm["status"] != "active":
        raise HTTPException(403, "this firm is not active")
    with _TENANT_LOCK:
        ctx = _TENANTS.get(firm_id)
        if ctx is None:
            ctx = AppContext.open(ROOT, tenant=PLATFORM.tenant_dir(firm_id), kb=APP.kb, scope="tenant")
            ctx.foundry.vault = Vault(ctx.foundry.paths.vault, PLATFORM.keys, firm_id)
            _TENANTS[firm_id] = ctx
        return ctx


def scope(user: dict[str, Any], client_id: str) -> str:
    if user["role"] == "client" and user.get("client_id") != client_id:
        raise HTTPException(403, "you can only access your own business")
    try:
        store.get_client(A(user).conn, client_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, "client not found")
    return client_id


def cpa_only(user: dict[str, Any]) -> None:
    if user["role"] != "cpa":
        raise HTTPException(403, "CPA access required")


def platform_admin(user: dict[str, Any]) -> None:
    if user.get("base_role", user["role"]) != "platform_admin":
        raise HTTPException(403, "platform administrator access required")


def platform_ctx(user: dict[str, Any]) -> AppContext:
    """Shared platform content (regulations, agents, models). In dev the firm CPA stands in for the
    platform reviewers; in production only platform administrators decide what every firm runs on."""
    if DEV and user.get("firm_id") == DEV_FIRM:
        cpa_only(user)
    else:
        platform_admin(user)
    return APP


def _link_key() -> bytes:
    return hashlib.sha256(PLATFORM.keys.master + b"download-links").digest()


def signed_link(user: dict[str, Any], path: str) -> str:
    exp = int(time.time()) + LINK_TTL
    msg = f"{user['id']}|{user['firm_id']}|{exp}|{path}".encode()
    sig = hmac.new(_link_key(), msg, hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{user['id']}|{user['firm_id']}|{exp}|{sig}".encode()).decode()
    return f"{path}?dl={token}"


def link_user(dl: str, path: str) -> dict[str, Any]:
    """A short-lived signed link stands in for the session header on file downloads (no session token in URLs)."""
    try:
        uid, firm, exp, sig = base64.urlsafe_b64decode(dl.encode()).decode().split("|")
        expired = int(exp) < time.time()
    except Exception:
        raise HTTPException(401, "invalid link")
    expected = hmac.new(_link_key(), f"{uid}|{firm}|{exp}|{path}".encode(), hashlib.sha256).hexdigest()
    if expired or not hmac.compare_digest(sig, expected):
        raise HTTPException(401, "link expired or invalid")
    if DEV and firm == DEV_FIRM:
        for u in users().values():
            if u["id"] == uid:
                return u
        raise HTTPException(401)
    try:
        u = PLATFORM.public_user(PLATFORM.user(uid))
    except AuthError:
        raise HTTPException(401)
    if u["disabled"] or u["firm_id"] != firm:
        raise HTTPException(401)
    return _api_user(u)


def jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=lambda o: str(o) if isinstance(o, (Decimal, date)) else o.__dict__))


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _as_actor(user: dict[str, Any]) -> dict[str, Any]:
    return {**user, "role": user.get("base_role", user["role"])}


# ------------------------------------------------------------------------------ authentication

@app.post("/api/auth/login")
def auth_login(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        step = PLATFORM.login(str(body.get("email", "")), str(body.get("password", "")), ip=_ip(request))
    except AuthError as e:
        raise HTTPException(401, str(e))
    if "mfa" in step:
        return {"next": "mfa", "challenge": step["mfa"]}
    e = step["enroll"]
    return {"next": "enroll", "challenge": e.challenge, "secret": e.secret, "otpauth_uri": e.uri}


@app.post("/api/auth/mfa")
def auth_mfa(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        token = PLATFORM.complete_mfa(str(body.get("challenge", "")), str(body.get("code", "")), ip=_ip(request),
                                      user_agent=request.headers.get("user-agent"))
    except AuthError as e:
        raise HTTPException(401, str(e))
    return {"token": token, "user": PLATFORM.session_user(token)}


@app.post("/api/auth/accept")
def auth_accept(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        e = PLATFORM.accept_invite(str(body.get("token", "")), str(body.get("name", "")), str(body.get("password", "")),
                                   ip=_ip(request))
    except AuthError as err:
        raise HTTPException(400, str(err))
    return {"next": "enroll", "challenge": e.challenge, "secret": e.secret, "otpauth_uri": e.uri}


@app.post("/api/auth/logout")
def auth_logout(authorization: str = Header(default="")) -> dict[str, Any]:
    PLATFORM.logout(authorization.removeprefix("Bearer ").strip())
    return {"ok": True}


@app.post("/api/auth/invite")
def auth_invite(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    firm = body.get("firm_id") or user["firm_id"]
    if body.get("role") == "client" and body.get("client_id"):
        scope(user, body["client_id"])
    try:
        token = PLATFORM.invite(firm, str(body["email"]), str(body["role"]), by=_as_actor(user), client_id=body.get("client_id"))
    except AuthError as e:
        raise HTTPException(403, str(e))
    return {"invite_token": token, "expires_in_days": 7}


@app.get("/api/auth/users")
def auth_users(user=Depends(me)) -> list[dict[str, Any]]:
    cpa_only(user)
    return PLATFORM.users(user["firm_id"])


@app.post("/api/auth/users/{user_id}/disable")
def auth_disable(user_id: str, body: dict[str, Any] = Body(default={}), user=Depends(me)) -> dict[str, Any]:
    try:
        PLATFORM.set_disabled(user_id, bool(body.get("disabled", True)), by=_as_actor(user))
    except AuthError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}


@app.get("/api/auth/events")
def auth_events(user=Depends(me)) -> list[dict[str, Any]]:
    if user.get("base_role") == "platform_admin":
        return PLATFORM.events()
    cpa_only(user)
    return PLATFORM.events(user["firm_id"])


@app.post("/api/links")
def make_link(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    path = str(body.get("path", ""))
    if not re.fullmatch(r"/api/(documents/[\w.-]+/file|clients/[\w.-]+/export/[\w.-]+)", path):
        raise HTTPException(400, "unsupported path")
    return {"url": signed_link(user, path), "expires_in": LINK_TTL}


# ------------------------------------------------------------------------------ platform administration

@app.get("/api/platform/firms")
def platform_firms(user=Depends(me)) -> list[dict[str, Any]]:
    platform_admin(user)
    return PLATFORM.firms()


@app.post("/api/platform/firms")
def platform_create_firm(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    platform_admin(user)
    try:
        firm = PLATFORM.create_firm(str(body["id"]), str(body["name"]), by=user["id"])
        token = PLATFORM.invite(firm["id"], str(body["admin_email"]), "firm_admin", by=_as_actor(user))
    except AuthError as e:
        raise HTTPException(400, str(e))
    return {"firm": firm, "admin_invite_token": token}


# ------------------------------------------------------------------------------ pages

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (WEB / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/api/me")
def get_me(user=Depends(me)) -> dict[str, Any]:
    return {k: v for k, v in user.items() if k != "token"}


@app.get("/api/users")
def list_users() -> list[dict[str, Any]]:
    """Demo identity picker. Exists only in dev mode; production sign-in is /api/auth/login."""
    if not DEV:
        raise HTTPException(404)
    return [{k: v for k, v in u.items()} for u in users().values()]


# ------------------------------------------------------------------------------ dashboard

@app.get("/api/dashboard")
def dashboard(user=Depends(me)) -> dict[str, Any]:
    clients = store.list_clients(A(user).conn)
    if user["role"] == "client":
        clients = [c for c in clients if c["id"] == user["client_id"]]
    cards = []
    for c in clients:
        f = list_findings(A(user).conn, c["id"])
        cards.append({"id": c["id"], "name": c["name"], "kind": c["kind"], "domain": c["domain"],
                      "integrity": integrity_score(f), "open_findings": sum(1 for x in f if x["status"] == "open"),
                      "open_tasks": len(crm.tasks(A(user).conn, c["id"])),
                      "docs_this_month": one(A(user).conn, "SELECT COUNT(*) n FROM documents WHERE client_id = ? AND received_at >= ?",
                                             c["id"], date.today().replace(day=1).isoformat())["n"]})
    out: dict[str, Any] = {"clients": cards, "today": date.today().isoformat()}
    if user["role"] == "cpa":
        out.update(
            pending=[p.model_dump(include={"id", "kind", "title", "risk", "agent", "created_at"}) for p in A(user).foundry.proposals("pending")][:20],
            adopted=[p.model_dump(include={"id", "kind", "title", "risk", "agent", "decision"}) for p in A(user).foundry.proposals("adopted")][:10],
            staleness=[a for a in staleness_scan(A(user).kb, date.today()) if a["severity"] != "info"],
            review_queue=one(A(user).conn, "SELECT COUNT(*) n FROM documents WHERE status = 'needs_review'")["n"],
            tasks=crm.tasks(A(user).conn, assignee="cpa")[:15],
            runs=A(user).foundry.runs(limit=12),
            ai=A(user).router.status(),
            kb={"rules": len(A(user).kb.rules), "version": A(user).kb.version()},
            ai_usage=rows(A(user).conn, "SELECT tier, COUNT(*) calls, SUM(ok) ok FROM ai_usage WHERE substr(at,1,10) >= ? GROUP BY tier",
                          date.today().replace(day=1).isoformat()),
        )
    else:
        out["tasks"] = crm.tasks(A(user).conn, user["client_id"], assignee="client")
    return jsonable(out)


# ------------------------------------------------------------------------------ clients

@app.get("/api/clients")
def clients(user=Depends(me)) -> list[dict[str, Any]]:
    cs = store.list_clients(A(user).conn)
    return [c for c in cs if user["role"] == "cpa" or c["id"] == user["client_id"]]


@app.post("/api/clients")
def create_client(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    store.add_client(A(user).conn, id=body["id"], name=body["name"], kind=body.get("kind", "business"),
                     entity_type=body.get("entity_type"), formed_under=body.get("formed_under", "domestic"),
                     tax_id_last4=body.get("tax_id_last4"), emails=body.get("emails", []), aliases=body.get("aliases", []),
                     consent_7216_at=body.get("consent_7216_at"), domain=body.get("domain", "general"), facts=body.get("facts", {}),
                     actor=user["id"])
    n = domains.onboard(A(user).conn, A(user).packs, body["id"], body.get("domain", "general"))
    return {"id": body["id"], "accounts_created": n}


@app.get("/api/clients/{client_id}")
def client_detail(client_id: str, year: int | None = None, user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    year = year or date.today().year
    c = store.get_client(A(user).conn, client_id)
    out: dict[str, Any] = {"client": c, "year": year,
                           "pack": A(user).packs.get(c["domain"]).model_dump(include={"id", "title", "description", "facts"})
                           if c["domain"] in A(user).packs.packs else None,
                           "balances": store.balances(A(user).conn, client_id, date(year, 1, 1), date(year, 12, 31)),
                           "kpis": domains.kpis(A(user).conn, A(user).packs, client_id, year),
                           "findings": list_findings(A(user).conn, client_id),
                           "documents": rows(A(user).conn, "SELECT id, original_name, doc_type, tax_year, status, confidence, vault_path, "
                                                       "summary, classified_by, received_at, channel FROM documents WHERE client_id = ? "
                                                       "ORDER BY received_at DESC LIMIT 100", client_id),
                           "tasks": crm.tasks(A(user).conn, client_id),
                           "deadlines": crm.deadlines(A(user).conn, ROOT / "config" / "deadlines.yaml", client_id),
                           "opportunities": A(user).brain.scan(A(user).conn, client_id),
                           "chain": store.verify_chain(A(user).conn, client_id)}
    out["integrity"] = integrity_score(out["findings"])
    if c["kind"] == "business":
        try:
            out["m1"] = m1.compute(A(user).conn, Ctx(A(user).kb), client_id, year).as_dict()
        except Exception as e:
            out["m1"] = {"error": str(e)}
        out["ar"] = business.ar_aging(A(user).conn, client_id)
        out["vendors_1099"] = business.vendor_1099_status(A(user).conn, A(user).kb, client_id, year)
        out["deals"] = business.deal_board(A(user).conn, client_id)
    return jsonable(out)


@app.patch("/api/clients/{client_id}/facts")
def update_facts(client_id: str, facts: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    c = store.get_client(A(user).conn, client_id)
    merged = {**c["facts"], **facts}
    A(user).conn.execute("UPDATE clients SET facts = ? WHERE id = ?", (json.dumps(merged), client_id))
    audit.record(A(user).conn, user["id"], user["role"], "client.facts", {"changed": facts}, client_id=client_id)
    return merged


@app.get("/api/clients/{client_id}/entries")
def client_entries(client_id: str, user=Depends(me)) -> list[dict[str, Any]]:
    scope(user, client_id)
    return store.entries(A(user).conn, client_id, limit=500)[::-1]


@app.get("/api/clients/{client_id}/templates")
def client_templates(client_id: str, user=Depends(me)) -> list[dict[str, Any]]:
    scope(user, client_id)
    c = store.get_client(A(user).conn, client_id)
    return [t.model_dump() for t in A(user).packs.templates(c["domain"]).values()]


@app.post("/api/clients/{client_id}/templates/{template_id}")
def post_template(client_id: str, template_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    on = date.fromisoformat(body.pop("date", date.today().isoformat()))
    try:
        entry = domains.post_template(A(user).conn, A(user).packs, A(user).kb, client_id, template_id, body, on, actor=user["id"], role=user["role"])
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"entry_id": entry}


@app.post("/api/clients/{client_id}/entries/{entry_id}/reverse")
def reverse_entry(client_id: str, entry_id: int, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    cpa_only(user)
    return {"entry_id": store.reverse(A(user).conn, client_id, entry_id, date.today(), body["reason"], user["id"])}


@app.post("/api/clients/{client_id}/bank/preview")
def bank_preview(client_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> list[dict[str, Any]]:
    scope(user, client_id)
    try:
        txns = bankfeed.parse_csv(body["csv"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return jsonable(bankfeed.suggest(A(user).conn, A(user).router, client_id, txns))


@app.post("/api/clients/{client_id}/bank/post")
def bank_post(client_id: str, body: list[dict[str, Any]] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    ok = [t for t in body if t.get("account") and not t.get("duplicate")]
    return {"posted": bankfeed.post_confirmed(A(user).conn, client_id, ok, user["id"], user["role"])}


@app.post("/api/clients/{client_id}/integrity/run")
def integrity_run(client_id: str, year: int | None = None, user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    f = run_all(A(user).conn, A(user).kb, client_id, year or date.today().year, packs=A(user).packs)
    return {"findings": f, "score": integrity_score(f)}


@app.post("/api/findings/{finding_id}/resolve")
def resolve_finding(finding_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    f = one(A(user).conn, "SELECT client_id FROM findings WHERE id = ?", finding_id)
    if not f:
        raise HTTPException(404)
    scope(user, f["client_id"])
    try:
        resolve(A(user).conn, finding_id, user["id"], user["role"], body["action"], body.get("note", ""))
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))
    if body.get("save_as_precedent") and user["role"] == "cpa":
        add_precedent(A(user).conn, topic=body.get("topic") or "finding resolution", situation=body.get("situation", ""),
                      judgment=body.get("note", ""), citations=body.get("citations", []), author=user["id"], client_id=f["client_id"])
    return {"ok": True}


@app.get("/api/clients/{client_id}/opportunities")
def opportunities(client_id: str, user=Depends(me)) -> list[dict[str, Any]]:
    scope(user, client_id)
    return jsonable(A(user).brain.scan(A(user).conn, client_id))


# ------------------------------------------------------------------------------ ask

@app.post("/api/ask")
def ask(body: dict[str, Any] = Body(...), user=Depends(me)) -> StreamingResponse:
    client_id = body.get("client_id")
    if user["role"] == "client":
        client_id = user["client_id"]
    elif client_id:
        scope(user, client_id)

    def gen():
        try:
            for ev in ask_stream(A(user).conn, A(user).kb, A(user).router, body["question"], client_id=client_id, actor=user["id"],
                                 role=user["role"], deep=body.get("deep"), brain=A(user).brain):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/precedents")
def new_precedent(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return {"id": add_precedent(A(user).conn, topic=body["topic"], situation=body["situation"], judgment=body["judgment"],
                                citations=body.get("citations", []), author=user["id"], domain=body.get("domain"),
                                client_id=body.get("client_id"))}


@app.get("/api/playbooks")
def playbooks(user=Depends(me)) -> list[dict[str, Any]]:
    return jsonable([{**pb.model_dump(), "freshness": A(user).brain.freshness(pb)} for pb in A(user).brain.playbooks.values()])


# ------------------------------------------------------------------------------ documents

@app.post("/api/documents/upload")
async def upload(file: UploadFile = File(...), client_id: str | None = Form(default=None), user=Depends(me)) -> list[dict[str, Any]]:
    if user["role"] == "client":
        client_id = user["client_id"]
    elif client_id:
        scope(user, client_id)
    data = await file.read()
    return jsonable(ingest(A(user).conn, A(user).router, A(user).foundry.vault, file.filename or "upload.bin", data, channel="upload",
                           client_hint=client_id, actor=user["id"]))


@app.get("/api/documents/review")
def review_queue(user=Depends(me)) -> list[dict[str, Any]]:
    cpa_only(user)
    return rows(A(user).conn, "SELECT id, original_name, doc_type, tax_year, confidence, summary, sender, received_at, channel, "
                          "classified_by FROM documents WHERE status = 'needs_review' ORDER BY received_at DESC")


@app.post("/api/documents/{doc_id}/assign")
def assign_doc(doc_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return jsonable(assign_document(A(user).conn, A(user).foundry.vault, doc_id, body["client_id"], user["id"]))


@app.get("/api/documents/{doc_id}/file")
def doc_file(doc_id: str, dl: str = "", authorization: str = Header(default="")) -> FileResponse:
    user = link_user(dl, f"/api/documents/{doc_id}/file") if dl else me(authorization)
    d = one(A(user).conn, "SELECT * FROM documents WHERE id = ?", doc_id)
    if not d:
        raise HTTPException(404)
    if d["client_id"]:
        scope(user, d["client_id"])
    else:
        cpa_only(user)
    data = A(user).foundry.vault.read(d["vault_path"])
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{d["original_name"]}"'})


# ------------------------------------------------------------------------------ rules & calculators

@app.get("/api/rules")
def rules(user=Depends(me)) -> list[dict[str, Any]]:
    today = date.today()
    out = []
    for r in sorted(A(user).kb.rules.values(), key=lambda r: r.id):
        cur = r.value_on(today)
        out.append({"id": r.id, "title": r.title, "jurisdiction": r.jurisdiction, "category": r.category, "unit": r.unit,
                    "citation": r.citation, "indexed": bool(r.indexed), "current": cur.value if cur else None,
                    "current_source": cur.provenance.source if cur else None, "values": len(r.values)})
    return out


@app.get("/api/rules/{rule_id}")
def rule(rule_id: str, user=Depends(me)) -> dict[str, Any]:
    try:
        return A(user).kb.get(rule_id).model_dump(mode="json")
    except KeyError:
        raise HTTPException(404)


@app.get("/api/calculators")
def calculators(user=Depends(me)) -> dict[str, str]:
    return {k: v[0] for k, v in CALCULATORS.items()}


@app.post("/api/calculators/{name}")
def calculate(name: str, inputs: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    ctx = Ctx(A(user).kb)
    try:
        return {"result": str(run_calc(ctx, name, inputs)), "trace": ctx.sources()}
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")


@app.post("/api/returns/individual")
def compute_individual_return(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    """Compute a Form 1040 from facts and source documents. Nothing is stored."""
    from pydantic import ValidationError

    from ..returns.individual import compute_individual
    from ..returns.model import IndividualReturn

    try:
        r = IndividualReturn.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False))
    try:
        return compute_individual(Ctx(A(user).kb), r).to_dict()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/coverage")
def get_coverage(year: int | None = None, user=Depends(me)) -> dict[str, Any]:
    """What the product actually supports, per form, year and jurisdiction (spec §7)."""
    from .. import coverage

    data = coverage.load()
    caps = [c for c in data["capabilities"] if year is None or year in c.get("years", [])]
    return {"owner": data.get("owner"), "reviewed_at": data.get("reviewed_at"), "states": list(coverage.STATES), "capabilities": caps}


@app.get("/api/staleness")
def staleness(user=Depends(me)) -> list[dict[str, Any]]:
    return staleness_scan(A(user).kb, date.today())


# ------------------------------------------------------------------------------ tax returns

def R(user: dict[str, Any]) -> "Returns":
    from ..returns.store import Returns, Sealer

    ctx = A(user)
    v = ctx.foundry.vault
    # Segregation of duties (preparer != reviewer) is on for real firms; the single-CPA demo turns it off.
    return Returns(ctx.conn, ctx.kb, Sealer(v.keyring, v.firm_id), segregation=not (DEV and user.get("firm_id") == DEV_FIRM))


def _return_for(user: dict[str, Any], rid: str) -> tuple[Any, dict[str, Any]]:
    rs = R(user)
    try:
        r = rs.get(rid)
    except KeyError:
        raise HTTPException(404, "return not found")
    scope(user, r["client_id"])
    return rs, r


def _wf_error(e: Exception) -> HTTPException:
    return HTTPException(409, str(e))


@app.get("/api/clients/{client_id}/returns")
def client_returns(client_id: str, user=Depends(me)) -> list[dict[str, Any]]:
    scope(user, client_id)
    return R(user).for_client(client_id)


@app.post("/api/clients/{client_id}/returns")
def create_return(client_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    scope(user, client_id)
    try:
        rid = R(user).create(client_id, int(body["tax_year"]), user["id"], body.get("inputs"))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"id": rid}


@app.get("/api/returns/{rid}")
def get_return(rid: str, user=Depends(me)) -> dict[str, Any]:
    rs, r = _return_for(user, rid)
    v = rs.latest(rid)
    st = rs.status(rid)
    out = {"return": r, "version": v["version"], "status": st.status, "history": st.history, "summary": json.loads(v["summary"]),
           "allowed": rs.wf.defs["return_1040"].allowed(st.status), "waiting_on": rs.wf.defs["return_1040"].waiting.get(st.status),
           "crosscheck": json.loads(v["crosscheck"]) if v["crosscheck"] else None}
    if user["role"] == "cpa":
        out.update({"inputs": v["inputs"], "provenance": v["provenance"], "result": v["result"]})
    else:  # a client sees the outcome and the status, not working papers
        res = v["result"] or {}
        out["forms"] = {"f1040": res.get("forms", {}).get("f1040", {})}
    return jsonable(out)


@app.put("/api/returns/{rid}/inputs")
def put_return_inputs(rid: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    rs, _ = _return_for(user, rid)
    from pydantic import ValidationError

    try:
        return jsonable(rs.save_inputs(rid, body, user["id"]))
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False))
    except TransitionError as e:
        raise _wf_error(e)


@app.post("/api/returns/{rid}/populate")
def populate_return(rid: str, user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    rs, _ = _return_for(user, rid)
    try:
        return rs.populate_from_documents(rid, user["id"])
    except TransitionError as e:
        raise _wf_error(e)


@app.post("/api/returns/{rid}/confirm")
def confirm_return_amounts(rid: str, body: dict[str, Any] = Body(default={}), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    rs, _ = _return_for(user, rid)
    return {"confirmed": rs.confirm(rid, body.get("paths"), user["id"])}


@app.post("/api/returns/{rid}/compute")
def compute_return(rid: str, body: dict[str, Any] = Body(default={}), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    rs, _ = _return_for(user, rid)
    return jsonable(rs.compute(rid, user["id"], oracle=bool(body.get("crosscheck"))))


@app.post("/api/returns/{rid}/{action}")
def return_action(rid: str, action: str, body: dict[str, Any] = Body(default={}), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    rs, _ = _return_for(user, rid)
    try:
        if action == "submit":
            st = rs.submit_for_review(rid, user["id"], explanation=body.get("explanation", ""))
        elif action == "approve":
            st = rs.approve(rid, user["id"], user["role"])
        elif action == "request-changes":
            st = rs.request_changes(rid, user["id"], user["role"], body.get("note", ""))
        elif action == "request-signature":
            st = rs.request_signature(rid, user["id"], user["role"])
        else:
            raise HTTPException(404, "unknown action")
    except TransitionError as e:
        raise _wf_error(e)
    audit.record(A(user).conn, user["id"], user["role"], f"return.{action}", {"return_id": rid, "status": st.status})
    return {"status": st.status, "history": st.history}


# ------------------------------------------------------------------------------ foundry

@app.get("/api/proposals")
def proposals(status: str | None = None, user=Depends(me)) -> list[dict[str, Any]]:
    if user.get("base_role") != "platform_admin":
        cpa_only(user)
    return [json.loads(p.model_dump_json(exclude={"payload": {"diff"}})) for p in APP.foundry.proposals(status)]


@app.get("/api/proposals/{pid}")
def proposal(pid: str, user=Depends(me)) -> dict[str, Any]:
    if user.get("base_role") != "platform_admin":
        cpa_only(user)
    return json.loads(APP.foundry.load(pid).model_dump_json())


@app.post("/api/proposals/{pid}/{decision}")
def decide(pid: str, decision: str, body: dict[str, Any] = Body(default={}), user=Depends(me)) -> dict[str, Any]:
    ctx = platform_ctx(user)
    note = body.get("note", "")
    try:
        if decision == "approve":
            p = ctx.foundry.adopt(pid, actor=user["id"], note=note)
        elif decision == "reject":
            p = ctx.foundry.reject(pid, actor=user["id"], note=note or "rejected")
        elif decision == "rollback":
            p = ctx.foundry.rollback(pid, actor=user["id"], note=note or "rolled back")
        else:
            raise HTTPException(400, "decision must be approve, reject or rollback")
    except (ValueError, KeyError, PermissionError) as e:
        raise HTTPException(400, str(e))
    ctx.reload()
    return json.loads(p.model_dump_json())


@app.get("/api/agents")
def agents(user=Depends(me)) -> dict[str, Any]:
    from ..foundry.core import AGENT_KINDS

    ctx = APP if user.get("base_role") == "platform_admin" else A(user)
    if user.get("base_role") != "platform_admin":
        cpa_only(user)

    specs = []
    due = {s.id for s in ctx.foundry.due()}
    for s in ctx.foundry.specs():
        last = next((r for r in ctx.foundry.runs(limit=2000) if r["agent"] == s.id), None)
        specs.append({**s.model_dump(), "due": s.id in due, "last_run": last})
    return {"agents": specs, "kinds": sorted(AGENT_KINDS), "runs": ctx.foundry.runs(limit=40)}


@app.post("/api/agents/{agent_id}/run")
def run_agent(agent_id: str, user=Depends(me)) -> dict[str, Any]:
    from ..foundry.core import TENANT_KINDS

    try:
        kind = APP.foundry.spec(agent_id).kind
    except KeyError:
        raise HTTPException(404, "unknown agent")
    if kind in TENANT_KINDS and user.get("base_role") != "platform_admin":
        cpa_only(user)
        ctx = A(user)
    else:
        ctx = platform_ctx(user)
    rec = ctx.foundry.run(agent_id)
    ctx.reload()
    return rec


@app.post("/api/design/{what}")
def design(what: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    ctx = platform_ctx(user)
    fn = {"agent": builders.design_agent, "domain_pack": builders.design_domain_pack, "playbook": builders.design_playbook,
          "automation": builders.design_automation}.get(what)
    if not fn:
        raise HTTPException(404)
    try:
        p = fn(ctx.foundry, body["description"])
    except Exception as e:
        raise HTTPException(503, f"{type(e).__name__}: {e}")
    return json.loads(p.model_dump_json())


@app.get("/api/models")
def models(user=Depends(me)) -> dict[str, Any]:
    ctx = platform_ctx(user)
    rep = ROOT / "state" / "model_scout_report.json"
    inv_path = ROOT / "state" / "model_inventory.json"
    if inv_path.exists():
        from ..ai import inventory

        inv_summary = inventory.summary(json.loads(inv_path.read_text(encoding="utf-8")))
    else:
        inv_summary = None
    return {"inventory": inv_summary, "router": ctx.router.status(), "scout": json.loads(rep.read_text()) if rep.exists() else None}


@app.get("/api/oss")
def oss(user=Depends(me)) -> dict[str, Any]:
    platform_ctx(user)
    rep = ROOT / "state" / "oss_inventory_report.json"
    inv = yaml.safe_load((ROOT / "config" / "oss_inventory.yaml").read_text(encoding="utf-8"))
    return {"inventory": inv, "report": json.loads(rep.read_text()) if rep.exists() else None}


# ------------------------------------------------------------------------------ plugins & hooks

@app.get("/api/plugins")
def plugins(user=Depends(me)) -> dict[str, Any]:
    cat = yaml.safe_load((ROOT / "config" / "connectors_catalog.yaml").read_text(encoding="utf-8"))
    return {"installed": [m.model_dump() for m in discover(ROOT).values()], "catalog": cat["connectors"]}


@app.post("/api/plugins/{plugin_id}/run")
def plugin_run(plugin_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> Any:
    scope(user, body["client_id"])
    try:
        out = run_plugin(A(user).foundry, plugin_id, body["client_id"], body.get("config", {}))
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")
    if isinstance(out["result"], str):
        return PlainTextResponse(out["result"])
    return jsonable(out)


@app.post("/api/plugins/request")
def request_connector(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    """Ask the AI Engineer to build a catalog connector (becomes a work item; result needs approval)."""
    cpa_only(user)
    work = ROOT / "state" / "work"
    work.mkdir(parents=True, exist_ok=True)
    cid = body["connector_id"]
    item = {"title": f"Build connector: {cid}", "status": "open", "source": f"requested by {user['id']}",
            "items": [f"Create plugins/{cid}/plugin.yaml and plugins/{cid}/connector.py following veritas/plugins/builtin.py. "
                      f"Docs: {body.get('docs', '')}. Use only PluginContext capabilities; declare network_domains; "
                      "credentials only from env vars; add tests with recorded fixtures."]}
    (work / f"connector_{cid}.json").write_text(json.dumps(item, indent=2), encoding="utf-8")
    audit.record(A(user).conn, user["id"], "cpa", "connector.requested", {"connector": cid})
    return {"queued": True}


@app.post("/api/hooks/{firm_id}/{client_id}")
async def inbound_hook(firm_id: str, client_id: str, request: Request, x_agentledger_signature: str = Header(default=""),
                       x_veritas_signature: str = Header(default="")) -> dict[str, Any]:
    secret = os.environ.get("VERITAS_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(503, "webhooks disabled (VERITAS_WEBHOOK_SECRET not set)")
    raw = await request.body()
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_agentledger_signature or x_veritas_signature):
        raise HTTPException(401, "bad signature")
    ctx = APP if (DEV and firm_id == DEV_FIRM) else firm_context(firm_id)
    store.get_client(ctx.conn, client_id)
    return jsonable(run_plugin(ctx.foundry, "webhook_inbound", client_id, {"payload": json.loads(raw)}))


# ------------------------------------------------------------------------------ CRM (firm side)

@app.get("/api/crm/pipeline")
def crm_pipeline(user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return crm.pipeline(A(user).conn)


@app.post("/api/crm/engagements")
def crm_new_engagement(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return {"id": crm.add_engagement(A(user).conn, body["client_id"], body["type"], body.get("tax_year"), body.get("owner", user["id"]),
                                     body.get("due_date"), body.get("fee"), body.get("stage", "engaged"), actor=user["id"])}


@app.post("/api/crm/engagements/{eid}/stage")
def crm_stage(eid: int, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    crm.move_engagement(A(user).conn, eid, body["stage"], user["id"])
    return {"ok": True}


@app.get("/api/tasks")
def list_tasks(client_id: str | None = None, user=Depends(me)) -> list[dict[str, Any]]:
    if user["role"] == "client":
        return crm.tasks(A(user).conn, user["client_id"], assignee="client")
    return crm.tasks(A(user).conn, client_id)


@app.post("/api/tasks")
def new_task(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    if body.get("client_id"):
        scope(user, body["client_id"])
    return {"id": crm.create_task(A(user).conn, title=body["title"], detail=body.get("detail", ""),
                                  assignee=body.get("assignee", "cpa"), source=user["id"], client_id=body.get("client_id"),
                                  due=body.get("due"))}


@app.post("/api/tasks/{task_id}/done")
def task_done(task_id: int, body: dict[str, Any] = Body(default={}), user=Depends(me)) -> dict[str, Any]:
    t = one(A(user).conn, "SELECT client_id FROM tasks WHERE id = ?", task_id)
    if not t:
        raise HTTPException(404)
    if t["client_id"]:
        scope(user, t["client_id"])
    try:
        crm.complete_task(A(user).conn, task_id, user["id"], user["role"], body.get("note", ""))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}


@app.get("/api/messages")
def messages(user=Depends(me)) -> list[dict[str, Any]]:
    if user["role"] == "client":
        return rows(A(user).conn, "SELECT * FROM messages WHERE client_id = ? AND status != 'draft' ORDER BY at DESC", user["client_id"])
    return rows(A(user).conn, "SELECT m.*, c.name AS client_name FROM messages m LEFT JOIN clients c ON c.id = m.client_id ORDER BY at DESC LIMIT 200")


@app.post("/api/messages/{mid}/send")
def send(mid: int, user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return crm.send_message(A(user).conn, mid, user["id"])


@app.post("/api/automations/run")
def automations_run(user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return autos.run(A(user).conn, ROOT / "config" / "automations.yaml", foundry=A(user).foundry, deadlines_path=ROOT / "config" / "deadlines.yaml")


@app.get("/api/automations")
def automations_list(user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return {"automations": [a.model_dump() for a in autos.load(ROOT / "config" / "automations.yaml")], "triggers": autos.TRIGGERS,
            "actions": sorted(autos.ACTIONS)}


# ------------------------------------------------------------------------------ CRM (business side)

@app.get("/api/clients/{client_id}/parties")
def biz_parties(client_id: str, user=Depends(me)) -> list[dict[str, Any]]:
    scope(user, client_id)
    return business.parties(A(user).conn, client_id)


@app.post("/api/clients/{client_id}/parties")
def biz_add_party(client_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    return {"id": business.add_party(A(user).conn, client_id, body["kind"], body["name"], email=body.get("email"),
                                     phone=body.get("phone"), entity_type=body.get("entity_type"), tin_last4=body.get("tin_last4"),
                                     w9_on_file=bool(body.get("w9_on_file")), terms_days=int(body.get("terms_days", 30)),
                                     actor=user["id"], role=user["role"])}


@app.post("/api/clients/{client_id}/deals")
def biz_add_deal(client_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    return {"id": business.add_deal(A(user).conn, client_id, body["title"], body.get("value", 0), body.get("party_id"),
                                    body.get("stage", "lead"), body.get("expected_close"), actor=user["id"], role=user["role"])}


@app.post("/api/deals/{deal_id}/stage")
def biz_deal_stage(deal_id: int, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    d = one(A(user).conn, "SELECT client_id FROM deals WHERE id = ?", deal_id)
    scope(user, d["client_id"])
    business.move_deal(A(user).conn, deal_id, body["stage"], user["id"], user["role"])
    return {"ok": True}


@app.post("/api/clients/{client_id}/invoices")
def biz_invoice(client_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    return {"id": business.create_invoice(A(user).conn, client_id, int(body["party_id"]), body["number"], body["amount"],
                                          body.get("description", ""), sales_tax=body.get("sales_tax", 0), actor=user["id"],
                                          role=user["role"])}


@app.post("/api/clients/{client_id}/invoices/{invoice_id}/pay")
def biz_pay(client_id: str, invoice_id: int, user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    return {"entry_id": business.record_payment(A(user).conn, client_id, invoice_id, actor=user["id"], role=user["role"])}


@app.post("/api/clients/{client_id}/vendors/{party_id}/pay")
def biz_pay_vendor(client_id: str, party_id: int, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    return {"entry_id": business.pay_vendor(A(user).conn, client_id, party_id, body["amount"], body["account"], body.get("memo", ""),
                                            tax_treatment=body.get("tax_treatment"), actor=user["id"], role=user["role"])}


# ------------------------------------------------------------------------------ audit

@app.get("/api/audit")
def audit_trail(client_id: str | None = None, user=Depends(me)) -> dict[str, Any]:
    if user["role"] == "client":
        client_id = user["client_id"]
    elif client_id:
        scope(user, client_id)
    return {"events": audit.events(A(user).conn, client_id, limit=300), "verification": audit.verify(A(user).conn)}


@app.get("/api/clients/{client_id}/export/{plugin_id}")
def export(client_id: str, plugin_id: str, dl: str = "", authorization: str = Header(default="")) -> PlainTextResponse:
    user = link_user(dl, f"/api/clients/{client_id}/export/{plugin_id}") if dl else me(authorization)
    scope(user, client_id)
    out = run_plugin(A(user).foundry, plugin_id, client_id, {})
    ext = {"beancount_export": "beancount", "quickbooks_iif_export": "iif", "tax_trial_balance_export": "csv"}.get(plugin_id, "txt")
    return PlainTextResponse(out["result"], headers={"Content-Disposition": f'attachment; filename="{client_id}.{ext}"'})


# ------------------------------------------------------------------------------ background workforce

def _scheduler() -> None:
    while True:
        try:
            for spec in APP.foundry.due():
                APP.foundry.run(spec.id)
            APP.reload()
            for firm in PLATFORM.firms():
                if firm["status"] == "active":
                    ctx = firm_context(firm["id"])
                    for spec in ctx.foundry.due():
                        ctx.foundry.run(spec.id)
            for firm in PLATFORM.firms():
                if firm["status"] == "active":
                    ctx = firm_context(firm["id"])
                    for spec in ctx.foundry.due():
                        ctx.foundry.run(spec.id)
        except Exception as e:  # keep the workforce alive; failures are recorded per run
            print("scheduler:", e)
        time.sleep(int(os.environ.get("VERITAS_TICK_SECONDS", "30")))


if os.environ.get("VERITAS_AGENTS", "1") == "1":
    threading.Thread(target=_scheduler, daemon=True, name="veritas-agents").start()
