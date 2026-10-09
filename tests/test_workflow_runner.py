"""The local workflow runner and the submission flow (backlog F-08, ADR-0003 §5; Q03, Q23).

Retries are deterministic against a fake clock; step results are memoised, so a re-run replays; a crash after the
domain committed is retried without a second send; an answer that never comes leaves the submission unknown, a lookup
by the planned id reconciles it without sending again, and when nothing is found a person is asked and the instance
waits for the reconciliation signal. The step names a run emits are those of flows/steps.json.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from test_return_workflow import fam  # noqa: F401  (fixture)
from test_workflow_commands import released

from agentledger.db import count
from agentledger.returns.filing import Filing, MockProvider
from agentledger.workflow import outbox
from agentledger.workflow.bus import ProviderUnknown, Reject
from agentledger.workflow.flows import expand, plan
from agentledger.workflow.runner import CrashAfterCommit, DbRuns, FakeClock, MemoryRuns, Timeout, instance_id_for, local_runner

REPO = Path(__file__).resolve().parent.parent
RETRY_DELAY = plan()["retries"]["command"]["delay_s"]


def start(fam, monkeypatch, *, provider=None, faults=None, runs=None, jurisdictions=("US-FED",)):  # noqa: F811
    """A released return, a runner over the firm store, and the federal instance started by the relay."""
    R, rid, subs = released(fam, monkeypatch, jurisdictions=jurisdictions)
    clock = FakeClock()
    runner = local_runner(fam.conn, R, provider or MockProvider(), clock=clock, runs=runs if runs is not None else MemoryRuns(), faults=faults)
    assert runner.tick(fam.conn)["relayed"] >= 1                     # submission.queued -> an instance per submission
    fed = subs["US-FED"]
    return R, rid, subs, runner, clock, instance_id_for(fed)


def events(R, rid):
    return [h["event"] for h in R.status(rid).history]


def test_retries_are_deterministic_against_the_fake_clock(fam, monkeypatch):  # noqa: F811
    provider = MockProvider()
    R, rid, subs, runner, clock, iid = start(fam, monkeypatch, provider=provider, faults={"transmit": [Timeout(), Timeout()]})
    run = runner.get(iid)
    assert run.status == "sleeping" and run.steps["transmit"] == {"state": "retrying", "attempts": 1, "error": "transmit: the call timed out"}
    assert provider.calls["submit"] == 0
    clock.advance(RETRY_DELAY - 1)
    assert runner.tick()["advanced"] == 0                             # not due yet
    clock.advance(1)
    runner.tick()
    assert runner.get(iid).steps["transmit"]["attempts"] == 2 and provider.calls["submit"] == 0
    clock.advance(RETRY_DELAY * 2 - 1)                                # exponential: the second wait is twice the first
    assert runner.tick()["advanced"] == 0
    clock.advance(1)
    runner.tick()
    run = runner.get(iid)
    assert run.steps["transmit"]["state"] == "done" and run.steps["transmit"]["attempts"] == 3 and provider.calls["submit"] == 1
    assert run.status == "sleeping" and run.steps["poll-wait-1"]["state"] == "sleeping"
    assert R.status(rid).status == "transmitted" and Filing(R).get(subs["US-FED"]["id"])["status"] == "transmitted"


def test_step_results_are_memoised_so_a_rerun_replays(fam, monkeypatch):  # noqa: F811
    provider = MockProvider(ack_after=2)
    R, rid, subs, runner, clock, iid = start(fam, monkeypatch, provider=provider)
    run = runner.get(iid)
    calls = len(runner.bus.calls)
    for _ in range(3):                                                # evicted and re-run from the start, as Cloudflare does
        runner.advance(run)
    assert len(runner.bus.calls) == calls and provider.calls["submit"] == 1
    assert runner.trace.count("transmit") == 1
    # Polls on the schedule: nothing until the first interval, one poll per interval, acknowledged on the second.
    first, second = expand(plan()["poll"]["schedule"])[:2]
    clock.advance(first)
    runner.tick()
    assert provider.calls["acknowledgement"] == 1 and runner.get(iid).status == "sleeping"
    clock.advance(second)
    runner.tick()
    run = runner.get(iid)
    assert run.status == "complete" and run.result == {"outcome": "accepted", "submission_id": subs["US-FED"]["id"]}
    assert R.status(rid).status == "accepted" and events(R, rid)[-1] == "ack_accepted"
    assert Filing(R).summary(rid)["complete"] is True


def test_crash_after_commit_is_retried_without_a_second_send(fam, monkeypatch):  # noqa: F811
    """Q03: the domain transmitted and committed; the reply to the orchestrator was lost."""
    provider = MockProvider()
    R, rid, subs, runner, clock, iid = start(fam, monkeypatch, provider=provider, faults={"transmit": [CrashAfterCommit()]})
    run = runner.get(iid)
    assert run.status == "sleeping" and run.steps["transmit"]["state"] == "retrying"
    assert provider.calls["submit"] == 1 and R.status(rid).status == "transmitted"
    clock.advance(RETRY_DELAY)
    runner.tick()
    run = runner.get(iid)
    assert run.steps["transmit"]["state"] == "done" and run.steps["transmit"]["result"]["status"] == "transmitted"
    assert provider.calls["submit"] == 1
    assert events(R, rid).count("activity_started:transmit") == 1 and events(R, rid).count("transmit") == 1
    assert R.wf.verify(rid) and R.wf.verify(subs["US-FED"]["id"])


def test_provider_unknown_then_lookup_finds_it(fam, monkeypatch):  # noqa: F811
    """The transmitter received the return and never answered: no second send; the retry stops in `unknown`, and the
    lookup by the planned SubmissionId records it as transmitted (reconciled by the provider's own record)."""
    provider = MockProvider()
    R, rid, subs, runner, clock, iid = start(fam, monkeypatch, provider=provider, faults={"transmit": [ProviderUnknown()]})
    fed = subs["US-FED"]["id"]
    run = runner.get(iid)
    assert run.steps["transmit"]["state"] == "retrying" and "did not answer" in run.steps["transmit"]["error"]
    assert provider.calls["submit"] == 1 and R.status(rid).status == "release_approved"
    assert events(R, rid)[-1] == "activity_started:transmit"
    clock.advance(RETRY_DELAY)
    runner.tick()
    run = runner.get(iid)
    assert run.steps["transmit"]["result"]["status"] == "unknown" and provider.calls["submit"] == 1
    assert R.status(rid).status == "unknown" and Filing(R).get(fed)["status"] == "unknown"
    assert run.status == "sleeping" and run.steps["lookup-wait-1"]["state"] == "sleeping"
    clock.advance(plan()["lookup"]["intervals_s"][0])
    runner.tick()
    run = runner.get(iid)
    assert run.steps["lookup-1"]["result"] == {**run.steps["lookup-1"]["result"], "status": "transmitted", "found": True}
    assert provider.calls["lookup"] == 1 and provider.calls["submit"] == 1
    st = R.status(rid)
    assert st.status == "transmitted" and st.facts["submission_id"] == subs["US-FED"]["planned_submission_id"]
    assert events(R, rid)[-1] == "reconciled_submitted" and Filing(R).get(fed)["status"] == "transmitted"
    assert run.steps["poll-wait-1"]["state"] == "sleeping"           # on to the acknowledgement


class LostProvider(MockProvider):
    """Receives nothing it could later find: the request died on the way."""

    def submit(self, package, submission_id):
        self.calls["submit"] += 1
        raise TimeoutError("the transmitter did not answer")


def test_lookup_never_finds_it_a_person_reconciles(fam, monkeypatch):  # noqa: F811
    """Q23: nothing is found by the deadline; a person is asked; the instance waits for the reconciliation and
    resumes on the signal. Never received: the submission goes back to the queue under a new planned id and the
    return back to signed; nothing is sent by the workflow."""
    provider = LostProvider()
    R, rid, subs, runner, clock, iid = start(fam, monkeypatch, provider=provider)
    fed = subs["US-FED"]["id"]
    clock.advance(RETRY_DELAY)
    runner.tick()
    assert R.status(rid).status == "unknown"
    for delay in plan()["lookup"]["intervals_s"]:
        clock.advance(delay)
        runner.tick(fam.conn)
    run = runner.get(iid)
    assert provider.calls["submit"] == 1 and provider.calls["lookup"] == len(plan()["lookup"]["intervals_s"])
    assert run.steps["notify-unknown"]["result"]["task_id"] and run.status == "waiting" and run.waiting_for == "submission-reconciled"
    assert count(fam.conn, "SELECT count(*) FROM tasks WHERE source = 'filing' AND status = 'open'") == 1
    assert count(fam.conn, "SELECT count(*) FROM outbox WHERE event_type = 'filing.attention'") == 1
    clock.advance(3600)
    assert runner.tick(fam.conn)["advanced"] == 0                     # parked: nothing to do until a person decides
    planned_before = Filing(R).get(fed)["planned_submission_id"]
    st = R.reconcile_transmission(rid, "lee", "cpa", submitted=False, evidence="the transmitter has no record of the planned id")
    assert st.status == "signed"
    out = runner.tick(fam.conn)                                       # the relay signals the waiting instance, which resumes
    assert out["relayed"] >= 1
    run = runner.get(iid)
    assert run.status == "complete" and run.result["outcome"] == "reconciled_not_submitted"
    sub = Filing(R).get(fed)
    assert sub["status"] == "queued" and sub["attempt"] == 2 and sub["planned_submission_id"] != planned_before
    assert provider.calls["submit"] == 1
    # The second release starts a second instance (a new id: the first is complete) and transmits once more, under
    # the new planned id, with a new activity key on the return's stream.
    good = MockProvider()
    runner2 = local_runner(fam.conn, R, good, clock=clock, runs=runner.runs)
    R.approve_release(rid, "lee", "cpa", efile_ready=True, jurisdictions=["US-FED"])
    runner2.tick(fam.conn)
    assert runner2.get(instance_id_for({"submission_id": fed, "attempt": 2})).steps["transmit"]["result"]["status"] == "transmitted"
    assert good.calls["submit"] == 1 and R.status(rid).status == "transmitted"
    ev = events(R, rid)
    assert ev.count("activity_started:transmit") == 2 and ev.index("reconciled_not_submitted") > ev.index("activity_started:transmit")
    assert R.wf.verify(rid)


def test_reconciled_as_received_resumes_into_polling(fam, monkeypatch):  # noqa: F811
    provider = LostProvider()
    R, rid, subs, runner, clock, iid = start(fam, monkeypatch, provider=provider)
    clock.advance(RETRY_DELAY)
    runner.tick()
    for delay in plan()["lookup"]["intervals_s"]:
        clock.advance(delay)
        runner.tick(fam.conn)
    assert runner.get(iid).status == "waiting"
    R.reconcile_transmission(rid, "lee", "cpa", submitted=True, submission_id="00000020263000000777", evidence="transmitter receipt")
    runner.tick(fam.conn)
    run = runner.get(iid)
    assert run.steps["after-reconcile-1"]["result"]["status"] == "transmitted" and run.steps["poll-wait-1"]["state"] == "sleeping"
    assert R.status(rid).status == "transmitted" and R.status(rid).facts["submission_id"] == "00000020263000000777"
    assert Filing(R).get(subs["US-FED"]["id"])["provider_submission_id"] == "00000020263000000777"


def test_unacknowledged_submission_tells_a_person_and_keeps_polling(fam, monkeypatch):  # noqa: F811
    provider = MockProvider(ack_after=10 ** 6)
    R, rid, subs, runner, clock, iid = start(fam, monkeypatch, provider=provider)
    schedule = expand(plan()["poll"]["schedule"])
    for delay in schedule:
        clock.advance(delay)
        runner.tick()
    run = runner.get(iid)
    assert provider.calls["acknowledgement"] == len(schedule) and run.steps["notify-unacknowledged"]["result"]["task_id"]
    assert sum(schedule) == 72 * 3600 and run.status == "sleeping"
    provider.ack_after = provider.calls["acknowledgement"] + 1
    clock.advance(plan()["poll"]["after_notice"]["every_s"])
    runner.tick()
    assert runner.get(iid).status == "complete" and R.status(rid).status == "accepted"


def test_rejection_is_recorded_and_a_person_told(fam, monkeypatch):  # noqa: F811
    provider = MockProvider()
    R, rid, subs, runner, clock, iid = start(fam, monkeypatch, provider=provider, faults={"poll-ack-1": [Reject(["IND-031-04"])]})
    clock.advance(expand(plan()["poll"]["schedule"])[0])
    runner.tick()
    run = runner.get(iid)
    assert run.status == "complete" and run.result["outcome"] == "rejected"
    assert R.status(rid).status == "rejected" and events(R, rid)[-1] == "ack_rejected"
    assert Filing(R).get(subs["US-FED"]["id"])["rejection_codes"] == ["IND-031-04"]
    assert run.steps["notify-rejected"]["result"]["task_id"]
    assert R.wf.send(rid, "correct", "lee", role="cpa").status == "preparing"      # today's path for a federal rejection


def test_runs_persist_in_the_store_and_a_new_runner_continues(fam, monkeypatch):  # noqa: F811
    provider = MockProvider()
    R, rid, subs, runner, clock, iid = start(fam, monkeypatch, provider=provider, runs=DbRuns(fam.conn))
    assert count(fam.conn, "SELECT count(*) FROM workflow_runs WHERE status = 'sleeping'") == 1
    clock.advance(expand(plan()["poll"]["schedule"])[0])
    again = local_runner(fam.conn, R, provider, clock=clock)         # another process picks the same store up
    again.tick(fam.conn)
    assert again.get(iid).status == "complete" and R.status(rid).status == "accepted"
    row = fam.conn.execute("SELECT status, result FROM workflow_runs WHERE instance_id = ?", (iid,)).fetchone()
    assert row["status"] == "complete" and json.loads(row["result"])["outcome"] == "accepted"
    again.run_until_idle(fam.conn)                                      # the acceptance emitted during that tick is relayed next
    assert not outbox.undelivered(fam.conn)                            # everything the domain emitted was relayed


def _templates():
    return [re.compile("^" + re.escape(t).replace(r"\{n\}", r"\d+") + "$") for t in plan()["steps"]]


def assert_trace(trace):
    """Every step name is one of steps.json's, and the templates appear in the file's order."""
    templates = _templates()
    order = []
    for name in trace:
        idx = next((i for i, t in enumerate(templates) if t.match(name)), None)
        assert idx is not None, f"{name} is not a step of flows/steps.json"
        if idx not in order:
            order.append(idx)
    assert order == sorted(order), f"steps out of the order steps.json gives: {trace}"


def test_step_names_come_from_steps_json(fam, monkeypatch):  # noqa: F811
    provider = LostProvider()
    R, rid, subs, runner, clock, iid = start(fam, monkeypatch, provider=provider)
    clock.advance(RETRY_DELAY)
    runner.tick()
    for delay in plan()["lookup"]["intervals_s"]:
        clock.advance(delay)
        runner.tick(fam.conn)
    R.reconcile_transmission(rid, "lee", "cpa", submitted=True, submission_id="00000020263000000778", evidence="receipt")
    runner.tick(fam.conn)
    clock.advance(expand(plan()["poll"]["schedule"])[0])
    runner.tick()
    assert_trace(runner.trace)
    assert runner.trace[:3] == ["transmit", "lookup-wait-1", "lookup-1"] and runner.trace[-2:] == ["poll-wait-1", "poll-ack-1"]
    assert "notify-unknown" in runner.trace and "wait-reconciled-1" in runner.trace and "after-reconcile-1" in runner.trace
    # The Cloudflare Workflow takes its names from the same file.
    ts = REPO / "edge" / "src" / "workflows" / "submission.ts"
    assert ts.exists() and "steps.json" in ts.read_text(encoding="utf-8")
    text = ts.read_text(encoding="utf-8")
    names = re.findall(r"step\.(?:do|sleep|waitForEvent)\(\s*[`\"]([^`\"]+)[`\"]", text)         # steps named in place
    names += re.findall(r"run\(\"[a-z_]+\",\s*[`\"]([^`\"]+)[`\"]", text)                        # steps named through run()
    assert {"transmit", "mark-unknown", "notify-unknown", "notify-unacknowledged", "notify-rejected"} <= set(names)
    assert any(n.startswith("lookup-wait-") for n in names) and any(n.startswith("poll-ack-") for n in names)
    for literal in names:
        assert any(t.match(literal.replace("${n}", "1")) for t in _templates()), literal
