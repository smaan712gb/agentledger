"""One place that wires the platform together for the API, CLI, MCP server and agents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .brain.playbooks import Brain
from .db import ThreadLocalConnection
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
    def open(cls, root: Path) -> "AppContext":
        root = Path(root).resolve()
        conn = ThreadLocalConnection(root / "state" / "veritas.db")
        foundry = Foundry(root, conn)
        return cls(root, foundry, Packs(root / "domains"), Brain(root / "playbooks", foundry.kb))

    def reload(self) -> None:
        self.foundry.kb.reload()
        self.packs = Packs(self.root / "domains")
        self.brain = Brain(self.root / "playbooks", self.foundry.kb)
