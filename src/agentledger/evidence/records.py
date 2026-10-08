"""Evidence lifecycle (backlog F-06): versions, retention classes, legal holds and deletion receipts.

* Every stored document has an append-only version history (`document_versions`).
* Each document gets a retention class and a retain-until date from config/retention.yaml.
* A legal hold on a client (or a single document) blocks deletion until a CPA releases it with a reason.
* Deletion happens only when a CPA runs it, only after retention ends, never under a hold, and leaves a receipt
  (`deletion_receipts`) plus an audit record. Content-addressed blobs shared by documents still retained are kept.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from .. import audit
from ..db import one, rows, unit_of_work

REVIEWER = ("cpa",)


class RetentionError(PermissionError):
    pass


def load_policy(config_dir: Path) -> dict[str, Any]:
    p = Path(config_dir) / "retention.yaml"
    policy = yaml.safe_load(p.read_text(encoding="utf-8"))
    for name, c in policy["classes"].items():
        if c.get("years") is not None and int(c["years"]) < 3:
            raise ValueError(f"retention class {name} is shorter than the 3-year statutory minimum")
    return policy


def policy() -> dict[str, Any]:
    """The installation's policy: $AGENTLEDGER_HOME/config/retention.yaml, else the one shipped with the code."""
    import os

    home = os.environ.get("AGENTLEDGER_HOME")
    for root in ([Path(home)] if home else []) + [Path(__file__).resolve().parents[3]]:
        if (root / "config" / "retention.yaml").exists():
            return load_policy(root / "config")
    raise FileNotFoundError("config/retention.yaml not found")


def retention_for(policy: dict[str, Any], doc_type: str | None, tax_year: int | None, received: str) -> tuple[str, str | None]:
    """(class, retain-until ISO date or None for 'until released')."""
    cls = (policy.get("by_doc_type") or {}).get(doc_type or "", policy["default"])
    years = policy["classes"][cls].get("years")
    if years is None:
        return cls, None
    start = date(int(tax_year), 12, 31) if tax_year else datetime.fromisoformat(received).date()
    try:
        until = start.replace(year=start.year + int(years))
    except ValueError:                       # 29 February
        until = start.replace(year=start.year + int(years), day=28)
    return cls, until.isoformat()


def add_version(conn: Any, document_id: str, locator: str, sha256: str, size: int, actor: str, reason: str = "received") -> int:
    with unit_of_work(conn):
        last = one(conn, "SELECT MAX(version) AS v FROM document_versions WHERE document_id = ?", document_id)
        version = int((last or {}).get("v") or 0) + 1
        conn.execute("INSERT INTO document_versions (document_id, version, locator, sha256, size, created_at, created_by, reason) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (document_id, version, locator, sha256, size, audit.now(), actor, reason))
    return version


def versions(conn: Any, document_id: str) -> list[dict[str, Any]]:
    return rows(conn, "SELECT * FROM document_versions WHERE document_id = ? ORDER BY version", document_id)


# ------------------------------------------------------------------------------------------------- legal holds
def place_hold(conn: Any, *, client_id: str, reason: str, actor: str, role: str, document_id: str | None = None) -> int:
    if role not in REVIEWER:
        raise RetentionError("placing a legal hold needs a CPA")
    if len(reason.strip()) < 10:
        raise RetentionError("a legal hold needs a reason (matter, notice or request)")
    with unit_of_work(conn):
        cur = conn.execute("INSERT INTO legal_holds (client_id, document_id, reason, placed_by, placed_at) VALUES (?, ?, ?, ?, ?)",
                           (client_id, document_id, reason.strip(), actor, audit.now()))
        hold_id = int(cur.lastrowid or 0)
        audit.record(conn, actor, role, "evidence.hold_placed", {"hold_id": hold_id, "document_id": document_id, "reason": reason.strip()},
                     client_id=client_id)
    return hold_id


def release_hold(conn: Any, hold_id: int, *, reason: str, actor: str, role: str) -> None:
    if role not in REVIEWER:
        raise RetentionError("releasing a legal hold needs a CPA")
    if len(reason.strip()) < 10:
        raise RetentionError("releasing a legal hold needs a reason")
    with unit_of_work(conn):
        h = one(conn, "SELECT * FROM legal_holds WHERE id = ? AND released_at IS NULL", hold_id)
        if not h:
            raise KeyError(f"no active hold {hold_id}")
        conn.execute("UPDATE legal_holds SET released_by = ?, released_at = ?, release_reason = ? WHERE id = ?",
                     (actor, audit.now(), reason.strip(), hold_id))
        audit.record(conn, actor, role, "evidence.hold_released", {"hold_id": hold_id, "reason": reason.strip()}, client_id=h["client_id"])


def on_hold(conn: Any, document: dict[str, Any]) -> bool:
    return bool(one(conn, "SELECT 1 AS x FROM legal_holds WHERE released_at IS NULL AND client_id = ? AND (document_id IS NULL OR document_id = ?)",
                    document["client_id"], document["id"]))


# ------------------------------------------------------------------------------------------------- deletion
def due_for_deletion(conn: Any, today: date) -> list[dict[str, Any]]:
    out = []
    for d in rows(conn, "SELECT * FROM documents WHERE deleted_at IS NULL AND retain_until IS NOT NULL AND retain_until < ?",
                  today.isoformat()):
        if d["client_id"] and on_hold(conn, d):
            continue
        out.append(d)
    return out


def purge_expired(conn: Any, vault: Any, today: date, *, actor: str, role: str, reason: str) -> list[dict[str, Any]]:
    """Delete evidence whose retention has ended (CPA only). Returns the receipts."""
    if role not in REVIEWER:
        raise RetentionError("deleting evidence needs a CPA")
    if len(reason.strip()) < 10:
        raise RetentionError("record why this deletion run happens (for example the annual retention review)")
    receipts = []
    for d in due_for_deletion(conn, today):
        with unit_of_work(conn):
            if d["client_id"] and on_hold(conn, d):           # re-checked inside the transaction
                continue
            locs = {v["locator"] for v in versions(conn, d["id"])} | ({d["vault_path"]} if d["vault_path"] else set())
            conn.execute("UPDATE documents SET deleted_at = ? WHERE id = ?", (audit.now(), d["id"]))
            for loc in sorted(locs):
                # Content-addressed: another document still retained may hold the same bytes.
                shared = one(conn, "SELECT 1 AS x FROM documents d JOIN document_versions v ON v.document_id = d.id "
                                   "WHERE v.locator = ? AND d.deleted_at IS NULL", loc)
                if not shared:
                    vault.delete(loc)
            cur = conn.execute("INSERT INTO deletion_receipts (document_id, client_id, sha256, locators, retention_class, retain_until, "
                               "deleted_by, deleted_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (d["id"], d["client_id"], d["sha256"], ",".join(sorted(locs)), d["retention_class"], d["retain_until"],
                                actor, audit.now(), reason.strip()))
            receipt = {"receipt_id": int(cur.lastrowid or 0), "document_id": d["id"], "sha256": d["sha256"],
                       "retention_class": d["retention_class"], "retain_until": d["retain_until"]}
            audit.record(conn, actor, role, "evidence.deleted", receipt, client_id=d["client_id"])
            receipts.append(receipt)
    return receipts
