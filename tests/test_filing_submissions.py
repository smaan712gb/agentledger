"""Federal and state submissions of one return (backlog F-08; Q24: federal accepted, state rejected).

The state submission follows the federal acceptance; a state rejection leaves the return accepted and the state
submission rejected, with a task for a reviewer and the filing incomplete; a CPA retransmits the state (a new row,
a new instance, no second federal send); the federal return is never retransmitted; a federal rejection cancels the
linked state submission. The API exposes the release and the picture.
"""

from __future__ import annotations

import importlib

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)
from test_workflow_commands import filing_approved, released
from test_workflow_runner import start

from agentledger import audit
from agentledger.db import count, rows
from agentledger.ledger import store
from agentledger.returns.filing import FEDERAL, Filing, MockProvider, jurisdictions_of
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError
from agentledger.workflow.flows import expand, plan
from agentledger.workflow.runner import instance_id_for

POLL = expand(plan()["poll"]["schedule"])[0]


def test_jurisdictions_are_validated_and_federal_first():
    assert jurisdictions_of(None) == ["US-FED"]
    assert jurisdictions_of(["us-ca", "US-FED", "US-NY", "US-CA"]) == ["US-FED", "US-CA", "US-NY"]
    assert jurisdictions_of(["US-CA"]) == ["US-FED", "US-CA"]           # a state return follows the federal one
    for bad in (["CA"], ["US-CAL"], ["US-1A"], [""]):
        with pytest.raises(ValueError):
            jurisdictions_of(bad)


def test_release_needs_every_jurisdiction_cleared(fam, monkeypatch):  # noqa: F811
    from agentledger import coverage

    R, rid, subs = released(fam, monkeypatch, jurisdictions=("US-FED",))
    assert set(subs) == {"US-FED"} and subs["US-FED"]["status"] == "queued" and subs["US-FED"]["linked_to"] is None
    # A second return, released for a state the registry does not support: refused before anything is planned.
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None:
                        {"id": cap, "status": "unsupported" if jurisdiction == "US-NY" else "filing-approved"})
    rid2 = _signed(R, client="ortiz")
    with pytest.raises(TransitionError, match=r"coverage does not allow filing: state_ny is unsupported \(US-NY\)"):
        R.approve_release(rid2, "lee", "cpa", efile_ready=True, jurisdictions=["US-FED", "US-NY"])
    with pytest.raises(TransitionError, match="e-file is not enabled"):
        R.approve_release(rid2, "lee", "cpa", efile_ready=False)
    with pytest.raises(TransitionError, match="requires one of"):
        R.approve_release(rid2, "sam", "staff", efile_ready=True)
    assert R.status(rid2).status == "signed" and Filing(R).for_return(rid2) == []
    st = R.approve_release(rid2, "lee", "cpa", efile_ready=True, jurisdictions=["US-FED", "US-CA"])
    assert st.status == "release_approved" and st.facts["release_hash"] == st.facts["approved_hash"] == st.facts["signed_hash"]
    assert st.facts["submissions"]["jurisdictions"] == ["US-FED", "US-CA"]
    by = {s["jurisdiction"]: s for s in Filing(R).for_return(rid2)}
    assert by["US-CA"]["linked_to"] == by["US-FED"]["id"] and len(by["US-FED"]["planned_submission_id"]) == 20


def _signed(R, *, client: str) -> str:
    """A second client's signed return, entered by hand (no documents to account for)."""
    if not store.list_clients(R.conn) or not any(c["id"] == client for c in store.list_clients(R.conn)):
        store.add_client(R.conn, id=client, name=client.title(), kind="individual", emails=[], tax_id_last4="0009", domain="general")
    rid = R.create(client, 2026, "maya", household())
    R.compute(rid, "maya")
    for doc in R.unaccounted_documents(rid):
        R.account_for_document(rid, doc, "not_applicable", "not part of this return (test household)", "maya")
    R.confirm(rid, None, "maya")
    R.submit_for_review(rid, "maya")
    h = R.approve(rid, "lee", "cpa").facts["approved_hash"]
    R.request_signature(rid, "lee", "cpa")
    R.record_signature(rid, "taxpayer", method="wet_signature", return_hash=h)
    return rid


def test_federal_accepted_state_rejected_then_retransmitted(fam, monkeypatch):  # noqa: F811
    """Q24."""
    provider = MockProvider()
    R, rid, subs, runner, clock, fed_iid = start(fam, monkeypatch, provider=provider, jurisdictions=("US-FED", "US-CA"))
    F = Filing(R)
    fed, ca = subs["US-FED"], subs["US-CA"]
    # The federal instance transmitted; the linked state waits for the federal acknowledgement.
    assert provider.calls["submit"] == 1 and set(provider.received) == {fed["planned_submission_id"]}
    assert runner.get(instance_id_for(ca)) is None and F.get(ca["id"])["status"] == "queued"
    assert R.status(rid).status == "transmitted"
    clock.advance(POLL)
    runner.tick(fam.conn)                                             # the acknowledgement: accepted
    st = R.status(rid)
    assert st.status == "accepted" and [h["event"] for h in st.history][-1] == "ack_accepted"
    assert F.summary(rid)["complete"] is False                        # the state is still owed
    out = runner.tick(fam.conn)                                       # relays submission.accepted(US-FED): the state instance starts
    assert out["relayed"] >= 1 and runner.get(instance_id_for(ca)) is not None
    assert provider.calls["submit"] == 2 and F.get(ca["id"])["status"] == "transmitted"
    provider.decide(ca["planned_submission_id"], "rejected", ["CA-F540-0001", "CA-F540-0190"])
    clock.advance(POLL)
    runner.run_until_idle(fam.conn)
    ca_now = F.get(ca["id"])
    assert ca_now["status"] == "rejected" and ca_now["rejection_codes"] == ["CA-F540-0001", "CA-F540-0190"]
    assert R.status(rid).status == "accepted"                         # the federal filing stands
    tasks = rows(fam.conn, "SELECT * FROM tasks WHERE source = 'filing' AND status = 'open'")
    assert len(tasks) == 1 and tasks[0]["assignee"] == "cpa" and "US-CA return rejected" in tasks[0]["title"]
    summary = R.filing_summary(rid)
    assert summary["complete"] is False and summary["status"] == "accepted"
    assert {s["jurisdiction"]: s["status"] for s in summary["submissions"]} == {"US-FED": "accepted", "US-CA": "rejected"}
    assert count(fam.conn, "SELECT count(*) FROM outbox WHERE event_type = 'submission.rejected'") == 1
    # A CPA retransmits the state: a retransmission row, a new instance, one more state send, still one federal send.
    with pytest.raises(TransitionError, match="not retransmitted"):
        R.retransmit(fed["id"], "lee", "cpa")
    with pytest.raises(TransitionError, match="needs a CPA"):
        R.retransmit(ca["id"], "sam", "staff")
    new = R.retransmit(ca["id"], "lee", "cpa")
    assert new["kind"] == "retransmission" and new["supersedes"] == ca["id"] and new["status"] == "queued"
    assert new["linked_to"] == fed["id"] and new["planned_submission_id"] not in (fed["planned_submission_id"], ca["planned_submission_id"])
    assert F.get(ca["id"])["status"] == "superseded"
    with pytest.raises(TransitionError, match="only a rejected submission"):
        R.retransmit(ca["id"], "lee", "cpa")
    runner.tick(fam.conn)                                             # submission.queued (linked, federal accepted): starts at once
    assert runner.get(instance_id_for(new)) is not None and provider.calls["submit"] == 3
    assert [k for k in provider.received if k == fed["planned_submission_id"]] == [fed["planned_submission_id"]]
    clock.advance(POLL)
    runner.run_until_idle(fam.conn)
    summary = R.filing_summary(rid)
    assert {s["id"]: s["status"] for s in summary["submissions"]}[new["id"]] == "accepted" and summary["complete"] is True
    assert R.status(rid).status == "accepted"
    for wid in (rid, fed["id"], ca["id"], new["id"]):
        assert R.wf.verify(wid)


def test_federal_rejection_cancels_the_linked_state(fam, monkeypatch):  # noqa: F811
    provider = MockProvider()
    R, rid, subs, runner, clock, fed_iid = start(fam, monkeypatch, provider=provider, jurisdictions=("US-FED", "US-CA"))
    provider.decide(subs["US-FED"]["planned_submission_id"], "rejected", ["IND-181-01"])
    clock.advance(POLL)
    runner.run_until_idle(fam.conn)
    F = Filing(R)
    assert R.status(rid).status == "rejected" and F.get(subs["US-FED"]["id"])["status"] == "rejected"
    assert F.get(subs["US-CA"]["id"])["status"] == "cancelled" and provider.calls["submit"] == 1
    assert runner.get(instance_id_for(subs["US-CA"])) is None
    assert count(fam.conn, "SELECT count(*) FROM outbox WHERE event_type = 'submission.cancelled'") == 1
    # Today's path: the CPA corrects, the return is prepared, reviewed, signed and released again (new rows).
    assert R.wf.send(rid, "correct", "lee", role="cpa").status == "preparing"
    assert R.filing_summary(rid)["release"] is None


def test_reopening_cancels_queued_submissions(fam, monkeypatch):  # noqa: F811
    R, rid, subs = released(fam, monkeypatch, jurisdictions=("US-FED", "US-CA"))
    changed = {**R.latest(rid)["inputs"], "payments": {"estimated_tax_payments": "500"}}
    R.save_inputs(rid, changed, "maya")
    assert R.status(rid).status == "preparing"
    assert {s["status"] for s in Filing(R).for_return(rid)} == {"cancelled"}
    assert R.filing_summary(rid)["release"] is None and R.filing_summary(rid)["complete"] is False


def test_paper_filing_and_void_cancel_queued_submissions(fam, monkeypatch):  # noqa: F811
    R, rid, subs = released(fam, monkeypatch, jurisdictions=("US-FED",))
    st = R.mark_paper_filed(rid, "lee", "cpa", "mailed by certified mail on 2027-04-10, receipt 7019 0000 0000")
    assert st.status == "paper_filed" and Filing(R).get(subs["US-FED"]["id"])["status"] == "cancelled"
    assert R.filing_summary(rid)["complete"] is True
    rid2 = _signed(R, client="ortiz")
    R.approve_release(rid2, "lee", "cpa", efile_ready=True)
    assert R.void(rid2, "lee", "cpa", "filed with other software on 2027-04-10, transcript on file").status == "void"
    assert {s["status"] for s in Filing(R).for_return(rid2)} == {"cancelled"}


def test_cpa_direct_transmission_approves_the_release_and_plans_the_federal_submission(fam, monkeypatch):  # noqa: F811
    filing_approved(monkeypatch)
    R = Returns(fam.conn, fam.kb)
    rid = _signed(R, client="ortiz")
    st = R.transmit(rid, "lee", "cpa", efile_ready=True, submit=lambda key: {"submission_id": "00000020263000000001"})
    assert st.status == "transmitted" and st.facts["release_approved_by"] == "lee" and st.facts["release_hash"] == st.facts["approved_hash"]
    subs = Filing(R).for_return(rid)
    assert len(subs) == 1 and subs[0]["jurisdiction"] == FEDERAL and subs[0]["status"] == "transmitted"
    assert subs[0]["provider_submission_id"] == "00000020263000000001"
    summary = R.filing_summary(rid)
    assert summary["release"]["approved_by"] == "lee" and summary["release"]["jurisdictions"] == ["US-FED"]
    assert R.wf.verify(rid) and R.wf.verify(subs[0]["id"])


def test_release_and_filing_over_the_api(home, monkeypatch):
    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))
    monkeypatch.setenv("AGENTLEDGER_AGENTS", "0")
    monkeypatch.setenv("AGENTLEDGER_DEV_AUTH", "1")
    monkeypatch.setenv("AGENTLEDGER_EFILE_READY", "1")
    import agentledger.api.app as api_mod

    importlib.reload(api_mod)
    from fastapi.testclient import TestClient

    filing_approved(monkeypatch)
    c = TestClient(api_mod.app)
    cpa = {"Authorization": "Bearer dev-cpa"}
    jordan = {"Authorization": "Bearer dev-jordan"}
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=cpa).status_code == 200
    add_doc(api_mod.APP.conn, "d1", "jordan-lee", "W-2", {"employer_name": "Acme", "box1": "60000", "box2": "6000"})
    rid = c.post("/api/clients/jordan-lee/returns", json={"tax_year": 2026, "inputs": {
        "filing_status": "single", "taxpayer": {"first_name": "Jordan", "ssn": "400-00-0009", "dob": "1990-01-01"}}},
        headers=cpa).json()["id"]
    c.post(f"/api/returns/{rid}/populate", headers=cpa)
    c.post(f"/api/returns/{rid}/confirm", json={}, headers=cpa)
    assert c.post(f"/api/returns/{rid}/submit", headers=cpa).json()["status"] == "in_review"
    assert c.post(f"/api/returns/{rid}/approve", headers=cpa).json()["status"] == "approved"
    assert c.post(f"/api/returns/{rid}/release-approve", headers=cpa).status_code == 409        # not signed yet
    assert c.post(f"/api/returns/{rid}/request-signature", headers=cpa).json()["status"] == "awaiting_signature"
    user = next(u for u in api_mod.users().values() if u["token"] == "dev-cpa")
    R = api_mod.R(user)
    R.record_signature(rid, "jordan", method="wet_signature", return_hash=R.status(rid).facts["approved_hash"])
    assert c.post(f"/api/returns/{rid}/release-approve", json={"jurisdictions": ["US-FED"]}, headers=jordan).status_code == 403
    r = c.post(f"/api/returns/{rid}/release-approve", json={"jurisdictions": ["US-FED", "US-CA"]}, headers=cpa)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "release_approved" and r.json()["filing"]["release"]["jurisdictions"] == ["US-FED", "US-CA"]
    full = c.get(f"/api/returns/{rid}", headers=cpa).json()
    assert full["status"] == "release_approved" and full["filing"]["complete"] is False
    assert {s["jurisdiction"]: s["status"] for s in full["filing"]["submissions"]} == {"US-FED": "queued", "US-CA": "queued"}
    mine = c.get(f"/api/returns/{rid}", headers=jordan).json()
    assert mine["filing"]["status"] == "release_approved" and "inputs" not in mine
    assert c.post(f"/api/returns/{rid}/release-approve", json={"jurisdictions": ["US-XX"]}, headers=cpa).status_code == 409
    assert c.post(f"/api/returns/{rid}/retransmit", json={"submission_id": full["filing"]["submissions"][0]["id"]}, headers=cpa).status_code == 409
    assert c.post(f"/api/returns/{rid}/retransmit", json={}, headers=cpa).status_code == 404
    assert "return.release_approved" in [e["action"] for e in audit.events(api_mod.APP.conn, "jordan-lee")]
    assert "return.release-approve" in [e["action"] for e in audit.events(api_mod.APP.conn)]   # the API's own record, as for every action
