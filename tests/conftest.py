import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("AGENTLEDGER_AGENTS", "0")
# Tests never touch real services configured in a developer's .env (WorkOS sign-in, the production R2 bucket). These
# are pinned before any test module loads .env, and .env loading never overrides a variable that is already set; a
# test that needs one of these services sets it explicitly (monkeypatch) for itself.
os.environ["AGENTLEDGER_IDENTITY"] = "local"
os.environ["AGENTLEDGER_BLOBS"] = "file"
for _var in ("AGENTLEDGER_BLOB_ENDPOINT", "AGENTLEDGER_BLOB_BUCKET", "AGENTLEDGER_BLOB_ACCESS_KEY_ID",
             "AGENTLEDGER_BLOB_SECRET_ACCESS_KEY", "AGENTLEDGER_BLOB_PREFIX", "WORKOS_REDIRECT_URI"):
    os.environ[_var] = ""
# The API refuses SQLite outside dev mode; the suite exercises production mode on both backends.
os.environ.setdefault("AGENTLEDGER_ALLOW_SQLITE", "1")
# Tests never reach a real local model, even when Ollama is running on the machine.
os.environ["AGENTLEDGER_OLLAMA_URL"] = "http://127.0.0.1:9"
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


@pytest.fixture(autouse=True)
def _firm_store_backend(monkeypatch):
    """With AGENTLEDGER_DATABASE=postgres the whole suite runs on PostgreSQL: each test's firm stores live in
    schemas under a unique prefix, dropped afterwards. Otherwise each test uses SQLite files in its tmp dir."""
    from agentledger import db

    if db.backend() != "postgres":
        yield
        return
    import secrets

    from agentledger import envfile, pg

    envfile.load(REPO)
    prefix = "t" + secrets.token_hex(4) + "_"
    monkeypatch.setenv("AGENTLEDGER_PG_SCHEMA_PREFIX", prefix)
    url = pg.dsn(direct=True)        # captured now: a test may point DATABASE_URL elsewhere while it runs
    yield
    from agentledger.pg import compat

    compat.close_all(prefix)
    _drop_test_objects(url, prefix)


def _drop_test_objects(url: str, prefix: str, attempts: int = 6) -> None:
    """Drop this test's schemas and runtime roles. Several suites share one PostgreSQL server (parallel work), and
    concurrent DROP ROLE / DROP SCHEMA statements can fail on catalog contention ("tuple concurrently updated",
    deadlocks): those transient errors are retried; anything else is raised."""
    import time

    import psycopg

    from agentledger import pg

    for attempt in range(attempts):
        owner = pg.connect(url)
        try:
            for (name,) in owner.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE %s", (prefix + "%",)).fetchall():
                owner.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
            for (role,) in owner.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE %s", ("rt\\_%" + prefix + "%",)).fetchall():
                pg.drop_role(owner, role)
            return
        except (psycopg.errors.InternalError, psycopg.errors.DeadlockDetected, psycopg.errors.ObjectInUse,
                psycopg.errors.LockNotAvailable):
            if attempt == attempts - 1:
                raise
            time.sleep(0.2 * (attempt + 1))
        finally:
            owner.close()


@pytest.fixture
def foundry(home):
    from agentledger.db import open_store
    from agentledger.foundry.core import Foundry

    return Foundry(home, open_store(home / "state" / "agentledger.db"), router=FakeRouter())


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
