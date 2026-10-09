"""The internal endpoint for the workflow runtime (backlog F-08): the credential, what the workflow may and may not
run, the status codes it maps to retry or stop, the outbox cursor and delivery marks, and the response marker that
makes the edge relay at once.
"""

from __future__ import annotations

import importlib
import secrets

import pytest
from test_return_workflow import household
from test_workflow_commands import filing_approved

from agentledger.returns.filing import Filing

TOKEN = "wf-" + secrets.token_urlsafe(32)
SMOKE = "smoke-" + secrets.token_urlsafe(32)
WF = {"Authorization": f"Bearer {TOKEN}", "X-AgentLedger-Workflow": "1"}


@pytest.fixture
def api(home, monkeypatch):
    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))
    monkeypatch.setenv("AGENTLEDGER_AGENTS", "0")
    monkeypatch.setenv("AGENTLEDGER_DEV_AUTH", "1")
    monkeypatch.setenv("AGENTLEDGER_WORKFLOW_TOKEN", TOKEN)
    monkeypatch.setenv("AGENTLEDGER_SMOKE_TOKEN", SMOKE)
    monkeypatch.delenv("AGENTLEDGER_MEF_PROVIDER", raising=False)
    import agentledger.api.app as api_mod

    importlib.reload(api_mod)
    from fastapi.testclient import TestClient

    return api_mod, TestClient(api_mod.app)


def released(api_mod, monkeypatch, jurisdictions=("US-FED", "US-CA")):
    """A signed, released return in the dev firm, entered by hand."""
    from agentledger.ledger import store

    filing_approved(monkeypatch)
    user = next(u for u in api_mod.users().values() if u["token"] == "dev-cpa")
    R = api_mod.R(user)
    store.add_client(R.conn, id="rivera", name="Alex Rivera", kind="individual", emails=[], tax_id_last4="0001", domain="general")
    rid = R.create("rivera", 2026, "maya", household())
    R.compute(rid, "maya")
    R.confirm(rid, None, "maya")
    R.submit_for_review(rid, "maya")
    h = R.approve(rid, "lee", "cpa").facts["approved_hash"]
    R.request_signature(rid, "lee", "cpa")
    R.record_signature(rid, "taxpayer", method="wet_signature", return_hash=h)
    R.approve_release(rid, "lee", "cpa", efile_ready=True, jurisdictions=list(jurisdictions))
    return R, rid, {s["jurisdiction"]: s for s in Filing(R).for_return(rid)}


def envelope(name, key, payload, instance="filing-sub-1", firm="dev"):
    return {"firm_id": firm, "name": name, "idempotency_key": key, "payload": payload, "instance_id": instance}


def test_only_the_workflow_token_opens_the_door(api, monkeypatch):
    mod, c = api
    body = envelope("notify_operator", "k", {"submission_id": "sub_x", "reason": "x"})
    assert c.post("/internal/commands", json=body).status_code == 403
    assert c.post("/internal/commands", json=body, headers={"Authorization": f"Bearer {TOKEN[:-1]}x"}).status_code == 403
    assert c.post("/internal/commands", json=body, headers={"Authorization": f"Bearer {SMOKE}"}).status_code == 403
    assert c.post("/internal/commands", json=body, headers={"Authorization": "Bearer dev-cpa"}).status_code == 403
    assert c.get("/internal/outbox?firm_id=dev", headers={"Authorization": f"Bearer {SMOKE}"}).status_code == 403
    assert c.get("/internal/returns/r/filing?firm_id=dev").status_code == 403
    assert c.post("/internal/outbox/1/delivered", json={"firm_id": "dev"}).status_code == 403
    short = "short-token-0123456789"                   # under 32 characters: the door stays shut, even with the right value
    monkeypatch.setenv("AGENTLEDGER_WORKFLOW_TOKEN", short)
    assert c.get("/internal/outbox?firm_id=dev", headers={"Authorization": f"Bearer {short}"}).status_code == 403
    monkeypatch.delenv("AGENTLEDGER_WORKFLOW_TOKEN")
    assert c.get("/internal/outbox?firm_id=dev", headers=WF).status_code == 403
    # Not in the public API description, and never a 401 (the sweep in test_health_and_smoke expects 403/404).
    assert "/internal/commands" not in c.get("/openapi.json").json()["paths"]


def test_commands_codes_and_what_the_workflow_may_run(api, monkeypatch):
    mod, c = api
    R, rid, subs = released(mod, monkeypatch)
    fed, ca = subs["US-FED"]["id"], subs["US-CA"]["id"]
    # A person's decisions are refused before any firm is opened.
    for name, payload in (("approve_release", {"return_id": rid}), ("reconcile_submission", {"submission_id": fed, "submitted": True}),
                          ("retransmit", {"submission_id": ca}), ("void_return", {"return_id": rid})):
        r = c.post("/internal/commands", json=envelope(name, "k", payload), headers=WF)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "human_only", r.text
    assert c.post("/internal/commands", json=envelope("no_such", "k", {}), headers=WF).status_code == 404
    assert c.post("/internal/commands", json=envelope("notify_operator", "", {"submission_id": fed}), headers=WF).status_code == 422
    r = c.post("/internal/commands", json=envelope("notify_operator", "k", {"submission_id": fed, "reason": "x"}, firm="nope"), headers=WF)
    assert r.status_code == 404
    # The domain refused: 409 transition_refused, final for the step. Nothing was receipted.
    r = c.post("/internal/commands", json=envelope("transmit_submission", "t-ca", {"submission_id": ca}), headers=WF)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "transition_refused" and "federal" in r.json()["detail"]["detail"]
    # No transmitter is configured here: the system transmit refuses, a person is not bypassed.
    r = c.post("/internal/commands", json=envelope("transmit_submission", "t-fed", {"submission_id": fed}), headers=WF)
    assert r.status_code == 409 and "no transmitter" in r.json()["detail"]["detail"]
    assert R.status(rid).status == "release_approved"
    # A system command: recorded once, replayed on retry, conflicting when the envelope changes.
    body = envelope("notify_operator", "n-1", {"submission_id": fed, "reason": "no acknowledgement"})
    first = c.post("/internal/commands", json=body, headers=WF)
    assert first.status_code == 200 and first.json()["replayed"] is False and first.json()["result"]["task_id"], first.text
    assert first.headers.get("X-AgentLedger-Outbox") == "1"                   # emitted: the edge relays at once
    again = c.post("/internal/commands", json=body, headers=WF)
    assert again.json()["replayed"] is True and again.json()["result"] == first.json()["result"]
    assert "X-AgentLedger-Outbox" not in again.headers                          # a replay emits nothing
    r = c.post("/internal/commands", json={**body, "payload": {"submission_id": fed, "reason": "other"}}, headers=WF)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "command_conflict"
    r = c.post("/internal/commands", json={**body, "instance_id": "filing-sub-2"}, headers=WF)   # another principal
    assert r.status_code == 409 and r.json()["detail"]["code"] == "command_conflict"
    r = c.post("/internal/commands", json=envelope("notify_operator", "n-2", {"submission_id": "sub_missing", "reason": "x"}), headers=WF)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
    # The firms the relay polls, and the picture the Workflow reads before deciding.
    assert c.get("/internal/firms", headers=WF).json() == {"firms": ["dev"]}
    assert c.get("/internal/firms").status_code == 403
    r = c.get(f"/internal/returns/{rid}/filing?firm_id=dev", headers=WF)
    assert r.status_code == 200 and r.json()["status"] == "release_approved" and len(r.json()["submissions"]) == 2
    assert c.get("/internal/returns/ret_missing/filing?firm_id=dev", headers=WF).status_code == 404


def test_outbox_cursor_paging_and_delivery_marks(api, monkeypatch):
    mod, c = api
    R, rid, subs = released(mod, monkeypatch)
    r = c.get("/internal/outbox?firm_id=dev&after=0&limit=1", headers=WF)
    assert r.status_code == 200, r.text
    page = r.json()
    assert len(page["events"]) == 1 and page["events"][0]["event_type"] == "submission.queued" and page["next"] == page["events"][0]["id"]
    first = page["events"][0]
    assert first["payload"]["submission_id"] == subs["US-FED"]["id"] and first["payload"]["linked_to"] is None
    assert "X-AgentLedger-Outbox" not in r.headers
    rest = c.get(f"/internal/outbox?firm_id=dev&after={page['next']}&limit=100", headers=WF).json()
    assert [e["payload"]["jurisdiction"] for e in rest["events"]] == ["US-CA"] and rest["events"][0]["payload"]["linked_to"] == subs["US-FED"]["id"]
    assert rest["events"][0]["payload"]["linked_accepted"] is False
    r = c.post(f"/internal/outbox/{first['id']}/delivered", json={"firm_id": "dev"}, headers=WF)
    assert r.status_code == 200 and r.json() == {"id": first["id"], "delivered": True}
    assert c.post(f"/internal/outbox/{first['id']}/delivered", json={"firm_id": "dev"}, headers=WF).json()["delivered"] is False
    assert [e["id"] for e in c.get("/internal/outbox?firm_id=dev", headers=WF).json()["events"]] == [rest["events"][0]["id"]]
    assert c.post("/internal/outbox/999999/delivered", json={"firm_id": "dev"}, headers=WF).status_code == 404
    assert c.get("/internal/outbox?firm_id=dev&limit=0", headers=WF).status_code == 422
    # Payloads carry ids, hashes and statuses: nothing a person entered.
    for e in [first] + rest["events"]:
        assert set(e["payload"]) <= {"submission_id", "return_id", "jurisdiction", "attempt", "linked_to", "linked_accepted"}


def test_the_mock_transmitter_files_through_the_endpoint_in_dev_only(api, monkeypatch):
    mod, c = api
    monkeypatch.setenv("AGENTLEDGER_MEF_PROVIDER", "mock")
    R, rid, subs = released(mod, monkeypatch, jurisdictions=("US-FED",))
    fed = subs["US-FED"]["id"]
    r = c.post("/internal/commands", json=envelope("transmit_submission", "t-1", {"submission_id": fed}), headers=WF)
    assert r.status_code == 200 and r.json()["result"]["status"] == "transmitted", r.text
    assert R.status(rid).status == "transmitted" and R.status(rid).facts["submission_id"] == subs["US-FED"]["planned_submission_id"]
    r = c.post("/internal/commands", json=envelope("poll_acks", "p-1", {"submission_id": fed}), headers=WF)
    assert r.status_code == 200 and r.json()["result"]["status"] == "accepted" and r.json()["result"]["acknowledged"] is True
    assert R.status(rid).status == "accepted" and R.filing_summary(rid)["complete"] is True
    # Outside the local demo the mock does not exist: the same variable yields no transmitter.
    monkeypatch.delenv("AGENTLEDGER_DEV_AUTH")
    from agentledger.returns.filing import provider_from_env

    assert provider_from_env() is None
