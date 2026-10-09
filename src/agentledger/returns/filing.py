"""Federal and state submissions of a return (backlog F-08, ADR-0003; spec §9, Q03, Q23, Q24).

The return stays the aggregate: its event stream carries the federal filing as it always has (activity_started:
transmit, transmit, ack_accepted, outcome_unknown, reconciled_*: evidence retention and the paper-filing guards read
it there). Each electronic submission to one jurisdiction has, in addition, its own row in `filing_submissions`
and its own event stream keyed by the submission id. The federal submission is written together with the return's
events in one transaction: one write path, two readers. State submissions have only their own stream; a state
rejection after the federal acceptance leaves the return `accepted` and the state submission `rejected`, with a task
for a reviewer.

Who does what (the roles the API maps people to):
- a CPA approves the release (`Returns.approve_release`), reconciles an unknown transmission, retransmits a rejected
  state submission, voids; a CPA may also transmit directly, which approves the release in the same act;
- the workflow ("system") transmits each queued submission once the release is approved, looks a lost answer up by
  the planned submission id, polls and records acknowledgements, marks an answer unknown and asks a person for help
  (`notify_operator`). It never approves, releases, reconciles or resends.

Every system command is an "ensure": it reads the submission first and returns the recorded outcome when the
transition already happened, so a retried step converges without a second effect. The transmission itself is the
engine's two-phase activity: a crash between sending and recording leaves a started marker, and the next attempt
reconciles (by the planned submission id) or stops in `unknown` for a person; it never sends again on its own.

The transmitter adapter (`Provider`) is the contract backlog T2-02 implements for MeF A2A; `MockProvider` stands in
for it in tests and the local demo.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from .. import audit
from ..crm import core as crm
from ..db import lock, one, rows, unit_of_work
from ..workflow import outbox
from ..workflow.commands import Command, Context, Spec, register
from ..workflow.engine import Definition, State, Transition, TransitionError, UncertainOutcome

FEDERAL = "US-FED"
KIND = "filing_submission"
# A submission that may still change or be filed; superseded, cancelled, rejected and accepted ones are history.
ACTIVE = ("queued", "transmitted", "unknown")
# What the return stream also records for the federal submission (see module doc).
MIRRORED = ("transmit", "outcome_unknown", "reconciled_submitted", "reconciled_not_submitted", "ack_accepted", "ack_rejected")
AUTHORITY = ("cpa",)
SYSTEM = "system"


class Provider(Protocol):
    """The transmitter (backlog T2-02 implements it for MeF A2A). `submission_id` is the transmitter-assigned
    SubmissionId AgentLedger plans before sending, so a lost answer can be looked up by it."""

    def submit(self, package: dict[str, Any], submission_id: str) -> dict[str, Any]: ...
    def lookup(self, submission_id: str) -> dict[str, Any] | None: ...
    def acknowledgement(self, submission_id: str) -> dict[str, Any] | None: ...


SUBMISSION = Definition(
    kind=KIND,
    initial="queued",
    transitions=[
        Transition("transmit", ("queued",), "transmitted", roles=("cpa", SYSTEM)),
        Transition("outcome_unknown", ("queued",), "unknown"),
        # Reconciled by the provider's own record (lookup by the planned id) or by a person with evidence.
        Transition("reconciled_submitted", ("unknown",), "transmitted"),
        # Never received: back in the queue under a new planned id; the old one is never reused.
        Transition("reconciled_not_submitted", ("unknown",), "queued", roles=AUTHORITY),
        Transition("ack_accepted", ("transmitted",), "accepted"),
        Transition("ack_rejected", ("transmitted",), "rejected"),
        # A rejected state submission is replaced by a retransmission (a new row); a person decides.
        Transition("retransmit", ("rejected",), "superseded", roles=AUTHORITY),
        Transition("cancel", ("queued",), "cancelled"),
    ],
    waiting={"queued": "transmission", "transmitted": "acknowledgement", "unknown": "reconciliation with the transmitter"},
)


def jurisdictions_of(names: list[str] | None) -> list[str]:
    """Validated, deduplicated, federal first. US-FED is always part of a release (the state returns of a Form 1040
    follow the federal one; a state-only filing is backlog T2-05); US-FED alone when none are named."""
    out: list[str] = [FEDERAL]
    for j in names or []:
        j = str(j).strip().upper()
        if j != FEDERAL and not (len(j) == 5 and j.startswith("US-") and j[3:].isalpha()):
            raise ValueError(f"unknown jurisdiction {j!r}: US-FED or US-XX")
        if j not in out:
            out.append(j)
    return sorted(out, key=lambda j: (j != FEDERAL, j))


def coverage_id(jurisdiction: str) -> str:
    """The coverage registry capability a jurisdiction's filing depends on (coverage/coverage.yaml)."""
    return "mef_1040" if jurisdiction == FEDERAL else f"state_{jurisdiction[3:].lower()}"


# ------------------------------------------------------------------------------------------------- the mock transmitter
class MockProvider:
    """Stands in for the transmitter: remembers what it received by submission id and acknowledges on request.
    Tests and the local demo only; it never files anything. Faults are armed per call: "timeout" (received, no
    answer), "down" (nothing received)."""

    def __init__(self, *, ack_after: int = 1, outcome: str = "accepted", codes: list[str] | None = None):
        self.received: dict[str, dict[str, Any]] = {}
        self.calls = {"submit": 0, "lookup": 0, "acknowledgement": 0}
        self.faults: list[str] = []
        self.ack_after, self.outcome, self.codes = ack_after, outcome, list(codes or [])
        self.decisions: dict[str, tuple[str, list[str]]] = {}

    def arm(self, fault: str) -> None:
        self.faults.append(fault)

    def decide(self, submission_id: str, outcome: str, codes: list[str] | None = None) -> None:
        self.decisions[submission_id] = (outcome, list(codes or []))

    def submit(self, package: dict[str, Any], submission_id: str) -> dict[str, Any]:
        self.calls["submit"] += 1
        fault = self.faults.pop(0) if self.faults else None
        if fault == "down":
            raise ConnectionError("the transmitter is unreachable")
        if submission_id in self.received:
            raise ValueError(f"duplicate SubmissionId {submission_id}")
        self.received[submission_id] = {"polls": 0, "receipt": f"mock-{len(self.received) + 1}"}
        if fault == "timeout":
            raise TimeoutError("the transmitter did not answer")
        return {"submission_id": submission_id, "receipt": self.received[submission_id]["receipt"]}

    def lookup(self, submission_id: str) -> dict[str, Any] | None:
        self.calls["lookup"] += 1
        r = self.received.get(submission_id)
        return {"submission_id": submission_id, "receipt": r["receipt"]} if r else None

    def acknowledgement(self, submission_id: str) -> dict[str, Any] | None:
        self.calls["acknowledgement"] += 1
        r = self.received.get(submission_id)
        if r is None:
            return None
        r["polls"] += 1
        if r["polls"] < self.ack_after:
            return None
        outcome, codes = self.decisions.get(submission_id, (self.outcome, self.codes))
        return {"status": outcome, "codes": codes, "payload": {"submission_id": submission_id, "status": outcome, "codes": codes}}


def provider_from_env() -> Any:
    """The configured transmitter. Only the mock exists today (T2-02 brings MeF), and only for the local demo
    (AGENTLEDGER_DEV_AUTH=1): a mock that "accepts" returns must never run where a filing is real."""
    name = os.environ.get("AGENTLEDGER_MEF_PROVIDER", "").strip().lower()
    if name == "mock" and os.environ.get("AGENTLEDGER_DEV_AUTH") == "1":
        return MockProvider()
    return None


# ------------------------------------------------------------------------------------------------- submissions
class Filing:
    """Submissions of one firm's returns. Built on a `Returns` (returns/store.py): the return's stream, the sealer
    and the review checks are its."""

    def __init__(self, returns: Any, *, clock: Callable[[], datetime] | None = None):
        self.R = returns
        self.conn = returns.conn
        self.wf = returns.wf
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # ---------------------------------------------------------------- rows
    def get(self, sub_id: str) -> dict[str, Any]:
        r = one(self.conn, "SELECT * FROM filing_submissions WHERE id = ?", sub_id)
        if not r:
            raise KeyError(f"no submission {sub_id}")
        r["rejection_codes"] = json.loads(r["rejection_codes"] or "[]")
        return r

    def for_return(self, rid: str) -> list[dict[str, Any]]:
        out = rows(self.conn, "SELECT * FROM filing_submissions WHERE return_id = ? ORDER BY created_at, id", rid)
        for r in out:
            r["rejection_codes"] = json.loads(r["rejection_codes"] or "[]")
        return out

    def client_of(self, sub_id: str) -> str:
        return str(self.R.get(self.get(sub_id)["return_id"])["client_id"])

    def _now(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    def _update(self, sub_id: str, **cols: Any) -> None:
        cols["updated_at"] = self._now()
        sets = ", ".join(f"{k} = ?" for k in cols)
        self.conn.execute(f"UPDATE filing_submissions SET {sets} WHERE id = ?", (*cols.values(), sub_id))

    def _planned_id(self) -> str:
        """The next MeF-style SubmissionId (EFIN, year, day of year, 7-digit sequence): planned before sending so a
        lost answer can be looked up by it. Sequence numbers are never reused."""
        lock(self.conn, "filing:submission_seq")
        row = one(self.conn, "SELECT value FROM kv WHERE key = 'filing.submission_seq'")
        n = int(row["value"]) + 1 if row else 1
        self.conn.execute("INSERT INTO kv (key, value) VALUES ('filing.submission_seq', ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                          (str(n),))
        efin = "".join(ch for ch in os.environ.get("AGENTLEDGER_EFIN", "") if ch.isdigit())[:6].rjust(6, "0")
        now = self.clock()
        return f"{efin}{now.year}{now.timetuple().tm_yday:03d}{n:07d}"

    def _public(self, s: dict[str, Any]) -> dict[str, Any]:
        return {k: s.get(k) for k in ("id", "return_id", "jurisdiction", "kind", "status", "attempt", "supersedes", "linked_to",
                                      "package_hash", "planned_submission_id", "provider_submission_id", "rejection_codes",
                                      "created_at", "updated_at")}

    def _outcome(self, sub: dict[str, Any]) -> dict[str, Any]:
        """What a command returns: the submission as recorded (ids, hashes and statuses only)."""
        return {**self._public(sub), "submission_id": sub["id"], "return_status": self.wf.state(sub["return_id"]).status}

    def _emit(self, sub: dict[str, Any], event: str, **extra: Any) -> int:
        client = self.R.get(sub["return_id"])["client_id"]
        payload = {"submission_id": sub["id"], "return_id": sub["return_id"], "jurisdiction": sub["jurisdiction"],
                   "attempt": int(sub.get("attempt") or 1), "linked_to": sub.get("linked_to"), **extra}
        return outbox.emit(self.conn, client, event, "filing_submission", sub["id"], payload)

    def _record(self, sub: dict[str, Any], event: str, actor: str, role: str = SYSTEM, *, facts: dict[str, Any] | None = None,
                note: str = "", **cols: Any) -> State:
        """One transition on the submission's stream, mirrored into its row (status and the named columns)."""
        st = self.wf.send(sub["id"], event, actor, role=role, facts=facts, note=note)
        self._update(sub["id"], status=st.status, **cols)
        return st

    # ---------------------------------------------------------------- planning
    def plan(self, rid: str, actor: str, jurisdictions: list[str], package_hash: str) -> list[str]:
        """Ensure one queued submission per jurisdiction of the release, linked to the federal one. Idempotent: a
        queued row of the same package is kept (and announced again, for a runner to pick up); a queued row of
        another package is cancelled and replaced; one already sent is left alone."""
        with unit_of_work(self.conn):
            active = {s["jurisdiction"]: s for s in self.for_return(rid) if s["status"] in ACTIVE}
            fed = active.get(FEDERAL)
            out = []
            for j in jurisdictions:
                s = active.get(j)
                if s and s["status"] != "queued":
                    out.append(s["id"])
                    continue
                if s and s["package_hash"] != package_hash:
                    self._record(s, "cancel", actor, "cpa", note="release approved for another package")
                    self._emit(s, "submission.cancelled")
                    s = None
                if s is None:
                    s = self._create(rid, j, actor, package_hash, kind="original", linked_to=None if j == FEDERAL else (fed or {}).get("id"))
                    if j == FEDERAL:
                        fed = s
                self._emit(s, "submission.queued", linked_accepted=bool(fed and fed["status"] == "accepted") if j != FEDERAL else False)
                out.append(s["id"])
            return out

    def _create(self, rid: str, jurisdiction: str, actor: str, package_hash: str, *, kind: str, linked_to: str | None,
                supersedes: str | None = None) -> dict[str, Any]:
        sub_id = "sub_" + secrets.token_hex(6)
        now = self._now()
        planned = self._planned_id()
        self.conn.execute("INSERT INTO filing_submissions (id, return_id, jurisdiction, kind, supersedes, linked_to, package_hash, attempt, "
                          "planned_submission_id, status, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 'queued', ?, ?, ?)",
                          (sub_id, rid, jurisdiction, kind, supersedes, linked_to, package_hash, planned, actor, now, now))
        self.wf.start(sub_id, KIND, actor, {"return_id": rid, "jurisdiction": jurisdiction, "package_hash": package_hash,
                                             "planned_submission_id": planned, "kind": kind, "supersedes": supersedes})
        return self.get(sub_id)

    def _federal(self, rid: str, actor: str, package_hash: str) -> dict[str, Any]:
        """The federal submission a transmission belongs to: the active one, or a new queued row (a CPA transmitting
        directly plans it in the same act)."""
        for s in self.for_return(rid):
            if s["jurisdiction"] == FEDERAL and s["status"] in ACTIVE:
                return s
        return self.get(self.plan(rid, actor, [FEDERAL], package_hash)[0])

    def cancel_queued(self, rid: str, actor: str, note: str) -> int:
        """Queued submissions of a return that will not be transmitted (voided, filed on paper, reopened)."""
        n = 0
        with unit_of_work(self.conn):
            for s in self.for_return(rid):
                if s["status"] == "queued":
                    self._record(s, "cancel", actor, "cpa", note=note)
                    self._emit(s, "submission.cancelled")
                    n += 1
        return n

    # ---------------------------------------------------------------- the transmission (one write path)
    def transmit_return(self, rid: str, actor: str, role: str, *, efile_ready: bool, send: Callable[[str], dict[str, Any]],
                        lookup: Callable[[str], dict[str, Any] | None] | None = None) -> State:
        """Transmit the approved package to the IRS at most once; see Returns.transmit for the contract. The federal
        submission row and its stream are written with the return's events."""
        R = self.R
        st = self.wf.state(rid)
        reasons = []
        if role == SYSTEM:
            if st.status != "release_approved":
                reasons.append("the workflow transmits only a return whose release a CPA approved")
            elif st.facts.get("release_hash") != st.facts.get("approved_hash"):
                reasons.append("the release approval names another package")
        elif role == "cpa":
            if st.status not in ("signed", "release_approved"):
                reasons.append(f"only a signed return can be transmitted (this one is {st.status})")
        else:
            reasons.append("transmission needs a credentialed reviewer (CPA)")
        if not st.facts.get("signed_hash") or st.facts.get("signed_hash") != st.facts.get("approved_hash"):
            reasons.append("no valid signature is bound to the approved package")
        if not efile_ready:
            reasons.append("e-file is not enabled for this firm (EFIN, ETIN and ATS approval required)")
        reasons += R._blockers_now(rid)
        if R.current_package_hash(rid) != st.facts.get("approved_hash"):
            reasons.append("the current return is not the approved and signed package")
        blockers = R.filing_blockers(rid, [FEDERAL])
        if blockers:
            reasons.append("coverage does not allow filing: " + ", ".join(f"{b['form']} is {b['status']}" for b in blockers))
        if reasons:
            raise TransitionError("; ".join(reasons))
        approved = st.facts["approved_hash"]
        sub = self._federal(rid, actor, approved)
        attempt = 1 + sum(1 for h in st.history if h["event"] == "reconciled_not_submitted")
        key = f"{rid}:{approved[:16]}"
        # The first attempt's activity key is the approved hash (the record every reader knows); an attempt after a
        # reconciled "never received" gets its own key, so the started marker of the first never blocks it.
        activity_key = approved if attempt == 1 else f"{approved}:{attempt}"
        try:
            result = self.wf.activity(rid, "transmit", activity_key, lambda: send(key), actor=actor,
                                      reconcile=(lambda: lookup(key)) if lookup else None,
                                      started={"planned_submission_id": sub["planned_submission_id"], "attempt": attempt})
        except UncertainOutcome as e:
            with unit_of_work(self.conn):
                self.wf.send(rid, "outcome_unknown", actor, note=str(e))
                if self.get(sub["id"])["status"] == "queued":
                    self._record(sub, "outcome_unknown", actor, role, note=str(e))
                    self._emit(sub, "submission.unknown")
            raise
        with unit_of_work(self.conn):
            release = ({} if st.status == "release_approved" else
                       {"release_approved_by": actor, "release_approved_at": self._now(), "release_hash": approved,
                        "release_efile_ready": True, "submissions": {"jurisdictions": [FEDERAL], "hashes": {FEDERAL: approved}}})
            out = self.wf.send(rid, "transmit", actor, role=role, context={"efile_ready": efile_ready, "role": role},
                               facts={"submission_id": result.get("submission_id"), "idempotency_key": key, **release})
            sub = self.get(sub["id"])
            if sub["status"] == "queued":
                self._record(sub, "transmit", actor, role, facts={"provider_submission_id": result.get("submission_id"),
                                                                  "reconciled": bool(result.get("reconciled"))},
                             provider_submission_id=result.get("submission_id"))
                self._emit(sub, "submission.transmitted")
            audit.record(self.conn, actor, role, "return.transmitted", {"return_id": rid, "submission": sub["id"],
                         "submission_id": result.get("submission_id")}, client_id=R.get(rid)["client_id"])
        return out

    def _package(self, rid: str) -> dict[str, Any]:
        from .store import package_of

        v = self.R.latest(rid)
        return package_of(v["inputs"], v["result"], v["provenance"], self.R.dispositions(rid))

    def _transmit_state(self, sub: dict[str, Any], actor: str, provider: Any) -> None:
        planned = sub["planned_submission_id"]
        package = self._package(sub["return_id"])
        try:
            result = self.wf.activity(sub["id"], "transmit", planned, lambda: provider.submit(package, planned), actor=actor,
                                      started={"planned_submission_id": planned})
        except UncertainOutcome as e:
            with unit_of_work(self.conn):
                self._record(sub, "outcome_unknown", actor, SYSTEM, note=str(e))
                self._emit(sub, "submission.unknown")
            raise
        with unit_of_work(self.conn):
            if self.get(sub["id"])["status"] == "queued":
                self._record(sub, "transmit", actor, SYSTEM, facts={"provider_submission_id": result.get("submission_id"),
                                                                    "reconciled": bool(result.get("reconciled"))},
                             provider_submission_id=result.get("submission_id"))
                self._emit(sub, "submission.transmitted")

    # ---------------------------------------------------------------- system commands (each an "ensure")
    def transmit_submission(self, sub_id: str, actor: str, provider: Any) -> dict[str, Any]:
        """Send a queued submission once. Already sent, acknowledged or unknown: the recorded outcome. An answer that
        never comes leaves the stream's started marker; the next attempt stops in `unknown` (no lookup is trusted
        blindly: a person, or `lookup_submission` by the planned id, reconciles it)."""
        sub = self.get(sub_id)
        if sub["status"] != "queued":
            if sub["status"] in ("cancelled", "superseded"):
                raise TransitionError(f"submission {sub_id} is {sub['status']}")
            return self._outcome(sub)
        if sub["linked_to"] and self.get(sub["linked_to"])["status"] != "accepted":
            raise TransitionError("a state submission is transmitted only after the federal return is accepted")
        if provider is None:
            raise TransitionError("no transmitter is configured (backlog T2-02 brings the MeF A2A adapter)")
        rid = sub["return_id"]
        try:
            if sub["jurisdiction"] == FEDERAL:
                st = self.wf.state(rid)
                package = self._package(rid)
                planned = sub["planned_submission_id"]
                self.transmit_return(rid, actor, SYSTEM, efile_ready=bool(st.facts.get("release_efile_ready")),
                                     send=lambda key: provider.submit(package, planned))
            else:
                self._transmit_state(sub, actor, provider)
        except UncertainOutcome:
            pass                                             # recorded as unknown; the outcome below says so
        return self._outcome(self.get(sub_id))

    def mark_unknown(self, sub_id: str, actor: str, note: str = "") -> dict[str, Any]:
        """The orchestrator gave up waiting for the transmitter's answer: the submission (and, federally, the return)
        is unknown until reconciled. Already past queued: the recorded outcome."""
        sub = self.get(sub_id)
        if sub["status"] != "queued":
            return self._outcome(sub)
        with unit_of_work(self.conn):
            if sub["jurisdiction"] == FEDERAL and self.wf.state(sub["return_id"]).status in ("signed", "release_approved"):
                self.wf.send(sub["return_id"], "outcome_unknown", actor, note=note or "no answer from the transmitter")
            self._record(sub, "outcome_unknown", actor, SYSTEM, note=note or "no answer from the transmitter")
            self._emit(sub, "submission.unknown")
        return self._outcome(self.get(sub_id))

    def lookup_submission(self, sub_id: str, actor: str, provider: Any) -> dict[str, Any]:
        """Ask the transmitter whether it holds the planned submission id. Found: transmitted, reconciled, nothing is
        sent. Not found: still unknown (the answer may still be in flight; a person decides when to give up)."""
        sub = self.get(sub_id)
        if sub["status"] != "unknown":
            return {**self._outcome(sub), "found": sub["status"] in ("transmitted", "accepted", "rejected")}
        if provider is None:
            raise TransitionError("no transmitter is configured (backlog T2-02 brings the MeF A2A adapter)")
        found = provider.lookup(sub["planned_submission_id"])
        if found is None:
            return {**self._outcome(sub), "found": False}
        self._reconciled(sub, actor, SYSTEM, submitted=True, provider_submission_id=str(found.get("submission_id") or sub["planned_submission_id"]),
                         evidence=f"the transmitter holds {sub['planned_submission_id']}", by_provider=True)
        return {**self._outcome(self.get(sub_id)), "found": True}

    def poll_acks(self, sub_id: str, actor: str, provider: Any) -> dict[str, Any]:
        """Ask for the acknowledgement of a transmitted submission and record it when it is there."""
        sub = self.get(sub_id)
        if sub["status"] in ("accepted", "rejected"):
            return {**self._outcome(sub), "acknowledged": True}
        if sub["status"] != "transmitted":
            raise TransitionError(f"submission {sub_id} is {sub['status']}; only a transmitted one is acknowledged")
        if provider is None:
            raise TransitionError("no transmitter is configured (backlog T2-02 brings the MeF A2A adapter)")
        ack = provider.acknowledgement(sub["provider_submission_id"] or sub["planned_submission_id"])
        if not ack:
            return {**self._outcome(sub), "acknowledged": False}
        return {**self.record_ack(sub_id, actor, str(ack.get("status", "")), list(ack.get("codes") or []), ack.get("payload")),
                "acknowledged": True}

    def record_ack(self, sub_id: str, actor: str, status: str, codes: list[str] | None = None,
                   payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record the IRS's or a state's acknowledgement. Federal: the return moves with it (accepted, or rejected for
        correction), linked state submissions are released or cancelled. State: the return keeps its status; a
        rejection opens a task for a reviewer."""
        if status not in ("accepted", "rejected"):
            raise ValueError("an acknowledgement is accepted or rejected")
        sub = self.get(sub_id)
        if sub["status"] in ("accepted", "rejected"):
            if sub["status"] != status:
                raise TransitionError(f"submission {sub_id} is already {sub['status']}; a different acknowledgement needs a person")
            return self._outcome(sub)
        if sub["status"] != "transmitted":
            raise TransitionError(f"submission {sub_id} is {sub['status']}; only a transmitted one is acknowledged")
        codes = [str(c) for c in (codes or [])]
        event = "ack_accepted" if status == "accepted" else "ack_rejected"
        rid = sub["return_id"]
        client = self.R.get(rid)["client_id"]
        with unit_of_work(self.conn):
            sealed = self.R.sealer.seal(payload, f"submission-ack:{sub_id}") if payload is not None else None
            self._record(sub, event, actor, SYSTEM, facts={"ack": status, "rejection_codes": codes}, ack_payload=sealed,
                         rejection_codes=json.dumps(codes))
            if sub["jurisdiction"] == FEDERAL:
                self.wf.send(rid, event, actor, facts={"ack": "A" if status == "accepted" else "R", "rejection_codes": codes})
                linked = [s for s in self.for_return(rid) if s["linked_to"] == sub_id and s["status"] == "queued"]
                if status == "accepted":
                    self._emit(sub, "submission.accepted", linked=[{"submission_id": s["id"], "attempt": int(s["attempt"] or 1),
                                                                    "jurisdiction": s["jurisdiction"]} for s in linked])
                else:
                    for s in linked:
                        self._record(s, "cancel", actor, SYSTEM, note="the federal return was rejected")
                        self._emit(s, "submission.cancelled")
                    self._emit(sub, "submission.rejected", codes=codes)
            elif status == "accepted":
                self._emit(sub, "submission.accepted", linked=[])
            else:
                crm.create_task(self.conn, title=f"{sub['jurisdiction']} return rejected ({', '.join(codes) or 'no code'})",
                                assignee="cpa", source="filing", client_id=client, dedupe_key=f"filing:{sub_id}:rejected",
                                detail=f"Return {rid}: the {sub['jurisdiction']} submission {sub_id} was rejected. Correct the state "
                                       "package (backlog T2-05) or retransmit it; the federal return stays as acknowledged.")
                self._emit(sub, "submission.rejected", codes=codes)
            audit.record(self.conn, actor, SYSTEM, f"filing.{status}", {"return_id": rid, "submission": sub_id, "jurisdiction": sub["jurisdiction"],
                         "codes": codes}, client_id=client)
        return self._outcome(self.get(sub_id))

    def notify_operator(self, sub_id: str, actor: str, reason: str) -> dict[str, Any]:
        """Ask a person to look at a submission (an answer that never came, an acknowledgement overdue): a task,
        an audit record and an outbox event, once per reason."""
        sub = self.get(sub_id)
        rid = sub["return_id"]
        client = self.R.get(rid)["client_id"]
        reason = reason.strip() or "attention needed"
        with unit_of_work(self.conn):
            task = crm.create_task(self.conn, title=f"Filing needs attention: {sub['jurisdiction']} {reason}", assignee="cpa", source="filing",
                                   client_id=client, dedupe_key=f"filing:{sub_id}:{reason}",
                                   detail=f"Return {rid}, submission {sub_id} ({sub['status']}): {reason}.")
            if task is not None:
                audit.record(self.conn, actor, SYSTEM, "filing.attention", {"return_id": rid, "submission": sub_id, "reason": reason,
                             "task_id": task}, client_id=client)
                self._emit(sub, "filing.attention", reason=reason, task_id=task)
        return {**self._outcome(sub), "task_id": task, "reason": reason}

    # ---------------------------------------------------------------- a person's decisions
    def _reconciled(self, sub: dict[str, Any], actor: str, role: str, *, submitted: bool, provider_submission_id: str,
                    evidence: str, by_provider: bool = False) -> None:
        """unknown -> transmitted (received after all) or -> queued under a new planned id (never received); the
        federal submission moves the return with it (transmitted, or back to signed for a new release)."""
        rid = sub["return_id"]
        with unit_of_work(self.conn):
            fed = sub["jurisdiction"] == FEDERAL and self.wf.state(rid).status == "unknown"
            if submitted:
                if fed:
                    self.wf.send(rid, "reconciled_submitted", actor, note=evidence, facts={"submission_id": provider_submission_id})
                self._record(sub, "reconciled_submitted", actor, role, note=evidence,
                             facts={"provider_submission_id": provider_submission_id, "reconciled": True, "by_provider": by_provider},
                             provider_submission_id=provider_submission_id)
                self._emit(sub, "submission.reconciled", submitted=True)
                self._emit(sub, "submission.transmitted")
            else:
                if fed:
                    self.wf.send(rid, "reconciled_not_submitted", actor, note=evidence)
                planned = self._planned_id()
                self._record(sub, "reconciled_not_submitted", actor, role, note=evidence,
                             facts={"planned_submission_id": planned, "attempt": int(sub["attempt"]) + 1},
                             planned_submission_id=planned, attempt=int(sub["attempt"]) + 1)
                self._emit(sub, "submission.reconciled", submitted=False)
            audit.record(self.conn, actor, role, "filing.reconciled", {"return_id": rid, "submission": sub["id"], "submitted": submitted,
                         "evidence": evidence}, client_id=self.R.get(rid)["client_id"])

    def reconcile(self, sub_id: str, actor: str, role: str, *, submitted: bool, provider_submission_id: str = "",
                  evidence: str = "") -> dict[str, Any]:
        """A CPA confirms with the transmitter what happened to a submission whose outcome was unknown."""
        if role not in AUTHORITY:
            raise TransitionError("reconciling a transmission needs a CPA")
        sub = self.get(sub_id)
        if sub["status"] != "unknown":
            raise TransitionError(f"submission {sub_id} is {sub['status']}, not unknown")
        self._reconciled(sub, actor, role, submitted=submitted, provider_submission_id=provider_submission_id, evidence=evidence)
        return self._outcome(self.get(sub_id))

    def reconcile_return(self, rid: str, actor: str, role: str, *, submitted: bool, provider_submission_id: str = "",
                         evidence: str = "") -> State:
        """Returns.reconcile_transmission: the return's unknown federal transmission, with its submission row when
        there is one."""
        if role not in AUTHORITY:
            raise TransitionError("reconciling a transmission needs a CPA")
        unknown = [s for s in self.for_return(rid) if s["jurisdiction"] == FEDERAL and s["status"] == "unknown"]
        if unknown:
            self._reconciled(unknown[0], actor, role, submitted=submitted, provider_submission_id=provider_submission_id, evidence=evidence)
            return self.wf.state(rid)
        if submitted:
            return self.wf.send(rid, "reconciled_submitted", actor, note=evidence, facts={"submission_id": provider_submission_id})
        return self.wf.send(rid, "reconciled_not_submitted", actor, note=evidence)

    def retransmit(self, sub_id: str, actor: str, role: str) -> dict[str, Any]:
        """Replace a rejected state submission with a retransmission of the same package (a state-only correction
        package is backlog T2-05). A federal rejection is corrected through the return (correct, review, sign,
        release); an accepted submission is never sent again."""
        if role not in AUTHORITY:
            raise TransitionError("retransmitting needs a CPA")
        sub = self.get(sub_id)
        if sub["jurisdiction"] == FEDERAL:
            raise TransitionError("the federal return is not retransmitted: correct it, then review, sign and release it again")
        if sub["status"] != "rejected":
            raise TransitionError(f"only a rejected submission is retransmitted (this one is {sub['status']})")
        rid = sub["return_id"]
        with unit_of_work(self.conn):
            self._record(sub, "retransmit", actor, role, note="replaced by a retransmission")
            new = self._create(rid, sub["jurisdiction"], actor, sub["package_hash"], kind="retransmission", linked_to=sub["linked_to"],
                               supersedes=sub_id)
            fed = self.get(sub["linked_to"]) if sub["linked_to"] else None
            self._emit(new, "submission.queued", linked_accepted=bool(fed and fed["status"] == "accepted"))
            self._emit(new, "submission.retransmit", supersedes=sub_id)
            audit.record(self.conn, actor, role, "filing.retransmit", {"return_id": rid, "superseded": sub_id, "submission": new["id"]},
                         client_id=self.R.get(rid)["client_id"])
        return self._outcome(self.get(new["id"]))

    # ---------------------------------------------------------------- the picture
    def summary(self, rid: str) -> dict[str, Any]:
        st = self.wf.state(rid)
        subs = self.for_return(rid)
        released = st.status in ("release_approved", "transmitted", "accepted", "rejected", "unknown") and st.facts.get("release_hash")
        plan = st.facts.get("submissions") or {}
        release = ({"approved_by": st.facts.get("release_approved_by"), "at": st.facts.get("release_approved_at"),
                    "hash": st.facts.get("release_hash"), "jurisdictions": list(plan.get("jurisdictions") or [FEDERAL])} if released else None)
        required = list(release["jurisdictions"]) if release else []
        accepted = {s["jurisdiction"] for s in subs if s["status"] == "accepted"}
        complete = st.status == "paper_filed" or (bool(required) and all(j in accepted for j in required))
        return {"status": st.status, "release": release, "submissions": [self._public(s) for s in subs], "complete": complete}


# ------------------------------------------------------------------------------------------------- commands
def _filing(ctx: Context) -> Filing:
    return Filing(ctx.returns, clock=ctx.clock)


def _scope_submission(ctx: Context, payload: dict[str, Any]) -> str:
    return _filing(ctx).client_of(str(payload["submission_id"]))


def _scope_return(ctx: Context, payload: dict[str, Any]) -> str:
    return str(ctx.returns.get(str(payload["return_id"]))["client_id"])


def _sub(cmd: Command) -> str:
    try:
        return str(cmd.payload["submission_id"])
    except KeyError:
        raise ValueError("the payload names the submission_id") from None


def _prepare_transmit(ctx: Context, cmd: Command) -> None:
    """Before the receipt transaction: the two-phase activity, durable on its own (see workflow/commands.py)."""
    _filing(ctx).transmit_submission(_sub(cmd), cmd.actor, ctx.provider)


def _h_transmit(ctx: Context, cmd: Command, prepared: Any) -> dict[str, Any]:
    f = _filing(ctx)
    return f._outcome(f.get(_sub(cmd)))


def _h_lookup(ctx: Context, cmd: Command, prepared: Any) -> dict[str, Any]:
    return _filing(ctx).lookup_submission(_sub(cmd), cmd.actor, ctx.provider)


def _h_poll(ctx: Context, cmd: Command, prepared: Any) -> dict[str, Any]:
    return _filing(ctx).poll_acks(_sub(cmd), cmd.actor, ctx.provider)


def _h_record_ack(ctx: Context, cmd: Command, prepared: Any) -> dict[str, Any]:
    p = cmd.payload
    return _filing(ctx).record_ack(_sub(cmd), cmd.actor, str(p.get("status", "")), list(p.get("codes") or []), p.get("payload"))


def _h_unknown(ctx: Context, cmd: Command, prepared: Any) -> dict[str, Any]:
    return _filing(ctx).mark_unknown(_sub(cmd), cmd.actor, str(cmd.payload.get("note", "")))


def _h_notify(ctx: Context, cmd: Command, prepared: Any) -> dict[str, Any]:
    return _filing(ctx).notify_operator(_sub(cmd), cmd.actor, str(cmd.payload.get("reason", "")))


def _h_reconcile(ctx: Context, cmd: Command, prepared: Any) -> dict[str, Any]:
    p = cmd.payload
    return _filing(ctx).reconcile(_sub(cmd), cmd.actor, cmd.role, submitted=bool(p.get("submitted")),
                                  provider_submission_id=str(p.get("provider_submission_id", "")), evidence=str(p.get("evidence", "")))


def _h_retransmit(ctx: Context, cmd: Command, prepared: Any) -> dict[str, Any]:
    return _filing(ctx).retransmit(_sub(cmd), cmd.actor, cmd.role)


def _h_release(ctx: Context, cmd: Command, prepared: Any) -> dict[str, Any]:
    p = cmd.payload
    st = ctx.returns.approve_release(str(p["return_id"]), cmd.actor, cmd.role, efile_ready=bool(p.get("efile_ready")),
                                     jurisdictions=p.get("jurisdictions"))
    return {**st.to_dict(("release_hash", "release_approved_by", "release_approved_at", "submissions")),
            "filing": _filing(ctx).summary(str(p["return_id"]))}


def _h_void(ctx: Context, cmd: Command, prepared: Any) -> dict[str, Any]:
    return ctx.returns.void(str(cmd.payload["return_id"]), cmd.actor, cmd.role, str(cmd.payload.get("note", ""))).to_dict()


# System commands: what the workflow may run. Human commands: a person's decisions, refused for the role "system".
register(Spec("transmit_submission", _h_transmit, _scope_submission, prepare=_prepare_transmit))
register(Spec("lookup_submission", _h_lookup, _scope_submission))
register(Spec("poll_acks", _h_poll, _scope_submission))
register(Spec("record_ack", _h_record_ack, _scope_submission))
register(Spec("mark_unknown", _h_unknown, _scope_submission))
register(Spec("notify_operator", _h_notify, _scope_submission))
register(Spec("approve_release", _h_release, _scope_return, human=True))
register(Spec("reconcile_submission", _h_reconcile, _scope_submission, human=True))
register(Spec("retransmit", _h_retransmit, _scope_submission, human=True))
register(Spec("void_return", _h_void, _scope_return, human=True))
