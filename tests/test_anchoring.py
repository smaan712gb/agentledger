"""Audit chain anchoring (backlog F-13, components C28 and C34; the chain half of Q33).

An anchor fixes the audit chain's head outside the firm's store, signed with the firm's key. A chain that no longer
holds an anchored record is reported as rewritten or truncated with the anchor that proves it; a forged anchor fails
its signature; an anchor older than the threshold with unanchored activity behind it is `anchor_missing`; and the
job never claims the anchors are locked unless the store refused to delete its probe. Runs on both backends: the
tampering below is what the store's owner can do, which is exactly what the anchors are for.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from agentledger import audit, db
from agentledger.evidence import blobs as blobstore
from agentledger.evidence.anchors import ANCHOR_AGENT_ID, MAX_AGE_ENV, Anchors
from test_evidence import keyring  # noqa: F401  (fixture)
from test_return_workflow import fam, household  # noqa: F401  (fixture)
from test_tenancy import api  # noqa: F401  (fixture)

FIRM = "rivera-cpa"
KEY = re.compile(r"\d{4}-\d{2}-\d{2}/\d{12}\.json")


def _anchors(f, keyring, store=None, **kw):  # noqa: F811
    store = store if store is not None else blobstore.FileBlobs(f.paths.data / "anchors")
    return Anchors(f.conn, store, firm_id=FIRM, keyring=keyring, location="file:test", **kw)


def _event(conn, action="client.note", actor="maya", role="cpa"):
    return audit.record(conn, actor, role, action, {"n": secrets.token_hex(2)}, client_id="acme")


def _below_the_app(conn, table, sql, *params):
    """Change hashed rows the way only the store's owner can: on SQLite with the append-only triggers dropped for the
    statement, on PostgreSQL as the table owner with the immutability triggers off."""
    if db.is_pg(conn):
        from agentledger import pg

        owner = pg.connect(pg.dsn(direct=True))
        try:
            owner.execute(f'SET search_path TO "{conn.schema}", public')
            owner.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
            owner.execute(sql.replace("?", "%s"), params)
            owner.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")
        finally:
            owner.close()
        return
    raw = db._raw(conn)
    for op in ("update", "delete"):
        raw.execute(f"DROP TRIGGER IF EXISTS {table}_no_{op}")
    raw.execute(sql, params)
    for op in ("UPDATE", "DELETE"):
        raw.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END")


def test_an_anchor_is_written_only_when_the_chain_moved(biz, keyring):  # noqa: F811
    a = _anchors(biz, keyring)
    before = a.head()
    assert before["seq"] > 0 and before["count"] == before["seq"]            # the fixture's client and accounts are audited
    first = a.anchor()
    assert first["anchored"] and first["previous"] is None
    assert first["lock"]["status"] == "unchecked" and first["lock_event"] == "evidence.anchor_lock_missing"   # file blobs: the gap, said
    key = first["anchor"]["key"]
    assert KEY.fullmatch(key) and first["anchor"]["signed"]
    obj = json.loads(a.store.get(key))
    assert obj["audit"] == a.head() and obj["audit"]["seq"] == before["seq"] + 1       # the gap record itself is anchored
    assert obj["signature"]["alg"] == "HMAC-SHA256" and obj["previous"] is None and obj["build"] and obj["firm_id"] == FIRM
    assert obj["lock"]["status"] == "unchecked" and obj["workflow_events"] == {"streams": 0, "events": 0, "heads": []}
    assert [r["object_key"] for r in db.rows(biz.conn, "SELECT object_key FROM audit_anchors")] == [key]
    again = a.anchor()
    assert not again["anchored"] and again["reason"] == "head unchanged" and again["lock_event"] is None and again["unanchored"] == 0
    audit.record(biz.conn, ANCHOR_AGENT_ID, "agent", "agent.run", {"ok": True})   # the job's own run record never justifies an anchor
    assert a.anchor()["reason"] == "head unchanged"
    _event(biz.conn)
    third = a.anchor()
    assert third["anchored"] and third["previous"] == {"key": key, "seq": obj["audit"]["seq"]} and third["unanchored"] == 1
    obj3 = json.loads(a.store.get(third["anchor"]["key"]))
    assert obj3["previous"] == {"key": key, "sha256": hashlib.sha256(a.store.get(key)).hexdigest()}   # anchors chain too
    assert a.keys() == [key, third["anchor"]["key"]]
    report = a.check()
    assert report["ok"] and report["signed"] and report["count"] == 2 and report["latest"]["key"] == third["anchor"]["key"]
    assert report["chain"]["ok"] and not report["anchor_missing"] and report["unanchored"] == 0 and report["lock"]["status"] == "unchecked"
    assert all(r["verified_at"] for r in db.rows(biz.conn, "SELECT verified_at FROM audit_anchors"))
    # The index records anchors once and their verification afterwards; nothing else, on either backend.
    with pytest.raises(sqlite3.DatabaseError):
        biz.conn.execute("DELETE FROM audit_anchors")
    with pytest.raises(sqlite3.DatabaseError):
        biz.conn.execute("UPDATE audit_anchors SET head_hash = 'x'")


def test_anchors_are_signed_with_the_firm_key_and_forgeries_are_detected(biz, keyring):  # noqa: F811
    a = _anchors(biz, keyring)
    key = a.anchor()["anchor"]["key"]
    obj = json.loads(a.store.get(key))
    assert a.signature_status(obj) == "valid"
    assert Anchors(biz.conn, a.store, firm_id="lake-tax", keyring=keyring, location="x").signature_status(obj) == "invalid"   # another firm's key
    assert Anchors(biz.conn, a.store, firm_id=None, keyring=None, location="x").signature_status(obj) == "unverifiable"       # no key at all
    # A forged anchor (the head changed, the signature kept) fails its signature; the chain contradicts it as well.
    forged = {**obj, "audit": {**obj["audit"], "hash": "0" * 64}}
    fkey = f"{obj['at'][:10]}/{obj['audit']['seq']:012d}-forged.json"
    a.store.put(fkey, json.dumps(forged).encode())
    report = a.check()
    assert report["signature_failures"] == [fkey] and not report["ok"]
    assert [x["anchor"] for x in report["chain_rewritten"]] == [fkey] and key not in report["signature_failures"]
    a.store.delete(fkey)
    # An anchor written without a firm key (the development store) is visible as unsigned, and the report says so.
    unsigned = {**obj, "signature": None}
    ukey = f"{obj['at'][:10]}/{obj['audit']['seq']:012d}-unsigned.json"
    a.store.put(ukey, json.dumps(unsigned).encode())
    report = a.check()
    assert report["unsigned"] == [ukey] and report["signed"] is False and report["ok"]
    # A development store's anchors are unsigned by construction: Anchors.for_foundry finds no keyring on its vault.
    dev = Anchors.for_foundry(biz)
    assert dev.firm_id is None and dev.keyring is None and dev.anchor()["reason"] == "head unchanged"   # same chain, same index
    _event(biz.conn)
    out = dev.anchor()
    assert out["anchored"] and out["anchor"]["signed"] is False and json.loads(dev.store.get(out["anchor"]["key"]))["signature"] is None


def test_a_rewritten_or_truncated_chain_is_reported_with_the_anchor_that_proves_it(biz, keyring):  # noqa: F811
    a = _anchors(biz, keyring)
    anchored = a.anchor()["anchor"]
    _event(biz.conn)
    _event(biz.conn)
    seq, h = anchored["seq"], anchored["hash"]
    _below_the_app(biz.conn, "audit", "UPDATE audit SET hash = ? WHERE seq = ?", "f" * 64, seq)
    report = a.check()
    assert report["chain_rewritten"] == [{"kind": "chain_rewritten", "anchor": anchored["key"], "seq": seq, "anchored_hash": h, "found": "f" * 64}]
    assert not report["ok"] and not report["chain"]["ok"]
    out = a.anchor()                                                       # a contradicted chain is never anchored again
    assert not out["anchored"] and out["reason"] == "chain_rewritten" and out["mismatch"]["anchor"] == anchored["key"]
    assert a.keys() == [anchored["key"]]
    _below_the_app(biz.conn, "audit", "UPDATE audit SET hash = ? WHERE seq = ?", h, seq)
    assert a.check()["ok"]
    _below_the_app(biz.conn, "audit", "DELETE FROM audit WHERE seq >= ?", seq)
    report = a.check()
    assert report["chain"]["ok"]                                           # the surviving rows recompute: only the anchor sees the cut
    [cut] = report["chain_truncated"]
    assert cut["anchor"] == anchored["key"] and cut["seq"] == seq and cut["current_seq"] < seq and not report["ok"]
    assert a.anchor()["reason"] == "chain_truncated"


def test_anchor_missing_after_the_threshold(biz, keyring, monkeypatch):  # noqa: F811
    t0 = datetime.now(timezone.utc)
    clock = {"t": t0}
    a = _anchors(biz, keyring, now=lambda: clock["t"], max_age=24)
    report = a.check()
    assert report["count"] == 0 and report["anchor_missing"] is False and report["unanchored"] > 0 and report["unanchored_since"]
    clock["t"] = t0 + timedelta(hours=25)
    assert a.check()["anchor_missing"] is True                             # a day of unanchored activity
    a.anchor()
    report = a.check()
    assert report["anchor_missing"] is False and report["unanchored"] == 0
    audit.record(biz.conn, ANCHOR_AGENT_ID, "agent", "agent.run", {"ok": True})
    clock["t"] = t0 + timedelta(hours=50)
    report = a.check()
    assert report["anchor_missing"] is False and report["unanchored"] == 0    # the job's own run records are not activity
    _event(biz.conn)                                                       # recorded at the wall clock: 50 hours "ago"
    report = a.check()
    assert report["anchor_missing"] is True and report["unanchored"] == 1
    monkeypatch.setenv(MAX_AGE_ENV, "72")
    assert _anchors(biz, keyring, now=lambda: clock["t"]).check()["anchor_missing"] is False   # the threshold is configurable
    assert _anchors(biz, keyring, now=lambda: clock["t"]).max_age == timedelta(hours=72)


class _Store:
    """An object store the probe cannot tell from R2: a FileBlobs behind the BlobStore interface."""

    def __init__(self, root):
        self.inner = blobstore.FileBlobs(root)
        self.deleted: list[str] = []

    def put(self, key, data):
        self.inner.put(key, data)

    def get(self, key):
        return self.inner.get(key)

    def exists(self, key):
        return self.inner.exists(key)

    def keys(self, prefix=""):
        return self.inner.keys(prefix)

    def delete(self, key):
        self.deleted.append(key)
        self.inner.delete(key)


class _Locked(_Store):
    """What a bucket lock rule on anchors/ does: the delete is refused and the object stays."""

    def delete(self, key):
        if key.startswith("probe/") or key.endswith(".json"):
            raise PermissionError("AccessDenied: the object is retained by a bucket lock rule")
        super().delete(key)


def test_the_lock_probe_reports_the_gap_and_confirms_a_refusing_store(biz, keyring):  # noqa: F811
    def lock_events():
        return [e["action"] for e in reversed(audit.events(biz.conn, limit=1000)) if e["action"].startswith("evidence.anchor_lock")]

    unlocked = _anchors(biz, keyring, store=_Store(biz.paths.data / "anchors"))
    out = unlocked.anchor()
    assert out["lock"]["checked"] and out["lock"]["locked"] is False and out["lock"]["status"] == "missing"
    assert "no bucket lock protects" in out["lock"]["detail"] and out["lock"]["key"].startswith("probe/")
    assert unlocked.store.deleted == [out["lock"]["key"]] and not unlocked.store.exists(out["lock"]["key"])   # the probe went through
    assert lock_events() == ["evidence.anchor_lock_missing"] and out["lock_event"] == "evidence.anchor_lock_missing"
    assert json.loads(unlocked.store.get(out["anchor"]["key"]))["lock"]["status"] == "missing"
    unlocked.anchor()
    assert lock_events() == ["evidence.anchor_lock_missing"]              # the same outcome again: no second record
    assert unlocked.check()["lock"]["status"] == "missing"
    locked = _anchors(biz, keyring, store=_Locked(biz.paths.data / "anchors"))
    out = locked.anchor()
    assert out["lock"]["locked"] is True and out["lock"]["status"] == "locked" and "refused" in out["lock"]["detail"]
    assert locked.store.exists(out["lock"]["key"])                         # the probe object stays, as a locked anchor would
    assert lock_events() == ["evidence.anchor_lock_missing", "evidence.anchor_lock_confirmed"]
    assert out["anchored"] and json.loads(locked.store.get(out["anchor"]["key"]))["lock"]["status"] == "locked"
    probes = [k for k in locked.store.keys() if k.startswith("probe/")]
    locked.anchor()
    assert [k for k in locked.store.keys() if k.startswith("probe/")] == probes   # one probe object a day, reused while it exists
    assert locked.check()["lock"]["status"] == "locked" and lock_events()[-1] == "evidence.anchor_lock_confirmed"
    with pytest.raises(PermissionError):
        locked.store.delete(out["anchor"]["key"])
    assert _anchors(biz, keyring).anchor()["lock"]["status"] == "unchecked"       # back to file blobs: the gap is recorded again
    assert lock_events()[-1] == "evidence.anchor_lock_missing"


def test_workflow_stream_heads_are_anchored_and_checked(fam, keyring):  # noqa: F811
    from agentledger.returns.store import Returns

    R = Returns(fam.conn, fam.kb, segregation=False)
    rid = R.create("rivera", 2026, "maya", household())
    a = Anchors(fam.conn, blobstore.FileBlobs(fam.paths.data / "anchors"), firm_id=FIRM, keyring=keyring, location="file:test")
    out = a.anchor()
    obj = json.loads(a.store.get(out["anchor"]["key"]))
    heads = obj["workflow_events"]["heads"]
    assert [h[0] for h in heads] == [rid] and obj["workflow_events"]["streams"] == 1 and obj["workflow_events"]["events"] >= 1
    assert a.check()["ok"]
    wid, seq, h = heads[0]
    _below_the_app(fam.conn, "workflow_events", "UPDATE workflow_events SET hash = ? WHERE workflow_id = ? AND seq = ?", "e" * 64, wid, seq)
    report = a.check()
    assert report["workflow_rewritten"] == [{"anchor": out["anchor"]["key"], "workflow_id": rid, "seq": seq, "anchored_hash": h, "found": "e" * 64}]
    assert not report["ok"] and report["chain"]["ok"]                      # the audit chain itself is untouched
    _below_the_app(fam.conn, "workflow_events", "UPDATE workflow_events SET hash = ? WHERE workflow_id = ? AND seq = ?", h, wid, seq)
    assert a.check()["ok"]
    _below_the_app(fam.conn, "workflow_events", "DELETE FROM workflow_events WHERE workflow_id = ? AND seq = ?", wid, seq)
    report = a.check()
    assert report["workflow_truncated"] == [{"anchor": out["anchor"]["key"], "workflow_id": rid, "seq": seq, "anchored_hash": h}] and not report["ok"]


@pytest.mark.skipif(not os.environ.get("AGENTLEDGER_TEST_S3_ENDPOINT"), reason="set AGENTLEDGER_TEST_S3_ENDPOINT to a local S3 server")
def test_anchors_live_under_their_own_prefix_and_the_local_server_reports_no_lock(biz, keyring, monkeypatch):  # noqa: F811
    import boto3

    from agentledger.security.vault import Vault

    endpoint = os.environ["AGENTLEDGER_TEST_S3_ENDPOINT"]
    bucket = "agentledger-test-" + secrets.token_hex(4)
    creds = {"aws_access_key_id": os.environ.get("AGENTLEDGER_TEST_S3_KEY", "test"),
             "aws_secret_access_key": os.environ.get("AGENTLEDGER_TEST_S3_SECRET", "test")}
    s3 = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1", **creds)
    s3.create_bucket(Bucket=bucket)
    for k, v in {"AGENTLEDGER_BLOBS": "s3", "AGENTLEDGER_BLOB_ENDPOINT": endpoint, "AGENTLEDGER_BLOB_BUCKET": bucket,
                 "AGENTLEDGER_BLOB_ACCESS_KEY_ID": creds["aws_access_key_id"],
                 "AGENTLEDGER_BLOB_SECRET_ACCESS_KEY": creds["aws_secret_access_key"]}.items():
        monkeypatch.setenv(k, v)

    def listed(prefix=""):
        return sorted(o["Key"] for o in s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", []))

    try:
        store, location = blobstore.anchors_for_firm(biz.paths.data / "anchors", FIRM)
        assert isinstance(store, blobstore.S3Blobs) and store.prefix == f"anchors/{FIRM}/" and location == f"s3:{bucket}/anchors/{FIRM}/"
        a = Anchors(biz.conn, store, firm_id=FIRM, keyring=keyring, location=location)
        out = a.anchor()
        assert out["anchored"] and out["lock"]["checked"] and out["lock"]["locked"] is False and out["lock"]["status"] == "missing"
        assert out["lock_event"] == "evidence.anchor_lock_missing"
        # The probe was deleted (no lock on this server: the gap is reported, never hidden); the anchor sits under
        # anchors/<firm>/, apart from the firm's documents under firms/<firm>/.
        assert listed() == [f"anchors/{FIRM}/{out['anchor']['key']}"]
        report = a.check()
        assert report["ok"] and report["signed"] and report["lock"]["status"] == "missing" and report["count"] == 1
        vault = Vault(biz.paths.vault, keyring, FIRM)
        loc = vault.put(b"a sealed document")
        assert listed("firms/") == [f"firms/{FIRM}/{loc.removeprefix('blob:')}"]
        assert blobstore.destroy_firm(FIRM) == "1 object(s) deleted from the object store"   # offboarding leaves the anchors
        assert listed() == [f"anchors/{FIRM}/{out['anchor']['key']}"] and a.check()["ok"]
    finally:
        for key in listed():
            s3.delete_object(Bucket=bucket, Key=key)
        s3.delete_bucket(Bucket=bucket)


def test_the_integrity_endpoint_the_cli_and_the_agent_report_anchors(api):  # noqa: F811
    from typer.testing import CliRunner

    from test_tenancy import PW, accept, enrol

    from agentledger import cli

    mod, c = api
    mod.PLATFORM.bootstrap_admin("ops@agentledger.example", "Ops", PW)
    step = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    ops = enrol(c, step["challenge"], step["secret"])
    r = c.post("/api/platform/firms", json={"id": FIRM, "name": "Rivera CPA", "admin_email": "maya@rivera.example"}, headers=ops)
    admin = accept(c, r.json()["admin_invite_token"], "Maya")
    tok = c.post("/api/auth/invite", json={"email": "lee@rivera.example", "role": "cpa"}, headers=admin).json()["invite_token"]
    lee = accept(c, tok, "Lee")
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=lee).status_code == 200
    r = c.get("/api/evidence/integrity", headers=lee)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["live_documents_missing_bytes"] == []           # the existing fields stay
    anchors = body["anchors"]
    assert anchors["count"] == 0 and anchors["latest"] is None and anchors["anchor_missing"] is False and anchors["unanchored"] >= 1
    assert anchors["store"].startswith("file:") and anchors["chain"]["ok"] and anchors["ok"] and anchors["max_age_hours"] == 24
    assert c.get("/api/evidence/integrity", headers=admin).status_code == 403          # a reviewer's report, as before
    # The scheduled job for the firm (what the API's scheduler runs hourly through the evidence-anchor agent).
    ctx = mod.firm_context(FIRM)
    rec = ctx.foundry.run("evidence-anchor")
    assert rec["ok"], rec
    assert rec["stats"]["anchored"] is True and rec["stats"]["lock"] == "unchecked" and rec["stats"]["anchor"]
    assert [x["type"] for x in rec["alerts"]] == ["anchor_lock_missing"]
    body = c.get("/api/evidence/integrity", headers=lee).json()
    a = body["anchors"]
    assert a["count"] == 1 and a["signed"] is True and a["latest"]["key"] == rec["stats"]["anchor"] and a["latest"]["signature"] == "valid"
    assert a["lock"]["status"] == "unchecked" and a["ok"] and body["ok"]
    rec = ctx.foundry.run("evidence-anchor")                                            # nothing but its own run record since
    assert rec["ok"] and rec["stats"]["anchored"] is False and rec["stats"]["reason"] == "head unchanged"
    # The command line reaches the same store and key.
    res = CliRunner().invoke(cli.app, ["evidence", "anchor", "--firm", FIRM])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)[FIRM]["reason"] == "head unchanged"
    res = CliRunner().invoke(cli.app, ["evidence", "anchor", "--all"])
    assert res.exit_code == 0, res.output
    assert set(json.loads(res.output)) == {FIRM}
    # The audit record of the run itself is in the chain, and nothing new is anchored because of it.
    actions = [e["action"] for e in audit.events(ctx.conn, limit=50)]
    assert actions.count("agent.run") == 2 and actions.count("evidence.anchor_lock_missing") == 1
