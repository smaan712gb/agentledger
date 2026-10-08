"""Model router: per-role champion models, tiered open-source-first, budgeted frontier use.

Roles ("modes") are jobs, not models. config/models.yaml maps each role to its
current champion; the Model Scout agent rewrites that mapping when a challenger
wins on the evaluation suite. Callers only ever name a role.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import yaml
from pydantic import BaseModel

from .. import audit
from .frontier import ClaudeClient, FrontierError
from .local import LocalUnavailable, OllamaClient

ROLES = {
    "triage": "Decide whether a public document affects any regulated parameter",
    "classify": "Classify and extract fields from client documents (text)",
    "vision": "Read scanned or photographed documents",
    "answer": "Answer everyday questions from an evidence pack",
    "draft": "Draft a regulation change proposal from an official document",
    "reason": "Advanced reasoning: new law, planning, ambiguous compliance questions",
    "code": "Write code changes when a regulation cannot be expressed as data",
}


class Unavailable(RuntimeError):
    pass


@dataclass
class RoleModel:
    tier: str  # local | frontier
    model: str
    fallback: dict[str, str] | None = None  # optional escalation target {tier, model}


class Registry:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = yaml.safe_load(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.data.setdefault("roles", {})
        self.data.setdefault("history", [])
        self.data.setdefault("frontier", {"daily_call_budget": 25})
        self.data.setdefault("ollama_url", "http://localhost:11434")

    def role(self, role: str) -> RoleModel:
        r = self.data["roles"].get(role)
        if not r:
            raise Unavailable(f"no model configured for role {role}")
        return RoleModel(r["tier"], r["model"], r.get("fallback"))

    def set_role(self, role: str, tier: str, model: str, reason: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        prev = self.data["roles"].get(role)
        entry = {"role": role, "from": prev, "to": {"tier": tier, "model": model}, "reason": reason,
                 "evidence": evidence or {}, "at": audit.now()}
        self.data["roles"][role] = {**(prev or {}), "tier": tier, "model": model}
        self.data["history"].append(entry)
        self.save()
        return entry

    def save(self) -> None:
        class NoAliases(yaml.SafeDumper):
            def ignore_aliases(self, data):  # plain, reviewable YAML (no &id001 anchors)
                return True

        self.path.write_text(yaml.dump(self.data, Dumper=NoAliases, sort_keys=False, width=110), encoding="utf-8")


class Router:
    def __init__(self, registry: Registry, conn: sqlite3.Connection | None = None,
                 local: OllamaClient | None = None, frontier: ClaudeClient | None = None):
        self.registry = registry
        self.conn = conn
        self.local = local or OllamaClient(registry.data["ollama_url"])
        self.frontier = frontier or ClaudeClient()

    # -- budget & accounting ---------------------------------------------------------
    def frontier_calls_today(self) -> int:
        if not self.conn:
            return 0
        r = self.conn.execute("SELECT COUNT(*) FROM ai_usage WHERE tier='frontier' AND substr(at,1,10)=?",
                              (date.today().isoformat(),)).fetchone()
        return int(r[0])

    def frontier_allowed(self) -> bool:
        budget = int(self.registry.data["frontier"].get("daily_call_budget", 0))
        return self.frontier.available() and self.frontier_calls_today() < budget

    def _log(self, tier: str, model: str, task: str, ok: bool, usage: dict[str, Any] | None = None,
             client_id: str | None = None, note: str | None = None) -> None:
        if self.conn:
            u = usage or {}
            self.conn.execute(
                "INSERT INTO ai_usage (at, tier, model, task, client_id, input_tokens, output_tokens, ok, note) VALUES (?,?,?,?,?,?,?,?,?)",
                (audit.now(), tier, model, task, client_id, u.get("input_tokens"), u.get("output_tokens"), int(ok), note),
            )

    # -- calls -----------------------------------------------------------------------
    def structured(self, role: str, *, system: str, user: str, schema: type[BaseModel], images: list[bytes] | None = None,
                   escalate: bool = False, client_id: str | None = None, effort: str = "medium") -> tuple[BaseModel, str]:
        """Run a structured task on the role's champion. Returns (result, "tier:model")."""
        rm = self.registry.role(role)
        targets = [(rm.tier, rm.model)]
        if escalate and rm.tier == "local":
            fb = rm.fallback or self.registry.data["roles"].get("reason")
            if fb:
                targets.append((fb["tier"], fb["model"]))
        last_err: Exception | None = None
        for tier, model in targets:
            try:
                if tier == "local":
                    data, usage = self.local.chat_json(model, [{"role": "system", "content": system},
                                                               {"role": "user", "content": user}],
                                                       schema=schema.model_json_schema(), images=images)
                    result = schema.model_validate(data)
                else:
                    if not self.frontier_allowed():
                        raise Unavailable("frontier tier unavailable or daily budget exhausted")
                    self.frontier.model = model
                    content: Any = user
                    if images:
                        import base64
                        content = [*({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                 "data": base64.b64encode(i).decode()}} for i in images),
                                   {"type": "text", "text": user}]
                    result, usage = self.frontier.structured(system=system, content=content, schema=schema, effort=effort)
                self._log(tier, model, role, True, usage, client_id)
                return result, f"{tier}:{model}"
            except (LocalUnavailable, FrontierError, Unavailable, ValueError) as e:
                self._log(tier, model, role, False, None, client_id, str(e)[:300])
                last_err = e
        raise Unavailable(f"role {role}: all tiers failed ({last_err})")

    def stream(self, role: str, *, system: str, messages: list[dict[str, Any]], client_id: str | None = None) -> tuple[Iterator[str], str]:
        rm = self.registry.role(role)
        if rm.tier == "local":
            gen = self.local.stream(rm.model, [{"role": "system", "content": system}, *messages])
        else:
            if not self.frontier_allowed():
                raise Unavailable("frontier tier unavailable or daily budget exhausted")
            self.frontier.model = rm.model
            gen = self.frontier.stream_text(system=system, messages=messages)
        self._log(rm.tier, rm.model, role, True, None, client_id, "stream")
        return gen, f"{rm.tier}:{rm.model}"

    def status(self) -> dict[str, Any]:
        local_ok = self.local.available()
        return {
            "local": {"available": local_ok, "url": self.registry.data["ollama_url"],
                      "installed": [m["name"] for m in self.local.models()] if local_ok else []},
            "frontier": {"available": self.frontier.available(), "calls_today": self.frontier_calls_today(),
                         "daily_budget": self.registry.data["frontier"].get("daily_call_budget")},
            "roles": {k: {**v, "purpose": ROLES.get(k, "")} for k, v in self.registry.data["roles"].items()},
            "history": self.registry.data["history"][-20:],
        }
