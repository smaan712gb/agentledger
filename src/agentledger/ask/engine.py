"""Ask anything: evidence-pack answering with deterministic grounding and honest escalation."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterator

from .. import audit
from ..ai.grounding import check_answer
from ..ai.router import Router, Unavailable
from ..calc.engine import Ctx
from ..db import rows
from ..integrity.checks import list_findings
from ..kb.store import KnowledgeBase
from ..ledger import m1, store

SYSTEM = """You are AgentLedger, the AI shared by a CPA firm and its client. Both see the same facts and the same answers.

Answer ONLY from the EVIDENCE block. Tag every fact with its citation exactly as shown, e.g.
[R:us_fed.individual.standard_deduction] for a rule, [E:42] for a ledger entry, [M:2026:10] for an M-1 line,
[F:ab12cd] for an integrity finding, [D:doc_123] for a document, [C:client-id] for the client profile,
[P:playbook_id] for a CPA playbook or firm precedent.

- Do not state any number that is not in the evidence. Do not do arithmetic; the platform computes.
- Put the citation in the same sentence as the number it supports, and quote the number exactly as the evidence shows it.
- If the evidence is not enough, say exactly what is missing and who should provide it (client or CPA).
- Be candid with whoever is asking. If something looks wrong, unsupported or aggressive, say so plainly.
- Never help hide income, backdate records, invent support, or mislead the IRS, the CPA or the client.
- Plain language first, then the technical basis. Keep it short."""

DEEP_HINTS = re.compile(r"\b(should (i|we)|plan|planning|strategy|structure|elect(ion)?|what if|optimi[sz]e|compare|"
                        r"audit risk|best way|trade-?off|scenario|restructur|acquisition|exit|succession)\b", re.I)


@dataclass
class Pack:
    text: str
    ids: dict[str, set[str]] = field(default_factory=dict)
    lines: dict[str, str] = field(default_factory=dict)

    def add(self, kind: str, ident: str, line: str) -> None:
        self.ids.setdefault(kind, set()).add(ident)
        key = f"{kind}:{ident}"
        self.lines[key] = (self.lines.get(key, "") + " " + line).strip()
        self.text += f"[{key}] {line}\n"


def build_pack(conn: sqlite3.Connection, kb: KnowledgeBase, question: str, client_id: str | None, brain: Any = None) -> Pack:
    today = date.today()
    pack = Pack(text=f"Today is {today.isoformat()}.\n\n## Rules (regulation knowledge base)\n")
    rules = kb.search(question, limit=8) or []
    for r in rules:
        for y in (today.year - 1, today.year, today.year + 1):
            v = r.value_on(date(y, 12, 31))
            pack.add("R", r.id, f"{r.title} — tax year {y}: "
                     + (f"{json.dumps(v.value)} {r.unit or ''} (source: {v.provenance.source})" if v else "NOT YET PUBLISHED"))
    if brain is not None:
        from ..brain.playbooks import search_precedents

        pbs = brain.relevant(question)
        precs = search_precedents(conn, question)
        if pbs or precs:
            pack.text += "\n## CPA playbooks and firm precedents\n"
        for pb in pbs:
            fresh = brain.freshness(pb)
            pack.add("P", pb.id, f"{pb.title} ({pb.category}, {pb.aggressiveness}): {pb.summary} Substance required: "
                                 f"{pb.substance} Authority: {'; '.join(pb.citations)}."
                                 + ("" if fresh["fresh"] else f" CAUTION: {'; '.join(fresh['reasons'])}."))
        for pr in precs:
            pack.add("P", f"prec-{pr['id']}", f"Firm precedent on {pr['topic']} ({pr['author']}, {pr['at'][:10]}): "
                                              f"situation: {pr['situation']} judgment: {pr['judgment']}")
    if not client_id:
        return pack

    c = store.get_client(conn, client_id)
    pack.text += "\n## Client\n"
    pack.add("C", client_id, f"{c['name']} — {c['kind']}{', ' + c['entity_type'] if c['entity_type'] else ''}; "
                             f"books closed through {c['closed_through'] or 'not closed'}")
    for y in (today.year - 1, today.year):
        bal = store.balances(conn, client_id, date(y, 1, 1), date(y, 12, 31))
        if bal:
            pack.text += f"\n## Account balances {y} (debit +, credit -)\n"
            for b in bal:
                pack.add("E", f"bal:{y}:{b['code']}", f"{b['code']} {b['name']} ({b['type']}): {b['balance']}")
        if c["kind"] == "business" and bal:
            try:
                res = m1.compute(conn, Ctx(kb), client_id, y)
                pack.text += f"\n## Schedule M-1 {y}\n"
                for l in res.lines:
                    pack.add("M", f"{y}:{l.line}", f"Line {l.line} {l.label}: {l.amount}")
            except Exception as e:
                pack.text += f"(M-1 {y} unavailable: {e})\n"
    words = [w for w in re.findall(r"[a-z]{4,}", question.lower())]
    ents = store.entries(conn, client_id, date(today.year - 1, 1, 1), date(today.year, 12, 31))
    rel = [e for e in ents if any(w in (e["memo"] or "").lower() for w in words)] or ents[-15:]
    if rel:
        pack.text += "\n## Ledger entries\n"
        for e in rel[:40]:
            amt = sum(float(p["amount"]) for p in e["postings"] if float(p["amount"]) > 0)
            tags = ",".join(sorted({p["tax_treatment"] for p in e["postings"] if p["tax_treatment"]}))
            pack.add("E", str(e["id"]), f"{e['date']} “{e['memo']}” {amt:.2f}"
                                        f"{' [' + tags + ']' if tags else ''}{' receipt attached' if e['document_id'] else ''}")
    irs = rows(conn, "SELECT id, form, tax_year, payer, amount, document_id FROM info_returns WHERE client_id = ? ORDER BY tax_year DESC",
               client_id)
    if irs:
        pack.text += "\n## Information returns reported to the IRS about this client\n"
        for r in irs:
            pack.add("D", f"ir-{r['id']}", f"Form {r['form']} for {r['tax_year']} from payer {r['payer'] or 'unknown'}: "
                                           f"{r['amount']} reported to the IRS")
    fnd = list_findings(conn, client_id)
    if fnd:
        pack.text += "\n## Integrity findings\n"
        for f in fnd[:15]:
            pack.add("F", f["id"], f"{f['status'].upper()} {f['severity']}: {f['title']} — {f['detail']}")
    docs = rows(conn, "SELECT id, doc_type, tax_year, status, summary FROM documents WHERE client_id = ? ORDER BY received_at DESC LIMIT 20",
                client_id)
    if docs:
        pack.text += "\n## Documents on file\n"
        for d in docs:
            pack.add("D", d["id"], f"{d['doc_type']} {d['tax_year'] or ''}: {d['summary'] or ''}")
    return pack


def redact_for_frontier(text: str, client: dict[str, Any] | None) -> str:
    """Strip direct identifiers before anything leaves the firm (IRC §7216 posture)."""
    if client:
        for n in [client["name"], *client.get("aliases", [])]:
            if n:
                text = re.sub(re.escape(n), "[CLIENT]", text, flags=re.I)
        for e in client.get("emails", []):
            text = text.replace(e, "[EMAIL]")
    text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[SSN]", text)
    text = re.sub(r"\b\d{2}-\d{7}\b", "[EIN]", text)
    text = re.sub(r"\b\d{9,17}\b", "[ACCOUNT]", text)
    return text


def should_go_deep(question: str, deep: bool | None) -> bool:
    return bool(deep) if deep is not None else bool(DEEP_HINTS.search(question))


def ask_stream(conn: sqlite3.Connection, kb: KnowledgeBase, router: Router, question: str, *, client_id: str | None,
               actor: str, role: str, deep: bool | None = None, brain: Any = None) -> Iterator[dict[str, Any]]:
    """Yields events: {type: meta|token|verify|escalate|done|error}."""
    client = store.get_client(conn, client_id) if client_id else None
    pack = build_pack(conn, kb, question, client_id, brain)
    want_deep = should_go_deep(question, deep)
    frontier_ok = router.frontier_allowed() and (client is None or bool(client.get("consent_7216_at")))
    plan = ["answer"] + (["reason"] if frontier_ok else [])
    if want_deep and frontier_ok:
        plan = ["reason"]
    asked_as = "the CPA" if role == "cpa" else "the client"
    # Clients never see an unverified answer: their answer is held until it passes verification.
    # CPAs see the live stream with a verification badge.
    hold = role == "client"
    for i, model_role in enumerate(plan):
        evidence = pack.text if model_role != "reason" else redact_for_frontier(pack.text, client)
        messages = [{"role": "user", "content": f"<evidence>\n{evidence}\n</evidence>\n\nQuestion from {asked_as}: {question}"}]
        try:
            gen, model = router.stream(model_role, system=SYSTEM, messages=messages, client_id=client_id,
                                       data_class="taxpayer" if client_id else "public")
            yield {"type": "meta", "model": model, "tier": model.split(":")[0], "evidence_items": sum(len(v) for v in pack.ids.values())}
            parts = []
            for tok in gen:
                parts.append(tok)
                if not hold:
                    yield {"type": "token", "text": tok}
        except Unavailable as e:
            yield {"type": "error", "message": str(e)}
            continue
        except Exception as e:
            yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
            continue
        answer = "".join(parts)
        answer_check = answer.replace("[CLIENT]", client["name"]) if model_role == "reason" and client else answer
        v = check_answer(answer_check, pack.text, pack.ids, pack.lines)
        audit.record(conn, actor, role, "ai.answer", {"question": question, "model": model, "answer": answer,
                                                     "grounded": v["grounded"], "citations": v["citations"],
                                                     "unsupported_numbers": v["unsupported_numbers"] + v["uncited_numbers"]
                                                     + v["misattributed_numbers"]}, client_id=client_id)
        last = i == len(plan) - 1
        if v["grounded"]:
            if hold:
                yield {"type": "token", "text": answer_check}
            yield {"type": "verify", **v}
            yield {"type": "done", "grounded": True, "model": model}
            return
        if not last:
            yield {"type": "verify", **v}
            yield {"type": "escalate", "reason": "answer failed verification; escalating to deep reasoning"}
            continue
        if hold:
            yield {"type": "token", "text": "I couldn't verify an answer to that from your records, so I'm not going to guess. "
                                            "I've asked your CPA to look at it, and you'll see their reply here."}
        yield {"type": "verify", **v}
        yield {"type": "done", "grounded": False, "model": model}
        return
    yield {"type": "done", "grounded": False, "model": None}
