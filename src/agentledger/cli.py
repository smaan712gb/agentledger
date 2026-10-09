"""Command line: `agentledger --help`."""

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
    root = Path(os.environ.get("AGENTLEDGER_HOME", Path.cwd())).resolve()
    from .envfile import load

    load(root)  # local .env for development; real environment variables always win
    return root


def ctx():
    from .app_context import AppContext

    return AppContext.open(home())


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8740, agents: bool = typer.Option(True, help="run the agent workforce in the background"),
          dev: bool = typer.Option(False, "--dev", help="single-firm demo with built-in identities; never with real client data")):
    """Start the web app (and the agent workforce)."""
    import uvicorn

    os.environ["AGENTLEDGER_HOME"] = str(home())
    os.environ["AGENTLEDGER_AGENTS"] = "1" if agents else "0"
    if dev:
        os.environ["AGENTLEDGER_DEV_AUTH"] = "1"
    elif not os.environ.get("AGENTLEDGER_MASTER_KEY"):
        con.print("[red]AGENTLEDGER_MASTER_KEY is not set.[/] Generate one with `agentledger platform new-master-key`, keep it in "
                  "your secrets manager, or run `agentledger serve --dev` for a local demo.")
        raise typer.Exit(2)
    mode = "dev (demo identities)" if dev else "multi-firm"
    con.print(f"[bold]AgentLedger[/] on http://{host}:{port}  ({mode}; agents {'on' if agents else 'off'})")
    uvicorn.run("agentledger.api.app:app", host=host, port=port, log_level="warning")


@app.command()
def demo():
    """Seed a realistic multi-industry demo firm."""
    from .demo import seed

    seed(home())
    con.print("[green]Demo firm seeded.[/] Sample documents are waiting in maildrop/ for the intake agent.")


@app.command()
def mcp():
    """Run AgentLedger as an MCP server over stdio (scope via AGENTLEDGER_MCP_ROLE / AGENTLEDGER_MCP_CLIENT)."""
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
def platform_bootstrap_admin(email: str = typer.Option(...), name: str = typer.Option(...),
                             password_env: str = typer.Option("", "--password-env", metavar="NAME",
                                                              help="read the password from this environment variable (CI, seeds)"),
                             password_stdin: bool = typer.Option(False, "--password-stdin",
                                                                 help="read the password from the first line of standard input")):
    """Create the first platform administrator. They enrol two-step verification at first sign-in. The password is
    asked for interactively; without a terminal, pass --password-env NAME or --password-stdin."""
    from .security.platform import AuthError, Platform

    if password_env:
        password = os.environ.get(password_env, "")
        if not password:
            con.print(f"[red]{password_env} is not set or is empty[/]")
            raise typer.Exit(2)
    elif password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            con.print("[red]no password on standard input[/]")
            raise typer.Exit(2)
    else:
        password = typer.prompt("Password (12+ characters)", hide_input=True, confirmation_prompt=True)
    plat = Platform(home(), dev=os.environ.get("AGENTLEDGER_DEV_AUTH") == "1")
    try:
        plat.bootstrap_admin(email, name, password)
    except AuthError as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    finally:
        plat.close()
    con.print(f"Platform administrator {email} created. Sign in at the web app to enrol two-step verification.")


@platform_app.command("migrate")
def platform_migrate():
    """Create or upgrade the platform schema (firms, users, sessions, wrapped keys) and its runtime role on PostgreSQL.
    A release step run with owner database credentials (AGENTLEDGER_MIGRATION_URL) and without the master key; the
    API then connects as the runtime role (rt_<database>_platform) and holds no owner credentials."""
    from .pg import migration_url
    from .security.platform import migrate_platform, platform_backend

    home()
    if platform_backend() != "postgres":
        con.print("[red]the platform store is on SQLite here (AGENTLEDGER_DATABASE / AGENTLEDGER_PLATFORM_DATABASE); nothing to migrate[/]")
        raise typer.Exit(1)
    if not migration_url():
        con.print("[red]AGENTLEDGER_MIGRATION_URL (owner credentials) is required to migrate the platform store[/]")
        raise typer.Exit(1)
    applied = migrate_platform()
    con.print("applied " + ", ".join(applied) if applied else "platform schema up to date")


@platform_app.command("provision")
def platform_provision():
    """The provisioning worker: create the stores of firms waiting in 'provisioning'. Runs with owner database
    credentials (AGENTLEDGER_MIGRATION_URL) and without the master key, as a release or operations job; the API never
    holds owner credentials."""
    from .pg import migration_url
    from .security.platform import Platform

    if not migration_url():
        con.print("[red]AGENTLEDGER_MIGRATION_URL (owner credentials) is required to provision firm stores[/]")
        raise typer.Exit(1)
    plat = Platform(home(), dev=os.environ.get("AGENTLEDGER_DEV_AUTH") == "1",
                    identity=os.environ.get("AGENTLEDGER_IDENTITY", "local").strip().lower(), need_keys=False)
    try:
        results = plat.provision_pending(by="provisioning-worker")
    finally:
        plat.close()
    for r in results:
        con.print(f"{r['firm']}: {r['status']}" + (f" [red]{r['error']}[/]" if r.get("error") else ""))
    if any(r.get("error") for r in results):
        raise typer.Exit(1)


@platform_app.command("firms")
def platform_firms():
    """List firms on this platform."""
    from .security.platform import Platform

    t = Table(title="Firms")
    for col in ("id", "name", "status", "created_at"):
        t.add_column(col)
    plat = Platform(home(), dev=os.environ.get("AGENTLEDGER_DEV_AUTH") == "1")
    try:
        firms = plat.firms()
    finally:
        plat.close()
    for f in firms:
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


release_app = typer.Typer(help="Release classification and coverage flags", no_args_is_help=True)
app.add_typer(release_app, name="release")


@release_app.command("classify")
def release_classify(base: str = typer.Option("origin/main", help="git ref the change set is compared against")):
    """Decide whether the current change set may ship without a person (exit 0 = auto, 10 = needs review)."""
    import subprocess

    from .release import classify

    paths = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], capture_output=True, text=True, check=True,
                           cwd=home()).stdout.split()
    d = classify(home(), paths)
    print(json.dumps(d.__dict__, indent=2))
    raise typer.Exit(0 if d.verdict == "auto" else 10)


@release_app.command("flags")
def release_flags():
    """Open coverage flags: changes that block filing until the tax-content owner clears them."""
    from . import coverage

    for x in coverage.active_flags(home()):
        con.print(f"[yellow]{x['id']}[/] {x['jurisdiction']} {x.get('form') or ''} {x['years']} - {x['reason']}  ({x['source']})")


@release_app.command("clear-flag")
def release_clear_flag(flag_id: str, by: str = typer.Option(...), note: str = typer.Option(...),
                       evidence: list[str] = typer.Option(..., help="test files, fixture ids, commit hashes")):
    """Tax-content owner: clear a coverage flag once the change is implemented and tested."""
    from . import coverage

    con.print(coverage.clear_flag(home(), flag_id, by=by, note=note, evidence=evidence))


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


# -- workflows ------------------------------------------------------------------------------------

workflows_app = typer.Typer(help="Long-running filings on the local orchestrator (AGENTLEDGER_ORCHESTRATOR=local)", no_args_is_help=True)
app.add_typer(workflows_app, name="workflows")


def _workflow_runner(firm: str | None):
    """The runner over one firm's store: the single-firm store at AGENTLEDGER_HOME, or tenants/<firm> (needs the
    master key, as the API does)."""
    from .returns.filing import provider_from_env
    from .returns.store import Returns, Sealer
    from .workflow.runner import local_runner

    if firm:
        from .app_context import AppContext
        from .security.platform import Platform

        root = home()
        plat = Platform(root, dev=os.environ.get("AGENTLEDGER_DEV_AUTH") == "1",
                        identity=os.environ.get("AGENTLEDGER_IDENTITY", "local").strip().lower())
        c = AppContext.open(root, tenant=plat.tenant_dir(firm), scope="tenant")
        c.conn.set_scope(["*"])
        returns = Returns(c.conn, c.kb, Sealer(plat.keys, firm))
    else:
        c = ctx()
        returns = Returns(c.conn, c.kb)
    return c, local_runner(c.conn, returns, provider_from_env())


@workflows_app.command("tick")
def workflows_tick(firm: str = typer.Option(None, help="firm id (tenants/<firm>); the single-firm store by default")):
    """One relay-and-advance pass: deliver the firm's outbox events, run every step that is due."""
    c, runner = _workflow_runner(firm)
    out = runner.tick(c.conn, firm_id=firm)
    con.print(f"relayed {out['relayed']} event(s), advanced {out['advanced']} instance(s)")


@workflows_app.command("list")
def workflows_list(firm: str = typer.Option(None, help="firm id (tenants/<firm>); the single-firm store by default")):
    """Every workflow instance and where it stands (orchestration state; a return's status is on the return)."""
    c, runner = _workflow_runner(firm)
    t = Table("instance", "flow", "status", "wake at", "waiting for", "error")
    for r in runner.runs.all():
        t.add_row(r.instance_id, r.flow, r.status, r.wake_at or "", r.waiting_for or "", (r.error or "")[:80])
    con.print(t)


@workflows_app.command("signal")
def workflows_signal(instance_id: str, type: str, payload: str = typer.Option("{}", help="the event payload, JSON"),
                     firm: str = typer.Option(None, help="firm id (tenants/<firm>); the single-firm store by default")):
    """Send an event to a waiting instance (for example submission-reconciled after a CPA reconciled by hand)."""
    c, runner = _workflow_runner(firm)
    ok = runner.send_event(instance_id, type, json.loads(payload))
    con.print("signalled" if ok else "[red]no running instance with that id[/]")
    raise typer.Exit(0 if ok else 1)


# -- evidence -------------------------------------------------------------------------------------

evidence_app = typer.Typer(help="Evidence integrity: audit chain anchors in the object store, restore drills (F-13)", no_args_is_help=True)
app.add_typer(evidence_app, name="evidence")


def _platform():
    """The platform with the master key (firm data keys sign the anchors), as the API opens it."""
    from .security.platform import Platform

    return Platform(home(), dev=os.environ.get("AGENTLEDGER_DEV_AUTH") == "1",
                    identity=os.environ.get("AGENTLEDGER_IDENTITY", "local").strip().lower())


@evidence_app.command("anchor")
def evidence_anchor(firm: str = typer.Option(None, help="firm id (tenants/<firm>); the single-firm store by default"),
                    all_firms: bool = typer.Option(False, "--all", help="every active firm (the scheduled job)"),
                    by: str = typer.Option("evidence-anchor", help="who runs it (the audit actor)")):
    """Fix the audit chain head in the firm's object store (anchors/<firm>/<utc date>/<seq>.json, signed with the firm's
    key), after probing that the anchors prefix is locked, and only when the chain moved since the last anchor. Exit 1
    when a chain contradicts its anchor (rewritten or truncated) or a firm's run failed."""
    from .evidence import anchors as anchoring
    from .security.platform import AuthError

    try:
        if all_firms:
            plat = _platform()
            try:
                results = anchoring.anchor_all(plat, actor=by)
            finally:
                plat.close()
        elif firm:
            plat = _platform()
            try:
                anchors, conn = anchoring.for_firm(plat, firm)
                try:
                    results = {firm: anchors.anchor(actor=by)}
                finally:
                    conn.close()
            finally:
                plat.close()
        else:
            results = {"dev": anchoring.Anchors.for_foundry(ctx().foundry).anchor(actor=by)}
    except (AuthError, anchoring.AnchorError) as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(2)
    print(json.dumps(results, indent=1, default=str))
    if any(r.get("mismatch") or r.get("error") for r in results.values()):
        raise typer.Exit(1)


@evidence_app.command("restore-drill")
def evidence_restore_drill(firm: str = typer.Argument(..., help="firm id (tenants/<firm>)"),
                           keep: bool = typer.Option(False, "--keep", help="leave the scratch copy in place for inspection"),
                           by: str = typer.Option("restore-drill", help="who runs it (the audit actor)")):
    """Restore drill (Q33): copy the firm's store (SQLite: the file; PostgreSQL: a fresh schema loaded from the firm's
    with the owner connection), verify the copy's audit chain against the anchors in the object store, check that every
    in-flight workflow instance is present and resumable, record the drill in the audit trail and write the report
    under the firm's tenant directory. Exit 1 when anything failed."""
    from .evidence import drill
    from .security.platform import AuthError

    plat = _platform()
    try:
        report = drill.run(plat, firm, keep=keep, actor=by)
    except (AuthError, drill.DrillError) as e:
        con.print(f"[red]{e}[/]")
        raise typer.Exit(2)
    finally:
        plat.close()
    print(json.dumps(report, indent=1, default=str))
    raise typer.Exit(0 if report["ok"] else 1)


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
