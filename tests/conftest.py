import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("AGENTLEDGER_AGENTS", "0")
REPO = Path(__file__).resolve().parent.parent


class FakeRouter:
    """Stands in for the model router: returns scripted structured outputs, never calls a model."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []
        self.frontier = type("F", (), {"available": lambda self: False, "model": "none"})()
        self.local = type("L", (), {"available": lambda self: False, "models": lambda self: []})()

    def structured(self, role, *, system, user, schema, images=None, escalate=False, client_id=None, effort="medium",
                   data_class="taxpayer"):
        self.calls.append((role, escalate))
        self.data_classes = getattr(self, "data_classes", []) + [data_class]
        key = (role, escalate) if (role, escalate) in self.responses else role
        if key not in self.responses:
            from agentledger.ai.router import Unavailable

            raise Unavailable(f"no scripted response for {role}")
        r = self.responses[key]
        return (r(user) if callable(r) else r), f"local:fake-{role}"

    def frontier_allowed(self):
        return False

    def stream(self, role, *, system, messages, client_id=None, data_class="taxpayer"):
        r = self.responses.get(("stream", role)) or self.responses.get("stream")
        if r is None:
            from agentledger.ai.router import Unavailable

            raise Unavailable("no stream")
        return iter([r]), f"local:fake-{role}"

    def _log(self, *a, **k):
        pass


@pytest.fixture
def home(tmp_path):
    for d in ("rules", "config", "golden", "domains", "playbooks", "evals"):
        shutil.copytree(REPO / d, tmp_path / d)
    shutil.copy(REPO / "pyproject.toml", tmp_path / "pyproject.toml")
    return tmp_path


@pytest.fixture
def foundry(home):
    from agentledger.db import connect
    from agentledger.foundry.core import Foundry

    return Foundry(home, connect(home / "state" / "agentledger.db"), router=FakeRouter())


@pytest.fixture
def biz(foundry):
    """A business client on the general pack with a little history."""
    from agentledger.domains.packs import Packs
    from agentledger.domains.service import onboard
    from agentledger.ledger import store

    store.add_client(foundry.conn, id="acme", name="Acme Fabrication LLC", kind="business", entity_type="s_corp",
                     emails=["owner@acme.example"], tax_id_last4="1234", domain="general",
                     facts={"employees": 5, "contractor_payments": True})
    onboard(foundry.conn, Packs(foundry.paths.domains), "acme", "general")
    return foundry
