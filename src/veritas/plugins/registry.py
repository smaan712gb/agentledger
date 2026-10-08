"""Plugins and connectors with least-privilege, audited access.

A plugin declares a manifest (what it is, what it needs). At run time it receives a
PluginContext exposing only the capabilities it declared. Plugins never write to the
ledger directly: they emit transactions (-> bank-feed suggestions), documents (-> intake),
or template postings (-> domain packs), so every byte flows through the same
integrity checks, client segregation and audit trail as everything else.

Discovery: built-ins below, any `plugins/<id>/plugin.yaml` + module in the repo, and
Python entry points in the `veritas.plugins` group (pip-installable plugins).
"""

from __future__ import annotations

import importlib
import importlib.util
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlparse

import httpx
import yaml
from pydantic import BaseModel

from .. import audit

Permission = Literal["ledger:read", "ledger:post_template", "ledger:opening_balances", "transactions:suggest",
                     "documents:ingest", "rules:read", "clients:read", "network", "export"]


class Manifest(BaseModel):
    id: str
    name: str
    version: str = "0.1.0"
    kind: Literal["connector", "importer", "exporter"]
    description: str
    entry: str  # "module.path:function"
    permissions: list[Permission]
    network_domains: list[str] = []
    config: dict[str, str] = {}  # config key -> description (secrets come from env vars named in config)
    builtin: bool = False


class PermissionDenied(PermissionError):
    pass


@dataclass
class PluginContext:
    manifest: Manifest
    conn: sqlite3.Connection
    client_id: str
    config: dict[str, Any]
    foundry: Any
    output: dict[str, Any] = field(default_factory=dict)

    def _need(self, perm: str) -> None:
        if perm not in self.manifest.permissions:
            raise PermissionDenied(f"plugin {self.manifest.id} did not declare {perm}")

    def _log(self, what: str, detail: dict[str, Any]) -> None:
        audit.record(self.conn, f"plugin:{self.manifest.id}", "agent", f"plugin.{what}", detail, client_id=self.client_id)

    # -- capabilities ---------------------------------------------------------------------
    def http_get(self, url: str, **kw: Any) -> httpx.Response:
        self._need("network")
        host = (urlparse(url).hostname or "").lower()
        if not any(host == d or host.endswith("." + d) for d in self.manifest.network_domains):
            raise PermissionDenied(f"{host} not in declared network_domains")
        return httpx.get(url, timeout=60, **kw)

    def suggest_transactions(self, txns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._need("transactions:suggest")
        from ..ledger.bankfeed import suggest

        out = suggest(self.conn, self.foundry.router if self.foundry else None, self.client_id, txns)
        self._log("transactions", {"count": len(txns)})
        self.output.setdefault("suggestions", []).extend(out)
        return out

    def ingest_document(self, name: str, data: bytes, sender: str | None = None) -> list[dict[str, Any]]:
        self._need("documents:ingest")
        from ..intake.pipeline import ingest

        out = ingest(self.conn, self.foundry.router if self.foundry else None, self.foundry.vault, name, data,
                     channel=f"plugin:{self.manifest.id}", sender=sender, client_hint=self.client_id)
        self.output.setdefault("documents", []).extend(out)
        return out

    def post_template(self, template_id: str, inputs: dict[str, Any], on: date) -> int:
        self._need("ledger:post_template")
        from ..domains.packs import Packs
        from ..domains.service import post_template

        entry = post_template(self.conn, Packs(self.foundry.paths.domains), self.foundry.kb, self.client_id, template_id, inputs, on,
                              actor=f"plugin:{self.manifest.id}", role="agent", source=f"plugin:{self.manifest.id}")
        self.output.setdefault("entries", []).append(entry)
        return entry

    def ledger(self) -> "ReadOnlyLedger":
        self._need("ledger:read")
        return ReadOnlyLedger(self.conn, self.client_id)

    def post_opening_balances(self, lines: list[tuple[str, Any]], as_of: date) -> int:
        """Migration only: one balanced opening entry, refused if the client already has history."""
        self._need("ledger:opening_balances")
        from ..ledger import store

        if store.entries(self.conn, self.client_id, limit=1):
            raise PermissionDenied("client already has ledger history; opening balances must be posted by a CPA")
        entry = store.post(self.conn, self.client_id, as_of, "Opening balances (migrated)",
                           [store.Line(code, amount) for code, amount in lines], source=f"plugin:{self.manifest.id}",
                           actor=f"plugin:{self.manifest.id}", role="agent")
        self.output.setdefault("entries", []).append(entry)
        return entry

    def client(self) -> dict[str, Any]:
        self._need("clients:read")
        from ..ledger.store import get_client

        return get_client(self.conn, self.client_id)


class ReadOnlyLedger:
    """What a plugin with ledger:read can see. No posting methods exist on it."""

    def __init__(self, conn: sqlite3.Connection, client_id: str):
        self._conn, self._client = conn, client_id

    def accounts(self) -> dict[str, dict[str, Any]]:
        from ..ledger import store

        return store.accounts(self._conn, self._client)

    def entries(self, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
        from ..ledger import store

        return store.entries(self._conn, self._client, start, end, limit=100000)

    def balances(self, start: date, end: date) -> list[dict[str, Any]]:
        from ..ledger import store

        return store.balances(self._conn, self._client, start, end)

    def beancount(self) -> str:
        from ..ledger import store

        return store.to_beancount(self._conn, self._client)


BUILTINS = [
    Manifest(id="bank_csv", name="Bank / card CSV", kind="connector", builtin=True,
             description="Any bank or card CSV export (Chase, BofA, Amex, Wells…): learns categories from your history.",
             entry="veritas.plugins.builtin:bank_csv", permissions=["transactions:suggest"], config={"text": "CSV content"}),
    Manifest(id="ofx", name="OFX / QFX bank file", kind="connector", builtin=True,
             description="Open Financial Exchange downloads (Quicken/QuickBooks Web Connect format).",
             entry="veritas.plugins.builtin:ofx", permissions=["transactions:suggest"], config={"text": "OFX content"}),
    Manifest(id="folder_watch", name="Synced folder", kind="connector", builtin=True,
             description="Watch a OneDrive / Dropbox / Google Drive desktop folder; every file is ingested and filed.",
             entry="veritas.plugins.builtin:folder_watch", permissions=["documents:ingest"], config={"path": "Folder to watch"}),
    Manifest(id="stripe", name="Stripe payouts", kind="connector", builtin=True,
             description="Pulls Stripe balance transactions (charges, fees, refunds, payouts) for categorization.",
             entry="veritas.plugins.builtin:stripe", permissions=["network", "transactions:suggest"], network_domains=["api.stripe.com"],
             config={"api_key_env": "Env var holding a restricted read-only key (default STRIPE_API_KEY)"}),
    Manifest(id="mcp_bridge", name="Any MCP server", kind="connector", builtin=True,
             description="Connect to any external MCP server (QuickBooks, Gmail, Google Drive, banks, POS...) and pull "
                         "transactions or documents through the normal pipelines.",
             entry="veritas.plugins.builtin:mcp_bridge", permissions=["network", "transactions:suggest", "documents:ingest"],
             config={"command": "MCP server command", "args": "arguments", "tool": "tool to call", "arguments": "tool arguments",
                     "produces": "transactions | documents"}),
    Manifest(id="webhook_inbound", name="Universal webhook", kind="connector", builtin=True,
             description="HMAC-signed inbound webhook for Zapier, Make, n8n or any SaaS: send transactions or documents.",
             entry="veritas.plugins.builtin:webhook_inbound", permissions=["transactions:suggest", "documents:ingest"],
             config={"secret_env": "Env var with the shared HMAC secret (default VERITAS_WEBHOOK_SECRET)"}),
    Manifest(id="trial_balance_import", name="Migrate from QuickBooks / Xero (trial balance)", kind="importer", builtin=True,
             description="Import a trial balance CSV export as opening balances; maps accounts by name and type.",
             entry="veritas.plugins.builtin:trial_balance_import", permissions=["ledger:read", "ledger:opening_balances"],
             config={"text": "CSV with Account, Debit, Credit", "as_of": "Opening balance date"}),
    Manifest(id="quickbooks_iif_export", name="QuickBooks Desktop IIF export", kind="exporter", builtin=True,
             description="Journal entries in IIF for firms that still need to hand data to QuickBooks Desktop.",
             entry="veritas.plugins.builtin:iif_export", permissions=["ledger:read", "export"]),
    Manifest(id="tax_trial_balance_export", name="Tax software trial balance (CSV)", kind="exporter", builtin=True,
             description="Adjusted trial balance with M-1 adjustments, for import into UltraTax, Lacerte, Drake or ProConnect.",
             entry="veritas.plugins.builtin:tax_tb_export", permissions=["ledger:read", "rules:read", "export"]),
    Manifest(id="beancount_export", name="Beancount export", kind="exporter", builtin=True,
             description="Full ledger in Beancount plain-text format: your data is never locked in.",
             entry="veritas.plugins.builtin:beancount_export", permissions=["ledger:read", "export"]),
]


def discover(root: Path) -> dict[str, Manifest]:
    found = {m.id: m for m in BUILTINS}
    for mf in sorted((root / "plugins").glob("*/plugin.yaml")):
        m = Manifest.model_validate(yaml.safe_load(mf.read_text(encoding="utf-8")))
        found[m.id] = m
    try:
        from importlib.metadata import entry_points

        for ep in entry_points(group="veritas.plugins"):
            m = Manifest.model_validate(ep.load()())
            found.setdefault(m.id, m)
    except Exception:
        pass
    return found


def _resolve(m: Manifest, root: Path) -> Callable[[PluginContext], Any]:
    mod_name, fn = m.entry.split(":")
    local = root / "plugins" / m.id / f"{mod_name.split('.')[-1]}.py"
    if not m.builtin and local.exists():
        spec = importlib.util.spec_from_file_location(f"veritas_plugin_{m.id}", local)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    else:
        mod = importlib.import_module(mod_name)
    return getattr(mod, fn)


def run_plugin(foundry: Any, plugin_id: str, client_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    plugins = discover(foundry.paths.root)
    if plugin_id not in plugins:
        raise KeyError(f"unknown plugin {plugin_id}")
    m = plugins[plugin_id]
    ctx = PluginContext(m, foundry.conn, client_id, config or {}, foundry)
    audit.record(foundry.conn, f"plugin:{m.id}", "agent", "plugin.run", {"plugin": m.id, "kind": m.kind}, client_id=client_id)
    result = _resolve(m, foundry.paths.root)(ctx)
    return {"plugin": m.id, "result": result, **ctx.output}
