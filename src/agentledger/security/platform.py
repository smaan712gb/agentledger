"""Platform identity: firms, users, invitations, MFA and sessions.

Controls (FTC Safeguards Rule 16 CFR 314.4(c); IRS Pub. 4557 and Pub. 1345):
- Every login needs a password (Argon2id) and a second factor. No session is ever issued
  without MFA. Enrolment is part of accepting an invitation.
- Five failed attempts lock the account for 15 minutes.
- Sessions expire after 30 idle minutes and 12 hours in total. Only a hash of each token is
  stored, so a database leak does not reveal live sessions.
- Every authentication event goes to an append-only log.
- MFA secrets are encrypted with the firm's data key.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import shutil
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .. import db
from ..evidence import offboarding
from . import totp
from .crypto import Keyring, load_master_key

PLATFORM_FIRM = "_platform"
# Ids no firm may take: the single-firm development store uses "dev" (its objects live under firms/dev/).
RESERVED_FIRM_IDS = frozenset({PLATFORM_FIRM, "dev"})
OFFBOARDING_LEASE = timedelta(hours=6)


class _Cancelled(Exception):
    """An offboarding run saw a cancellation before its store's removal began."""


ROLES =("platform_admin", "firm_admin", "cpa", "staff", "client")
FIRM_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=15)
IDLE = timedelta(minutes=30)
ABSOLUTE = timedelta(hours=12)
CHALLENGE_TTL = timedelta(minutes=5)
FRESH = timedelta(minutes=5)          # consequential actions need a sign-in or step-up this recent
IDP_STATE_TTL = timedelta(minutes=10)
NO_PASSWORD = "!idp"                  # accounts that sign in only through the identity provider
INVITE_TTL = timedelta(days=7)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS firms (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')), deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS firm_keys (
    firm_id TEXT NOT NULL, version INTEGER NOT NULL, wrapped BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), destroyed_at TEXT,
    PRIMARY KEY (firm_id, version)
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('platform_admin','firm_admin','cpa','staff','client')),
    client_id TEXT, password_hash TEXT NOT NULL, totp_secret TEXT, totp_last_step INTEGER,
    mfa_enrolled_at TEXT, failed_logins INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
    disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT (datetime('now')), last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS invites (
    token_hash TEXT PRIMARY KEY, firm_id TEXT NOT NULL, email TEXT NOT NULL, role TEXT NOT NULL, client_id TEXT,
    created_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')), expires_at TEXT NOT NULL, accepted_at TEXT
);
CREATE TABLE IF NOT EXISTS challenges (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('mfa','enroll')),
    pending_secret TEXT, expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL, ip TEXT, user_agent TEXT, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS auth_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL DEFAULT (datetime('now')), firm_id TEXT, user_id TEXT,
    email TEXT, event TEXT NOT NULL, ip TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS engagement_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT, firm_id TEXT NOT NULL, user_id TEXT NOT NULL, client_id TEXT NOT NULL,
    granted_by TEXT NOT NULL, granted_at TEXT NOT NULL DEFAULT (datetime('now')), revoked_by TEXT, revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS engagement_grants_user ON engagement_grants (user_id, client_id);
-- What a deleted firm's store held, kept after the store is destroyed (holds, counts, audit head).
CREATE TABLE IF NOT EXISTS firm_offboarding (
    id INTEGER PRIMARY KEY AUTOINCREMENT, firm_id TEXT NOT NULL, at TEXT NOT NULL DEFAULT (datetime('now')),
    by TEXT NOT NULL, reason TEXT NOT NULL, summary TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS firm_offboarding_no_update BEFORE UPDATE ON firm_offboarding
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS firm_offboarding_no_delete BEFORE DELETE ON firm_offboarding
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
-- How far a firm's destruction got (sealed, store_removed, blobs_removed, tenant_removed, key_shredded): a retry
-- resumes, and a store recorded as removed is never mistaken for one that cannot be found.
-- One offboarding run at a time per firm (a lease), and a cancellation requested while one runs.
CREATE TABLE IF NOT EXISTS offboarding_runs (
    firm_id TEXT PRIMARY KEY, holder TEXT, lease_until TEXT, cancel_requested_at TEXT, cancel_by TEXT, cancel_reason TEXT
);
CREATE TABLE IF NOT EXISTS firm_destruction (
    firm_id TEXT NOT NULL, stage TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (firm_id, stage)
);
CREATE TABLE IF NOT EXISTS sso_mfa_attestations (
    organization_id TEXT PRIMARY KEY, firm_id TEXT NOT NULL, evidence TEXT NOT NULL, attested_by TEXT NOT NULL,
    attested_at TEXT NOT NULL DEFAULT (datetime('now')), revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS idp_states (
    state_hash TEXT PRIMARY KEY, purpose TEXT NOT NULL CHECK (purpose IN ('login','invite','link','step_up')),
    invite_hash TEXT, session_hash TEXT, verifier TEXT NOT NULL, redirect_uri TEXT NOT NULL,
    expires_at TEXT NOT NULL, used_at TEXT
);
CREATE TABLE IF NOT EXISTS provisioning (
    firm_id TEXT PRIMARY KEY, resource TEXT, name TEXT,
    state TEXT CHECK (state IN ('creating','created','migrated','ready','removed')),
    holder TEXT, lease_until TEXT, error TEXT, updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TRIGGER IF NOT EXISTS auth_events_no_update BEFORE UPDATE ON auth_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS auth_events_no_delete BEFORE DELETE ON auth_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""


def client_scope(user: dict[str, Any]) -> list[str]:
    """The clients a signed-in user's database session may see (row-level security on PostgreSQL). A client user
    sees one business; staff see the clients they are engaged on; CPAs and firm administrators see the firm."""
    role = user.get("base_role", user["role"])
    if role == "client":
        return [user["client_id"]] if user.get("client_id") else []
    if role == "staff":
        return list(user.get("engaged") or [])
    return ["*"]


class AuthError(Exception):
    """Raised with a message that is safe to show to the person signing in."""


class _Refused(Exception):
    """Raised inside a critical section (Platform._critical) to refuse the operation once what the section recorded
    (the refusal's event, a failure count) is committed: the AuthError it carries is raised after the unit of work."""

    def __init__(self, error: AuthError):
        super().__init__(str(error))
        self.error = error


def platform_backend() -> str:
    """Where the platform store lives: AGENTLEDGER_PLATFORM_DATABASE ('sqlite' or 'postgres'), else the firm stores'
    backend (db.backend()). The override exists for tests that put firm stores on a database they never reach."""
    value = (os.environ.get("AGENTLEDGER_PLATFORM_DATABASE") or db.backend()).strip().lower() or db.backend()
    if value not in ("sqlite", "postgres"):
        raise ValueError(f"AGENTLEDGER_PLATFORM_DATABASE must be 'sqlite' or 'postgres', not {value!r}")
    return value


def _open_platform_sqlite(path: Path) -> sqlite3.Connection:
    """One connection to the SQLite platform store, schema applied (idempotent) and its per-connection pragmas set.
    `check_same_thread=False` only so the store can close every thread's connection from one place."""
    c = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def migrate_platform() -> list[str]:
    """Create or upgrade the platform schema and its runtime role on PostgreSQL as the owner (`agentledger platform
    migrate`: a release step, before the API starts). Returns the migration files applied. The schema lives in the
    database runtime connections use (AGENTLEDGER_RUNTIME_DATABASE_URL, else the owner URL's); the owner credentials
    come from AGENTLEDGER_MIGRATION_URL (else DATABASE_URL_UNPOOLED). No master key is needed."""
    from ..pg import PLATFORM_MIGRATIONS, PLATFORM_SCHEMA, connect, database_of, migrate, migration_url, runtime_base_url, with_database

    owner_url = migration_url()
    if not owner_url:
        raise RuntimeError("migrating the platform store needs owner credentials (AGENTLEDGER_MIGRATION_URL or DATABASE_URL_UNPOOLED)")
    owner = connect(with_database(owner_url, database_of(runtime_base_url() or owner_url)))
    try:
        return migrate(owner, db.pg_name(PLATFORM_SCHEMA), migrations=PLATFORM_MIGRATIONS)
    finally:
        owner.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def password_problems(password: str, email: str = "") -> list[str]:
    out = []
    if len(password) < 12:
        out.append("use at least 12 characters")
    local = email.split("@")[0].lower() if email else ""
    if len(local) >= 4 and local in password.lower():
        out.append("do not include your email name")
    if password.lower() in {"password1234", "123456789012", "qwertyuiopas", "letmein12345", "veritas12345", "agentledger1234"}:
        out.append("choose a less common password")
    if len(set(password)) < 5:
        out.append("use more varied characters")
    return out


@dataclass
class Enrolment:
    challenge: str
    secret: str
    uri: str


class Platform:
    """The platform store: on PostgreSQL (platform_backend() == "postgres") the schema `platform` of the admin database,
    opened as its own runtime role with one connection per thread (pg.compat.PgStore; a container's connections to the
    direct endpoint number its request threads); otherwise the SQLite file state/platform.db on one shared connection.

    Read-modify-write sections (a firm's status, an account's failure count, an invitation's single use) run in
    `_critical`: one unit of work serialized across every process on a key, so two API containers never both admit
    the same invitation or lose a failed-login count."""

    def __init__(self, root: Path, *, dev: bool = False, identity: str = "local", need_keys: bool = True):
        """`need_keys=False` is for jobs that hold owner database credentials and not the master key (the release
        step's `platform migrate`, the provisioning worker): the master key is not loaded, no data key is created,
        and `self.keys` refuses with a clear error."""
        self.root = Path(root)
        if "tenants" in Path(root).resolve().parts:
            raise ValueError("the platform root must not lie inside a directory named 'tenants': firm stores are located "
                             "by their tenants/<firm> path")
        # "workos": firm users sign in only through the identity provider; the password + TOTP stack stays for
        # platform administrators (break-glass). "local": password + TOTP for everyone (self-hosting).
        if identity not in ("local", "workos"):
            raise ValueError("identity must be 'local' or 'workos'")
        self.identity = identity
        state = self.root / "state"
        # The master key first: a deployment without one refuses to start before any store is created or touched.
        master = load_master_key(state, allow_dev_file=dev) if need_keys else None
        self.backend = platform_backend()
        self.lock = threading.RLock()     # SQLite: one shared connection, one critical section at a time in this process
        self.conn: Any
        if self.backend == "postgres":
            from ..pg import PLATFORM_MIGRATIONS, PLATFORM_SCHEMA, runtime_base_url
            from ..pg.compat import PgStore

            # With owner credentials in the environment (development, the release and provisioning jobs) the schema
            # is migrated first; the API holds none and needs `agentledger platform migrate` to have run. Nothing is
            # written under state/ (the development master key file apart).
            self.conn = PgStore(db.pg_name(PLATFORM_SCHEMA), url=runtime_base_url(), migrations=PLATFORM_MIGRATIONS)
            self._check_open()
        else:
            state.mkdir(parents=True, exist_ok=True)
            # One connection per thread: a critical section's transaction (BEGIN IMMEDIATE on this thread's
            # connection) never captures another request's statements, so a rolled-back section loses only its own.
            self.conn = db.ThreadLocalConnection(state / "platform.db", opener=_open_platform_sqlite)
            self._upgrade()
        self._keys = Keyring(self.conn, master) if master is not None else None
        self.ph = PasswordHasher()
        if self._keys is not None and not self.conn.execute("SELECT 1 FROM firm_keys WHERE firm_id = ?", (PLATFORM_FIRM,)).fetchone():
            try:
                self._keys.create(PLATFORM_FIRM)
            except sqlite3.IntegrityError:    # created meanwhile by another process (two containers booting at once)
                pass

    @property
    def keys(self) -> Keyring:
        if self._keys is None:
            raise RuntimeError("this job runs without the master key (Platform(need_keys=False)): firm data keys cannot be "
                               "created, read or destroyed here")
        return self._keys

    def _check_open(self) -> None:
        """The first statement as the runtime role, so a platform schema that was never migrated (no role, no tables)
        is reported with the step that creates it, not as a login failure in the first request."""
        try:
            self.conn.execute("SELECT 1 FROM firm_keys WHERE firm_id = ?", (PLATFORM_FIRM,)).fetchone()
        except Exception as exc:
            import psycopg

            if not isinstance(exc, (psycopg.OperationalError, db.DatabaseError)):
                raise
            raise RuntimeError(
                f"the platform store ({self.conn.location}) could not be opened as its runtime role {self.conn.role}: {exc}. "
                "If the platform schema was never created, run `agentledger platform migrate` with owner credentials "
                "(AGENTLEDGER_MIGRATION_URL); otherwise check AGENTLEDGER_RUNTIME_DATABASE_URL and AGENTLEDGER_DB_ROLE_KEY") from exc

    def close(self) -> None:
        """Close the store's connections (command-line jobs, tests); the object cannot be used afterwards."""
        self.conn.close()

    @contextmanager
    def _critical(self, key: str) -> Iterator[None]:
        """A read-modify-write section as one unit of work, serialized on `key` ("firm:<id>", "user:<id>", ...) across
        every process: on PostgreSQL a transaction holding an advisory lock (db.lock; keyed by schema, so the firm
        stores never contend), on SQLite this process's lock and the database's write lock (BEGIN IMMEDIATE). Nested
        sections join the outer one. A `_Refused` raised inside commits what the section recorded (the refusal's
        event, a failure count) and raises its AuthError afterwards; any other exception rolls the section back."""
        refused: AuthError | None = None
        with self.lock if self.backend != "postgres" else nullcontext():
            with db.unit_of_work(self.conn):
                if self.backend == "postgres":
                    db.lock(self.conn, key)
                try:
                    yield
                except _Refused as r:
                    refused = r.error
        if refused is not None:
            raise refused

    def _upgrade(self) -> None:
        """Columns added after the first release, for platform stores created before them (SQLite only: on PostgreSQL
        the schema comes from pg/migrations/platform, which has them from the start)."""
        for table, col, ddl in (("users", "idp_subject", "TEXT"), ("users", "reviewer", "INTEGER NOT NULL DEFAULT 0"),
                                ("users", "reviewer_credential", "TEXT"), ("sessions", "auth_method", "TEXT NOT NULL DEFAULT 'password+totp'"),
                                ("sessions", "stepped_up_at", "TEXT"),
                                ("sessions", "authenticated_at", "TEXT"), ("idp_states", "browser_hash", "TEXT"),
                                ("firms", "offboarding_from", "TEXT"), ("provisioning", "database", "TEXT"),
                                ("provisioning", "server", "TEXT"), ("firms", "blob_location", "TEXT")):
            if col not in {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_idp_subject ON users (idp_subject) WHERE idp_subject IS NOT NULL")

    # ------------------------------------------------------------------ audit
    def event(self, event: str, *, firm_id: str | None = None, user_id: str | None = None, email: str | None = None,
              ip: str | None = None, detail: str = "") -> None:
        self.conn.execute("INSERT INTO auth_events (firm_id, user_id, email, event, ip, detail) VALUES (?, ?, ?, ?, ?, ?)",
                          (firm_id, user_id, email, event, ip, detail))

    def events(self, firm_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        q = "SELECT * FROM auth_events" + (" WHERE firm_id = ?" if firm_id else "") + " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in self.conn.execute(q, ((firm_id, limit) if firm_id else (limit,)))]

    # ------------------------------------------------------------------ firms
    def tenant_dir(self, firm_id: str) -> Path:
        return self.root / "tenants" / firm_id

    def create_firm(self, firm_id: str, name: str, *, by: str) -> dict[str, Any]:
        """Create a firm, or resume one whose store provisioning did not finish. The firm stays in status
        'provisioning' (nobody can use it) until its store is ready; a failure is recorded and the same call retries."""
        if not FIRM_ID.fullmatch(firm_id) or firm_id in RESERVED_FIRM_IDS:
            raise AuthError("firm id must be 2-41 lowercase letters, digits or hyphens, and not a reserved name")
        with self._critical(f"firm:{firm_id}"):
            row = self.conn.execute("SELECT status FROM firms WHERE id = ?", (firm_id,)).fetchone()
            if row and row["status"] != "provisioning":
                raise AuthError("that firm id is taken")
            if not row:
                status = "provisioning" if db.backend() == "postgres" else "active"
                try:
                    self.conn.execute("INSERT INTO firms (id, name, status) VALUES (?, ?, ?)", (firm_id, name, status))
                except sqlite3.IntegrityError as exc:       # inserted meanwhile by a writer not holding this lock
                    raise AuthError("that firm id is taken") from exc
                self.keys.create(firm_id)
                self.tenant_dir(firm_id).mkdir(parents=True, exist_ok=True)
                if db.backend() != "postgres":   # the store exists from the start: a missing one is never "no holds"
                    db.connect(self._store_path(firm_id)).close()
                from ..evidence.blobs import location_of

                # Where its objects live, recorded once: offboarding removes them from there, whatever its own settings.
                self.conn.execute("UPDATE firms SET blob_location = ? WHERE id = ?", (location_of(firm_id), firm_id))
                self.event("firm_created", firm_id=firm_id, user_id=by, detail=name)
        if db.backend() == "postgres":   # outside the lock: provisioning talks to the database service
            from ..pg import migration_url, provision

            if not migration_url():
                # Production API processes hold no owner credentials: the provisioning worker
                # (`agentledger platform provision`) creates the store; the firm stays unusable until then.
                self.event("firm_store_provisioning_queued", firm_id=firm_id, user_id=by)
                return self.firm(firm_id)
            try:
                detail = provision.provision(firm_id, self)
            except Exception as exc:
                self.event("firm_store_provisioning_failed", firm_id=firm_id, user_id=by, detail=f"{type(exc).__name__}: {exc}"[:300])
                raise
            self._admit_store(firm_id, by=by)
            self.conn.execute("UPDATE firms SET status = 'active' WHERE id = ? AND status = 'provisioning'", (firm_id,))
            self.event("firm_store_provisioned", firm_id=firm_id, user_id=by, detail=detail)
        return self.firm(firm_id)

    def provision_pending(self, *, by: str) -> list[dict[str, Any]]:
        """The provisioning worker: run with owner credentials (a release or operations job, never the API)."""
        out = []
        for f in self.conn.execute("SELECT id, name FROM firms WHERE status = 'provisioning' ORDER BY created_at").fetchall():
            try:
                out.append({"firm": f["id"], "status": self.create_firm(f["id"], f["name"], by=by)["status"]})
            except Exception as exc:  # recorded by create_firm; the next run resumes from the journal
                out.append({"firm": f["id"], "status": "provisioning", "error": f"{type(exc).__name__}: {exc}"[:300]})
        return out

    def abandon_firm(self, firm_id: str, *, by: str) -> str:
        """Give up on a firm whose provisioning never finished: remove what provisioning recorded creating, shred its
        key and retire the id."""
        self._not_reserved(firm_id)
        if self.firm(firm_id)["status"] != "provisioning":
            raise AuthError("only a firm that is still provisioning can be abandoned")
        rec = self.get(firm_id)
        if rec and rec.get("state") == "ready":
            raise AuthError("this firm's store is provisioned: let the provisioning worker activate the firm, then delete it")
        ref = self._store_ref(firm_id, allow_unrecorded=True)
        if ref is not None:
            try:
                offboarding.seal(ref, allow_missing=True)      # a store provisioning never finished cannot hold anything
            except offboarding.Held as e:
                self.event("firm_abandon_refused", firm_id=firm_id, user_id=by, detail=str(e)[:300])
                raise AuthError(f"{e}: a CPA must release each with a reason before the firm can be abandoned") from e
            except Exception as exc:
                self._unseal_or_record(firm_id, ref, by=by)      # the seal may have landed before the failure
                self.event("firm_abandon_refused", firm_id=firm_id, user_id=by, detail=f"store unreadable: {exc}"[:300])
                raise AuthError("the firm's store could not be read to check legal holds; refusing to abandon the firm") from exc
        from ..pg import provision

        try:
            detail = provision.abandon(firm_id, self)
        except BaseException:
            if ref is not None:                                  # the firm may yet be provisioned and used
                self._unseal_or_record(firm_id, ref, by=by)
            raise
        with self._critical(f"firm:{firm_id}"):
            self.conn.execute("UPDATE firms SET status = 'deleted', deleted_at = datetime('now') WHERE id = ?", (firm_id,))
            self.keys.destroy(firm_id)
        self.event("firm_abandoned", firm_id=firm_id, user_id=by, detail=detail)
        return detail

    def _unseal_or_record(self, firm_id: str, ref: offboarding.StoreRef, *, by: str) -> bool:
        """Unseal a store after an abandonment stopped; when the same outage stops the unseal, record that the store is
        left sealed (the firm's activation unseals it, or keeps the firm out of use)."""
        try:
            offboarding.unseal(ref)
            return True
        except Exception as exc:
            self.event("firm_abandon_stuck", firm_id=firm_id, user_id=by,
                       detail=f"store left sealed, could not unseal: {type(exc).__name__}: {exc}"[:300])
            return False

    def _admit_store(self, firm_id: str, *, by: str) -> None:
        """Before a provisioned firm goes into use: a store left sealed against legal holds (by an abandonment the same
        outage stopped before it could unseal) is unsealed first, or the firm stays out of use."""
        ref = self._store_ref(firm_id, allow_unrecorded=True)
        if ref is None or not offboarding.sealed(ref):
            return
        try:
            offboarding.unseal(ref)
        except Exception as exc:
            self.event("firm_store_sealed", firm_id=firm_id, user_id=by,
                       detail=f"could not unseal: {type(exc).__name__}: {exc}"[:300])
            raise AuthError("this firm's store is sealed against legal holds (an abandonment stopped midway) and could not "
                            "be unsealed; the firm stays out of use") from exc
        self.event("firm_store_unsealed", firm_id=firm_id, user_id=by, detail="left sealed by an abandonment that stopped midway")

    # ------------------------------------------------------------------ provisioning journal (pg.provision.Journal)
    def get(self, firm_id: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM provisioning WHERE firm_id = ?", (firm_id,)).fetchone()
        return dict(r) if r and r["state"] else None

    def put(self, firm_id: str, **fields: Any) -> None:
        allowed = {"resource", "name", "database", "server", "state", "error"}
        if set(fields) - allowed:
            raise ValueError(f"unknown provisioning fields {set(fields) - allowed}")
        self.conn.execute("INSERT INTO provisioning (firm_id) VALUES (?) ON CONFLICT (firm_id) DO NOTHING", (firm_id,))
        sets = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = datetime('now')"
        self.conn.execute(f"UPDATE provisioning SET {sets} WHERE firm_id = ?", (*fields.values(), firm_id))

    def claim(self, firm_id: str, holder: str, seconds: int) -> bool:
        with self._critical(f"provisioning:{firm_id}"):
            self.conn.execute("INSERT INTO provisioning (firm_id) VALUES (?) ON CONFLICT (firm_id) DO NOTHING", (firm_id,))
            cur = self.conn.execute("UPDATE provisioning SET holder = ?, lease_until = ? WHERE firm_id = ? "
                                    "AND (lease_until IS NULL OR lease_until < ?)",
                                    (holder, _iso(_now() + timedelta(seconds=seconds)), firm_id, _iso(_now())))
            return cur.rowcount == 1

    def release(self, firm_id: str, holder: str) -> None:
        self.conn.execute("UPDATE provisioning SET holder = NULL, lease_until = NULL WHERE firm_id = ? AND holder = ?",
                          (firm_id, holder))

    def firm(self, firm_id: str) -> dict[str, Any]:
        r = self.conn.execute("SELECT * FROM firms WHERE id = ?", (firm_id,)).fetchone()
        if not r:
            raise AuthError("firm not found")
        return dict(r)

    def firms(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM firms WHERE deleted_at IS NULL ORDER BY name")]

    def _store_path(self, firm_id: str) -> Path:
        return self.tenant_dir(firm_id) / "state" / "agentledger.db"

    def _store_ref(self, firm_id: str, *, allow_unrecorded: bool = False) -> offboarding.StoreRef | None:
        """Where the firm's store is, from the platform's own records: the provisioning journal (PostgreSQL) or the
        tenant directory (SQLite), never from the environment of whoever runs the offboarding. A job connected to
        another database server than the one recorded is refused."""
        if db.backend() != "postgres":
            return offboarding.StoreRef("sqlite", path=self._store_path(firm_id))
        rec = self.get(firm_id)
        if not rec or not rec.get("resource") or not rec.get("name"):
            if allow_unrecorded:
                return None
            raise AuthError("no provisioning record says where this firm's store is; refusing to destroy anything")
        from ..pg import database_of, migration_url, runtime_base_url
        from ..pg.provision import FIRM_SCHEMA, server_of

        here = server_of(migration_url() or runtime_base_url() or "")
        if rec.get("server") and here and rec["server"] != here:
            raise AuthError(f"this firm's store was provisioned on {rec['server']}, but this job connects to {here}: refusing")
        if rec["resource"] == "database":
            return offboarding.StoreRef("postgres", resource="database", database=rec["name"], schema=FIRM_SCHEMA)
        return offboarding.StoreRef("postgres", resource="schema", schema=rec["name"],
                                    database=rec.get("database") or database_of(runtime_base_url() or ""))

    def store_ref(self, firm_id: str) -> offboarding.StoreRef:
        """Where the firm's store is, from the platform's records, for jobs that read it in place (the restore drill,
        backlog F-13). Refused when no record says where it is."""
        ref = self._store_ref(firm_id)
        assert ref is not None
        return ref

    def _not_reserved(self, firm_id: str) -> None:
        if firm_id in RESERVED_FIRM_IDS:
            raise AuthError(f"{firm_id!r} is a reserved id (the single-firm store's objects share its prefix): a firm with "
                            "this id is removed by hand after checking that store's legal holds")

    def _stages(self, firm_id: str) -> set[str]:
        return {r["stage"] for r in self.conn.execute("SELECT stage FROM firm_destruction WHERE firm_id = ?", (firm_id,))}

    def _stage(self, firm_id: str, stage: str, detail: str = "") -> None:
        self.conn.execute("INSERT INTO firm_destruction (firm_id, stage, detail) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                          (firm_id, stage, detail[:500]))

    # The offboarding run's lease: one run at a time, and a cancellation never lands in the middle of one.
    def _claim_run(self, firm_id: str, holder: str) -> bool:
        with self._critical(f"offboarding:{firm_id}"):
            self.conn.execute("INSERT INTO offboarding_runs (firm_id) VALUES (?) ON CONFLICT (firm_id) DO NOTHING", (firm_id,))
            cur = self.conn.execute("UPDATE offboarding_runs SET holder = ?, lease_until = ? WHERE firm_id = ? "
                                    "AND (holder IS NULL OR lease_until < ?)",
                                    (holder, _iso(_now() + OFFBOARDING_LEASE), firm_id, _iso(_now())))
            return cur.rowcount == 1

    def _renew_run(self, firm_id: str, holder: str) -> None:
        self.conn.execute("UPDATE offboarding_runs SET lease_until = ? WHERE firm_id = ? AND holder = ?",
                          (_iso(_now() + OFFBOARDING_LEASE), firm_id, holder))

    def _release_run(self, firm_id: str, holder: str) -> None:
        self.conn.execute("UPDATE offboarding_runs SET holder = NULL, lease_until = NULL WHERE firm_id = ? AND holder = ?",
                          (firm_id, holder))

    def _cancel_request(self, firm_id: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM offboarding_runs WHERE firm_id = ? AND cancel_requested_at IS NOT NULL",
                              (firm_id,)).fetchone()
        return dict(r) if r else None

    def _begin_removal(self, firm_id: str) -> bool:
        """Record that the store is about to be removed, unless a cancellation was requested: one statement, so a
        cancellation either lands first (and the run stops) or is refused (the store is going)."""
        cur = self.conn.execute(
            "INSERT INTO firm_destruction (firm_id, stage, detail) SELECT ?, 'store_removing', '' WHERE NOT EXISTS "
            "(SELECT 1 FROM offboarding_runs WHERE firm_id = ? AND cancel_requested_at IS NOT NULL) "
            "ON CONFLICT DO NOTHING", (firm_id, firm_id))
        return cur.rowcount == 1 or "store_removing" in self._stages(firm_id)

    def _destroy(self, firm_id: str, ref: offboarding.StoreRef | None, *, by: str, holder: str) -> str:
        """Remove the sealed store, the firm's objects and tenant directory, then shred its key: each stage recorded
        ('store_removing' before the store is touched), so a retry resumes where a failure stopped and the key goes
        last. The seal is verified again inside the removal; the objects are removed from the store recorded for the
        firm, never from wherever this job's settings point."""
        details = []
        stages = self._stages(firm_id)
        try:
            if "store_removed" not in stages:
                if not self._begin_removal(firm_id):
                    raise _Cancelled()
                detail = offboarding.destroy(ref) if ref is not None else "no store was provisioned"
                self._stage(firm_id, "store_removed", detail)
                if db.backend() == "postgres" and self.get(firm_id):
                    self.put(firm_id, state="removed")
                details.append(detail)
                self._renew_run(firm_id, holder)
            if "blobs_removed" not in stages:
                from ..evidence import blobs

                recorded = self.firm(firm_id).get("blob_location")
                here = blobs.location_of(firm_id)
                if not recorded:                   # created before the location was recorded: never guess it
                    raise AuthError("where this firm's objects live was never recorded (the firm predates the record): "
                                    "check the deployment's object-store settings, record it with "
                                    "record_blob_location, and retry")
                if recorded != here:
                    raise AuthError(f"this firm's objects are in {recorded}, but this job is configured for {here}: "
                                    "configure that object store and retry")
                detail = blobs.destroy_firm(firm_id)
                self._stage(firm_id, "blobs_removed", detail)
                details.append(detail)
                self._renew_run(firm_id, holder)
            if "tenant_removed" not in stages:
                tenant = self.tenant_dir(firm_id)
                if tenant.exists():
                    shutil.rmtree(tenant)
                self._stage(firm_id, "tenant_removed", "tenant directory removed")
                details.append("tenant directory removed")
        except _Cancelled:
            raise
        except Exception as exc:
            self.event("firm_data_destroy_failed", firm_id=firm_id, user_id=by, detail=f"{type(exc).__name__}: {exc}"[:300])
            raise
        if "key_shredded" not in stages:
            self.keys.destroy(firm_id)                 # last: nothing that could still be held is left to protect
            self._stage(firm_id, "key_shredded")
            details.append("data key shredded")
        detail = "; ".join(details) or "already destroyed"
        self.event("firm_data_destroyed", firm_id=firm_id, user_id=by, detail=detail[:300])
        return detail

    def record_blob_location(self, firm_id: str, location: str, *, by: str, reason: str) -> None:
        """Record where the objects of a firm created before the platform recorded it live ('file', or
        's3:<bucket>/<prefix>' as blobs.location_of names it), so offboarding removes them from there. An operator
        records it after checking the deployment's settings; a recorded location is never changed."""
        location = location.strip()
        if not reason.strip():
            raise AuthError("say why: what the location was checked against")
        if location != "file" and not re.fullmatch(r"s3:[^/\s]+/\S*", location):
            raise AuthError("a location is 'file' or 's3:<bucket>/<prefix>'")
        with self._critical(f"firm:{firm_id}"):
            cur = self.conn.execute("UPDATE firms SET blob_location = ? WHERE id = ? AND blob_location IS NULL",
                                    (location, firm_id))
            if cur.rowcount != 1:
                raise AuthError("unknown firm, or its location is already recorded (a recorded location never changes)")
        self.event("firm_blob_location_recorded", firm_id=firm_id, user_id=by, detail=f"{location}: {reason}"[:300])

    def _seal_or_refuse(self, firm_id: str, ref: offboarding.StoreRef, *, by: str, action: str) -> dict[str, Any] | None:
        """Seal the store against new holds and read what it holds, or refuse (fail closed) with the reason."""
        try:
            return offboarding.seal(ref)
        except offboarding.Held as e:
            self.event(f"firm_{action}_refused", firm_id=firm_id, user_id=by, detail=f"active legal holds: {e}"[:300])
            raise AuthError(f"{e}: a CPA must release each with a reason before the firm's evidence can be destroyed") from e
        except offboarding.NotFound as e:
            self.event(f"firm_{action}_refused", firm_id=firm_id, user_id=by, detail=f"store not found: {e}"[:300])
            raise AuthError(f"the firm's store is not where the platform's records say ({e}); its legal holds cannot be "
                            f"checked, so nothing is destroyed") from e
        except Exception as exc:
            self.event(f"firm_{action}_refused", firm_id=firm_id, user_id=by,
                       detail=f"store unreadable: {type(exc).__name__}: {exc}"[:300])
            raise AuthError(f"the firm's store could not be read to check legal holds; refusing to {action} the firm") from exc

    def _back_out(self, firm_id: str, ref: offboarding.StoreRef | None, *, by: str, event: str, detail: str) -> None:
        """An offboarding that stops before its store is removed: unseal the store (whatever the seal's outcome was:
        a lost acknowledgement may have left it sealed), then put the firm back into use. If unsealing fails the firm
        stays in 'offboarding', so nobody is told a hold was placed while holds are still refused."""
        if ref is not None:
            offboarding.unseal(ref)
        self.conn.execute("DELETE FROM firm_destruction WHERE firm_id = ? AND stage = 'sealed'", (firm_id,))
        self.conn.execute("UPDATE offboarding_runs SET cancel_requested_at = NULL, cancel_by = NULL, cancel_reason = NULL "
                          "WHERE firm_id = ?", (firm_id,))
        self._end_offboarding(firm_id)
        self.event(event, firm_id=firm_id, user_id=by, detail=detail[:300])

    def delete_firm(self, firm_id: str, *, by: str, reason: str = "") -> None:
        """Offboarding: keep a record of what the store held, remove the store and files, crypto-shred the firm's data
        key, then retire the firm. Irreversible: the firm's export bundle (backlog A1-10) must be delivered first.

        The firm is taken out of use first ('offboarding': every request for it is refused). One run at a time holds
        the offboarding's lease. The store, located from the platform's records, is sealed in one locked step
        (evidence.offboarding.seal): every hold still being placed lands first and is seen; none can be added
        afterwards. Refused, with the store unsealed and the firm back in use, while any hold is active or if the store
        cannot be found or read. A cancellation (cancel_offboarding) stops the run at its next stage, until the store's
        removal begins. Each stage is recorded, so a retry resumes; the firm is marked deleted and its users disabled
        only once everything is gone. A firm still provisioning is abandoned (abandon_firm), never deleted."""
        if len(reason.strip()) < 10:
            raise AuthError("record why the firm is being deleted (for example the signed offboarding request)")
        self._not_reserved(firm_id)
        if db.backend() == "postgres":
            from ..pg import migration_url

            if not migration_url():       # sealing and removal need them; without, the firm would be left out of use
                raise AuthError("firm offboarding runs as an operations job with owner database credentials "
                                "(AGENTLEDGER_MIGRATION_URL)")
        with self._critical(f"firm:{firm_id}"):
            f = self.firm(firm_id)
            if f["status"] == "deleted":
                raise AuthError("this firm is already deleted; use destroy_firm_data to finish removing its data")
            if f["status"] == "provisioning":
                raise AuthError("this firm never finished provisioning: use abandon_firm, which removes only what "
                                "provisioning recorded creating")
            if f["status"] != "offboarding":
                self.conn.execute("UPDATE firms SET status = 'offboarding', offboarding_from = ? WHERE id = ?", (f["status"], firm_id))
                # A cancellation belongs to the offboarding it was made against: a new one begins with none.
                self.conn.execute("UPDATE offboarding_runs SET cancel_requested_at = NULL, cancel_by = NULL, "
                                  "cancel_reason = NULL WHERE firm_id = ?", (firm_id,))
                self.event("firm_offboarding_started", firm_id=firm_id, user_id=by, detail=reason.strip()[:300])
        holder = f"{os.getpid()}:{secrets.token_hex(6)}"
        if not self._claim_run(firm_id, holder):
            raise AuthError("an offboarding run of this firm is already in progress")
        ref: offboarding.StoreRef | None = None
        try:
            try:
                ref = self._store_ref(firm_id)
                if not ({"store_removing", "store_removed"} & self._stages(firm_id)):
                    if self._cancel_request(firm_id):
                        raise _Cancelled()
                    summary = self._seal_or_refuse(firm_id, ref, by=by, action="delete") if ref is not None else None
                    self._stage(firm_id, "sealed")
                    from ..evidence.records import to_json

                    self.conn.execute("INSERT INTO firm_offboarding (firm_id, by, reason, summary) VALUES (?, ?, ?, ?)",
                                      (firm_id, by, reason.strip(), to_json(summary)))
                self._destroy(firm_id, ref, by=by, holder=holder)
            except _Cancelled:
                req = self._cancel_request(firm_id) or {}
                self._back_out(firm_id, ref, by=str(req.get("cancel_by") or by), event="firm_offboarding_cancelled",
                               detail=str(req.get("cancel_reason") or "cancelled"))
                raise AuthError("the offboarding was cancelled before the store's removal began; the firm is back in use")
            except BaseException as exc:
                if not ({"store_removing", "store_removed"} & self._stages(firm_id)):   # nothing removed: back into use
                    try:
                        self._back_out(firm_id, ref, by=by, event="firm_offboarding_stopped",
                                       detail=f"{type(exc).__name__}: {exc}")
                    except Exception as unseal_error:      # still sealed: the firm stays out of use
                        self.event("firm_offboarding_stuck", firm_id=firm_id, user_id=by,
                                   detail=f"could not unseal: {type(unseal_error).__name__}: {unseal_error}"[:300])
                raise
        finally:
            self._release_run(firm_id, holder)
        with self._critical(f"firm:{firm_id}"):
            self.conn.execute("UPDATE firms SET status = 'deleted', deleted_at = datetime('now') WHERE id = ?", (firm_id,))
            self.conn.execute("UPDATE users SET disabled = 1 WHERE firm_id = ?", (firm_id,))
            self.conn.execute("UPDATE sessions SET revoked_at = datetime('now') WHERE user_id IN "
                              "(SELECT id FROM users WHERE firm_id = ?)", (firm_id,))
            self.event("firm_deleted", firm_id=firm_id, user_id=by, detail=reason.strip()[:300])

    def _end_offboarding(self, firm_id: str) -> None:
        with self._critical(f"firm:{firm_id}"):
            self.conn.execute("UPDATE firms SET status = coalesce(offboarding_from, 'active'), offboarding_from = NULL "
                              "WHERE id = ? AND status = 'offboarding'", (firm_id,))

    def cancel_offboarding(self, firm_id: str, *, by: str, reason: str) -> str:
        """Stop an offboarding before its store is removed (a hold must be placed, the request was withdrawn).

        With a run in progress the cancellation is recorded and the run stops at its next stage, before the store's
        removal begins; the firm stays out of use until it has ("requested"), and a run already removing the store is
        not interrupted (refused). With no run in progress the store is unsealed and the firm goes back into use now
        ("cancelled"), also after a removal attempt that failed, provided the store is verifiably intact; a store
        removed in part can only be finished."""
        if len(reason.strip()) < 10:
            raise AuthError("record why the offboarding is cancelled")
        f = self.firm(firm_id)
        if f["status"] != "offboarding":
            raise AuthError("this firm is not being offboarded")
        holder = f"{os.getpid()}:{secrets.token_hex(6)}"
        if not self._claim_run(firm_id, holder):          # a run is in progress: ask it to stop at its next stage
            with self._critical(f"offboarding:{firm_id}"):   # the request is made against a run still holding the lease
                cur = self.conn.execute(
                    "UPDATE offboarding_runs SET cancel_requested_at = ?, cancel_by = ?, cancel_reason = ? WHERE firm_id = ? "
                    "AND holder IS NOT NULL AND lease_until >= ? "
                    "AND NOT EXISTS (SELECT 1 FROM firm_destruction WHERE firm_id = ? AND stage IN ('store_removing', 'store_removed'))",
                    (_iso(_now()), by, reason.strip(), firm_id, _iso(_now()), firm_id))
            if cur.rowcount == 1:
                self.event("firm_offboarding_cancel_requested", firm_id=firm_id, user_id=by, detail=reason.strip()[:300])
                return "requested"
            if not self._claim_run(firm_id, holder):      # still held: the run is removing the store
                raise AuthError("this firm's store is being removed by a run in progress; the offboarding can only be finished")
        try:                                              # this cancellation holds the lease
            if self.firm(firm_id)["status"] != "offboarding":          # the run stopped on its own meanwhile
                raise AuthError("this firm is no longer being offboarded: its run stopped and put it back in use")
            stages = self._stages(firm_id)
            if "store_removed" in stages:
                raise AuthError("this firm's store is already removed; the offboarding can only be finished")
            ref = self._store_ref(firm_id)
            if "store_removing" in stages:                 # a removal attempt failed: only an intact store comes back
                if ref is None or not offboarding.intact(ref):
                    raise AuthError("this firm's store was partly removed; the offboarding can only be finished")
                self.conn.execute("DELETE FROM firm_destruction WHERE firm_id = ? AND stage = 'store_removing'", (firm_id,))
            self._back_out(firm_id, ref, by=by, event="firm_offboarding_cancelled", detail=reason.strip())
        finally:
            self._release_run(firm_id, holder)
        return "cancelled"

    def offboarding_records(self, firm_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM firm_offboarding WHERE firm_id = ? ORDER BY id", (firm_id,))]

    def destroy_firm_data(self, firm_id: str, *, by: str) -> str:
        """Finish removing a deleted firm's data (a firm deleted before staged destruction, or one whose removal was
        interrupted): the store is sealed first (refused while a legal hold is active or if it cannot be found where the
        records say, unless the records say its removal began or finished), then the stages run; the key goes last."""
        self._not_reserved(firm_id)
        if self.firm(firm_id)["status"] != "deleted":
            raise AuthError("only a deleted firm's data can be destroyed")
        holder = f"{os.getpid()}:{secrets.token_hex(6)}"
        if not self._claim_run(firm_id, holder):
            raise AuthError("an offboarding run of this firm is already in progress")
        try:
            stages = self._stages(firm_id)
            rec = self.get(firm_id) if db.backend() == "postgres" else None
            removed = bool({"store_removing", "store_removed"} & stages) or bool(rec and rec.get("state") == "removed")
            ref = self._store_ref(firm_id, allow_unrecorded=bool(rec and rec.get("state") == "removed"))
            if not removed and ref is not None:
                self._seal_or_refuse(firm_id, ref, by=by, action="destroy")
                self._stage(firm_id, "sealed")
            elif rec and rec.get("state") == "removed" and "store_removed" not in stages:
                self._stage(firm_id, "store_removing")
                self._stage(firm_id, "store_removed", "recorded as removed by provisioning")
            return self._destroy(firm_id, ref, by=by, holder=holder)
        finally:
            self._release_run(firm_id, holder)

    # ------------------------------------------------------------------ users and invitations
    def is_platform_admin(self, email: str) -> bool:
        r = self.conn.execute("SELECT role FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        return bool(r and r["role"] == "platform_admin")

    def bootstrap_admin(self, email: str, name: str, password: str) -> str:
        """First platform administrator; allowed only while no platform admin exists."""
        with self._critical("platform:bootstrap"):
            if self.conn.execute("SELECT 1 FROM users WHERE role = 'platform_admin'").fetchone():
                raise AuthError("a platform administrator already exists")
            return self._create_user(PLATFORM_FIRM, email, name, "platform_admin", None, password)

    def invite(self, firm_id: str, email: str, role: str, *, by: dict[str, Any], client_id: str | None = None) -> str:
        if role not in ROLES or role == "platform_admin":
            raise AuthError("invalid role")
        if role == "client" and not client_id:
            raise AuthError("a client user must be linked to a client")
        if by["role"] != "platform_admin" and (by["firm_id"] != firm_id or by["role"] not in ("firm_admin", "cpa")):
            raise AuthError("you cannot invite users to this firm")
        if by["role"] == "cpa" and role in ("firm_admin", "cpa", "staff"):
            raise AuthError("only a firm administrator can invite staff")
        self.firm(firm_id)
        token = secrets.token_urlsafe(32)
        self.conn.execute("INSERT INTO invites (token_hash, firm_id, email, role, client_id, created_by, expires_at) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (_hash(token), firm_id, email.strip().lower(), role, client_id, by["id"], _iso(_now() + INVITE_TTL)))
        self.event("invite_created", firm_id=firm_id, user_id=by["id"], email=email, detail=role)
        return token

    def accept_invite(self, token: str, name: str, password: str, ip: str | None = None) -> Enrolment:
        if self.identity == "workos":
            raise AuthError("this firm signs in through its sign-in provider; open the invitation link to continue there")
        with self._critical(f"invite:{_hash(token)}"):            # single use, whichever acceptance arrives first
            inv = self.conn.execute("SELECT * FROM invites WHERE token_hash = ?", (_hash(token),)).fetchone()
            if not inv or inv["accepted_at"] or inv["expires_at"] < _iso(_now()):
                raise AuthError("this invitation is invalid or has expired")
            user_id = self._create_user(inv["firm_id"], inv["email"], name, inv["role"], inv["client_id"], password)
            self.conn.execute("UPDATE invites SET accepted_at = datetime('now') WHERE token_hash = ?", (_hash(token),))
            self.event("invite_accepted", firm_id=inv["firm_id"], user_id=user_id, email=inv["email"], ip=ip)
            return self._enrolment(user_id)

    def _create_user(self, firm_id: str, email: str, name: str, role: str, client_id: str | None, password: str) -> str:
        problems = password_problems(password, email)
        if problems:
            raise AuthError("password: " + "; ".join(problems))
        address = email.strip().lower()           # stored and looked up in one form; the unique index is on lower(email)
        if self.conn.execute("SELECT 1 FROM users WHERE email = ?", (address,)).fetchone():
            raise AuthError("an account with this email already exists")
        user_id = "u_" + secrets.token_hex(8)
        try:
            self.conn.execute("INSERT INTO users (id, firm_id, email, name, role, client_id, password_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                              (user_id, firm_id, address, name, role, client_id, self.ph.hash(password)))
        except sqlite3.IntegrityError as exc:       # the same address, accepted elsewhere at the same moment
            raise AuthError("an account with this email already exists") from exc
        self.event("user_created", firm_id=firm_id, user_id=user_id, email=email, detail=role)
        return user_id

    def user(self, user_id: str) -> dict[str, Any]:
        r = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not r:
            raise AuthError("user not found")
        return dict(r)

    def public_user(self, u: dict[str, Any]) -> dict[str, Any]:
        out = {k: u[k] for k in ("id", "firm_id", "email", "name", "role", "client_id", "mfa_enrolled_at", "disabled",
                                 "last_login_at")}
        out["reviewer"] = u["role"] == "cpa" or bool(u.get("reviewer"))
        if u["role"] == "staff":
            out["engaged"] = self.grants(u["id"])
        return out

    # ------------------------------------------------------------------ authority: reviewers and engagements
    def set_reviewer(self, user_id: str, on: bool, *, by: dict[str, Any], credential: str = "") -> None:
        """Give a firm administrator reviewer authority (a firm admin who is also a CPA). Never self-granted: another
        firm administrator or a platform administrator records it, with the credential it rests on."""
        u = self.user(user_id)
        if u["role"] != "firm_admin":
            raise AuthError("reviewer authority is added to firm administrators; CPAs hold it already")
        if by["id"] == user_id:
            raise AuthError("reviewer authority cannot be granted to yourself")
        if by["role"] != "platform_admin" and (by["role"] != "firm_admin" or by["firm_id"] != u["firm_id"]):
            raise AuthError("not allowed")
        if on and len(credential.strip()) < 4:
            raise AuthError("record the credential this rests on (for example the CPA license and state)")
        self.conn.execute("UPDATE users SET reviewer = ?, reviewer_credential = ? WHERE id = ?",
                          (1 if on else 0, credential.strip() if on else None, user_id))
        self.event("reviewer_granted" if on else "reviewer_revoked", firm_id=u["firm_id"], user_id=by["id"], email=u["email"],
                   detail=credential.strip()[:200])

    def grants(self, user_id: str) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT client_id FROM engagement_grants WHERE user_id = ? AND revoked_at IS NULL "
                                                "ORDER BY client_id", (user_id,))]

    def grant(self, user_id: str, client_id: str, *, by: dict[str, Any]) -> None:
        """Engage a staff member on a client. Firm administrators and CPAs of the same firm assign work."""
        u = self.user(user_id)
        if u["role"] != "staff":
            raise AuthError("engagement grants apply to staff; CPAs and administrators see the whole firm")
        if by["firm_id"] != u["firm_id"] or by["role"] not in ("firm_admin", "cpa"):
            raise AuthError("not allowed")
        if client_id in self.grants(user_id):
            return
        self.conn.execute("INSERT INTO engagement_grants (firm_id, user_id, client_id, granted_by) VALUES (?, ?, ?, ?)",
                          (u["firm_id"], user_id, client_id, by["id"]))
        self.event("engagement_granted", firm_id=u["firm_id"], user_id=by["id"], email=u["email"], detail=client_id)

    def revoke(self, user_id: str, client_id: str, *, by: dict[str, Any]) -> None:
        u = self.user(user_id)
        if by["firm_id"] != u["firm_id"] or by["role"] not in ("firm_admin", "cpa"):
            raise AuthError("not allowed")
        self.conn.execute("UPDATE engagement_grants SET revoked_at = datetime('now'), revoked_by = ? "
                          "WHERE user_id = ? AND client_id = ? AND revoked_at IS NULL", (by["id"], user_id, client_id))
        self.event("engagement_revoked", firm_id=u["firm_id"], user_id=by["id"], email=u["email"], detail=client_id)

    def users(self, firm_id: str) -> list[dict[str, Any]]:
        return [self.public_user(dict(r)) for r in self.conn.execute("SELECT * FROM users WHERE firm_id = ? ORDER BY name", (firm_id,))]

    def set_disabled(self, user_id: str, disabled: bool, *, by: dict[str, Any]) -> None:
        u = self.user(user_id)
        if by["role"] != "platform_admin" and (by["role"] != "firm_admin" or by["firm_id"] != u["firm_id"]):
            raise AuthError("not allowed")
        self.conn.execute("UPDATE users SET disabled = ? WHERE id = ?", (1 if disabled else 0, user_id))
        if disabled:
            self.conn.execute("UPDATE sessions SET revoked_at = datetime('now') WHERE user_id = ? AND revoked_at IS NULL", (user_id,))
        self.event("user_disabled" if disabled else "user_enabled", firm_id=u["firm_id"], user_id=by["id"], email=u["email"])

    # ------------------------------------------------------------------ login
    def _enrolment(self, user_id: str) -> Enrolment:
        u = self.user(user_id)
        secret = totp.new_secret()
        challenge = secrets.token_urlsafe(32)
        sealed = self.keys.seal_text(u["firm_id"], secret, f"totp-pending:{user_id}")
        self.conn.execute("INSERT INTO challenges (token_hash, user_id, kind, pending_secret, expires_at) VALUES (?, ?, 'enroll', ?, ?)",
                          (_hash(challenge), user_id, sealed, _iso(_now() + timedelta(minutes=15))))
        return Enrolment(challenge, secret, totp.provisioning_uri(secret, u["email"]))

    def _count_failure(self, user_id: str) -> int:
        """One more failed password or code for the account, inside the account's critical section: the counter is
        incremented in place (never written from a value read earlier, so concurrent attempts all count) and
        MAX_FAILURES locks the account. Returns the count reached."""
        self.conn.execute("UPDATE users SET failed_logins = failed_logins + 1 WHERE id = ?", (user_id,))
        failures = int(self.conn.execute("SELECT failed_logins FROM users WHERE id = ?", (user_id,)).fetchone()[0])
        if failures >= MAX_FAILURES:
            self.conn.execute("UPDATE users SET failed_logins = 0, locked_until = ? WHERE id = ?", (_iso(_now() + LOCKOUT), user_id))
        return failures

    def login(self, email: str, password: str, ip: str | None = None) -> dict[str, Any]:
        """Step 1. Returns {"mfa": challenge} or {"enroll": Enrolment}. Never a session. The password check (Argon2
        work) runs outside any lock; a failure is counted in the account's own critical section."""
        r = self.conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        generic = AuthError("email or password is incorrect")
        if not r:
            self.ph.hash(password)  # equalise timing for unknown accounts
            self.event("login_failed", email=email, ip=ip, detail="unknown account")
            raise generic
        u = dict(r)
        if self.identity == "workos" and u["role"] != "platform_admin":
            self.ph.hash(password)
            self.event("login_failed", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail="password sign-in is off for firm users")
            raise generic
        if u["disabled"]:
            self.event("login_failed", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail="disabled")
            raise generic
        if u["locked_until"] and u["locked_until"] > _iso(_now()):
            self.event("login_locked", firm_id=u["firm_id"], user_id=u["id"], ip=ip)
            raise AuthError("too many attempts; try again in 15 minutes")
        try:
            if u["password_hash"] == NO_PASSWORD:
                raise VerifyMismatchError()
            self.ph.verify(u["password_hash"], password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            with self._critical(f"user:{u['id']}"):
                failures = self._count_failure(u["id"])
                self.event("login_failed", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail=f"failure {failures}")
            raise generic
        if self.ph.check_needs_rehash(u["password_hash"]):
            self.conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (self.ph.hash(password), u["id"]))
        self.conn.execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = ?", (u["id"],))
        if not u["mfa_enrolled_at"]:
            return {"enroll": self._enrolment(u["id"])}
        challenge = secrets.token_urlsafe(32)
        self.conn.execute("INSERT INTO challenges (token_hash, user_id, kind, expires_at) VALUES (?, ?, 'mfa', ?)",
                          (_hash(challenge), u["id"], _iso(_now() + CHALLENGE_TTL)))
        self.event("password_ok", firm_id=u["firm_id"], user_id=u["id"], ip=ip)
        return {"mfa": challenge}

    def complete_mfa(self, challenge: str, code: str, ip: str | None = None, user_agent: str | None = None) -> str:
        """Step 2. Verifies the one-time code (enrolling it if this is the first time) and issues a session token.
        One code check per account at a time, so a step used by a concurrent attempt is seen and the challenge is
        spent exactly once."""
        h = _hash(challenge)
        pre = self.conn.execute("SELECT user_id FROM challenges WHERE token_hash = ?", (h,)).fetchone()
        if not pre:
            raise AuthError("this sign-in attempt has expired; start again")
        with self._critical(f"user:{pre['user_id']}"):
            ch = self.conn.execute("SELECT * FROM challenges WHERE token_hash = ?", (h,)).fetchone()
            if not ch or ch["used_at"] or ch["expires_at"] < _iso(_now()):
                raise AuthError("this sign-in attempt has expired; start again")
            u = self.user(ch["user_id"])
            if ch["kind"] == "enroll":
                secret = self.keys.open_text(u["firm_id"], ch["pending_secret"], f"totp-pending:{u['id']}")
                last = None
            else:
                secret = self.keys.open_text(u["firm_id"], u["totp_secret"], f"totp:{u['id']}")
                last = u["totp_last_step"]
            step = totp.verify(secret, code, last_used_step=last)
            if step is None:
                if self._count_failure(u["id"]) >= MAX_FAILURES:
                    self.conn.execute("UPDATE challenges SET used_at = datetime('now') WHERE token_hash = ?", (h,))
                self.event("mfa_failed", firm_id=u["firm_id"], user_id=u["id"], ip=ip)
                raise _Refused(AuthError("that code is not valid"))
            self.conn.execute("UPDATE challenges SET used_at = datetime('now') WHERE token_hash = ?", (h,))
            if ch["kind"] == "enroll":
                self.conn.execute("UPDATE users SET totp_secret = ?, mfa_enrolled_at = datetime('now') WHERE id = ?",
                                  (self.keys.seal_text(u["firm_id"], secret, f"totp:{u['id']}"), u["id"]))
                self.event("mfa_enrolled", firm_id=u["firm_id"], user_id=u["id"], ip=ip)
            self.conn.execute("UPDATE users SET totp_last_step = ?, failed_logins = 0, last_login_at = datetime('now') WHERE id = ?",
                              (step, u["id"]))
            return self._start_session(u, ip, user_agent, "password+totp")

    def _start_session(self, u: dict[str, Any], ip: str | None, user_agent: str | None, method: str,
                       authenticated_at: datetime | None = None) -> str:
        """`authenticated_at` is when the person proved who they are: now for password + TOTP here, the provider's
        verified auth_time for a provider sign-in (which may be an older provider session)."""
        token = secrets.token_urlsafe(32)
        now = _now()
        self.conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, ip, user_agent, auth_method, "
                          "authenticated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (_hash(token), u["id"], _iso(now), _iso(now), _iso(now + ABSOLUTE), ip, (user_agent or "")[:200], method,
                           _iso(authenticated_at or now)))
        self.conn.execute("UPDATE users SET last_login_at = datetime('now') WHERE id = ?", (u["id"],))
        self.event("login", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail=method)
        return token

    def session_user(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        r = self.conn.execute("SELECT * FROM sessions WHERE token_hash = ?", (_hash(token),)).fetchone()
        now = _now()
        if not r or r["revoked_at"] or r["expires_at"] < _iso(now) or r["last_seen_at"] < _iso(now - IDLE):
            return None
        u = self.user(r["user_id"])
        if u["disabled"]:
            return None
        if u["firm_id"] != PLATFORM_FIRM and self.firm(u["firm_id"])["status"] != "active":
            return None
        self.conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (_iso(now), _hash(token)))
        fresh_at = max(r["authenticated_at"] or r["created_at"], r["stepped_up_at"] or "")
        return {**self.public_user(u), "session_id": r["token_hash"], "auth_method": r["auth_method"], "fresh_at": fresh_at}

    # ------------------------------------------------------------------ step-up
    def require_fresh(self, user: dict[str, Any]) -> None:
        """Consequential actions (approving or reconciling a return, closing books, creating firms) need a sign-in or
        step-up within the last few minutes, so an unattended or stolen session cannot perform them."""
        fresh_at = user.get("fresh_at") or ""
        if fresh_at < _iso(_now() - FRESH):
            raise AuthError("step_up_required")

    def step_up_totp(self, token: str, code: str, ip: str | None = None) -> None:
        """Re-verify a local account with its one-time code."""
        r = self.conn.execute("SELECT * FROM sessions WHERE token_hash = ? AND revoked_at IS NULL", (_hash(token),)).fetchone()
        if not r:
            raise AuthError("sign in required")
        with self._critical(f"user:{r['user_id']}"):          # one code check per account at a time (used steps)
            u = self.user(r["user_id"])
            if not u["totp_secret"]:
                raise AuthError("this account steps up through its sign-in provider")
            secret = self.keys.open_text(u["firm_id"], u["totp_secret"], f"totp:{u['id']}")
            step = totp.verify(secret, code, last_used_step=u["totp_last_step"])
            if step is None:
                self.event("step_up_failed", firm_id=u["firm_id"], user_id=u["id"], ip=ip)
                raise _Refused(AuthError("that code is not valid"))
            self.conn.execute("UPDATE users SET totp_last_step = ? WHERE id = ?", (step, u["id"]))
            self.conn.execute("UPDATE sessions SET stepped_up_at = ? WHERE token_hash = ?", (_iso(_now()), _hash(token)))
            self.event("step_up", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail="totp")

    # ------------------------------------------------------------------ identity provider (WorkOS AuthKit, ADR-0004)
    def attest_sso_mfa(self, organization_id: str, firm_id: str, *, by: dict[str, Any], evidence: str) -> None:
        """Record that an organization's own identity provider enforces MFA (SSO sign-ins skip WorkOS's MFA). Only a
        platform administrator records it, with the evidence reviewed (policy screenshot, IdP configuration export)."""
        if by["role"] != "platform_admin":
            raise AuthError("only a platform administrator records SSO MFA enforcement")
        if len(evidence.strip()) < 10:
            raise AuthError("describe the evidence that the identity provider enforces MFA")
        self.firm(firm_id)
        self.conn.execute("INSERT INTO sso_mfa_attestations (organization_id, firm_id, evidence, attested_by) VALUES (?, ?, ?, ?) "
                          "ON CONFLICT (organization_id) DO UPDATE SET firm_id = excluded.firm_id, evidence = excluded.evidence, "
                          "attested_by = excluded.attested_by, attested_at = datetime('now'), revoked_at = NULL",
                          (organization_id, firm_id, evidence.strip(), by["id"]))
        self.event("sso_mfa_attested", firm_id=firm_id, user_id=by["id"], detail=f"{organization_id}: {evidence.strip()[:150]}")

    def revoke_sso_mfa(self, organization_id: str, *, by: dict[str, Any]) -> None:
        if by["role"] != "platform_admin":
            raise AuthError("only a platform administrator changes SSO MFA records")
        self.conn.execute("UPDATE sso_mfa_attestations SET revoked_at = datetime('now') WHERE organization_id = ?", (organization_id,))
        self.event("sso_mfa_revoked", user_id=by["id"], detail=organization_id)

    def _multi_factor(self, idp: Any, auth: dict[str, Any], firm_id: str | None) -> bool:
        from .workos import PASSKEY

        method = auth.get("authentication_method")
        if method == PASSKEY:
            return True
        if method == "SSO":
            org = auth.get("organization_id")
            return bool(org and firm_id and self.conn.execute(
                "SELECT 1 FROM sso_mfa_attestations WHERE organization_id = ? AND firm_id = ? AND revoked_at IS NULL",
                (org, firm_id)).fetchone())
        return idp.has_totp_factor(auth["user"]["id"])

    def idp_begin(self, idp: Any, purpose: str, redirect_uri: str, *, invite_token: str | None = None,
                  session_token: str | None = None, browser_nonce: str = "") -> str:
        """Start a hosted sign-in. `login` signs in an account already linked; `invite` accepts an invitation (the
        verified email must match it); `link` attaches the provider to the signed-in local account; `step_up` forces
        a fresh authentication for the signed-in account (max_age=0)."""
        from .workos import pkce_pair

        if purpose not in ("login", "invite", "link", "step_up"):
            raise AuthError("unknown sign-in purpose")
        invite_hash = session_hash = None
        hint = None
        if purpose == "invite":
            inv = self.conn.execute("SELECT * FROM invites WHERE token_hash = ?", (_hash(invite_token or ""),)).fetchone()
            if not inv or inv["accepted_at"] or inv["expires_at"] < _iso(_now()):
                raise AuthError("this invitation is invalid or has expired")
            invite_hash, hint = inv["token_hash"], inv["email"]
        if purpose in ("link", "step_up"):
            u = self.session_user(session_token or "")
            if not u:
                raise AuthError("sign in required")
            session_hash, hint = u["session_id"], u["email"]
        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        sealed = self.keys.seal_text(PLATFORM_FIRM, verifier, f"idp:{_hash(state)}")
        if len(browser_nonce) < 32:
            raise AuthError("sign-in must start from the browser that finishes it")
        self.conn.execute("INSERT INTO idp_states (state_hash, purpose, invite_hash, session_hash, verifier, redirect_uri, expires_at, "
                          "browser_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (_hash(state), purpose, invite_hash, session_hash, sealed, redirect_uri, _iso(_now() + IDP_STATE_TTL),
                           _hash(browser_nonce)))
        return idp.authorization_url(redirect_uri, state, challenge, login_hint=hint, max_age=0 if purpose == "step_up" else None)

    def idp_complete(self, idp: Any, code: str, state: str, *, ip: str | None = None, user_agent: str | None = None,
                     browser_nonce: str = "") -> dict[str, Any]:
        """Finish a hosted sign-in. Returns {"purpose", "token"?}. Refuses a callback in another browser, unverified
        email, impersonation and sign-ins without a second factor; never creates an account without a matching
        invitation. Freshness comes from the provider's signed auth_time, not from when this session was created."""
        with self._critical(f"idp:{_hash(state)}"):         # the state is spent once, whichever callback arrives first
            st = self.conn.execute("SELECT * FROM idp_states WHERE state_hash = ?", (_hash(state),)).fetchone()
            if not st or st["used_at"] or st["expires_at"] < _iso(_now()):
                raise AuthError("this sign-in attempt has expired; start again")
            self.conn.execute("UPDATE idp_states SET used_at = datetime('now') WHERE state_hash = ?", (_hash(state),))
            if not st["browser_hash"] or not hmac.compare_digest(st["browser_hash"], _hash(browser_nonce or "")):
                self.event("idp_refused", ip=ip, detail="callback from a different browser")
                raise _Refused(AuthError("finish signing in from the same browser you started in"))
        verifier = self.keys.open_text(PLATFORM_FIRM, st["verifier"], f"idp:{st['state_hash']}")
        try:
            auth = idp.authenticate(code, verifier, ip=ip, user_agent=user_agent)
        except Exception as exc:
            self.event("idp_failed", ip=ip, detail=str(exc)[:200])
            raise AuthError("the sign-in provider did not confirm this sign-in") from exc
        wu = auth.get("user") or {}
        email = str(wu.get("email", "")).strip().lower()
        if auth.get("impersonator"):
            self.event("idp_refused", email=email, ip=ip, detail="impersonation")
            raise AuthError("impersonated sessions cannot sign in to client data")
        if not wu.get("email_verified"):
            self.event("idp_refused", email=email, ip=ip, detail="email not verified")
            raise AuthError("verify your email address with the sign-in provider first")
        subject, method = str(wu["id"]), f"workos:{auth.get('authentication_method', 'unknown')}"
        try:
            claims = idp.verify_access_token(str(auth.get("access_token", "")), subject=subject)
        except Exception as exc:
            self.event("idp_refused", email=email, ip=ip, detail=f"access token: {exc}"[:200])
            raise AuthError("the sign-in provider did not confirm this sign-in") from exc
        auth_time = datetime.fromtimestamp(float(claims["auth_time"]), timezone.utc)
        firm_for_mfa = self._idp_firm(st, subject)
        if not self._multi_factor(idp, auth, firm_for_mfa):
            self.event("idp_refused", firm_id=firm_for_mfa, email=email, ip=ip, detail=f"no second factor ({auth.get('authentication_method')})")
            raise AuthError("turn on two-step verification or use a passkey in your sign-in settings, then try again")
        if st["purpose"] == "invite":
            with self._critical(f"invite:{st['invite_hash']}"):    # single use, whichever acceptance arrives first
                inv = self.conn.execute("SELECT * FROM invites WHERE token_hash = ?", (st["invite_hash"],)).fetchone()
                if not inv or inv["accepted_at"] or inv["expires_at"] < _iso(_now()):
                    raise AuthError("this invitation is invalid or has expired")
                if inv["email"] != email:
                    self.event("idp_refused", firm_id=inv["firm_id"], email=email, ip=ip, detail="email does not match invitation")
                    raise _Refused(AuthError("sign in with the email address the invitation was sent to"))
                if self.conn.execute("SELECT 1 FROM users WHERE email = ? OR idp_subject = ?", (email, subject)).fetchone():
                    raise AuthError("an account with this email already exists")
                user_id = "u_" + secrets.token_hex(8)
                name = " ".join(x for x in (wu.get("first_name"), wu.get("last_name")) if x) or email
                try:
                    self.conn.execute("INSERT INTO users (id, firm_id, email, name, role, client_id, password_hash, idp_subject, "
                                      "mfa_enrolled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                                      (user_id, inv["firm_id"], email, name, inv["role"], inv["client_id"], NO_PASSWORD, subject))
                except sqlite3.IntegrityError as exc:       # the same address or sign-in, linked elsewhere at the same moment
                    raise AuthError("an account with this email already exists") from exc
                self.conn.execute("UPDATE invites SET accepted_at = datetime('now') WHERE token_hash = ?", (st["invite_hash"],))
                self.event("invite_accepted", firm_id=inv["firm_id"], user_id=user_id, email=email, ip=ip, detail=method)
                token = self._start_session(self.user(user_id), ip, user_agent, method, auth_time)
            return {"purpose": "invite", "token": token}
        if st["purpose"] in ("link", "step_up"):
            r = self.conn.execute("SELECT * FROM sessions WHERE token_hash = ? AND revoked_at IS NULL", (st["session_hash"],)).fetchone()
            if not r:
                raise AuthError("sign in required")
            with self._critical(f"user:{r['user_id']}"):
                u = self.user(r["user_id"])
                if st["purpose"] == "link":
                    if u["idp_subject"] or self.conn.execute("SELECT 1 FROM users WHERE idp_subject = ?", (subject,)).fetchone():
                        raise AuthError("this account or sign-in is already linked")
                    if u["email"] != email:
                        raise AuthError("sign in with the same email address as this account")
                    try:
                        self.conn.execute("UPDATE users SET idp_subject = ? WHERE id = ?", (subject, u["id"]))
                    except sqlite3.IntegrityError as exc:
                        raise AuthError("this account or sign-in is already linked") from exc
                    self.event("idp_linked", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail=method)
                    return {"purpose": "link"}
                if u["idp_subject"] != subject:
                    self.event("step_up_failed", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail="different identity")
                    raise _Refused(AuthError("step up with the account you are signed in as"))
                if auth_time < _now() - FRESH:      # max_age=0 asked for a fresh sign-in; the token must show one
                    self.event("step_up_failed", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail="provider session not re-authenticated")
                    raise _Refused(AuthError("the sign-in provider did not re-authenticate you; try again"))
                self.conn.execute("UPDATE sessions SET stepped_up_at = ? WHERE token_hash = ?", (_iso(auth_time), st["session_hash"]))
                self.event("step_up", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail=method)
                return {"purpose": "step_up"}
        row = self.conn.execute("SELECT * FROM users WHERE idp_subject = ?", (subject,)).fetchone()
        if not row:   # never by email alone: an account is linked through an invitation or from a signed-in session
            self.event("login_failed", email=email, ip=ip, detail="no linked account")
            raise AuthError("no AgentLedger account is linked to this sign-in; ask your firm for an invitation")
        u = dict(row)
        if u["disabled"] or (u["firm_id"] != PLATFORM_FIRM and self.firm(u["firm_id"])["status"] != "active"):
            self.event("login_failed", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail="disabled or inactive firm")
            raise AuthError("this account cannot sign in")
        return {"purpose": "login", "token": self._start_session(u, ip, user_agent, method, auth_time)}

    def _idp_firm(self, st: Any, subject: str) -> str | None:
        """The firm a provider sign-in is for (its SSO MFA attestation is per firm)."""
        if st["purpose"] == "invite":
            inv = self.conn.execute("SELECT firm_id FROM invites WHERE token_hash = ?", (st["invite_hash"],)).fetchone()
            return inv["firm_id"] if inv else None
        if st["purpose"] in ("link", "step_up"):
            r = self.conn.execute("SELECT u.firm_id FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                                  (st["session_hash"],)).fetchone()
            return r["firm_id"] if r else None
        r = self.conn.execute("SELECT firm_id FROM users WHERE idp_subject = ?", (subject,)).fetchone()
        return r["firm_id"] if r else None

    def logout(self, token: str) -> None:
        r = self.conn.execute("SELECT user_id FROM sessions WHERE token_hash = ?", (_hash(token),)).fetchone()
        self.conn.execute("UPDATE sessions SET revoked_at = datetime('now') WHERE token_hash = ?", (_hash(token),))
        if r:
            u = self.user(r["user_id"])
            self.event("logout", firm_id=u["firm_id"], user_id=u["id"])
