"""Where each firm's store lives on PostgreSQL, and how it is created and destroyed (ADR-0002).

Two tenancy modes (AGENTLEDGER_PG_TENANCY):

* ``schema`` (default; development, tests, self-hosting): one database, a schema per firm (``firm_<id>``).
* ``database`` (production on Neon): a database per firm (``al_<id>``) on the configured branch, created and
  deleted through the Neon API.

Either way each store has its own runtime login role (agentledger.pg.runtime_role) with privileges in that store
only, and deleting a firm removes the store and the role.

Provisioning is a durable operation recorded in a journal (the platform store): the intent is written before
anything is created, each step afterwards, and a retry resumes from the recorded step. A database is treated as ours
only when the journal says we were creating it; anything else with the same name is refused, never adopted or
deleted. A lease keeps concurrent retries apart, and `abandon` removes only what the journal says we created.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

from .. import db
from . import connect, database_of, drop_role, migration_url, runtime_base_url, runtime_role, with_database

NEON_API = "https://console.neon.tech/api/v2"
FIRM_SCHEMA = "agentledger"           # the schema inside a firm's own database
STEPS = ("creating", "created", "migrated", "ready")


class ProvisioningError(RuntimeError):
    pass


class ProvisioningBusy(ProvisioningError):
    """Another attempt holds the lease for this firm."""


def tenancy() -> str:
    mode = os.environ.get("AGENTLEDGER_PG_TENANCY", "schema").strip().lower() or "schema"
    if mode not in ("schema", "database"):
        raise ProvisioningError(f"AGENTLEDGER_PG_TENANCY must be 'schema' or 'database', not {mode!r}")
    return mode


def server_of(url: str) -> str:
    """The database server a URL names, the same for its pooled and direct endpoints (Neon: ep-x-pooler vs ep-x)."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    first, _, rest = host.partition(".")
    return (first.removesuffix("-pooler") + ("." + rest if rest else "")) + (f":{u.port}" if u.port else "")


def firm_database(firm_id: str) -> str:
    return db.pg_name(f"al_{firm_id}")


def store_location(path: Path | str) -> tuple[str, str]:
    """(server and database URL, schema) for the store at `path`. Credentials in the URL are not used."""
    base = runtime_base_url()
    if not base:
        raise ProvisioningError("AGENTLEDGER_DATABASE=postgres needs DATABASE_URL_UNPOOLED (or DATABASE_URL)")
    firm = db.firm_of(path)
    if firm and tenancy() == "database":
        return with_database(base, firm_database(firm)), FIRM_SCHEMA
    return base, db.schema_for(path)


class Journal(Protocol):
    """Durable provisioning records, one per firm (implemented by the platform store)."""

    def get(self, firm_id: str) -> dict[str, Any] | None: ...
    def put(self, firm_id: str, **fields: Any) -> None: ...
    def claim(self, firm_id: str, holder: str, seconds: int) -> bool: ...
    def release(self, firm_id: str, holder: str) -> None: ...


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
def _migrate_store(url: str, schema: str, own_database: bool) -> None:
    from .compat import PgStore

    PgStore(schema, url=url, own_database=own_database).close()


def provision(firm_id: str, journal: Journal, *, neon: Neon | None = None,
              migrate_store: Callable[[str, str, bool], None] | None = None, lease_s: int = 600) -> str:
    """Create the firm's store, resuming from the journal after a failure. Returns what was done."""
    holder = f"{os.getpid()}:{time.monotonic_ns()}"
    if not journal.claim(firm_id, holder, lease_s):
        raise ProvisioningBusy(f"provisioning of {firm_id} is already running")
    try:
        return _provision(firm_id, journal, neon, migrate_store or _migrate_store)
    except Exception as exc:
        journal.put(firm_id, error=f"{type(exc).__name__}: {exc}"[:300])
        raise
    finally:
        journal.release(firm_id, holder)


def _provision(firm_id: str, journal: Journal, neon: Neon | None, migrate_store: Callable[[str, str, bool], None]) -> str:
    base = runtime_base_url()
    if not base or not migration_url():
        raise ProvisioningError("provisioning needs the owner connection (AGENTLEDGER_MIGRATION_URL or DATABASE_URL_UNPOOLED)")
    database_mode = tenancy() == "database"
    if database_mode:
        resource, name = "database", firm_database(firm_id)
        url, schema = with_database(base, name), FIRM_SCHEMA
    else:
        resource, name = "schema", db.schema_for(Path("tenants") / firm_id / "state" / "agentledger.db")
        url, schema = base, name
    rec = journal.get(firm_id)
    if rec and (rec.get("resource"), rec.get("name")) != (resource, name):
        raise ProvisioningError(f"journal for {firm_id} records {rec.get('resource')} {rec.get('name')}, not {resource} {name}")
    state = rec["state"] if rec else None
    if state == "ready":
        return f"{resource} {name} already provisioned"
    if state == "removed":
        raise ProvisioningError(f"{resource} {name} was removed; a removed firm is not provisioned again")
    if database_mode:
        neon = neon or Neon()
        exists = name in neon.databases()
        if state is None:
            if exists:   # not ours: no record that we ever started creating it
                raise ProvisioningError(f"database {name} exists without a provisioning record; refusing to adopt it")
            journal.put(firm_id, resource=resource, name=name, database=name, server=server_of(base), state="creating",
                        error=None)
            state = "creating"
        if state == "creating":
            if not exists:   # a timeout after Neon accepted the request leaves it existing; then it is ours
                neon.create_database(name, urlparse(migration_url() or "").username or "")
            journal.put(firm_id, state="created")
            state = "created"
    elif state is None:
        owner = connect(with_database(migration_url() or "", database_of(base)))
        try:
            taken = owner.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,)).fetchone()
        finally:
            owner.close()
        if taken:   # not ours: no record that we ever started creating it (another firm's, or a leftover)
            raise ProvisioningError(f"schema {name} exists without a provisioning record; refusing to adopt it")
        journal.put(firm_id, resource=resource, name=name, database=database_of(base), server=server_of(base),
                    state="created", error=None)
        state = "created"
    if state == "created":
        migrate_store(url, schema, database_mode)        # idempotent: resumes from schema_migrations
        journal.put(firm_id, state="migrated")
        state = "migrated"
    journal.put(firm_id, state="ready", error=None)
    return f"{resource} {name} created and migrated"


def abandon(firm_id: str, journal: Journal, *, neon: Neon | None = None) -> str:
    """Remove what an unfinished provisioning created, and nothing else."""
    rec = journal.get(firm_id)
    if not rec or rec["state"] in ("removed",):
        return "nothing recorded"
    if rec["state"] == "ready":
        raise ProvisioningError("a provisioned firm is removed by deleting the firm, not by abandoning provisioning")
    detail = _remove(rec["resource"], rec["name"], neon=neon)
    journal.put(firm_id, state="removed")
    return detail


def _remove(resource: str, name: str, *, neon: Neon | None = None) -> str:
    owner_url = migration_url()
    base = runtime_base_url()
    if not owner_url or not base:
        raise ProvisioningError("removing a store needs the owner connection")
    if resource == "database":
        role = runtime_role(name, FIRM_SCHEMA)
        neon = neon or Neon()
        done = f"Neon database {name} deleted" if name in neon.databases() else f"no Neon database {name}"
        if name in neon.databases():
            neon.delete_database(name)
    else:
        role = runtime_role(urlparse(base).path.lstrip("/") or "postgres", name)
        owner = connect(owner_url)
        try:
            existed = owner.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,)).fetchone()
            owner.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        finally:
            owner.close()
        done = f"schema {name} dropped" if existed else f"no schema {name}"
    owner = connect(owner_url)
    try:
        drop_role(owner, role)
    finally:
        owner.close()
    return done + f"; runtime role {role} dropped"


def store_exists(path: Path | str) -> bool:
    """Whether the firm's schema or database exists. Answers only from a definite catalog lookup or a definite
    'database does not exist'; any other failure is raised (callers that protect evidence fail closed)."""
    import psycopg

    from . import runtime_role, runtime_url

    url, schema = store_location(path)
    database = urlparse(url).path.lstrip("/") or "postgres"
    owner_url = migration_url()
    if owner_url:
        owner = connect(with_database(owner_url, urlparse(owner_url).path.lstrip("/") or "postgres"))
        try:
            if not owner.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone():
                return False
        finally:
            owner.close()
        probe = connect(with_database(owner_url, database))
    else:
        try:
            probe = connect(runtime_url(url, runtime_role(database, schema)))
        except psycopg.OperationalError as exc:
            if "does not exist" in str(exc):
                return False
            raise
    try:
        return bool(probe.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)).fetchone())
    finally:
        probe.close()


def destroy(path: Path | str, *, neon: Neon | None = None) -> str:
    """Remove a firm's store permanently: close this process's connections, then drop the schema or delete the
    firm's database, and drop its runtime role."""
    from .compat import location

    url, schema = store_location(path)
    db.close_stores(location(url, schema))
    firm = db.firm_of(path)
    if firm and tenancy() == "database":
        return _remove("database", firm_database(firm), neon=neon)
    return _remove("schema", schema)
