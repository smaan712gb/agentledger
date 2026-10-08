"""RegWatch agents: watch official sources, draft rule changes, verify, adopt or escalate."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from ...ai.router import Unavailable
from ...calc.engine import Ctx
from ...kb.store import new_value, snapshot, restore
from ...ledger import m1, store
from ...regwatch import documents as docs
from ...regwatch.draft import SYSTEM, Draft, build_prompt, parse_value, prefilter
from ..core import AgentResult, AgentSpec, Foundry, Proposal, agent_kind, applier
from ..verify import load_golden, place_value, verify_rule_change


# Final and proposed regulations dismissed by the local model get one budgeted second opinion:
# a brand-new area of law is exactly what a small model is most likely to miss.
SECOND_OPINION_TYPES = {"Rule", "Proposed Rule"}


# -- seen-document memory (git-friendly JSON) -------------------------------------------------

def _seen_path(f: Foundry) -> Path:
    return f.paths.state / "regwatch_seen.json"


def seen(f: Foundry) -> dict[str, Any]:
    p = _seen_path(f)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def mark(f: Foundry, doc: docs.Document, outcome: str, detail: str = "") -> None:
    data = seen(f)
    data[doc.key] = {"title": doc.title, "url": doc.url, "outcome": outcome, "detail": detail[:300],
                     "fingerprint": doc.fingerprint(), "published": doc.published}
    _seen_path(f).write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


# -- the pipeline shared by every regulatory source -------------------------------------------

def process(f: Foundry, agent_id: str, items: Iterable[docs.Document], res: AgentResult, *,
            force: bool = False, max_docs: int = 25, official_extra: list[str] | None = None) -> None:
    memory = seen(f)
    official = sorted(set(f.policy.get("official_domains", [])) | set(official_extra or []))
    golden = load_golden(f.paths.golden / "scenarios.yaml")
    stats = res.stats
    for doc in items:
        stats["seen"] = stats.get("seen", 0) + 1
        if not force and doc.key in memory:
            continue
        keep, why = prefilter(doc, f.kb)
        if not keep and not force:
            mark(f, doc, "filtered", why)
            stats["filtered"] = stats.get("filtered", 0) + 1
            continue
        if stats.get("drafted", 0) >= max_docs:
            res.log.append(f"draft limit {max_docs} reached; remaining documents wait for the next run")
            break
        try:
            text = doc.text or docs.fetch_text(doc.text_url or doc.url)
        except Exception as e:
            res.log.append(f"fetch failed {doc.url}: {e}")
            continue
        doc.text = text
        pid = draft_and_submit(f, agent_id, doc, official, golden, res)
        stats["drafted"] = stats.get("drafted", 0) + 1
        mark(f, doc, "proposal" if pid else "not_relevant", pid or "")


def draft_and_submit(f: Foundry, agent_id: str, doc: docs.Document, official: list[str], golden: list[dict[str, Any]],
                     res: AgentResult) -> str | None:
    prompt, truncated = build_prompt(doc, f.kb, doc.text or "")
    attempts = [False, True]  # local first; escalate to frontier only if the local draft fails verification
    last: Proposal | None = None
    for escalate in attempts:
        try:
            draft, by = f.router.structured("draft", system=SYSTEM, user=prompt, schema=Draft, escalate=escalate, effort="high",
                                            data_class="public")
        except Unavailable as e:
            res.log.append(f"draft unavailable for {doc.key}: {e}")
            break
        if not draft.relevant or not draft.changes:
            if (not escalate and not draft.relevant and doc.doc_type in SECOND_OPINION_TYPES
                    and f.router.frontier_allowed()):
                res.log.append(f"{doc.key}: local model dismissed a {doc.doc_type}; asking for a second opinion")
                continue
            if draft.code_change_needed and draft.relevant:
                res.alerts.append({"type": "code_change_needed", "document": doc.url, "items": draft.code_change_needed})
                queue_work(f, doc, draft.code_change_needed)
            return None
        payload = {"changes": [], "new_rules": [r.model_dump() for r in draft.new_rules],
                   "code_change_needed": draft.code_change_needed, "drafted_by": by, "confidence": draft.confidence}
        for c in draft.changes:
            try:
                value = parse_value(c.value_json)
            except (json.JSONDecodeError, TypeError):
                value = c.value_json
            payload["changes"].append({**c.model_dump(exclude={"value_json"}), "value": value})
        payload["fingerprint"] = json.dumps(sorted((c["rule_id"], c["effective_from"], json.dumps(c["value"], sort_keys=True))
                                                   for c in payload["changes"]))
        checks, risk, reasons, impact = verify_rule_change(f.kb, payload, doc.text or "", doc.url, official, golden,
                                                           truncated=truncated)
        p = Proposal(kind="rule_change", agent=agent_id, title=_title(payload), summary=draft.summary, risk=risk,
                     risk_reasons=reasons, source={"key": doc.key, "title": doc.title, "url": doc.url,
                                                   "published": doc.published, "type": doc.doc_type},
                     payload=payload, checks=checks, impact=impact)
        last = p
        if p.verified or by.startswith("frontier") or escalate:
            break
        res.log.append(f"{doc.key}: local draft failed verification; escalating")
    if last is None:
        return None
    dup = f.duplicate_of("rule_change", last.payload["fingerprint"])
    if dup:
        res.log.append(f"{doc.key}: duplicate of {dup.id}")
        return dup.id
    last = f.submit(last)
    res.proposals.append(last.id)
    return last.id


def _title(payload: dict[str, Any]) -> str:
    parts = [f"{c['rule_id'].split('.')[-1].replace('_', ' ')} → {json.dumps(c['value'])} from {c['effective_from']}"
             for c in payload["changes"][:3]]
    return "; ".join(parts) + (" …" if len(payload["changes"]) > 3 else "")


def queue_work(f: Foundry, doc: docs.Document, items: list[str]) -> None:
    work = f.paths.state / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / f"{doc.key.replace(':', '_')}.json").write_text(json.dumps(
        {"source": doc.url, "title": doc.title, "items": items, "status": "open"}, indent=2), encoding="utf-8")


# -- agent kinds ------------------------------------------------------------------------------

@agent_kind("federal_register")
def federal_register_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    p = spec.params
    process(f, spec.id, docs.federal_register(p.get("agencies", ["internal-revenue-service"]), int(p.get("lookback_days", 10))),
            res, max_docs=int(p.get("max_docs", 25)))


@agent_kind("irs_newsroom")
def irs_newsroom_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    process(f, spec.id, docs.irs_newsroom(spec.params.get("url", "https://www.irs.gov/newsroom/news-releases-for-current-month")),
            res, max_docs=int(spec.params.get("max_docs", 10)))


@agent_kind("web_watch")
def web_watch_agent(f: Foundry, spec: AgentSpec, res: AgentResult) -> None:
    """Generic, spec-defined watcher; the Agent Architect creates these without code."""
    p = spec.params
    process(f, spec.id, docs.web_page_links(p["url"], p.get("keywords", []), spec.id), res, max_docs=int(p.get("max_docs", 10)))


def ingest_document(f: Foundry, title: str, url: str, text: str, agent_id: str = "manual-ingest") -> AgentResult:
    """Push a specific document (e.g. a Rev. Proc. PDF someone forwarded) through the pipeline."""
    import hashlib

    res = AgentResult()
    doc = docs.Document(key=f"manual:{hashlib.sha1((url + text[:1000]).encode()).hexdigest()[:12]}", source="manual",
                        title=title, url=url, text=text)
    process(f, agent_id, [doc], res, force=True)
    return res


# -- applying an adopted rule change ----------------------------------------------------------

@applier("rule_change", undo=lambda f, p: _undo_rule_change(f, p))
def apply_rule_change(f: Foundry, p: Proposal) -> dict[str, Any]:
    from ...kb.model import Rule

    before_kb_clients = _m1_snapshot(f, p)
    undo: dict[str, Any] = {"rules": {}}
    new_rules = {r["id"]: r for r in p.payload.get("new_rules", [])}
    approver = (p.decision or {}).get("by") or "pending"
    for c in p.payload["changes"]:
        rid = c["rule_id"]
        current = f.kb.rules.get(rid)
        undo["rules"].setdefault(rid, snapshot(current))
        rule = current or Rule.model_validate({**new_rules[rid], "values": []})
        nv = new_value(c["value"], date.fromisoformat(c["effective_from"]),
                       date.fromisoformat(c["effective_to"]) if c.get("effective_to") else None,
                       c.get("citation") or p.source.get("title", ""), (p.source or {}).get("url"),
                       adopted_via=p.id, adopted_at=_now(), approved_by=approver)
        updated, _ = place_value(rule, nv)
        f.kb.save_rule(updated)
    f.kb.reload()
    p.impact["clients"] = _m1_delta(f, p, before_kb_clients)
    p.impact["kb_version"] = f.kb.version()
    _changelog(f, p)
    return undo


def _undo_rule_change(f: Foundry, p: Proposal) -> None:
    for rid, snap in (p.undo or {}).get("rules", {}).items():
        rule = restore(snap)
        if rule is None:
            path = f.kb.path_for(rid)
            if path.exists():
                path.unlink()
        else:
            f.kb.save_rule(rule)
    f.kb.reload()


def _affected_years(p: Proposal) -> set[int]:
    years = set()
    this = date.today().year
    for c in p.payload["changes"]:
        start = date.fromisoformat(c["effective_from"]).year
        end = date.fromisoformat(c["effective_to"]).year if c.get("effective_to") else this
        years |= {y for y in range(start, min(end, this) + 1) if y >= this - 3}
    return years


def _m1_snapshot(f: Foundry, p: Proposal) -> dict[str, str]:
    if not any(c["rule_id"] in m1.M1_RULES for c in p.payload["changes"]):
        return {}
    out = {}
    for c in store.list_clients(f.conn):
        if c["kind"] != "business":
            continue
        for y in _affected_years(p):
            try:
                out[f"{c['id']}|{y}"] = str(m1.compute(f.conn, Ctx(f.kb), c["id"], y).taxable_income)
            except Exception:
                continue
    return out


def _m1_delta(f: Foundry, p: Proposal, before: dict[str, str]) -> list[dict[str, Any]]:
    from decimal import Decimal

    out = []
    for key, prev in before.items():
        cid, y = key.split("|")
        try:
            after = m1.compute(f.conn, Ctx(f.kb), cid, int(y)).taxable_income
        except Exception as e:
            out.append({"client_id": cid, "tax_year": int(y), "error": str(e)})
            continue
        if after != Decimal(prev):
            out.append({"client_id": cid, "tax_year": int(y), "taxable_income_before": prev, "taxable_income_after": str(after),
                        "delta": str(after - Decimal(prev)), "action": "M-1 recomputed automatically; review before filing"})
    return out


def _changelog(f: Foundry, p: Proposal) -> None:
    path = f.paths.rules / "CHANGELOG.md"
    lines = [f"## {date.today().isoformat()} — {p.title}", "",
             f"- Proposal `{p.id}` by `{p.agent}`, risk **{p.risk}**, decided by {(p.decision or {}).get('by', 'auto-policy')}",
             f"- Source: [{(p.source or {}).get('title', '')}]({(p.source or {}).get('url', '')})"]
    for c in p.payload["changes"]:
        lines.append(f"- `{c['rule_id']}` = `{json.dumps(c['value'])}` effective {c['effective_from']}"
                     f"{' to ' + c['effective_to'] if c.get('effective_to') else ''} ({c.get('citation', '')})")
    prior = path.read_text(encoding="utf-8") if path.exists() else "# Regulation knowledge-base changelog\n\n"
    head, _, rest = prior.partition("\n\n")
    path.write_text(head + "\n\n" + "\n".join(lines) + "\n\n" + rest, encoding="utf-8")


def _now() -> str:
    from ... import audit

    return audit.now()
