"""The builders: the AI Engineer, the AI Researcher and the Architect.

Engineer   - turns work items (law that needs new code, approved dependency upgrades, new
             connectors) into code changes. It drives an external coding agent (open source
             first, e.g. Aider on a local model; Claude Code for hard cases) inside an isolated
             git worktree, then runs deterministic gates: protected paths untouched, diff size,
             new tests present, full test suite and golden regression green.
Researcher - studies what changed (adopted rules, overdue parameters, flagged documents, new
             models and repos), writes briefs, and proposes golden scenarios and playbook
             updates. A golden scenario is only auto-added if the engine already reproduces
             the authority's number.
Architect  - designs new agents, domain packs, playbooks and automations from plain English,
             verified mechanically before a human approves.
"""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ...ai.router import Unavailable
from ..core import AgentResult, AgentSpec, Check, Foundry, Proposal, agent_kind, applier
from ..verify import load_golden, run_golden


# ============================================================================ engineer

def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=check)


# The coding agent runs AI-written code. It gets a minimal environment: no platform secrets, no cloud credentials,
# no model keys except those explicitly allowed in config/foundry.yaml (engineer.pass_env). A scrubbed environment is
# not a sandbox: in CI the Engineer runs in a container without repository secrets (backlog F-03).
_SAFE_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
             "LANG", "LC_ALL", "PYTHONIOENCODING", "VIRTUAL_ENV")


def sandbox_env(allow: list[str] | None = None) -> dict[str, str]:
    import os

    keep = set(_SAFE_ENV) | set(allow or [])
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["AGENTLEDGER_AGENTS"] = "0"
    return env


def protected(path: str, patterns: list[str]) -> bool:
    return any(path.startswith(p.rstrip("*")) if p.endswith("/") else fnmatch.fnmatch(path, p) for p in patterns)


def open_work_items(f: Foundry) -> list[tuple[Path, dict[str, Any]]]:
    work = f.paths.state / "work"
    out = []
    for p in sorted(work.glob("*.json")) if work.exists() else []:
        item = json.loads(p.read_text(encoding="utf-8"))
        if item.get("status") == "open":
            out.append((p, item))
    return out


@agent_kind("engineer")
def engineer(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    cfg = f.policy.get("engineer", {})
    items = open_work_items(f)[: int(spec.params.get("max_items", 1))]
    if not items:
        res.log.append("no open work items")
        return
    if _git(f.paths.root, "rev-parse", "HEAD", check=False).returncode != 0:
        res.log.append("repository has no commits yet; the engineer needs a base commit to branch from")
        return
    agents = [c for c in cfg.get("commands", []) if shutil.which(c[0])]
    if not agents:
        res.alerts.append({"type": "engineer_unavailable", "note": "install a coding agent: `pip install aider-chat` (open source) "
                                                                  "or Claude Code; see config/foundry.yaml"})
        return
    for path, item in items:
        branch = f"agentledger/auto-{path.stem}-{date.today().strftime('%Y%m%d')}"
        wt = Path(tempfile.mkdtemp(prefix="agentledger-wt-"))
        _git(f.paths.root, "worktree", "add", "-b", branch, str(wt))
        try:
            prompt = (f"You are maintaining the AgentLedger accounting/tax platform. Work item: {item['title']}\n"
                      + "\n".join(f"- {x}" for x in item["items"])
                      + f"\nSource: {item.get('source')}\n\nRules: prefer changing data (rules/, domains/, playbooks/) over code; "
                        "add or update tests under tests/ for every behaviour change; never modify these protected paths: "
                      + ", ".join(f.policy.get("protected_paths", [])) + ". Keep the change minimal.")
            used = None
            for cmd in agents:
                argv = [a.replace("{prompt}", prompt) for a in cmd]
                r = subprocess.run(argv, cwd=wt, capture_output=True, text=True, timeout=int(cfg.get("timeout_s", 1800)),
                                   env=sandbox_env(cfg.get("pass_env")))
                used = cmd[0]
                if r.returncode == 0 and _git(wt, "status", "--porcelain").stdout.strip():
                    break
            _git(wt, "add", "-A")
            diff = _git(wt, "diff", "--cached").stdout
            files = [l for l in _git(wt, "diff", "--cached", "--name-only").stdout.splitlines() if l]
            if not files:
                res.log.append(f"{path.stem}: coding agent produced no change")
                continue
            touched = [x for x in files if protected(x, f.policy.get("protected_paths", []))]
            lines = sum(1 for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))
            tests = subprocess.run(cfg.get("test_command", ["python", "-m", "pytest", "-q"]), cwd=wt, capture_output=True, text=True,
                                   timeout=1800, env=sandbox_env())
            from ...kb.store import KnowledgeBase

            golden = run_golden(KnowledgeBase(wt / "rules"), load_golden(wt / "golden" / "scenarios.yaml"))
            golden_fail = [k for k, v in golden.items() if not v["ok"]]
            checks = [
                Check(name="protected_paths_untouched", ok=not touched, detail=", ".join(touched) or "ok"),
                Check(name="diff_size", ok=lines <= int(cfg.get("max_diff_lines", 600)), detail=f"{lines} changed lines"),
                Check(name="tests_added_or_changed", ok=any(x.startswith("tests/") for x in files), detail=", ".join(files)),
                Check(name="test_suite", ok=tests.returncode == 0, detail=tests.stdout.strip().splitlines()[-1] if tests.stdout.strip() else tests.stderr[-300:]),
                Check(name="golden_regression", ok=not golden_fail, detail=", ".join(golden_fail) or f"{len(golden)} scenarios pass"),
            ]
            _git(wt, "-c", "user.name=AgentLedger Engineer", "-c", "user.email=engineer@agentledger.local", "commit", "-q", "-m",
                 f"{item['title']}\n\nAutomated change by the AgentLedger AI Engineer ({used}).")
            p = Proposal(kind="code_change", agent=spec.id, title=item["title"],
                         summary=f"Code change written by {used} in branch {branch}: {len(files)} file(s), {lines} line(s).",
                         risk="critical" if touched else ("low" if all(c.ok for c in checks) else "high"),
                         risk_reasons=(["touches protected guardrail code"] if touched else []) or ["all gates passed"],
                         payload={"branch": branch, "files": files, "diff": diff[:60000], "touches_protected": bool(touched),
                                  "work_item": path.name, "coding_agent": used, "fingerprint": f"code:{path.stem}"},
                         checks=checks)
            res.proposals.append(f.submit(p).id)
            item["status"] = "proposed"
            item["proposal"] = p.id
            path.write_text(json.dumps(item, indent=2), encoding="utf-8")
        finally:
            _git(f.paths.root, "worktree", "remove", "--force", str(wt), check=False)


@applier("code_change", undo=lambda f, p: _git(f.paths.root, "revert", "--no-edit", p.undo["merge_commit"]))
def apply_code_change(f: Foundry, p: Proposal) -> dict[str, Any]:
    if p.payload.get("touches_protected") and (p.decision or {}).get("by") == "auto-policy":
        raise PermissionError("protected-path changes need a human")
    _git(f.paths.root, "merge", "--no-ff", "-m", f"Merge {p.payload['branch']} (proposal {p.id})", p.payload["branch"])
    head = _git(f.paths.root, "rev-parse", "HEAD").stdout.strip()
    return {"merge_commit": head}


# ============================================================================ researcher

class Scenario(BaseModel):
    id: str
    calc: str
    inputs_json: str = Field(description="JSON object of calculator inputs")
    expect_json: str = Field(description="JSON value the authority states")
    source: str


class PlaybookIdea(BaseModel):
    title: str
    why_now: str
    citations: list[str]


class Brief(BaseModel):
    headline: str
    what_changed: list[str]
    who_is_affected: list[str]
    actions_for_cpas: list[str]
    golden_scenarios: list[Scenario]
    playbook_ideas: list[PlaybookIdea]
    engineering_needs: list[str]


@agent_kind("researcher")
def researcher(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    from ...calc.federal import CALCULATORS
    from .staleness import scan

    since = spec.params.get("since_days", 14)
    recent = [p for p in f.proposals() if p.kind == "rule_change" and p.created_at[:10] >= str(date.fromordinal(date.today().toordinal() - since))]
    alerts = [a for a in scan(f.kb, date.today()) if a["severity"] in ("critical", "high", "medium")]
    scout = {}
    for name in ("model_scout_report.json", "oss_inventory_report.json"):
        p = f.paths.state / name
        if p.exists():
            scout[name] = json.loads(p.read_text(encoding="utf-8"))
    if not recent and not alerts and not scout:
        res.log.append("nothing new to research")
        return
    context = {
        "rule_changes": [{"title": p.title, "status": p.status, "summary": p.summary, "source": p.source, "changes": p.payload.get("changes"),
                          "code_change_needed": p.payload.get("code_change_needed")} for p in recent[:20]],
        "parameter_alerts": alerts[:20],
        "calculators": {k: v[0] for k, v in CALCULATORS.items()},
        "technology": {k: (v.get("candidates") or v.get("discovered") or [])[:10] for k, v in scout.items()},
    }
    system = ("You are the research lead of an AI-native CPA platform. Write a brief for practitioners. Use only the facts given. "
              "Propose golden test scenarios only for values explicitly present in the rule changes, using the listed calculators.")
    role = "reason" if f.router.frontier_allowed() else "answer"
    try:
        brief, by = f.router.structured(role, system=system, user=json.dumps(context, default=str)[:150000], schema=Brief, effort="high",
                                        data_class="public")
    except Unavailable as e:
        res.log.append(f"research unavailable: {e}")
        return
    f.paths.research.mkdir(exist_ok=True)
    md = [f"# {brief.headline}", f"_{date.today().isoformat()} — AgentLedger Researcher ({by})_", "", "## What changed",
          *[f"- {x}" for x in brief.what_changed], "", "## Who is affected", *[f"- {x}" for x in brief.who_is_affected], "",
          "## Actions for CPAs", *[f"- {x}" for x in brief.actions_for_cpas], "", "## Playbook ideas",
          *[f"- **{x.title}** — {x.why_now} ({'; '.join(x.citations)})" for x in brief.playbook_ideas], "", "## Engineering needs",
          *[f"- {x}" for x in brief.engineering_needs]]
    out = f.paths.research / f"{date.today().isoformat()}-brief.md"
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    res.stats["brief"] = str(out.name)

    from ...calc.engine import Ctx
    from ...calc.federal import run_calc

    existing = {s["id"] for s in load_golden(f.paths.golden / "scenarios.yaml")}
    for s in brief.golden_scenarios:
        if s.id in existing or s.calc not in CALCULATORS:
            continue
        try:
            inputs, expect = json.loads(s.inputs_json), json.loads(s.expect_json)
            got = run_calc(Ctx(f.kb), s.calc, inputs)
            reproduced = str(got).lower() == str(expect).lower() or float(got) == float(expect)
        except Exception as e:
            reproduced, got = False, f"{type(e).__name__}: {e}"
        p = Proposal(kind="golden_scenario", agent=spec.id, title=f"Regression test {s.id}",
                     summary=f"{s.calc}({s.inputs_json}) should equal {s.expect_json} per {s.source}. Engine returns {got}.",
                     risk="low" if reproduced else "high",
                     risk_reasons=["engine already reproduces the authority's value"] if reproduced else
                                  ["engine disagrees with the stated value: investigate rule data or code"],
                     payload={"scenario": {"id": s.id, "calc": s.calc, "inputs": inputs, "expect": expect,
                                           "source": s.source}, "fingerprint": f"golden:{s.id}"},
                     checks=[Check(name="engine_reproduces_value", ok=reproduced, detail=str(got))])
        if not f.duplicate_of("golden_scenario", p.payload["fingerprint"]):
            res.proposals.append(f.submit(p).id)
    for need in brief.engineering_needs[:3]:
        work = f.paths.state / "work"
        work.mkdir(parents=True, exist_ok=True)
        key = "".join(ch for ch in need.lower() if ch.isalnum())[:40]
        target = work / f"research_{key}.json"
        if not target.exists():
            target.write_text(json.dumps({"title": need[:120], "items": [need], "status": "needs_triage",
                                          "source": str(out.name)}, indent=2), encoding="utf-8")


@applier("golden_scenario")
def apply_golden(f: Foundry, p: Proposal) -> dict[str, Any]:
    path = f.paths.golden / "scenarios.yaml"
    data = load_golden(path)
    data.append(p.payload["scenario"])
    path.write_text(yaml.safe_dump(data, sort_keys=False, width=110), encoding="utf-8")
    return {"added": p.payload["scenario"]["id"]}


# ============================================================================ architect

class DesignedAgent(BaseModel):
    id: str = Field(description="lowercase-with-dashes")
    kind: str = Field(description="one of the available agent kinds")
    title: str
    mission: str
    every_hours: float
    params_json: str = Field(description="JSON object of parameters for that kind")


def design_agent(f: Foundry, description: str, actor: str = "cpa") -> Proposal:
    from ..core import AGENT_KINDS

    system = ("Design one monitoring agent for an accounting platform. Available kinds and their params:\n"
              "- web_watch: {url, keywords[], max_docs} — watch an official web page for regulatory news\n"
              "- federal_register: {agencies[], lookback_days} — Federal Register agency slugs\n"
              "- connector: {plugin, client_id, config{}} — run an integration on a schedule\n"
              "Prefer official government URLs.")
    d, by = f.router.structured("reason" if f.router.frontier_allowed() else "answer", data_class="firm", system=system, user=description,
                                schema=DesignedAgent)
    from ...regwatch.documents import is_official

    params = json.loads(d.params_json)
    checks = [Check(name="kind_exists", ok=d.kind in AGENT_KINDS, detail=d.kind),
              Check(name="id_unique", ok=d.id not in {s.id for s in f.specs()}, detail=d.id),
              Check(name="schedule_sane", ok=1 <= d.every_hours <= 24 * 31, detail=str(d.every_hours))]
    if d.kind == "web_watch":
        checks.append(Check(name="official_url", ok=is_official(params.get("url", ""), f.policy.get("official_domains", [])),
                            detail=params.get("url", "")))
    p = Proposal(kind="agent_spec", agent="architect", title=f"New agent: {d.title}", summary=d.mission, risk="medium",
                 risk_reasons=["new autonomous agent"], payload={"spec": {"id": d.id, "kind": d.kind, "title": d.title,
                 "mission": d.mission, "every_hours": d.every_hours, "params": params}, "designed_by": by, "request": description},
                 checks=checks)
    return f.submit(p)


@applier("agent_spec")
def apply_agent_spec(f: Foundry, p: Proposal) -> dict[str, Any]:
    path = f.paths.config / "agents.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["agents"].append(p.payload["spec"])
    path.write_text(yaml.safe_dump(data, sort_keys=False, width=110), encoding="utf-8")
    return {"agent": p.payload["spec"]["id"]}


def design_domain_pack(f: Foundry, description: str) -> Proposal:
    from ...domains.packs import DomainPack, Packs, verify_pack

    packs = Packs(f.paths.domains)
    example = (f.paths.domains / "auto_repair.yaml").read_text(encoding="utf-8")
    general_codes = ", ".join(f"{a.code} {a.name}" for a in packs.get("general").accounts)
    system = ("You design industry 'domain packs' for an accounting platform: a chart of accounts extending 'general', balanced "
              "posting templates (positive amount = debit; every template's lines must sum to zero for its sample), "
              "domain document types, KPIs and declarative integrity checks (types: min_balance, ratio_max, memo_pattern, "
              "memo_account, coverage). Return the pack as YAML in the same shape as the example. Use new account codes that do "
              "not collide with general's. Allowed tax_treatment values: meals, entertainment, fines_penalties, "
              f"officer_life_insurance, municipal_interest, depreciation, federal_income_tax, travel, vehicle.\n"
              f"General accounts: {general_codes}\n\nExample pack:\n{example}")

    class PackOut(BaseModel):
        yaml_text: str

    out, by = f.router.structured("reason" if f.router.frontier_allowed() else "answer", data_class="firm", system=system,
                                  user=f"Design a pack for: {description}", schema=PackOut, effort="high")
    try:
        pack = DomainPack.model_validate(yaml.safe_load(out.yaml_text))
        checks = [Check(**c) for c in verify_pack(pack, packs, f.kb)]
        checks.append(Check(name="id_unique", ok=pack.id not in packs.packs, detail=pack.id))
        payload = {"pack": pack.model_dump(), "designed_by": by, "request": description, "fingerprint": f"pack:{pack.id}"}
        title = f"New domain pack: {pack.title}"
    except Exception as e:
        checks = [Check(name="schema_valid", ok=False, detail=f"{type(e).__name__}: {e}")]
        payload = {"raw": out.yaml_text, "designed_by": by, "request": description}
        title = f"Domain pack draft (invalid): {description[:60]}"
    p = Proposal(kind="domain_pack", agent="architect", title=title, summary=f"Drafted from: {description}", risk="medium",
                 risk_reasons=["new industry chart of accounts and posting logic: CPA review required"], payload=payload, checks=checks)
    return f.submit(p)


@applier("domain_pack")
def apply_domain_pack(f: Foundry, p: Proposal) -> dict[str, Any]:
    from ...domains.packs import DomainPack, Packs

    pack = DomainPack.model_validate(p.payload["pack"])
    Packs(f.paths.domains).save(pack)
    return {"pack": pack.id}


def design_playbook(f: Foundry, description: str) -> Proposal:
    from ...brain.playbooks import Playbook
    from ...expr import names_used

    class PlaybookOut(BaseModel):
        id: str
        title: str
        category: str = Field(description="planning | compliance | review | risk")
        summary: str
        applies_when: str = Field(description="expression over client facts, e.g. kind == 'business' and employees > 10")
        rule_refs: list[str]
        steps: list[str]
        substance: str
        aggressiveness: str = Field(description="conservative | moderate | aggressive")
        citations: list[str]

    catalog = ", ".join(sorted(f.kb.rules))
    out, by = f.router.structured("reason" if f.router.frontier_allowed() else "answer", data_class="firm",
                                  system="You are a senior CPA writing a firm playbook. Be accurate and conservative; cite primary "
                                         f"authority. rule_refs must come from this list: {catalog}",
                                  user=description, schema=PlaybookOut, effort="high")
    data = {**out.model_dump(), "status": "seed_unreviewed", "review_by": date(date.today().year + 1, 3, 31)}
    checks = []
    try:
        pb = Playbook.model_validate(data)
        checks.append(Check(name="schema_valid", ok=True))
        names_used(pb.applies_when)
        checks.append(Check(name="condition_parses", ok=True, detail=pb.applies_when))
        bad = [r for r in pb.rule_refs if r not in f.kb.rules]
        checks.append(Check(name="rule_refs_exist", ok=not bad, detail=", ".join(bad) or "ok"))
    except Exception as e:
        checks.append(Check(name="schema_valid", ok=False, detail=str(e)[:300]))
    p = Proposal(kind="playbook", agent="architect", title=f"Playbook: {out.title}", summary=out.summary, risk="medium",
                 risk_reasons=["expert knowledge: a CPA reviews before it is used in answers"],
                 payload={"playbook": json.loads(json.dumps(data, default=str)), "designed_by": by}, checks=checks)
    return f.submit(p)


@applier("playbook")
def apply_playbook(f: Foundry, p: Proposal) -> dict[str, Any]:
    from ...brain.playbooks import Brain, Playbook

    pb = Playbook.model_validate({**p.payload["playbook"], "status": "approved", "last_reviewed": date.today(),
                                  "reviewed_by": (p.decision or {}).get("by")})
    Brain(f.paths.root / "playbooks", f.kb).save(pb)
    return {"playbook": pb.id}


def design_automation(f: Foundry, description: str) -> Proposal:
    from ...crm.automations import ACTIONS, TRIGGERS, Automation, validate

    class AutoOut(BaseModel):
        yaml_text: str

    example = (f.paths.config / "automations.yaml").read_text(encoding="utf-8")[:3000]
    out, by = f.router.structured("answer", data_class="firm", system=f"Write ONE automation as YAML (a single mapping, not a list). Triggers: "
                                                    f"{json.dumps(TRIGGERS)}. Actions: {sorted(ACTIONS)}. Examples:\n{example}",
                                  user=description, schema=AutoOut)
    try:
        data = yaml.safe_load(out.yaml_text)
        data = data[0] if isinstance(data, list) else data.get("automations", [data])[0] if "automations" in data else data
        auto = Automation.model_validate(data)
        checks = [Check(**c) for c in validate(auto)]
        payload = {"automation": auto.model_dump(exclude_none=True), "designed_by": by}
    except Exception as e:
        checks = [Check(name="schema_valid", ok=False, detail=str(e)[:300])]
        payload = {"raw": out.yaml_text}
    p = Proposal(kind="automation", agent="architect", title=f"Automation: {description[:80]}",
                 summary=description, risk="medium", risk_reasons=["new automated behaviour: human approval"], payload=payload,
                 checks=checks)
    return f.submit(p)


@applier("automation")
def apply_automation(f: Foundry, p: Proposal) -> dict[str, Any]:
    path = f.paths.config / "automations.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["automations"].append(p.payload["automation"])
    path.write_text(yaml.safe_dump(data, sort_keys=False, width=110), encoding="utf-8")
    return {"automation": p.payload["automation"]["id"]}


# ============================================================================ glue agents

@agent_kind("connector")
def connector_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    from ...plugins.registry import run_plugin

    out = run_plugin(f, spec.params["plugin"], spec.params["client_id"], spec.params.get("config", {}))
    res.stats.update({k: (len(v) if isinstance(v, list) else v) for k, v in out.items() if k != "plugin"})


@agent_kind("automations")
def automations_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    from ...crm.automations import run

    out = run(f.conn, f.paths.config / "automations.yaml", foundry=f, deadlines_path=f.paths.config / "deadlines.yaml")
    res.stats.update(events=out["events"], fired=len(out["fired"]))
