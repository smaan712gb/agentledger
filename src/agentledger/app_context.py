"""One place that wires the platform together for the API, CLI, MCP server and agents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .brain.playbooks import Brain
from .db import open_store
from .domains.packs import Packs
from .foundry.core import Foundry


@dataclass
class AppContext:
    root: Path
    foundry: Foundry
    packs: Packs
    brain: Brain

    @property
    def conn(self):
        return self.foundry.conn

    @property
    def kb(self):
        return self.foundry.kb

    @property
    def router(self):
        return self.foundry.router

    @classmethod
    def open(cls, root: Path, *, tenant: Path | None = None, kb=None, scope: str = "all") -> "AppContext":
        """`tenant` is a firm's data directory; omitted, the firm's data lives under root (single-firm/dev)."""
        root = Path(root).resolve()
        data = Path(tenant).resolve() if tenant else root
        conn = open_store(data / "state" / "agentledger.db")
        foundry = Foundry(root, conn, tenant=tenant, kb=kb, scope=scope)
        return cls(root, foundry, Packs(root / "domains"), Brain(root / "playbooks", foundry.kb))

    def reload(self) -> None:
        self.foundry.kb.reload()
        self.packs = Packs(self.root / "domains")
        self.brain = Brain(self.root / "playbooks", self.foundry.kb)
