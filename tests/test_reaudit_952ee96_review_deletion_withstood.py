"""Adversarial review of the write-ahead deletion (re-audit of 952ee96, finding 4): attacks the fix withstood in the
first review, kept as regression tests. Asserted: on real S3 semantics a delete that landed but raised is completed by
the next run and a hold placed in between keeps it pending and says the bytes are gone; a hold placed while phase 2
decides waits for it (a serial order); two runs and an upload of identical bytes at once leave one receipt and the
live copy's bytes. The S3 test runs against a local S3 server (SeaweedFS or MinIO) when AGENTLEDGER_TEST_S3_ENDPOINT
is set, never against R2. Both backends.
"""

from __future__ import annotations

import os
import secrets
import sys
import threading
from datetime import date

import pytest

from agentledger import db
from agentledger.evidence import records
from agentledger.evidence.blobs import S3Blobs
from agentledger.intake import pipeline
from agentledger.security.vault import Vault
from test_evidence import _doc

TODAY = date(2026, 10, 8)
REASON = "annual retention review"
EXAM = "IRS examination letter dated 2026-10-01"


def _purge(conn, vault):
    return records.purge_expired(conn, vault, TODAY, actor="maya", role="cpa", attested=True, reason=REASON)


def _outcomes(conn, doc_id):
    return [r["outcome"] for r in db.rows(conn, "SELECT r.outcome FROM blob_deletion_results r JOIN blob_deletions b "
                                                "ON b.id = r.deletion_id WHERE b.document_id = ? ORDER BY r.id", doc_id)]


@pytest.mark.skipif(not os.environ.get("AGENTLEDGER_TEST_S3_ENDPOINT"), reason="set AGENTLEDGER_TEST_S3_ENDPOINT to a local S3 server")
def test_s3_retry_of_a_delete_that_landed_but_raised_completes(biz, tmp_path, monkeypatch):
    import boto3

    endpoint = os.environ["AGENTLEDGER_TEST_S3_ENDPOINT"]
    key, secret = os.environ.get("AGENTLEDGER_TEST_S3_KEY", "test"), os.environ.get("AGENTLEDGER_TEST_S3_SECRET", "test")
    bucket = "review-deletion-" + secrets.token_hex(4)
    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1", aws_access_key_id=key, aws_secret_access_key=secret)
    s3.create_bucket(Bucket=bucket)
    try:
        blobs = S3Blobs(bucket=bucket, endpoint=endpoint, access_key=key, secret_key=secret, prefix="firms/review")
        vault = Vault(tmp_path / "v", blobs=blobs)
        loc = _doc(biz.conn, vault, "doc_s3", "acme", b"2017 receipts on S3", "2025-01-01")
        real = blobs.delete

        def lands_then_raises(k):
            real(k)
            raise OSError("injected: reply lost after the delete landed")

        monkeypatch.setattr(blobs, "delete", lands_then_raises)
        out = _purge(biz.conn, vault)
        assert [f["stage"] for f in out.failures] == ["storage"] and not vault.exists(loc)
        monkeypatch.setattr(blobs, "delete", real)

        hold = records.place_hold(biz.conn, client_id="acme", reason=EXAM, actor="maya", role="cpa")
        out = _purge(biz.conn, vault)
        [held] = out.failures
        assert held["stage"] == "held" and held["bytes_present"] is False and "no longer stored" in held["error"]
        records.release_hold(biz.conn, hold, reason="examination closed, no-change letter received", actor="maya", role="cpa")

        out = _purge(biz.conn, vault)                 # deleting a missing key succeeds on S3
        assert out.failures == [] and records.pending_deletions(biz.conn) == []
        assert _outcomes(biz.conn, "doc_s3") == ["started", "failed", "started", "deleted"] and records.integrity(biz.conn, vault)["ok"]
    finally:
        for o in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=o["Key"])
        s3.delete_bucket(Bucket=bucket)


def test_a_hold_placed_while_phase_two_decides_waits_for_it(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    loc = _doc(conn, vault, "doc_h", "acme", b"2017 payroll register", "2025-01-01")
    real = records.hold_on
    placed: dict = {}

    def hold_on(c, document_id, client_id):
        if "thread" not in placed and sys._getframe(1).f_code.co_name == "_delete_object":
            t = threading.Thread(target=lambda: placed.setdefault("id", records.place_hold(
                conn, client_id="acme", reason=EXAM, actor="maya", role="cpa")), daemon=True)
            t.start()
            t.join(1.5)                               # waits for evidence:acme (PostgreSQL) or the write lock (SQLite)
            placed["waited"], placed["thread"] = t.is_alive(), t
        return real(c, document_id, client_id)

    monkeypatch.setattr(records, "hold_on", hold_on)
    out = _purge(conn, vault)
    placed["thread"].join(30)
    assert placed["waited"], "the hold was placed while phase 2 was deciding about the bytes it covers"
    assert out.failures == [] and placed.get("id") and _outcomes(conn, "doc_h") == ["started", "deleted"]
    assert not vault.exists(loc)                      # deleted before the hold was placed: a serial order
    assert [h["id"] for h in records.active_holds(conn)] == [placed["id"]] and records.integrity(conn, vault)["ok"]


def test_two_runs_and_a_reupload_race_keep_live_bytes(biz):
    conn, vault = biz.conn, biz.vault
    data = b"2017 receipt for the triple race"
    _doc(conn, vault, "doc_r", "acme", data, "2025-01-01")
    out, start = {}, threading.Barrier(3)

    def run(i):
        start.wait(10)
        out[i] = _purge(conn, vault)

    def upload():
        start.wait(10)
        out["up"] = pipeline.ingest(conn, None, vault, "r.txt", data, channel="upload", client_hint="acme")

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)] + [threading.Thread(target=upload)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert sorted(r["document_id"] for i in range(2) for r in out[i]) == ["doc_r"]
    for d in out["up"]:
        row = db.one(conn, "SELECT deleted_at, vault_path FROM documents WHERE id = ?", d["id"])
        assert row and row["deleted_at"] is None and vault.read(row["vault_path"]) == data
    report = records.integrity(conn, vault)
    assert report["live_documents_missing_bytes"] == [] and report["pending_deletions"] == []
    assert len(db.rows(conn, "SELECT id FROM deletion_receipts WHERE document_id = 'doc_r'")) == 1
