"""Operational agents: email/maildrop intake, the integrity sweeper and the audit chain anchor."""

from __future__ import annotations

import imaplib
import os
import shutil
from datetime import date

from ...integrity.checks import run_all
from ...intake.pipeline import ingest
from ...ledger import store
from ..core import AgentResult, AgentSpec, Foundry, agent_kind


@agent_kind("intake_maildrop")
def maildrop_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    """Any file dropped (or synced, or forwarded by a mail rule) into maildrop/ is ingested."""
    drop = f.paths.maildrop
    done = drop / "processed"
    drop.mkdir(parents=True, exist_ok=True)
    done.mkdir(exist_ok=True)
    for path in sorted(p for p in drop.iterdir() if p.is_file()):
        out = ingest(f.conn, f.router, f.vault, path.name, path.read_bytes(), channel="maildrop")
        for d in out:
            res.stats[d["status"]] = res.stats.get(d["status"], 0) + 1
            if d["status"] == "needs_review" and not d.get("duplicate"):
                res.alerts.append({"type": "document_review", "document_id": d["id"], "name": d["name"], "reason": d["match"]})
        shutil.move(str(path), done / f"{date.today().isoformat()}_{path.name}")


@agent_kind("intake_imap")
def imap_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    """Polls a mailbox (credentials from env, never from config files)."""
    p = spec.params
    host, user, pw = os.environ.get(p.get("host_env", "AGENTLEDGER_IMAP_HOST")), os.environ.get(
        p.get("user_env", "AGENTLEDGER_IMAP_USER")), os.environ.get(p.get("password_env", "AGENTLEDGER_IMAP_PASSWORD"))
    if not (host and user and pw):
        res.log.append("IMAP credentials not set; skipping")
        return
    with imaplib.IMAP4_SSL(host) as m:
        m.login(user, pw)
        m.select(p.get("folder", "INBOX"))
        _, ids = m.search(None, "UNSEEN")
        for num in ids[0].split()[: int(p.get("max_messages", 50))]:
            _, data = m.fetch(num, "(RFC822)")
            raw = data[0][1]
            out = ingest(f.conn, f.router, f.vault, f"mail-{num.decode()}.eml", raw, channel="email")
            for d in out:
                res.stats[d["status"]] = res.stats.get(d["status"], 0) + 1
            m.store(num, "+FLAGS", "\\Seen")


@agent_kind("integrity_sweeper")
def integrity_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    from ...domains.packs import Packs

    this = date.today().year
    packs = Packs(f.paths.domains)
    for c in store.list_clients(f.conn):
        findings: list = []
        for year in (this - 1, this):
            findings = run_all(f.conn, f.kb, c["id"], year, packs=packs)
        opened = [x for x in findings if x["status"] == "open"]
        res.stats[c["id"]] = len(opened)
        res.alerts += [{"type": "finding", "client_id": c["id"], "severity": x["severity"], "title": x["title"]}
                       for x in opened if x["severity"] in ("critical", "high")]


@agent_kind("evidence_anchor")
def evidence_anchor_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    """Fix the firm's audit chain head in its object store (backlog F-13; evidence/anchors.py): only when the chain
    moved since the last anchor, after probing that the anchors prefix is locked. The scheduler runs it per firm
    (TENANT_KINDS); where no scheduler thread runs (Cloudflare containers start with AGENTLEDGER_AGENTS=0) the same
    job is `agentledger evidence anchor --all`."""
    from ...evidence.anchors import LOCKED, MISSING, Anchors

    out = Anchors.for_foundry(f).anchor(actor=spec.id)
    res.stats.update({"anchored": out["anchored"], "reason": out["reason"], "lock": out["lock"]["status"],
                      "unanchored": out.get("unanchored", 0), "store": out["store"]})
    if out["anchor"]:
        res.stats["anchor"] = out["anchor"]["key"]
    if out["lock"]["status"] != LOCKED:
        res.alerts.append({"type": "anchor_lock_missing", "severity": "high" if out["lock"]["status"] == MISSING else "medium",
                           "store": out["store"], "detail": out["lock"]["detail"]})
    if out["mismatch"]:
        res.alerts.append({"type": "audit_chain_tampered", "severity": "critical", **out["mismatch"]})
