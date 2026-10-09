"""Second adversarial review of the retention fix (re-audit of 952ee96), the foreign tax window of a fiscal-year return:
the 10-year rule of IRC 6511(d)(3)(A) was counted from 15 April after the calendar year whatever the filing date, so the
1099-DIV (1,200.00 foreign tax) of Harbor Freight Lines' 2026 Form 1120 (year ending 2027-06-30, due 2027-10-15, filed
on time 2027-10-01 and recorded without its due date) was deletable from 2037-07-15, inside the claim window ending
2037-10-15. Asserted now: the window runs from the later of the filing and the due date: 2037-10-01 plus the margin
without the due date, 2037-10-15 plus the margin with it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agentledger.domains.packs import Packs
from agentledger.domains.service import onboard
from agentledger.evidence import records
from agentledger.ledger import store
from test_reaudit_952ee96_premature_retention import GRACE, confirm, deletable_from, due, end_of
from test_reaudit_952ee96_review_retention_later_year_support import put_doc

DAY = timedelta(days=1)
WINDOW_ENDS = date(2037, 10, 15)          # 10 years from 2027-10-15, the due date of the year ending 2027-06-30


@pytest.mark.parametrize("due_on, ten_years", [(None, date(2037, 10, 1)), ("2027-10-15", WINDOW_ENDS)],
                         ids=["due-date-not-recorded", "due-date-recorded"])
def test_a_fiscal_year_return_keeps_the_ten_year_window_of_its_due_date(biz, due_on, ten_years):
    """10 years from the filing (2027-10-01) when the due date is not recorded, from the due date (2027-10-15) when it
    is; either way past the end of the claim window."""
    store.add_client(biz.conn, id="harbor-co", name="Harbor Freight Lines Inc", kind="business", entity_type="c_corp",
                     tax_id_last4="7788", domain="general")
    onboard(biz.conn, Packs(biz.paths.domains), "harbor-co", "general")
    put_doc(biz.conn, biz.vault, "d_div_fy", "harbor-co", doc_type="1099-DIV", tax_year=2026, received=date(2027, 2, 10),
            fields={"payer_name": "Harbor International Index Fund", "box1a": "40000.00", "box7": "1200.00"})
    confirm(biz.conn, "d_div_fy", 2026)
    records.record_tax_event(biz.conn, "harbor-co", 2026, "filed", "2027-10-01", actor="lee", role="cpa", form="1120", due_on=due_on,
                             note="IRS account transcript: Form 1120 for the year 2026-07-01 to 2027-06-30 received 2027-10-01")
    assert end_of(biz.conn, "d_div_fy") == (ten_years + GRACE, "ok")
    assert "d_div_fy" not in due(biz.conn, WINDOW_ENDS)
    deletable_from(biz.conn, "d_div_fy", ten_years + GRACE + DAY)
