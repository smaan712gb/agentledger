"""Command line: `veritas --help`."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="AgentLedger — autonomous accounting, ledger and tax platform", no_args_is_help=True)
agents_app = typer.Typer(help="The agent workforce", no_args_is_help=True)
rules_app = typer.Typer(help="Regulation knowledge base", no_args_is_help=True)
prop_app = typer.Typer(help="Changes proposed by agents", no_args_is_help=True)
app.add_typer(agents_app, name="agents")
app.add_typer(rules_app, name="rules")
app.add_typer(prop_app, name="proposals")
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
con = Console()


def home() -> Path:
    return Path(os.environ.get("VERITAS_HOME", Path.cwd())).resolve()


def ctx():
    from .app_context import AppContext

    return AppContext.open(home())


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8740, agents: bool = typer.Option(True, help="run the agent workforce in the background"),
          dev: bool = typer.Option(False, "--dev", help="single-firm demo with built-in identities; never with real client data")):
    """Start the web app (and the agent workforce)."""
    import uvicorn

    os.environ["VERITAS_HOME"] = str(home())
    os.environ["VERITAS_AGENTS"] = "1" if agents else "0"
    if dev:
        os.environ["VERITAS_DEV_AUTH"] = "1"
    elif not os.environ.get("VERITAS_MASTER_KEY"):
        con.print("[red]VERITAS_MASTER_KEY is not set.[/] Generate one with `veritas platform new-master-key`, keep it in "
                  "your secrets manager, or run `veritas serve --dev` for a local demo.")
        raise typer.Exit(2)
    mode = "dev (demo identities)" if dev else "multi-firm"
    con.print(f"[bold]AgentLedger[/] on http://{host}:{port}  ({mode}; agents {'on' if agents else 'off'})")
    uvicorn.run("veritas.api.app:app", host=host, port=port, log_level="warning")


@app.command()
def demo():
    """Seed a realistic multi-industry demo firm."""
    from .demo import seed

    seed(home())
    con.print("[green]Demo firm seeded.[/] Sample documents are waiting in maildrop/ for the intake agent.")


@app.command()
def mcp():
    """Run AgentLedger as an MCP server over stdio (scope via VERITAS_MCP_ROLE / VERITAS_MCP_CLIENT)."""
    from .mcp_server import main

    main()


@app.command()
def ingest(path: Path, client: str = typer.Option(None, help="client id, if known")):
    """Ingest any document (or folder) through the autonomous intake pipeline."""
    from .intake.pipeline import ingest as run

    c = ctx()
    files = [p for p in path.rglob("*") if p.is_file()] if path.is_dir() else [path]
    for f in files:
        for d in run(c.conn, c.router, c.foundry.vault, f.name, f.read_bytes(), channel="cli", client_hint=client):
            con.print(f"{d['name']}: [bold]{d['doc_type']}[/] -> {d['status']} {d.get('vault_path', '')} ({d.get('match', '')})")


@app.command()
def regdoc(url: str, title: str = typer.Option(..., help="document title"), file: Path = typer.Option(None, help="local copy (PDF/HTML/TXT)")):
    """Push a specific regulatory document (e.g. a new Rev. Proc.) through RegWatch."""
    from .foundry.agents.regwatch import ingest_document
    from .regwatch.documents import fetch_text, pdf_to_text

    c = ctx()
    if file:
        text = pdf_to_text(file.read_bytes()) if file.suffix.lower() == ".pdf" else file.read_text(encoding="utf-8", errors="replace")
    else:
        text = fetch_text(url)
    res = ingest_document(c.foundry, title, url, text)
    con.print_json(json.dumps({"proposals": res.proposals, "log": res.log, "alerts": res.alerts}, default=str))


@app.command()
def golden():
    """Run the golden regression scenarios against the current knowledge base."""
    from .foundry.verify import load_golden, run_golden

    c = ctx()
    res = run_golden(c.kb, load_golden(c.root / "golden" / "scenarios.yaml"))
    t = Table("scenario", "ok", "got", "expected")
    for k, v in res.items():
        t.add_row(k, "[green]✓[/]" if v["ok"] else "[red]✗[/]", str(v["got"]), str(v["expect"]))
    con.print(t)
    raise typer.Exit(0 if all(v["ok"] for v in res.values()) else 1)


platform_app = typer.Typer(help="Multi-firm platform administration", no_args_is_help=True)
app.add_typer(platform_app, name="platform")


@platform_app.command("new-master-key")
def platform_new_master_key():
    """Print a fresh 256-bit master key (store it in a secrets manager; losing it loses all firm data)."""
    import base64
    import secrets

    print(base64.b64encode(secrets.token_bytes(32)).decode())


@platform_app.command("bootstrap-admin")
def platform_bootstrap_admin(email: str = typer.Option(...), name: str = typer.Option(...)):
    """Create the first platform administrator. They enrol two-step verification at first sign-in."""
    from .security.platform import AuthError, Platform

    password = typer.prompt("Password (12+ characters)", hide_input=True, confirmation_prompt=True)
    try:
        Platform(home(), dev=os.environ.get("VERITAS_DEV_AUTH") == "1").bootstrap_admin(email, name, password)
    except AuthError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    con.print(f"Platform administrator {email} created. Sign in at the web app to enrol two-step verification.")


@platform_app.command("firms")
def platform_firms():
    """List firms on this platform."""
    from .security.platform import Platform

    t = Table(title="Firms")
    for col in ("id", "name", "status", "created_at"):
        t.add_column(col)
    for f in Platform(home(), dev=os.environ.get("VERITAS_DEV_AUTH") == "1").firms():
        t.add_row(f["id"], f["name"], f["status"], f["created_at"])
    con.print(t)


models_app = typer.Typer(help="Open-model inventory and routing", no_args_is_help=True)
app.add_typer(models_app, name="models")


@models_app.command("inventory")
def models_inventory(refresh: bool = typer.Option(True, help="fetch the sources now"),
                     top: int = typer.Option(15, help="show the N cheapest text models with structured output")):
    """Refresh and show the live inventory of open models across Workers AI, NVIDIA, Hugging Face and Ollama."""
    from .ai import inventory

    path = home() / "state" / "model_inventory.json"
    inv = inventory.refresh(path) if refresh else json.loads(path.read_text(encoding="utf-8"))
    s = inventory.summary(inv)
    con.print(f"[bold]{s['models']} models[/], {s['deployable']} deployable; new {s['new']}, retired {s['retired']}")
    for k, v in s["sources"].items():
        con.print(f"  {k:11s} {v}")
    t = Table(title=f"Cheapest {top} routes with structured output")
    t.add_column("model", no_wrap=True)
    for col in ("host", "$ in / out per M", "context", "first token ms"):
        t.add_column(col)
    rows = []
    for m in inv["models"].values():
        for r in m["routes"]:
            if r.get("price_in") is not None and r.get("structured_output"):
                rows.append((r["price_in"] + (r["price_out"] or 0), m["id"], r))
    for _, mid, r in sorted(rows, key=lambda x: (x[0], x[1]))[:top]:
        t.add_row(mid, r["host"], f"{r['price_in']:g} / {(r['price_out'] or 0):g}", str(r.get("context") or ""), str(int(r["first_token_ms"])) if r.get("first_token_ms") else "")
    con.print(t)


@app.command("return")
def compute_return(path: Path, as_json: bool = typer.Option(False, "--json", help="print the full return as JSON")):
    """Compute an individual return (Form 1040) from a JSON or YAML file of facts and documents."""
    import yaml

    from .calc.engine import Ctx
    from .returns.individual import compute_individual
    from .returns.model import IndividualReturn

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    res = compute_individual(Ctx(ctx().kb), IndividualReturn.model_validate(data))
    if as_json:
        print(json.dumps(res.to_dict(), indent=2, default=str))
        return
    con = Console()
    t = Table(title=f"Form 1040 ({res.tax_year}), {res.filing_status}")
    t.add_column("Line")
    t.add_column("Amount", justify="right")
    t.add_column("How")
    notes = res.sheets.notes.get("f1040", {})
    for line, value in res.forms.get("f1040", {}).items():
        if value:
            t.add_row(line, f"{value:,}", notes.get(line, ""))
    con.print(t)
    for d in res.diagnostics:
        style = {"error": "red", "warning": "yellow"}.get(d.severity, "dim")
        con.print(f"[{style}]{d.severity.upper()}[/] {d.code}: {d.message}")
    if res.blocking:
        raise typer.Exit(2)


@app.command()
def stale():
    """What's due, overdue or sunsetting in the regulation knowledge base."""
    from .foundry.agents.staleness import scan

    t = Table("severity", "rule", "status", "year", "expected by", "days")
    for a in scan(ctx().kb, date.today()):
        t.add_row(a["severity"], a["rule_id"], a["status"], str(a.get("tax_year", "")), a["expected_by"], str(a["days"]))
    con.print(t)


# -- agents ---------------------------------------------------------------------------------------

@agents_app.command("list")
def agents_list():
    c = ctx()
    due = {s.id for s in c.foundry.due()}
    t = Table("id", "kind", "every", "enabled", "due")
    for s in c.foundry.specs():
        t.add_row(s.id, s.kind, f"{s.every_hours}h", str(s.enabled), "yes" if s.id in due else "")
    con.print(t)


@agents_app.command("run")
def agents_run(agent_id: str):
    rec = ctx().foundry.run(agent_id)
    con.print_json(json.dumps(rec, default=str))


@agents_app.command("due")
def agents_due():
    """Run every agent that is due (this is what cron / GitHub Actions calls)."""
    for rec in ctx().foundry.run_due():
        con.print(f"{rec['agent']}: {'ok' if rec['ok'] else rec['error']} · {len(rec['proposals'])} proposal(s) · {len(rec['alerts'])} alert(s)")


@agents_app.command("daemon")
def agents_daemon(tick: int = 30):
    """Run the workforce continuously."""
    import time

    c = ctx()
    while True:
        for rec in c.foundry.run_due():
            con.print(f"{rec['started_at'][:19]} {rec['agent']}: {'ok' if rec['ok'] else rec['error']}")
        c.reload()
        time.sleep(tick)


@agents_app.command("design")
def agents_design(what: str = typer.Argument(..., help="agent | domain_pack | playbook | automation"), description: str = typer.Argument(...)):
    """Have the Architect draft something new from plain English (lands as a proposal)."""
    from .foundry.agents import builders

    fn = {"agent": builders.design_agent, "domain_pack": builders.design_domain_pack, "playbook": builders.design_playbook,
          "automation": builders.design_automation}[what]
    p = fn(ctx().foundry, description)
    con.print(f"proposal [bold]{p.id}[/]: {p.title} · risk {p.risk} · verified {p.verified}")


# -- rules ----------------------------------------------------------------------------------------

@rules_app.command("list")
def rules_list():
    kb = ctx().kb
    t = Table("id", "title", "today")
    for r in sorted(kb.rules.values(), key=lambda r: r.id):
        v = r.value_on(date.today())
        t.add_row(r.id, r.title, json.dumps(v.value) if v else "—")
    con.print(t)


@rules_app.command("show")
def rules_show(rule_id: str):
    con.print_json(ctx().kb.get(rule_id).model_dump_json())


@rules_app.command("lint")
def rules_lint():
    """Validate every rule file (types, bounds, non-overlapping timelines)."""
    kb = ctx().kb
    con.print(f"[green]{len(kb.rules)} rules valid[/] · version {kb.version()}")


# -- proposals ------------------------------------------------------------------------------------

@prop_app.command("list")
def prop_list(status: str = typer.Option(None)):
    t = Table("id", "kind", "status", "risk", "verified", "title")
    for p in ctx().foundry.proposals(status):
        t.add_row(p.id, p.kind, p.status, p.risk, "✓" if p.verified else "✗", p.title[:80])
    con.print(t)


@prop_app.command("show")
def prop_show(pid: str):
    con.print_json(ctx().foundry.load(pid).model_dump_json())


@prop_app.command("approve")
def prop_approve(pid: str, by: str = typer.Option(..., help="who is approving"), note: str = ""):
    p = ctx().foundry.adopt(pid, actor=by, note=note)
    con.print(f"[green]adopted[/] {p.id}: {p.title}")


@prop_app.command("reject")
def prop_reject(pid: str, by: str = typer.Option(...), note: str = typer.Option(...)):
    ctx().foundry.reject(pid, actor=by, note=note)
    con.print("rejected")


@prop_app.command("rollback")
def prop_rollback(pid: str, by: str = typer.Option(...), note: str = typer.Option(...)):
    ctx().foundry.rollback(pid, actor=by, note=note)
    con.print("rolled back")


if __name__ == "__main__":
    app()
