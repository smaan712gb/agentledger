"""The Agent Foundry: one contract for every autonomous agent.

    observe -> propose -> verify (deterministic) -> adopt | escalate

Agents never change the system directly. They submit Proposals. A proposal is
adopted automatically only when (a) every deterministic check passed and (b) its
risk tier is within what policy allows for its kind. Everything else waits for a
human decision in plain language (a CPA, not a developer). Every adoption stores
what is needed to undo it.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from pydantic import BaseModel, Field

from .. import audit
from ..ai.router import Registry, Router
from ..kb.store import KnowledgeBase
from ..security.vault import Vault

Kind = Literal["rule_change", "model_upgrade", "dependency_upgrade", "repo_adoption", "code_change", "agent_spec", "playbook",
               "domain_pack", "golden_scenario", "automation"]
Risk = Literal["low", "medium", "high", "critical"]
RISK_ORDER = {"none": -1, "low": 0, "medium": 1, "high": 2, "critical": 3}


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class Proposal(BaseModel):
    id: str = Field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + secrets.token_hex(3))
    kind: Kind
    agent: str
    title: str
    summary: str
    risk: Risk = "high"
    risk_reasons: list[str] = []
    status: Literal["pending", "adopted", "rejected", "rolled_back", "failed"] = "pending"
    created_at: str = Field(default_factory=audit.now)
    source: dict[str, Any] | None = None
    payload: dict[str, Any] = {}
    checks: list[Check] = []
    impact: dict[str, Any] = {}
    decision: dict[str, Any] | None = None
    undo: dict[str, Any] | None = None

    @property
    def verified(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)


# Agents that work on one firm's data run inside each firm; the rest maintain the shared platform
# (regulations, models, repositories, code) and run once.
TENANT_KINDS = {"intake_maildrop", "intake_imap", "integrity_sweeper", "automations", "evidence_anchor"}


@dataclass
class Paths:
    """Shared platform content lives under `root`; a firm's data lives under `tenant` (or root in dev)."""

    root: Path
    tenant: Path | None = None

    @property
    def data(self) -> Path: return self.tenant or self.root
    @property
    def rules(self) -> Path: return self.root / "rules"
    @property
    def config(self) -> Path: return self.root / "config"
    @property
    def state(self) -> Path: return self.data / "state"
    @property
    def proposals(self) -> Path: return self.root / "state" / "proposals"
    @property
    def vault(self) -> Path: return self.data / "vault"
    @property
    def maildrop(self) -> Path: return self.data / "maildrop"
    @property
    def golden(self) -> Path: return self.root / "golden"
    @property
    def evals(self) -> Path: return self.root / "evals"
    @property
    def domains(self) -> Path: return self.root / "domains"
    @property
    def research(self) -> Path: return self.root / "research"
    @property
    def db(self) -> Path: return self.state / "agentledger.db"


@dataclass
class AgentResult:
    proposals: list[str] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)


class AgentSpec(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    kind: str
    title: str = ""
    mission: str = ""
    every_hours: float = 24
    enabled: bool = True
    first_run: Literal["immediate", "after_interval"] = "immediate"
    params: dict[str, Any] = {}


Applier = Callable[["Foundry", Proposal], dict[str, Any] | None]
Undoer = Callable[["Foundry", Proposal], None]
AGENT_KINDS: dict[str, Callable[["Foundry", AgentSpec, AgentResult], None]] = {}
APPLIERS: dict[str, tuple[Applier, Undoer | None]] = {}


def _register() -> None:
    """Agent kinds and appliers live in foundry.agents; import it before dispatching on them."""
    from . import agents  # noqa: F401


def agent_kind(name: str):
    def deco(fn):
        AGENT_KINDS[name] = fn
        return fn
    return deco


def applier(kind: str, undo: Undoer | None = None):
    def deco(fn: Applier) -> Applier:
        APPLIERS[kind] = (fn, undo)
        return fn
    return deco


class Foundry:
    def __init__(self, root: Path, conn: sqlite3.Connection, router: Router | None = None, *,
                 tenant: Path | None = None, kb: KnowledgeBase | None = None, scope: str = "all"):
        self.paths = Paths(Path(root), Path(tenant) if tenant else None)
        self.conn = conn
        self.scope = scope  # "all" (single-firm/dev), "platform" or "tenant"
        self.kb = kb or KnowledgeBase(self.paths.rules)
        self.vault = Vault(self.paths.vault)  # replaced by an encrypted vault inside a firm
        self.policy = yaml.safe_load((self.paths.config / "foundry.yaml").read_text(encoding="utf-8"))
        self.router = router or Router(Registry(self.paths.config / "models.yaml"), conn)
        self.paths.proposals.mkdir(parents=True, exist_ok=True)

    # -- specs -----------------------------------------------------------------------
    def specs(self) -> list[AgentSpec]:
        data = yaml.safe_load((self.paths.config / "agents.yaml").read_text(encoding="utf-8")) or {}
        return [AgentSpec.model_validate(a) for a in data.get("agents", [])]

    def spec(self, agent_id: str) -> AgentSpec:
        for s in self.specs():
            if s.id == agent_id:
                return s
        raise KeyError(f"unknown agent {agent_id}")

    # -- proposals -------------------------------------------------------------------
    def save(self, p: Proposal) -> None:
        (self.paths.proposals / f"{p.id}.json").write_text(p.model_dump_json(indent=2), encoding="utf-8")

    def load(self, pid: str) -> Proposal:
        path = self.paths.proposals / f"{pid}.json"
        if not path.exists():
            raise KeyError(f"unknown proposal {pid}")
        return Proposal.model_validate_json(path.read_text(encoding="utf-8"))

    def proposals(self, status: str | None = None, kind: str | None = None) -> list[Proposal]:
        out = [Proposal.model_validate_json(p.read_text(encoding="utf-8")) for p in self.paths.proposals.glob("*.json")]
        out = [p for p in out if (status is None or p.status == status) and (kind is None or p.kind == kind)]
        return sorted(out, key=lambda p: p.created_at, reverse=True)

    def duplicate_of(self, kind: str, fingerprint: str) -> Proposal | None:
        for p in self.proposals(kind=kind):
            if p.payload.get("fingerprint") == fingerprint and p.status in ("pending", "adopted"):
                return p
        return None

    def submit(self, p: Proposal) -> Proposal:
        """Persist a proposal and adopt it automatically if policy allows."""
        max_auto = self.policy.get("auto_adopt", {}).get(p.kind, "none")
        auto = p.verified and RISK_ORDER[p.risk] <= RISK_ORDER[max_auto]
        if p.kind == "code_change" and p.payload.get("touches_protected"):
            auto = False  # the AI never approves changes to its own guardrails
        self.save(p)
        audit.record(self.conn, p.agent, "agent", "proposal.submitted",
                     {"proposal_id": p.id, "kind": p.kind, "title": p.title, "risk": p.risk, "verified": p.verified})
        if auto:
            return self.adopt(p.id, actor="auto-policy", note=f"verified; risk {p.risk} within auto-adopt policy ({max_auto})")
        return p

    def adopt(self, pid: str, actor: str, note: str = "", role: str = "cpa") -> Proposal:
        p = self.load(pid)
        if p.status != "pending":
            raise ValueError(f"proposal {pid} is {p.status}")
        if not p.verified and actor == "auto-policy":
            raise ValueError("unverified proposals are never adopted automatically")
        _register()
        apply_fn, _ = APPLIERS[p.kind]
        p.decision = {"by": actor, "at": audit.now(), "note": note, "mode": "auto" if actor == "auto-policy" else "human"}
        try:
            p.undo = apply_fn(self, p)
        except Exception as e:  # keep the failure visible rather than half-applied
            p.status = "failed"
            p.decision = {"by": actor, "at": audit.now(), "note": f"apply failed: {e}", "mode": "auto" if actor == "auto-policy" else "human"}
            self.save(p)
            audit.record(self.conn, actor, role, "proposal.failed", {"proposal_id": pid, "error": str(e)})
            raise
        p.status = "adopted"
        p.decision = {"by": actor, "at": audit.now(), "note": note, "mode": "auto" if actor == "auto-policy" else "human"}
        self.save(p)
        audit.record(self.conn, actor, "agent" if actor == "auto-policy" else role, "proposal.adopted",
                     {"proposal_id": pid, "kind": p.kind, "title": p.title, "note": note})
        return p

    def reject(self, pid: str, actor: str, note: str, role: str = "cpa") -> Proposal:
        p = self.load(pid)
        if p.status != "pending":
            raise ValueError(f"proposal {pid} is {p.status}")
        p.status = "rejected"
        p.decision = {"by": actor, "at": audit.now(), "note": note, "mode": "human"}
        self.save(p)
        audit.record(self.conn, actor, role, "proposal.rejected", {"proposal_id": pid, "title": p.title, "note": note})
        return p

    def rollback(self, pid: str, actor: str, note: str, role: str = "cpa") -> Proposal:
        p = self.load(pid)
        if p.status != "adopted":
            raise ValueError(f"proposal {pid} is {p.status}, not adopted")
        _register()
        _, undo_fn = APPLIERS[p.kind]
        if undo_fn is None:
            raise ValueError(f"{p.kind} proposals cannot be rolled back automatically")
        undo_fn(self, p)
        p.status = "rolled_back"
        p.decision = {**(p.decision or {}), "rolled_back_by": actor, "rolled_back_at": audit.now(), "rollback_note": note}
        self.save(p)
        audit.record(self.conn, actor, role, "proposal.rolled_back", {"proposal_id": pid, "title": p.title, "note": note})
        return p

    # -- running agents --------------------------------------------------------------
    def _runs_path(self) -> Path:
        return self.paths.state / "agent_runs.jsonl"

    def runs(self, limit: int = 100) -> list[dict[str, Any]]:
        path = self._runs_path()
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(l) for l in reversed(lines)]

    def last_run(self, agent_id: str) -> datetime | None:
        for r in self.runs(limit=5000):
            if r["agent"] == agent_id:
                return datetime.fromisoformat(r["started_at"])
        return None

    def installed_at(self) -> datetime:
        p = self.paths.state / "installed_at"
        if not p.exists():
            p.write_text(audit.now(), encoding="utf-8")
        return datetime.fromisoformat(p.read_text(encoding="utf-8").strip())

    def due(self) -> list[AgentSpec]:
        now = datetime.now(timezone.utc)
        out = []
        for s in self.specs():
            if self.scope == "platform" and s.kind in TENANT_KINDS or self.scope == "tenant" and s.kind not in TENANT_KINDS:
                continue
            last = self.last_run(s.id)
            if last is None and s.first_run == "after_interval":
                last = self.installed_at()
            if s.enabled and (last is None or now - last >= timedelta(hours=s.every_hours)):
                out.append(s)
        return out

    def run(self, agent_id: str) -> dict[str, Any]:
        _register()
        spec = self.spec(agent_id)
        if spec.kind not in AGENT_KINDS:
            raise KeyError(f"agent {agent_id}: unknown kind {spec.kind}")
        started = audit.now()
        res = AgentResult()
        ok, error = True, None
        try:
            AGENT_KINDS[spec.kind](self, spec, res)
        except Exception as e:
            ok, error = False, f"{type(e).__name__}: {e}"
            res.log.append(traceback.format_exc(limit=3))
        record = {"agent": agent_id, "kind": spec.kind, "started_at": started, "finished_at": audit.now(), "ok": ok,
                  "error": error, "proposals": res.proposals, "alerts": res.alerts, "stats": res.stats, "log": res.log[-30:]}
        self._runs_path().parent.mkdir(parents=True, exist_ok=True)   # a PostgreSQL tenant has no store file to create it
        with self._runs_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        audit.record(self.conn, agent_id, "agent", "agent.run", {"ok": ok, "error": error, "proposals": res.proposals,
                                                                "alerts": len(res.alerts), "stats": res.stats})
        return record

    def run_due(self) -> list[dict[str, Any]]:
        return [self.run(s.id) for s in self.due()]
