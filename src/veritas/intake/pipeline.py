"""Autonomous intake: any channel, any format -> classified, matched to a client, filed, linked.

Client segregation is enforced here: a document is filed under exactly one client's
vault, or held in the review inbox. It is never guessed into a client.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .. import audit
from ..ai.router import Router, Unavailable
from ..db import one, rows
from ..ledger import store
from ..security.vault import Vault, as_vault
from .classify import FOLDERS, SYSTEM, Classification, detect, ground_fields
from .extract import Part, explode

AUTO_FILE_CONFIDENCE = 0.75
SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def ingest(conn: sqlite3.Connection, router: Router | None, vault: "Vault | Path", name: str, data: bytes, *, channel: str,
           sender: str | None = None, client_hint: str | None = None, actor: str = "intake-agent") -> list[dict[str, Any]]:
    vault = as_vault(vault)
    results = []
    for part in explode(name, data, sender=sender):
        results.append(_ingest_part(conn, router, vault, part, channel, client_hint, actor))
    return results


def _ingest_part(conn, router, vault: Vault, part: Part, channel: str, client_hint: str | None, actor: str) -> dict[str, Any]:
    sha = hashlib.sha256(part.data).hexdigest()
    dup = one(conn, "SELECT id, client_id, status, vault_path FROM documents WHERE sha256 = ?", sha)
    if dup:
        return {**dup, "duplicate": True, "name": part.name}

    clients = store.list_clients(conn)
    client_id, match_reason = match_client(clients, part, client_hint)
    client = next((c for c in clients if c["id"] == client_id), None)

    det = detect(part.text) if part.text else None
    cls: Classification | None = None
    by = "deterministic"
    is_cover_note = part.parent is not None and part.name.endswith("-body.txt") and not (det and det.doc_type)
    if is_cover_note:
        # An email's own body is correspondence unless it *is* a recognised form; its attachments
        # are classified separately. This also avoids spending a model call on "see attached".
        cls = Classification(doc_type="Correspondence", tax_year=None, party_names=[], tin_last4=[], fields=[],
                             summary=(part.subject or part.text.strip().splitlines()[0])[:200], confidence=0.9)
    elif router is not None and (part.text.strip() or part.images):
        user = (f"File name: {part.name}\nEmail subject: {part.subject or '-'}\n"
                f"Deterministic pre-classification: {det.doc_type if det else None}\n<document>\n{part.text[:30000]}\n</document>")
        # Local models are on-premises, so no taxpayer data leaves the firm. Escalation to
        # the frontier tier happens only when the matched client has a §7216 consent on file.
        may_escalate = bool(client and client.get("consent_7216_at"))
        role = "classify" if part.text.strip() else "vision"
        try:
            cls, by = router.structured(role, system=SYSTEM, user=user, schema=Classification,
                                        images=part.images or None if role == "vision" else None,
                                        escalate=False, client_id=client_id)
            if cls.confidence < AUTO_FILE_CONFIDENCE and may_escalate:
                cls, by = router.structured(role, system=SYSTEM, user=user, schema=Classification, images=part.images or None,
                                            escalate=True, client_id=client_id)
        except Unavailable:
            cls = None
    if cls is not None:
        if cls.confidence > 1:  # some local models answer in percent
            cls.confidence = min(cls.confidence / 100, 1.0)
        cls = ground_fields(cls, part.text or "")
        if det and det.doc_type and det.doc_type != cls.doc_type:
            cls.confidence = min(cls.confidence, 0.6)  # disagreement between tiers -> human review
        if client_id is None:
            client_id, match_reason = match_client(clients, part, client_hint, cls)
    doc_type = (cls.doc_type if cls else None) or (det.doc_type if det else None) or "Other"
    tax_year = (cls.tax_year if cls else None) or (det.tax_year if det else None)
    confidence = cls.confidence if cls else (0.8 if det and det.doc_type else 0.0)

    status = "filed" if client_id and confidence >= AUTO_FILE_CONFIDENCE else "needs_review"
    doc_id = "doc_" + secrets.token_hex(6)
    rel = _vault_path(client_id if status == "filed" else None, tax_year, doc_type, part.name, sha)
    vault.write(rel, part.data)
    fields = {kv.name: kv.value for kv in cls.fields} if cls else {}
    conn.execute(
        "INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, sender, parent_id, doc_type, "
        "tax_year, confidence, status, vault_path, fields, summary, classified_by, text_excerpt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, client_id if status == "filed" else None, sha, part.name, part.media_type, channel, audit.now(), part.sender,
         part.parent, doc_type, tax_year, confidence, status, str(rel).replace("\\", "/"), json.dumps(fields),
         cls.summary if cls else part.note, by, (part.text or "")[:4000]),
    )
    audit.record(conn, actor, "agent", "document.received",
                 {"document_id": doc_id, "name": part.name, "doc_type": doc_type, "status": status, "match": match_reason,
                  "classified_by": by, "channel": channel}, client_id=client_id if status == "filed" else None)
    if status == "filed":
        _effects(conn, client_id, doc_id, doc_type, tax_year, fields, part.text or "")
    return {"id": doc_id, "client_id": client_id if status == "filed" else None, "status": status, "doc_type": doc_type,
            "tax_year": tax_year, "confidence": confidence, "vault_path": str(rel), "match": match_reason, "name": part.name,
            "suggested_client": client_id if status != "filed" else None}


def match_client(clients: list[dict[str, Any]], part: Part, hint: str | None,
                 cls: Classification | None = None) -> tuple[str | None, str]:
    if hint and any(c["id"] == hint for c in clients):
        return hint, "explicit (uploaded into client workspace)"
    sender = (part.sender or "").lower()
    by_email = [c["id"] for c in clients if sender and sender in c["emails"]]
    if len(by_email) == 1:
        return by_email[0], f"sender {sender} is a known client contact"
    text = (part.text or "")[:30000].lower()
    tins = set(cls.tin_last4) if cls else set()
    by_tin = [c["id"] for c in clients if c.get("tax_id_last4") and (c["tax_id_last4"] in tins or
              re.search(rf"(x{{2,}}|\*{{2,}}|\d{{2}}-\d{{3}}|-)\s?{c['tax_id_last4']}\b", text))]
    if len(by_tin) == 1:
        return by_tin[0], "TIN last-4 matches"
    names = [n.lower() for n in (cls.party_names if cls else [])]
    by_name = []
    for c in clients:
        candidates = [c["name"].lower(), *[a.lower() for a in c["aliases"]]]
        if any(n and (n in text or any(n in pn or pn in n for pn in names if len(pn) > 3)) for n in candidates):
            by_name.append(c["id"])
    if len(by_name) == 1:
        return by_name[0], "client name appears in document"
    if len(by_name) > 1 or len(by_tin) > 1 or len(by_email) > 1:
        return None, "ambiguous: matches more than one client"
    return None, "no client match"


def _vault_path(client_id: str | None, year: int | None, doc_type: str, name: str, sha: str) -> Path:
    stem = SAFE.sub("_", Path(name).stem)[:60]
    ext = Path(name).suffix.lower() or ".bin"
    folder = FOLDERS.get(doc_type, "other")
    fname = f"{date.today().isoformat()}_{SAFE.sub('_', doc_type)}_{stem}_{sha[:8]}{ext}"
    if client_id is None:
        return Path("_review") / fname
    return Path(client_id) / str(year or "undated") / folder / fname


def _money(v: str) -> Decimal | None:
    try:
        return Decimal(re.sub(r"[^\d.\-]", "", v))
    except InvalidOperation:
        return None


def _effects(conn, client_id: str, doc_id: str, doc_type: str, year: int | None, fields: dict[str, str], text: str) -> None:
    """Downstream automation once a document is filed to a client."""
    if doc_type in ("1099-NEC", "1099-MISC", "1099-K") and year:
        amount = None
        for k, v in fields.items():
            if re.search(r"box\s*1|nonemployee|gross|total|amount", k, re.I) and _money(v):
                amount = _money(v)
                break
        if amount:
            payer = next((v for k, v in fields.items() if "payer" in k.lower() or "filer" in k.lower()), None)
            conn.execute("INSERT INTO info_returns (client_id, document_id, form, tax_year, payer, amount) VALUES (?,?,?,?,?,?)",
                         (client_id, doc_id, doc_type, year, payer, str(amount)))
    if doc_type == "Receipt":
        total = _pick_total(fields)
        if total:
            when = next((v for k, v in fields.items() if re.search(r"date", k, re.I)), None)
            link_receipt(conn, client_id, doc_id, total, _parse_date(when))


def _pick_total(fields: dict[str, str]) -> Decimal | None:
    """The amount actually paid: prefer 'total'/'amount due' over subtotals, tax or tip."""
    ranked = sorted(fields.items(), key=lambda kv: (
        0 if re.fullmatch(r"\s*(grand\s*)?total(\s*(paid|due|amount))?\s*", kv[0], re.I) else
        1 if re.search(r"amount\s*(due|paid)|total\s*(due|paid)", kv[0], re.I) else
        2 if re.search(r"total", kv[0], re.I) and not re.search(r"sub", kv[0], re.I) else 9))
    for k, v in ranked:
        if re.search(r"total|amount", k, re.I) and not re.search(r"sub|tax|tip", k, re.I) and _money(v):
            return _money(v)
    return None


def _parse_date(v: str | None) -> str | None:
    if not v:
        return None
    from datetime import datetime

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(v.strip()[:20], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def link_receipt(conn, client_id: str, doc_id: str, total: Decimal, when: str | None) -> int | None:
    """Attach a receipt to the matching unsupported expense entry (same amount, nearby date)."""
    try:
        center = date.fromisoformat(when) if when else None
    except ValueError:
        center = None
    candidates = rows(
        conn,
        "SELECT e.id, e.date FROM entries e JOIN postings p ON p.entry_id = e.id JOIN accounts a "
        "ON a.client_id = e.client_id AND a.code = p.account_code WHERE e.client_id = ? AND a.type = 'expense' "
        "AND CAST(p.amount AS REAL) = ? AND e.document_id IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM entry_documents ed WHERE ed.entry_id = e.id)",
        client_id, float(total),
    )
    if center:
        candidates = [c for c in candidates if abs(date.fromisoformat(c["date"]) - center) <= timedelta(days=7)]
    if len(candidates) != 1:
        return None
    conn.execute("INSERT INTO entry_documents (entry_id, document_id, linked_by, at) VALUES (?,?,?,?)",
                 (candidates[0]["id"], doc_id, "intake-agent", audit.now()))
    audit.record(conn, "intake-agent", "agent", "document.linked", {"entry_id": candidates[0]["id"], "document_id": doc_id},
                 client_id=client_id)
    return candidates[0]["id"]


def assign(conn, vault: "Vault | Path", doc_id: str, client_id: str, actor: str, role: str = "cpa") -> dict[str, Any]:
    """A human resolves a review-queue document to a client (moves it into that client's vault)."""
    d = one(conn, "SELECT * FROM documents WHERE id = ?", doc_id)
    if not d:
        raise KeyError(doc_id)
    store.get_client(conn, client_id)
    new_rel = _vault_path(client_id, d["tax_year"], d["doc_type"], d["original_name"], d["sha256"])
    as_vault(vault).move(d["vault_path"], new_rel)
    conn.execute("UPDATE documents SET client_id = ?, status = 'filed', vault_path = ? WHERE id = ?",
                 (client_id, str(new_rel).replace("\\", "/"), doc_id))
    audit.record(conn, actor, role, "document.assigned", {"document_id": doc_id, "name": d["original_name"]}, client_id=client_id)
    _effects(conn, client_id, doc_id, d["doc_type"], d["tax_year"], json.loads(d["fields"]), d["text_excerpt"] or "")
    return one(conn, "SELECT * FROM documents WHERE id = ?", doc_id)
