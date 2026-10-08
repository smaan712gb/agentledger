"""Model router: per-role champion models, tiered open-source-first, budgeted frontier use.

Roles ("modes") are jobs, not models. config/models.yaml maps each role to its
current champion; the Model Scout agent rewrites that mapping when a challenger
wins on the evaluation suite. Callers only ever name a role.
"""

from __future__ import annotations

import os
import re

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator, TypeVar

import yaml
from pydantic import BaseModel

from .. import audit
from .frontier import ClaudeClient, FrontierError
from .local import LocalUnavailable, OllamaClient

M = TypeVar("M", bound=BaseModel)  # the schema a structured call returns

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


# Data classes (ADR-0007). Every call states what it carries; the default is the most restrictive.
DATA_CLASSES = ("public", "firm", "taxpayer")
_SSN = re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b")
_EIN = re.compile(r"\b\d{2}-\d{7}\b")
_ACCOUNT = re.compile(r"\b\d{9,17}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")


BLOCKED_CONTENT = {"image", "image_url", "input_image", "document", "file", "input_file", "input_audio", "audio", "video"}


def sanitize(value: Any, names: list[str] | None = None) -> Any:
    """Redact every text field at any depth of a structured message, and refuse media blocks outright: images and
    documents of taxpayers never leave our infrastructure."""
    if isinstance(value, str):
        return redact(value, names)
    if isinstance(value, list):
        return [sanitize(v, names) for v in value]
    if isinstance(value, dict):
        kind = value.get("type")
        if isinstance(kind, str) and kind.lower() in BLOCKED_CONTENT:
            raise Unavailable(f"external model refused: {kind} content is never sent for taxpayer data")
        return {k: (v if k == "type" else sanitize(v, names)) for k, v in value.items()}
    return value


def redact(text: str, names: list[str] | None = None) -> str:
    """Defense in depth before an external model: direct identifiers removed. Not the legal basis (consent is)."""
    for n in names or []:
        if n and len(n) > 2:
            text = re.sub(re.escape(n), "[TAXPAYER]", text, flags=re.I)
    text = _SSN.sub("[SSN]", text)
    text = _EIN.sub("[EIN]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _PHONE.sub("[PHONE]", text)
    return _ACCOUNT.sub("[ACCOUNT]", text)


class Router:
    def __init__(self, registry: Registry, conn: sqlite3.Connection | None = None,
                 local: OllamaClient | None = None, frontier: ClaudeClient | None = None):
        self.registry = registry
        self.conn = conn
        # AGENTLEDGER_OLLAMA_URL overrides config/models.yaml (tests point it at a closed port)
        self.local = local or OllamaClient(os.environ.get("AGENTLEDGER_OLLAMA_URL") or registry.data["ollama_url"])
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

    def external_policy(self, data_class: str, client_id: str | None) -> tuple[bool, str, list[str]]:
        """The single gate for every external-model request: (allowed, reason, names to redact)."""
        if data_class not in DATA_CLASSES:
            return False, f"unknown data class {data_class!r}", []
        if data_class in ("public", "firm"):
            return True, data_class, []
        if not client_id:
            return False, "taxpayer data with no identified client: no consent can cover it", []
        row = self.conn.execute("SELECT name, aliases, consent_7216_at FROM clients WHERE id = ?", (client_id,)).fetchone() \
            if self.conn else None
        if not row or not row["consent_7216_at"]:
            return False, f"no IRC §7216 consent on file for {client_id}", []
        import json as _json

        return True, "consent on file", [row["name"], *(_json.loads(row["aliases"] or "[]"))]

    def _log(self, tier: str, model: str, task: str, ok: bool, usage: dict[str, Any] | None = None,
             client_id: str | None = None, note: str | None = None) -> None:
        if self.conn:
            u = usage or {}
            self.conn.execute(
                "INSERT INTO ai_usage (at, tier, model, task, client_id, input_tokens, output_tokens, ok, note) VALUES (?,?,?,?,?,?,?,?,?)",
                (audit.now(), tier, model, task, client_id, u.get("input_tokens"), u.get("output_tokens"), int(ok), note),
            )

    # -- calls -----------------------------------------------------------------------
    def structured(self, role: str, *, system: str, user: str, schema: type[M], images: list[bytes] | None = None,
                   escalate: bool = False, client_id: str | None = None, effort: str = "medium",
                   data_class: str = "taxpayer") -> tuple[M, str]:
        """Run a structured task on the role's champion. Returns (result, "tier:model").

        Local models run inside our infrastructure. Any external (frontier) call first passes `external_policy`:
        taxpayer data needs a §7216 consent for that client, is redacted, and never carries images."""
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
                    allowed, why, names = self.external_policy(data_class, client_id)
                    if not allowed:
                        raise Unavailable(f"external model refused: {why}")
                    if not self.frontier_allowed():
                        raise Unavailable("frontier tier unavailable or daily budget exhausted")
                    self.frontier.model = model
                    sys_text = system
                    content: str | list[dict[str, Any]] = user
                    if data_class == "taxpayer":
                        sys_text, content = redact(system, names), redact(user, names)
                        images = None  # document images are never sent outside our infrastructure
                    if images:
                        import base64
                        content = [*({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                 "data": base64.b64encode(i).decode()}} for i in images),
                                   {"type": "text", "text": content}]
                    result, usage = self.frontier.structured(system=sys_text, content=content, schema=schema, effort=effort)
                self._log(tier, model, role, True, usage, client_id)
                return result, f"{tier}:{model}"
            except (LocalUnavailable, FrontierError, Unavailable, ValueError) as e:
                self._log(tier, model, role, False, None, client_id, str(e)[:300])
                last_err = e
        raise Unavailable(f"role {role}: all tiers failed ({last_err})")

    def stream(self, role: str, *, system: str, messages: list[dict[str, Any]], client_id: str | None = None,
               data_class: str = "taxpayer") -> tuple[Iterator[str], str]:
        rm = self.registry.role(role)
        if rm.tier == "local":
            gen = self.local.stream(rm.model, [{"role": "system", "content": system}, *messages])
        else:
            allowed, why, names = self.external_policy(data_class, client_id)
            if not allowed:
                raise Unavailable(f"external model refused: {why}")
            if not self.frontier_allowed():
                raise Unavailable("frontier tier unavailable or daily budget exhausted")
            self.frontier.model = rm.model
            if data_class == "taxpayer":
                system = redact(system, names)
                messages = [sanitize(m, names) for m in messages]
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
