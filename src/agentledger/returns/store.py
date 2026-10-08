"""Returns as durable, versioned records moving through a preparation workflow.

Inputs and computed results are sealed with the firm's data key (they hold SSNs and income).
Every save is a new immutable version. The workflow (prepare -> review -> sign -> transmit ->
acknowledge) is event-sourced; changing a return after review sends it back to preparation
and invalidates any signature, because the taxpayer signed a specific return.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import audit
from . import facts
from ..db import is_pg
from ..calc.engine import Ctx
from ..kb.store import KnowledgeBase
from ..workflow.engine import Definition, Engine, State, Transition, TransitionError
from . import documents as docs
from .individual import compute_individual
from .model import IndividualReturn

SCHEMA = """
CREATE TABLE IF NOT EXISTS tax_returns (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(id),
    tax_year INTEGER NOT NULL,
    form TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    amends TEXT REFERENCES tax_returns(id),
    UNIQUE (client_id, tax_year, form)
);
CREATE TABLE IF NOT EXISTS tax_return_versions (
    return_id TEXT NOT NULL REFERENCES tax_returns(id),
    version INTEGER NOT NULL,
    inputs TEXT NOT NULL,
    provenance TEXT NOT NULL,
    result TEXT,
    summary TEXT NOT NULL DEFAULT '{}',
    diagnostics TEXT NOT NULL DEFAULT '[]',
    crosscheck TEXT,
    input_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (return_id, version)
);
CREATE TABLE IF NOT EXISTS fact_assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    return_id TEXT NOT NULL REFERENCES tax_returns(id),
    path TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT NOT NULL,
    source_ref TEXT,
    asserted_by TEXT NOT NULL,
    asserted_at TEXT NOT NULL,
    supersedes INTEGER REFERENCES fact_assertions(id)
);
CREATE INDEX IF NOT EXISTS fact_assertions_path ON fact_assertions (return_id, path, id);
CREATE TABLE IF NOT EXISTS fact_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    return_id TEXT NOT NULL REFERENCES tax_returns(id),
    path TEXT NOT NULL,
    anchor TEXT NOT NULL,
    document_id TEXT NOT NULL,
    box TEXT,
    current_value TEXT NOT NULL,
    proposed_value TEXT NOT NULL,
    current_source TEXT NOT NULL,
    raised_by TEXT NOT NULL,
    raised_at TEXT NOT NULL,
    resolution TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    note TEXT
);
CREATE TRIGGER IF NOT EXISTS fact_assertions_no_update BEFORE UPDATE ON fact_assertions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS fact_assertions_no_delete BEFORE DELETE ON fact_assertions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS fact_conflicts_no_delete BEFORE DELETE ON fact_conflicts
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS fact_conflicts_resolve_once BEFORE UPDATE ON fact_conflicts WHEN OLD.resolved_at IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'a fact conflict is resolved once (append-only)'); END;
CREATE TRIGGER IF NOT EXISTS tax_return_versions_no_update BEFORE UPDATE ON tax_return_versions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS tax_return_versions_no_delete BEFORE DELETE ON tax_return_versions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""

CPA = ("cpa",)
ENGINE_VERSION = "1040-2026.1"


def _g_review(st: State, c: dict[str, Any]) -> list[str]:
    out = []
    if not c.get("computed"):
        out.append("compute the return first")
    if c.get("blocking"):
        out.append(f"{c['blocking']} blocking diagnostic(s) must be resolved")
    if c.get("unconfirmed"):
        out.append(f"{c['unconfirmed']} document-sourced amount(s) are not confirmed by the preparer")
    if c.get("conflicts"):
        out.append(f"{c['conflicts']} fact conflict(s) between documents and entered values must be resolved")
    if c.get("crosscheck") == "differ" and not c.get("explained"):
        out.append("the independent cross-check disagrees; explain each difference before review")
    return out


def _g_approve(st: State, c: dict[str, Any]) -> list[str]:
    out = []
    if c.get("segregation") and c.get("actor") == st.facts.get("submitted_by"):
        out.append("the reviewer must be a different person from the preparer")
    if c.get("package_hash") != st.facts.get("review_hash"):
        out.append("the return changed after it was submitted for review")
    return out


def _g_signed(st: State, c: dict[str, Any]) -> list[str]:
    out = []
    if c.get("method") not in ("kba_esign", "wet_signature"):
        out.append("signature method must be kba_esign or wet_signature (IRS Pub. 1345)")
    if c.get("return_hash") != st.facts.get("approved_hash"):
        out.append("the signed Form 8879 does not match the approved return")
    if c.get("method") == "kba_esign" and not c.get("kba_transaction_id"):
        out.append("a KBA transaction id is required for electronic signatures")
    return out


def _g_transmit(st: State, c: dict[str, Any]) -> list[str]:
    return [] if c.get("efile_ready") else ["e-file is not enabled for this firm (EFIN, ETIN and ATS approval required)"]


REVIEWABLE = ("in_review", "approved", "awaiting_signature", "signed")
# A filed (or possibly filed) return is evidence of what was sent. It is never recomputed or edited in place:
# a what-if goes through recalculation_preview, and a change goes through an amendment case.
FROZEN = ("transmitted", "accepted", "paper_filed", "unknown", "rejected")
RETURN_1040 = Definition(
    kind="return_1040",
    initial="preparing",
    transitions=[
        Transition("submit_for_review", ("preparing",), "in_review", _g_review),
        Transition("request_changes", ("in_review",), "preparing", roles=CPA),
        Transition("approve", ("in_review",), "approved", _g_approve, roles=CPA),
        Transition("request_signature", ("approved",), "awaiting_signature", roles=CPA),
        Transition("signed", ("awaiting_signature",), "signed", _g_signed),
        Transition("transmit", ("signed",), "transmitted", _g_transmit, roles=CPA),
        Transition("ack_accepted", ("transmitted",), "accepted"),
        Transition("ack_rejected", ("transmitted",), "rejected"),
        Transition("correct", ("rejected",), "preparing", roles=CPA),
        Transition("mark_paper_filed", ("signed",), "paper_filed", roles=CPA),
        Transition("reopen", REVIEWABLE, "preparing"),
        # A transmission whose outcome is unknown (timeout, crash after sending) is reconciled, never blindly resent.
        Transition("outcome_unknown", ("signed",), "unknown"),
        Transition("reconciled_submitted", ("unknown",), "transmitted"),
        Transition("reconciled_not_submitted", ("unknown",), "signed"),
    ],
    waiting={"awaiting_signature": "taxpayer signature on Form 8879", "transmitted": "IRS acknowledgement",
             "unknown": "reconciliation with the transmitter",
             "in_review": "reviewer", "approved": "signature request"},
)


@dataclass
class Sealer:
    """Encrypts JSON with the firm's data key; in single-firm dev mode it stores plain JSON."""

    keyring: Any = None
    firm_id: str | None = None

    def seal(self, obj: Any, purpose: str) -> str:
        text = json.dumps(obj, default=str, sort_keys=True)
        return "enc:" + self.keyring.seal_text(self.firm_id, text, purpose) if self.keyring else text

    def open(self, blob: str | None, purpose: str) -> Any:
        if blob is None:
            return None
        if blob.startswith("enc:"):
            if not self.keyring:
                raise PermissionError("this return is encrypted and no firm key is available")
            return json.loads(self.keyring.open_text(self.firm_id, blob[4:], purpose))
        return json.loads(blob)


def input_hash(inputs: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()


def package_of(inputs: dict[str, Any], result: dict[str, Any] | None, provenance: dict[str, Any]) -> dict[str, Any]:
    """The immutable package a reviewer approves and a taxpayer signs: inputs, every computed form line, the
    summary, the pinned rule and engine versions, and the source documents relied on."""
    r = result or {}
    return {"inputs": inputs, "forms": r.get("forms"), "summary": r.get("summary"), "pinned": r.get("pinned"),
            "documents": sorted({v.get("document_id") for v in (provenance or {}).values() if v.get("document_id")})}


def package_hash(inputs: dict[str, Any], result: dict[str, Any] | None, provenance: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(package_of(inputs, result, provenance), sort_keys=True, default=str).encode()).hexdigest()


class Returns:
    def __init__(self, conn: sqlite3.Connection, kb: KnowledgeBase, sealer: Sealer | None = None, *, segregation: bool = True):
        self.conn = conn
        self.kb = kb
        self.sealer = sealer or Sealer()
        self.segregation = segregation
        conn.executescript(SCHEMA)
        cols = set() if is_pg(conn) else {r[1] for r in conn.execute("PRAGMA table_info(tax_returns)")}
        if not is_pg(conn) and "amends" not in cols:  # databases created before amendments existed
            conn.execute("ALTER TABLE tax_returns ADD COLUMN amends TEXT REFERENCES tax_returns(id)")
        self.wf = Engine(conn, {"return_1040": RETURN_1040})

    # ------------------------------------------------------------------ records
    def create(self, client_id: str, tax_year: int, actor: str, inputs: dict[str, Any] | None = None, *,
               form: str = "1040", amends: str | None = None) -> str:
        row = self.conn.execute("SELECT id FROM tax_returns WHERE client_id = ? AND tax_year = ? AND form = ?",
                                (client_id, tax_year, form)).fetchone()
        if row:
            raise ValueError(f"a {tax_year} Form {form} already exists for {client_id}")
        rid = "ret_" + secrets.token_hex(6)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute("INSERT INTO tax_returns (id, client_id, tax_year, form, created_at, created_by, amends) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (rid, client_id, tax_year, form, now, actor, amends))
        self.wf.start(rid, "return_1040", actor, {"client_id": client_id, "tax_year": tax_year})
        base = {"tax_year": tax_year, "filing_status": "single", "taxpayer": {}}
        self._save(rid, {**base, **(inputs or {})}, {}, actor, "created")
        audit.record(self.conn, actor, "cpa", "return.created", {"return_id": rid, "tax_year": tax_year}, client_id=client_id)
        return rid

    def get(self, rid: str) -> dict[str, Any]:
        r = self.conn.execute("SELECT * FROM tax_returns WHERE id = ?", (rid,)).fetchone()
        if not r:
            raise KeyError(rid)
        return dict(r)

    def for_client(self, client_id: str) -> list[dict[str, Any]]:
        out = []
        for r in self.conn.execute("SELECT * FROM tax_returns WHERE client_id = ? ORDER BY tax_year DESC", (client_id,)):
            v = self.latest(r["id"], decrypt=False)
            out.append({**dict(r), "status": self.wf.state(r["id"]).status, "version": v["version"],
                        "summary": json.loads(v["summary"])})
        return out

    def latest(self, rid: str, *, decrypt: bool = True) -> dict[str, Any]:
        v = self.conn.execute("SELECT * FROM tax_return_versions WHERE return_id = ? ORDER BY version DESC LIMIT 1", (rid,)).fetchone()
        if not v:
            raise KeyError(rid)
        v = dict(v)
        if decrypt:
            v["inputs"] = self.sealer.open(v["inputs"], f"return-inputs:{rid}")
            v["provenance"] = self.sealer.open(v["provenance"], f"return-provenance:{rid}")
            v["result"] = self.sealer.open(v["result"], f"return-result:{rid}")
        return v

    def _save(self, rid: str, inputs: dict[str, Any], provenance: dict[str, Any], actor: str, note: str,
              result: dict[str, Any] | None = None, crosscheck: dict[str, Any] | None = None) -> int:
        last = self.conn.execute("SELECT MAX(version) FROM tax_return_versions WHERE return_id = ?", (rid,)).fetchone()[0] or 0
        summary = result["summary"] if result else {}
        diagnostics = [{"severity": d["severity"], "code": d["code"], "form": d["form"], "line": d["line"]}
                       for d in (result or {}).get("diagnostics", [])]
        self.conn.execute(
            "INSERT INTO tax_return_versions (return_id, version, inputs, provenance, result, summary, diagnostics, crosscheck, "
            "input_hash, created_at, created_by, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, last + 1, self.sealer.seal(inputs, f"return-inputs:{rid}"), self.sealer.seal(provenance, f"return-provenance:{rid}"),
             self.sealer.seal(result, f"return-result:{rid}") if result is not None else None, json.dumps(summary),
             json.dumps(diagnostics), json.dumps(crosscheck) if crosscheck else None, input_hash(inputs),
             datetime.now(timezone.utc).isoformat(timespec="seconds"), actor, note))
        return last + 1

    # ------------------------------------------------------------------ preparation
    def save_inputs(self, rid: str, inputs: dict[str, Any], actor: str, *, provenance: dict[str, Any] | None = None,
                    note: str = "edited") -> dict[str, Any]:
        IndividualReturn.model_validate(inputs)  # reject malformed input before it is stored
        cur = self.latest(rid)
        st = self.wf.state(rid)
        if st.status in REVIEWABLE:
            self.wf.send(rid, "reopen", actor, note=f"inputs changed while {st.status}")
        elif st.status not in ("preparing",):
            raise TransitionError(f"a return that is {st.status} cannot be edited")
        if provenance is None:      # a person's edit: changed document values become the preparer's, on record
            prov, changed = facts.mark_edits(cur["inputs"], inputs, cur["provenance"], actor)
        else:
            prov, changed = provenance, facts.changed_paths(cur["inputs"], inputs)
        self._save(rid, inputs, prov, actor, note)
        facts.record(self.conn, self.sealer, rid, inputs, prov, changed, actor=actor)
        return self.compute(rid, actor)

    def populate_from_documents(self, rid: str, actor: str) -> dict[str, Any]:
        """Fill the return from its filed documents without overwriting anything (backlog F-07): disagreements with
        what is already there become fact conflicts for a person to resolve."""
        r = self.get(rid)
        cur = self.latest(rid)
        pop = docs.populate(self.conn, r["client_id"], r["tax_year"], joint=cur["inputs"].get("filing_status") == "mfj")
        inputs, prov, conflicts, issues, changed = facts.merge_population(
            cur["inputs"], cur["provenance"], pop.inputs, pop.provenance, facts.resolved_keeps(self.conn, self.sealer, rid))
        for i in issues:   # an amount a document should carry but does not: a person decides, it is never zero
            if i["code"] == "missing_value":
                conflicts.append({"path": i["path"], "anchor": i["anchor"], "current": None, "proposed": None,
                                  "document_id": i["document_id"] or "", "box": "required amount", "current_source": "missing"})
        if changed or inputs != cur["inputs"]:
            self.save_inputs(rid, inputs, actor, provenance=prov, note=f"populated from {len(pop.documents)} document(s)")
        else:
            self.compute(rid, actor)
        raised = facts.raise_conflicts(self.conn, self.sealer, rid, conflicts, actor)
        if raised:
            audit.record(self.conn, actor, "agent", "return.fact_conflicts", {"return_id": rid, "conflicts": len(raised)},
                         client_id=r["client_id"])
        return {"documents": pop.documents, "issues": pop.issues + issues, "fields": len(changed),
                "conflicts": len(facts.open_conflicts(self.conn, self.sealer, rid))}

    def conflicts(self, rid: str) -> list[dict[str, Any]]:
        return facts.open_conflicts(self.conn, self.sealer, rid)

    def resolve_conflict(self, rid: str, conflict_id: int, choice: str, actor: str, note: str = "") -> dict[str, Any]:
        """keep: the current value stands (and this document value is not raised again); document: take the
        document's value, which supersedes the current assertion."""
        c = next((x for x in self.conflicts(rid) if x["id"] == conflict_id), None)
        if c is None:
            raise KeyError(f"no open conflict {conflict_id}")
        if choice not in ("keep", "document"):
            raise ValueError("choose 'keep' or 'document'")
        if choice == "document" and c["anchor"].startswith("missing:"):
            raise ValueError("the document has no value here; enter the amount, then keep it")
        if choice == "document":
            cur = self.latest(rid)
            path = facts.locate(c["anchor"], cur["provenance"], cur["inputs"]) or c["path"]
            inputs = facts.set_path(cur["inputs"], path, c["proposed_value"])
            prov = {**cur["provenance"], path: {"source": "resolution", "document_id": c["document_id"], "box": c["box"],
                                                "value": str(c["proposed_value"]), "resolved_by": actor, "confirmed": True}}
            self.save_inputs(rid, inputs, actor, provenance=prov, note=f"fact conflict {conflict_id}: document value taken")
        facts.close_conflict(self.conn, conflict_id, resolution="replaced" if choice == "document" else "kept", actor=actor,
                             note=note)
        audit.record(self.conn, actor, "cpa", "return.fact_conflict_resolved",
                     {"return_id": rid, "conflict_id": conflict_id, "choice": choice}, client_id=self.get(rid)["client_id"])
        return {"open": len(self.conflicts(rid))}

    def confirm(self, rid: str, paths: list[str] | None, actor: str) -> int:
        """The preparer confirms document-sourced amounts (all of them when paths is None)."""
        cur = self.latest(rid)
        prov = cur["provenance"]
        n = 0
        for k, v in prov.items():
            if (paths is None or k in paths) and not v.get("confirmed"):
                v["confirmed"], v["confirmed_by"] = True, actor
                n += 1
        if n:
            self._save(rid, cur["inputs"], prov, actor, f"confirmed {n} amount(s)", result=cur["result"],
                       crosscheck=json.loads(cur["crosscheck"]) if cur["crosscheck"] else None)
        return n

    def _calculate(self, rid: str, cur: dict[str, Any]) -> tuple[IndividualReturn, Any, dict[str, Any]]:
        ret = IndividualReturn.model_validate(cur["inputs"])
        res = compute_individual(Ctx(self.kb), ret)
        result = res.to_dict()
        forms = list(result["forms"]) + (["f1040x"] if self.get(rid)["form"] == "1040-X" else [])
        result["coverage"] = self._coverage(forms, ret.tax_year)
        result["pinned"] = {"kb_version": self.kb.version(), "engine": ENGINE_VERSION}
        return ret, res, result

    def recalculation_preview(self, rid: str) -> dict[str, Any]:
        """What the return would be under today's rules and engine. Nothing is stored; a filed return stays as filed."""
        cur = self.latest(rid)
        _, _, result = self._calculate(rid, cur)
        filed = (cur["result"] or {}).get("summary", {})
        result["changes_vs_latest"] = {k: {"latest": filed.get(k), "now": v} for k, v in result["summary"].items() if filed.get(k) != v}
        return result

    def start_amendment(self, rid: str, actor: str) -> str:
        """A change to a filed return starts a linked amendment case (Form 1040-X), copying the filed facts."""
        st = self.wf.state(rid)
        if st.status not in ("accepted", "paper_filed", "transmitted"):
            raise TransitionError(f"only a filed return can be amended (this one is {st.status})")
        r = self.get(rid)
        cur = self.latest(rid)
        new = self.create(r["client_id"], r["tax_year"], actor, dict(cur["inputs"]), form="1040-X", amends=rid)
        audit.record(self.conn, actor, "cpa", "return.amendment_started", {"return_id": new, "amends": rid}, client_id=r["client_id"])
        return new

    def compute(self, rid: str, actor: str, *, oracle: bool = False) -> dict[str, Any]:
        st0 = self.wf.state(rid)
        if st0.status in FROZEN:
            raise TransitionError(f"this return is {st0.status}: its package is frozen. Use recalculation_preview for a what-if, "
                                  "or start_amendment to change it")
        cur = self.latest(rid)
        ret, res, result = self._calculate(rid, cur)
        cc = None
        if oracle:
            try:
                from .oracle import crosscheck

                x = crosscheck(ret, res)
                cc = {"status": "agree" if x.agrees else "differ", "compared": len(x.compared), "unmodelled": x.unmodelled,
                      "discrepancies": [{"item": d.item, "agentledger": str(d.agentledger), "policyengine": str(d.policyengine)}
                                        for d in x.discrepancies]}
            except ImportError:
                cc = {"status": "unavailable"}
        self._save(rid, cur["inputs"], cur["provenance"], actor, "computed", result=result, crosscheck=cc)
        st = self.wf.state(rid)
        bound = st.facts.get("approved_hash") if st.status in ("approved", "awaiting_signature", "signed") else st.facts.get("review_hash")
        if st.status in REVIEWABLE and bound and package_hash(cur["inputs"], result, cur["provenance"]) != bound:
            self.wf.send(rid, "reopen", actor, note="recomputed return differs from the reviewed/approved package "
                                                    "(rules, engine or results changed); review and signature are void")
        return result

    def _coverage(self, forms: list[str], year: int) -> dict[str, Any]:
        root = Path(self.kb.root).parent if getattr(self.kb, "root", None) else None
        from .. import coverage

        ids = [i for i in (coverage.form_id(f) for f in forms) if i]
        statuses = {i: coverage.lookup(i, year)["status"] for i in ids}
        lowest = min(statuses.values(), key=lambda x: coverage.RANK[x]) if statuses else "unsupported"
        blockers = coverage.check_forms(forms + ["mef_1040"], year, need="filing-approved")
        flags = coverage.active_flags(root, jurisdiction="US-FED", year=year) if root else []
        relevant = [x for x in flags if not x.get("form") or x["form"] in statuses]
        blockers += [{"form": x.get("form") or "US-FED", "status": "review-required", "need": "flag cleared",
                      "limits": [f"{x['reason']} ({x['source']})"], "flag": x["id"]} for x in relevant]
        return {"forms": statuses, "lowest": lowest, "flags": relevant,
                "below_preparation": coverage.check_forms(forms, year, need="manual-assisted"),
                "filing_blockers": blockers}

    # ------------------------------------------------------------------ workflow
    def status(self, rid: str) -> State:
        return self.wf.state(rid)

    def _review_context(self, rid: str, explained: bool) -> dict[str, Any]:
        v = self.latest(rid)
        result = v["result"] or {}
        cc = json.loads(v["crosscheck"]) if v["crosscheck"] else {"status": "unavailable"}
        below = len((result.get("coverage") or {}).get("below_preparation", []))
        return {"computed": bool(result), "blocking": below + sum(1 for d in result.get("diagnostics", []) if d["severity"] == "error"),
                "unconfirmed": sum(1 for p in v["provenance"].values() if not p.get("confirmed")),
                "conflicts": len(facts.open_conflicts(self.conn, self.sealer, rid)),
                "crosscheck": cc.get("status"), "explained": explained,
                "package_hash": package_hash(v["inputs"], v["result"], v["provenance"])}

    def submit_for_review(self, rid: str, actor: str, *, explanation: str = "") -> State:
        ctx = self._review_context(rid, bool(explanation.strip()))
        return self.wf.send(rid, "submit_for_review", actor, role="cpa", context=ctx, note=explanation,
                            facts={"submitted_by": actor, "review_hash": ctx["package_hash"]})

    def current_package_hash(self, rid: str) -> str:
        v = self.latest(rid)
        return package_hash(v["inputs"], v["result"], v["provenance"])

    def approve(self, rid: str, actor: str, role: str) -> State:
        h = self.current_package_hash(rid)
        v = self.latest(rid, decrypt=False)["version"]
        return self.wf.send(rid, "approve", actor, role=role, context={"segregation": self.segregation, "package_hash": h},
                            facts={"approved_by": actor, "approved_hash": h, "approved_version": v})

    def request_changes(self, rid: str, actor: str, role: str, note: str) -> State:
        return self.wf.send(rid, "request_changes", actor, role=role, note=note)

    def request_signature(self, rid: str, actor: str, role: str) -> State:
        return self.wf.send(rid, "request_signature", actor, role=role,
                            facts={"signature_requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    def record_signature(self, rid: str, actor: str, *, method: str, return_hash: str, kba_transaction_id: str = "",
                         signers: list[str] | None = None) -> State:
        return self.wf.send(rid, "signed", actor, context={"method": method, "return_hash": return_hash,
                                                            "kba_transaction_id": kba_transaction_id},
                            facts={"signature_method": method, "kba_transaction_id": kba_transaction_id,
                                   "signers": signers or [], "signed_hash": return_hash})

    def transmit(self, rid: str, actor: str, role: str, *, efile_ready: bool, submit, lookup=None) -> State:
        """Transmit the approved package at most once.

        `submit(idempotency_key)` sends it; `lookup(idempotency_key)` asks the transmitter whether that key was
        already received (returns the result dict, or None if not). If a previous attempt started but its outcome
        was never recorded, we reconcile through `lookup` instead of sending again; without `lookup` the return
        moves to `unknown` for a person to reconcile."""
        from ..workflow.engine import UncertainOutcome

        st = self.wf.state(rid)
        # Every check runs before anything leaves the system.
        reasons = []
        if st.status != "signed":
            reasons.append(f"only a signed return can be transmitted (this one is {st.status})")
        if role != "cpa":
            reasons.append("transmission needs a credentialed reviewer (CPA)")
        if not st.facts.get("signed_hash") or st.facts.get("signed_hash") != st.facts.get("approved_hash"):
            reasons.append("no valid signature is bound to the approved package")
        reasons += _g_transmit(st, {"efile_ready": efile_ready})
        if self.current_package_hash(rid) != st.facts.get("approved_hash"):
            reasons.append("the current return is not the approved and signed package")
        result = self.latest(rid)["result"] or {}
        blockers = (result.get("coverage") or {}).get("filing_blockers")
        if blockers is None:
            blockers = self._coverage(list(result.get("forms", {})), self.get(rid)["tax_year"])["filing_blockers"]
        if blockers:
            reasons.append("coverage does not allow filing: " + ", ".join(f"{b['form']} is {b['status']}" for b in blockers))
        if reasons:
            raise TransitionError("; ".join(reasons))
        key = f"{rid}:{st.facts['approved_hash'][:16]}"
        try:
            result = self.wf.activity(rid, "transmit", st.facts["approved_hash"], lambda: submit(key), actor=actor,
                                      reconcile=(lambda: lookup(key)) if lookup else None)
        except UncertainOutcome as e:
            self.wf.send(rid, "outcome_unknown", actor, note=str(e))
            raise
        return self.wf.send(rid, "transmit", actor, role=role, context={"efile_ready": efile_ready},
                            facts={"submission_id": result.get("submission_id"), "idempotency_key": key})

    def reconcile_transmission(self, rid: str, actor: str, role: str, *, submitted: bool, submission_id: str = "",
                               evidence: str = "") -> State:
        """A person confirms with the transmitter what happened to an attempt whose outcome was unknown."""
        if role != "cpa":
            raise TransitionError("reconciling a transmission needs a CPA")
        if submitted:
            return self.wf.send(rid, "reconciled_submitted", actor, note=evidence, facts={"submission_id": submission_id})
        return self.wf.send(rid, "reconciled_not_submitted", actor, note=evidence)
