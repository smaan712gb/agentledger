"""Domain commands with receipts (backlog F-08, ADR-0003; Q02/Q03 for the filing commands).

A retried command replays its recorded result; the same key with another payload or principal conflicts; the
receipt, the domain events and the outbox rows commit together or not at all; concurrent retries have one effect; a
command whose effect leaves the system converges after a crash between the effect and the receipt, with one send.
The outbox: the app role writes it only through `emit` (a function on PostgreSQL), and delivery marks are idempotent.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_return_workflow import fam, household  # noqa: F401  (fam is a fixture)

from agentledger import coverage, db
from agentledger.db import CommandConflict, count, open_store
from agentledger.returns.filing import Filing, MockProvider
from agentledger.returns.store import Returns
from agentledger.workflow import commands, outbox
from agentledger.workflow.commands import Command, Context, NotPermitted
from agentledger.workflow.engine import TransitionError

NEC_NOTE = "issued in error; the payer is sending a corrected 1099"


def filing_approved(monkeypatch):
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})


def released(fam, monkeypatch, jurisdictions=("US-FED", "US-CA")):  # noqa: F811
    """A reviewed, signed return whose release a CPA approved for the given jurisdictions."""
    filing_approved(monkeypatch)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    R.confirm(rid, None, "maya")
    R.submit_for_review(rid, "maya")
    h = R.approve(rid, "lee", "cpa").facts["approved_hash"]
    R.request_signature(rid, "lee", "cpa")
    R.record_signature(rid, "taxpayer", method="kba_esign", return_hash=h, kba_transaction_id="kba-1")
    st = R.approve_release(rid, "lee", "cpa", efile_ready=True, jurisdictions=list(jurisdictions))
    assert st.status == "release_approved"
    subs = {s["jurisdiction"]: s for s in Filing(R).for_return(rid)}
    return R, rid, subs


def cmd(name, key, payload, actor="workflow:test", role="system"):
    return Command(name, key, payload, actor, role)


def test_replay_returns_the_recorded_result(fam, monkeypatch):  # noqa: F811
    R, rid, subs = released(fam, monkeypatch)
    ctx = Context(fam.conn, R, MockProvider())
    sub = subs["US-FED"]["id"]
    first = commands.dispatch(ctx, cmd("notify_operator", "n-1", {"submission_id": sub, "reason": "no acknowledgement"}))
    again = commands.dispatch(ctx, cmd("notify_operator", "n-1", {"submission_id": sub, "reason": "no acknowledgement"}))
    assert first.replayed is False and again.replayed is True and again.result == first.result
    assert first.result["task_id"] and count(fam.conn, "SELECT count(*) FROM tasks WHERE source = 'filing'") == 1
    assert count(fam.conn, f"SELECT count(*) FROM {commands.receipt_table(fam.conn)} WHERE scope = 'client:rivera'") == 1
    # Another reason is another command (its own key), and its task is deduplicated by (submission, reason).
    second = commands.dispatch(ctx, cmd("notify_operator", "n-2", {"submission_id": sub, "reason": "no acknowledgement"}))
    assert second.replayed is False and second.result["task_id"] is None


def test_same_key_other_payload_or_principal_conflicts(fam, monkeypatch):  # noqa: F811
    R, rid, subs = released(fam, monkeypatch)
    ctx = Context(fam.conn, R, MockProvider())
    sub = subs["US-FED"]["id"]
    commands.dispatch(ctx, cmd("notify_operator", "n-1", {"submission_id": sub, "reason": "a"}))
    with pytest.raises(CommandConflict):
        commands.dispatch(ctx, cmd("notify_operator", "n-1", {"submission_id": sub, "reason": "b"}))
    with pytest.raises(CommandConflict):
        commands.dispatch(ctx, cmd("notify_operator", "n-1", {"submission_id": sub, "reason": "a"}, actor="workflow:other"))
    with pytest.raises(CommandConflict):
        commands.dispatch(ctx, cmd("notify_operator", "n-1", {"submission_id": sub, "reason": "a"}, actor="lee", role="cpa"))
    assert count(fam.conn, "SELECT count(*) FROM tasks WHERE source = 'filing'") == 1


def _counts(conn, sub):
    return (count(conn, "SELECT count(*) FROM workflow_events WHERE workflow_id = ?", sub),
            count(conn, "SELECT count(*) FROM outbox WHERE aggregate_id = ?", sub),
            count(conn, f"SELECT count(*) FROM {commands.receipt_table(conn)} WHERE scope = 'client:rivera'"))


def test_receipt_events_and_outbox_commit_together_or_not_at_all(fam, monkeypatch):  # noqa: F811
    R, rid, subs = released(fam, monkeypatch, jurisdictions=("US-FED",))
    provider = MockProvider()
    ctx = Context(fam.conn, R, provider)
    sub = subs["US-FED"]["id"]
    commands.dispatch(ctx, cmd("transmit_submission", "t-1", {"submission_id": sub}))
    before = _counts(fam.conn, sub)

    def boom():
        raise RuntimeError("disk full while writing the receipt")

    monkeypatch.setattr(commands.audit, "now", boom)          # between the handler and the receipt insert
    with pytest.raises(RuntimeError):
        commands.dispatch(ctx, cmd("record_ack", "a-1", {"submission_id": sub, "status": "accepted", "codes": []}))
    assert _counts(fam.conn, sub) == before                      # nothing of the acknowledgement survived
    assert Filing(R).get(sub)["status"] == "transmitted" and R.status(rid).status == "transmitted"
    monkeypatch.undo()
    filing_approved(monkeypatch)
    out = commands.dispatch(ctx, cmd("record_ack", "a-1", {"submission_id": sub, "status": "accepted", "codes": []}))
    events, events_out, receipts = _counts(fam.conn, sub)
    assert (events, events_out, receipts) == (before[0] + 1, before[1] + 1, before[2] + 1)
    assert out.result["status"] == "accepted" and R.status(rid).status == "accepted"
    assert [e["event"] for e in R.status(rid).history][-1] == "ack_accepted"       # records.py counts from this


def test_refusals_leave_no_receipt(fam, monkeypatch):  # noqa: F811
    R, rid, subs = released(fam, monkeypatch)
    ctx = Context(fam.conn, R, MockProvider())
    state = subs["US-CA"]["id"]
    with pytest.raises(TransitionError, match="after the federal return is accepted"):
        commands.dispatch(ctx, cmd("transmit_submission", "t-ca", {"submission_id": state}))
    assert count(fam.conn, f"SELECT count(*) FROM {commands.receipt_table(fam.conn)} WHERE scope = 'client:rivera'") == 0
    assert ctx.provider.calls["submit"] == 0
    with pytest.raises(KeyError):
        commands.dispatch(ctx, cmd("transmit_submission", "t-x", {"submission_id": "sub_missing"}))
    with pytest.raises(KeyError):
        commands.dispatch(ctx, cmd("no_such_command", "k", {"submission_id": state}))


def test_human_decisions_are_refused_for_the_system(fam, monkeypatch):  # noqa: F811
    R, rid, subs = released(fam, monkeypatch)
    ctx = Context(fam.conn, R, MockProvider())
    for name, payload in (("approve_release", {"return_id": rid}), ("reconcile_submission", {"submission_id": subs["US-FED"]["id"]}),
                          ("retransmit", {"submission_id": subs["US-CA"]["id"]}), ("void_return", {"return_id": rid})):
        assert commands.registry()[name].human
        with pytest.raises(NotPermitted):
            commands.dispatch(ctx, cmd(name, "k", payload))
    assert set(commands.system_commands()) == {"transmit_submission", "lookup_submission", "poll_acks", "record_ack", "mark_unknown",
                                                "notify_operator"}
    # A person may run them, and the domain's own guards still apply (nothing is unknown yet).
    with pytest.raises(TransitionError, match="not unknown"):
        commands.dispatch(ctx, cmd("reconcile_submission", "r-1", {"submission_id": subs["US-FED"]["id"], "submitted": True},
                                   actor="lee", role="cpa"))
    with pytest.raises(TransitionError, match="needs a CPA"):
        commands.dispatch(ctx, cmd("retransmit", "r-2", {"submission_id": subs["US-CA"]["id"]}, actor="sam", role="staff"))


def test_transmit_crash_between_effect_and_receipt_converges_with_one_send(fam, monkeypatch):  # noqa: F811
    """Q03 for the filing command: the provider received the return, the receipt was never written (a crash, a lost
    reply). The retry finds the recorded transmission and returns it; the provider is not called again."""
    R, rid, subs = released(fam, monkeypatch, jurisdictions=("US-FED",))
    provider = MockProvider()
    ctx = Context(fam.conn, R, provider)
    sub = subs["US-FED"]["id"]
    real = commands.registry()["transmit_submission"].handler
    crashed = {"n": 0}

    def crash_once(c, command, prepared):
        if not crashed["n"]:
            crashed["n"] += 1
            raise ConnectionError("reply lost")
        return real(c, command, prepared)

    monkeypatch.setitem(commands.COMMANDS, "transmit_submission", commands.Spec("transmit_submission", crash_once, commands.COMMANDS["transmit_submission"].scope,
                                                                                 prepare=commands.COMMANDS["transmit_submission"].prepare))
    with pytest.raises(ConnectionError):
        commands.dispatch(ctx, cmd("transmit_submission", "t-1", {"submission_id": sub}))
    assert provider.calls["submit"] == 1 and Filing(R).get(sub)["status"] == "transmitted"
    out = commands.dispatch(ctx, cmd("transmit_submission", "t-1", {"submission_id": sub}))
    assert out.replayed is False and out.result["status"] == "transmitted" and provider.calls["submit"] == 1
    assert out.result["provider_submission_id"] == subs["US-FED"]["planned_submission_id"]
    again = commands.dispatch(ctx, cmd("transmit_submission", "t-1", {"submission_id": sub}))
    assert again.replayed is True and provider.calls["submit"] == 1
    assert R.status(rid).status == "transmitted" and R.status(rid).facts["submission_id"] == subs["US-FED"]["planned_submission_id"]


def test_concurrent_retries_of_one_command_have_one_effect(fam, monkeypatch):  # noqa: F811
    R, rid, subs = released(fam, monkeypatch)
    sub = subs["US-FED"]["id"]
    shared = open_store(fam.paths.db)                # one connection per thread on the same firm store

    def attempt(i):
        rs = Returns(shared, fam.kb)
        return commands.dispatch(Context(shared, rs, MockProvider()),
                                 cmd("notify_operator", "n-1", {"submission_id": sub, "reason": "no acknowledgement"}))

    with ThreadPoolExecutor(12) as ex:
        results = list(ex.map(attempt, range(12)))
    assert len({r.result["task_id"] for r in results}) == 1 and sum(1 for r in results if not r.replayed) == 1
    assert count(fam.conn, "SELECT count(*) FROM tasks WHERE source = 'filing'") == 1
    assert count(fam.conn, "SELECT count(*) FROM outbox WHERE event_type = 'filing.attention'") == 1


def test_outbox_emit_paging_and_idempotent_delivery(fam):  # noqa: F811
    conn = fam.conn
    ids = [outbox.emit(conn, "rivera", "test.event", "thing", f"t{i}", {"n": i}) for i in range(5)]
    assert ids == sorted(ids) and len(set(ids)) == 5
    page = outbox.undelivered(conn, 0, 2)
    assert [e["aggregate_id"] for e in page] == ["t0", "t1"] and page[0]["payload"] == {"n": 0}
    assert [e["aggregate_id"] for e in outbox.undelivered(conn, page[-1]["id"], 100)] == ["t2", "t3", "t4"]
    assert outbox.mark_delivered(conn, ids[1]) is True
    assert outbox.mark_delivered(conn, ids[1]) is False
    assert [e["aggregate_id"] for e in outbox.undelivered(conn, 0, 100)] == ["t0", "t2", "t3", "t4"]
    with pytest.raises(KeyError):
        outbox.mark_delivered(conn, 10 ** 9)
    with pytest.raises(sqlite3.DatabaseError):            # append-only on both backends (db errors subclass sqlite3's)
        conn.execute("UPDATE outbox SET payload = '{}' WHERE id = ?", (ids[0],))


@pytest.mark.skipif(db.backend() != "postgres", reason="the application role's privileges exist on PostgreSQL")
def test_app_role_writes_the_outbox_only_through_emit_event(fam):  # noqa: F811
    with pytest.raises(db.DatabaseError):
        fam.conn.execute("INSERT INTO outbox (client_id, event_type, aggregate, aggregate_id, payload) VALUES ('rivera', 'x', 'x', 'x', '{}')")
    assert outbox.emit(fam.conn, "rivera", "x", "x", "x", {}) > 0
    with pytest.raises(PermissionError):                  # a client outside the session's scope is refused by the function
        fam.conn.set_scope(["someone-else"])
        try:
            outbox.emit(fam.conn, "rivera", "x", "x", "y", {})
        finally:
            fam.conn.set_scope(["*"])
