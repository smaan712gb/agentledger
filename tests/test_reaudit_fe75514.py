"""Re-audit of fe75514: two further findings, reproduced and fixed.

1. (high) The encrypted vault accepted plaintext replacement bytes, and another document's valid ciphertext, without
   noticing: every object was sealed with the same associated data and an unsealed object was returned as is. Each
   object is now sealed for its own locator, an encrypted vault refuses unsealed bytes, objects sealed before that
   binding must match their content address, and a read given the document's hash checks the plaintext.
2. (medium) The R2 configuration passed checksum settings botocore 1.34 does not know (TypeError at startup), while
   the project allowed boto3 1.34. The minimum is now 1.36 and older botocore gets no such settings; CI runs the suite
   at the lowest versions pyproject.toml allows.
"""

from __future__ import annotations

import hashlib

import pytest

from agentledger import audit, db
from agentledger.evidence import blobs as blobstore
from agentledger.evidence import records
from agentledger.security.vault import LEGACY_PURPOSE, Vault, VaultIntegrityError
from test_evidence import keyring  # noqa: F401  (fixture)
from test_tenancy import PW, accept, api, enrol  # noqa: F401  (api is a fixture)

W2_A = b"Form W-2 2026 Employer: Brightline LLC Employee SSN 400-00-0001 Wages 61,200.00"
W2_B = b"Form W-2 2026 Employer: Harbor Freight Lines Employee SSN 400-00-0002 Wages 48,000.00"


def _key(loc: str) -> str:
    return loc.removeprefix("blob:")


# ------------------------------------------------------------------------------------------- 1. vault integrity
def test_an_encrypted_vault_refuses_plaintext_replacement(tmp_path, keyring):  # noqa: F811
    v = Vault(tmp_path, keyring, "rivera-cpa")
    loc = v.put(W2_A, owner="doc_a")
    v.blobs.put(_key(loc), b"Form W-2 2026 ... Wages 6,120.00 (forged, unsealed)")
    with pytest.raises(VaultIntegrityError, match="not sealed"):
        v.read(loc)


def test_another_documents_ciphertext_in_its_place_is_detected(tmp_path, keyring):  # noqa: F811
    v = Vault(tmp_path, keyring, "rivera-cpa")
    a, b = v.put(W2_A, owner="doc_a"), v.put(W2_B, owner="doc_b")
    v.blobs.put(_key(a), v.blobs.get(_key(b)))                      # a valid ciphertext of the same firm, wrong object
    with pytest.raises(VaultIntegrityError, match="not sealed for this locator"):
        v.read(a)
    assert v.read(b) == W2_B                                         # the genuine object still reads


def test_objects_sealed_before_the_binding_still_read_and_are_checked_by_address(tmp_path, keyring):  # noqa: F811
    v = Vault(tmp_path, keyring, "rivera-cpa")
    a, b = v.address(W2_A), v.address(W2_B)                         # legacy: content addressed, no owner, shared purpose
    v.blobs.put(_key(a), keyring.encrypt("rivera-cpa", W2_A, LEGACY_PURPOSE))
    v.blobs.put(_key(b), keyring.encrypt("rivera-cpa", W2_B, LEGACY_PURPOSE))
    assert v.read(a) == W2_A
    v.blobs.put(_key(a), v.blobs.get(_key(b)))                      # the swap the legacy sealing could not see
    with pytest.raises(VaultIntegrityError, match="content address"):
        v.read(a)


def test_a_read_given_the_documents_hash_checks_the_plaintext(tmp_path, keyring):  # noqa: F811
    v = Vault(tmp_path, keyring, "rivera-cpa")
    loc = v.put(W2_A, owner="doc_a")
    assert v.read(loc, sha256=hashlib.sha256(W2_A).hexdigest()) == W2_A
    with pytest.raises(VaultIntegrityError, match="hash"):
        v.read(loc, sha256=hashlib.sha256(W2_B).hexdigest())
    dev = Vault(tmp_path / "dev")                                     # no keyring (single-firm development)
    dloc = dev.put(W2_A, owner="doc_a")
    dev.blobs.put(_key(dloc), W2_B)
    with pytest.raises(VaultIntegrityError, match="hash"):
        dev.read(dloc, sha256=hashlib.sha256(W2_A).hexdigest())


def test_path_addressed_documents_are_sealed_for_their_path_and_copies_are_resealed(tmp_path, keyring):  # noqa: F811
    v = Vault(tmp_path, keyring, "rivera-cpa")
    v.write("acme/2026/w2/a.txt", W2_A)
    v.write("acme/2026/w2/b.txt", W2_B)
    (tmp_path / "acme/2026/w2/a.txt").write_bytes((tmp_path / "acme/2026/w2/b.txt").read_bytes())
    with pytest.raises(VaultIntegrityError):
        v.read("acme/2026/w2/a.txt")
    v.copy("acme/2026/w2/b.txt", "beta/2026/w2/b.txt")
    assert v.read("beta/2026/w2/b.txt") == W2_B


def test_the_download_never_serves_tampered_evidence(api):  # noqa: F811
    mod, c = api
    mod.PLATFORM.bootstrap_admin("ops@agentledger.example", "Ops", PW)
    step = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    ops = enrol(c, step["challenge"], step["secret"])
    r = c.post("/api/platform/firms", json={"id": "rivera-cpa", "name": "Rivera CPA", "admin_email": "maya@rivera.example"},
               headers=ops)
    admin = accept(c, r.json()["admin_invite_token"], "Maya")
    tok = c.post("/api/auth/invite", json={"email": "lee@rivera.example", "role": "cpa"}, headers=admin).json()["invite_token"]
    lee = accept(c, tok, "Lee")
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=lee).status_code == 200
    a = c.post("/api/documents/upload", files={"file": ("a.txt", W2_A, "text/plain")}, data={"client_id": "jordan-lee"},
               headers=lee).json()[0]
    b = c.post("/api/documents/upload", files={"file": ("b.txt", W2_B, "text/plain")}, data={"client_id": "jordan-lee"},
               headers=lee).json()[0]
    assert c.get(f"/api/documents/{a['id']}/file", headers=lee).content == W2_A
    ctx = mod.firm_context("rivera-cpa")
    vault = ctx.foundry.vault
    vault.blobs.put(_key(a["vault_path"]), vault.blobs.get(_key(b["vault_path"])))      # swapped in storage
    r = c.get(f"/api/documents/{a['id']}/file", headers=lee)
    assert r.status_code == 409 and "integrity" in r.text and W2_B not in r.content
    assert "evidence.integrity_failed" in [e["action"] for e in audit.events(ctx.conn)]          # review-queue documents
    report = c.get("/api/evidence/integrity?verify=true", headers=lee).json()
    assert report["ok"] is False and [f["document_id"] for f in report["failed_authentication"]] == [a["id"]]


def test_the_integrity_report_authenticates_every_object_on_request(biz):
    conn, vault = biz.conn, biz.vault
    from agentledger.intake import pipeline

    [doc] = pipeline.ingest(conn, None, vault, "w2.txt", W2_A, channel="upload", client_hint="acme")
    assert records.integrity(conn, vault, verify_contents=True)["failed_authentication"] == []
    vault.blobs.put(_key(doc["vault_path"]), b"not sealed at all")
    report = records.integrity(conn, vault, verify_contents=True)
    assert not report["ok"] and [f["document_id"] for f in report["failed_authentication"]] == [doc["id"]]
    assert db.one(conn, "SELECT deleted_at FROM documents WHERE id = ?", doc["id"])["deleted_at"] is None


# ------------------------------------------------------------------------------------------- 2. boto3 minimum
def test_older_botocore_gets_no_checksum_settings(monkeypatch):
    import botocore

    monkeypatch.setattr(botocore, "__version__", "1.34.162")
    assert blobstore._checksum_settings() == {}
    store = blobstore.S3Blobs(bucket="evidence", endpoint="http://127.0.0.1:9", access_key="k", secret_key="s",
                              prefix="firms/rivera-cpa")          # builds its client without the unknown settings
    assert store.bucket == "evidence"
    monkeypatch.setattr(botocore, "__version__", "1.36.0")
    assert blobstore._checksum_settings() == {"request_checksum_calculation": "when_required",
                                              "response_checksum_validation": "when_required"}


def test_the_declared_minimum_is_the_one_that_has_the_settings():
    import tomllib
    from pathlib import Path

    deps = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert "boto3>=1.36" in [d.replace(" ", "") for d in deps]
