"""Evidence lifecycle (backlog F-06): versions, statutory retention, legal holds and write-ahead deletion.

* Every stored document has an append-only version history (`document_versions`) and its own stored objects (the
  vault's locator is keyed by the document as well as its bytes), so one document's deletion never touches another's
  bytes.
* A legal hold on a client (or a single document) blocks deletion, of a document or of the whole firm, until a CPA
  releases it with a reason. A hold on a client covers every document that ever belonged to it (`document_moves`).

Retention is computed when deletion is considered, never frozen at intake (re-audit of 952ee96, finding 5, and its
adversarial review). A document may be deleted only when all of these hold:

* it is filed to a client (review-inbox documents are never deleted) and no active legal hold covers it;
* a person confirmed its tax year (or that it has none) and its retention class: a heuristic year never drives
  deletion; its class has a finite period (basis records, and records of capitalized costs, are kept until a CPA
  releases them);
* N years have passed since it was received, N being the class's period;
* no return that relied on it is still unfiled (preparing, in review, signed, transmitted without acceptance,
  rejected, unknown): a return in progress needs its documents whatever any earlier filing says;
* for every (client, tax year) it supports - its confirmed year, the year of every return that relied on it (that
  return's client), the year of every journal entry it supports (the entry's client), and every later year in which
  that client uses a carryover - the returns that year needs are on record as filed (through AgentLedger, or
  recorded by a CPA with its evidence) or as not required. Income tax records need the client's income tax return;
  a pass-through entity's records also need its owners' returns (`owners_filed`); payroll records also need the
  employment tax returns: an annual return, or the 941 of every quarter, and the 940. With a return missing no
  limitation period runs (IRC 6501(c)(3)). A "not required" record is void once AgentLedger holds a return for the
  year. The period then runs from the later of the latest such filing, original or amended, and the date prescribed
  for filing (an early return counts from the due date, 6501(b)(1) and (b)(2)): max(N, 7) years for income tax
  records (6501(a), 6501(e), 6511(d)(1)), 10 years from the due date when the year shows foreign tax
  (6511(d)(3)(A)), N years for employment records; 2 years after the latest income tax payment (6511(a)) and 4
  years after the latest employment tax payment (Treas. Reg. 31.6001-1(e)(2));
* and GRACE_DAYS more: a deadline on a weekend or holiday moves to the next business day (7503), a timely mailed
  claim or notice arrives later (7502).

No rule sees fraud, an agreement extending the assessment period, a missing foreign information return, a listed
transaction or a pending claim: a retention run is a CPA's decision over the list of documents they reviewed, with the
attestation in ATTESTATION, and a legal hold is how any such circumstance keeps evidence.

Deletion is write-ahead (finding 4). In one transaction the document is tombstoned (marked deleted, its extracted
content cleared, its hash released so the same file can be filed again later), and the receipt, the audit record and
one pending object deletion per stored object are written. Only after that transaction commits are bytes deleted, and
each outcome is recorded. A failure or crash after the commit leaves a receipt with the bytes still present, the
safe direction, and the next run retries. Bytes are never gone without a committed receipt, and bytes still present
are never deleted while a legal hold covers any document that references them.

Races are closed with db.lock("evidence:<client>") (`evidence_lock`): placing a hold, recording a filing, confirming
retention, a return recording the documents it relies on, moving a document, and deciding or finishing a deletion all
serialize on the client (every client involved, always in the same order; the set is re-checked once locked). No lock
is held across a call to storage: before an object is deleted, an in-flight marker with a deadline is committed under
the locks, and a hold placed meanwhile reports the objects still being removed (`in_flight`) instead of claiming they
are kept. A session that ends in the middle of a delete therefore loses nothing it was protecting.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .. import audit
from ..db import is_pg, lock, one, rows, unit_of_work

REVIEWER = ("cpa",)
# Statutory floors for each class, in years counted as above. A firm may lengthen a class, never shorten it below
# these (IRC 6501, 6511(d)(1); employment tax records at least 4 years, Treas. Reg. 31.6001-1(e)(2); a preparer's
# copy 3 years after the close of the July-June return period, 6107(b), which 4 years from filing always covers).
FLOORS = {"tax_return_support": 7, "employment_tax": 4, "preparer_copy": 4, "engagement_and_correspondence": 3}
DEFAULT_FLOOR = 7
TAX_RECORD_YEARS = 7          # a record supporting an income tax year is kept at least this long for that year
FOREIGN_TAX_YEARS = 10        # IRC 6511(d)(3)(A): a foreign tax credit claim, 10 years from the due date
GROUPS = ("income", "employment", "basis", "firm")
KINDS = ("filed", "amended", "payment", "not_required", "owners_filed")
FILING_KINDS = ("filed", "amended", "not_required")
GRACE_DAYS = 90
IN_FLIGHT = timedelta(minutes=15)    # an object deletion's deadline; storage calls are bounded well below it

# Returns retention counts from. Anything else (an extension, a state return, an information return, a gift tax
# return, an FBAR) is not a federal income or employment tax return and is refused.
INDIVIDUAL_INCOME_FORMS = frozenset({"1040", "1040-SR", "1040-NR", "1040-SS", "1040-PR"})
INCOME_FORMS = INDIVIDUAL_INCOME_FORMS | frozenset({
    "1041", "1065", "1066", "1120", "1120-S", "1120-C", "1120-F", "1120-FSC", "1120-H", "1120-L", "1120-ND", "1120-PC",
    "1120-POL", "1120-REIT", "1120-RIC", "1120-SF", "990-T"})
EMPLOYMENT_ANNUAL = frozenset({"943", "943-PR", "944", "CT-1", "SCHEDULE H"})
EMPLOYMENT_QUARTERLY = frozenset({"941", "941-SS", "941-PR"})
EMPLOYMENT_FORMS = EMPLOYMENT_ANNUAL | EMPLOYMENT_QUARTERLY | frozenset({"940", "945"})
QUARTERS = ("Q1", "Q2", "Q3", "Q4")
# Entities whose income is taxed to their owners: their records support the owners' returns too.
NOT_PASS_THROUGH = frozenset({"c_corp", "corporation", "nonprofit", "exempt", "tax_exempt"})
CASH_ACCOUNT = re.compile(r"\b(cash|bank|checking|savings|receivables?|undeposited funds|clearing|petty cash)\s*$", re.I)
# The shipped classes keep their groups whatever a firm's policy file says; a firm's own class must declare one.
SHIPPED_GROUPS = {"tax_return_support": "income", "employment_tax": "employment", "preparer_copy": "income",
                  "engagement_and_correspondence": "firm", "property_basis": "basis"}
# Document types whose class must belong to a group (a payroll report is never an income-only record, a closing
# disclosure never anything but a basis record).
DOC_TYPE_GROUPS = {"Payroll report": "employment", "Closing disclosure": "basis", "Settlement statement": "basis",
                   "K-1": "basis", "1099-B": "basis", "Brokerage statement": "basis", "Trade confirmation": "basis",
                   "Capital account statement": "basis", "Capital call notice": "basis", "Distribution notice": "basis"}
PASS_THROUGH_FORMS = frozenset({"1065", "1066", "1120-S", "1041"})
C_CORPORATION_FORMS = frozenset({"1120", "1120-C", "1120-F", "1120-FSC", "1120-H", "1120-L", "1120-ND", "1120-PC",
                                 "1120-POL", "1120-REIT", "1120-RIC", "1120-SF", "990-T"})
ATTESTATION = ("No open examination or notice; no fraud (IRC 6501(c)(1)-(2)); no agreement extending the assessment "
               "period (Form 872); no foreign information return left unfiled (6501(c)(8)); no undisclosed listed "
               "transaction (6501(c)(10)); no claim, carryback or refund suit still possible (6511(d), 6532(a)); no "
               "carryover still in use and no basis of property still owned that these records support; no client "
               "request to keep them. Where any applies, a legal hold is placed instead.")
USES_INDEXED = "return_document_uses_indexed"


class RetentionError(PermissionError):
    pass


def _default_group(name: str) -> str:
    return {"employment_tax": "employment", "property_basis": "basis", "engagement_and_correspondence": "firm"}.get(name, "income")


def load_policy(config_dir: Path) -> dict[str, Any]:
    p = Path(config_dir) / "retention.yaml"
    policy = yaml.safe_load(p.read_text(encoding="utf-8"))
    classes = policy.get("classes") or {}
    if policy.get("default") not in classes:
        raise ValueError("the retention policy's default class is not defined")
    for name, c in classes.items():
        group = c.get("group") or SHIPPED_GROUPS.get(name)
        if group is None:
            raise ValueError(f"retention class {name}: declare its group ({', '.join(GROUPS)})")
        if name in SHIPPED_GROUPS and group != SHIPPED_GROUPS[name]:
            raise ValueError(f"retention class {name} belongs to group {SHIPPED_GROUPS[name]}; a policy cannot move it")
        if group not in GROUPS:
            raise ValueError(f"retention class {name}: group is one of {', '.join(GROUPS)}")
        c["group"] = group
        years = c.get("years")
        if group == "basis":
            if years is not None:
                raise ValueError(f"retention class {name} holds basis records: they are kept until a CPA releases them "
                                 "(years: null); the period that matters starts with the year the property is sold")
            continue
        if years is None:
            continue                                         # kept until a CPA releases it
        floor = FLOORS.get(name, DEFAULT_FLOOR)
        if group == "employment":
            floor = max(floor, 4)
        if int(years) < floor:
            raise ValueError(f"retention class {name} keeps records {years} years; the statutory minimum is {floor} "
                             "(counted from the later of filing and the due date)")
    for doc_type, cls in (policy.get("by_doc_type") or {}).items():
        if cls not in classes:
            raise ValueError(f"document type {doc_type} maps to retention class {cls}, which is not defined")
        expected = DOC_TYPE_GROUPS.get(doc_type)
        if expected and classes[cls]["group"] != expected:
            raise ValueError(f"document type {doc_type} needs a class of group {expected}, not {cls} ({classes[cls]['group']})")
    return policy


def policy() -> dict[str, Any]:
    """The installation's policy: $AGENTLEDGER_HOME/config/retention.yaml, else the one shipped with the code."""
    import os

    home = os.environ.get("AGENTLEDGER_HOME")
    for root in ([Path(home)] if home else []) + [Path(__file__).resolve().parents[3]]:
        if (root / "config" / "retention.yaml").exists():
            return load_policy(root / "config")
    raise FileNotFoundError("config/retention.yaml not found")


def add_years(d: date, years: int) -> date:
    """`years` later; 29 February rounds up to 1 March, never down to an earlier day."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return date(d.year + years, 3, 1)


def _day(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def retention_for(policy: dict[str, Any], doc_type: str | None, tax_year: int | None, received: str) -> tuple[str, str | None]:
    """(class, earliest possible deletion date) at intake: N years after receipt plus the margin, or None for 'until
    released'. The actual date depends on filings and on a person's confirmation; see retention_end."""
    cls = (policy.get("by_doc_type") or {}).get(doc_type or "", policy["default"])
    spec = policy["classes"][cls]
    years = spec.get("years")
    if years is None or spec.get("group") == "basis":
        return cls, None
    return cls, (add_years(_day(received), int(years)) + timedelta(days=GRACE_DAYS)).isoformat()


def add_version(conn: Any, document_id: str, locator: str, sha256: str, size: int, actor: str, reason: str = "received") -> int:
    with unit_of_work(conn):
        last = one(conn, "SELECT MAX(version) AS v FROM document_versions WHERE document_id = ?", document_id)
        version = int((last or {}).get("v") or 0) + 1
        conn.execute("INSERT INTO document_versions (document_id, version, locator, sha256, size, created_at, created_by, reason) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (document_id, version, locator, sha256, size, audit.now(), actor, reason))
    return version


def versions(conn: Any, document_id: str) -> list[dict[str, Any]]:
    return rows(conn, "SELECT * FROM document_versions WHERE document_id = ? ORDER BY version", document_id)


def evidence_lock(conn: Any, *clients: str | None) -> None:
    """Serialize everything that decides whether a client's evidence may be deleted (inside a unit_of_work).
    Several clients are locked in one order, so two moves never deadlock."""
    for c in sorted({c for c in clients if c}):
        lock(conn, f"evidence:{c}")


def _has_table(conn: Any, name: str) -> bool:
    if is_pg(conn):
        return True                                           # every table exists once the migrations ran
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone())


# ------------------------------------------------------------------------------------------------- legal holds
def former_clients(conn: Any, document_id: str) -> list[str]:
    """Clients a document was moved away from: their holds still cover it."""
    if not _has_table(conn, "document_moves"):
        return []
    return [r["from_client"] for r in rows(conn, "SELECT DISTINCT from_client FROM document_moves WHERE document_id = ? "
                                                 "ORDER BY from_client", document_id)]


def hold_on(conn: Any, document_id: str, client_id: str | None) -> dict[str, Any] | None:
    """The active hold covering a document: on the document itself, or on its client or any client it belonged to."""
    clients = sorted({c for c in [client_id, *former_clients(conn, document_id)] if c})
    if not clients:
        return one(conn, "SELECT id, client_id, reason FROM legal_holds WHERE released_at IS NULL AND document_id = ? "
                         "ORDER BY id LIMIT 1", document_id)
    marks = ", ".join("?" for _ in clients)
    return one(conn, "SELECT id, client_id, reason FROM legal_holds WHERE released_at IS NULL AND (document_id = ? OR "
                     f"(document_id IS NULL AND client_id IN ({marks}))) ORDER BY id LIMIT 1", document_id, *clients)


def on_hold(conn: Any, document: dict[str, Any]) -> bool:
    return bool(hold_on(conn, document["id"], document.get("client_id")))


def place_hold(conn: Any, *, client_id: str, reason: str, actor: str, role: str, document_id: str | None = None) -> int:
    if role not in REVIEWER:
        raise RetentionError("placing a legal hold needs a CPA")
    if len(reason.strip()) < 10:
        raise RetentionError("a legal hold needs a reason (matter, notice or request)")
    with unit_of_work(conn):
        evidence_lock(conn, client_id)                        # a deletion deciding for this client finishes first
        if document_id is not None:
            d = one(conn, "SELECT client_id FROM documents WHERE id = ?", document_id)
            if not d or (d["client_id"] != client_id and client_id not in former_clients(conn, document_id)):
                raise RetentionError(f"document {document_id} is not a document of {client_id}")
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


def in_flight(conn: Any, client_id: str, document_id: str | None = None) -> list[dict[str, Any]]:
    """Object deletions under way (marker committed, outcome not yet recorded, deadline not passed) for documents of a
    client (or one document): a hold placed now cannot keep those bytes, and says so."""
    now = audit.now()
    out = []
    for r in rows(conn, "SELECT r.deletion_id, r.detail, b.document_id, b.locator, d.client_id FROM blob_deletion_results r "
                        "JOIN blob_deletions b ON b.id = r.deletion_id JOIN deletion_receipts d ON d.id = b.receipt_id "
                        "WHERE r.outcome = 'started' AND NOT EXISTS (SELECT 1 FROM blob_deletion_results f WHERE "
                        "f.deletion_id = r.deletion_id AND f.id > r.id) ORDER BY r.id"):
        if str(r["detail"]) < now:
            continue                                          # its deadline passed: the next run retries under the locks
        if document_id is not None and r["document_id"] != document_id:
            continue
        if r["client_id"] == client_id or client_id in former_clients(conn, r["document_id"]):
            out.append({"document_id": r["document_id"], "locator": r["locator"], "until": r["detail"]})
    return out


def active_holds(conn: Any) -> list[dict[str, Any]]:
    return rows(conn, "SELECT id, client_id, document_id, reason, placed_by, placed_at FROM legal_holds "
                      "WHERE released_at IS NULL ORDER BY id")


# ------------------------------------------------------------------------------------------------- what retention counts from
def _squash(form: str) -> str:
    return re.sub(r"[-\s]", "", form.upper())


_KNOWN = {_squash(f): f for f in INCOME_FORMS | EMPLOYMENT_FORMS}


def normal_form(form: str | None) -> tuple[str, bool]:
    """(base form, amended?) from what a person typed, hyphens and spaces aside: '1040-X' -> ('1040', True),
    '941x' -> ('941', True), '1120S' -> ('1120-S', False)."""
    key = _squash(str(form or "").strip())
    if key in _KNOWN:
        return _KNOWN[key], False
    if key.endswith("X") and key[:-1] in _KNOWN:
        return _KNOWN[key[:-1]], True
    return " ".join(str(form or "").strip().upper().split()), False


def form_group(form: str | None) -> str | None:
    base, _ = normal_form(form)
    if base in INCOME_FORMS:
        return "income"
    if base in EMPLOYMENT_FORMS:
        return "employment"
    return None


def record_tax_event(conn: Any, client_id: str, tax_year: int, kind: str, occurred_on: str, *, actor: str, role: str, note: str,
                     form: str, due_on: str | None = None, period: str | None = None) -> int:
    """Record what retention counts from, for returns not filed through AgentLedger (or payments it does not see),
    with the evidence it rests on (for example an IRS account transcript): a return filed or amended, a payment, a
    year in which no return was required, or the owners' returns of a pass-through entity filed."""
    if role not in REVIEWER:
        raise RetentionError("recording a filing or payment needs a CPA")
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    if len(note.strip()) < 10:
        raise RetentionError("record the evidence (for example 'IRS account transcript shows the return received 2027-04-10')")
    group = form_group(form)
    if group is None:
        raise ValueError(f"{form!r} is not a federal income or employment tax return (an extension, a state or information "
                         "return does not start a limitation period)")
    base, _ = normal_form(form)
    if kind == "owners_filed" and group != "income":
        raise ValueError("owners_filed records the owners' income tax returns (for example 1040)")
    if base in EMPLOYMENT_QUARTERLY:
        if period not in QUARTERS:
            raise ValueError(f"Form {base} is quarterly: give the period (Q1 to Q4)")
    elif period is not None:
        raise ValueError(f"Form {base} is annual: it has no quarter")
    on = _day(occurred_on).isoformat()
    due = _day(due_on).isoformat() if due_on else None
    with unit_of_work(conn):
        evidence_lock(conn, client_id)                        # a retention run deciding for this client sees it, or finished
        c = one(conn, "SELECT kind FROM clients WHERE id = ?", client_id)
        if not c:
            raise KeyError(client_id)
        if base == "SCHEDULE H" and c["kind"] != "individual":
            raise ValueError("Schedule H is a household employer's return (with Form 1040): a business files 941 and 940")
        if kind != "owners_filed" and group == "income":
            if c["kind"] == "individual" and base not in INDIVIDUAL_INCOME_FORMS:
                raise ValueError(f"{client_id} is an individual: Form {base} is not its income tax return")
            if c["kind"] != "individual" and base in INDIVIDUAL_INCOME_FORMS:
                raise ValueError(f"{client_id} is a business: record its owners' Form {base} as owners_filed")
        cur = conn.execute("INSERT INTO tax_year_events (client_id, tax_year, kind, occurred_on, form, period, due_on, note, "
                           "recorded_by, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (client_id, int(tax_year), kind, on, base + ("-X" if normal_form(form)[1] else ""), period, due,
                            note.strip(), actor, audit.now()))
        eid = int(cur.lastrowid or 0)
        audit.record(conn, actor, role, "evidence.tax_year_event",
                     {"event_id": eid, "tax_year": tax_year, "kind": kind, "on": on, "form": form.strip(), "period": period,
                      "due_on": due}, client_id=client_id)
    return eid


def tax_year_events(conn: Any, client_id: str, tax_year: int) -> list[dict[str, Any]]:
    """Every event retention counts from: AgentLedger returns accepted or paper-filed (original and 1040-X), and
    events a CPA recorded. Each carries its group (income or employment)."""
    out: list[dict[str, Any]] = []
    if _has_table(conn, "tax_returns") and _has_table(conn, "workflow_events"):
        for e in rows(conn, "SELECT t.id, t.form, w.event, w.at FROM tax_returns t JOIN workflow_events w ON w.workflow_id = t.id "
                            "WHERE t.client_id = ? AND t.tax_year = ? AND w.event IN ('ack_accepted', 'mark_paper_filed') "
                            "ORDER BY w.at", client_id, int(tax_year)):
            out.append({"kind": "amended" if str(e["form"]).endswith("-X") else "filed", "on": _day(e["at"][:10]),
                        "form": e["form"], "group": "income", "period": None, "source": f"agentledger:{e['id']}",
                        "due_on": None})
    for e in rows(conn, "SELECT * FROM tax_year_events WHERE client_id = ? AND tax_year = ? ORDER BY id", client_id, int(tax_year)):
        out.append({"kind": e["kind"], "on": _day(e["occurred_on"]), "form": e["form"], "group": form_group(e["form"]),
                    "period": e.get("period"), "source": f"recorded:{e['id']}",
                    "due_on": _day(e["due_on"]) if e["due_on"] else None})
    return out


def confirm_retention(conn: Any, document_id: str, *, tax_year: int | None, retention_class: str, actor: str, role: str,
                      note: str) -> dict[str, Any]:
    """A person confirms a document's tax year (or that it has none) and its retention class. Until then the document
    is never deleted. A confirmation can add a year to wait for, never remove one the records show (supported)."""
    if role not in REVIEWER:
        raise RetentionError("confirming retention needs a CPA")
    if len(note.strip()) < 10:
        raise RetentionError("say how the tax year and class were confirmed")
    pol = policy()
    if retention_class not in pol["classes"]:
        raise ValueError(f"unknown retention class {retention_class}")
    with unit_of_work(conn):
        d = one(conn, "SELECT client_id FROM documents WHERE id = ? AND deleted_at IS NULL", document_id)
        if not d:
            raise KeyError(document_id)
        if not d["client_id"]:
            raise RetentionError("file the document to a client first: its retention depends on that client's returns")
        evidence_lock(conn, d["client_id"])                   # serialized with a retention run deciding about it
        d = one(conn, "SELECT * FROM documents WHERE id = ? AND deleted_at IS NULL", document_id)
        if not d or not d["client_id"]:
            raise KeyError(document_id)
        _, earliest = retention_for({**pol, "by_doc_type": {}, "default": retention_class}, d["doc_type"], tax_year, d["received_at"])
        cur = conn.execute("UPDATE documents SET tax_year = ?, retention_class = ?, retain_until = ?, retention_confirmed_by = ?, "
                           "retention_confirmed_at = ? WHERE id = ? AND deleted_at IS NULL AND client_id = ?",
                           (tax_year, retention_class, earliest, actor, audit.now(), document_id, d["client_id"]))
        if cur.rowcount != 1:
            raise KeyError(document_id)
        audit.record(conn, actor, role, "evidence.retention_confirmed",
                     {"document_id": document_id, "tax_year": [d["tax_year"], tax_year],
                      "retention_class": [d["retention_class"], retention_class], "note": note.strip()}, client_id=d["client_id"])
    return one(conn, "SELECT * FROM documents WHERE id = ?", document_id) or {}


def _return_filed(conn: Any, rid: str) -> bool:
    return bool(one(conn, "SELECT 1 AS x FROM workflow_events WHERE workflow_id = ? AND event IN ('ack_accepted', "
                          "'mark_paper_filed')", rid))


def _return_void(conn: Any, rid: str) -> bool:
    """A return a CPA voided (abandoned, or filed with other software: that filing is recorded as a tax-year event)."""
    return bool(one(conn, "SELECT 1 AS x FROM workflow_events WHERE workflow_id = ? AND event = 'void'", rid))


def supported(conn: Any, d: dict[str, Any]) -> tuple[dict[tuple[str, int], list[str]], list[str]]:
    """((client, tax year) -> why) every year a document supports, and the reasons it may not be deleted at all (a
    return that relied on it is not filed)."""
    pairs: dict[tuple[str, int], list[str]] = {}
    blockers: list[str] = []
    if d.get("tax_year") and d.get("client_id"):
        pairs.setdefault((d["client_id"], int(d["tax_year"])), []).append("its confirmed tax year")
    if _has_table(conn, "return_document_uses"):
        for r in rows(conn, "SELECT t.id, t.client_id, t.tax_year FROM return_document_uses u JOIN tax_returns t ON t.id = u.return_id "
                            "WHERE u.document_id = ? ORDER BY t.id", d["id"]):
            pairs.setdefault((r["client_id"], int(r["tax_year"])), []).append(f"return {r['id']} relied on it")
            if not _return_filed(conn, r["id"]) and not _return_void(conn, r["id"]):
                blockers.append(f"return {r['id']} ({r['tax_year']}), which relied on it, is not filed (file it, or void it "
                                "and record where it was filed)")
    for r in rows(conn, "SELECT id, client_id, date FROM entries WHERE document_id = ? UNION "
                        "SELECT e.id, e.client_id, e.date FROM entry_documents x JOIN entries e ON e.id = x.entry_id "
                        "WHERE x.document_id = ?", d["id"], d["id"]):
        pairs.setdefault((r["client_id"], int(str(r["date"])[:4])), []).append(f"journal entry {r['id']}")
    if _has_table(conn, "return_retention_facts"):            # a carryover keeps the years it comes from open
        for client in sorted({c for c, _ in pairs}):
            first = min(y for c, y in pairs if c == client)
            for r in rows(conn, "SELECT DISTINCT t.tax_year FROM return_retention_facts k JOIN tax_returns t ON t.id = k.return_id "
                                "WHERE k.kind = 'carryover' AND t.client_id = ? AND t.tax_year > ? ORDER BY t.tax_year",
                          client, first):
                pairs.setdefault((client, int(r["tax_year"])), []).append(f"a carryover is used in {r['tax_year']}")
    return pairs, blockers


def _basis_record(conn: Any, d: dict[str, Any]) -> str | None:
    """A document behind a capitalized cost (a debit to an asset account other than cash or receivables: equipment,
    property, inventory, investments) supports basis until the property is disposed of."""
    for r in rows(conn, "SELECT a.name, p.amount FROM entries e JOIN postings p ON p.entry_id = e.id "
                        "JOIN accounts a ON a.client_id = e.client_id AND a.code = p.account_code "
                        "WHERE a.type = 'asset' AND (e.document_id = ? OR e.id IN (SELECT entry_id FROM entry_documents "
                        "WHERE document_id = ?))", d["id"], d["id"]):
        amount = str(r["amount"]).strip()
        if not amount.startswith("-") and amount.strip("0.") != "" and not CASH_ACCOUNT.search(str(r["name"])):
            return str(r["name"])
    return None


def _pass_through(conn: Any, client_id: str, events: list[dict[str, Any]] | None = None) -> bool:
    """Whether the client's income is taxed to its owners: decided by the return it files for the year (1065, 1120-S,
    1041: yes; a C corporation's 1120: no), else by its entity type (any spelling of a C corporation or exempt
    organization: no; anything else, unknown included: yes, the safe direction)."""
    c = one(conn, "SELECT kind, entity_type FROM clients WHERE id = ?", client_id)
    if not c or c["kind"] == "individual":
        return False
    forms = {normal_form(e["form"])[0] for e in (events or []) if e["group"] == "income" and e["kind"] in ("filed", "amended")}
    if forms & PASS_THROUGH_FORMS:
        return True
    if forms & C_CORPORATION_FORMS:
        return False
    kind = re.sub(r"[^a-z0-9]", "", str(c["entity_type"] or "").lower())
    return kind not in {"ccorp", "ccorporation", "corporation", "corp", "nonprofit", "exempt", "taxexempt", "501c3"}


def _foreign_tax(conn: Any, client_id: str, year: int) -> bool:
    """The year shows foreign tax (1099-INT box 6, 1099-DIV box 7, under any spelling the documents use; on the year's
    documents and on those its returns relied on), or a return of the year shows foreign tax or claims the credit: a
    credit may be claimed for 10 years."""
    from ..returns.documents import canonical

    if _has_table(conn, "return_retention_facts") and one(
            conn, "SELECT 1 AS x FROM return_retention_facts k JOIN tax_returns t ON t.id = k.return_id "
                  "WHERE k.kind = 'foreign_tax' AND t.client_id = ? AND t.tax_year = ?", client_id, int(year)):
        return True
    docs = rows(conn, "SELECT id, doc_type, fields FROM documents WHERE client_id = ? AND tax_year = ? AND doc_type IN "
                      "('1099-INT', '1099-DIV')", client_id, int(year))
    if _has_table(conn, "return_document_uses"):
        docs += rows(conn, "SELECT d.id, d.doc_type, d.fields FROM return_document_uses u JOIN tax_returns t ON t.id = u.return_id "
                           "JOIN documents d ON d.id = u.document_id WHERE t.client_id = ? AND t.tax_year = ? AND d.doc_type "
                           "IN ('1099-INT', '1099-DIV')", client_id, int(year))
    for r in docs:
        box = "box6" if r["doc_type"] == "1099-INT" else "box7"
        fields = json.loads(r["fields"] or "{}")
        values = [v for k, v in {**fields, **(fields.get("_unverified") or {})}.items()
                  if k != "_unverified" and canonical(r["doc_type"], k) == box]
        for value in values:
            if str(value).strip() == "":
                continue
            try:
                if float(str(value).replace(",", "").replace("$", "")) > 0:
                    return True
            except ValueError:
                return True                                   # unreadable: it may be foreign tax
    return False


def _employment_filed(events: list[dict[str, Any]], year: int) -> str | None:
    """None when the year's employment tax returns are all on record; otherwise what is missing."""
    filed = [e for e in events if e["group"] == "employment" and e["kind"] in FILING_KINDS]
    returned = [e for e in filed if e["kind"] in ("filed", "amended")]
    annual = any(normal_form(e["form"])[0] in EMPLOYMENT_ANNUAL for e in returned)    # an annual form not required
    quarters = {e.get("period") for e in filed if normal_form(e["form"])[0] in EMPLOYMENT_QUARTERLY}   # says nothing of 941s
    if not annual and not set(QUARTERS) <= quarters:
        missing = ", ".join(q for q in QUARTERS if q not in quarters)
        return f"the {year} employment tax returns are not all on record (no annual return filed; no 941 for {missing})"
    futa = any(normal_form(e["form"])[0] == "940" for e in filed) or any(
        normal_form(e["form"])[0] in ("SCHEDULE H", "CT-1") for e in returned)
    if not futa:
        return f"no {year} Form 940 (federal unemployment tax) is on record as filed or not required"
    return None


def _pair_end(conn: Any, client: str, year: int, group: str, years: int) -> tuple[date | None, str]:
    """When the evidence one (client, tax year) needs may go: the latest of its filing-based periods."""
    events = tax_year_events(conn, client, year)
    has_return = _has_table(conn, "tax_returns") and any(
        not _return_void(conn, r["id"]) for r in rows(conn, "SELECT id FROM tax_returns WHERE client_id = ? AND tax_year = ?",
                                                      client, int(year)))
    income = [e for e in events if e["group"] == "income" and e["kind"] in FILING_KINDS
              and not (e["kind"] == "not_required" and has_return)]
    if not income:
        return None, (f"no {year} income tax return of {client} is on record as filed or not required: no limitation "
                      "period runs (IRC 6501(c)(3))")
    # Without a recorded due date, 15 April of the next year (a fiscal-year business records its due date, due_on).
    due = max([e["due_on"] for e in income if e["due_on"]] + [date(year + 1, 4, 15)])
    n_income = max(years, TAX_RECORD_YEARS)
    deemed = max(max(e["on"] for e in income), due)
    end = add_years(deemed, n_income)                                       # 6501(b)(1): early returns count from the due date
    if _foreign_tax(conn, client, year):                                    # 6511(d)(3)(A), from the later of the two
        end = max(end, add_years(deemed, FOREIGN_TAX_YEARS))
    payments = [e["on"] for e in events if e["kind"] == "payment" and e["group"] == "income"]
    if payments:
        end = max(end, add_years(max(payments), 2))                           # 6511(a)
    owners = [e["on"] for e in events if e["kind"] == "owners_filed"]
    if _pass_through(conn, client, events) and not owners:
        return None, (f"{client} passes its {year} income through to its owners, whose returns are not on record as "
                      "filed (record them as owners_filed)")
    if owners:                                                              # recorded: always counted
        end = max(end, add_years(max(max(owners), due), n_income))
    if group == "employment":
        missing = _employment_filed(events, year)
        if missing:
            return None, f"{missing}: no limitation period runs"
        filed = [e for e in events if e["group"] == "employment" and e["kind"] in FILING_KINDS]
        due_e = max([e["due_on"] for e in filed if e["due_on"]] + [date(year + 1, 4, 15)])   # 6501(b)(2)
        end = max(end, add_years(max(max(e["on"] for e in filed), due_e), years))
        paid = [e["on"] for e in events if e["kind"] == "payment" and e["group"] == "employment"]
        if paid:
            end = max(end, add_years(max(paid), 4))                           # Treas. Reg. 31.6001-1(e)(2)
    return end, "ok"


def uses_indexed(conn: Any) -> bool:
    """Whether every return's relied-on documents are indexed (returns.store backfills returns saved before the
    index existed): until then a document's years cannot all be known, and no retention run starts."""
    if not _has_table(conn, "tax_returns") or not one(conn, "SELECT 1 AS x FROM tax_returns LIMIT 1"):
        return True
    return bool(one(conn, "SELECT 1 AS x FROM kv WHERE key = ?", USES_INDEXED))


def retention_end(conn: Any, pol: dict[str, Any], d: dict[str, Any]) -> tuple[date | None, str]:
    """The day after which the document may be deleted, or None with the reason it may not (yet)."""
    cls = d.get("retention_class") or pol["default"]
    spec = pol["classes"].get(cls)
    if spec is None:
        return None, f"unknown retention class {cls}"
    group = spec.get("group") or _default_group(cls)
    release = basis_release(conn, d["id"])
    if (group == "basis" or spec.get("years") is None) and not release:
        return None, f"class {cls} is kept until a CPA releases it"
    if not d.get("retention_confirmed_at"):
        return None, "a person has not confirmed its tax year and retention class"
    asset = _basis_record(conn, d)
    if asset and not release:
        return None, f"it supports a capitalized cost ({asset}): basis records are kept until a CPA releases them"
    pairs, blockers = supported(conn, d)
    if release:                                   # kept as a tax record of the year the property was disposed of
        pairs.setdefault((d["client_id"], int(release["disposed_tax_year"])), []).append(
            f"basis released: property disposed of in {release['disposed_tax_year']}")
        group = "income" if group in ("basis", "firm") else group
    if blockers:
        return None, blockers[0]
    years = int(spec["years"]) if spec.get("years") is not None else TAX_RECORD_YEARS
    end = add_years(_day(d["received_at"]), years)
    for (client, year), why in sorted(pairs.items()):
        pair_end, reason = _pair_end(conn, client, year, group, years)
        if pair_end is None:
            return None, f"{reason} ({'; '.join(why)})"
        end = max(end, pair_end)
    return end + timedelta(days=GRACE_DAYS), "ok"


def basis_release(conn: Any, document_id: str) -> dict[str, Any] | None:
    if not _has_table(conn, "basis_releases"):
        return None
    return one(conn, "SELECT * FROM basis_releases WHERE document_id = ? ORDER BY id DESC LIMIT 1", document_id)


def release_basis(conn: Any, document_id: str, *, disposed_tax_year: int, actor: str, role: str, note: str) -> int:
    """A CPA records that the property a basis record supports was disposed of in `disposed_tax_year`: from then on
    the record is kept as a tax record of that year (the disposition year's returns on record, its period run), never
    deleted on the release alone."""
    if role not in REVIEWER:
        raise RetentionError("releasing a basis record needs a CPA")
    if len(note.strip()) < 10:
        raise RetentionError("record the disposition (for example 'sold 2031-06-30, closing statement on file')")
    with unit_of_work(conn):
        d = one(conn, "SELECT client_id FROM documents WHERE id = ? AND deleted_at IS NULL", document_id)
        if not d or not d["client_id"]:
            raise KeyError(document_id)
        evidence_lock(conn, d["client_id"])
        cur = conn.execute("INSERT INTO basis_releases (document_id, disposed_tax_year, note, released_by, released_at) "
                           "VALUES (?, ?, ?, ?, ?)", (document_id, int(disposed_tax_year), note.strip(), actor, audit.now()))
        rid = int(cur.lastrowid or 0)
        audit.record(conn, actor, role, "evidence.basis_released", {"document_id": document_id,
                                                                    "disposed_tax_year": int(disposed_tax_year),
                                                                    "note": note.strip()}, client_id=d["client_id"])
    return rid


def due_for_deletion(conn: Any, today: date) -> list[dict[str, Any]]:
    """Documents a retention run would delete today, each with its computed retention end."""
    if not uses_indexed(conn):
        raise RetentionError("the documents relied on by returns saved before this version are not indexed yet: open the "
                             "firm's returns once (returns.store indexes them) before a retention run")
    pol = policy()
    out = []
    for d in rows(conn, "SELECT * FROM documents WHERE deleted_at IS NULL AND status = 'filed' AND client_id IS NOT NULL ORDER BY id"):
        if on_hold(conn, d):
            continue
        end, _ = retention_end(conn, pol, d)
        if end is not None and end < today:
            out.append({**d, "retention_end": end.isoformat()})
    return out


def explain(conn: Any, document_id: str) -> dict[str, Any]:
    """For one document: its computed retention end (or why there is none yet), any hold, and the years it supports."""
    d = one(conn, "SELECT * FROM documents WHERE id = ?", document_id)
    if not d:
        raise KeyError(document_id)
    if d["deleted_at"]:
        return {"document_id": document_id, "deleted_at": d["deleted_at"], "retention_end": None, "reason": "deleted"}
    end, reason = retention_end(conn, policy(), d)
    pairs, _ = supported(conn, d)
    return {"document_id": document_id, "retention_class": d["retention_class"], "tax_year": d["tax_year"],
            "retention_confirmed_at": d["retention_confirmed_at"], "retention_end": end.isoformat() if end else None,
            "reason": reason, "hold": hold_on(conn, document_id, d["client_id"]),
            "supports": [{"client_id": c, "tax_year": y, "why": w} for (c, y), w in sorted(pairs.items())]}


# ------------------------------------------------------------------------------------------------- deletion
class Receipts(list):
    """The receipts of a retention run; `failures` lists what it could not finish (nothing destroyed without a receipt)."""

    def __init__(self) -> None:
        super().__init__()
        self.failures: list[dict[str, Any]] = []


def _referencing(conn: Any, locator: str) -> list[dict[str, Any]]:
    """Every document, live or deleted, that references a stored object."""
    return rows(conn, "SELECT DISTINCT d.id, d.client_id, d.deleted_at FROM documents d WHERE d.vault_path = ? "
                      "OR d.id IN (SELECT document_id FROM document_versions WHERE locator = ?) "
                      "OR d.id IN (SELECT document_id FROM blob_deletions WHERE locator = ?) ORDER BY d.id",
                locator, locator, locator)


def _outcome(conn: Any, deletion_id: int, outcome: str, detail: str = "") -> None:
    conn.execute("INSERT INTO blob_deletion_results (deletion_id, outcome, detail, at) VALUES (?, ?, ?, ?)",
                 (deletion_id, outcome, detail[:500], audit.now()))


def _clients_of(conn: Any, p: dict[str, Any], refs: list[dict[str, Any]]) -> set[str]:
    clients = {p["client_id"], *(r["client_id"] for r in refs if r["client_id"])}
    for r in refs:
        clients |= set(former_clients(conn, r["id"]))
    return {c for c in clients if c}


def _delete_object(conn: Any, vault: Any, p: dict[str, Any]) -> dict[str, Any] | None:
    """Delete one object whose deletion is committed, unless a hold now covers any document that references it (it
    stays pending) or a retained document references it (objects stored before per-document addressing may be
    shared). The checks and an in-flight marker with a deadline commit under the evidence locks of every client
    involved; storage is called after that, with no lock or transaction open; the outcome is recorded last."""
    refs = _referencing(conn, p["locator"])
    clients = _clients_of(conn, p, refs)
    with unit_of_work(conn):
        evidence_lock(conn, *clients)
        refs = _referencing(conn, p["locator"])
        if not _clients_of(conn, p, refs) <= clients:         # moved meanwhile: the next run takes the right locks
            return {"document_id": p["document_id"], "locator": p["locator"], "stage": "retry",
                    "error": "the documents referencing this object changed hands during the run; retried next run"}
        last = one(conn, "SELECT outcome, detail FROM blob_deletion_results WHERE deletion_id = ? ORDER BY id DESC LIMIT 1", p["id"])
        if last and last["outcome"] == "started" and str(last["detail"]) >= audit.now():
            # Another run's delete is under way: whatever holds exist now, nothing can say these bytes are kept.
            return {"document_id": p["document_id"], "locator": p["locator"], "stage": "in_flight",
                    "error": f"another run is deleting this object (until {last['detail']}); a hold placed meanwhile "
                             "cannot keep it"}
        for r in refs:
            hold = hold_on(conn, r["id"], r["client_id"])
            if hold:
                present = vault.exists(p["locator"])
                return {"document_id": p["document_id"], "locator": p["locator"], "stage": "held", "bytes_present": present,
                        "error": (f"legal hold #{hold['id']} covers document {r['id']}: "
                                  + ("the bytes are kept until it is released" if present else
                                     "the bytes are no longer stored (an earlier attempt removed them)"))}
        if any(r["deleted_at"] is None for r in refs):         # a retained document holds the same bytes
            _outcome(conn, p["id"], "kept_shared", "still referenced by a retained document")
            return None
        deadline = (datetime.fromisoformat(audit.now()) + IN_FLIGHT).isoformat(timespec="seconds")
        _outcome(conn, p["id"], "started", deadline)
    vault.delete(p["locator"])                                # no lock held: a dropped session loses nothing it protects
    with unit_of_work(conn):
        _outcome(conn, p["id"], "deleted")
    return None


def _delete_blobs(conn: Any, vault: Any, pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Phase 2: delete each object whose deletion is committed, recording every outcome. Idempotent: a retry of an
    object already gone succeeds."""
    failures = []
    for p in pending:
        try:
            held = _delete_object(conn, vault, p)
            if held:
                failures.append(held)
        except Exception as exc:                                  # recorded; the receipt stands and the next run retries
            detail = f"{type(exc).__name__}: {exc}"
            failures.append({"document_id": p["document_id"], "locator": p["locator"], "stage": "storage", "error": detail[:300]})
            try:
                _outcome(conn, p["id"], "failed", detail)
            except Exception:                                     # the outcome itself could not be written: still pending
                pass
    return failures


def pending_deletions(conn: Any) -> list[dict[str, Any]]:
    return rows(conn, "SELECT b.*, r.client_id FROM blob_deletions b JOIN deletion_receipts r ON r.id = b.receipt_id "
                      "WHERE NOT EXISTS (SELECT 1 FROM blob_deletion_results x "
                      "WHERE x.deletion_id = b.id AND x.outcome IN ('deleted', 'kept_shared')) ORDER BY b.id")


def complete_pending(conn: Any, vault: Any) -> list[dict[str, Any]]:
    """Finish object deletions an earlier run committed but could not complete."""
    return _delete_blobs(conn, vault, pending_deletions(conn))


def _commit_deletion(conn: Any, pol: dict[str, Any], doc_id: str, today: date, *, actor: str, role: str,
                     reason: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Phase 1, one transaction: re-check everything, tombstone the document, write the receipt, the audit record and
    the pending object deletions. Nothing outside the database is touched."""
    first = one(conn, "SELECT client_id FROM documents WHERE id = ?", doc_id)
    if not first or not first["client_id"]:
        return None, []
    with unit_of_work(conn):
        if is_pg(conn):
            conn.execute("SET LOCAL synchronous_commit = on")      # the receipt is durable before any byte is deleted
        locked = {first["client_id"], *former_clients(conn, doc_id)}
        evidence_lock(conn, *locked)
        d = one(conn, "SELECT * FROM documents WHERE id = ?" + (" FOR UPDATE" if is_pg(conn) else ""), doc_id)
        if (not d or d["client_id"] != first["client_id"] or d["deleted_at"] or d["status"] != "filed"
                or not {d["client_id"], *former_clients(conn, doc_id)} <= locked or on_hold(conn, d)):
            return None, []                                       # moved, deleted or held meanwhile: the next run decides
        end, _ = retention_end(conn, pol, d)
        if end is None or not end < today:
            return None, []
        locs = sorted({v["locator"] for v in versions(conn, doc_id)} | ({d["vault_path"]} if d["vault_path"] else set()))
        now = audit.now()
        cur = conn.execute("UPDATE documents SET deleted_at = ?, sha256 = ?, fields = '{}', summary = NULL, text_excerpt = NULL "
                           "WHERE id = ? AND deleted_at IS NULL AND client_id = ?",
                           (now, f"deleted:{doc_id}:{d['sha256']}", doc_id, d["client_id"]))
        if cur.rowcount != 1:                                     # another run got here first
            return None, []
        rc = conn.execute("INSERT INTO deletion_receipts (document_id, client_id, sha256, locators, retention_class, retain_until, "
                          "deleted_by, deleted_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (doc_id, d["client_id"], d["sha256"], ",".join(locs), d["retention_class"], end.isoformat(),
                           actor, now, reason.strip()))
        receipt_id = int(rc.lastrowid or 0)
        pending = []
        for loc in locs:
            b = conn.execute("INSERT INTO blob_deletions (receipt_id, document_id, locator, created_at) VALUES (?, ?, ?, ?)",
                             (receipt_id, doc_id, loc, now))
            pending.append({"id": int(b.lastrowid or 0), "document_id": doc_id, "client_id": d["client_id"], "locator": loc})
        receipt = {"receipt_id": receipt_id, "document_id": doc_id, "sha256": d["sha256"], "retention_class": d["retention_class"],
                   "retain_until": end.isoformat(), "objects": len(locs), "attestation": ATTESTATION}
        audit.record(conn, actor, role, "evidence.deleted", receipt, client_id=d["client_id"])
    return receipt, pending


def purge_expired(conn: Any, vault: Any, today: date, *, actor: str, role: str, reason: str, attested: bool = False,
                  document_ids: list[str] | None = None) -> Receipts:
    """Delete evidence whose retention has ended (CPA only), after the CPA attests ATTESTATION. With `document_ids`
    only those (the list the CPA reviewed) are considered. Returns the receipts; `.failures` lists documents whose
    deletion could not be recorded (nothing of theirs was destroyed) or whose objects are not deleted yet (their
    receipts stand; the next run retries)."""
    from ..db import _raw

    if role not in REVIEWER:
        raise RetentionError("deleting evidence needs a CPA")
    if len(reason.strip()) < 10:
        raise RetentionError("record why this deletion run happens (for example the annual retention review)")
    if not attested:
        raise RetentionError("a retention run needs the CPA's attestation: " + ATTESTATION)
    if _raw(conn).in_transaction:
        raise RetentionError("a retention run commits each receipt before deleting bytes: run it outside any open transaction")
    if is_pg(conn) and str(conn.execute("SELECT current_setting('agentledger.clients', true)").fetchone()[0]) != "*":
        raise RetentionError("retention runs are firm-wide: a client-scoped session cannot see every document sharing evidence")
    out = Receipts()
    out.failures += complete_pending(conn, vault)
    pol = policy()
    wanted = set(document_ids) if document_ids is not None else None
    for d in due_for_deletion(conn, today):
        if wanted is not None and d["id"] not in wanted:
            continue
        try:
            receipt, pending = _commit_deletion(conn, pol, d["id"], today, actor=actor, role=role, reason=reason)
        except Exception as exc:                                  # rolled back: nothing was destroyed
            out.failures.append({"document_id": d["id"], "stage": "record", "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        if receipt is None:
            continue
        out.append(receipt)
        out.failures += _delete_blobs(conn, vault, pending)
    return out


def integrity(conn: Any, vault: Any, *, sweep: bool = True, limit: int = 1000, verify_contents: bool = False) -> dict[str, Any]:
    """Evidence that should exist but does not, deletions not finished, and (with `sweep`) stored objects that should
    be gone (recorded as deleted, still stored) or that no record references (an intake that failed half way). With
    `verify_contents` every live object is read back and authenticated against its recorded hash (replaced, swapped
    or unsealed bytes are listed in `failed_authentication`)."""
    live: set[str] = set()
    referenced: set[str] = set()
    missing = []
    tampered = []
    for d in rows(conn, "SELECT id, client_id, vault_path, deleted_at, sha256 FROM documents ORDER BY id"):
        vers = versions(conn, d["id"])
        locs = {v["locator"] for v in vers} | ({d["vault_path"]} if d["vault_path"] else set())
        referenced |= locs
        if d["deleted_at"]:
            continue
        live |= locs
        gone = [loc for loc in sorted(locs) if not vault.exists(loc)]
        if gone:
            missing.append({"document_id": d["id"], "client_id": d["client_id"], "missing": gone})
        if verify_contents:
            expected = {v["locator"]: v["sha256"] for v in vers if v.get("size")} | (
                {d["vault_path"]: d["sha256"]} if d["vault_path"] else {})
            for loc, sha in sorted(expected.items()):
                if loc in gone:
                    continue
                try:
                    vault.read(loc, sha256=sha)
                except Exception as exc:
                    tampered.append({"document_id": d["id"], "locator": loc, "error": f"{type(exc).__name__}: {exc}"[:200]})
    pending = []
    for p in pending_deletions(conn):
        refs = _referencing(conn, p["locator"])
        pending.append({**p, "held": any(hold_on(conn, r["id"], r["client_id"]) for r in refs),
                        "bytes_present": vault.exists(p["locator"])})
    failed = rows(conn, "SELECT r.*, b.document_id, b.locator FROM blob_deletion_results r JOIN blob_deletions b ON b.id = r.deletion_id "
                        "WHERE r.outcome = 'failed' ORDER BY r.id DESC LIMIT 200")
    deleted = {r["locator"] for r in rows(conn, "SELECT b.locator FROM blob_deletions b JOIN blob_deletion_results x "
                                                "ON x.deletion_id = b.id WHERE x.outcome = 'deleted'")}
    survived: list[str] = []
    unreferenced: list[str] = []
    swept = False
    if sweep and hasattr(getattr(vault, "blobs", None), "keys"):
        try:
            pending_locs = {p["locator"] for p in pending}
            for key in vault.blobs.keys():
                loc = "blob:" + str(key)
                if loc in live or loc in pending_locs:
                    continue
                (survived if loc in deleted else unreferenced if loc not in referenced else survived).append(loc)
                if len(survived) + len(unreferenced) >= limit:
                    break
            root = getattr(vault, "root", None)
            if root is not None and Path(root).exists():         # documents filed before content addressing
                blobs_dir = Path(root) / "blobs"
                for f in sorted(Path(root).rglob("*")):
                    if not f.is_file() or blobs_dir in f.parents:
                        continue
                    loc = f.relative_to(root).as_posix()
                    if loc in live or loc in pending_locs:
                        continue
                    (survived if loc in deleted or loc in referenced else unreferenced).append(loc)
            swept = True
        except Exception:                                         # the store cannot be listed: reported, not hidden
            swept = False
    return {"ok": not missing and not pending and not survived and not tampered and not unreferenced,
            "live_documents_missing_bytes": missing,
            "pending_deletions": pending, "recent_failed_attempts": failed, "storage_swept": swept,
            "deleted_but_still_stored": survived, "unreferenced_objects": unreferenced,
            "contents_verified": verify_contents, "failed_authentication": tampered}


def to_json(summary: dict[str, Any] | None) -> str:
    return json.dumps(summary, default=str, sort_keys=True)
