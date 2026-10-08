"""Evidence vault (backlog F-06): content addressing, client-side encryption, versions, retention, legal holds and
deletion receipts; S3-compatible storage (R2 in production) exercised against a local S3 server when one is set in
AGENTLEDGER_TEST_S3_ENDPOINT.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import date

import pytest

from agentledger import audit, db
from agentledger.evidence import blobs as blobstore
from agentledger.evidence import records
from agentledger.security.crypto import Keyring
from agentledger.security.vault import Vault
from test_tenancy import api  # noqa: F401  (fixture)

W2 = b"Form W-2 2026 Employer: Brightline LLC Employee SSN 400-00-0001 Wages 61,200.00"


@pytest.fixture
def keyring(tmp_path):
    import sqlite3

    conn = sqlite3.connect(tmp_path / "keys.db", isolation_level=None)
    conn.execute("CREATE TABLE firm_keys (firm_id TEXT NOT NULL, version INTEGER NOT NULL, wrapped BLOB NOT NULL, "
                 "created_at TEXT NOT NULL DEFAULT (datetime('now')), destroyed_at TEXT, PRIMARY KEY (firm_id, version))")
    k = Keyring(conn, secrets.token_bytes(32))
    k.create("rivera-cpa")
    k.create("lake-tax")
    return k


def test_content_addressed_encrypted_and_deduplicated(tmp_path, keyring):
    a = Vault(tmp_path / "a", keyring, "rivera-cpa")
    loc = a.put(W2)
    assert loc.startswith("blob:cas/") and "rivera" not in loc and "W-2" not in loc
    assert a.put(W2) == loc and len(list(a.blobs.keys())) == 1                  # stored once
    raw = a.blobs.get(loc.removeprefix("blob:"))
    assert raw[:3] == b"VX1" and b"400-00-0001" not in raw                      # sealed before storage
    assert a.read(loc) == W2
    b = Vault(tmp_path / "b", keyring, "lake-tax")
    assert b.address(W2) != loc                                                  # per-firm keyed hash: no cross-firm linking
    keyring.destroy("rivera-cpa")
    with pytest.raises(Exception):
        Vault(tmp_path / "a", keyring, "rivera-cpa").read(loc)                  # crypto-shredded


def test_retention_classes_follow_the_policy(home):
    policy = records.load_policy(home / "config")
    assert records.retention_for(policy, "W-2", 2026, "2027-02-01T00:00:00+00:00") == ("tax_return_support", "2033-12-31")
    assert records.retention_for(policy, "Settlement statement", 2026, "2027-02-01T00:00:00+00:00") == ("property_basis", None)
    assert records.retention_for(policy, "Mystery", None, "2026-05-10T00:00:00+00:00") == ("tax_return_support", "2033-05-10")
    (home / "config" / "retention.yaml").write_text(
        (home / "config" / "retention.yaml").read_text(encoding="utf-8").replace("years: 7\n    basis: \"IRC §6501(a)", "years: 2\n    basis: \"IRC §6501(a)"),
        encoding="utf-8")
    with pytest.raises(ValueError, match="statutory minimum"):
        records.load_policy(home / "config")


def _doc(conn, vault, doc_id, client_id, data, retain_until, doc_type="Receipt"):
    import hashlib

    from agentledger import audit

    loc = vault.put(data)
    conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, vault_path, "
                 "fields, doc_type, retention_class, retain_until) VALUES (?, ?, ?, ?, 'text/plain', 'upload', ?, 'filed', ?, '{}', ?, "
                 "'tax_return_support', ?)",
                 (doc_id, client_id, hashlib.sha256(data + doc_id.encode()).hexdigest(), f"{doc_id}.txt", audit.now(), loc, doc_type,
                  retain_until))
    records.add_version(conn, doc_id, loc, hashlib.sha256(data).hexdigest(), len(data), "intake-agent")
    return loc


def test_holds_block_deletion_and_deletions_leave_receipts(biz):
    conn, vault = biz.conn, biz.vault
    old = _doc(conn, vault, "doc_old", "acme", b"2017 receipt", "2025-01-01")
    _doc(conn, vault, "doc_new", "acme", b"2026 receipt", "2033-12-31")
    shared = _doc(conn, vault, "doc_shared_a", "acme", b"same bytes", "2025-01-01")
    assert _doc(conn, vault, "doc_shared_b", "acme", b"same bytes", "2034-01-01") == shared   # one blob, two records
    with pytest.raises(records.RetentionError):
        records.place_hold(conn, client_id="acme", reason="IRS exam letter 2026", actor="sam", role="staff")
    hold = records.place_hold(conn, client_id="acme", reason="IRS exam letter dated 2026-09-30", actor="maya", role="cpa")
    assert records.purge_expired(conn, vault, date(2026, 10, 8), actor="maya", role="cpa", reason="annual retention review") == []
    assert vault.exists(old)
    with pytest.raises(records.RetentionError):
        records.release_hold(conn, hold, reason="done", actor="maya", role="cpa")          # needs a real reason
    records.release_hold(conn, hold, reason="examination closed, no change letter received", actor="maya", role="cpa")
    with pytest.raises(KeyError):
        records.release_hold(conn, hold, reason="examination closed again", actor="maya", role="cpa")
    receipts = records.purge_expired(conn, vault, date(2026, 10, 8), actor="maya", role="cpa", reason="annual retention review")
    assert sorted(r["document_id"] for r in receipts) == ["doc_old", "doc_shared_a"]
    assert not vault.exists(old)
    assert vault.exists(shared)                          # still held by doc_shared_b, which is retained
    assert db.one(conn, "SELECT deleted_at FROM documents WHERE id = 'doc_new'")["deleted_at"] is None
    actions = [e["action"] for e in audit.events(conn, "acme")]
    assert actions.count("evidence.deleted") == 2 and "evidence.hold_placed" in actions and "evidence.hold_released" in actions
    with pytest.raises(sqlite3.DatabaseError):            # db.DatabaseError on PostgreSQL is a subclass
        conn.execute("DELETE FROM deletion_receipts")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM legal_holds")


def test_staff_and_clients_cannot_delete_evidence(biz):
    with pytest.raises(records.RetentionError):
        records.purge_expired(biz.conn, biz.vault, date(2026, 10, 8), actor="sam", role="staff", reason="annual retention review")


@pytest.mark.skipif(not os.environ.get("AGENTLEDGER_TEST_S3_ENDPOINT"), reason="set AGENTLEDGER_TEST_S3_ENDPOINT to a local S3 server")
def test_s3_store_round_trip_and_firm_offboarding(tmp_path, keyring, monkeypatch):
    import boto3

    endpoint = os.environ["AGENTLEDGER_TEST_S3_ENDPOINT"]
    bucket = "agentledger-test-" + secrets.token_hex(4)
    creds = {"aws_access_key_id": os.environ.get("AGENTLEDGER_TEST_S3_KEY", "test"),
             "aws_secret_access_key": os.environ.get("AGENTLEDGER_TEST_S3_SECRET", "test")}
    boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1", **creds).create_bucket(Bucket=bucket)
    for k, v in {"AGENTLEDGER_BLOBS": "s3", "AGENTLEDGER_BLOB_ENDPOINT": endpoint, "AGENTLEDGER_BLOB_BUCKET": bucket,
                 "AGENTLEDGER_BLOB_ACCESS_KEY_ID": creds["aws_access_key_id"],
                 "AGENTLEDGER_BLOB_SECRET_ACCESS_KEY": creds["aws_secret_access_key"]}.items():
        monkeypatch.setenv(k, v)
    v = Vault(tmp_path, keyring, "rivera-cpa")
    assert isinstance(v.blobs, blobstore.S3Blobs) and v.blobs.prefix == "firms/rivera-cpa/"
    loc = v.put(W2)
    assert v.exists(loc) and v.read(loc) == W2 and v.put(W2) == loc
    other = Vault(tmp_path, keyring, "lake-tax")
    other.put(b"another firm's document")
    raw = v.blobs.get(loc.removeprefix("blob:"))
    assert b"400-00-0001" not in raw
    assert blobstore.destroy_firm("rivera-cpa") == "1 object(s) deleted from the object store"
    assert not v.exists(loc) and len(list(other.blobs.keys())) == 1        # only that firm's objects
    blobstore.destroy_firm("lake-tax")
    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1", **creds)
    s3.delete_bucket(Bucket=bucket)


def test_evidence_endpoints(api):  # noqa: F811
    from test_tenancy import PW, accept, enrol

    mod, c = api
    mod.PLATFORM.bootstrap_admin("ops@agentledger.example", "Ops", PW)
    step = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    ops = enrol(c, step["challenge"], step["secret"])
    r = c.post("/api/platform/firms", json={"id": "rivera-cpa", "name": "Rivera CPA", "admin_email": "maya@rivera.example"}, headers=ops)
    admin = accept(c, r.json()["admin_invite_token"], "Maya")
    tok = c.post("/api/auth/invite", json={"email": "lee@rivera.example", "role": "cpa"}, headers=admin).json()["invite_token"]
    lee = accept(c, tok, "Lee")
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=lee).status_code == 200
    doc = c.post("/api/documents/upload", files={"file": ("w2.txt", W2, "text/plain")}, data={"client_id": "jordan-lee"},
                 headers=lee).json()[0]
    versions = c.get(f"/api/documents/{doc['id']}/versions", headers=lee).json()
    assert [v["version"] for v in versions] == [1] and "locator" not in versions[0]     # storage names stay internal
    assert c.post("/api/clients/jordan-lee/holds", json={"reason": "IRS notice CP2000 dated 2026-09-01"}, headers=admin).status_code == 403
    hold = c.post("/api/clients/jordan-lee/holds", json={"reason": "IRS notice CP2000 dated 2026-09-01"}, headers=lee).json()["id"]
    assert [h["id"] for h in c.get("/api/clients/jordan-lee/holds", headers=lee).json()] == [hold]
    assert c.get("/api/evidence/due", headers=lee).json() == []                         # nothing has reached its date
    assert c.post("/api/evidence/purge", json={"reason": "annual retention review"}, headers=lee).json()["deleted"] == 0
    assert c.post(f"/api/holds/{hold}/release", json={"reason": "CP2000 resolved, agreed with no change"}, headers=lee).status_code == 200
    assert c.post(f"/api/holds/{hold}/release", json={"reason": "CP2000 resolved, agreed with no change"}, headers=lee).status_code == 409
