"""Second adversarial review of the write-ahead deletion (re-audit of 952ee96, findings 4 and 5): a return populated
while a retention run deleted one of its documents recorded its use of the tombstone (the evidence lock covered the
insert of the use, not the read of the documents it rests on), so an unfiled return relied on deleted evidence and
nothing said so. Asserted now (both backends): a run deciding between the population's read and its save deletes the
document (no use was on record), the save does not index the deleted document, and its item is an orphan every gate
refuses until a person decides; a run deciding while the save holds the client's evidence lock waits for it and keeps
the document, which the unfiled return now relies on.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date

import pytest

from agentledger import db
from agentledger.evidence import records
from agentledger.ledger import store
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError
from test_evidence import _doc

PG = db.backend() == "postgres"
TODAY = date(2034, 8, 1)              # the annual retention run, seven years on
REASON = "annual retention review"
W2 = "doc_w2_2026"


class Worker:
    """Another API worker: `fn` runs in its own thread, on its own database connection."""

    def __init__(self, conn, fn):
        self.result, self.error, self.pid = None, None, None
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(conn, fn), daemon=True)
        self.thread.start()
        self._ready.wait(30)

    def _run(self, conn, fn):
        try:
            if PG:
                self.pid = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
            self._ready.set()
            self.result = fn()
        except Exception as exc:
            self.error = exc
        finally:
            self._ready.set()

    def blocked(self, conn) -> bool:
        """Waiting for a lock the caller's transaction holds: on PostgreSQL an ungranted lock of its session in
        pg_locks; on SQLite still running after 1.5 s (waiting for the write lock)."""
        if not PG:
            self.thread.join(1.5)
            return self.thread.is_alive()
        end = time.monotonic() + 15
        while time.monotonic() < end and self.thread.is_alive():
            if conn.execute("SELECT 1 FROM pg_locks WHERE pid = ? AND NOT granted", (self.pid,)).fetchone():
                return True
            time.sleep(0.02)
        return False

    def finish(self) -> "Worker":
        self.thread.join(60)
        assert not self.thread.is_alive(), "the other worker never finished"
        return self


def _jordan(biz):
    """Jordan's 2026 W-2 (received 2027-02-01; the year filed elsewhere on 2027-04-10, on record) is due for deletion
    when a 2026 return is prepared in AgentLedger in 2034 (the IRS asks about the year)."""
    conn, vault = biz.conn, biz.vault
    store.add_client(conn, id="jordan", name="Jordan Lee", kind="individual", tax_id_last4="0007",
                     facts={"taxpayer_ssn_last4": "0007", "taxpayer_name": "Jordan Lee"})
    _doc(conn, vault, W2, "jordan", b"Form W-2 2026 Employer: Brightline LLC Wages 48,000.00", "2034-05-02", doc_type="W-2")
    conn.execute("UPDATE documents SET tax_year = 2026, fields = ? WHERE id = ?",
                 (json.dumps({"employer_name": "Brightline LLC", "recipient_tin_last4": "0007", "box1": "48000.00",
                              "box2": "5100.00"}), W2))
    records.record_tax_event(conn, "jordan", 2026, "filed", "2027-04-10", actor="maya", role="cpa", form="1040",
                             note="IRS account transcript shows the 2026 return received 2027-04-10")
    R = Returns(conn, biz.kb)
    rid = R.create("jordan", 2026, "maya", {"taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0007",
                                                         "dob": "1980-01-01"}})
    assert [d["id"] for d in records.due_for_deletion(conn, TODAY)] == [W2]
    return R, rid


def _run(conn, vault):
    return records.purge_expired(conn, vault, TODAY, actor="lee", role="cpa", attested=True, reason=REASON)


def _uses(conn, rid):
    return [r["document_id"] for r in db.rows(conn, "SELECT document_id FROM return_document_uses WHERE return_id = ?", rid)]


def test_a_document_deleted_between_the_read_and_the_save_is_an_orphan_the_gates_refuse(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    R, rid = _jordan(biz)
    real = Returns._population
    seen: dict = {}

    def population(self, rid_, v):
        pop = real(self, rid_, v)
        if "run" not in seen:                    # the documents are read; the annual retention run completes now
            seen["run"] = Worker(conn, lambda: _run(conn, vault)).finish()
        return pop

    monkeypatch.setattr(Returns, "_population", population)
    out = R.populate_from_documents(rid, "maya")
    monkeypatch.setattr(Returns, "_population", real)
    run = seen["run"]
    assert run.error is None and [r["document_id"] for r in run.result] == [W2] and run.result.failures == []
    assert out["documents"] == [W2]                                       # read before the run deleted it
    assert db.one(conn, "SELECT deleted_at FROM documents WHERE id = ?", W2)["deleted_at"]
    assert _uses(conn, rid) == []                                        # a deleted document is never indexed
    [item] = R.latest(rid)["inputs"]["w2s"]
    assert item["source_document"] == W2                                 # the values read stay on the return ...
    with pytest.raises(TransitionError, match="whose document left the return"):
        R.submit_for_review(rid, "maya")                                 # ... and no gate lets them through
    R.populate_from_documents(rid, "maya")
    [orphan] = [c for c in R.conflicts(rid) if c["anchor"].startswith("orphan:")]
    assert orphan["document_id"] == W2
    with pytest.raises(TransitionError, match="remove each one or keep it with a reason"):
        R.submit_for_review(rid, "maya")
    assert _uses(conn, rid) == []


def test_a_run_deciding_while_the_population_saves_waits_and_keeps_the_document(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    R, rid = _jordan(biz)
    real = Returns._record_uses
    seen: dict = {}

    def record_uses(self, rid_, documents, version):
        real(self, rid_, documents, version)
        if W2 in documents and "run" not in seen:
            # The use is written, not committed: the save holds evidence:jordan. The annual retention run starts now.
            seen["run"] = Worker(conn, lambda: _run(conn, vault))
            seen["waited"] = seen["run"].blocked(conn)

    monkeypatch.setattr(Returns, "_record_uses", record_uses)
    R.populate_from_documents(rid, "maya")
    monkeypatch.setattr(Returns, "_record_uses", real)
    assert "run" in seen, "the population never recorded its use of the W-2"
    run = seen["run"].finish()
    assert seen["waited"] is True, "the run did not wait for the save holding the client's evidence lock"
    assert run.error is None and list(run.result) == [] and run.result.failures == []
    assert db.one(conn, "SELECT deleted_at FROM documents WHERE id = ?", W2)["deleted_at"] is None
    assert _uses(conn, rid) == [W2] and db.rows(conn, "SELECT id FROM deletion_receipts") == []
    assert f"return {rid} (2026), which relied on it, is not filed" in records.explain(conn, W2)["reason"]
    assert records.due_for_deletion(conn, TODAY) == []
