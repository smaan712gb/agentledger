"""Review of the fix for finding 5 (re-audit of 952ee96), an entity's records support its owners' returns. The first
adversarial review found that a disregarded LLC's invoice (entity return "not required") and an S corporation's
invoice (1120-S on time) became deletable from 2034-07-15 while the owner's 2026 return, filed late on 2032-06-01,
stayed assessable to 2035-06-01 (6501(a); Bufferd v. Commissioner for the S corporation). Asserted now: a pass-through
entity's records are kept until its owners' returns are recorded on the entity (owners_filed; a filing recorded on the
owner's own client is not linked), then counted from the owners' latest filing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal

from agentledger.domains.packs import Packs
from agentledger.domains.service import onboard
from agentledger.evidence import records
from agentledger.ledger import store
from agentledger.ledger.store import Line
from test_reaudit_952ee96_premature_retention import GRACE, _event, confirm, deletable_from, due, end_of

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


OWNERS = "passes its 2026 income through to its owners, whose returns are not on record as filed"


def test_disregarded_llc_records_follow_the_owners_return(biz):
    """Ortiz Auto Care LLC (single member, disregarded): a 2026 parts invoice supports a 2026 expense entry; no entity
    return is required; the owner's 2026 Form 1040 with Schedule C is filed late on 2032-06-01."""
    store.add_client(biz.conn, id="ortiz-llc", name="Ortiz Auto Care LLC", kind="business", entity_type="llc",
                     tax_id_last4="4471", domain="general")
    onboard(biz.conn, Packs(biz.paths.domains), "ortiz-llc", "general")
    store.add_client(biz.conn, id="pat-ortiz", name="Pat Ortiz", kind="individual", tax_id_last4="3301", domain="general")
    put_doc(biz.conn, biz.vault, "d_parts_invoice", "ortiz-llc", doc_type="Invoice", received=date(2026, 6, 3))
    store.post(biz.conn, "ortiz-llc", date(2026, 6, 3), "transmission parts", [Line("5000", Decimal("18400")),
               Line("1000", Decimal("-18400"))], source="invoice", actor="maya", document_id="d_parts_invoice")
    confirm(biz.conn, "d_parts_invoice", 2026)
    _event(biz.conn, "ortiz-llc", 2026, "not_required", "2027-03-01", "1065",
           note="single-member LLC disregarded for income tax: no entity return; owner reports on Schedule C")
    _event(biz.conn, "pat-ortiz", 2026, "filed", "2032-06-01", "1040",
           note="IRS account transcript: 2026 Form 1040 with Schedule C received 2032-06-01")
    end, why = end_of(biz.conn, "d_parts_invoice")
    assert end is None and f"ortiz-llc {OWNERS}" in why                 # the owner's own client is not linked
    assert "d_parts_invoice" not in due(biz.conn, date(2045, 1, 1))
    _event(biz.conn, "ortiz-llc", 2026, "owners_filed", "2032-06-01", "1040",
           note="owner Pat Ortiz's 2026 Form 1040 with Schedule C received 2032-06-01, transcript")
    assert "d_parts_invoice" not in due(biz.conn, date(2035, 6, 1) - DAY)
    assert end_of(biz.conn, "d_parts_invoice") == (date(2039, 6, 1) + GRACE, "ok")           # the owner's late return
    deletable_from(biz.conn, "d_parts_invoice", date(2039, 6, 1) + GRACE + DAY)


def test_s_corporation_records_follow_the_shareholders_return(biz):
    """Acme (S corporation) files its 2026 Form 1120-S on 2027-03-10; its sole shareholder's 2026 Form 1040 is filed
    late on 2032-06-01 (Bufferd: assessable to 2035-06-01)."""
    store.add_client(biz.conn, id="dana-acme", name="Dana Okafor", kind="individual", tax_id_last4="5521", domain="general")
    put_doc(biz.conn, biz.vault, "d_sales_invoice", "acme", doc_type="Invoice", received=date(2026, 9, 14))
    store.post(biz.conn, "acme", date(2026, 9, 14), "fabrication job 2219", [Line("1100", Decimal("96000")),
               Line("4000", Decimal("-96000"))], source="invoice", actor="maya", document_id="d_sales_invoice")
    confirm(biz.conn, "d_sales_invoice", 2026)
    _event(biz.conn, "acme", 2026, "filed", "2027-03-10", "1120-S", note="IRS account transcript: 2026 Form 1120-S received 2027-03-10")
    _event(biz.conn, "dana-acme", 2026, "filed", "2032-06-01", "1040",
           note="IRS account transcript: 2026 Form 1040 (Schedule K-1 from Acme) received 2032-06-01")
    end, why = end_of(biz.conn, "d_sales_invoice")
    assert end is None and f"acme {OWNERS}" in why
    assert "d_sales_invoice" not in due(biz.conn, date(2045, 1, 1))
    _event(biz.conn, "acme", 2026, "owners_filed", "2032-06-01", "1040",
           note="sole shareholder's 2026 Form 1040 received 2032-06-01, transcript")
    assert "d_sales_invoice" not in due(biz.conn, date(2035, 6, 1) - DAY)
    assert end_of(biz.conn, "d_sales_invoice") == (date(2039, 6, 1) + GRACE, "ok")
    deletable_from(biz.conn, "d_sales_invoice", date(2039, 6, 1) + GRACE + DAY)
