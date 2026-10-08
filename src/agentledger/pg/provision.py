"""Where each firm's store lives on PostgreSQL, and how it is created and destroyed (ADR-0002).

Two tenancy modes (AGENTLEDGER_PG_TENANCY):

* ``schema`` (default; development, tests, self-hosting): one database, a schema per firm (``firm_<id>``).
* ``database`` (production on Neon): a database per firm (``al_<id>``) on the configured branch, created and
  deleted through the Neon API. Firms share the endpoint and its roles, so the connection string is the
  configured one with the firm's database name; no per-firm credential is stored anywhere.

Either way the firm's tables live in a schema the migrations manage, and deleting a firm removes its store.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from .. import db
from . import connect, dsn

NEON_API = "https://console.neon.tech/api/v2"
FIRM_SCHEMA = "agentledger"           # the schema inside a firm's own database


class ProvisioningError(RuntimeError):
    pass


def tenancy() -> str:
    mode = os.environ.get("AGENTLEDGER_PG_TENANCY", "schema").strip().lower() or "schema"
    if mode not in ("schema", "database"):
        raise ProvisioningError(f"AGENTLEDGER_PG_TENANCY must be 'schema' or 'database', not {mode!r}")
    return mode


def firm_database(firm_id: str) -> str:
    return db.pg_name(f"al_{firm_id}")


def with_database(url: str, name: str) -> str:
    u = urlparse(url)
    return urlunparse(u._replace(path="/" + name))


def store_location(path: Path | str) -> tuple[str, str]:
    """(connection string, schema) for the store at `path`."""
    base = dsn(direct=True)
    if not base:
        raise ProvisioningError("AGENTLEDGER_DATABASE=postgres needs DATABASE_URL_UNPOOLED (or DATABASE_URL)")
    firm = db.firm_of(path)
    if firm and tenancy() == "database":
        return with_database(base, firm_database(firm)), FIRM_SCHEMA
    return base, db.schema_for(path)


# ------------------------------------------------------------------------------------------------- Neon API
class Neon:
    """The few Neon API calls tenancy needs. Configuration: NEON_API_KEY, NEON_PROJECT_ID and the branch as
    NEON_BRANCH_ID or NEON_BRANCH (its name)."""

    def __init__(self, *, key: str | None = None, project: str | None = None, branch: str | None = None,
                 client: Any = None, timeout: float = 120.0):
        import httpx

        self.key = key or os.environ.get("NEON_API_KEY", "")
        self.project = project or os.environ.get("NEON_PROJECT_ID", "")
        if not self.key or not self.project:
            raise ProvisioningError("database tenancy needs NEON_API_KEY and NEON_PROJECT_ID")
        self.http = client or httpx.Client(base_url=NEON_API, timeout=30,
                                           headers={"Authorization": f"Bearer {self.key}", "Accept": "application/json"})
        self.timeout = timeout
        self.branch = branch or os.environ.get("NEON_BRANCH_ID") or self._branch_id(os.environ.get("NEON_BRANCH", ""))

    def _call(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        r = self.http.request(method, path, **kw)
        if r.status_code >= 400:
            raise ProvisioningError(f"Neon API {method} {path}: {r.status_code} {r.text[:200]}")
        return r.json() if r.content else {}

    def _branch_id(self, name: str) -> str:
        if not name:
            raise ProvisioningError("set NEON_BRANCH_ID or NEON_BRANCH")
        for b in self._call("GET", f"/projects/{self.project}/branches").get("branches", []):
            if b.get("name") == name or b.get("id") == name:
                return b["id"]
        raise ProvisioningError(f"no Neon branch named {name!r} in the project")

    def _wait(self, body: dict[str, Any]) -> None:
        """Neon applies changes as operations; wait until they finish before using the database."""
        deadline = time.monotonic() + self.timeout
        for op in body.get("operations", []):
            while True:
                status = self._call("GET", f"/projects/{self.project}/operations/{op['id']}")["operation"]["status"]
                if status in ("finished", "skipped"):
                    break
                if status in ("failed", "error", "cancelled"):
                    raise ProvisioningError(f"Neon operation {op.get('action')} {status}")
                if time.monotonic() > deadline:
                    raise ProvisioningError(f"Neon operation {op.get('action')} did not finish in {self.timeout:.0f}s")
                time.sleep(1)

    def databases(self) -> list[str]:
        out = self._call("GET", f"/projects/{self.project}/branches/{self.branch}/databases")
        return [d["name"] for d in out.get("databases", [])]

    def create_database(self, name: str, owner: str) -> None:
        self._wait(self._call("POST", f"/projects/{self.project}/branches/{self.branch}/databases",
                              json={"database": {"name": name, "owner_name": owner}}))

    def delete_database(self, name: str) -> None:
        self._wait(self._call("DELETE", f"/projects/{self.project}/branches/{self.branch}/databases/{name}"))


# ------------------------------------------------------------------------------------------------- lifecycle
def provision(firm_id: str, *, neon: Neon | None = None) -> str:
    """Create the firm's store before the firm exists (a failure leaves nothing half-created)."""
    if tenancy() == "schema":
        return "schema tenancy: the firm schema is created and migrated on first use"
    neon = neon or Neon()
    name = firm_database(firm_id)
    if name in neon.databases():
        raise ProvisioningError(f"database {name} already exists; refusing to reuse another firm's data")
    neon.create_database(name, urlparse(dsn(direct=True) or "").username or "")
    from .compat import PgStore

    PgStore(FIRM_SCHEMA, url=with_database(dsn(direct=True), name)).close()     # apply the migrations now
    return f"Neon database {name} created and migrated"


def destroy(path: Path | str, *, neon: Neon | None = None) -> str:
    """Remove a firm's store permanently: close this process's connections, then drop the schema or delete the
    firm's database."""
    from .compat import location

    url, schema = store_location(path)
    db.close_stores(location(url, schema))
    firm = db.firm_of(path)
    if firm and tenancy() == "database":
        neon = neon or Neon()
        name = firm_database(firm)
        if name in neon.databases():
            neon.delete_database(name)
            return f"Neon database {name} deleted"
        return f"no Neon database {name}"
    owner = connect(url)
    try:
        existed = owner.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)).fetchone()
        owner.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        owner.close()
    return f"schema {schema} dropped" if existed else f"no schema {schema}"
