"""Adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv_holds_race_pg.py): a hold that a request
already in flight (another API worker, after firm_context saw the firm active) committed while delete_firm ran was
invisible to its unlocked second read and destroyed with the store, the object-store copy and the data key. Asserted
now, on PostgreSQL in both tenancy modes: the seal's LOCK TABLE waits for that request, sees the hold and refuses,
naming it, with the store, the hold, the object and the key intact, the firm back in use and no offboarding record;
and a request that began before the seal but inserts after it is refused by the database (INSERT revoked), so no
hold is acknowledged.
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
import time

import pytest

from agentledger import audit, db
from agentledger.evidence import blobs as blobstore
from agentledger.evidence import offboarding, records
from agentledger.ledger import store
from agentledger.security.crypto import CryptoError, Keyring
from agentledger.security.platform import AuthError, Platform
from agentledger.security.vault import Vault

pytestmark = pytest.mark.skipif(db.backend() != "postgres", reason="needs AGENTLEDGER_DATABASE=postgres (real concurrency)")

FIRM, CLIENT = "race-cpa", "jordan-lee"
OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
REASON = "IRS examination notice dated 2026-09-30: preserve all 2026 records"
W2 = b"Form W-2 2026 Employer: Brightline LLC Employee: Jordan Lee SSN 400-00-0001 Wages 61,200.00"


class MemoryS3:
    """The S3 calls S3Blobs makes, against one in-memory bucket (stands in for R2)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = bytes(Body)

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)

    def list_objects_v2(self, *, Bucket, Prefix="", ContinuationToken=None):
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)], "IsTruncated": False}


@pytest.fixture
def bucket(monkeypatch):
    b = MemoryS3()
    monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")
    monkeypatch.setenv("AGENTLEDGER_BLOBS", "s3")
    monkeypatch.setattr(blobstore, "for_firm",
                        lambda root, firm_id: blobstore.S3Blobs(bucket="evidence", prefix=f"firms/{firm_id}", client=b))
    return b


def _firm_with_w2(tmp_path):
    plat = Platform(tmp_path, dev=True)
    plat.create_firm(FIRM, "Race CPA", by="ops")
    assert plat.firm(FIRM)["status"] == "active"
    path = plat.tenant_dir(FIRM) / "state" / "agentledger.db"
    conn = db.open_store(path)
    try:
        store.add_client(conn, id=CLIENT, name="Jordan Lee", kind="individual")
        loc = Vault(plat.tenant_dir(FIRM) / "vault", plat.keys, FIRM).put(W2, owner="doc_w2")
        sha = hashlib.sha256(W2).hexdigest()
        conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, "
                     "vault_path, doc_type, tax_year) VALUES ('doc_w2', ?, ?, 'w2-2026.txt', 'text/plain', 'upload', ?, 'filed', "
                     "?, 'W-2', 2026)", (CLIENT, sha, audit.now(), loc))
        records.add_version(conn, "doc_w2", loc, sha, len(W2), "intake-agent")
    finally:
        conn.close()
    return plat, path


@pytest.fixture(params=["schema", "database"])
def tenancy(request, monkeypatch, bucket):
    """Both tenancy modes. A database per firm is created and dropped on the local server in place of Neon's API."""
    if request.param == "schema":
        yield "schema"
        return
    from agentledger import pg
    from agentledger.pg import compat, provision

    owner_url = pg.migration_url()
    created: list[str] = []

    class LocalNeon:
        def databases(self):
            c = pg.connect(owner_url)
            try:
                return [r[0] for r in c.execute("SELECT datname FROM pg_database").fetchall()]
            finally:
                c.close()

        def create_database(self, name, owner):
            c = pg.connect(owner_url)
            try:
                c.execute(f'CREATE DATABASE "{name}" OWNER "{owner}"')
                created.append(name)
            finally:
                c.close()

        def delete_database(self, name):
            c = pg.connect(owner_url)
            try:
                c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                c.close()

    monkeypatch.setattr(provision, "Neon", LocalNeon)
    monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "database")
    yield "database"
    for s in list(db._OPEN):
        if isinstance(s, compat.PgStore) and s.database in created:
            s.close()
    c = pg.connect(owner_url)
    try:
        for name in created:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        c.close()


def _other_worker(path):
    """A store connection of another API worker (another process): not in this process's registry, so nothing here
    closes it."""
    worker = db.open_store(path)
    db._OPEN.discard(worker)
    return worker


def _key_alive(plat) -> bool:
    try:
        Keyring(plat.conn, plat.keys.master).current_version(FIRM)
        return True
    except CryptoError:
        return False


def _active(path) -> list[int]:
    if not db.store_exists(path):
        return []
    conn = db.open_store(path)
    try:
        return [h["id"] for h in records.active_holds(conn)]
    finally:
        conn.close()


def test_hold_committed_while_the_seal_waits_is_seen_and_kept(tmp_path, monkeypatch, bucket, tenancy):
    from agentledger import pg

    plat, path = _firm_with_w2(tmp_path)
    assert plat.get(FIRM)["resource"] == tenancy
    worker = _other_worker(path)
    inserted, release = threading.Event(), threading.Event()
    real_record = audit.record
    placed: dict = {}

    def record(c, *a, **kw):
        out = real_record(c, *a, **kw)
        if threading.current_thread().name == "api-worker-2":
            placed["pid"] = c.execute("SELECT pg_backend_pid()").fetchone()[0]
            inserted.set()                     # the hold row and its audit record are written; COMMIT not sent yet
            release.wait(60)
        return out

    monkeypatch.setattr(audit, "record", record)

    def place_hold():                          # POST /api/clients/{id}/holds, after firm_context saw the firm active
        try:
            placed["id"] = records.place_hold(worker, client_id=CLIENT, reason=REASON, actor="u_lee", role="cpa")
        except Exception as exc:               # pragma: no cover - diagnostic
            placed["error"] = f"{type(exc).__name__}: {exc}"

    request = threading.Thread(target=place_hold, name="api-worker-2", daemon=True)
    request.start()
    assert inserted.wait(30), placed
    result: dict = {}

    def offboard():
        try:
            plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
            result["outcome"] = "went ahead"
        except AuthError as e:
            result["refused"] = str(e)

    ops = threading.Thread(target=offboard, name="ops", daemon=True)
    ops.start()
    watch = pg.connect(pg.migration_url())
    blocked: list = []
    try:                                       # the request commits once the seal is seen waiting for it
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and ops.is_alive() and not blocked:
            blocked = [r[0] for r in watch.execute("SELECT query FROM pg_stat_activity WHERE %s = ANY (pg_blocking_pids(pid))",
                                                    (placed["pid"],)).fetchall()]
            time.sleep(0.05)
    finally:
        watch.close()
        release.set()
        request.join(30)
        ops.join(60)
        worker.close()

    assert blocked and "LOCK TABLE" in blocked[0] and "legal_holds" in blocked[0], (blocked, result)
    assert "id" in placed, placed
    assert f"#{placed['id']} ({CLIENT})" in result.get("refused", ""), result
    assert _active(path) == [placed["id"]]
    assert len(bucket.objects) == 1 and _key_alive(plat)
    assert plat.firm(FIRM)["status"] == "active" and plat.offboarding_records(FIRM) == [] and not plat._stages(FIRM)
    assert "firm_delete_refused" in [e["event"] for e in plat.events(FIRM)]


def test_request_that_inserts_after_the_seal_is_refused_by_the_database(tmp_path, monkeypatch, bucket):
    """The request's transaction began (it holds the client's evidence lock) before the seal, but its INSERT runs only
    after the seal committed: the seal does not wait for it (it has touched no hold yet), and the INSERT is refused."""
    plat, path = _firm_with_w2(tmp_path)
    worker = _other_worker(path)
    began, go = threading.Event(), threading.Event()
    real_lock = records.evidence_lock
    attempt: dict = {}

    def evidence_lock(conn, *clients):
        real_lock(conn, *clients)
        if threading.current_thread().name == "api-worker-2":
            began.set()
            go.wait(60)

    monkeypatch.setattr(records, "evidence_lock", evidence_lock)

    def place_hold():
        try:
            attempt["id"] = records.place_hold(worker, client_id=CLIENT, reason=REASON, actor="u_lee", role="cpa",
                                               document_id="doc_w2")
        except Exception as exc:
            attempt["refused"] = f"{type(exc).__name__}: {exc}"

    request = threading.Thread(target=place_hold, name="api-worker-2", daemon=True)
    request.start()
    assert began.wait(30), attempt
    real_destroy = offboarding.destroy

    def destroy(ref, **kw):                    # the seal has committed: let the request insert now, then remove
        go.set()
        request.join(30)
        return real_destroy(ref, **kw)

    monkeypatch.setattr(offboarding, "destroy", destroy)
    try:
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    finally:
        go.set()
        request.join(30)
        worker.close()

    assert "id" not in attempt and "permission denied for table legal_holds" in attempt.get("refused", ""), attempt
    assert plat.firm(FIRM)["status"] == "deleted" and not bucket.objects and not _key_alive(plat)
    [rec] = plat.offboarding_records(FIRM)
    assert json.loads(rec["summary"])["holds"] == [] and json.loads(rec["summary"])["documents"] == 1
