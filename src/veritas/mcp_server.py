"""Veritas as an MCP server: any MCP-capable AI (Claude Desktop, Claude Code, other agents)
can use the platform's tools — with the same segregation and audit as the web app.

Scope is fixed at launch, not chosen by the calling AI:
    VERITAS_MCP_ROLE=cpa                      -> firm-wide (CPA) access
    VERITAS_MCP_ROLE=client VERITAS_MCP_CLIENT=<client-id>  -> that client's data only

Run:  veritas mcp            (stdio transport)
"""

from __future__ import annotations

import base64
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .app_context import AppContext

ROLE = os.environ.get("VERITAS_MCP_ROLE", "cpa")
SCOPE = os.environ.get("VERITAS_MCP_CLIENT")
ACTOR = os.environ.get("VERITAS_MCP_ACTOR", f"mcp:{ROLE}")

server = MCPServer(name="veritas", title="Veritas accounting & tax",
                   instructions="Grounded accounting, tax and compliance tools. Numbers come from deterministic engines; "
                                "every answer cites its evidence. Data access is scoped by the server's configured role.")
_app: AppContext | None = None


def app() -> AppContext:
    global _app
    if _app is None:
        _app = AppContext.open(Path(os.environ.get("VERITAS_HOME", Path.cwd())))
    return _app


def _client(client_id: str | None) -> str | None:
    if ROLE == "client":
        if client_id and client_id != SCOPE:
            raise PermissionError("this MCP server is scoped to a single client")
        return SCOPE
    return client_id


@server.tool(description="List clients visible to this server's scope.")
def list_clients() -> list[dict[str, Any]]:
    from .ledger import store

    cs = store.list_clients(app().conn)
    return [{"id": c["id"], "name": c["name"], "kind": c["kind"], "domain": c["domain"]} for c in cs
            if ROLE == "cpa" or c["id"] == SCOPE]


@server.tool(description="Ask a question. Answered only from cited evidence and verified for grounding.")
def ask(question: str, client_id: str | None = None, deep: bool = False) -> dict[str, Any]:
    from .ask.engine import ask_stream

    a = app()
    text, meta = [], {}
    for ev in ask_stream(a.conn, a.kb, a.router, question, client_id=_client(client_id), actor=ACTOR, role=ROLE, deep=deep,
                         brain=a.brain):
        if ev["type"] == "token":
            text.append(ev["text"])
        elif ev["type"] == "escalate":
            text.clear()
        elif ev["type"] in ("verify", "done", "meta"):
            meta.update({k: v for k, v in ev.items() if k != "type"})
    return {"answer": "".join(text), **meta}


@server.tool(description="Get a regulation parameter's value on a date, with its authority.")
def get_rule(rule_id: str, on: str | None = None) -> dict[str, Any]:
    r = app().kb.resolve(rule_id, date.fromisoformat(on) if on else date.today())
    return {"rule_id": r.rule_id, "value": r.value, "effective_from": str(r.effective_from), "source": r.source, "url": r.url}


@server.tool(description="Search the regulation knowledge base.")
def search_rules(query: str) -> list[dict[str, Any]]:
    return [{"id": r.id, "title": r.title, "citation": r.citation} for r in app().kb.search(query)]


@server.tool(description="Run a deterministic calculator (e.g. section_179, standard_deduction) with JSON inputs.")
def calculate(name: str, inputs: dict[str, Any]) -> dict[str, Any]:
    from .calc.engine import Ctx
    from .calc.federal import run_calc

    ctx = Ctx(app().kb)
    out = run_calc(ctx, name, inputs)
    return {"result": str(out), "trace": ctx.sources()}


@server.tool(description="Schedule M-1 book-to-tax bridge for a client and tax year.")
def m1_bridge(client_id: str, tax_year: int) -> dict[str, Any]:
    from .calc.engine import Ctx
    from .ledger import m1

    return m1.compute(app().conn, Ctx(app().kb), _client(client_id), tax_year).as_dict()


@server.tool(description="Integrity findings for a client (open first).")
def findings(client_id: str) -> list[dict[str, Any]]:
    from .integrity.checks import list_findings

    return list_findings(app().conn, _client(client_id))


@server.tool(description="Upcoming compliance deadlines for a client.")
def deadlines(client_id: str, horizon_days: int = 120) -> list[dict[str, Any]]:
    from .crm.core import deadlines as dl

    return dl(app().conn, app().foundry.paths.config / "deadlines.yaml", _client(client_id), horizon_days=horizon_days)


@server.tool(description="Planning opportunities and risks from the CPA playbooks for a client.")
def opportunities(client_id: str) -> list[dict[str, Any]]:
    return app().brain.scan(app().conn, _client(client_id))


@server.tool(description="Submit a document (base64) for autonomous classification and filing.")
def ingest_document(filename: str, content_base64: str, client_id: str | None = None) -> list[dict[str, Any]]:
    from .intake.pipeline import ingest

    a = app()
    return ingest(a.conn, a.router, a.foundry.vault, filename, base64.b64decode(content_base64), channel="mcp",
                  client_hint=_client(client_id), actor=ACTOR)


@server.tool(description="Pending changes proposed by the platform's agents (CPA scope only).")
def pending_proposals() -> list[dict[str, Any]]:
    if ROLE != "cpa":
        raise PermissionError("CPA scope required")
    return [json.loads(p.model_dump_json(include={"id", "kind", "title", "summary", "risk", "created_at"}))
            for p in app().foundry.proposals(status="pending")]


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
