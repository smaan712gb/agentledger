"""Documents -> return inputs (no re-keying), versioned returns, and the durable preparation workflow."""

import json
import sqlite3

import pytest

from agentledger.ledger import store
from agentledger.returns.store import Returns, Sealer
from agentledger.workflow.engine import TransitionError


def add_doc(conn, doc_id, client_id, doc_type, fields, year=2026):
    conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, doc_type, tax_year, "
                 "confidence, status, vault_path, fields) VALUES (?, ?, ?, ?, 'text/plain', 'upload', ?, ?, ?, 0.99, "
                 "'filed', ?, ?)", (doc_id, client_id, doc_id, f"{doc_id}.pdf", "2026-02-01T00:00:00+00:00", doc_type, year,
                                    f"{doc_id}.pdf", json.dumps(fields)))


@pytest.fixture
def fam(foundry):
    store.add_client(foundry.conn, id="rivera", name="Alex and Sam Rivera", kind="individual", emails=[], tax_id_last4="0001",
                     domain="general", facts={"taxpayer_ssn_last4": "0001", "spouse_ssn_last4": "0002",
                                              "taxpayer_name": "Alex Rivera", "spouse_name": "Sam Rivera"})
    c = foundry.conn
    add_doc(c, "d_w2a", "rivera", "W-2", {"employer_name": "Lakeside Market", "recipient_tin_last4": "0001", "box1": "52,000.00",
                                          "box2": "4100.00", "box3": "52000", "box4": "3224", "box5": "52000", "box6": "754",
                                          "box12_D": "3000"})
    add_doc(c, "d_w2b", "rivera", "W-2", {"employer_name": "City Schools", "recipient_tin_last4": "0002", "wages": "41000",
                                          "federal_income_tax_withheld": "2900", "box3": "41000", "box5": "41000"})
    add_doc(c, "d_int", "rivera", "1099-INT", {"payer_name": "First Bank", "recipient_name": "Alex Rivera", "box1": "312.40"})
    add_doc(c, "d_nec", "rivera", "1099-NEC", {"payer_name": "Acme", "box1": "1500"})
    return foundry


def household():
    return {"tax_year": 2026, "filing_status": "mfj",
            "taxpayer": {"first_name": "Alex", "last_name": "Rivera", "ssn": "400-00-0001", "dob": "1985-06-01"},
            "spouse": {"first_name": "Sam", "last_name": "Rivera", "ssn": "400-00-0002", "dob": "1986-03-01"}}


def test_documents_populate_return_with_provenance(fam):
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    out = R.populate_from_documents(rid, "maya")
    assert set(out["documents"]) == {"d_w2a", "d_w2b", "d_int"}
    assert any(i["code"] == "nec_needs_business" for i in out["issues"])   # never guessed into a business
    v = R.latest(rid)
    w2s = v["inputs"]["w2s"]
    assert [w["owner"] for w in w2s] == ["taxpayer", "spouse"]
    assert w2s[0]["wages"] == "52000.00" and w2s[1]["wages"] == "41000"     # alias 'wages' -> box 1
    assert w2s[0]["box12"] == {"D": "3000"}
    assert v["provenance"]["w2s[0].wages"] == {"document_id": "d_w2a", "box": "box1", "value": "52000.00", "confirmed": False}
    assert v["result"]["summary"]["agi"] == "93312"
    assert v["result"]["forms"]["f1040"]["25a"] == "7000"


def test_workflow_gates_and_durable_states(fam, monkeypatch):
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    with pytest.raises(TransitionError, match="not confirmed"):
        R.submit_for_review(rid, "maya")
    assert R.confirm(rid, None, "maya") == 12
    st = R.submit_for_review(rid, "maya")
    assert st.status == "in_review"
    with pytest.raises(TransitionError, match="different person"):
        R.approve(rid, "maya", "cpa")
    with pytest.raises(TransitionError, match="requires one of"):
        R.approve(rid, "sam.ortiz", "client")
    st = R.approve(rid, "lee", "cpa")
    approved_hash = st.facts["approved_hash"]
    R.request_signature(rid, "lee", "cpa")
    assert [s.workflow_id for s in R.wf.waiting("return_1040")] == [rid]     # durably paused on the taxpayer
    with pytest.raises(TransitionError, match="does not match"):
        R.record_signature(rid, "taxpayer", method="wet_signature", return_hash="0" * 64)
    with pytest.raises(TransitionError, match="KBA"):
        R.record_signature(rid, "taxpayer", method="kba_esign", return_hash=approved_hash)
    R.record_signature(rid, "taxpayer", method="kba_esign", return_hash=approved_hash, kba_transaction_id="kba-123")
    with pytest.raises(TransitionError, match="e-file is not enabled"):
        R.transmit(rid, "lee", "cpa", efile_ready=False, submit=lambda: {"submission_id": "x"})
    with pytest.raises(TransitionError, match="coverage does not allow filing: f1040 is manual-assisted"):
        R.transmit(rid, "lee", "cpa", efile_ready=True, submit=lambda: {"submission_id": "x"})
    # From here on, simulate a future registry in which these forms and the MeF channel are filing-approved.
    from agentledger import coverage

    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})
    R.compute(rid, "lee")
    calls = []

    def submit():
        calls.append(1)
        return {"submission_id": "00000020263000000001"}

    R.wf.activity(rid, "transmit", approved_hash, submit)                       # a crash after sending, before the transition...
    st = R.transmit(rid, "lee", "cpa", efile_ready=True, submit=submit)        # ...then a retry
    assert calls == [1]                                                          # transmitted exactly once
    assert st.status == "transmitted" and st.facts["submission_id"] == "00000020263000000001"
    st = R.wf.send(rid, "ack_accepted", "mef-poller", facts={"ack": "A"})
    assert st.status == "accepted"
    assert R.wf.verify(rid)
    with pytest.raises(TransitionError):
        R.save_inputs(rid, household(), "maya")                                 # an accepted return is closed


def test_editing_after_approval_reopens_and_voids_signature(fam):
    R = Returns(fam.conn, fam.kb, segregation=False)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.confirm(rid, None, "maya")
    R.submit_for_review(rid, "maya")
    R.approve(rid, "maya", "cpa")
    R.request_signature(rid, "maya", "cpa")
    changed = {**R.latest(rid)["inputs"], "payments": {"estimated_tax_payments": "500"}}
    R.save_inputs(rid, changed, "maya")
    st = R.status(rid)
    assert st.status == "preparing"
    assert st.history[-1]["event"] == "reopen"


def test_history_is_append_only(fam):
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    with pytest.raises(sqlite3.DatabaseError):
        fam.conn.execute("UPDATE workflow_events SET event = 'approve' WHERE workflow_id = ?", (rid,))
    with pytest.raises(sqlite3.DatabaseError):
        fam.conn.execute("DELETE FROM tax_return_versions WHERE return_id = ?", (rid,))


def test_returns_are_encrypted_with_firm_key(fam, tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTLEDGER_MASTER_KEY", raising=False)
    from agentledger.security.platform import Platform

    plat = Platform(tmp_path, dev=True)
    plat.keys.create("rivera-cpa")
    R = Returns(fam.conn, fam.kb, Sealer(plat.keys, "rivera-cpa"))
    rid = R.create("rivera", 2026, "maya", household())
    raw = fam.conn.execute("SELECT inputs FROM tax_return_versions WHERE return_id = ?", (rid,)).fetchone()[0]
    assert raw.startswith("enc:") and "400-00-0001" not in raw
    assert R.latest(rid)["inputs"]["taxpayer"]["ssn"] == "400-00-0001"


def test_return_api_dev_mode(home, monkeypatch):
    import importlib

    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))
    monkeypatch.setenv("AGENTLEDGER_AGENTS", "0")
    monkeypatch.setenv("AGENTLEDGER_DEV_AUTH", "1")
    import agentledger.api.app as api_mod

    importlib.reload(api_mod)
    from fastapi.testclient import TestClient

    c = TestClient(api_mod.app)
    cpa = {"Authorization": "Bearer dev-cpa"}
    jordan = {"Authorization": "Bearer dev-jordan"}
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=cpa).status_code == 200
    add_doc(api_mod.APP.conn, "d1", "jordan-lee", "W-2", {"employer_name": "Acme", "box1": "60000", "box2": "6000"})
    rid = c.post("/api/clients/jordan-lee/returns", json={"tax_year": 2026, "inputs": {
        "filing_status": "single", "taxpayer": {"first_name": "Jordan", "ssn": "400-00-0009", "dob": "1990-01-01"}}},
        headers=cpa).json()["id"]
    assert c.post(f"/api/returns/{rid}/populate", headers=cpa).json()["fields"] == 2
    assert c.post(f"/api/returns/{rid}/submit", headers=cpa).status_code == 409          # amounts not confirmed yet
    assert c.post(f"/api/returns/{rid}/confirm", json={}, headers=cpa).json()["confirmed"] == 2
    assert c.post(f"/api/returns/{rid}/submit", headers=cpa).json()["status"] == "in_review"
    assert c.post(f"/api/returns/{rid}/approve", headers=cpa).json()["status"] == "approved"
    full = c.get(f"/api/returns/{rid}", headers=cpa).json()
    assert full["result"]["summary"]["refund"] == "977" and "inputs" in full
    mine = c.get(f"/api/returns/{rid}", headers=jordan).json()
    assert "inputs" not in mine and mine["forms"]["f1040"]["35a"] == "977" and mine["status"] == "approved"
    assert c.post(f"/api/returns/{rid}/approve", headers=jordan).status_code == 403
    assert c.get(f"/api/returns/{rid}", headers={"Authorization": "Bearer dev-ortiz"}).status_code == 403


def test_coverage_is_published_and_pinned(fam):
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    res = R.latest(rid)["result"]
    assert res["coverage"]["lowest"] == "manual-assisted"
    assert {"form": "mef_1040", "status": "unsupported", "need": "filing-approved", "limits": res["coverage"]["filing_blockers"][-1]["limits"]} in res["coverage"]["filing_blockers"]
    assert res["pinned"]["kb_version"] == fam.kb.version() and res["pinned"]["engine"]
    assert all(s["source"] for s in res["sources"])   # every rule value used is recorded, so the run reproduces
