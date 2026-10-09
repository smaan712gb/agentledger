"""Re-audit of 952ee96, finding 4: a failed retention deletion can destroy evidence without leaving a receipt.
Regression tests: on 952ee96 each fault below left bytes gone with no committed receipt.

On 952ee96, evidence.records.purge_expired handled each expired document in one database transaction
(db.unit_of_work), in this order:

    1. UPDATE documents SET deleted_at                    records.py:132   rolled back on failure
    2. for each locator: "still shared?" SELECT           records.py:135   rolled back on failure
                         vault.delete(locator)            records.py:138   IRREVERSIBLE, outside the transaction
    3. INSERT INTO deletion_receipts                      records.py:139   rolled back on failure
    4. audit.record("evidence.deleted")                   records.py:145   rolled back on failure (a savepoint)
    5. COMMIT                                             db.py:540        outside unit_of_work's try: no ROLLBACK

The bytes are destroyed in step 2, before anything that records the destruction is durable. Any failure after the
first vault.delete leaves the bytes gone while the receipt, the audit record and deleted_at are rolled back or never
committed: the document is still listed as live, and downloading it fails instead of answering 410.

Deletion is now write-ahead (records.py): the receipt, the tombstone, the audit record and one pending deletion per
object commit first; bytes are deleted afterwards and each outcome is recorded; a later run finishes what a failed
or killed run left pending.

Every test asserts one safe invariant: a document's stored bytes are never gone unless a committed deletion receipt
for that document exists. Each run first checks that the document is due, so a test can only pass by surviving the
fault, never because nothing was attempted. Faults are injected with SQLite triggers, a wrapped blob store, a
stand-in S3 client and a killed child process, so the module runs on the default SQLite backend. The last tests show
the runs that must succeed: a clean deletion, a later run completing an interrupted one, and a hold placed after the
receipt keeping the bytes.
"""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from agentledger import audit, db
from agentledger.evidence import records
from agentledger.evidence.blobs import S3Blobs
from agentledger.security.vault import Vault
from test_evidence import _doc
from test_tenancy import PW, accept, api, enrol  # noqa: F401  (api is a fixture)

pytestmark = pytest.mark.skipif(db.backend() == "postgres", reason="faults are injected with SQLite triggers")

SRC = Path(__file__).resolve().parents[1] / "src"
TODAY = date(2026, 10, 8)
EXPIRED = "2025-01-01"                     # retain-until date already past on TODAY
REASON = "annual retention review 2026"
FULL_DISK = ("CREATE TRIGGER inject_receipt_failure BEFORE INSERT ON deletion_receipts "
             "BEGIN SELECT RAISE(ABORT, 'injected: database or disk is full'); END")


def _purge(conn, vault, actor="maya", expect_due=True):
    """Run the retention deletion as a CPA. Returns the exception the run ended with, or its receipts (with
    `.failures`) when it completed."""
    if expect_due:
        assert records.due_for_deletion(conn, TODAY), "nothing is due on TODAY: the run would not reach a deletion"
    try:
        return records.purge_expired(conn, vault, TODAY, actor=actor, role="cpa", attested=True, reason=REASON)
    except Exception as exc:               # the injected fault surfaced here on 952ee96
        return exc


def _committed(conn, sql, *args):
    """Rows as another worker, or this one after a restart, sees them: a separate connection reads committed data only."""
    c = sqlite3.connect(conn.location)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def _assert_no_destruction_without_receipt(conn, vault, doc_id, locators, outcome, context=""):
    """The safe invariant: if any stored object of the document is gone, a committed deletion receipt exists."""
    gone = [loc for loc in locators if not vault.exists(loc)]
    receipts = _committed(conn, "SELECT id FROM deletion_receipts WHERE document_id = ?", doc_id)
    audits = _committed(conn, "SELECT seq FROM audit WHERE action = 'evidence.deleted' AND payload LIKE ?", f'%"{doc_id}"%')
    live = _committed(conn, "SELECT deleted_at FROM documents WHERE id = ?", doc_id)[0]["deleted_at"] is None
    assert not gone or receipts, (
        f"{len(gone)} of {len(locators)} stored object(s) of {doc_id} destroyed with no deletion receipt committed "
        f"(evidence.deleted audit records: {len(audits)}; document still listed as live: {live}; {context}"
        f"purge outcome: {outcome!r})")


# ------------------------------------------------------------------------------ path A: one locator, failure after it
def test_receipt_insert_fails_after_bytes_are_deleted(biz):
    """Path A, step 3: vault.delete (records.py:138) succeeds, then INSERT INTO deletion_receipts (records.py:139)
    fails. A trigger stands in for a full disk, a dropped database connection or a refused insert. unit_of_work rolls
    back the receipt and deleted_at (db.py:535-539); the deleted bytes stay deleted."""
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2017_receipts", "acme", b"2017 Schedule C expense receipts", EXPIRED)
    conn.execute(FULL_DISK)
    err = _purge(conn, vault)
    _assert_no_destruction_without_receipt(conn, vault, "doc_2017_receipts", [loc], err)


def test_audit_record_fails_after_bytes_are_deleted(biz, monkeypatch):
    """Path A, step 4: vault.delete (records.py:138) and the receipt INSERT (records.py:139) succeed, then
    audit.record('evidence.deleted') (records.py:145) fails. The unit of work rolls back and takes the receipt with it;
    the bytes stay deleted."""
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2018_mileage", "acme", b"2018 vehicle mileage log", EXPIRED)
    real_record = audit.record

    def record(c, actor, role, action, payload, client_id=None):
        if action == "evidence.deleted":
            raise db.DatabaseError("injected: audit trail write failed")
        return real_record(c, actor, role, action, payload, client_id=client_id)

    monkeypatch.setattr(audit, "record", record)
    err = _purge(conn, vault)
    _assert_no_destruction_without_receipt(conn, vault, "doc_2018_mileage", [loc], err)


def test_commit_fails_after_bytes_are_deleted(biz):
    """Path A, step 5: vault.delete, the receipt INSERT and audit.record all succeed, then COMMIT (db.py:540) fails.
    A deferred foreign key, violated by a trigger on the receipt insert, stands in for a disk-full or I/O error, or a
    lost PostgreSQL connection, at commit time. The COMMIT is outside unit_of_work's try block, so no ROLLBACK runs:
    the receipt never becomes durable, and this thread's connection stays inside the failed transaction holding the
    write lock."""
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2016_bank", "acme", b"2016 bank statements, operating account", EXPIRED)
    conn.execute("CREATE TABLE inject_commit_probe (ref TEXT REFERENCES documents(id) DEFERRABLE INITIALLY DEFERRED)")
    conn.execute("CREATE TRIGGER inject_commit_failure AFTER INSERT ON deletion_receipts "
                 "BEGIN INSERT INTO inject_commit_probe VALUES ('no-such-document'); END")
    try:
        err = _purge(conn, vault)
        _assert_no_destruction_without_receipt(conn, vault, "doc_2016_bank", [loc], err,
                                               context=f"connection left inside the open transaction: {conn._get().in_transaction}; ")
    finally:
        raw = conn._get()
        if raw.in_transaction:             # left open by the failed COMMIT; release the write lock for teardown
            raw.execute("ROLLBACK")


CHILD = r'''
import os, sys
from datetime import date
from agentledger.db import open_store
from agentledger.evidence import records
from agentledger.security.vault import Vault

conn, vault = open_store(sys.argv[1]), Vault(sys.argv[2])
delete = vault.blobs.delete


def delete_then_die(key):
    delete(key)        # storage confirms the delete ...
    os._exit(137)      # ... and the worker is killed (deploy, OOM killer, timeout) before the transaction commits


vault.blobs.delete = delete_then_die
records.purge_expired(conn, vault, date(2026, 10, 8), actor="maya", role="cpa", attested=True, reason="annual retention review 2026")
'''


def test_process_dies_after_bytes_are_deleted(biz):
    """Path A, anywhere between records.py:138 and db.py:540, with no exception to handle: the worker running the
    purge is killed (a deploy, the OOM killer, a request timeout) after storage deleted the bytes and before COMMIT.
    The database discards the open transaction; the bytes are gone. An except handler cannot repair this; only a
    durable record written before the bytes are destroyed can."""
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2019_invoices", "acme", b"2019 sales invoices 1001-1250", EXPIRED)
    child = subprocess.run([sys.executable, "-c", CHILD, conn.location, str(vault.root)], capture_output=True, text=True,
                           timeout=120, env={**os.environ, "PYTHONPATH": str(SRC)})
    assert child.returncode == 137, f"the child never reached vault.delete: {child.stderr[-2000:]}"
    _assert_no_destruction_without_receipt(conn, vault, "doc_2019_invoices", [loc], f"worker killed, exit {child.returncode}")


# ------------------------------------------------------------------------------ path B: several locators
class _FailingDeletes:
    """The vault's blob store, except that delete() raises from its `fail_from`-th call on."""

    def __init__(self, inner, fail_from, error):
        self.inner, self.fail_from, self.error, self.calls = inner, fail_from, error, 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def delete(self, key):
        self.calls += 1
        if self.calls >= self.fail_from:
            raise self.error
        self.inner.delete(key)


def test_second_locator_fails_after_first_is_deleted(biz, monkeypatch):
    """Path B, step 2: a document with two stored versions has two locators (records.py:131). The first vault.delete
    succeeds and the second raises (a file locked by another process on Windows, an R2 bucket lock, a 5xx after
    retries). The rollback restores the document and leaves no receipt; the first version's bytes are gone. A failing
    "still shared?" SELECT (records.py:135) for the second locator leaves the same state. Latent at 952ee96: intake
    writes only version 1, but the schema, records.add_version and this loop are built for more."""
    conn, vault = biz.conn, biz.vault
    first = _doc(conn, vault, "doc_2015_1099", "acme", b"2015 1099-NEC as first received", EXPIRED)
    corrected = b"2015 1099-NEC corrected copy"
    second = vault.put(corrected)
    records.add_version(conn, "doc_2015_1099", second, hashlib.sha256(corrected).hexdigest(), len(corrected), "intake-agent",
                        reason="corrected copy received")
    monkeypatch.setattr(vault, "blobs", _FailingDeletes(vault.blobs, 2, PermissionError(13, "injected: object is locked")))
    err = _purge(conn, vault)
    _assert_no_destruction_without_receipt(conn, vault, "doc_2015_1099", [first, second], err)


# ------------------------------------------------------------------------------ path C: the store deleted, then raised
class _R2Bucket:
    """Stands in for the boto3 client inside blobs.S3Blobs: one bucket, in memory. delete_object removes the object
    and then raises ReadTimeoutError: what the caller sees when R2 carried out the delete but the replies were lost
    until botocore's retries (max_attempts 5, set in S3Blobs.__init__) ran out."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):  # noqa: N803  (boto3's argument names)
        self.objects[Key] = bytes(Body)

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)
        raise ReadTimeoutError(endpoint_url=f"https://example.r2.cloudflarestorage.com/{Bucket}/{Key}")


def test_object_store_deletes_but_reports_failure(biz, tmp_path):
    """Path C, step 2 with a single locator: on R2 a failed delete_object (S3Blobs.delete) does not mean nothing was
    deleted. purge_expired treats every exception as "nothing happened" and rolls back the receipt and deleted_at, so
    even a document with one version loses its evidence without a receipt."""
    conn = biz.conn
    vault = Vault(tmp_path / "r2", blobs=S3Blobs(bucket="agentledger-evidence", prefix="firms/rivera-cpa", client=_R2Bucket()))
    loc = _doc(conn, vault, "doc_2014_w2", "acme", b"Form W-2 2014 Employer: Acme Fabrication LLC Wages 48,000.00", EXPIRED)
    err = _purge(conn, vault)
    _assert_no_destruction_without_receipt(conn, vault, "doc_2014_w2", [loc], err)


# ------------------------------------------------------------------------------ the damage is not repaired later
def test_hold_after_failed_run_covers_evidence_that_is_already_gone(biz):
    """Path A, then a legal hold: after the failed run the document still looks live, so nothing prompts a re-run. An
    IRS letter arrives and a CPA places a hold on the client (records.py:76); the next run skips the held document
    (records.py:114 and 129), so no receipt is ever written and the hold now "preserves" evidence that is gone."""
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2017_payroll", "acme", b"2017 payroll register, fourth quarter", EXPIRED)
    conn.execute(FULL_DISK)
    first = _purge(conn, vault)
    assert [f["stage"] for f in first.failures] == ["record"] and vault.exists(loc)   # nothing destroyed, reported
    conn.execute("DROP TRIGGER inject_receipt_failure")                     # the fault clears
    records.place_hold(conn, client_id="acme", reason="IRS examination letter dated 2026-10-01", actor="maya", role="cpa")
    err = _purge(conn, vault, expect_due=False)                              # the next run, with the hold in place
    _assert_no_destruction_without_receipt(conn, vault, "doc_2017_payroll", [loc], err,
                                           context=f"legal hold active on the client; first run ended with {first!r}; ")


# ------------------------------------------------------------------------------ the production entry point
def test_purge_endpoint_destroys_evidence_without_receipt(api):  # noqa: F811
    """POST /api/evidence/purge (app.py:811-822), the only production caller, with a firm's encrypted vault: the run
    fails after the bytes are deleted (path A, step 3) and answers 500; the sealed object is gone, no receipt exists,
    and GET /api/documents/{id}/file still treats the document as live (app.py:742-744 answers 410 only for a recorded
    deletion)."""
    mod, c = api
    mod.PLATFORM.bootstrap_admin("ops@agentledger.example", "Ops", PW)
    step = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    ops = enrol(c, step["challenge"], step["secret"])
    r = c.post("/api/platform/firms", json={"id": "rivera-cpa", "name": "Rivera CPA", "admin_email": "maya@rivera.example"}, headers=ops)
    admin = accept(c, r.json()["admin_invite_token"], "Maya")
    tok = c.post("/api/auth/invite", json={"email": "lee@rivera.example", "role": "cpa"}, headers=admin).json()["invite_token"]
    lee = accept(c, tok, "Lee")
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=lee).status_code == 200
    w2 = b"Form W-2 2017 Employer: Brightline LLC Employee SSN 400-00-0002 Wages 58,900.00"
    doc_id = c.post("/api/documents/upload", files={"file": ("w2-2017.txt", w2, "text/plain")}, data={"client_id": "jordan-lee"},
                    headers=lee).json()[0]["id"]
    r = c.post(f"/api/documents/{doc_id}/assign", json={"client_id": "jordan-lee"}, headers=lee)   # filed from review
    assert r.status_code == 200 and r.json()["status"] == "filed", r.text
    ctx = mod.firm_context("rivera-cpa")
    loc = db.one(ctx.conn, "SELECT vault_path FROM documents WHERE id = ?", doc_id)["vault_path"]
    # Retention over on any day: received in 2018, its year and class confirmed, the 2017 return on record as filed.
    ctx.conn.execute("UPDATE documents SET received_at = '2018-02-01T00:00:00+00:00' WHERE id = ?", (doc_id,))
    r = c.post(f"/api/documents/{doc_id}/retention", json={"tax_year": 2017, "retention_class": "tax_return_support",
                                                          "note": "2017 W-2, checked against the filed return"}, headers=lee)
    assert r.status_code == 200, r.text
    r = c.post("/api/clients/jordan-lee/tax-year-events", json={"tax_year": 2017, "kind": "filed", "occurred_on": "2018-04-10",
                                                               "form": "1040",
                                                               "note": "IRS account transcript shows the return received 2018-04-10"},
               headers=lee)
    assert r.status_code == 200, r.text
    assert [d["id"] for d in c.get("/api/evidence/due", headers=lee).json()] == [doc_id]    # the run reaches it
    ctx.conn.execute(FULL_DISK)
    try:
        r = c.post("/api/evidence/purge", json={"reason": "annual retention review", "attested": True,
                                                "document_ids": [doc_id]}, headers=lee)
        purge = f"{r.status_code} {r.text[:300]}"
        assert r.status_code == 200 and r.json()["deleted"] == 0 and r.json()["failures"][0]["stage"] == "record", purge
    except AssertionError:
        raise
    except Exception as exc:               # TestClient re-raises the server's exception: the client gets a 500
        purge = f"500 ({type(exc).__name__}: {exc})"
    try:
        download = c.get(f"/api/documents/{doc_id}/file", headers=lee).status_code
    except Exception as exc:
        download = f"500 ({type(exc).__name__})"
    _assert_no_destruction_without_receipt(ctx.conn, ctx.foundry.vault, doc_id, [loc], purge,
                                           context=f"GET /api/documents/{{id}}/file -> {download}; ")
    assert download == 200                                                  # still live and readable
    ctx.conn.execute("DROP TRIGGER inject_receipt_failure")
    r = c.post("/api/evidence/purge", json={"reason": "annual retention review", "attested": True,
                                            "document_ids": [doc_id]}, headers=lee).json()
    assert r["deleted"] == 1 and r["failures"] == [] and not ctx.foundry.vault.exists(loc)
    assert c.get(f"/api/documents/{doc_id}/file", headers=lee).status_code == 410            # a recorded deletion
    assert c.get("/api/evidence/integrity", headers=lee).json()["ok"] is True


# ------------------------------------------------------------------------------ the runs that must succeed
def _results(conn, doc_id):
    return [r["outcome"] for r in db.rows(conn, "SELECT r.outcome FROM blob_deletion_results r JOIN blob_deletions b "
                                                "ON b.id = r.deletion_id WHERE b.document_id = ? ORDER BY r.id", doc_id)]


def test_a_clean_run_writes_the_receipt_first_and_then_deletes(biz):
    """No fault: one receipt, the audit record, the tombstone (extracted content cleared, the hash released so the
    same file can be filed again), the bytes deleted and the outcome recorded."""
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2017_receipts", "acme", b"2017 Schedule C expense receipts", EXPIRED)
    out = _purge(conn, vault)
    assert [r["document_id"] for r in out] == ["doc_2017_receipts"] and out.failures == []
    assert not vault.exists(loc) and _results(conn, "doc_2017_receipts") == ["started", "deleted"]
    d = db.one(conn, "SELECT * FROM documents WHERE id = 'doc_2017_receipts'")
    assert d["deleted_at"] and d["sha256"].startswith("deleted:doc_2017_receipts:") and d["fields"] == "{}"
    [rc] = db.rows(conn, "SELECT * FROM deletion_receipts WHERE document_id = 'doc_2017_receipts'")
    assert rc["sha256"] == d["sha256"].split(":", 2)[2] and rc["retain_until"] <= TODAY.isoformat()
    assert records.integrity(conn, vault)["ok"]
    assert _purge(conn, vault, expect_due=False) == []                      # nothing left to do; no second receipt


def test_a_storage_failure_after_the_receipt_is_finished_by_the_next_run(biz, monkeypatch):
    """The receipt commits, then storage fails: the bytes are still there (the safe direction), the failure is
    recorded and reported, the integrity report lists the pending deletion, and the next run completes it."""
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2016_bank", "acme", b"2016 bank statements, operating account", EXPIRED)
    real = vault.blobs
    monkeypatch.setattr(vault, "blobs", _FailingDeletes(real, 1, OSError("injected: storage unavailable")))
    out = _purge(conn, vault)
    assert [r["document_id"] for r in out] == ["doc_2016_bank"] and [f["stage"] for f in out.failures] == ["storage"]
    assert vault.exists(loc) and _results(conn, "doc_2016_bank") == ["started", "failed"]
    report = records.integrity(conn, vault)
    assert not report["ok"] and [p["document_id"] for p in report["pending_deletions"]] == ["doc_2016_bank"]
    monkeypatch.setattr(vault, "blobs", real)                                # storage is back
    out = _purge(conn, vault, expect_due=False)
    assert out == [] and out.failures == [] and not vault.exists(loc)        # completed; no second receipt
    assert _results(conn, "doc_2016_bank") == ["started", "failed", "started", "deleted"] and records.integrity(conn, vault)["ok"]
    assert len(db.rows(conn, "SELECT id FROM deletion_receipts WHERE document_id = 'doc_2016_bank'")) == 1


def test_a_killed_run_is_finished_by_the_next_run(biz):
    """The worker dies after storage deleted the bytes (the child process above): the receipt and the in-flight marker
    were committed first, the outcome was not. Until the marker's deadline another run leaves the object alone (the
    worker might only be slow); after it, the next run completes the pending deletion (deleting an object already gone
    succeeds)."""
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2019_invoices", "acme", b"2019 sales invoices 1001-1250", EXPIRED)
    child = subprocess.run([sys.executable, "-c", CHILD, conn.location, str(vault.root)], capture_output=True, text=True,
                           timeout=120, env={**os.environ, "PYTHONPATH": str(SRC)})
    assert child.returncode == 137, child.stderr[-2000:]
    assert _committed(conn, "SELECT id FROM deletion_receipts WHERE document_id = 'doc_2019_invoices'")
    assert [p["document_id"] for p in records.pending_deletions(conn)] == ["doc_2019_invoices"]
    out = _purge(conn, vault, expect_due=False)
    assert [f["stage"] for f in out.failures] == ["in_flight"] and records.pending_deletions(conn)
    later = datetime.now(timezone.utc) + records.IN_FLIGHT + timedelta(minutes=1)
    with audit.clock(later):
        out = _purge(conn, vault, expect_due=False)
    assert out.failures == [] and not vault.exists(loc) and records.pending_deletions(conn) == []


def test_a_hold_placed_after_the_receipt_keeps_the_bytes(biz, monkeypatch):
    """The receipt commits and storage fails; before the next run an IRS letter arrives and a CPA places a hold. The
    bytes still exist, so they are preserved: the next run skips them (reported as held), and only after the hold is
    released does a run delete them."""
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_2017_payroll", "acme", b"2017 payroll register, fourth quarter", EXPIRED)
    real = vault.blobs
    monkeypatch.setattr(vault, "blobs", _FailingDeletes(real, 1, OSError("injected: storage unavailable")))
    _purge(conn, vault)
    monkeypatch.setattr(vault, "blobs", real)
    hold = records.place_hold(conn, client_id="acme", reason="IRS examination letter dated 2026-10-01", actor="maya", role="cpa")
    out = _purge(conn, vault, expect_due=False)
    assert [f["stage"] for f in out.failures] == ["held"] and vault.exists(loc)
    assert records.integrity(conn, vault)["pending_deletions"][0]["held"] is True
    records.release_hold(conn, hold, reason="examination closed, no-change letter received", actor="maya", role="cpa")
    out = _purge(conn, vault, expect_due=False)
    assert out.failures == [] and not vault.exists(loc)
