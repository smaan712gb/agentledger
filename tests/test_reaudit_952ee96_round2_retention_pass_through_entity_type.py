"""Second adversarial review of the retention fix (re-audit of 952ee96), pass-through entities: the free-text entity_type
alone decided whether an entity's records wait for its owners' returns, so Harbor Fabrication Inc (entity_type
"corporation") filing a 2026 Form 1120-S had its sales invoice deletable from 2034-07-15 although its shareholder's
2026 return, filed 2032-06-01 and recorded, was assessable to 2035-06-01 (Bufferd v. Commissioner, 506 U.S. 523).
Asserted now: the year's filed income return decides (an 1120-S makes the client pass-through, an 1120 does not), a
recorded owners_filed is always counted, and the invoice is kept until the shareholder's return is on record, then to
2039-06-01 plus the margin.
"""

from __future__ import annotations

from datetime import date, timedelta

from agentledger.domains.packs import Packs
from agentledger.domains.service import onboard
from agentledger.ledger import store
from test_reaudit_952ee96_premature_retention import GRACE, _event, confirm, deletable_from, due, end_of
from test_reaudit_952ee96_review_retention_later_year_support import put_doc

DAY = timedelta(days=1)


def _client_invoice(f, client_id: str, name: str, entity_type: str, **facts) -> None:
    """A business client and its 2026 sales invoice, confirmed 2026."""
    store.add_client(f.conn, id=client_id, name=name, kind="business", entity_type=entity_type, tax_id_last4="7788",
                     domain="general", facts=facts)
    onboard(f.conn, Packs(f.paths.domains), client_id, "general")
    put_doc(f.conn, f.vault, "d_sales_invoice", client_id, doc_type="Invoice", tax_year=2026, received=date(2026, 9, 14))
    confirm(f.conn, "d_sales_invoice", 2026)


def test_an_s_corporation_recorded_as_a_corporation_waits_for_its_shareholders(biz):
    _client_invoice(biz, "harbor-fab", "Harbor Fabrication Inc", "corporation", s_election="2019-01-01")
    _event(biz.conn, "harbor-fab", 2026, "filed", "2027-03-10", "1120-S", note="IRS account transcript: 2026 Form 1120-S received 2027-03-10")
    assert end_of(biz.conn, "d_sales_invoice") == (None, "harbor-fab passes its 2026 income through to its owners, whose returns "
                                                         "are not on record as filed (record them as owners_filed) (its confirmed "
                                                         "tax year)")
    assert "d_sales_invoice" not in due(biz.conn, date(2045, 1, 1))
    _event(biz.conn, "harbor-fab", 2026, "owners_filed", "2032-06-01", "1040",
           note="sole shareholder's 2026 Form 1040 (Schedule K-1 from Harbor Fabrication) received 2032-06-01, transcript")
    assert "d_sales_invoice" not in due(biz.conn, date(2035, 6, 1))
    assert end_of(biz.conn, "d_sales_invoice") == (date(2039, 6, 1) + GRACE, "ok")
    deletable_from(biz.conn, "d_sales_invoice", date(2039, 6, 1) + GRACE + DAY)


def test_an_1120_decides_against_the_entity_type_and_recorded_owners_still_count(biz):
    """Harbor Holdings LLC (entity_type "llc", pass-through by default) elected to be taxed as a C corporation (Form
    8832) and filed its 2026 Form 1120 on 2027-04-10: its records do not wait for owners' returns (7 years from the due
    date plus the margin). Owners' returns a CPA records anyway (filed 2032-06-01) are counted."""
    _client_invoice(biz, "harbor-hold", "Harbor Holdings LLC", "llc")
    _event(biz.conn, "harbor-hold", 2026, "filed", "2027-04-10", "1120",
           note="IRS account transcript: 2026 Form 1120 received 2027-04-10 (Form 8832 election on file)")
    assert end_of(biz.conn, "d_sales_invoice") == (date(2034, 4, 15) + GRACE, "ok")
    _event(biz.conn, "harbor-hold", 2026, "owners_filed", "2032-06-01", "1040",
           note="members' 2026 Forms 1040 received 2032-06-01, transcripts")
    assert end_of(biz.conn, "d_sales_invoice") == (date(2039, 6, 1) + GRACE, "ok")
    deletable_from(biz.conn, "d_sales_invoice", date(2039, 6, 1) + GRACE + DAY)
