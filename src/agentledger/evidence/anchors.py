"""Audit chain anchoring (backlog F-13, components C28 and C34; Q33).

A firm's audit trail is a hash chain inside its own store (audit.py; on PostgreSQL the database's `write_audit`).
Whoever holds the store's owner credentials can rewrite or truncate that chain and recompute every hash after the
change, so the chain alone proves nothing against the store's owner. An anchor fixes the chain's head outside the
store: a small JSON object in the firm's object store under its own prefix (anchors/<firm>/<utc date>/<seq>.json,
immutable naming, never overwritten) carrying the audit head (last seq, head hash, count), the head of every hashed
workflow stream, the previous anchor (key and digest, so anchors chain too), the platform build, the time, the lock
probe's outcome, and an HMAC-SHA256 under a key derived from the firm's data key: an anchor cannot be forged without
the key, and dies with the key when the firm is crypto-shredded. Under an R2 bucket lock rule on the anchors/ prefix
the objects can be neither deleted nor overwritten (ADR-0006 amendment), so a later chain must still hold every
anchored (seq, hash): one that does not is reported as `chain_rewritten` or `chain_truncated` with the anchor that
proves it.

The object store is the authority; `audit_anchors` in the firm store indexes what was written and when each anchor
was last verified. An anchor is written only when the chain moved since the last one, the anchor job's own run
records (`agent.run` by the anchor agent) aside: those alone never justify a new anchor and never count as
unanchored activity, otherwise every scheduled run would anchor the record of the previous run.

The lock is probed at every run (AGENTLEDGER_BLOBS=s3): a probe object is written under anchors/<firm>/probe/ (one
per UTC day, reused while it exists) and its deletion is attempted; the delete MUST be refused. A delete that
succeeds (a bucket without the rule, a local S3 server) is recorded in the audit (`evidence.anchor_lock_missing`,
once per change of outcome) and reported in every anchor and integrity report; anchors are still written, but
nothing here ever claims they are locked when the probe says otherwise. With file blobs there is no lock to probe:
the gap is reported the same way (`unchecked`).

`anchor_missing` says that unanchored activity older than AGENTLEDGER_ANCHOR_MAX_AGE_HOURS (default 24) exists: the
anchor job has not run, or failed, for that long on a firm that was active.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .. import audit
from ..db import one, rows, unit_of_work
from ..security.crypto import Keyring
from . import blobs
from .records import _has_table

ANCHOR_AGENT_ID = "evidence-anchor"            # the agent spec id in config/agents.yaml (its run records are excluded)
MAX_AGE_ENV = "AGENTLEDGER_ANCHOR_MAX_AGE_HOURS"
DEFAULT_MAX_AGE_HOURS = 24.0
VERSION = 1
PROBE = "probe/"
LOCKED, MISSING, UNCHECKED = "locked", "missing", "unchecked"
SIGNATURE_LABEL = "audit-anchor"               # Keyring.derive label: a per-firm key, stable across rotation
ALG = "HMAC-SHA256"
EXCLUDED_ACTION = "agent.run"


class AnchorError(Exception):
    pass


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def build_id() -> str:
    """What is running: AGENTLEDGER_BUILD, else the BUILD_SHA file the release writes (the repository's copy says dev)."""
    env = os.environ.get("AGENTLEDGER_BUILD", "").strip()
    if env:
        return env
    home = os.environ.get("AGENTLEDGER_HOME")
    for p in [Path("/app/BUILD_SHA")] + ([Path(home) / "BUILD_SHA"] if home else []) + [Path(__file__).resolve().parents[3] / "BUILD_SHA"]:
        try:
            text = p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "dev"


def max_age_hours() -> float:
    try:
        return float(os.environ.get(MAX_AGE_ENV, "") or DEFAULT_MAX_AGE_HOURS)
    except ValueError:
        return DEFAULT_MAX_AGE_HOURS


def _parse(at: str) -> datetime:
    t = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def anchor_key(at: datetime, seq: int) -> str:
    """<utc date>/<seq>.json; the sequence is zero-padded so the store lists anchors in chain order."""
    return f"{at.date().isoformat()}/{int(seq):012d}.json"


def is_anchor_key(key: str) -> bool:
    return key.endswith(".json") and not key.startswith(PROBE) and "/" in key


class Anchors:
    """One firm's anchors: the store they live in, the chain they fix, the key that signs them."""

    def __init__(self, conn: Any, store: blobs.BlobStore, *, firm_id: str | None, keyring: Keyring | None, location: str,
                 now: Callable[[], datetime] | None = None, max_age: float | None = None):
        self.conn = conn
        self.store = store
        self.firm_id = firm_id
        self.keyring = keyring
        self.location = location
        self.now = now or utcnow
        self.max_age = timedelta(hours=max_age if max_age is not None else max_age_hours())

    @classmethod
    def for_foundry(cls, foundry: Any, **kw: Any) -> "Anchors":
        """The anchors of the firm a foundry works on: its vault carries the firm id and keyring inside a firm (the
        API's firm_context), neither in the single-firm development store (its anchors are unsigned, and say so)."""
        vault = foundry.vault
        firm_id = getattr(vault, "firm_id", None)
        store, location = blobs.anchors_for_firm(Path(foundry.paths.data) / blobs.ANCHORS, firm_id)
        return cls(foundry.conn, store, firm_id=firm_id, keyring=getattr(vault, "keyring", None), location=location, **kw)

    # ------------------------------------------------------------------------------------------- the chain
    def head(self) -> dict[str, Any]:
        last = one(self.conn, "SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1")
        n = one(self.conn, "SELECT COUNT(*) AS n FROM audit") or {"n": 0}
        return {"seq": int(last["seq"]) if last else 0, "hash": last["hash"] if last else None, "count": int(n["n"] or 0)}

    def workflow_heads(self) -> dict[str, Any]:
        """The head (seq, hash) of every hashed workflow stream (workflow/engine.py), sorted by stream."""
        if not _has_table(self.conn, "workflow_events"):
            return {"streams": 0, "events": 0, "heads": []}
        heads = rows(self.conn, "SELECT w.workflow_id, w.seq, w.hash FROM workflow_events w WHERE w.seq = "
                                "(SELECT MAX(x.seq) FROM workflow_events x WHERE x.workflow_id = w.workflow_id) ORDER BY w.workflow_id")
        total = one(self.conn, "SELECT COUNT(*) AS n FROM workflow_events") or {"n": 0}
        return {"streams": len(heads), "events": int(total["n"] or 0), "heads": [[h["workflow_id"], int(h["seq"]), h["hash"]] for h in heads]}

    def unanchored(self, since_seq: int, actors: tuple[str, ...] = (ANCHOR_AGENT_ID,)) -> list[dict[str, Any]]:
        """Audit records after `since_seq` that are activity: everything but the anchor job's own run records."""
        out = []
        for r in rows(self.conn, "SELECT seq, at, actor, action FROM audit WHERE seq > ? ORDER BY seq", int(since_seq)):
            if r["action"] == EXCLUDED_ACTION and r["actor"] in actors:
                continue
            out.append(r)
        return out

    # ------------------------------------------------------------------------------------------- signing
    def _key(self) -> bytes | None:
        if self.keyring is None or not self.firm_id:
            return None
        return self.keyring.derive(self.firm_id, SIGNATURE_LABEL)

    def sign(self, body: dict[str, Any]) -> dict[str, Any] | None:
        key = self._key()
        if key is None:
            return None
        return {"alg": ALG, "key": f"firm-data-key:{SIGNATURE_LABEL}", "value": hmac.new(key, canonical(body), hashlib.sha256).hexdigest()}

    def signature_status(self, obj: dict[str, Any]) -> str:
        """valid | invalid | unsigned (written without a firm key) | unverifiable (signed, but this process has no key)."""
        sig = obj.get("signature")
        if not sig:
            return "unsigned"
        key = self._key()
        if key is None:
            return "unverifiable"
        body = {k: v for k, v in obj.items() if k != "signature"}
        expected = hmac.new(key, canonical(body), hashlib.sha256).hexdigest()
        return "valid" if sig.get("alg") == ALG and hmac.compare_digest(expected, str(sig.get("value", ""))) else "invalid"

    # ------------------------------------------------------------------------------------------- the store
    def keys(self) -> list[str]:
        return sorted(k for k in self.store.keys() if is_anchor_key(k))

    def read(self, key: str) -> tuple[dict[str, Any], str]:
        raw = self.store.get(key)
        return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()

    def latest_recorded(self) -> dict[str, Any] | None:
        return one(self.conn, "SELECT * FROM audit_anchors ORDER BY seq DESC LIMIT 1")

    def latest_stored(self) -> tuple[str, dict[str, Any], str] | None:
        keys = self.keys()
        if not keys:
            return None
        obj, digest = self.read(keys[-1])
        return keys[-1], obj, digest

    # ------------------------------------------------------------------------------------------- the lock
    def probe_lock(self) -> dict[str, Any]:
        """Write a probe object under the anchors prefix and try to delete it. Locked means the bytes are still there
        afterwards; a delete that goes through means nothing protects the anchors. File blobs cannot be locked."""
        at = self.now()
        if isinstance(self.store, blobs.FileBlobs):
            return {"checked": False, "locked": False, "status": UNCHECKED, "at": iso(at), "key": None,
                    "detail": "file blobs have no lock: the anchors are as deletable as the directory they are in "
                              "(AGENTLEDGER_BLOBS=s3 with an R2 bucket lock rule on anchors/ is what locks them)"}
        key = f"{PROBE}{at.date().isoformat()}"
        if not self.store.exists(key):
            self.store.put(key, canonical({"probe": True, "firm": self.firm_id or "dev", "at": iso(at)}))
        error = None
        try:
            self.store.delete(key)
        except Exception as exc:                                     # a refusal is the hoped-for outcome
            error = f"{type(exc).__name__}: {exc}"[:200]
        locked = self.store.exists(key)
        if locked:
            detail = "the store refused to delete the probe object: the anchors prefix is locked" + (f" ({error})" if error else "")
        elif error:
            detail = f"the delete failed ({error}) yet the probe object is gone: the lock cannot be confirmed"
        else:
            detail = "the probe object was deleted: no bucket lock protects the anchors prefix"
        return {"checked": True, "locked": locked, "status": LOCKED if locked else MISSING, "at": iso(at), "key": key, "detail": detail}

    def _record_lock_status(self, lock: dict[str, Any], previous: str | None, actor: str) -> str | None:
        """One audit record per change of the probe's outcome: the gap, or its closing. Returns the action recorded."""
        if lock["status"] == previous:
            return None
        action = "evidence.anchor_lock_confirmed" if lock["status"] == LOCKED else "evidence.anchor_lock_missing"
        audit.record(self.conn, actor, "system", action, {"store": self.location, "status": lock["status"], "detail": lock["detail"],
                                                          "probe": lock.get("key"), "previous": previous})
        return action

    # ------------------------------------------------------------------------------------------- the job
    def _mismatch(self, seq: int, head_hash: str, key: str) -> dict[str, Any] | None:
        """Whether the current chain still holds the anchored record: None, or the finding that proves it does not."""
        row = one(self.conn, "SELECT hash FROM audit WHERE seq = ?", int(seq))
        if row is None:
            current = self.head()
            kind = "chain_truncated" if current["seq"] < int(seq) else "chain_rewritten"
            return {"kind": kind, "anchor": key, "seq": int(seq), "anchored_hash": head_hash, "found": None, "current_seq": current["seq"]}
        if row["hash"] != head_hash:
            return {"kind": "chain_rewritten", "anchor": key, "seq": int(seq), "anchored_hash": head_hash, "found": row["hash"]}
        return None

    def anchor(self, *, actor: str = ANCHOR_AGENT_ID) -> dict[str, Any]:
        """Probe the lock, check the chain against the last anchor, and write a new anchor when the chain moved.
        Returns what happened; never raises on a mismatch (the finding is the result)."""
        lock = self.probe_lock()
        last = self.latest_recorded()
        if last is None:                                             # an index that knows nothing: the store decides
            stored = self.latest_stored()
            if stored is not None:
                key, obj, digest = stored
                last = {"object_key": key, "seq": int(obj["audit"]["seq"]), "head_hash": obj["audit"]["hash"], "object_sha256": digest,
                        "lock_status": (obj.get("lock") or {}).get("status")}
        recorded = self._record_lock_status(lock, last["lock_status"] if last else None, actor)
        out: dict[str, Any] = {"firm_id": self.firm_id or "dev", "store": self.location, "lock": lock, "lock_event": recorded,
                               "anchored": False, "reason": None, "anchor": None, "mismatch": None,
                               "previous": {"key": last["object_key"], "seq": int(last["seq"])} if last else None}
        if last is not None:
            found = self._mismatch(int(last["seq"]), last["head_hash"], last["object_key"])
            if found:                                                # never bless a chain that contradicts its anchor
                out["mismatch"], out["reason"] = found, found["kind"]
                return out
        since = int(last["seq"]) if last else 0
        activity = self.unanchored(since, (ANCHOR_AGENT_ID, actor))
        out["unanchored"] = len(activity)
        head = self.head()
        if head["seq"] == 0:
            out["reason"] = "empty chain"
            return out
        if not activity:
            out["reason"] = "head unchanged"
            return out
        at = self.now()
        body: dict[str, Any] = {
            "version": VERSION, "firm_id": self.firm_id or "dev", "store": self.location, "at": iso(at), "build": build_id(),
            "audit": head, "workflow_events": self.workflow_heads(), "lock": lock,
            "previous": {"key": last["object_key"], "sha256": last["object_sha256"]} if last else None,
        }
        obj = {**body, "signature": self.sign(body)}
        key = anchor_key(at, head["seq"])
        if self.store.exists(key):
            raise AnchorError(f"anchor {key} already exists and is never overwritten")
        raw = json.dumps(obj, sort_keys=True, indent=1).encode()
        self.store.put(key, raw)
        with unit_of_work(self.conn):
            self.conn.execute("INSERT INTO audit_anchors (object_key, seq, head_hash, object_sha256, written_at, lock_status) VALUES (?, ?, ?, ?, ?, ?)",
                              (key, head["seq"], head["hash"], hashlib.sha256(raw).hexdigest(), iso(at), lock["status"]))
        out.update(anchored=True, anchor={"key": key, **head, "at": iso(at), "signed": obj["signature"] is not None})
        return out

    # ------------------------------------------------------------------------------------------- verification
    def check(self) -> dict[str, Any]:
        """Every stored anchor against the current chain: its signature, its (seq, hash) still in the chain, its link
        to the previous anchor, and, for the latest, every anchored workflow stream head; plus the chain's own
        verification, the age of unanchored activity and the lock probe's last outcome. Read-only apart from
        `verified_at` on anchors that verified."""
        now = self.now()
        report: dict[str, Any] = {
            "store": self.location, "count": 0, "latest": None, "signed": False, "unsigned": [], "unverifiable": [],
            "signature_failures": [], "unreadable": [], "chain_rewritten": [], "chain_truncated": [], "workflow_rewritten": [],
            "workflow_truncated": [], "anchors_missing_from_store": [], "anchors_replaced": [], "chain": None,
            "anchor_missing": False, "unanchored_since": None, "unanchored": 0, "max_age_hours": self.max_age.total_seconds() / 3600,
            "lock": None, "store_error": None, "ok": False,
        }
        try:
            keys = self.keys()
        except Exception as exc:                                     # the store cannot be listed: reported, never hidden
            report["store_error"] = f"{type(exc).__name__}: {exc}"[:200]
            report["chain"] = audit.verify(self.conn)
            return report
        report["count"] = len(keys)
        anchors: list[tuple[str, dict[str, Any], str]] = []
        for key in keys:
            try:
                obj, digest = self.read(key)
                int(obj["audit"]["seq"])
            except Exception as exc:
                report["unreadable"].append({"key": key, "error": f"{type(exc).__name__}: {exc}"[:200]})
                continue
            anchors.append((key, obj, digest))
        digests = {key: digest for key, _, digest in anchors}
        verified: list[str] = []
        for key, obj, _ in anchors:
            status = self.signature_status(obj)
            if status == "invalid":
                report["signature_failures"].append(key)
            elif status == "unsigned":
                report["unsigned"].append(key)
            elif status == "unverifiable":
                report["unverifiable"].append(key)
            found = self._mismatch(int(obj["audit"]["seq"]), str(obj["audit"]["hash"]), key)
            if found:
                report[found["kind"]].append(found)
            prev = obj.get("previous") or None
            if prev and prev.get("key"):
                if prev["key"] not in digests:
                    report["anchors_missing_from_store"].append({"anchor": key, "missing": prev["key"]})
                elif digests[prev["key"]] != prev.get("sha256"):
                    report["anchors_replaced"].append({"anchor": key, "replaced": prev["key"]})
            if status != "invalid" and not found:
                verified.append(key)
        for r in rows(self.conn, "SELECT object_key FROM audit_anchors ORDER BY seq"):
            if r["object_key"] not in digests and not any(u["key"] == r["object_key"] for u in report["unreadable"]):
                report["anchors_missing_from_store"].append({"anchor": None, "missing": r["object_key"], "indexed": True})
        latest_seq = 0
        if anchors:
            key, obj, _ = max(anchors, key=lambda a: int(a[1]["audit"]["seq"]))
            latest_seq = int(obj["audit"]["seq"])
            report["latest"] = {"key": key, "seq": latest_seq, "hash": obj["audit"]["hash"], "count": obj["audit"].get("count"),
                                "at": obj.get("at"), "build": obj.get("build"), "signature": self.signature_status(obj)}
            report["lock"] = obj.get("lock")
            self._check_workflows(key, obj, report)
        report["signed"] = bool(anchors) and len(verified) == len(anchors) and not report["unsigned"] and not report["unverifiable"]
        report["chain"] = audit.verify(self.conn)
        activity = self.unanchored(latest_seq)
        report["unanchored"] = len(activity)
        if activity:
            oldest = _parse(activity[0]["at"])
            report["unanchored_since"] = activity[0]["at"]
            report["anchor_missing"] = now - oldest > self.max_age
        if verified:
            try:
                with unit_of_work(self.conn):
                    for key in verified:
                        self.conn.execute("UPDATE audit_anchors SET verified_at = ? WHERE object_key = ?", (iso(now), key))
            except Exception:                                        # bookkeeping only: a read-only session changes nothing
                pass
        report["ok"] = bool(report["chain"] and report["chain"].get("ok")) and not any(
            report[k] for k in ("signature_failures", "unreadable", "chain_rewritten", "chain_truncated", "workflow_rewritten",
                                "workflow_truncated", "anchors_missing_from_store", "anchors_replaced"))
        return report

    def _check_workflows(self, key: str, obj: dict[str, Any], report: dict[str, Any]) -> None:
        heads = (obj.get("workflow_events") or {}).get("heads") or []
        if not heads or not _has_table(self.conn, "workflow_events"):
            if heads:
                report["workflow_truncated"].append({"anchor": key, "streams": len(heads), "found": "no workflow_events table"})
            return
        for workflow_id, seq, head_hash in heads:
            row = one(self.conn, "SELECT hash FROM workflow_events WHERE workflow_id = ? AND seq = ?", workflow_id, int(seq))
            if row is None:
                report["workflow_truncated"].append({"anchor": key, "workflow_id": workflow_id, "seq": int(seq), "anchored_hash": head_hash})
            elif row["hash"] != head_hash:
                report["workflow_rewritten"].append({"anchor": key, "workflow_id": workflow_id, "seq": int(seq), "anchored_hash": head_hash,
                                                     "found": row["hash"]})


# ------------------------------------------------------------------------------------------------- jobs
def for_firm(plat: Any, firm_id: str, **kw: Any) -> tuple[Anchors, Any]:
    """The anchors of one firm from the platform's records: its store (firm-wide scope) and its key. Returns the
    Anchors and the store connection (the caller closes it)."""
    from .. import db

    plat.firm(firm_id)                                # AuthError for a firm the platform does not know
    path = plat.tenant_dir(firm_id) / "state" / "agentledger.db"
    if not db.store_exists(path):
        raise AnchorError(f"the store of firm {firm_id} does not exist; nothing to anchor")
    conn = db.open_store(path)
    conn.set_scope(["*"])
    store, location = blobs.anchors_for_firm(plat.tenant_dir(firm_id) / blobs.ANCHORS, firm_id)
    return Anchors(conn, store, firm_id=firm_id, keyring=plat.keys, location=location, **kw), conn


def anchor_all(plat: Any, *, actor: str = ANCHOR_AGENT_ID) -> dict[str, dict[str, Any]]:
    """Anchor every active firm (the scheduled job). A firm whose run fails is reported, and the others still run."""
    out: dict[str, dict[str, Any]] = {}
    for firm in plat.firms():
        if firm["status"] != "active":
            continue
        try:
            anchors, conn = for_firm(plat, firm["id"])
            try:
                out[firm["id"]] = anchors.anchor(actor=actor)
            finally:
                conn.close()
        except Exception as exc:
            out[firm["id"]] = {"firm_id": firm["id"], "anchored": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    return out
