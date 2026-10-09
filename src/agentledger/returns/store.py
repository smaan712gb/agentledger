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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .. import audit
from . import facts
from ..db import is_pg, lock, unit_of_work
from ..calc.engine import Ctx
from ..kb.store import KnowledgeBase
from ..workflow.engine import Definition, Engine, State, Transition, TransitionError
from . import documents as docs
from .individual import PER_OWNER_CARRYFORWARDS, PER_YEAR_CARRYFORWARDS, compute_individual
from .model import IndividualReturn, ParentFacts

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
-- One open conflict per field, decided by the database when two populations overlap (created in Returns.__init__
-- on SQLite, after older duplicates are retired; migration 0005 on PostgreSQL).
-- A filed document a return does not use is accounted for by a person (entered by hand, or not applicable), with
-- a reason; until then it blocks review (re-audit of 952ee96, finding 1 path f).
CREATE TABLE IF NOT EXISTS return_document_dispositions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    return_id TEXT NOT NULL REFERENCES tax_returns(id),
    document_id TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('entered_by_hand', 'not_applicable')),
    note TEXT NOT NULL,
    actor TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS return_document_dispositions_no_update BEFORE UPDATE ON return_document_dispositions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS return_document_dispositions_no_delete BEFORE DELETE ON return_document_dispositions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
-- Every document any version of a return relied on, in clear (versions are sealed): evidence retention counts the
-- return's year for each of them (evidence/records.py: supported_years).
CREATE TABLE IF NOT EXISTS return_document_uses (
    return_id TEXT NOT NULL REFERENCES tax_returns(id),
    document_id TEXT NOT NULL,
    first_version INTEGER NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (return_id, document_id)
);
CREATE INDEX IF NOT EXISTS return_document_uses_document ON return_document_uses (document_id);
-- What retention needs to know about a return version, in clear (versions are sealed; evidence/records.py): it uses
-- a carryover (the years it comes from stay open until this year is closed), or it shows foreign tax (a credit can be
-- claimed for 10 years).
CREATE TABLE IF NOT EXISTS return_retention_facts (
    return_id TEXT NOT NULL REFERENCES tax_returns(id),
    version INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('carryover', 'foreign_tax')),
    detail TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (return_id, version, kind)
);
CREATE TRIGGER IF NOT EXISTS return_retention_facts_no_update BEFORE UPDATE ON return_retention_facts
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS return_retention_facts_no_delete BEFORE DELETE ON return_retention_facts
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
-- What a return version carries to the next tax year (Result.carryforwards: capital loss carryovers, IRA basis, the HSA
-- last-month-rule amount), per kind and detail (the owner of a per-person kind, else empty). The amount is sealed with
-- the firm key like the version; kind and detail are in clear so the roll-forward can find them (migration 0007 on
-- PostgreSQL).
CREATE TABLE IF NOT EXISTS return_carryforwards (
    return_id TEXT NOT NULL REFERENCES tax_returns(id),
    version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    amount TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (return_id, version, kind, detail)
);
CREATE TRIGGER IF NOT EXISTS return_carryforwards_no_update BEFORE UPDATE ON return_carryforwards
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS return_carryforwards_no_delete BEFORE DELETE ON return_carryforwards
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS return_document_uses_no_update BEFORE UPDATE ON return_document_uses
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS return_document_uses_no_delete BEFORE DELETE ON return_document_uses
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS tax_return_versions_no_update BEFORE UPDATE ON tax_return_versions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS tax_return_versions_no_delete BEFORE DELETE ON tax_return_versions
BEGIN SELECT RAISE(ABORT, 'append-only'); END;
-- One row per electronic submission of a return to one jurisdiction (backlog F-08, returns/filing.py). The row
-- mirrors the submission's own event stream (workflow_events keyed by the submission id), which is the record.
CREATE TABLE IF NOT EXISTS filing_submissions (
    id TEXT PRIMARY KEY,
    return_id TEXT NOT NULL REFERENCES tax_returns(id),
    jurisdiction TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('original', 'retransmission')),
    supersedes TEXT REFERENCES filing_submissions(id),
    linked_to TEXT REFERENCES filing_submissions(id),
    package_hash TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    planned_submission_id TEXT,
    provider_submission_id TEXT,
    status TEXT NOT NULL,
    ack_payload TEXT,
    rejection_codes TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS filing_submissions_return ON filing_submissions (return_id, jurisdiction);
CREATE UNIQUE INDEX IF NOT EXISTS filing_submissions_planned ON filing_submissions (planned_submission_id)
    WHERE planned_submission_id IS NOT NULL;
"""

CPA = ("cpa",)
ENGINE_VERSION = "1040-2026.4"
USES_INDEXED = "return_document_uses_indexed"
CARRYFORWARDS_INDEXED = "return_carryforwards_indexed"
# A return whose figures are final for the next year's roll-forward: filed and accepted, or filed on paper.
FILED = ("accepted", "paper_filed")


# The filed documents that feed an individual return: each one must be on the return or accounted for by a person.
TAX_FORMS = ("W-2", "1099-NEC", "1099-MISC", "1099-K", "1099-INT", "1099-DIV", "1099-B", "1099-R", "1098", "1095", "K-1",
             "SSA-1099", "1099-G", "1098-E", "1098-T", "1099-SA", "5498", "5498-SA", "1095-A", "Prior-year return")


def _blockers(c: dict[str, Any]) -> list[str]:
    """What stops a return from moving on: checked at review, approval, signature request and transmission alike,
    so nothing that appeared after one step slips through the next."""
    out = []
    if not c.get("computed"):
        out.append("compute the return first")
    if c.get("blocking"):
        out.append(f"{c['blocking']} blocking diagnostic(s) must be resolved")
    if c.get("missing"):
        out.append(f"{c['missing']} item(s) lack a required amount (for example a W-2 without wages): enter it from the "
                   "document; a missing amount is never taken as zero")
    if c.get("unconfirmed"):
        out.append(f"{c['unconfirmed']} document-sourced amount(s) are not confirmed by the preparer")
    if c.get("orphaned"):
        out.append(f"{c['orphaned']} item(s) whose document left the return (moved, re-dated or deleted): remove each one "
                   "or keep it with a reason")
    if c.get("conflicts"):
        out.append(f"{c['conflicts']} fact conflict(s) between documents and the return must be resolved")
    if c.get("unaccounted"):
        names = ", ".join(c["unaccounted"][:5])
        out.append(f"{len(c['unaccounted'])} filed document(s) for the year are not on the return ({names}): populate, or "
                   "account for each one (entered by hand, or not applicable) with a reason")
    d = c.get("drift") or {}
    if d.get("left"):
        out.append(f"{len(d['left'])} item(s) whose document left the return (moved, re-dated or deleted): re-populate, then "
                   "remove each one or keep it with a reason")
    if d.get("disagreements"):
        out.append(f"{len(d['disagreements'])} value(s) disagree with the documents and were never decided: re-populate and "
                   "resolve each conflict")
    if d.get("not_applied"):
        out.append(f"the documents now provide {len(d['not_applied'])} value(s) not on the return: re-populate")
    if d.get("duplicates"):
        out.append("more than one item claims the same document: remove the copies")
    return out


def _g_review(st: State, c: dict[str, Any]) -> list[str]:
    out = _blockers(c)
    if c.get("crosscheck") == "differ" and not c.get("explained"):
        out.append("the independent cross-check disagrees; explain each difference before review")
    return out


def _g_approve(st: State, c: dict[str, Any]) -> list[str]:
    out = []
    if c.get("segregation") and c.get("actor") == st.facts.get("submitted_by"):
        out.append("the reviewer must be a different person from the preparer")
    if c.get("package_hash") != st.facts.get("review_hash"):
        out.append("the return changed after it was submitted for review")
    return out + _blockers(c)


def _g_request_signature(st: State, c: dict[str, Any]) -> list[str]:
    return _blockers(c)


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
    out = [] if c.get("efile_ready") else ["e-file is not enabled for this firm (EFIN, ETIN and ATS approval required)"]
    if c.get("role") == "system" and st.status != "release_approved":
        out.append("the workflow transmits only a return whose release a CPA approved")
    return out


def _g_release(st: State, c: dict[str, Any]) -> list[str]:
    """Approving the release re-runs every filing check: the signature is bound to the approved package, the current
    return is that package, e-file is enabled, nothing blocks, and every jurisdiction named is cleared for filing."""
    out = []
    if not st.facts.get("signed_hash") or st.facts.get("signed_hash") != st.facts.get("approved_hash"):
        out.append("no valid signature is bound to the approved package")
    if c.get("package_hash") != st.facts.get("approved_hash"):
        out.append("the current return is not the approved and signed package")
    if not c.get("jurisdictions"):
        out.append("name at least one jurisdiction to file in (US-FED, US-XX)")
    out += _g_transmit(st, {"efile_ready": c.get("efile_ready")})
    for b in c.get("filing_blockers") or []:
        out.append(f"coverage does not allow filing: {b['form']} is {b['status']}" + (f" ({b['jurisdiction']})" if b.get("jurisdiction") else ""))
    return out + _blockers(c)


def _g_void(st: State, c: dict[str, Any]) -> list[str]:
    if len(str(c.get("note") or "").strip()) < 10:
        return ["say why the return is void (for example 'filed with other software on 2027-04-10, transcript on file')"]
    if c.get("transmission_started"):
        return ["an electronic transmission of this return was started: reconcile it with the transmitter first"]
    return []


def _g_paper_filed(st: State, c: dict[str, Any]) -> list[str]:
    """Marking a return filed on paper is a filing: the same checks as transmission, and how it was filed on record
    (evidence retention counts from it)."""
    out = []
    if not st.facts.get("signed_hash") or st.facts.get("signed_hash") != st.facts.get("approved_hash"):
        out.append("no valid signature is bound to the approved package")
    if c.get("package_hash") != st.facts.get("approved_hash"):
        out.append("the current return is not the approved and signed package")
    if len(str(c.get("note") or "").strip()) < 10:
        out.append("record how and when it was filed (for example 'mailed by certified mail on 2027-04-10, receipt ...')")
    if c.get("transmission_started"):
        out.append("an electronic transmission of this return was started: reconcile it with the transmitter first")
    return out + _blockers(c)


REVIEWABLE = ("in_review", "approved", "awaiting_signature", "signed", "release_approved")
# Statuses whose package is bound to a hash a person approved (an edit or a recomputation that changes it reopens).
HASH_BOUND = ("approved", "awaiting_signature", "signed", "release_approved")
# A filed (or possibly filed) return is evidence of what was sent. It is never recomputed or edited in place:
# a what-if goes through recalculation_preview, and a change goes through an amendment case.
FROZEN = ("transmitted", "accepted", "paper_filed", "unknown", "rejected", "void")
# Who transmits (backlog F-08). A CPA may transmit a signed return directly: that act approves the release, and is
# recorded as such (facts release_approved_by/at). The workflow ("system") transmits only after a CPA approved the
# release (status release_approved), and only the package whose hash that approval named. Neither path bypasses the
# other: both run the same guards in Returns.transmit, and the two-phase activity on the return's stream keeps the
# transmission to one send whoever starts it.
TRANSMITTERS = ("cpa", "system")
RETURN_1040 = Definition(
    kind="return_1040",
    initial="preparing",
    transitions=[
        Transition("submit_for_review", ("preparing",), "in_review", _g_review),
        Transition("request_changes", ("in_review",), "preparing", roles=CPA),
        Transition("approve", ("in_review",), "approved", _g_approve, roles=CPA),
        Transition("request_signature", ("approved",), "awaiting_signature", _g_request_signature, roles=CPA),
        Transition("signed", ("awaiting_signature",), "signed", _g_signed),
        # The release: a CPA decides that the signed package goes to the named jurisdictions; the workflow then
        # transmits each submission. Separate from approving the return (the numbers) and from the signature.
        Transition("approve_release", ("signed",), "release_approved", _g_release, roles=CPA),
        Transition("transmit", ("signed", "release_approved"), "transmitted", _g_transmit, roles=TRANSMITTERS),
        Transition("ack_accepted", ("transmitted",), "accepted"),
        Transition("ack_rejected", ("transmitted",), "rejected"),
        Transition("correct", ("rejected",), "preparing", roles=CPA),
        Transition("mark_paper_filed", ("signed", "release_approved"), "paper_filed", _g_paper_filed, roles=CPA),
        Transition("reopen", REVIEWABLE, "preparing"),
        # A return that will not be filed through AgentLedger: abandoned, or filed with other software (that filing is
        # recorded as a tax-year event). Its documents stop waiting for it; the year still needs a filing on record.
        Transition("void", ("preparing", "in_review", "approved", "awaiting_signature", "signed", "release_approved", "rejected"),
                   "void", _g_void, roles=CPA),
        # A transmission whose outcome is unknown (timeout, crash after sending) is reconciled, never blindly resent.
        Transition("outcome_unknown", ("signed", "release_approved"), "unknown"),
        Transition("reconciled_submitted", ("unknown",), "transmitted"),
        Transition("reconciled_not_submitted", ("unknown",), "signed"),
    ],
    waiting={"awaiting_signature": "taxpayer signature on Form 8879", "transmitted": "IRS acknowledgement",
             "unknown": "reconciliation with the transmitter", "release_approved": "transmission by the workflow",
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


# The input lists documents populate: only there does an item's source_document name a document the return uses.
DOC_LISTS = frozenset(v[0] for m in docs.BOXES.values() for v in m.values())
MODEL_FIELDS = frozenset(IndividualReturn.model_fields)


def relied_on(inputs: dict[str, Any], provenance: dict[str, Any]) -> set[str]:
    """Every document the return relies on: each item's document, and every document behind a summed amount."""
    out: set[str] = set()
    for v in (provenance or {}).values():
        if v.get("document_id"):
            out.add(v["document_id"])
        out |= {x["document_id"] for x in v.get("documents", []) if x.get("document_id")}
    for key, items in inputs.items():
        if key in DOC_LISTS and isinstance(items, list):
            out |= {str(i[facts.IDENTITY]) for i in items if isinstance(i, dict) and i.get(facts.IDENTITY)}
    return out


# Inputs that bring an amount from an earlier year (a loss, deduction, credit or payment carried forward): the years
# it comes from stay open until this year's return is closed. tests keep this list in step with the model.
CARRYOVER_INPUTS = frozenset({"capital_loss_carryover_short", "capital_loss_carryover_long", "charity_carryover",
                              "qbi_loss_carryforward", "reit_ptp_loss_carryforward", "prior_year_unallowed_loss",
                              "prior_year_overpayment_applied",
                              # the prior-year group (model.PriorYear) and the Form 8880 testing-period distributions
                              "prior_year", "ftc_carryovers", "carryover", "amt_carryover", "nonrecaptured_loss", "traditional_ira_basis",
                              "spouse_traditional_ira_basis", "roth_ira_basis", "spouse_roth_ira_basis", "roth_conversion_basis",
                              "spouse_roth_conversion_basis", "hsa_last_month_rule_excess", "spouse_hsa_last_month_rule_excess",
                              "testing_period_distributions"})
NOL_LINES = frozenset({"8a", "a"})        # Schedule 1 line 8a, the net operating loss deduction (other_income)


def carryforward_row(key: str) -> tuple[str, str]:
    """A Result.carryforwards key as stored: (kind, detail), the detail being the owner of a per-person kind or the year of
    a per-year kind (`nonrecaptured_1231_loss_2026` -> ("nonrecaptured_1231_loss", "2026"))."""
    for kind in PER_OWNER_CARRYFORWARDS:
        if key == kind:
            return kind, "taxpayer"
        if key == f"spouse_{kind}":
            return kind, "spouse"
    for kind in PER_YEAR_CARRYFORWARDS:
        if key.startswith(f"{kind}_") and key[len(kind) + 1:].isdigit():
            return kind, key[len(kind) + 1:]
    return key, ""


def carryforward_name(kind: str, detail: str) -> str:
    """The inverse of carryforward_row: the next year's prior_year input name (a per-year kind keeps its `<kind>_<year>` key;
    roll_forward turns those into the prior_year list the year belongs to)."""
    if kind in PER_YEAR_CARRYFORWARDS:
        return f"{kind}_{detail}" if detail else kind
    return kind if detail in ("", "taxpayer") else f"{detail}_{kind}"


def _nonzero(v: Any) -> bool:
    try:
        return Decimal(str(v).replace(",", "")) != 0
    except (InvalidOperation, ValueError):
        return False


def carryovers(inputs: dict[str, Any], prefix: str = "") -> list[str]:
    """The carryover inputs a return uses, at any depth (CARRYOVER_INPUTS, and an NOL on Schedule 1 line 8a)."""
    out: list[str] = []
    for k, v in inputs.items():
        name = f"{prefix}{k}"
        if k == "other_income" and isinstance(v, dict):
            out += [f"{name}.{line}" for line, amount in v.items() if str(line).lower() in NOL_LINES and _nonzero(amount)]
        elif isinstance(v, dict):
            out += carryovers(v, name + ".")
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    out += carryovers(item, f"{name}[{i}].")
        elif k in CARRYOVER_INPUTS and _nonzero(v):
            out.append(name)
    return out


def _unknown(model: Any, data: Any, path: str) -> list[str]:
    """Keys the model does not know, at any depth (a list item may also carry its source_document)."""
    import typing

    from pydantic import BaseModel

    out: list[str] = []
    if not isinstance(data, dict):
        return out
    fields = model.model_fields
    for k, v in data.items():
        if k not in fields:
            if not (k == facts.IDENTITY and path.endswith("].")):
                out.append(f"{path}{k}")
            continue
        ann = fields[k].annotation
        for arg in (ann, *typing.get_args(ann)):
            sub = next((a for a in (arg, *typing.get_args(arg)) if isinstance(a, type) and issubclass(a, BaseModel)), None)
            if sub is None:
                continue
            if isinstance(v, list):
                for i, item in enumerate(v):
                    out += _unknown(sub, item, f"{path}{k}[{i}].")
            elif isinstance(v, dict):
                out += _unknown(sub, v, f"{path}{k}.")
            break
    return out


def _check_fields(inputs: dict[str, Any]) -> None:
    """Inputs the model does not know are refused, not stored, at every depth: they would never be computed, yet could
    carry identities or values a reader takes as part of the return (a W-2 "box10" instead of
    dependent_care_benefits)."""
    unknown = _unknown(IndividualReturn, inputs, "")
    if unknown:
        raise facts.InputRejected(f"unknown return input(s): {', '.join(unknown)}")


def package_of(inputs: dict[str, Any], result: dict[str, Any] | None, provenance: dict[str, Any],
               dispositions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The immutable package a reviewer approves and a taxpayer signs: inputs, every computed form line, the
    summary, the pinned rule and engine versions, the source documents relied on, and how each filed document the
    return does not use was accounted for."""
    r = result or {}
    return {"inputs": inputs, "forms": r.get("forms"), "summary": r.get("summary"), "pinned": r.get("pinned"),
            "documents": sorted(relied_on(inputs, provenance)),
            "dispositions": sorted((d["document_id"], d["disposition"], d["note"]) for d in (dispositions or []))}


def package_hash(inputs: dict[str, Any], result: dict[str, Any] | None, provenance: dict[str, Any],
                 dispositions: list[dict[str, Any]] | None = None) -> str:
    return hashlib.sha256(json.dumps(package_of(inputs, result, provenance, dispositions), sort_keys=True, default=str)
                          .encode()).hexdigest()


class Returns:
    def __init__(self, conn: sqlite3.Connection, kb: KnowledgeBase, sealer: Sealer | None = None, *, segregation: bool = True):
        self.conn = conn
        self.kb = kb
        self.sealer = sealer or Sealer()
        self.segregation = segregation
        conn.executescript(SCHEMA)
        if not is_pg(conn):
            # Stores from 952ee96 could hold two open conflicts on one field: the older ones are retired as superseded
            # before the database enforces one per field.
            conn.execute("UPDATE fact_conflicts SET resolution = 'superseded', resolved_by = 'upgrade', resolved_at = ?, "
                         "note = 'one open conflict per field (upgrade): a newer conflict on this field is open' "
                         "WHERE resolved_at IS NULL AND id NOT IN (SELECT MAX(id) FROM fact_conflicts WHERE resolved_at IS NULL "
                         "GROUP BY return_id, anchor)", (audit.now(),))
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS fact_conflicts_one_open ON fact_conflicts (return_id, anchor) "
                         "WHERE resolved_at IS NULL")
        cols = set() if is_pg(conn) else {r[1] for r in conn.execute("PRAGMA table_info(tax_returns)")}
        if not is_pg(conn) and "amends" not in cols:  # databases created before amendments existed
            conn.execute("ALTER TABLE tax_returns ADD COLUMN amends TEXT REFERENCES tax_returns(id)")
        from .filing import SUBMISSION

        # One engine for the return's stream and for its submissions' streams (returns/filing.py).
        self.wf = Engine(conn, {"return_1040": RETURN_1040, SUBMISSION.kind: SUBMISSION})
        self._backfill_uses()
        self._backfill_carryforwards()

    def _backfill_uses(self) -> None:
        """Returns saved before return_document_uses existed: index once every document any of their versions relied
        on, so evidence retention counts their years (a filed return is never saved again). It needs the firm key and
        a firm-wide session; until it has run, retention runs refuse to start (evidence.records)."""
        if self.conn.execute("SELECT 1 FROM kv WHERE key = ?", (USES_INDEXED,)).fetchone():
            return
        if is_pg(self.conn) and self.conn.execute("SELECT current_setting('agentledger.clients', true)").fetchone()[0] != "*":
            return                                                    # a client-scoped session sees only some returns
        try:
            with unit_of_work(self.conn):
                for (client,) in sorted({(r["client_id"],) for r in self.conn.execute("SELECT client_id FROM tax_returns").fetchall()}):
                    lock(self.conn, f"evidence:{client}")      # every lock first, in one order: never a deadlock with a move
                for r in self.conn.execute("SELECT id FROM tax_returns ORDER BY id").fetchall():
                    for v in self.conn.execute("SELECT version, inputs, provenance, result FROM tax_return_versions "
                                               "WHERE return_id = ? ORDER BY version", (r["id"],)).fetchall():
                        inputs = self.sealer.open(v["inputs"], f"return-inputs:{r['id']}")
                        prov = self.sealer.open(v["provenance"], f"return-provenance:{r['id']}")
                        result = self.sealer.open(v["result"], f"return-result:{r['id']}") if v["result"] else None
                        self._record_uses(r["id"], relied_on(inputs, prov), int(v["version"]))
                        self._record_retention_facts(r["id"], inputs, result, int(v["version"]))
                self.conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
                                  (USES_INDEXED, audit.now()))
        except PermissionError:
            return                                                    # opened without the firm key: done later

    def _backfill_carryforwards(self) -> None:
        """Versions computed before return_carryforwards existed (engine 1040-2026.2 already reported Result.carryforwards):
        record each version's carryforwards once, so the roll-forward sees every filed return."""
        if self.conn.execute("SELECT 1 FROM kv WHERE key = ?", (CARRYFORWARDS_INDEXED,)).fetchone():
            return
        if is_pg(self.conn) and self.conn.execute("SELECT current_setting('agentledger.clients', true)").fetchone()[0] != "*":
            return                                                    # a client-scoped session sees only some returns
        try:
            with unit_of_work(self.conn):
                for (client,) in sorted({(r["client_id"],) for r in self.conn.execute("SELECT client_id FROM tax_returns").fetchall()}):
                    lock(self.conn, f"evidence:{client}")
                for r in self.conn.execute("SELECT id FROM tax_returns ORDER BY id").fetchall():
                    for v in self.conn.execute("SELECT version, result FROM tax_return_versions WHERE return_id = ? AND result IS NOT NULL "
                                               "ORDER BY version", (r["id"],)).fetchall():
                        self._record_carryforwards(r["id"], self.sealer.open(v["result"], f"return-result:{r['id']}"), int(v["version"]))
                self.conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING",
                                  (CARRYFORWARDS_INDEXED, audit.now()))
        except PermissionError:
            return

    # ------------------------------------------------------------------ records
    def create(self, client_id: str, tax_year: int, actor: str, inputs: dict[str, Any] | None = None, *,
               form: str = "1040", amends: str | None = None, provenance: dict[str, Any] | None = None) -> str:
        base = {"tax_year": tax_year, "filing_status": "single", "taxpayer": {}}
        IndividualReturn.model_validate({**base, **(inputs or {})})   # malformed input is refused before anything is stored
        if provenance is None:      # a person's return; an amendment copies the filed return as it was stored
            _check_fields({**base, **(inputs or {})})
            facts.check_identities({}, {}, {**base, **(inputs or {})})
        row = self.conn.execute("SELECT id FROM tax_returns WHERE client_id = ? AND tax_year = ? AND form = ?",
                                (client_id, tax_year, form)).fetchone()
        if row:
            raise ValueError(f"a {tax_year} Form {form} already exists for {client_id}")
        rid = "ret_" + secrets.token_hex(6)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute("INSERT INTO tax_returns (id, client_id, tax_year, form, created_at, created_by, amends) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (rid, client_id, tax_year, form, now, actor, amends))
        self.wf.start(rid, "return_1040", actor, {"client_id": client_id, "tax_year": tax_year})
        self._save(rid, {**base, **(inputs or {})}, dict(provenance or {}), actor, "created")
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
        """A new version, the documents it relies on and what retention needs to know about it, in one transaction
        under the client's evidence lock (a retention run deciding for this client sees all of it, or none)."""
        with unit_of_work(self.conn):
            lock(self.conn, f"evidence:{self.get(rid)['client_id']}")
            return self._save_locked(rid, inputs, provenance, actor, note, result, crosscheck)

    def _save_locked(self, rid: str, inputs: dict[str, Any], provenance: dict[str, Any], actor: str, note: str,
                     result: dict[str, Any] | None, crosscheck: dict[str, Any] | None) -> int:
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
        self._record_uses(rid, relied_on(inputs, provenance), last + 1)
        self._record_retention_facts(rid, inputs, result, last + 1)
        self._record_carryforwards(rid, result, last + 1)
        return last + 1

    def _record_carryforwards(self, rid: str, result: dict[str, Any] | None, version: int) -> None:
        """What the version carries to the next year, sealed, one row per kind and detail (append-only)."""
        for key, amount in sorted(((result or {}).get("carryforwards") or {}).items()):
            kind, detail = carryforward_row(key)
            self.conn.execute("INSERT INTO return_carryforwards (return_id, version, kind, detail, amount, at) VALUES (?, ?, ?, ?, ?, ?) "
                              "ON CONFLICT (return_id, version, kind, detail) DO NOTHING",
                              (rid, version, kind, detail, self.sealer.seal({"v": str(amount)}, f"carryforward:{rid}"), audit.now()))

    def carryforwards(self, rid: str, version: int | None = None) -> dict[str, Decimal]:
        """A version's carryforwards (the latest version's by default), keyed by the next year's prior_year input names."""
        if version is None:
            version = int(self.latest(rid, decrypt=False)["version"])
        out: dict[str, Decimal] = {}
        for row in self.conn.execute("SELECT kind, detail, amount FROM return_carryforwards WHERE return_id = ? AND version = ? "
                                     "ORDER BY kind, detail", (rid, version)).fetchall():
            out[carryforward_name(row["kind"], row["detail"])] = Decimal(str(self.sealer.open(row["amount"], f"carryforward:{rid}")["v"]))
        return out

    def roll_forward(self, rid: str, *, require_filed: bool = True) -> dict[str, Any]:
        """The next year's `prior_year` group from this return: its carryforwards as recorded with its latest version,
        and its filing status, AGI and total tax, each with provenance naming the return and version it came from (source
        "return"; a prior-year document that later disagrees raises a fact conflict, like any other value). Only a filed
        return rolls forward unless require_filed is False (an estimate from a return still in preparation). The tax
        organizer (T1-03) puts the block into the next year's return."""
        st = self.wf.state(rid)
        if require_filed and st.status not in FILED:
            raise TransitionError(f"only a filed return (accepted or filed on paper) rolls forward; this one is {st.status}")
        r = self.get(rid)
        v = self.latest(rid)
        if not v["result"]:
            raise ValueError("compute the return before rolling it forward")
        block: dict[str, Any] = {"filing_status": v["inputs"].get("filing_status"), "agi": (v["result"].get("summary") or {}).get("agi"),
                                 "tax": (v["result"].get("summary") or {}).get("total_tax")}
        # Per-year carryforwards (the nonrecaptured net section 1231 losses, Form 4797 line 8) become the prior_year list
        # that holds one entry per loss year, each entry with its own provenance.
        per_year: dict[str, list[tuple[str, Decimal]]] = {}
        for k, x in self.carryforwards(rid, int(v["version"])).items():
            kind, detail = carryforward_row(k)
            if kind in PER_YEAR_CARRYFORWARDS:
                per_year.setdefault(kind, []).append((detail, x))
            else:
                block[k] = str(x)
        prior_year: dict[str, Any] = {k: str(x) for k, x in block.items() if x is not None}
        provenance = {f"prior_year.{k}": {"source": "return", "return_id": rid, "version": int(v["version"]), "box": k, "value": x,
                                          "confirmed": False} for k, x in prior_year.items()}
        for year, amount in sorted(per_year.get("nonrecaptured_1231_loss", [])):
            entries = prior_year.setdefault("nonrecaptured_1231_losses", [])
            provenance[f"prior_year.nonrecaptured_1231_losses[{len(entries)}].nonrecaptured_loss"] = {
                "source": "return", "return_id": rid, "version": int(v["version"]), "box": f"nonrecaptured_1231_loss_{year}", "value": str(amount),
                "confirmed": False}
            entries.append({"tax_year": int(year), "nonrecaptured_loss": str(amount)})
        return {"client_id": r["client_id"], "tax_year": r["tax_year"] + 1, "from_return": rid, "version": int(v["version"]),
                "prior_year": prior_year, "provenance": provenance}

    def _record_retention_facts(self, rid: str, inputs: dict[str, Any], result: dict[str, Any] | None, version: int) -> None:
        facts_now = {"carryover": ",".join(carryovers(inputs))}
        foreign = [f"{k}[{i}]" for k in ("interest", "dividends", "k1s") for i, it in enumerate(inputs.get(k) or [])
                   if isinstance(it, dict) and _nonzero(it.get("foreign_tax_paid"))]
        if _nonzero(((result or {}).get("forms") or {}).get("sch_3", {}).get("1")):
            foreign.append("schedule 3 line 1")
        facts_now["foreign_tax"] = ",".join(foreign)
        for kind, detail in facts_now.items():
            if detail:
                self.conn.execute("INSERT INTO return_retention_facts (return_id, version, kind, detail, at) VALUES (?, ?, ?, ?, ?) "
                                  "ON CONFLICT (return_id, version, kind) DO NOTHING", (rid, version, kind, detail, audit.now()))

    def _record_uses(self, rid: str, documents: set[str], version: int) -> None:
        """Index the documents a version relies on, under the client's evidence lock. A document deleted under retention
        meanwhile is not indexed: its item is then an orphan, which every gate refuses until a person decides."""
        if not documents:
            return
        client = self.get(rid)["client_id"]
        with unit_of_work(self.conn):
            lock(self.conn, f"evidence:{client}")               # a retention run deciding for this client sees it, or finished
            for doc in sorted(documents):
                if self.conn.execute("SELECT 1 FROM documents WHERE id = ? AND deleted_at IS NOT NULL", (doc,)).fetchone():
                    continue
                self.conn.execute("INSERT INTO return_document_uses (return_id, document_id, first_version, at) VALUES (?, ?, ?, ?) "
                                  "ON CONFLICT (return_id, document_id) DO NOTHING", (rid, doc, version, audit.now()))

    # ------------------------------------------------------------------ preparation
    def save_inputs(self, rid: str, inputs: dict[str, Any], actor: str, *, provenance: dict[str, Any] | None = None,
                    note: str = "edited") -> dict[str, Any]:
        IndividualReturn.model_validate(inputs)  # reject malformed input before it is stored
        cur = self.latest(rid)
        if provenance is None:      # a person's edit: no unknown inputs, identities are the population's
            _check_fields(inputs)
            inputs = facts.carry_identity(cur["inputs"], cur["provenance"], inputs)
            facts.check_identities(cur["inputs"], cur["provenance"], inputs)
        st = self.wf.state(rid)
        if st.status in REVIEWABLE:
            self._reopen(rid, actor, f"inputs changed while {st.status}")
        elif st.status not in ("preparing",):
            raise TransitionError(f"a return that is {st.status} cannot be edited")
        if provenance is None:      # a person's edit: changed document values become the preparer's, on record
            prov, changed = facts.mark_edits(cur["inputs"], inputs, cur["provenance"], actor)
        else:
            prov, changed = provenance, facts.changed_anchors(cur["inputs"], cur["provenance"], inputs, provenance)
        self._save(rid, inputs, prov, actor, note)
        facts.record(self.conn, self.sealer, rid, inputs, prov, changed, actor=actor)
        return self.compute(rid, actor)

    def populate_from_documents(self, rid: str, actor: str) -> dict[str, Any]:
        """Fill the return from its filed documents without overwriting anything (backlog F-07): disagreements with
        what is already there become fact conflicts for a person to resolve."""
        r = self.get(rid)
        cur = self.latest(rid)
        if self.wf.state(rid).status in FROZEN:
            raise TransitionError("a filed return is frozen; start an amendment to change it")
        pop = self._population(rid, cur)
        inputs, prov, conflicts, issues, changed = facts.merge_population(
            cur["inputs"], cur["provenance"], pop.inputs, pop.provenance, facts.resolved_keeps(self.conn, self.sealer, rid),
            pop.unreadable)
        kept = facts.kept_orphans(self.conn, rid)
        for i in issues:   # a person decides these; a missing amount is never zero, an orphaned item never silent
            if i["code"] == "missing_value" and i.get("document_id"):
                conflicts.append({"path": i["path"], "anchor": i["anchor"], "current": None, "proposed": None,
                                  "document_id": i["document_id"], "box": "required amount", "current_source": "missing"})
            elif i["code"] == "document_no_longer_provides" and i["anchor"] not in kept:
                conflicts.append({"path": i["path"], "anchor": i["anchor"], "current": None, "proposed": None,
                                  "document_id": i["document_id"], "box": "document left the return", "current_source": "orphan"})
        if inputs != cur["inputs"] or prov != cur["provenance"]:
            self.save_inputs(rid, inputs, actor, provenance=prov, note=f"populated from {len(pop.documents)} document(s)")
        else:
            self.compute(rid, actor)
        raised, superseded = self._reconcile_conflicts(rid, conflicts, actor)
        if raised or superseded:
            audit.record(self.conn, actor, "agent", "return.fact_conflicts",
                         {"return_id": rid, "raised": len(raised), "superseded": superseded}, client_id=r["client_id"])
        return {"documents": pop.documents, "issues": pop.issues + issues, "fields": len(changed),
                "conflicts": len(facts.open_conflicts(self.conn, self.sealer, rid)), "superseded": superseded}

    def _reconcile_conflicts(self, rid: str, proposals: list[dict[str, Any]], actor: str) -> tuple[list[int], int]:
        """One open conflict per field, always matching what the documents say now: an open conflict whose proposal
        changed, or whose disagreement is gone, is closed as superseded before the current one is raised."""
        by_anchor: dict[str, dict[str, Any]] = {}
        for c in proposals:
            by_anchor.setdefault(c["anchor"], c)
        superseded = 0
        for oc in facts.open_conflicts(self.conn, self.sealer, rid):
            new = by_anchor.get(oc["anchor"])
            if (new and new["document_id"] == oc["document_id"] and facts.same(new["proposed"], oc["proposed_value"])
                    and facts.same(new["current"], oc["current_value"])) or (
                    new and oc["anchor"].startswith(("missing:", "orphan:")) and new["document_id"] == oc["document_id"]):
                by_anchor.pop(oc["anchor"])                           # the same question is already open
                continue
            why = (f"the documents now propose {new['proposed']} (return: {new['current']})" if new else
                   "no longer in conflict: the documents and the return agree, or the document left the return")
            facts.close_conflict(self.conn, oc["id"], resolution="superseded", actor=actor, note=why)
            superseded += 1
        return facts.raise_conflicts(self.conn, self.sealer, rid, list(by_anchor.values()), actor), superseded

    def _population(self, rid: str, v: dict[str, Any]) -> docs.Populated:
        """The return's documents as they are now, without those a person accounted for on this return."""
        r = self.get(rid)
        return docs.populate(self.conn, r["client_id"], r["tax_year"], joint=v["inputs"].get("filing_status") == "mfj",
                             exclude={d["document_id"] for d in self.dispositions(rid)})

    def _drift(self, rid: str, v: dict[str, Any], pop: docs.Populated) -> dict[str, list[str]]:
        """What re-populating would change or ask right now. Every gate refuses a return that no longer matches its
        documents: one whose document left after the last population, an edit over a document value never decided,
        or values the documents provide that the return does not have."""
        _, _, conflicts, issues, changed = facts.merge_population(
            v["inputs"], v["provenance"], pop.inputs, pop.provenance, facts.resolved_keeps(self.conn, self.sealer, rid),
            pop.unreadable)
        open_anchors = {c["anchor"] for c in facts.open_conflicts(self.conn, self.sealer, rid)}
        kept = facts.kept_orphans(self.conn, rid)
        return {"disagreements": sorted({c["anchor"] for c in conflicts} - open_anchors),
                "left": sorted({i["anchor"] for i in issues if i["code"] == "document_no_longer_provides"} - open_anchors - kept),
                "not_applied": sorted(set(changed)),
                "duplicates": sorted({i["anchor"] for i in issues if i["code"] == "duplicate_identity"})}

    def _document_value_now(self, rid: str, c: dict[str, Any]) -> tuple[Any, dict[str, Any]] | None:
        """What the documents say for a conflict's field at this moment: only live documents filed to this return's
        client and year count (populate reads no deleted, moved or re-dated document). None if they say nothing."""
        cur = self.latest(rid)
        pop = self._population(rid, cur)
        anchor = c["anchor"]
        if "[" in anchor:
            key, rest = anchor.split("[", 1)
            doc, _, field = rest.partition("]")
            field = field.lstrip(".")
            for j, item in enumerate(pop.inputs.get(key) or []):
                if item.get(facts.IDENTITY) == doc:
                    path = f"{key}[{j}].{field}"
                    p = pop.provenance.get(path)
                    value = facts.get_path(pop.inputs, path)
                    if p is None or facts.empty(value):
                        return None
                    return value, p
            return None
        key, field = anchor.split(".", 1)
        p = pop.provenance.get(anchor)
        if not p or c["document_id"] not in ({x["document_id"] for x in p.get("documents", [])} | {p.get("document_id")}):
            return None
        return (pop.inputs.get(key) or {}).get(field), p

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
        cur = self.latest(rid)
        anchor = c["anchor"]
        if anchor.startswith("missing:"):
            if choice == "document":
                raise ValueError("the document has no value here; enter the amount, then keep it")
            path = facts.locate(anchor, cur["inputs"], cur["provenance"])
            lst, _, rest = anchor.split(":", 1)[1].partition("[")
            field = rest.partition("]")[2].lstrip(".")
            if path is None or not facts.satisfied(lst, field, facts.get_path(cur["inputs"], path)):
                raise ValueError("the amount is still missing: enter it from the document (0 only if the document shows 0; a "
                                 "valid code where a code is required), then keep it")
        elif anchor.startswith("orphan:"):
            if choice == "document":
                raise ValueError("that document no longer belongs to this return: remove the item, or keep it with a reason")
            if len(note.strip()) < 10:
                raise ValueError("say why the item stays although its document left the return")
        elif choice == "document":
            fresh = self._document_value_now(rid, c)
            if fresh is None:
                facts.close_conflict(self.conn, conflict_id, resolution="superseded", actor=actor,
                                     note="the documents no longer provide this value")
                raise ValueError("the documents no longer provide this value (re-read, moved, re-dated or deleted); "
                                 "re-populate to see what they say now")
            value, new_p = fresh
            path = facts.locate(anchor, cur["inputs"], cur["provenance"])
            if path is None:
                facts.close_conflict(self.conn, conflict_id, resolution="superseded", actor=actor,
                                     note="the item from that document is no longer on the return")
                raise ValueError("the item from that document is no longer on the return")
            if not facts.same(value, c["proposed_value"]):
                facts.close_conflict(self.conn, conflict_id, resolution="superseded", actor=actor,
                                     note=f"the documents now say {value}, not {c['proposed_value']}")
                facts.raise_conflicts(self.conn, self.sealer, rid, [{
                    "path": path, "anchor": anchor, "current": facts.get_path(cur["inputs"], path), "proposed": value,
                    "document_id": new_p.get("document_id", ""), "box": new_p.get("box"),
                    "current_source": (cur["provenance"].get(path) or {}).get("source", "preparer")}], actor)
                raise ValueError(f"the documents now say {value}, not {c['proposed_value']}: a new conflict shows it")
            inputs = facts.set_path(cur["inputs"], path, value)
            prov = {**cur["provenance"], path: {**new_p, "source": "resolution", "resolved_by": actor, "confirmed": True}}
            self.save_inputs(rid, inputs, actor, provenance=prov, note=f"fact conflict {conflict_id}: document value taken")
        facts.close_conflict(self.conn, conflict_id, resolution="replaced" if choice == "document" else "kept", actor=actor,
                             note=note)
        audit.record(self.conn, actor, "cpa", "return.fact_conflict_resolved",
                     {"return_id": rid, "conflict_id": conflict_id, "choice": choice}, client_id=self.get(rid)["client_id"])
        return {"open": len(self.conflicts(rid))}

    def dispositions(self, rid: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM return_document_dispositions WHERE return_id = ? ORDER BY id", (rid,))]

    def unaccounted_documents(self, rid: str, v: dict[str, Any] | None = None) -> list[str]:
        """Filed documents of the year that the return neither uses nor has accounted for."""
        r = self.get(rid)
        v = v or self.latest(rid)
        used = relied_on(v["inputs"], v["provenance"])
        used |= {p.get("previous_document") for p in v["provenance"].values() if p.get("previous_document")}
        used |= {d["document_id"] for d in self.dispositions(rid)}
        out = []
        # A filed tax form with no known year may belong to this return: it is listed until a CPA confirms its year
        # (evidence.records.confirm_retention) or a person accounts for it here. The prior year's filed return is this
        # return's document too (documents.populate reads it into the prior_year group).
        for d in self.conn.execute("SELECT id, doc_type FROM documents WHERE client_id = ? AND (tax_year = ? OR tax_year IS NULL "
                                   "OR (tax_year = ? AND doc_type = ?)) AND status = 'filed' AND deleted_at IS NULL ORDER BY received_at, id",
                                   (r["client_id"], r["tax_year"], r["tax_year"] - 1, docs.PRIOR_YEAR_RETURN)):
            if d["doc_type"] in TAX_FORMS and d["id"] not in used:
                out.append(d["id"])
        return out

    def account_for_document(self, rid: str, document_id: str, disposition: str, note: str, actor: str) -> list[dict[str, Any]]:
        """A person accounts for a filed document the return does not use: its amounts were entered by hand, or it
        does not apply to this return. On record, part of the approved package."""
        st = self.wf.state(rid)
        if st.status in FROZEN:
            raise TransitionError(f"this return is {st.status}: its package is frozen")
        if disposition not in ("entered_by_hand", "not_applicable"):
            raise ValueError("disposition is entered_by_hand or not_applicable")
        if len(note.strip()) < 10:
            raise ValueError("say why (for example 'entered as two Form 8949 rows from the 1099-B')")
        r = self.get(rid)
        d = self.conn.execute("SELECT id FROM documents WHERE id = ? AND client_id = ? AND (tax_year = ? OR tax_year IS NULL "
                              "OR (tax_year = ? AND doc_type = ?)) AND status = 'filed' AND deleted_at IS NULL",
                              (document_id, r["client_id"], r["tax_year"], r["tax_year"] - 1, docs.PRIOR_YEAR_RETURN)).fetchone()
        if not d:
            raise KeyError(f"{document_id} is not a filed {r['tax_year']} document of this client (or its prior-year return)")
        v = self.latest(rid)
        if document_id in relied_on(v["inputs"], v["provenance"]):
            raise ValueError("this document is on the return; remove its item first (an entry by hand then replaces it)")
        self.conn.execute("INSERT INTO return_document_dispositions (return_id, document_id, disposition, note, actor, at) "
                          "VALUES (?, ?, ?, ?, ?, ?)", (rid, document_id, disposition, note.strip(), actor, audit.now()))
        if disposition == "entered_by_hand":               # its amounts are on the return: retention counts its year
            self._record_uses(rid, {document_id}, int(self.latest(rid, decrypt=False)["version"]))
        audit.record(self.conn, actor, "cpa", "return.document_accounted_for",
                     {"return_id": rid, "document_id": document_id, "disposition": disposition}, client_id=r["client_id"])
        if st.status in REVIEWABLE:
            self._reopen(rid, actor, f"document {document_id} accounted for while {st.status}")
        return self.dispositions(rid)

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
        assets = None
        if any(d.asset_id is not None for d in ret.dispositions):   # Form 4797: a disposition names a registered asset
            from ..ledger.store import assets as registered_assets

            assets = [a for a, _ in registered_assets(self.conn, self.get(rid)["client_id"])]
        res = compute_individual(Ctx(self.kb), ret, assets, linked=self._linked_parent(rid, ret))
        result = res.to_dict()
        forms = list(result["forms"]) + (["f1040x"] if self.get(rid)["form"] == "1040-X" else [])
        result["coverage"] = self._coverage(forms, ret.tax_year)
        result["pinned"] = {"kb_version": self.kb.version(), "engine": ENGINE_VERSION}
        return ret, res, result

    def _linked_parent(self, rid: str, ret: IndividualReturn) -> dict[str, Any] | None:
        """Form 8615 from the parent's own return of this firm (form_8615.parent_return_id): the lines the form takes from
        the parent's Form 1040 (line 15, line 16, line 3a, the net capital gain and Schedule D lines 18 and 19), read from
        that return's latest computed result, with the version and status so the child's return records what it relied on.
        The connection is the firm's store, so another firm's return is not reachable. Anything that cannot be read is
        returned as an error the engine raises as a blocking diagnostic (form_8615_parent_return_unresolved)."""
        pid = ret.form_8615.parent_return_id
        if not pid:
            return None
        if pid == rid:
            return {"parent": {"return_id": pid, "error": "it is the child's own return"}}
        try:
            pr, pv = self.get(pid), self.latest(pid)
        except KeyError:
            return {"parent": {"return_id": pid, "error": "no return with that id exists in this firm"}}
        if pr["tax_year"] != ret.tax_year:
            return {"parent": {"return_id": pid, "error": f"it is a {pr['tax_year']} return; Form 8615 uses the parent's return for the tax "
                                                          f"year ending in the child's {ret.tax_year} tax year, and a different parent tax year is not supported"}}
        result = pv["result"] or {}
        forms = result.get("forms") or {}
        f1040 = forms.get("f1040") or {}
        if not result or "15" not in f1040:
            return {"parent": {"return_id": pid, "error": "it has not been computed yet (compute the parent's return first)"}}
        sch_d = forms.get("sch_d") or {}
        if "15" in sch_d and "16" in sch_d:
            ncg = max(Decimal(0), min(Decimal(sch_d["15"]), Decimal(sch_d["16"])))
        else:
            ncg = max(Decimal(0), Decimal(f1040.get("7a", "0")))
        taxpayer = pv["inputs"].get("taxpayer") or {}
        facts = ParentFacts(name=" ".join(x for x in (taxpayer.get("first_name"), taxpayer.get("last_name")) if x), ssn=taxpayer.get("ssn"),
                            filing_status=result.get("filing_status") or pv["inputs"].get("filing_status"),
                            taxable_income=Decimal(f1040["15"]), tax=Decimal(f1040.get("16", "0")), qualified_dividends=Decimal(f1040.get("3a", "0")),
                            net_capital_gain=ncg, rate_28_gain=Decimal(sch_d.get("18", "0")), unrecaptured_1250_gain=Decimal(sch_d.get("19", "0")))
        blocking = sum(1 for d in result.get("diagnostics", []) if d.get("severity") == "error")
        return {"parent": {"return_id": pid, "version": int(pv["version"]), "status": self.wf.state(pid).status, "blocking": blocking, "facts": facts}}

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
        # The amendment keeps each item's document and provenance, so re-populating it never duplicates them.
        new = self.create(r["client_id"], r["tax_year"], actor, dict(cur["inputs"]), form="1040-X", amends=rid,
                          provenance=cur["provenance"])
        for d in self.dispositions(rid):        # what the filed return accounted for stays accounted for
            self.conn.execute("INSERT INTO return_document_dispositions (return_id, document_id, disposition, note, actor, at) "
                              "VALUES (?, ?, ?, ?, ?, ?)", (new, d["document_id"], d["disposition"],
                                                            f"{d['note']} (carried from {rid})", actor, audit.now()))
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
        bound = st.facts.get("approved_hash") if st.status in HASH_BOUND else st.facts.get("review_hash")
        if st.status in REVIEWABLE and bound and package_hash(cur["inputs"], result, cur["provenance"], self.dispositions(rid)) != bound:
            self._reopen(rid, actor, "recomputed return differs from the reviewed/approved package "
                                     "(rules, engine or results changed); review and signature are void")
        return result

    def _reopen(self, rid: str, actor: str, note: str) -> None:
        """Back to preparation: review, approval, signature and release are void, and submissions planned under the
        release will not be transmitted."""
        from .filing import Filing

        with unit_of_work(self.conn):
            self.wf.send(rid, "reopen", actor, note=note)
            Filing(self).cancel_queued(rid, actor, f"the return was reopened: {note}")

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
        pop = self._population(rid, v)
        open_now = facts.open_conflicts(self.conn, self.sealer, rid)
        return {"computed": bool(result), "blocking": below + sum(1 for d in result.get("diagnostics", []) if d["severity"] == "error"),
                "missing": len(facts.missing_required(v["inputs"], v["provenance"], pop.unreadable)),
                "drift": self._drift(rid, v, pop),
                "unconfirmed": sum(1 for p in v["provenance"].values() if not p.get("confirmed")),
                "conflicts": len([x for x in open_now if not x["anchor"].startswith("orphan:")]),
                "orphaned": len([x for x in open_now if x["anchor"].startswith("orphan:")]),
                "unaccounted": self.unaccounted_documents(rid, v),
                "crosscheck": cc.get("status"), "explained": explained,
                "package_hash": package_hash(v["inputs"], v["result"], v["provenance"], self.dispositions(rid))}

    def submit_for_review(self, rid: str, actor: str, *, explanation: str = "") -> State:
        ctx = self._review_context(rid, bool(explanation.strip()))
        return self.wf.send(rid, "submit_for_review", actor, role="cpa", context=ctx, note=explanation,
                            facts={"submitted_by": actor, "review_hash": ctx["package_hash"]})

    def current_package_hash(self, rid: str) -> str:
        v = self.latest(rid)
        return package_hash(v["inputs"], v["result"], v["provenance"], self.dispositions(rid))

    def approve(self, rid: str, actor: str, role: str) -> State:
        """Approval re-runs every review check: a conflict raised or a document filed during review stops it."""
        ctx = {**self._review_context(rid, True), "segregation": self.segregation}
        v = self.latest(rid, decrypt=False)["version"]
        return self.wf.send(rid, "approve", actor, role=role, context=ctx,
                            facts={"approved_by": actor, "approved_hash": ctx["package_hash"], "approved_version": v})

    def request_changes(self, rid: str, actor: str, role: str, note: str) -> State:
        return self.wf.send(rid, "request_changes", actor, role=role, note=note)

    def request_signature(self, rid: str, actor: str, role: str) -> State:
        return self.wf.send(rid, "request_signature", actor, role=role, context=self._review_context(rid, True),
                            facts={"signature_requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    def record_signature(self, rid: str, actor: str, *, method: str, return_hash: str, kba_transaction_id: str = "",
                         signers: list[str] | None = None) -> State:
        return self.wf.send(rid, "signed", actor, context={"method": method, "return_hash": return_hash,
                                                            "kba_transaction_id": kba_transaction_id},
                            facts={"signature_method": method, "kba_transaction_id": kba_transaction_id,
                                   "signers": signers or [], "signed_hash": return_hash})

    def _blockers_now(self, rid: str) -> list[str]:
        """Every review blocker as things stand now (re-run at each filing gate)."""
        return _blockers(self._review_context(rid, True))

    def filing_blockers(self, rid: str, jurisdictions: list[str]) -> list[dict[str, Any]]:
        """What the coverage registry says against filing in each jurisdiction: federally, the return's forms and the
        MeF channel (as computed and pinned with the return); for a state, that state's capability."""
        from .. import coverage
        from .filing import FEDERAL, coverage_id

        result = self.latest(rid)["result"] or {}
        year = self.get(rid)["tax_year"]
        out: list[dict[str, Any]] = []
        for j in jurisdictions:
            if j == FEDERAL:
                blockers = (result.get("coverage") or {}).get("filing_blockers")
                if blockers is None:
                    blockers = self._coverage(list(result.get("forms", {})), year)["filing_blockers"]
                out += [{**b, "jurisdiction": FEDERAL} for b in blockers]
            else:
                c = coverage.lookup(coverage_id(j), year, jurisdiction=j)
                if coverage.RANK[c["status"]] < coverage.RANK["filing-approved"]:
                    out.append({"form": c["id"], "status": c["status"], "need": "filing-approved", "limits": c.get("limits", []),
                                "jurisdiction": j})
        return out

    def approve_release(self, rid: str, actor: str, role: str, *, efile_ready: bool, jurisdictions: list[str] | None = None) -> State:
        """A CPA releases the signed package for electronic filing in the named jurisdictions (US-FED alone by
        default). Every filing check runs now, per jurisdiction; the workflow then transmits each submission
        (returns/filing.py). Approving the return, the taxpayer's signature and this release are three decisions."""
        from .filing import FEDERAL, Filing, jurisdictions_of

        where = jurisdictions_of(jurisdictions)
        with unit_of_work(self.conn):
            ctx = {**self._review_context(rid, True), "efile_ready": efile_ready, "jurisdictions": where,
                   "filing_blockers": self.filing_blockers(rid, where)}
            h = ctx["package_hash"]
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            # Today every jurisdiction files the whole package; per-state packages and their hashes arrive with T1-05.
            st = self.wf.send(rid, "approve_release", actor, role=role, context=ctx,
                              facts={"release_approved_by": actor, "release_approved_at": now, "release_hash": h,
                                     "release_efile_ready": bool(efile_ready),
                                     "submissions": {"jurisdictions": where, "hashes": {j: h for j in where}}})
            subs = Filing(self).plan(rid, actor, where, h)
            audit.record(self.conn, actor, role, "return.release_approved",
                         {"return_id": rid, "jurisdictions": where, "release_hash": h, "submissions": subs,
                          "federal": next((s for s, j in zip(subs, where) if j == FEDERAL), None)}, client_id=self.get(rid)["client_id"])
        return st

    def transmit(self, rid: str, actor: str, role: str, *, efile_ready: bool, submit, lookup=None) -> State:
        """Transmit the approved package at most once.

        `submit(idempotency_key)` sends it; `lookup(idempotency_key)` asks the transmitter whether that key was
        already received (returns the result dict, or None if not). If a previous attempt started but its outcome
        was never recorded, we reconcile through `lookup` instead of sending again; without `lookup` the return
        moves to `unknown` for a person to reconcile.

        Two roles may call this, and neither bypasses the other (see TRANSMITTERS): a CPA transmits a signed return
        directly, which approves the release in the same act and is recorded as such; the workflow ("system")
        transmits only after a CPA approved the release, and only that package. Every check runs before anything
        leaves the system; the federal submission row and stream are written with the return's events."""
        from .filing import Filing

        return Filing(self).transmit_return(rid, actor, role, efile_ready=efile_ready, send=submit, lookup=lookup)

    def filing_summary(self, rid: str) -> dict[str, Any]:
        """The release and every submission of the return, and whether all required ones are accepted."""
        from .filing import Filing

        return Filing(self).summary(rid)

    def retransmit(self, sub_id: str, actor: str, role: str) -> dict[str, Any]:
        """A CPA replaces a rejected state submission with a retransmission (returns/filing.py)."""
        from .filing import Filing

        return Filing(self).retransmit(sub_id, actor, role)

    def _transmission_started(self, rid: str) -> bool:
        events = [h.get("event") for h in self.wf.state(rid).history]
        started = max((i for i, e in enumerate(events) if e == "activity_started:transmit"), default=-1)
        cleared = max((i for i, e in enumerate(events) if e == "reconciled_not_submitted"), default=-1)
        return started > cleared

    def void(self, rid: str, actor: str, role: str, note: str) -> State:
        """A CPA voids a return that will not be filed through AgentLedger (abandoned, or filed elsewhere: record that
        filing with evidence.records.record_tax_event). Irreversible; an amendment or a new return starts afresh."""
        from .filing import Filing

        with unit_of_work(self.conn):
            st = self.wf.send(rid, "void", actor, role=role, note=note,
                              context={"note": note, "transmission_started": self._transmission_started(rid)})
            Filing(self).cancel_queued(rid, actor, "the return was voided")
        return st

    def mark_paper_filed(self, rid: str, actor: str, role: str, note: str) -> State:
        """A CPA records that the signed return was filed on paper, how and when (every filing check runs first)."""
        from .filing import Filing

        ctx = {**self._review_context(rid, True), "note": note, "transmission_started": self._transmission_started(rid)}
        with unit_of_work(self.conn):
            st = self.wf.send(rid, "mark_paper_filed", actor, role=role, context=ctx, note=note)
            Filing(self).cancel_queued(rid, actor, "the return was filed on paper")
        return st

    def reconcile_transmission(self, rid: str, actor: str, role: str, *, submitted: bool, submission_id: str = "",
                               evidence: str = "") -> State:
        """A person confirms with the transmitter what happened to an attempt whose outcome was unknown (the federal
        submission row, when there is one, moves with the return)."""
        from .filing import Filing

        return Filing(self).reconcile_return(rid, actor, role, submitted=submitted, provider_submission_id=submission_id,
                                             evidence=evidence)
