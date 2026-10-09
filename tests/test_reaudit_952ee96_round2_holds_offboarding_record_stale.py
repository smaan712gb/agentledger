"""Second adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv2_holds_offboarding_record_stale.py):
delete_firm wrote the firm_offboarding record only for the first attempt, so after a cancelled offboarding the
permanent record of what a deleted firm's store held was the first attempt's: the hold placed in between (the reason
for the cancellation), the documents added and the second request's reason appeared nowhere once the store was gone.
Asserted now: every successful seal appends a record, so the last one lists exactly what the store held when it was
destroyed (the hold with its release, the document, the audit head) under the request that destroyed it, the first
attempt's record is kept unchanged, the cancellation is in the event log, and the records cannot be altered.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from agentledger import audit, db
from agentledger.evidence import offboarding, records
from agentledger.ledger import store
from agentledger.security.platform import Platform

FIRM, CLIENT = "twice-cpa", "jordan-lee"
FIRST = "signed offboarding request from the firm's owner, 2026-10-01"
CANCEL = "subpoena received 2026-10-02: the firm's records must be preserved"
SECOND = "renewed offboarding request after the subpoena was answered, 2026-11-15"
REASON = "subpoena duces tecum dated 2026-10-02: preserve all records of Jordan Lee"
RELEASE = "subpoena answered and closed by the requesting party on 2026-11-14"
W2 = b"Form W-2 2026 Employer: Brightline LLC Employee: Jordan Lee SSN 400-00-0001 Wages 61,200.00"


@pytest.fixture(autouse=True)
def _schema_tenancy(monkeypatch):
    if db.backend() == "postgres":
        monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")


def _head(path) -> dict:
    conn = db.open_store(path)
    try:
        h = db.one(conn, "SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1")
    finally:
        conn.close()
    return {"seq": h["seq"], "hash": h["hash"]}


def test_record_of_a_re_run_offboarding_lists_what_the_store_held_when_destroyed(tmp_path, monkeypatch):
    plat = Platform(tmp_path, dev=True)
    plat.create_firm(FIRM, "Twice CPA", by="ops")
    path = plat.tenant_dir(FIRM) / "state" / "agentledger.db"
    conn = db.open_store(path)
    try:
        store.add_client(conn, id=CLIENT, name="Jordan Lee", kind="individual")
    finally:
        conn.close()
    first_head = _head(path)

    def removal_fails(ref, **kw):
        raise ConnectionError("injected: the database service is unreachable while removing the store")

    with monkeypatch.context() as m:                                  # first attempt: sealed, then interrupted
        m.setattr(offboarding, "destroy", removal_fails)
        with pytest.raises(ConnectionError):
            plat.delete_firm(FIRM, by="ops", reason=FIRST)
    assert plat.cancel_offboarding(FIRM, by="ops", reason=CANCEL) == "cancelled"     # the store is intact

    conn = db.open_store(path)                                        # the firm works on; the hold it was cancelled for
    try:
        conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, "
                     "doc_type, tax_year) VALUES ('doc_w2', ?, ?, 'w2-2026.txt', 'text/plain', 'upload', ?, 'filed', 'W-2', 2026)",
                     (CLIENT, hashlib.sha256(W2).hexdigest(), audit.now()))
        hold = records.place_hold(conn, client_id=CLIENT, reason=REASON, actor="u_lee", role="cpa")
        records.release_hold(conn, hold, reason=RELEASE, actor="u_lee", role="cpa")
    finally:
        conn.close()
    second_head = _head(path)

    plat.delete_firm(FIRM, by="ops", reason=SECOND)                   # second attempt: destroys the store
    assert plat.firm(FIRM)["status"] == "deleted" and not db.store_exists(path)

    first, last = plat.offboarding_records(FIRM)
    assert (first["reason"], last["reason"]) == (FIRST, SECOND)
    assert json.loads(first["summary"]) == {"holds": [], "documents": 0, "documents_deleted_under_retention": 0,
                                            "audit_head": first_head}
    summary = json.loads(last["summary"])
    assert [(h["id"], h["client_id"], h["reason"], h["release_reason"], bool(h["released_at"])) for h in summary["holds"]] \
        == [(hold, CLIENT, REASON, RELEASE, True)]
    assert (summary["documents"], summary["documents_deleted_under_retention"], summary["audit_head"]) == (1, 0, second_head)
    cancelled = [(e["user_id"], e["detail"]) for e in plat.events(FIRM) if e["event"] == "firm_offboarding_cancelled"]
    assert cancelled == [("ops", CANCEL)]

    for statement in ("UPDATE firm_offboarding SET summary = '{}' WHERE firm_id = ?", "DELETE FROM firm_offboarding WHERE firm_id = ?"):
        # SQLite: the append-only trigger. PostgreSQL: the platform's runtime role has no UPDATE or DELETE on the table
        # (the trigger refuses the owner as well).
        with pytest.raises(sqlite3.DatabaseError, match="append-only|permission denied"):
            plat.conn.execute(statement, (FIRM,))
    assert plat.offboarding_records(FIRM) == [first, last]
