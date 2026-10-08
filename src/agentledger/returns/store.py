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
        self.wf = Engine(conn, {"return_1040": RETURN_1040})

    # ------------------------------------------------------------------ records
    def create(self, client_id: str, tax_year: int, actor: str, inputs: dict[str, Any] | None = None) -> str:
        row = self.conn.execute("SELECT id FROM tax_returns WHERE client_id = ? AND tax_year = ? AND form = '1040'",
                                (client_id, tax_year)).fetchone()
        if row:
            raise ValueError(f"a {tax_year} Form 1040 already exists for {client_id}")
        rid = "ret_" + secrets.token_hex(6)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute("INSERT INTO tax_returns (id, client_id, tax_year, form, created_at, created_by) VALUES (?, ?, ?, '1040', ?, ?)",
                          (rid, client_id, tax_year, now, actor))
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
        prov = cur["provenance"] if provenance is None else provenance
        st = self.wf.state(rid)
        if st.status in REVIEWABLE:
            self.wf.send(rid, "reopen", actor, note=f"inputs changed while {st.status}")
        elif st.status not in ("preparing",):
            raise TransitionError(f"a return that is {st.status} cannot be edited")
        self._save(rid, inputs, prov, actor, note)
        return self.compute(rid, actor)

    def populate_from_documents(self, rid: str, actor: str) -> dict[str, Any]:
        r = self.get(rid)
        cur = self.latest(rid)
        inputs = dict(cur["inputs"])
        pop = docs.populate(self.conn, r["client_id"], r["tax_year"], joint=inputs.get("filing_status") == "mfj")
        for key, value in pop.inputs.items():
            if isinstance(value, list):
                inputs[key] = value  # documents are the source of truth for these lists
            else:
                inputs[key] = {**inputs.get(key, {}), **value}
        prov = {k: {**v, "confirmed": False} for k, v in pop.provenance.items()}
        self.save_inputs(rid, inputs, actor, provenance=prov, note=f"populated from {len(pop.documents)} document(s)")
        return {"documents": pop.documents, "issues": pop.issues, "fields": len(prov)}

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

    def compute(self, rid: str, actor: str, *, oracle: bool = False) -> dict[str, Any]:
        cur = self.latest(rid)
        ret = IndividualReturn.model_validate(cur["inputs"])
        res = compute_individual(Ctx(self.kb), ret)
        result = res.to_dict()
        result["coverage"] = self._coverage(list(result["forms"]), ret.tax_year)
        result["pinned"] = {"kb_version": self.kb.version(), "engine": ENGINE_VERSION}
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

        statuses = {coverage.form_id(f): coverage.lookup(coverage.form_id(f), year)["status"] for f in forms if coverage.form_id(f)}
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
        reasons = _g_transmit(st, {"efile_ready": efile_ready})
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
