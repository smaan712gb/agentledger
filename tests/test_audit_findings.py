"""Regression tests for the external audit of commit aa78019 (2026-10-08). Each test reproduces a finding as reported,
and must stay green: the guarantee it names is now enforced by code, not described in docs."""

import importlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from veritas.ledger import store

REPO = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- 1. atomic invoice posting, safe retries
def ar_balance(conn, client_id):
    return sum((Decimal(p["amount"]) for e in store.entries(conn, client_id) for p in e["postings"] if p["account_code"] == "1100"),
               Decimal(0))


def test_duplicate_invoice_leaves_books_unchanged(biz):
    from veritas.crm import business

    pid = business.add_party(biz.conn, "acme", "customer", "Bolt Co")
    business.create_invoice(biz.conn, "acme", pid, "INV-1", 100, "work")
    with pytest.raises(Exception):
        business.create_invoice(biz.conn, "acme", pid, "INV-1", 100, "work")
    assert len(business.rows(biz.conn, "SELECT * FROM invoices WHERE client_id='acme'")) == 1
    assert ar_balance(biz.conn, "acme") == 100                   # the failed retry posted nothing


def test_command_id_makes_retries_idempotent(biz):
    from veritas.crm import business
    from veritas.db import CommandConflict

    pid = business.add_party(biz.conn, "acme", "customer", "Bolt Co")
    a = business.create_invoice(biz.conn, "acme", pid, "INV-7", 250, "work", command_id="cmd-1")
    b = business.create_invoice(biz.conn, "acme", pid, "INV-7", 250, "work", command_id="cmd-1")
    assert a == b and ar_balance(biz.conn, "acme") == 250
    with pytest.raises(CommandConflict):
        business.create_invoice(biz.conn, "acme", pid, "INV-8", 999, "other", command_id="cmd-1")


# --------------------------------------------------------------------------- 6. closed periods are enforced at posting
def test_closed_period_rejects_postings_until_authorized_reopen(biz):
    store.close_period(biz.conn, "acme", date(2026, 6, 30), actor="maya", role="cpa")
    with pytest.raises(store.ClosedPeriod):
        store.post(biz.conn, "acme", date(2026, 6, 15), "late accrual", [store.Line("6000", Decimal(5)), store.Line("1000", Decimal(-5))],
                   source="manual", actor="maya")
    with pytest.raises(PermissionError):
        store.reopen_period(biz.conn, "acme", date(2026, 5, 31), actor="sam", role="client", reason="mine")
    store.reopen_period(biz.conn, "acme", date(2026, 5, 31), actor="maya", role="cpa", reason="late vendor bill")
    store.post(biz.conn, "acme", date(2026, 6, 15), "late accrual", [store.Line("6000", Decimal(5)), store.Line("1000", Decimal(-5))],
               source="manual", actor="maya")
    acts = [e["action"] for e in business_audit(biz.conn)]
    assert "period.closed" in acts and "period.reopened" in acts


def business_audit(conn):
    from veritas import audit

    return audit.events(conn, "acme")


# --------------------------------------------------------------------------- 4. related-record ownership
def test_deal_cannot_reference_another_clients_customer(biz):
    from veritas.crm import business

    store.add_client(biz.conn, id="other", name="Other LLC", kind="business", emails=[], tax_id_last4="9", domain="general")
    theirs = business.add_party(biz.conn, "other", "customer", "Secret Customer of Other")
    with pytest.raises(KeyError):
        business.add_deal(biz.conn, "acme", "Upsell", 1000, party_id=theirs)


# --------------------------------------------------------------------------- 5. grounding compares typed values
def test_grounding_rejects_scaled_money():
    from veritas.ai.grounding import check_answer, unsupported_numbers

    ev = "The penalty is $5,000 per return [R:x]."
    assert unsupported_numbers("The penalty is $500,000.", ev) == ["$500,000"]
    res = check_answer("The penalty is $500,000 [R:x].", ev, {"R": {"x"}}, lines={"R:x": ev})
    assert res["grounded"] is False
    assert unsupported_numbers("The rate is 20%.", "rate of 0.20 applies") == []        # fraction <-> percent is fine
    assert unsupported_numbers("It is $50.", "it is 5,000 cents") == []                   # cents convert only when stated
    assert unsupported_numbers("It is $5,000.", "it is 5,000 cents") == ["$5,000"]
    from veritas.ai.grounding import number_supported
    assert number_supported(0.725, ["72.5 cents per mile"]) and number_supported(0.2, ["20 percent"])
    assert not number_supported(500000, ["a $5,000 penalty"])


# --------------------------------------------------------------------------- 7. central AI data controls
class _Frontier:
    def __init__(self):
        self.calls = []
        self.model = "x"

    def available(self):
        return True

    def structured(self, *, system, content, schema, effort="medium"):
        self.calls.append(content)
        return schema.model_validate({"answer": "ok"}), {}


def test_frontier_requires_consent_and_redacts(foundry):
    from pydantic import BaseModel

    from veritas.ai.router import Registry, Router, Unavailable

    class Out(BaseModel):
        answer: str

    fr = _Frontier()
    local = type("L", (), {"available": lambda self: False, "models": lambda self: []})()
    r = Router(Registry(foundry.paths.config / "models.yaml"), foundry.conn, local=local, frontier=fr)
    store.add_client(foundry.conn, id="pat", name="Pat", kind="individual", emails=[], tax_id_last4="6789", domain="general")
    with pytest.raises(Unavailable):
        r.structured("reason", system="s", user="SSN 123-45-6789", schema=Out, client_id="pat")      # no §7216 consent
    with pytest.raises(Unavailable):
        r.structured("reason", system="s", user="SSN 123-45-6789", schema=Out)                       # taxpayer data, no client
    foundry.conn.execute("UPDATE clients SET consent_7216_at = '2026-10-01' WHERE id = 'pat'")
    r.structured("reason", system="s", user="SSN 123-45-6789 acct 000123456789", schema=Out, client_id="pat",
                 images=[b"\x89PNG"])
    sent = json.dumps(fr.calls[-1])
    assert "123-45-6789" not in sent and "000123456789" not in sent and "image" not in sent
    r.structured("reason", system="s", user="What is the 2026 standard deduction?", schema=Out, data_class="public")


# --------------------------------------------------------------------------- 8. one approval policy; guardrails protected
def test_code_changes_have_one_policy_and_core_code_is_protected():
    foundry = yaml.safe_load((REPO / "config" / "foundry.yaml").read_text(encoding="utf-8"))
    release = yaml.safe_load((REPO / "config" / "release_policy.yaml").read_text(encoding="utf-8"))
    assert foundry["auto_adopt"]["code_change"] == "none"
    assert release["categories"]["code_change"]["auto_release"] is False
    prot = foundry["protected_paths"]
    for p in ["src/veritas/security/", "src/veritas/ledger/", "src/veritas/workflow/", "src/veritas/returns/", "src/veritas/api/",
              "src/veritas/crm/", "src/veritas/ai/router.py", "src/veritas/coverage.py", "src/veritas/release.py"]:
        assert any(p == x or p.startswith(x) for x in prot), p


def test_engineer_subprocess_env_has_no_secrets(monkeypatch):
    from veritas.foundry.agents.builders import sandbox_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("VERITAS_MASTER_KEY", "k")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    env = sandbox_env()
    assert not {"ANTHROPIC_API_KEY", "VERITAS_MASTER_KEY", "CLOUDFLARE_API_TOKEN"} & set(env)
    assert "PATH" in env


# --------------------------------------------------------------------------- 9. CI workflows are valid
def test_workflows_parse_and_use_valid_expressions():
    for wf in (REPO / ".github" / "workflows").glob("*.yml"):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        assert "jobs" in doc, wf.name
        for name, job in doc["jobs"].items():
            assert "hashFiles" not in str(job.get("if", "")), f"{wf.name}:{name} uses hashFiles() in a job-level if"


# --------------------------------------------------------------------------- 2. approval binds the complete package
@pytest.fixture
def fam(foundry):
    from test_return_workflow import add_doc

    store.add_client(foundry.conn, id="rivera", name="Alex and Sam Rivera", kind="individual", emails=[], tax_id_last4="0001",
                     domain="general", facts={"taxpayer_ssn_last4": "0001", "spouse_ssn_last4": "0002"})
    add_doc(foundry.conn, "d_w2a", "rivera", "W-2", {"recipient_tin_last4": "0001", "box1": "90000", "box2": "9000"})
    return foundry


def _signed_return(fam, monkeypatch):
    from test_return_workflow import household

    from veritas.returns.store import Returns

    R = Returns(fam.conn, fam.kb, segregation=False)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.confirm(rid, None, "maya")
    R.submit_for_review(rid, "maya")
    st = R.approve(rid, "maya", "cpa")
    R.request_signature(rid, "maya", "cpa")
    R.record_signature(rid, "taxpayer", method="wet_signature", return_hash=st.facts["approved_hash"])
    return R, rid


def test_rule_change_after_signature_voids_it(fam, monkeypatch):
    R, rid = _signed_return(fam, monkeypatch)
    before = R.latest(rid)["result"]["summary"]["total_tax"]
    path = fam.kb._paths["us_fed.individual.standard_deduction"]
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for v in doc["values"]:
        if v["effective_from"] == "2026-01-01":
            v["value"]["mfj"] = 60000                                    # a (hypothetical) rule change
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    fam.kb.reload()
    R.compute(rid, "recalc-agent")
    assert R.latest(rid)["result"]["summary"]["total_tax"] != before
    st = R.status(rid)
    assert st.status == "preparing" and st.history[-1]["event"] == "reopen"     # review and signature are void


def test_transmit_refuses_a_package_other_than_the_signed_one(fam, monkeypatch):
    from veritas import coverage
    from veritas.workflow.engine import TransitionError

    R, rid = _signed_return(fam, monkeypatch)
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})
    # Bypass compute()'s reopen to simulate a tampered/stale version sitting under a signed status.
    cur = R.latest(rid)
    res = dict(cur["result"])
    res["forms"] = {**res["forms"], "f1040": {**res["forms"]["f1040"], "16": "1"}}
    R._save(rid, cur["inputs"], cur["provenance"], "x", "tampered", result=res)
    with pytest.raises(TransitionError, match="not the approved and signed package"):
        R.transmit(rid, "maya", "cpa", efile_ready=True, submit=lambda key: {"submission_id": "s"})


# --------------------------------------------------------------------------- 3. crash between sending and recording
def test_crash_after_send_is_reconciled_not_resent(fam, monkeypatch):
    from veritas import coverage
    from veritas.workflow.engine import Engine, UncertainOutcome

    R, rid = _signed_return(fam, monkeypatch)
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})
    R.compute(rid, "maya")                                              # coverage changed, package unchanged: stays signed
    assert R.status(rid).status == "signed"
    sent = []
    real_append = Engine._append

    def crash_on_result(self, workflow_id, kind, event, data, actor, idempotency_key=None):
        if event == "activity:transmit":
            raise SystemExit("power lost after the transmitter accepted the return")
        return real_append(self, workflow_id, kind, event, data, actor, idempotency_key)

    monkeypatch.setattr(Engine, "_append", crash_on_result)
    with pytest.raises(SystemExit):
        R.transmit(rid, "maya", "cpa", efile_ready=True, submit=lambda key: sent.append(key) or {"submission_id": "S1"})
    monkeypatch.setattr(Engine, "_append", real_append)
    assert len(sent) == 1
    with pytest.raises(UncertainOutcome):                               # no lookup: never a blind re-send
        R.transmit(rid, "maya", "cpa", efile_ready=True, submit=lambda key: sent.append(key) or {"submission_id": "S2"})
    assert len(sent) == 1 and R.status(rid).status == "unknown"
    st = R.reconcile_transmission(rid, "maya", "cpa", submitted=True, submission_id="S1", evidence="transmitter receipt S1")
    assert st.status == "transmitted"


def test_crash_window_with_provider_lookup_does_not_duplicate(fam, monkeypatch):
    from veritas import coverage
    from veritas.workflow.engine import Engine

    R, rid = _signed_return(fam, monkeypatch)
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})
    R.compute(rid, "maya")
    provider: dict[str, dict] = {}
    real_append = Engine._append
    crashed = {"done": False}

    def crash_once(self, workflow_id, kind, event, data, actor, idempotency_key=None):
        if event == "activity:transmit" and not crashed["done"]:
            crashed["done"] = True
            raise SystemExit("crash")
        return real_append(self, workflow_id, kind, event, data, actor, idempotency_key)

    def submit(key):
        provider.setdefault(key, {"submission_id": f"S{len(provider) + 1}"})
        return provider[key]

    monkeypatch.setattr(Engine, "_append", crash_once)
    with pytest.raises(SystemExit):
        R.transmit(rid, "maya", "cpa", efile_ready=True, submit=submit, lookup=provider.get)
    st = R.transmit(rid, "maya", "cpa", efile_ready=True, submit=submit, lookup=provider.get)
    assert st.status == "transmitted" and len(provider) == 1 and st.facts["submission_id"] == "S1"


# --------------------------------------------------------------------------- 4b. staff cannot approve; related records are owned
def test_staff_cannot_approve_and_api_checks_related_ownership(home, monkeypatch):
    import base64
    import secrets
    import time

    from fastapi.testclient import TestClient

    from veritas.security import totp

    PW = "correct horse battery staple"
    monkeypatch.setenv("VERITAS_HOME", str(home))
    monkeypatch.setenv("VERITAS_AGENTS", "0")
    monkeypatch.delenv("VERITAS_DEV_AUTH", raising=False)
    monkeypatch.setenv("VERITAS_MASTER_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    import veritas.api.app as mod

    importlib.reload(mod)
    c = TestClient(mod.app)

    def enrol(step):
        r = c.post("/api/auth/mfa", json={"challenge": step["challenge"], "code": totp.code_at(step["secret"], int(time.time() // 30))})
        return {"Authorization": f"Bearer {r.json()['token']}"}

    mod.PLATFORM.bootstrap_admin("ops@x.example", "Ops", PW)
    ops = enrol(c.post("/api/auth/login", json={"email": "ops@x.example", "password": PW}).json())
    tok = c.post("/api/platform/firms", json={"id": "f1", "name": "F1", "admin_email": "admin@f1.example"}, headers=ops).json()["admin_invite_token"]
    admin = enrol(c.post("/api/auth/accept", json={"token": tok, "name": "Admin", "password": PW}).json())
    tok = c.post("/api/auth/invite", json={"email": "staff@f1.example", "role": "staff"}, headers=admin).json()["invite_token"]
    staff = enrol(c.post("/api/auth/accept", json={"token": tok, "name": "Staff", "password": PW}).json())
    for cid in ("a", "b"):
        assert c.post("/api/clients", json={"id": cid, "name": cid.upper()}, headers=admin).status_code == 200
    b_party = c.post("/api/clients/b/parties", json={"kind": "customer", "name": "B's secret customer"}, headers=admin).json()["id"]
    r = c.post("/api/clients/a/deals", json={"title": "x", "value": 10, "party_id": b_party}, headers=admin)
    assert r.status_code == 404
    rid = c.post("/api/clients/a/returns", json={"tax_year": 2026, "inputs": {"filing_status": "single",
                                                                               "taxpayer": {"ssn": "400-00-0001", "dob": "1990-01-01"}}},
                 headers=staff).json()["id"]
    assert c.post(f"/api/returns/{rid}/approve", headers=staff).status_code == 403
    assert c.post(f"/api/returns/{rid}/approve", headers=admin).status_code == 403      # admin without the CPA role
    assert c.post("/api/clients/a/periods/close", json={"through": "2026-06-30"}, headers=staff).status_code == 403


# --------------------------------------------------------------------------- Q02: concurrent retries, one effect
def test_concurrent_retries_of_one_command_post_once(biz):
    from concurrent.futures import ThreadPoolExecutor

    from veritas.crm import business
    from veritas.db import ThreadLocalConnection

    pid = business.add_party(biz.conn, "acme", "customer", "Bolt Co")
    shared = ThreadLocalConnection(biz.paths.db)

    def attempt(_):
        return business.create_invoice(shared, "acme", pid, "INV-C", 300, "work", command_id="same-command")

    with ThreadPoolExecutor(20) as ex:
        ids = list(ex.map(attempt, range(20)))
    assert len(set(ids)) == 1
    assert ar_balance(biz.conn, "acme") == 300
    assert len(business.rows(biz.conn, "SELECT * FROM invoices WHERE number = 'INV-C'")) == 1
