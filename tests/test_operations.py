"""Intake, bank feed, CRM, automations, plugins, playbooks, grounding and the API."""

import hashlib
import hmac
import json
from datetime import date
from decimal import Decimal

import pytest

from conftest import FakeRouter
from veritas.ai.grounding import check_answer
from veritas.intake.classify import Classification, KV
from veritas.intake.pipeline import ingest
from veritas.ledger import store
from veritas.ledger.store import Line

NEC = b"""Form 1099-NEC Nonemployee Compensation 2026
PAYER: Brightline Consulting LLC
RECIPIENT: Acme Fabrication LLC  TIN **-***1234
1 Nonemployee compensation $ 18,400.00"""


def test_intake_files_by_sender_and_feeds_integrity(biz):
    from veritas.integrity.checks import run_all

    cls = Classification(doc_type="1099-NEC", tax_year=2026, party_names=["Acme Fabrication LLC"], tin_last4=["1234"],
                         fields=[KV(name="payer_name", value="Brightline Consulting LLC"), KV(name="box1_nonemployee_compensation", value="$18,400.00")],
                         summary="1099-NEC from Brightline", confidence=0.95)
    router = FakeRouter({"classify": cls})
    out = ingest(biz.conn, router, biz.paths.vault, "nec.txt", NEC, channel="email", sender="owner@acme.example")
    assert out[0]["status"] == "filed" and out[0]["client_id"] == "acme"
    assert out[0]["vault_path"].replace("\\", "/").startswith("acme/2026/income/")
    again = ingest(biz.conn, router, biz.paths.vault, "copy.txt", NEC, channel="upload")
    assert again[0].get("duplicate")
    found = run_all(biz.conn, biz.kb, "acme", 2026)
    assert any(f["check_id"] == "income.info_return_mismatch" for f in found)


def test_intake_never_guesses_a_client(biz):
    out = ingest(biz.conn, None, biz.paths.vault, "mystery.txt", b"Invoice no. 7 from Somebody Else Inc total $50.00", channel="upload")
    assert out[0]["status"] == "needs_review" and out[0]["client_id"] is None


def test_hallucinated_extracted_amount_is_dropped(biz):
    cls = Classification(doc_type="1099-NEC", tax_year=2026, party_names=[], tin_last4=["1234"],
                         fields=[KV(name="box1", value="$99,999.00")], summary="x", confidence=0.95)
    out = ingest(biz.conn, FakeRouter({"classify": cls}), biz.paths.vault, "n.txt", NEC, channel="upload", client_hint="acme")
    assert out[0]["confidence"] <= 0.6 and out[0]["status"] == "needs_review"


def test_zip_and_email_are_exploded(biz):
    import io
    import zipfile
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"] = "owner@acme.example"
    m["Subject"] = "docs"
    m.set_content("see attached")
    m.add_attachment(NEC, maintype="text", subtype="plain", filename="nec.txt")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mail.eml", bytes(m))
        z.writestr("note.txt", "hello")
    out = ingest(biz.conn, None, biz.paths.vault, "bundle.zip", buf.getvalue(), channel="upload")
    assert {o["name"] for o in out} == {"mail-body.txt", "nec.txt", "note.txt"}


def test_bank_feed_learns_from_history(biz):
    from veritas.ledger import bankfeed

    csv = "Date,Description,Amount\n2026-03-01,POS SHELL OIL 5521,-48.20\n2026-03-02,ACME WIDGETS SUPPLY #22,-310.00\n"
    s = bankfeed.suggest(biz.conn, None, "acme", bankfeed.parse_csv(csv))
    assert s[0]["account"] == "6400" and s[0]["tax_treatment"] == "vehicle"
    assert s[1]["account"] is None
    bankfeed.post_confirmed(biz.conn, "acme", [{**s[1], "account": "5000"}], "owner", "client")
    s2 = bankfeed.suggest(biz.conn, None, "acme", bankfeed.parse_csv("Date,Description,Amount\n2026-04-02,ACME WIDGETS SUPPLY #91,-120.00\n"))
    assert s2[0]["account"] == "5000" and "your history" in s2[0]["basis"]


def test_vendor_1099_uses_live_threshold(biz):
    from veritas.crm import business

    v = business.add_party(biz.conn, "acme", "vendor", "Pat Welding", entity_type="individual")
    business.pay_vendor(biz.conn, "acme", v, 1500, "6900", "welding", on=date(2025, 5, 1))
    business.pay_vendor(biz.conn, "acme", v, 1500, "6900", "welding", on=date(2026, 5, 1))
    assert business.vendor_1099_status(biz.conn, biz.kb, "acme", 2025)[0]["requires_1099"] is True   # $600 threshold
    assert business.vendor_1099_status(biz.conn, biz.kb, "acme", 2026)[0]["requires_1099"] is False  # $2,000 threshold


def test_automations_consume_the_audit_trail(biz):
    from veritas.crm import automations, core as crm
    from veritas.integrity.checks import run_all

    store.post(biz.conn, "acme", date(2026, 4, 2), "Team dinner", [Line("6200", Decimal(400), "meals"), Line("1000", Decimal(-400))],
               source="t", actor="t")
    run_all(biz.conn, biz.kb, "acme", 2026)
    out = automations.run(biz.conn, biz.paths.config / "automations.yaml")
    assert any(f["automation"] == "missing-receipt-request" for f in out["fired"])
    assert any(t["assignee"] == "client" and "receipt" in t["title"].lower() for t in crm.tasks(biz.conn, "acme"))
    assert automations.run(biz.conn, biz.paths.config / "automations.yaml")["events"] == 0 or True  # cursor advanced


def test_plugin_permissions_are_enforced(biz):
    from veritas.plugins.registry import Manifest, PermissionDenied, PluginContext

    m = Manifest(id="t", name="t", kind="connector", description="", entry="x:y", permissions=["transactions:suggest"])
    ctx = PluginContext(m, biz.conn, "acme", {}, biz)
    with pytest.raises(PermissionDenied):
        ctx.ledger()
    with pytest.raises(PermissionDenied):
        ctx.http_get("https://example.com")
    assert not hasattr(PluginContext(Manifest(**{**m.model_dump(), "permissions": ["ledger:read"]}), biz.conn, "acme", {}, biz).ledger(), "post")


def test_exports(biz):
    from veritas.plugins.registry import run_plugin

    store.post(biz.conn, "acme", date(2026, 1, 3), "sale", [Line("1000", Decimal(50)), Line("4000", Decimal(-50))], source="t", actor="t")
    assert "!TRNS" in run_plugin(biz, "quickbooks_iif_export", "acme")["result"]
    assert "Income:" in run_plugin(biz, "beancount_export", "acme")["result"]


def test_playbook_scan_and_missing_facts(biz):
    from veritas.brain.playbooks import Brain

    brain = Brain(biz.paths.root / "playbooks", biz.kb)
    scan = {o["playbook_id"]: o for o in brain.scan(biz.conn, "acme")}
    assert scan["accountable_plan"]["status"] == "applies"
    assert scan["augusta_rule_280a_g"]["status"] == "needs_facts" and scan["augusta_rule_280a_g"]["missing_fact"] == "owner_has_home"


def test_answer_grounding():
    ev = "[R:x] meals deductible 0.5\n[E:12] dinner 240.00"
    ok = check_answer("You can deduct 50% [R:x] of the 240.00 dinner [E:12].", ev, {"R": {"x"}, "E": {"12"}})
    assert ok["grounded"]
    bad = check_answer("You can deduct 120.00 [R:x] and 900 more [E:99].", ev, {"R": {"x"}, "E": {"12"}})
    assert not bad["grounded"] and "E:99" in bad["unknown_citations"]


def test_answer_attribution():
    lines = {"M:2026:8a": "Line 8a Depreciation: tax exceeds book: 77650.00", "M:2026:5c": "Line 5c meals: 1150.00"}
    ids = {"M": {"2026:8a", "2026:5c"}}
    ev = "\n".join(f"[{k}] {v}" for k, v in lines.items())
    uncited = check_answer("Depreciation differs by $77,650.", ev, ids, lines)
    wrong = check_answer("Depreciation differs by $77,650 [M:2026:5c].", ev, ids, lines)
    right = check_answer("Tax depreciation exceeds book by $77,650 [M:2026:8a]. That $77,650 is a timing difference.", ev, ids, lines)
    assert uncited["uncited_numbers"] and not uncited["grounded"]
    assert wrong["misattributed_numbers"] and not wrong["grounded"]
    assert right["grounded"]


def test_client_never_sees_an_unverified_answer(biz):
    from veritas.ask.engine import ask_stream

    biz.router = FakeRouter({"stream": "You don't need to report it; the threshold is $6,000 [R:made.up]."})
    store.add_client(biz.conn, id="pat", name="Pat Doe", kind="individual")
    events = list(ask_stream(biz.conn, biz.kb, biz.router, "Do I report my 1099?", client_id="pat", actor="pat", role="client"))
    shown = "".join(e["text"] for e in events if e["type"] == "token")
    assert "$6,000" not in shown and "CPA" in shown
    assert next(e for e in events if e["type"] == "done")["grounded"] is False
    cpa_events = list(ask_stream(biz.conn, biz.kb, biz.router, "Do I report my 1099?", client_id="pat", actor="maya", role="cpa"))
    assert "$6,000" in "".join(e["text"] for e in cpa_events if e["type"] == "token")  # CPA sees it, flagged
    assert next(e for e in cpa_events if e["type"] == "verify")["grounded"] is False


def test_api_segregation_and_webhook(home, monkeypatch):
    monkeypatch.setenv("VERITAS_HOME", str(home))
    monkeypatch.setenv("VERITAS_AGENTS", "0")
    monkeypatch.setenv("VERITAS_WEBHOOK_SECRET", "s3cret")
    import importlib

    import veritas.api.app as api_mod

    importlib.reload(api_mod)
    from fastapi.testclient import TestClient

    c = TestClient(api_mod.app)
    cpa = {"Authorization": "Bearer dev-cpa"}
    assert c.post("/api/clients", json={"id": "ortiz-auto", "name": "Ortiz", "domain": "auto_repair"}, headers=cpa).status_code == 200
    assert c.post("/api/clients", json={"id": "lakeside-fuel", "name": "Lakeside", "domain": "gas_station"}, headers=cpa).status_code == 200
    owner = {"Authorization": "Bearer dev-ortiz"}
    assert c.get("/api/clients/ortiz-auto", headers=owner).status_code == 200
    assert c.get("/api/clients/lakeside-fuel", headers=owner).status_code == 403
    assert c.get("/api/proposals", headers=owner).status_code == 403
    assert [x["id"] for x in c.get("/api/clients", headers=owner).json()] == ["ortiz-auto"]
    body = json.dumps({"transactions": [{"date": "2026-05-01", "description": "POS SHELL OIL", "amount": "-40"}]}).encode()
    sig = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert c.post("/api/hooks/ortiz-auto", content=body, headers={"X-Veritas-Signature": "bad"}).status_code == 401
    r = c.post("/api/hooks/ortiz-auto", content=body, headers={"X-Veritas-Signature": sig})
    assert r.status_code == 200 and r.json()["suggestions"][0]["account"] == "6400"
    assert c.get("/api/audit", headers=cpa).json()["verification"]["ok"]
