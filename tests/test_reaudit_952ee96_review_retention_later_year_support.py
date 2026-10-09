"""Review of the fix for finding 5 (re-audit of 952ee96), records that support later years. The first adversarial review
found that a loss-year 1099-B became deletable seven years after its own year while a later return deducting the
capital loss carryover was still open (Treas. Reg. 1.6001-1(e): kept while material), and that the purchase invoice of
equipment still owned and depreciated was deletable like any expense receipt. Asserted now: a later return that uses a
carryover keeps the client's earlier records until that return's period has run (exact date), and a document behind a
capitalized cost is kept until a CPA releases it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal

from agentledger import db
from agentledger.evidence import records
from agentledger.ledger import store as ledger
from agentledger.ledger.store import Line
from agentledger.returns.store import Returns
from test_reaudit_952ee96_premature_retention import (GRACE, _event, confirm, deletable_from, due, end_of, on,
                                                      paper_file)
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


def test_loss_year_records_outlive_the_returns_that_use_the_carryover(fam):  # noqa: F811
    """The 2020 1099-B behind a long-term capital loss; the 2020 return filed 2021-04-01 (recorded). The 2026 return,
    paper-filed in AgentLedger on 2027-04-01, deducts 3,000 of the carryover (Schedule D line 14) and is assessable
    until 2030-04-15: the 1099-B is kept as a record of 2026 too, 7 years from 2027-04-15 plus the margin."""
    put_doc(fam.conn, fam.vault, "d_1099b_2020", "rivera", doc_type="1099-B", tax_year=2020, received=date(2021, 2, 10))
    confirm(fam.conn, "d_1099b_2020", 2020)
    _event(fam.conn, "rivera", 2020, "filed", "2021-04-01", "1040",
           note="IRS account transcript: 2020 Form 1040 received 2021-04-01, Schedule D loss carried forward")
    assert end_of(fam.conn, "d_1099b_2020") == (date(2028, 4, 15) + GRACE, "ok")             # before the later return
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2027, 4, 1)):
        rid = R.create("rivera", 2026, "maya", {**household(), "capital_loss_carryover_long": "3000"})
        R.populate_from_documents(rid, "maya")
        assert paper_file(R, rid).status == "paper_filed"
    assert Decimal(R.latest(rid)["result"]["forms"]["sch_d"]["14"]) == Decimal("-3000")   # the carryover is on the return
    assert db.one(fam.conn, "SELECT detail FROM return_retention_facts WHERE return_id = ? AND kind = 'carryover'",
                  rid)["detail"] == "capital_loss_carryover_long"
    supports = records.explain(fam.conn, "d_1099b_2020")["supports"]
    assert {"client_id": "rivera", "tax_year": 2026, "why": ["a carryover is used in 2026"]} in supports
    assert "d_1099b_2020" not in due(fam.conn, date(2030, 4, 15) - DAY)
    assert end_of(fam.conn, "d_1099b_2020") == (date(2034, 4, 15) + GRACE, "ok")
    deletable_from(fam.conn, "d_1099b_2020", date(2034, 4, 15) + GRACE + DAY)


def test_asset_purchase_invoice_outlives_the_depreciation_it_supports(biz):
    """A 2026 invoice for equipment capitalized by a journal entry (debit 1500 Equipment), in the asset register as
    7-year MACRS property; every 2026-2033 Form 1120-S on record. The asset is still owned and depreciated: the invoice
    is a basis record, kept until a CPA releases it, however late."""
    from agentledger.calc.federal import Asset

    put_doc(biz.conn, biz.vault, "d_invoice_cnc", "acme", doc_type="Invoice", received=date(2026, 3, 5))
    ledger.post(biz.conn, "acme", date(2026, 3, 2), "CNC router, Lakeshore Machine invoice 8841",
                [Line("1500", Decimal("84000")), Line("1000", Decimal("-84000"))], source="invoice", actor="maya",
                document_id="d_invoice_cnc")
    ledger.add_asset(biz.conn, "acme", Asset(id="cnc-1", cost=Decimal("84000"), acquired=date(2026, 3, 2),
                                            placed_in_service=date(2026, 3, 2), recovery_years=7, book_life_years=7),
                     "CNC router (invoice d_invoice_cnc)")
    confirm(biz.conn, "d_invoice_cnc", 2026)
    for year in range(2026, 2034):
        on_ = f"{year + 1}-03-10"
        _event(biz.conn, "acme", year, "filed", on_, "1120-S", note=f"IRS account transcript: {year} Form 1120-S received {on_}")
        _event(biz.conn, "acme", year, "owners_filed", f"{year + 1}-04-10", "1040", note=f"shareholders' {year} returns, transcripts")
    assert end_of(biz.conn, "d_invoice_cnc") == (
        None, "it supports a capitalized cost (Equipment): basis records are kept until a CPA releases them")
    assert "d_invoice_cnc" not in due(biz.conn, date(2036, 12, 31))
    assert "d_invoice_cnc" not in due(biz.conn, date(2099, 1, 1))
