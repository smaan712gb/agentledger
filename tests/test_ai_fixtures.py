"""Scripted extraction answers (ai/fixtures.py): keyed by the document's hash, honoured only in the local demo or the
end-to-end run and refused anywhere else; the router answers from them and never reaches a model."""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import secrets
from pathlib import Path

import pytest

from agentledger.ai import fixtures as fx
from agentledger.ai.router import Registry, Router, Unavailable
from agentledger.intake.classify import SYSTEM, Classification

W2_TEXT = ("Form W-2 Wage and Tax Statement 2026\nEmployer: Brightline LLC EIN 82-1234567\nEmployee: Jordan Lee SSN XXX-XX-0009\n"
           "Box 1 Wages, tips, other compensation 61,200.00\nBox 2 Federal income tax withheld 6,400.00\n"
           "Box 3 Social security wages 61,200.00\nBox 4 Social security tax withheld 3,794.40\n"
           "Box 5 Medicare wages and tips 61,200.00\nBox 6 Medicare tax withheld 887.40\n")
ANSWER = {"doc_type": "W-2", "tax_year": 2026, "party_names": ["Jordan Lee", "Brightline LLC"], "tin_last4": ["0009"],
          "fields": {"employer_name": "Brightline LLC", "recipient_name": "Jordan Lee", "recipient_tin_last4": "0009",
                     "box1": "61200.00", "box2": "6400.00", "box3": "61200.00", "box4": "3794.40", "box5": "61200.00", "box6": "887.40"},
          "summary": "2026 Form W-2 from Brightline LLC for Jordan Lee", "confidence": 0.97}
TAXPAYER = {"filing_status": "single", "taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0009", "dob": "1990-01-01"}}


def prompt(text: str, name: str = "w2.txt") -> str:
    """What intake/pipeline.py shows the router for a document."""
    return f"File name: {name}\nEmail subject: -\nDeterministic pre-classification: W-2\n<document>\n{text[:30000]}\n</document>"


def minimal_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with a text layer (Helvetica, uncompressed), the shape apps/web/e2e/fixtures/make-fixtures.mjs writes."""
    content = "BT /F1 11 Tf 14 TL 56 760 Td " + " ".join(f"({ln}) Tj T*" for ln in lines) + " ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"]
    out, offsets = "%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


def write_fixtures(directory: Path, documents: dict[str, bytes]) -> dict[str, str]:
    """Writes the documents and fixtures.json (every one answered with ANSWER); returns name -> sha256."""
    directory.mkdir(parents=True, exist_ok=True)
    mapping, shas = {}, {}
    for name, data in documents.items():
        (directory / name).write_bytes(data)
        shas[name] = hashlib.sha256(data).hexdigest()
        mapping[shas[name]] = {"file": name, "response": ANSWER}
    (directory / fx.MAPPING).write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    return shas


@pytest.fixture
def fixture_dir(tmp_path):
    d = tmp_path / "fixtures"
    write_fixtures(d, {"w2.txt": W2_TEXT.encode()})
    return d


@pytest.fixture
def e2e_env(fixture_dir, monkeypatch):
    monkeypatch.setenv(fx.ENV, str(fixture_dir))
    monkeypatch.delenv(fx.DEV_FLAG, raising=False)
    monkeypatch.setenv(fx.E2E_FLAG, "1")
    return fixture_dir


def test_fixtures_are_refused_outside_dev_or_e2e(fixture_dir, monkeypatch):
    monkeypatch.setenv(fx.ENV, str(fixture_dir))
    monkeypatch.delenv(fx.DEV_FLAG, raising=False)
    monkeypatch.delenv(fx.E2E_FLAG, raising=False)
    with pytest.raises(RuntimeError, match="honoured only"):
        fx.from_env()
    with pytest.raises(RuntimeError, match="honoured only"):      # the router, and so the API, refuses to start
        Router(Registry(fixture_dir / "models.yaml"))
    monkeypatch.delenv(fx.ENV)
    assert fx.from_env() is None                                   # unset: the real tiers, as always
    assert Router(Registry(fixture_dir / "models.yaml")).fixtures is None


@pytest.mark.parametrize("flag", [fx.DEV_FLAG, fx.E2E_FLAG])
def test_fixtures_are_honoured_in_dev_or_e2e(fixture_dir, monkeypatch, flag):
    monkeypatch.setenv(fx.ENV, str(fixture_dir))
    monkeypatch.delenv(fx.DEV_FLAG, raising=False)
    monkeypatch.delenv(fx.E2E_FLAG, raising=False)
    monkeypatch.setenv(flag, "1")
    router = Router(Registry(fixture_dir / "models.yaml"))
    assert isinstance(router.fixtures, fx.FixtureRouter)
    assert router.status()["fixtures"] == {"directory": str(fixture_dir), "documents": 1}


def test_the_router_answers_by_document_hash_and_never_guesses(e2e_env):
    router = Router(Registry(e2e_env / "models.yaml"))
    cls, by = router.structured("classify", system=SYSTEM, user=prompt(W2_TEXT), schema=Classification)
    assert by == f"fixture:{hashlib.sha256(W2_TEXT.encode()).hexdigest()[:12]}"
    assert cls.doc_type == "W-2" and cls.tax_year == 2026 and cls.confidence == 0.97
    assert {kv.name: kv.value for kv in cls.fields}["box1"] == "61200.00"     # the mapping's dict became the KV list
    with pytest.raises(Unavailable, match="no extraction fixture"):
        router.structured("classify", system=SYSTEM, user=prompt("Form 1099-INT 2026 Payer First Bank Interest income 12.00"),
                          schema=Classification)
    with pytest.raises(Unavailable, match="structured extraction only"):
        router.stream("answer", system="", messages=[{"role": "user", "content": "hello"}])


def test_image_bytes_match_a_vision_call(tmp_path, monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + secrets.token_bytes(64)
    d = tmp_path / "fixtures"
    write_fixtures(d, {"w2.png": png})
    monkeypatch.setenv(fx.ENV, str(d))
    monkeypatch.setenv(fx.E2E_FLAG, "1")
    router = Router(Registry(d / "models.yaml"))
    cls, by = router.structured("vision", system=SYSTEM, user=prompt("", "w2.png"), schema=Classification, images=[png])
    assert cls.doc_type == "W-2" and by.startswith("fixture:")
    with pytest.raises(Unavailable):
        router.structured("vision", system=SYSTEM, user=prompt("", "other.png"), schema=Classification, images=[b"\x89PNG other"])


def test_a_pdf_fixture_matches_the_text_intake_extracts(tmp_path):
    from agentledger.intake.extract import explode

    pdf = minimal_pdf(W2_TEXT.splitlines())
    d = tmp_path / "fixtures"
    write_fixtures(d, {"w2.pdf": pdf})
    [part] = explode("w2.pdf", pdf)
    assert "61,200.00" in part.text                                  # pypdf reads the text layer
    router = fx.FixtureRouter(d)
    cls, _ = router.structured("classify", system=SYSTEM, user=prompt(part.text, "w2.pdf"), schema=Classification)
    assert cls.doc_type == "W-2"


def test_a_stale_or_missing_mapping_is_refused(tmp_path):
    d = tmp_path / "fixtures"
    write_fixtures(d, {"w2.txt": W2_TEXT.encode()})
    (d / "w2.txt").write_bytes(b"edited after the mapping was made")
    with pytest.raises(RuntimeError, match="does not hash"):
        fx.FixtureRouter(d)
    with pytest.raises(RuntimeError, match="missing"):
        fx.FixtureRouter(tmp_path / "nowhere")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / fx.MAPPING).write_text('{"abc": {"nope": 1}}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="'response'"):
        fx.FixtureRouter(tmp_path / "bad")


def test_intake_files_the_fixture_document_and_the_return_reads_its_boxes(home, e2e_env):
    """The whole path a browser test relies on, without the API: upload -> classified from the fixture -> filed with
    its box amounts -> populated onto a return with provenance. A document without a fixture is never guessed."""
    from agentledger.db import open_store
    from agentledger.foundry.core import Foundry
    from agentledger.intake.pipeline import ingest
    from agentledger.ledger import store
    from agentledger.returns.store import Returns

    f = Foundry(home, open_store(home / "state" / "agentledger.db"))     # the real Router, answering from fixtures
    assert isinstance(f.router.fixtures, fx.FixtureRouter)
    store.add_client(f.conn, id="jordan-lee", name="Jordan Lee", kind="individual", emails=[], tax_id_last4="0009", domain="general",
                     facts={})
    [doc] = ingest(f.conn, f.router, f.vault, "w2.txt", W2_TEXT.encode(), channel="upload", client_hint="jordan-lee", actor="lee")
    assert doc["status"] == "filed" and doc["doc_type"] == "W-2" and doc["tax_year"] == 2026 and doc["confidence"] == 0.97
    row = f.conn.execute("SELECT fields, classified_by FROM documents WHERE id = ?", (doc["id"],)).fetchone()
    assert json.loads(row["fields"])["box1"] == "61200.00" and row["classified_by"].startswith("fixture:")

    R = Returns(f.conn, f.kb)
    rid = R.create("jordan-lee", 2026, "lee", TAXPAYER)
    out = R.populate_from_documents(rid, "lee")
    assert out["documents"] == [doc["id"]] and out["fields"] == 6 and out["conflicts"] == 0
    v = R.latest(rid)
    assert v["inputs"]["w2s"][0]["wages"] == "61200.00"
    assert v["provenance"]["w2s[0].wages"] == {"document_id": doc["id"], "box": "box1", "value": "61200.00", "confirmed": False}

    [other] = ingest(f.conn, f.router, f.vault, "1099int.txt", b"Form 1099-INT 2026 Payer: First Bank Interest income 12.00",
                     channel="upload", client_hint="jordan-lee", actor="lee")
    assert other["doc_type"] == "1099-INT" and other["status"] == "filed"       # the deterministic detector, not a guess
    assert f.conn.execute("SELECT classified_by FROM documents WHERE id = ?", (other["id"],)).fetchone()[0] == "deterministic"
    assert f.conn.execute("SELECT COUNT(*) FROM ai_usage WHERE tier = 'fixture' AND ok = 0").fetchone()[0] == 1   # the miss, on record


@pytest.fixture
def api(home, e2e_env, monkeypatch):
    """The API in multi-firm mode (tests/test_tenancy.py's recipe) with the fixtures the end-to-end launcher sets."""
    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))
    monkeypatch.setenv("AGENTLEDGER_AGENTS", "0")
    monkeypatch.setenv("AGENTLEDGER_MASTER_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    import agentledger.api.app as api_mod

    importlib.reload(api_mod)
    from fastapi.testclient import TestClient

    return api_mod, TestClient(api_mod.app)


def test_an_upload_through_the_api_is_read_from_the_fixture(api):
    from test_tenancy import PW, accept, enrol

    mod, c = api
    mod.PLATFORM.bootstrap_admin("ops@agentledger.example", "Ops", PW)
    step = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    ops = enrol(c, step["challenge"], step["secret"])
    r = c.post("/api/platform/firms", json={"id": "rivera-cpa", "name": "Rivera CPA", "admin_email": "maya@rivera.example"}, headers=ops)
    maya = accept(c, r.json()["admin_invite_token"], "Maya")
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=maya).status_code == 200
    up = c.post("/api/documents/upload", files={"file": ("w2.txt", W2_TEXT.encode(), "text/plain")}, data={"client_id": "jordan-lee"},
                headers=maya)
    assert up.status_code == 200, up.text
    [doc] = up.json()
    assert doc["status"] == "filed" and doc["doc_type"] == "W-2" and doc["confidence"] == 0.97
    rid = c.post("/api/clients/jordan-lee/returns", json={"tax_year": 2026, "inputs": TAXPAYER}, headers=maya).json()["id"]
    assert c.post(f"/api/returns/{rid}/populate", headers=maya).json()["fields"] == 6
    full = c.get(f"/api/returns/{rid}", headers=maya).json()
    assert full["inputs"]["w2s"][0]["wages"] == "61200.00"
    assert full["provenance"]["w2s[0].wages"]["document_id"] == doc["id"] and full["status"] == "preparing"
