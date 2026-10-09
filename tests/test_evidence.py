"""Evidence vault (backlog F-06): content addressing, client-side encryption, versions, retention, legal holds and
deletion receipts; S3-compatible storage (R2 in production) exercised against a local S3 server when one is set in
AGENTLEDGER_TEST_S3_ENDPOINT.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import date, timedelta

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
    # At intake: the earliest possible date, 7 years after receipt plus the margin. The date that governs deletion is
    # computed when deletion is considered, from the filings on record (test_reaudit_952ee96_premature_retention).
    grace = timedelta(days=records.GRACE_DAYS)
    assert records.retention_for(policy, "W-2", 2026, "2027-02-01T00:00:00+00:00") == (
        "tax_return_support", (date(2034, 2, 1) + grace).isoformat())
    assert records.retention_for(policy, "Settlement statement", 2026, "2027-02-01T00:00:00+00:00") == ("property_basis", None)
    assert records.retention_for(policy, "Mystery", None, "2026-05-10T00:00:00+00:00") == (
        "tax_return_support", (date(2033, 5, 10) + grace).isoformat())
    (home / "config" / "retention.yaml").write_text(
        (home / "config" / "retention.yaml").read_text(encoding="utf-8").replace("years: 7\n    basis: \"IRC §6501(a)", "years: 2\n    basis: \"IRC §6501(a)"),
        encoding="utf-8")
    with pytest.raises(ValueError, match="statutory minimum"):
        records.load_policy(home / "config")


def _doc(conn, vault, doc_id, client_id, data, retain_until, doc_type="Receipt"):
    """A filed document without a tax year whose retention a CPA confirmed, received 7 years and the margin before
    `retain_until`, so its retention period ends on that day."""
    import hashlib

    from agentledger import audit

    loc = vault.put(data)
    received = records.add_years(date.fromisoformat(retain_until) - timedelta(days=records.GRACE_DAYS), -7).isoformat() + "T00:00:00+00:00"
    conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, vault_path, "
                 "fields, doc_type, retention_class, retain_until, retention_confirmed_by, retention_confirmed_at) "
                 "VALUES (?, ?, ?, ?, 'text/plain', 'upload', ?, 'filed', ?, '{}', ?, 'tax_return_support', ?, 'maya', ?)",
                 (doc_id, client_id, hashlib.sha256(data + doc_id.encode()).hexdigest(), f"{doc_id}.txt", received, loc, doc_type,
                  retain_until, audit.now()))
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
    assert records.purge_expired(conn, vault, date(2026, 10, 8), actor="maya", role="cpa", attested=True, reason="annual retention review") == []
    assert vault.exists(old)
    with pytest.raises(records.RetentionError):
        records.release_hold(conn, hold, reason="done", actor="maya", role="cpa")          # needs a real reason
    records.release_hold(conn, hold, reason="examination closed, no change letter received", actor="maya", role="cpa")
    with pytest.raises(KeyError):
        records.release_hold(conn, hold, reason="examination closed again", actor="maya", role="cpa")
    receipts = records.purge_expired(conn, vault, date(2026, 10, 8), actor="maya", role="cpa", attested=True, reason="annual retention review")
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


def test_identical_bytes_uploaded_during_a_deletion_are_kept(biz, monkeypatch):
    """A retention run deletes a document's bytes while the same file is uploaded again: the new document has its own
    object (objects are addressed by document and bytes), so the deletion can never take it. Runs on both backends."""
    import threading
    import time

    from agentledger.intake import pipeline

    conn, vault = biz.conn, biz.vault
    data = b"2017 receipt, uploaded again during the retention run"
    loc = _doc(conn, vault, "doc_old", "acme", data, "2025-01-01")
    deleting, uploaded = threading.Event(), {}
    real_delete = vault.blobs.delete

    def slow_delete(key):
        deleting.set()
        time.sleep(0.5)                                   # the upload runs now; it must wait for this deletion to commit
        real_delete(key)

    monkeypatch.setattr(vault.blobs, "delete", slow_delete)

    def upload():
        deleting.wait(10)
        uploaded["out"] = pipeline.ingest(conn, None, vault, "receipt.txt", data, channel="upload", client_hint="acme")

    t = threading.Thread(target=upload)
    t.start()
    receipts = records.purge_expired(conn, vault, date(2026, 10, 8), actor="maya", role="cpa", attested=True, reason="annual retention review")
    t.join(30)
    assert [r["document_id"] for r in receipts] == ["doc_old"] and receipts.failures == []
    [new] = uploaded["out"]
    assert not new.get("duplicate") and new["vault_path"] != loc                    # its own object
    assert not vault.exists(loc) and vault.read(new["vault_path"]) == data
    assert records.integrity(conn, vault)["ok"]


def test_concurrent_retention_runs_delete_each_document_once(biz):
    """Two runs at once (two workers, or a double-clicked button): one receipt per document, never two."""
    import threading

    conn, vault = biz.conn, biz.vault
    for i in range(5):
        _doc(conn, vault, f"doc_{i}", "acme", f"2017 receipt {i}".encode(), "2025-01-01")
    out, start = [], threading.Barrier(2)

    def run():
        start.wait(10)
        out.append(records.purge_expired(conn, vault, date(2026, 10, 8), actor="maya", role="cpa", attested=True, reason="annual retention review"))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert sorted(r["document_id"] for run_ in out for r in run_) == [f"doc_{i}" for i in range(5)]
    assert [f for run_ in out for f in run_.failures if f["stage"] != "record"] == []
    n = db.one(conn, "SELECT COUNT(*) AS n FROM deletion_receipts")["n"]
    assert n == 5 and records.integrity(conn, vault)["ok"]


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
    r = c.post("/api/evidence/purge", json={"reason": "annual retention review", "attested": True}, headers=lee)
    assert r.status_code == 400 and "document_ids" in r.text                           # the reviewed list is required
    r = c.post("/api/evidence/purge", json={"reason": "annual retention review", "document_ids": [doc["id"]]}, headers=lee)
    assert r.status_code == 400 and "attestation" in r.text                            # and the attestation
    assert c.post("/api/evidence/purge", json={"reason": "annual retention review", "attested": True,
                                               "document_ids": [doc["id"]]}, headers=lee).json()["deleted"] == 0
    explained = c.get(f"/api/documents/{doc['id']}/retention", headers=lee).json()
    assert explained["retention_end"] is None and "confirmed" in explained["reason"]
    assert c.post(f"/api/holds/{hold}/release", json={"reason": "CP2000 resolved, agreed with no change"}, headers=lee).status_code == 200
    assert c.post(f"/api/holds/{hold}/release", json={"reason": "CP2000 resolved, agreed with no change"}, headers=lee).status_code == 409
