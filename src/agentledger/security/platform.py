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
import re
import secrets
import shutil
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .. import db
from . import totp
from .crypto import Keyring, load_master_key

PLATFORM_FIRM = "_platform"
ROLES = ("platform_admin", "firm_admin", "cpa", "staff", "client")
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
    sees one business; firm staff are firm-wide until engagement grants arrive (backlog F-05)."""
    if user["role"] == "client":
        return [user["client_id"]] if user.get("client_id") else []
    return ["*"]


class AuthError(Exception):
    """Raised with a message that is safe to show to the person signing in."""


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
    def __init__(self, root: Path, *, dev: bool = False):
        self.root = Path(root)
        state = self.root / "state"
        state.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(state / "platform.db", check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._upgrade()
        self.lock = threading.RLock()
        self.keys = Keyring(self.conn, load_master_key(state, allow_dev_file=dev))
        self.ph = PasswordHasher()
        if not self.conn.execute("SELECT 1 FROM firm_keys WHERE firm_id = ?", (PLATFORM_FIRM,)).fetchone():
            self.keys.create(PLATFORM_FIRM)

    def _upgrade(self) -> None:
        """Columns added after the first release, for platform stores created before them."""
        for table, col, ddl in (("users", "idp_subject", "TEXT"), ("sessions", "auth_method", "TEXT NOT NULL DEFAULT 'password+totp'"),
                                ("sessions", "stepped_up_at", "TEXT")):
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
        if not FIRM_ID.match(firm_id) or firm_id == PLATFORM_FIRM:
            raise AuthError("firm id must be 2-41 lowercase letters, digits or hyphens")
        with self.lock:
            row = self.conn.execute("SELECT status FROM firms WHERE id = ?", (firm_id,)).fetchone()
            if row and row["status"] != "provisioning":
                raise AuthError("that firm id is taken")
            if not row:
                status = "provisioning" if db.backend() == "postgres" else "active"
                self.conn.execute("INSERT INTO firms (id, name, status) VALUES (?, ?, ?)", (firm_id, name, status))
                self.keys.create(firm_id)
                self.tenant_dir(firm_id).mkdir(parents=True, exist_ok=True)
                self.event("firm_created", firm_id=firm_id, user_id=by, detail=name)
        if db.backend() == "postgres":   # outside the lock: provisioning talks to the database service
            from ..pg import provision

            try:
                detail = provision.provision(firm_id, self)
            except Exception as exc:
                self.event("firm_store_provisioning_failed", firm_id=firm_id, user_id=by, detail=f"{type(exc).__name__}: {exc}"[:300])
                raise
            self.conn.execute("UPDATE firms SET status = 'active' WHERE id = ? AND status = 'provisioning'", (firm_id,))
            self.event("firm_store_provisioned", firm_id=firm_id, user_id=by, detail=detail)
        return self.firm(firm_id)

    def abandon_firm(self, firm_id: str, *, by: str) -> str:
        """Give up on a firm whose provisioning never finished: remove what provisioning recorded creating, shred its
        key and retire the id."""
        if self.firm(firm_id)["status"] != "provisioning":
            raise AuthError("only a firm that is still provisioning can be abandoned")
        from ..pg import provision

        detail = provision.abandon(firm_id, self)
        with self.lock:
            self.conn.execute("UPDATE firms SET status = 'deleted', deleted_at = datetime('now') WHERE id = ?", (firm_id,))
            self.keys.destroy(firm_id)
        self.event("firm_abandoned", firm_id=firm_id, user_id=by, detail=detail)
        return detail

    # ------------------------------------------------------------------ provisioning journal (pg.provision.Journal)
    def get(self, firm_id: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM provisioning WHERE firm_id = ?", (firm_id,)).fetchone()
        return dict(r) if r and r["state"] else None

    def put(self, firm_id: str, **fields: Any) -> None:
        allowed = {"resource", "name", "state", "error"}
        if set(fields) - allowed:
            raise ValueError(f"unknown provisioning fields {set(fields) - allowed}")
        self.conn.execute("INSERT INTO provisioning (firm_id) VALUES (?) ON CONFLICT (firm_id) DO NOTHING", (firm_id,))
        sets = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = datetime('now')"
        self.conn.execute(f"UPDATE provisioning SET {sets} WHERE firm_id = ?", (*fields.values(), firm_id))

    def claim(self, firm_id: str, holder: str, seconds: int) -> bool:
        with self.lock:
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

    def delete_firm(self, firm_id: str, *, by: str) -> None:
        """Offboarding: revoke access, crypto-shred the firm's data key, then remove its store and files.
        Irreversible: the firm's export bundle (backlog A1-10) must be delivered before this is called.

        The key is destroyed first, so documents and sealed returns are unreadable even if removing the store
        fails; a failed removal is recorded and can be retried with destroy_firm_data."""
        with self.lock:
            self.conn.execute("UPDATE firms SET status = 'deleted', deleted_at = datetime('now') WHERE id = ?", (firm_id,))
            self.conn.execute("UPDATE users SET disabled = 1 WHERE firm_id = ?", (firm_id,))
            self.conn.execute("UPDATE sessions SET revoked_at = datetime('now') WHERE user_id IN "
                              "(SELECT id FROM users WHERE firm_id = ?)", (firm_id,))
            self.keys.destroy(firm_id)
            self.event("firm_deleted", firm_id=firm_id, user_id=by)
        self.destroy_firm_data(firm_id, by=by)

    def destroy_firm_data(self, firm_id: str, *, by: str) -> str:
        """Remove a deleted firm's operational store (ledger, CRM, returns, workflow history, audit trail) and its
        tenant directory (vault ciphertext, agent state). Safe to retry."""
        if self.firm(firm_id)["status"] != "deleted":
            raise AuthError("only a deleted firm's data can be destroyed")
        if db.backend() == "postgres":
            rec = self.get(firm_id)
            if rec and rec["state"] == "removed":
                return "store already removed"
        tenant = self.tenant_dir(firm_id)
        try:
            detail = db.destroy_store(tenant / "state" / "agentledger.db")
            if tenant.exists():
                shutil.rmtree(tenant)
                detail += "; tenant directory removed"
        except Exception as exc:
            self.event("firm_data_destroy_failed", firm_id=firm_id, user_id=by, detail=f"{type(exc).__name__}: {exc}"[:300])
            raise
        if self.get(firm_id):
            self.put(firm_id, state="removed")
        self.event("firm_data_destroyed", firm_id=firm_id, user_id=by, detail=detail)
        return detail

    # ------------------------------------------------------------------ users and invitations
    def is_platform_admin(self, email: str) -> bool:
        r = self.conn.execute("SELECT role FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        return bool(r and r["role"] == "platform_admin")

    def bootstrap_admin(self, email: str, name: str, password: str) -> str:
        """First platform administrator; allowed only while no platform admin exists."""
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
        with self.lock:
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
        if self.conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise AuthError("an account with this email already exists")
        user_id = "u_" + secrets.token_hex(8)
        self.conn.execute("INSERT INTO users (id, firm_id, email, name, role, client_id, password_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (user_id, firm_id, email.strip().lower(), name, role, client_id, self.ph.hash(password)))
        self.event("user_created", firm_id=firm_id, user_id=user_id, email=email, detail=role)
        return user_id

    def user(self, user_id: str) -> dict[str, Any]:
        r = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not r:
            raise AuthError("user not found")
        return dict(r)

    def public_user(self, u: dict[str, Any]) -> dict[str, Any]:
        return {k: u[k] for k in ("id", "firm_id", "email", "name", "role", "client_id", "mfa_enrolled_at", "disabled",
                                  "last_login_at")}

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

    def login(self, email: str, password: str, ip: str | None = None) -> dict[str, Any]:
        """Step 1. Returns {"mfa": challenge} or {"enroll": Enrolment}. Never a session."""
        with self.lock:
            r = self.conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
            generic = AuthError("email or password is incorrect")
            if not r:
                self.ph.hash(password)  # equalise timing for unknown accounts
                self.event("login_failed", email=email, ip=ip, detail="unknown account")
                raise generic
            u = dict(r)
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
                failures = u["failed_logins"] + 1
                locked = _iso(_now() + LOCKOUT) if failures >= MAX_FAILURES else None
                self.conn.execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                                  (0 if locked else failures, locked, u["id"]))
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
        """Step 2. Verifies the one-time code (enrolling it if this is the first time) and issues a session token."""
        with self.lock:
            ch = self.conn.execute("SELECT * FROM challenges WHERE token_hash = ?", (_hash(challenge),)).fetchone()
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
                failures = u["failed_logins"] + 1
                locked = _iso(_now() + LOCKOUT) if failures >= MAX_FAILURES else None
                self.conn.execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                                  (0 if locked else failures, locked, u["id"]))
                if locked:
                    self.conn.execute("UPDATE challenges SET used_at = datetime('now') WHERE token_hash = ?", (_hash(challenge),))
                self.event("mfa_failed", firm_id=u["firm_id"], user_id=u["id"], ip=ip)
                raise AuthError("that code is not valid")
            self.conn.execute("UPDATE challenges SET used_at = datetime('now') WHERE token_hash = ?", (_hash(challenge),))
            if ch["kind"] == "enroll":
                self.conn.execute("UPDATE users SET totp_secret = ?, mfa_enrolled_at = datetime('now') WHERE id = ?",
                                  (self.keys.seal_text(u["firm_id"], secret, f"totp:{u['id']}"), u["id"]))
                self.event("mfa_enrolled", firm_id=u["firm_id"], user_id=u["id"], ip=ip)
            self.conn.execute("UPDATE users SET totp_last_step = ?, failed_logins = 0, last_login_at = datetime('now') WHERE id = ?",
                              (step, u["id"]))
            return self._start_session(u, ip, user_agent, "password+totp")

    def _start_session(self, u: dict[str, Any], ip: str | None, user_agent: str | None, method: str) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        self.conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, ip, user_agent, auth_method) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (_hash(token), u["id"], _iso(now), _iso(now), _iso(now + ABSOLUTE), ip, (user_agent or "")[:200], method))
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
        fresh_at = max(r["created_at"], r["stepped_up_at"] or "")
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
        with self.lock:
            r = self.conn.execute("SELECT * FROM sessions WHERE token_hash = ? AND revoked_at IS NULL", (_hash(token),)).fetchone()
            if not r:
                raise AuthError("sign in required")
            u = self.user(r["user_id"])
            if not u["totp_secret"]:
                raise AuthError("this account steps up through its sign-in provider")
            secret = self.keys.open_text(u["firm_id"], u["totp_secret"], f"totp:{u['id']}")
            step = totp.verify(secret, code, last_used_step=u["totp_last_step"])
            if step is None:
                self.event("step_up_failed", firm_id=u["firm_id"], user_id=u["id"], ip=ip)
                raise AuthError("that code is not valid")
            self.conn.execute("UPDATE users SET totp_last_step = ? WHERE id = ?", (step, u["id"]))
            self.conn.execute("UPDATE sessions SET stepped_up_at = ? WHERE token_hash = ?", (_iso(_now()), _hash(token)))
            self.event("step_up", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail="totp")

    # ------------------------------------------------------------------ identity provider (WorkOS AuthKit, ADR-0004)
    def idp_begin(self, idp: Any, purpose: str, redirect_uri: str, *, invite_token: str | None = None,
                  session_token: str | None = None) -> str:
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
        self.conn.execute("INSERT INTO idp_states (state_hash, purpose, invite_hash, session_hash, verifier, redirect_uri, expires_at) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (_hash(state), purpose, invite_hash, session_hash, sealed, redirect_uri, _iso(_now() + IDP_STATE_TTL)))
        return idp.authorization_url(redirect_uri, state, challenge, login_hint=hint, max_age=0 if purpose == "step_up" else None)

    def idp_complete(self, idp: Any, code: str, state: str, *, ip: str | None = None, user_agent: str | None = None) -> dict[str, Any]:
        """Finish a hosted sign-in. Returns {"purpose", "token"?}. Refuses unverified email, impersonation and
        sign-ins without a second factor; never creates an account without a matching invitation."""
        with self.lock:
            st = self.conn.execute("SELECT * FROM idp_states WHERE state_hash = ?", (_hash(state),)).fetchone()
            if not st or st["used_at"] or st["expires_at"] < _iso(_now()):
                raise AuthError("this sign-in attempt has expired; start again")
            self.conn.execute("UPDATE idp_states SET used_at = datetime('now') WHERE state_hash = ?", (_hash(state),))
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
        if not idp.multi_factor(auth):
            self.event("idp_refused", email=email, ip=ip, detail=f"no second factor ({auth.get('authentication_method')})")
            raise AuthError("turn on two-step verification or use a passkey in your sign-in settings, then try again")
        subject, method = str(wu["id"]), f"workos:{auth.get('authentication_method', 'unknown')}"
        with self.lock:
            if st["purpose"] == "invite":
                inv = self.conn.execute("SELECT * FROM invites WHERE token_hash = ?", (st["invite_hash"],)).fetchone()
                if not inv or inv["accepted_at"] or inv["expires_at"] < _iso(_now()):
                    raise AuthError("this invitation is invalid or has expired")
                if inv["email"] != email:
                    self.event("idp_refused", firm_id=inv["firm_id"], email=email, ip=ip, detail="email does not match invitation")
                    raise AuthError("sign in with the email address the invitation was sent to")
                if self.conn.execute("SELECT 1 FROM users WHERE email = ? OR idp_subject = ?", (email, subject)).fetchone():
                    raise AuthError("an account with this email already exists")
                user_id = "u_" + secrets.token_hex(8)
                name = " ".join(x for x in (wu.get("first_name"), wu.get("last_name")) if x) or email
                self.conn.execute("INSERT INTO users (id, firm_id, email, name, role, client_id, password_hash, idp_subject, mfa_enrolled_at) "
                                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                                  (user_id, inv["firm_id"], email, name, inv["role"], inv["client_id"], NO_PASSWORD, subject))
                self.conn.execute("UPDATE invites SET accepted_at = datetime('now') WHERE token_hash = ?", (st["invite_hash"],))
                self.event("invite_accepted", firm_id=inv["firm_id"], user_id=user_id, email=email, ip=ip, detail=method)
                return {"purpose": "invite", "token": self._start_session(self.user(user_id), ip, user_agent, method)}
            if st["purpose"] in ("link", "step_up"):
                r = self.conn.execute("SELECT * FROM sessions WHERE token_hash = ? AND revoked_at IS NULL", (st["session_hash"],)).fetchone()
                if not r:
                    raise AuthError("sign in required")
                u = self.user(r["user_id"])
                if st["purpose"] == "link":
                    if u["idp_subject"] or self.conn.execute("SELECT 1 FROM users WHERE idp_subject = ?", (subject,)).fetchone():
                        raise AuthError("this account or sign-in is already linked")
                    if u["email"] != email:
                        raise AuthError("sign in with the same email address as this account")
                    self.conn.execute("UPDATE users SET idp_subject = ? WHERE id = ?", (subject, u["id"]))
                    self.event("idp_linked", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail=method)
                    return {"purpose": "link"}
                if u["idp_subject"] != subject:
                    self.event("step_up_failed", firm_id=u["firm_id"], user_id=u["id"], ip=ip, detail="different identity")
                    raise AuthError("step up with the account you are signed in as")
                self.conn.execute("UPDATE sessions SET stepped_up_at = ? WHERE token_hash = ?", (_iso(_now()), st["session_hash"]))
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
            return {"purpose": "login", "token": self._start_session(u, ip, user_agent, method)}

    def logout(self, token: str) -> None:
        r = self.conn.execute("SELECT user_id FROM sessions WHERE token_hash = ?", (_hash(token),)).fetchone()
        self.conn.execute("UPDATE sessions SET revoked_at = datetime('now') WHERE token_hash = ?", (_hash(token),))
        if r:
            u = self.user(r["user_id"])
            self.event("logout", firm_id=u["firm_id"], user_id=u["id"])
