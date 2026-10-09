"""Random walks over the return filing state machine (backlog F-08). Whatever a person, the workflow and the
transmitter do in whatever order, these hold:

- the return's stream never records two transmission starts without a "never received" reconciliation between them;
- an accepted return has exactly one accepted federal submission, and no jurisdiction has two accepted ones;
- a release approval binds one package: signed_hash == approved_hash == release_hash;
- no system actor ever approves, releases, reconciles by its own decision, retransmits, voids or files on paper;
- a filed (frozen) return never gains a version;
- every event stream's hash chain verifies.

Plain `random.Random(seed)` walks; failures print the seed and the step that broke the invariant.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest
from test_return_workflow import fam, household  # noqa: F401  (fam is a fixture)
from test_workflow_commands import filing_approved

from agentledger.returns.filing import Filing, MockProvider
from agentledger.returns.store import FROZEN, Returns
from agentledger.workflow.bus import ProviderUnknown
from agentledger.workflow.engine import TransitionError, UncertainOutcome
from agentledger.workflow.runner import CrashAfterCommit, FakeClock, MemoryRuns, Timeout, local_runner

HUMAN_ONLY = {"approve", "approve_release", "request_signature", "request_changes", "reconciled_not_submitted", "retransmit",
              "void", "mark_paper_filed", "correct"}
IGNORED = (TransitionError, UncertainOutcome, ValueError, KeyError, TimeoutError, ConnectionError)
NEC_NOTE = "issued in error; the payer is sending a corrected 1099"


class Walk:
    def __init__(self, fam, seed):  # noqa: F811
        self.rng = random.Random(seed)
        self.R = Returns(fam.conn, fam.kb, segregation=False)
        self.conn = fam.conn
        self.rid = self.R.create("rivera", 2026, "maya", household())
        self.R.populate_from_documents(self.rid, "maya")
        self.R.account_for_document(self.rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
        self.review_cycle()
        self.provider = MockProvider()
        self.clock = FakeClock()
        self.runner = local_runner(fam.conn, self.R, self.provider, clock=self.clock, runs=MemoryRuns())
        self.frozen_version: int | None = None
        self.sent = 0
        self.log: list[str] = []

    # ---- what people, the workflow and the transmitter may do
    def review_cycle(self):
        R, rid = self.R, self.rid
        for step in (lambda: R.confirm(rid, None, "maya"), lambda: R.submit_for_review(rid, "maya"), lambda: R.approve(rid, "lee", "cpa"),
                     lambda: R.request_signature(rid, "lee", "cpa"),
                     lambda: R.record_signature(rid, "taxpayer", method="wet_signature", return_hash=R.status(rid).facts.get("approved_hash", ""))):
            try:
                step()
            except IGNORED:
                pass

    def release(self):
        where = self.rng.choice([["US-FED"], ["US-FED", "US-CA"], ["US-FED", "US-CA", "US-NY"]])
        self.R.approve_release(self.rid, "lee", "cpa", efile_ready=True, jurisdictions=where)

    def cpa_transmit(self):
        outcome = self.rng.choice(["ok", "ok", "timeout", "down"])

        def submit(key):
            if outcome == "down":
                raise ConnectionError("unreachable")
            self.sent += 1
            if outcome == "timeout":
                raise TimeoutError("no answer")
            return {"submission_id": f"CPA{self.sent:017d}"}

        self.R.transmit(self.rid, "lee", "cpa", efile_ready=True, submit=submit)

    def tick(self):
        self.clock.advance(self.rng.choice([1, 30, 60, 900, 1800, 3600, 86400]))
        self.runner.tick(self.conn)

    def decide(self):
        if self.provider.received:
            pid = self.rng.choice(sorted(self.provider.received))
            self.provider.decide(pid, self.rng.choice(["accepted", "accepted", "rejected"]), ["R0001"])

    def reconcile_return(self):
        self.R.reconcile_transmission(self.rid, "lee", "cpa", submitted=self.rng.random() < 0.5,
                                      submission_id=f"REC{self.rng.randint(1, 999):017d}", evidence="checked with the transmitter")

    def reconcile_submission(self):
        subs = Filing(self.R).for_return(self.rid)
        if subs:
            s = self.rng.choice(subs)
            Filing(self.R).reconcile(s["id"], "lee", "cpa", submitted=self.rng.random() < 0.5, provider_submission_id="REC00000000000000001",
                                     evidence="checked with the transmitter")

    def retransmit(self):
        subs = Filing(self.R).for_return(self.rid)
        if subs:
            self.R.retransmit(self.rng.choice(subs)["id"], "lee", "cpa")

    def edit(self):
        inputs = {**self.R.latest(self.rid)["inputs"], "payments": {"estimated_tax_payments": str(self.rng.randint(1, 999))}}
        self.R.save_inputs(self.rid, inputs, "maya")

    def compute(self):
        self.R.compute(self.rid, "maya")

    def void(self):
        self.R.void(self.rid, "lee", "cpa", "abandoned by the client on 2027-04-10, see the email on file")

    def paper(self):
        self.R.mark_paper_filed(self.rid, "lee", "cpa", "mailed by certified mail on 2027-04-10, receipt 7019 0000 0000")

    def correct(self):
        self.R.wf.send(self.rid, "correct", "lee", role="cpa")

    def fault(self):
        self.runner.faults.setdefault("transmit", []).append(self.rng.choice([Timeout(), CrashAfterCommit(), ProviderUnknown()]))

    def arm_provider(self):
        self.provider.arm(self.rng.choice(["timeout", "down"]))

    def system_tries_a_decision(self):
        """The workflow never gets these; the registry refuses them, and so do the guards if it tried."""
        from agentledger.workflow import commands
        from agentledger.workflow.commands import Command, Context, NotPermitted

        ctx = Context(self.conn, self.R, self.provider)
        subs = Filing(self.R).for_return(self.rid)
        name, payload = self.rng.choice([("approve_release", {"return_id": self.rid}), ("void_return", {"return_id": self.rid, "note": "x" * 12})]
                                        + ([("reconcile_submission", {"submission_id": subs[0]["id"], "submitted": True}),
                                            ("retransmit", {"submission_id": subs[0]["id"]})] if subs else []))
        with pytest.raises(NotPermitted):
            commands.dispatch(ctx, Command(name, f"sys-{self.rng.random()}", payload, "workflow:rogue", "system"))

    ACTIONS = [(tick, 6), (review_cycle, 3), (release, 3), (cpa_transmit, 2), (decide, 3), (reconcile_return, 2), (reconcile_submission, 1),
               (retransmit, 1), (edit, 1), (compute, 1), (void, 0.4), (paper, 0.4), (correct, 1), (fault, 2), (arm_provider, 1),
               (system_tries_a_decision, 1)]

    def step(self):
        fn = self.rng.choices([a for a, _ in self.ACTIONS], weights=[w for _, w in self.ACTIONS])[0]
        self.log.append(fn.__name__)
        try:
            fn(self)
        except IGNORED:
            pass

    # ---- what must hold after every step
    def check(self):
        R, rid = self.R, self.rid
        st = R.status(rid)
        events = [h["event"] for h in st.history]
        last_started = None
        for i, e in enumerate(events):
            if e == "activity_started:transmit":
                if last_started is not None:
                    assert "reconciled_not_submitted" in events[last_started + 1:i], (self.log, events)
                last_started = i
        subs = Filing(R).for_return(rid)
        accepted = Counter(s["jurisdiction"] for s in subs if s["status"] == "accepted")
        assert all(n <= 1 for n in accepted.values()), (self.log, subs)
        if st.status == "accepted":
            assert accepted["US-FED"] == 1, (self.log, subs)
        active = Counter(s["jurisdiction"] for s in subs if s["status"] in ("queued", "transmitted", "unknown"))
        assert all(n <= 1 for n in active.values()), (self.log, subs)
        if st.status == "release_approved":
            f = st.facts
            assert f["signed_hash"] == f["approved_hash"] == f["release_hash"] == R.current_package_hash(rid), self.log
        for wid in [rid] + [s["id"] for s in subs]:
            state = R.wf.state(wid)
            for h in state.history:
                if h["event"] in HUMAN_ONLY:
                    assert not h["actor"].startswith("workflow:"), (self.log, wid, h)
            assert R.wf.verify(wid), (self.log, wid)
        for s in subs:
            assert R.wf.state(s["id"]).status == s["status"], (self.log, s)
        version = R.latest(rid, decrypt=False)["version"]
        if st.status in FROZEN:
            if self.frozen_version is None:
                self.frozen_version = version
            assert version == self.frozen_version, (self.log, st.status)
        else:
            self.frozen_version = None


@pytest.mark.parametrize("seed", list(range(1, 13)))
def test_random_walks_keep_the_filing_invariants(fam, monkeypatch, seed):  # noqa: F811
    filing_approved(monkeypatch)
    walk = Walk(fam, seed)
    walk.check()
    for _ in range(40):
        walk.step()
        walk.check()
    assert walk.R.wf.verify(walk.rid)
