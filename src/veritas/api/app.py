"""HTTP API + web app. Every client-scoped route goes through `scope()`, which is where client
segregation is enforced: a client user can only ever reach their own client's data."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
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

ROOT = Path(os.environ.get("VERITAS_HOME", Path.cwd())).resolve()
WEB = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Veritas", version="0.1.0")
APP: AppContext = AppContext.open(ROOT)


# ------------------------------------------------------------------------------ identity & scope

def users() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load((ROOT / "config" / "users.yaml").read_text(encoding="utf-8"))
    return {u["token"]: u for u in data["users"]}


def me(authorization: str = Header(default="")) -> dict[str, Any]:
    token = authorization.removeprefix("Bearer ").strip()
    u = users().get(token)
    if not u:
        raise HTTPException(401, "unknown user")
    return u


def scope(user: dict[str, Any], client_id: str) -> str:
    if user["role"] == "client" and user.get("client_id") != client_id:
        raise HTTPException(403, "you can only access your own business")
    try:
        store.get_client(APP.conn, client_id)
    except Exception:
        raise HTTPException(404, "client not found")
    return client_id


def cpa_only(user: dict[str, Any]) -> None:
    if user["role"] != "cpa":
        raise HTTPException(403, "CPA access required")


def jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=lambda o: str(o) if isinstance(o, (Decimal, date)) else o.__dict__))


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
    """Demo identity picker (local development only)."""
    return [{k: v for k, v in u.items()} for u in users().values()]


# ------------------------------------------------------------------------------ dashboard

@app.get("/api/dashboard")
def dashboard(user=Depends(me)) -> dict[str, Any]:
    clients = store.list_clients(APP.conn)
    if user["role"] == "client":
        clients = [c for c in clients if c["id"] == user["client_id"]]
    cards = []
    for c in clients:
        f = list_findings(APP.conn, c["id"])
        cards.append({"id": c["id"], "name": c["name"], "kind": c["kind"], "domain": c["domain"],
                      "integrity": integrity_score(f), "open_findings": sum(1 for x in f if x["status"] == "open"),
                      "open_tasks": len(crm.tasks(APP.conn, c["id"])),
                      "docs_this_month": one(APP.conn, "SELECT COUNT(*) n FROM documents WHERE client_id = ? AND received_at >= ?",
                                             c["id"], date.today().replace(day=1).isoformat())["n"]})
    out: dict[str, Any] = {"clients": cards, "today": date.today().isoformat()}
    if user["role"] == "cpa":
        out.update(
            pending=[p.model_dump(include={"id", "kind", "title", "risk", "agent", "created_at"}) for p in APP.foundry.proposals("pending")][:20],
            adopted=[p.model_dump(include={"id", "kind", "title", "risk", "agent", "decision"}) for p in APP.foundry.proposals("adopted")][:10],
            staleness=[a for a in staleness_scan(APP.kb, date.today()) if a["severity"] != "info"],
            review_queue=one(APP.conn, "SELECT COUNT(*) n FROM documents WHERE status = 'needs_review'")["n"],
            tasks=crm.tasks(APP.conn, assignee="cpa")[:15],
            runs=APP.foundry.runs(limit=12),
            ai=APP.router.status(),
            kb={"rules": len(APP.kb.rules), "version": APP.kb.version()},
            ai_usage=rows(APP.conn, "SELECT tier, COUNT(*) calls, SUM(ok) ok FROM ai_usage WHERE substr(at,1,10) >= ? GROUP BY tier",
                          date.today().replace(day=1).isoformat()),
        )
    else:
        out["tasks"] = crm.tasks(APP.conn, user["client_id"], assignee="client")
    return jsonable(out)


# ------------------------------------------------------------------------------ clients

@app.get("/api/clients")
def clients(user=Depends(me)) -> list[dict[str, Any]]:
    cs = store.list_clients(APP.conn)
    return [c for c in cs if user["role"] == "cpa" or c["id"] == user["client_id"]]


@app.post("/api/clients")
def create_client(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    store.add_client(APP.conn, id=body["id"], name=body["name"], kind=body.get("kind", "business"),
                     entity_type=body.get("entity_type"), formed_under=body.get("formed_under", "domestic"),
                     tax_id_last4=body.get("tax_id_last4"), emails=body.get("emails", []), aliases=body.get("aliases", []),
                     consent_7216_at=body.get("consent_7216_at"), domain=body.get("domain", "general"), facts=body.get("facts", {}),
                     actor=user["id"])
    n = domains.onboard(APP.conn, APP.packs, body["id"], body.get("domain", "general"))
    return {"id": body["id"], "accounts_created": n}


@app.get("/api/clients/{client_id}")
def client_detail(client_id: str, year: int | None = None, user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    year = year or date.today().year
    c = store.get_client(APP.conn, client_id)
    out: dict[str, Any] = {"client": c, "year": year,
                           "pack": APP.packs.get(c["domain"]).model_dump(include={"id", "title", "description", "facts"})
                           if c["domain"] in APP.packs.packs else None,
                           "balances": store.balances(APP.conn, client_id, date(year, 1, 1), date(year, 12, 31)),
                           "kpis": domains.kpis(APP.conn, APP.packs, client_id, year),
                           "findings": list_findings(APP.conn, client_id),
                           "documents": rows(APP.conn, "SELECT id, original_name, doc_type, tax_year, status, confidence, vault_path, "
                                                       "summary, classified_by, received_at, channel FROM documents WHERE client_id = ? "
                                                       "ORDER BY received_at DESC LIMIT 100", client_id),
                           "tasks": crm.tasks(APP.conn, client_id),
                           "deadlines": crm.deadlines(APP.conn, ROOT / "config" / "deadlines.yaml", client_id),
                           "opportunities": APP.brain.scan(APP.conn, client_id),
                           "chain": store.verify_chain(APP.conn, client_id)}
    out["integrity"] = integrity_score(out["findings"])
    if c["kind"] == "business":
        try:
            out["m1"] = m1.compute(APP.conn, Ctx(APP.kb), client_id, year).as_dict()
        except Exception as e:
            out["m1"] = {"error": str(e)}
        out["ar"] = business.ar_aging(APP.conn, client_id)
        out["vendors_1099"] = business.vendor_1099_status(APP.conn, APP.kb, client_id, year)
        out["deals"] = business.deal_board(APP.conn, client_id)
    return jsonable(out)


@app.patch("/api/clients/{client_id}/facts")
def update_facts(client_id: str, facts: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    c = store.get_client(APP.conn, client_id)
    merged = {**c["facts"], **facts}
    APP.conn.execute("UPDATE clients SET facts = ? WHERE id = ?", (json.dumps(merged), client_id))
    audit.record(APP.conn, user["id"], user["role"], "client.facts", {"changed": facts}, client_id=client_id)
    return merged


@app.get("/api/clients/{client_id}/entries")
def client_entries(client_id: str, user=Depends(me)) -> list[dict[str, Any]]:
    scope(user, client_id)
    return store.entries(APP.conn, client_id, limit=500)[::-1]


@app.get("/api/clients/{client_id}/templates")
def client_templates(client_id: str, user=Depends(me)) -> list[dict[str, Any]]:
    scope(user, client_id)
    c = store.get_client(APP.conn, client_id)
    return [t.model_dump() for t in APP.packs.templates(c["domain"]).values()]


@app.post("/api/clients/{client_id}/templates/{template_id}")
def post_template(client_id: str, template_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    on = date.fromisoformat(body.pop("date", date.today().isoformat()))
    try:
        entry = domains.post_template(APP.conn, APP.packs, APP.kb, client_id, template_id, body, on, actor=user["id"], role=user["role"])
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"entry_id": entry}


@app.post("/api/clients/{client_id}/entries/{entry_id}/reverse")
def reverse_entry(client_id: str, entry_id: int, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    cpa_only(user)
    return {"entry_id": store.reverse(APP.conn, client_id, entry_id, date.today(), body["reason"], user["id"])}


@app.post("/api/clients/{client_id}/bank/preview")
def bank_preview(client_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> list[dict[str, Any]]:
    scope(user, client_id)
    try:
        txns = bankfeed.parse_csv(body["csv"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return jsonable(bankfeed.suggest(APP.conn, APP.router, client_id, txns))


@app.post("/api/clients/{client_id}/bank/post")
def bank_post(client_id: str, body: list[dict[str, Any]] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    ok = [t for t in body if t.get("account") and not t.get("duplicate")]
    return {"posted": bankfeed.post_confirmed(APP.conn, client_id, ok, user["id"], user["role"])}


@app.post("/api/clients/{client_id}/integrity/run")
def integrity_run(client_id: str, year: int | None = None, user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    f = run_all(APP.conn, APP.kb, client_id, year or date.today().year, packs=APP.packs)
    return {"findings": f, "score": integrity_score(f)}


@app.post("/api/findings/{finding_id}/resolve")
def resolve_finding(finding_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    f = one(APP.conn, "SELECT client_id FROM findings WHERE id = ?", finding_id)
    if not f:
        raise HTTPException(404)
    scope(user, f["client_id"])
    try:
        resolve(APP.conn, finding_id, user["id"], user["role"], body["action"], body.get("note", ""))
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))
    if body.get("save_as_precedent") and user["role"] == "cpa":
        add_precedent(APP.conn, topic=body.get("topic") or "finding resolution", situation=body.get("situation", ""),
                      judgment=body.get("note", ""), citations=body.get("citations", []), author=user["id"], client_id=f["client_id"])
    return {"ok": True}


@app.get("/api/clients/{client_id}/opportunities")
def opportunities(client_id: str, user=Depends(me)) -> list[dict[str, Any]]:
    scope(user, client_id)
    return jsonable(APP.brain.scan(APP.conn, client_id))


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
            for ev in ask_stream(APP.conn, APP.kb, APP.router, body["question"], client_id=client_id, actor=user["id"],
                                 role=user["role"], deep=body.get("deep"), brain=APP.brain):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/precedents")
def new_precedent(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return {"id": add_precedent(APP.conn, topic=body["topic"], situation=body["situation"], judgment=body["judgment"],
                                citations=body.get("citations", []), author=user["id"], domain=body.get("domain"),
                                client_id=body.get("client_id"))}


@app.get("/api/playbooks")
def playbooks(user=Depends(me)) -> list[dict[str, Any]]:
    return jsonable([{**pb.model_dump(), "freshness": APP.brain.freshness(pb)} for pb in APP.brain.playbooks.values()])


# ------------------------------------------------------------------------------ documents

@app.post("/api/documents/upload")
async def upload(file: UploadFile = File(...), client_id: str | None = Form(default=None), user=Depends(me)) -> list[dict[str, Any]]:
    if user["role"] == "client":
        client_id = user["client_id"]
    elif client_id:
        scope(user, client_id)
    data = await file.read()
    return jsonable(ingest(APP.conn, APP.router, ROOT / "vault", file.filename or "upload.bin", data, channel="upload",
                           client_hint=client_id, actor=user["id"]))


@app.get("/api/documents/review")
def review_queue(user=Depends(me)) -> list[dict[str, Any]]:
    cpa_only(user)
    return rows(APP.conn, "SELECT id, original_name, doc_type, tax_year, confidence, summary, sender, received_at, channel, "
                          "classified_by FROM documents WHERE status = 'needs_review' ORDER BY received_at DESC")


@app.post("/api/documents/{doc_id}/assign")
def assign_doc(doc_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return jsonable(assign_document(APP.conn, ROOT / "vault", doc_id, body["client_id"], user["id"]))


@app.get("/api/documents/{doc_id}/file")
def doc_file(doc_id: str, token: str = "") -> FileResponse:
    user = users().get(token)
    if not user:
        raise HTTPException(401)
    d = one(APP.conn, "SELECT * FROM documents WHERE id = ?", doc_id)
    if not d:
        raise HTTPException(404)
    if d["client_id"]:
        scope(user, d["client_id"])
    else:
        cpa_only(user)
    return FileResponse(ROOT / "vault" / d["vault_path"], filename=d["original_name"])


# ------------------------------------------------------------------------------ rules & calculators

@app.get("/api/rules")
def rules(user=Depends(me)) -> list[dict[str, Any]]:
    today = date.today()
    out = []
    for r in sorted(APP.kb.rules.values(), key=lambda r: r.id):
        cur = r.value_on(today)
        out.append({"id": r.id, "title": r.title, "jurisdiction": r.jurisdiction, "category": r.category, "unit": r.unit,
                    "citation": r.citation, "indexed": bool(r.indexed), "current": cur.value if cur else None,
                    "current_source": cur.provenance.source if cur else None, "values": len(r.values)})
    return out


@app.get("/api/rules/{rule_id}")
def rule(rule_id: str, user=Depends(me)) -> dict[str, Any]:
    try:
        return APP.kb.get(rule_id).model_dump(mode="json")
    except KeyError:
        raise HTTPException(404)


@app.get("/api/calculators")
def calculators(user=Depends(me)) -> dict[str, str]:
    return {k: v[0] for k, v in CALCULATORS.items()}


@app.post("/api/calculators/{name}")
def calculate(name: str, inputs: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    ctx = Ctx(APP.kb)
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
        return compute_individual(Ctx(APP.kb), r).to_dict()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/staleness")
def staleness(user=Depends(me)) -> list[dict[str, Any]]:
    return staleness_scan(APP.kb, date.today())


# ------------------------------------------------------------------------------ foundry

@app.get("/api/proposals")
def proposals(status: str | None = None, user=Depends(me)) -> list[dict[str, Any]]:
    cpa_only(user)
    return [json.loads(p.model_dump_json(exclude={"payload": {"diff"}})) for p in APP.foundry.proposals(status)]


@app.get("/api/proposals/{pid}")
def proposal(pid: str, user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return json.loads(APP.foundry.load(pid).model_dump_json())


@app.post("/api/proposals/{pid}/{decision}")
def decide(pid: str, decision: str, body: dict[str, Any] = Body(default={}), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    note = body.get("note", "")
    try:
        if decision == "approve":
            p = APP.foundry.adopt(pid, actor=user["id"], note=note)
        elif decision == "reject":
            p = APP.foundry.reject(pid, actor=user["id"], note=note or "rejected")
        elif decision == "rollback":
            p = APP.foundry.rollback(pid, actor=user["id"], note=note or "rolled back")
        else:
            raise HTTPException(400, "decision must be approve, reject or rollback")
    except (ValueError, KeyError, PermissionError) as e:
        raise HTTPException(400, str(e))
    APP.reload()
    return json.loads(p.model_dump_json())


@app.get("/api/agents")
def agents(user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    from ..foundry.core import AGENT_KINDS

    specs = []
    due = {s.id for s in APP.foundry.due()}
    for s in APP.foundry.specs():
        last = next((r for r in APP.foundry.runs(limit=2000) if r["agent"] == s.id), None)
        specs.append({**s.model_dump(), "due": s.id in due, "last_run": last})
    return {"agents": specs, "kinds": sorted(AGENT_KINDS), "runs": APP.foundry.runs(limit=40)}


@app.post("/api/agents/{agent_id}/run")
def run_agent(agent_id: str, user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    rec = APP.foundry.run(agent_id)
    APP.reload()
    return rec


@app.post("/api/design/{what}")
def design(what: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    fn = {"agent": builders.design_agent, "domain_pack": builders.design_domain_pack, "playbook": builders.design_playbook,
          "automation": builders.design_automation}.get(what)
    if not fn:
        raise HTTPException(404)
    try:
        p = fn(APP.foundry, body["description"])
    except Exception as e:
        raise HTTPException(503, f"{type(e).__name__}: {e}")
    return json.loads(p.model_dump_json())


@app.get("/api/models")
def models(user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    rep = ROOT / "state" / "model_scout_report.json"
    return {"router": APP.router.status(), "scout": json.loads(rep.read_text()) if rep.exists() else None}


@app.get("/api/oss")
def oss(user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
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
        out = run_plugin(APP.foundry, plugin_id, body["client_id"], body.get("config", {}))
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
    audit.record(APP.conn, user["id"], "cpa", "connector.requested", {"connector": cid})
    return {"queued": True}


@app.post("/api/hooks/{client_id}")
async def inbound_hook(client_id: str, request: Request, x_veritas_signature: str = Header(default="")) -> dict[str, Any]:
    secret = os.environ.get("VERITAS_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(503, "webhooks disabled (VERITAS_WEBHOOK_SECRET not set)")
    raw = await request.body()
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_veritas_signature):
        raise HTTPException(401, "bad signature")
    store.get_client(APP.conn, client_id)
    return jsonable(run_plugin(APP.foundry, "webhook_inbound", client_id, {"payload": json.loads(raw)}))


# ------------------------------------------------------------------------------ CRM (firm side)

@app.get("/api/crm/pipeline")
def crm_pipeline(user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return crm.pipeline(APP.conn)


@app.post("/api/crm/engagements")
def crm_new_engagement(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return {"id": crm.add_engagement(APP.conn, body["client_id"], body["type"], body.get("tax_year"), body.get("owner", user["id"]),
                                     body.get("due_date"), body.get("fee"), body.get("stage", "engaged"), actor=user["id"])}


@app.post("/api/crm/engagements/{eid}/stage")
def crm_stage(eid: int, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    crm.move_engagement(APP.conn, eid, body["stage"], user["id"])
    return {"ok": True}


@app.get("/api/tasks")
def list_tasks(client_id: str | None = None, user=Depends(me)) -> list[dict[str, Any]]:
    if user["role"] == "client":
        return crm.tasks(APP.conn, user["client_id"], assignee="client")
    return crm.tasks(APP.conn, client_id)


@app.post("/api/tasks")
def new_task(body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    if body.get("client_id"):
        scope(user, body["client_id"])
    return {"id": crm.create_task(APP.conn, title=body["title"], detail=body.get("detail", ""),
                                  assignee=body.get("assignee", "cpa"), source=user["id"], client_id=body.get("client_id"),
                                  due=body.get("due"))}


@app.post("/api/tasks/{task_id}/done")
def task_done(task_id: int, body: dict[str, Any] = Body(default={}), user=Depends(me)) -> dict[str, Any]:
    t = one(APP.conn, "SELECT client_id FROM tasks WHERE id = ?", task_id)
    if not t:
        raise HTTPException(404)
    if t["client_id"]:
        scope(user, t["client_id"])
    try:
        crm.complete_task(APP.conn, task_id, user["id"], user["role"], body.get("note", ""))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}


@app.get("/api/messages")
def messages(user=Depends(me)) -> list[dict[str, Any]]:
    if user["role"] == "client":
        return rows(APP.conn, "SELECT * FROM messages WHERE client_id = ? AND status != 'draft' ORDER BY at DESC", user["client_id"])
    return rows(APP.conn, "SELECT m.*, c.name AS client_name FROM messages m LEFT JOIN clients c ON c.id = m.client_id ORDER BY at DESC LIMIT 200")


@app.post("/api/messages/{mid}/send")
def send(mid: int, user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return crm.send_message(APP.conn, mid, user["id"])


@app.post("/api/automations/run")
def automations_run(user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return autos.run(APP.conn, ROOT / "config" / "automations.yaml", foundry=APP.foundry, deadlines_path=ROOT / "config" / "deadlines.yaml")


@app.get("/api/automations")
def automations_list(user=Depends(me)) -> dict[str, Any]:
    cpa_only(user)
    return {"automations": [a.model_dump() for a in autos.load(ROOT / "config" / "automations.yaml")], "triggers": autos.TRIGGERS,
            "actions": sorted(autos.ACTIONS)}


# ------------------------------------------------------------------------------ CRM (business side)

@app.get("/api/clients/{client_id}/parties")
def biz_parties(client_id: str, user=Depends(me)) -> list[dict[str, Any]]:
    scope(user, client_id)
    return business.parties(APP.conn, client_id)


@app.post("/api/clients/{client_id}/parties")
def biz_add_party(client_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    return {"id": business.add_party(APP.conn, client_id, body["kind"], body["name"], email=body.get("email"),
                                     phone=body.get("phone"), entity_type=body.get("entity_type"), tin_last4=body.get("tin_last4"),
                                     w9_on_file=bool(body.get("w9_on_file")), terms_days=int(body.get("terms_days", 30)),
                                     actor=user["id"], role=user["role"])}


@app.post("/api/clients/{client_id}/deals")
def biz_add_deal(client_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    return {"id": business.add_deal(APP.conn, client_id, body["title"], body.get("value", 0), body.get("party_id"),
                                    body.get("stage", "lead"), body.get("expected_close"), actor=user["id"], role=user["role"])}


@app.post("/api/deals/{deal_id}/stage")
def biz_deal_stage(deal_id: int, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    d = one(APP.conn, "SELECT client_id FROM deals WHERE id = ?", deal_id)
    scope(user, d["client_id"])
    business.move_deal(APP.conn, deal_id, body["stage"], user["id"], user["role"])
    return {"ok": True}


@app.post("/api/clients/{client_id}/invoices")
def biz_invoice(client_id: str, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    return {"id": business.create_invoice(APP.conn, client_id, int(body["party_id"]), body["number"], body["amount"],
                                          body.get("description", ""), sales_tax=body.get("sales_tax", 0), actor=user["id"],
                                          role=user["role"])}


@app.post("/api/clients/{client_id}/invoices/{invoice_id}/pay")
def biz_pay(client_id: str, invoice_id: int, user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    return {"entry_id": business.record_payment(APP.conn, client_id, invoice_id, actor=user["id"], role=user["role"])}


@app.post("/api/clients/{client_id}/vendors/{party_id}/pay")
def biz_pay_vendor(client_id: str, party_id: int, body: dict[str, Any] = Body(...), user=Depends(me)) -> dict[str, Any]:
    scope(user, client_id)
    return {"entry_id": business.pay_vendor(APP.conn, client_id, party_id, body["amount"], body["account"], body.get("memo", ""),
                                            tax_treatment=body.get("tax_treatment"), actor=user["id"], role=user["role"])}


# ------------------------------------------------------------------------------ audit

@app.get("/api/audit")
def audit_trail(client_id: str | None = None, user=Depends(me)) -> dict[str, Any]:
    if user["role"] == "client":
        client_id = user["client_id"]
    elif client_id:
        scope(user, client_id)
    return {"events": audit.events(APP.conn, client_id, limit=300), "verification": audit.verify(APP.conn)}


@app.get("/api/clients/{client_id}/export/{plugin_id}")
def export(client_id: str, plugin_id: str, token: str = "") -> PlainTextResponse:
    user = users().get(token)
    if not user:
        raise HTTPException(401)
    scope(user, client_id)
    out = run_plugin(APP.foundry, plugin_id, client_id, {})
    ext = {"beancount_export": "beancount", "quickbooks_iif_export": "iif", "tax_trial_balance_export": "csv"}.get(plugin_id, "txt")
    return PlainTextResponse(out["result"], headers={"Content-Disposition": f'attachment; filename="{client_id}.{ext}"'})


# ------------------------------------------------------------------------------ background workforce

def _scheduler() -> None:
    while True:
        try:
            for spec in APP.foundry.due():
                APP.foundry.run(spec.id)
            APP.reload()
        except Exception as e:  # keep the workforce alive; failures are recorded per run
            print("scheduler:", e)
        time.sleep(int(os.environ.get("VERITAS_TICK_SECONDS", "30")))


if os.environ.get("VERITAS_AGENTS", "1") == "1":
    threading.Thread(target=_scheduler, daemon=True, name="veritas-agents").start()
