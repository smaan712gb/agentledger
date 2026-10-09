"""Review of the fix for finding 5 (re-audit of 952ee96), the foreign tax credit claim window. The first adversarial
review found that a 2026 1099-DIV whose foreign tax the filed 2026 return claimed as a credit was deletable from
2034-07-15, inside the 10-year claim window of IRC 6511(d)(3)(A) (to 2037-04-15), and that a Form 1040-X in
preparation relying on it did not stop the deletion. Asserted now: a year whose 1099-DIV shows foreign tax keeps its
records 10 years from the due date plus the margin, and a return in preparation that relied on a document keeps it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from agentledger import coverage, db
from agentledger.evidence import records
from agentledger.returns.store import Returns
from test_reaudit_952ee96_premature_retention import GRACE, confirm, deletable_from, due, end_of, on, paper_file
from test_return_workflow import fam, household  # noqa: F401  (fixture)

DAY = timedelta(days=1)


def put_doc(conn, vault, doc_id: str, client_id: str, *, doc_type: str, received: date, tax_year: int | None = None,
            fields: dict | None = None) -> None:
    data = f"{doc_id} {doc_type} received {received}".encode()
    loc = vault.put(data)
    sha = hashlib.sha256(data).hexdigest()
    conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, vault_path, "
                 "fields, doc_type, tax_year) VALUES (?, ?, ?, ?, 'text/plain', 'upload', ?, 'filed', ?, ?, ?, ?)",
                 (doc_id, client_id, sha, f"{doc_id}.txt", f"{received.isoformat()}T15:00:00+00:00", loc, json.dumps(fields or {}),
                  doc_type, tax_year))
    records.add_version(conn, doc_id, loc, sha, len(data), "intake-agent")


DIV = {"payer_name": "Harbor International Index Fund", "recipient_tin_last4": "0001", "box1a": "2400.00",
       "box1b": "2000.00", "box7": "85.00"}


def _file_2026_with_foreign_tax(f) -> tuple[Returns, str]:
    put_doc(f.conn, f.vault, "d_div", "rivera", doc_type="1099-DIV", tax_year=2026, received=date(2027, 2, 10), fields=DIV)
    R = Returns(f.conn, f.kb, segregation=False)
    with on(date(2027, 4, 1)):
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
        st = paper_file(R, rid)
    assert st.status == "paper_filed"
    assert R.latest(rid)["result"]["forms"]["sch_3"]["1"] == "85"          # the foreign tax credit AgentLedger claimed
    assert db.one(f.conn, "SELECT 1 AS x FROM return_document_uses WHERE return_id = ? AND document_id = 'd_div'", rid)
    confirm(f.conn, "d_div", 2026)
    return R, rid


def test_foreign_tax_records_are_kept_for_the_ten_year_claim_window(fam):  # noqa: F811
    """2026 return paper-filed 2027-04-01 (deemed filed 2027-04-15) claiming an 85.00 foreign tax credit from the
    1099-DIV: kept until 10 years from the due date (2037-04-15) plus the margin, not the 7 years of other records."""
    _file_2026_with_foreign_tax(fam)
    window_ends = date(2037, 4, 15)
    assert end_of(fam.conn, "d_div") == (window_ends + GRACE, "ok")
    assert "d_div" not in due(fam.conn, window_ends - DAY)
    deletable_from(fam.conn, "d_div", window_ends + GRACE + DAY)


def test_an_amendment_in_preparation_keeps_its_documents(fam, monkeypatch):  # noqa: F811
    """On 2034-08-01 the firm starts a 1040-X for 2026 to claim the foreign taxes through Form 1116 (timely under
    6511(d)(3) until 2037-04-15). The amendment relies on the 1099-DIV and the household's other 2026 documents; while
    it is not filed none of them is ever due."""
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})
    R, rid = _file_2026_with_foreign_tax(fam)
    with on(date(2034, 8, 1)):
        am = R.start_amendment(rid, "maya")
    assert R.status(am).status == "preparing"
    used = {r["document_id"] for r in db.rows(fam.conn, "SELECT document_id FROM return_document_uses WHERE return_id = ?", am)}
    assert "d_div" in used and len(used) > 1
    for d in used:
        if not db.one(fam.conn, "SELECT retention_confirmed_at AS c FROM documents WHERE id = ?", d)["c"]:
            confirm(fam.conn, d, 2026)
    for d in sorted(used):
        end, why = end_of(fam.conn, d)
        assert end is None and f"return {am} (2026), which relied on it, is not filed" in why, (d, why)
    assert not used & due(fam.conn, date(2034, 8, 10))
    assert not used & due(fam.conn, date(2060, 1, 1))
