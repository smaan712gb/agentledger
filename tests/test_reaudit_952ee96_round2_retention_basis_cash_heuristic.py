"""Second adversarial review of the retention fix (re-audit of 952ee96), records behind a capitalized cost: the cash
test matched "cash", "clearing" or "savings" anywhere in an account's name, so the 2026 invoice behind a debit to "Cash
Registers and POS Equipment", "Land Clearing and Grading" or "U.S. Savings Bonds" got the ordinary count and was
deletable from 2034-07-15 while the property was still owned (basis records, Treas. Reg. 1.6001-1(e)). Asserted now:
only a name ending in a cash word (cash, bank, checking, savings, receivable(s), undeposited funds, clearing, petty
cash) is cash, so those three invoices are kept until a CPA releases them, and a debit to a cash or receivable account
still gets the ordinary 7-year count (exact date).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from agentledger.ledger import store as ledger
from agentledger.ledger.store import Line
from test_reaudit_952ee96_premature_retention import GRACE, _event, confirm, deletable_from, due, end_of
from test_reaudit_952ee96_review_retention_later_year_support import put_doc

DAY = timedelta(days=1)


def _invoice_behind(f, code: str, name: str, memo: str, amount: str, credit: str) -> None:
    """A 2026 invoice behind a journal entry debiting `code` (an asset account named `name`), confirmed 2026; acme's
    2026 Form 1120-S and its shareholders' returns on record."""
    ledger.add_account(f.conn, "acme", code, name, "asset")
    put_doc(f.conn, f.vault, "d_purchase", "acme", doc_type="Invoice", received=date(2026, 3, 5))
    ledger.post(f.conn, "acme", date(2026, 3, 2), memo, [Line(code, Decimal(amount)), Line(credit, Decimal("-" + amount))],
                source="invoice", actor="maya", document_id="d_purchase")
    confirm(f.conn, "d_purchase", 2026)
    _event(f.conn, "acme", 2026, "filed", "2027-03-10", "1120-S", note="IRS account transcript: 2026 Form 1120-S received 2027-03-10")
    _event(f.conn, "acme", 2026, "owners_filed", "2027-04-12", "1040", note="shareholders' 2026 Forms 1040 received, transcripts")


ASSETS = [("1510", "Cash Registers and POS Equipment", "12 POS terminals and cash drawers", "24000"),
          ("1520", "Land Clearing and Grading", "site clearing and grading of the new yard, 2 acres", "38000"),
          ("1910", "U.S. Savings Bonds", "Series EE bonds bought for the building reserve", "10000")]


@pytest.mark.parametrize("code, name, memo, amount", ASSETS, ids=[a[1] for a in ASSETS])
def test_a_capitalized_cost_is_a_basis_record_whatever_the_account_is_called(biz, code, name, memo, amount):
    """Paid from 1000 Operating Cash; the property is still owned: kept until a CPA releases it, however late."""
    _invoice_behind(biz, code, name, memo, amount, "1000")
    assert end_of(biz.conn, "d_purchase") == (
        None, f"it supports a capitalized cost ({name}): basis records are kept until a CPA releases them")
    assert "d_purchase" not in due(biz.conn, date(2040, 1, 1))
    assert "d_purchase" not in due(biz.conn, date(2099, 1, 1))


CASH = [("1010", "Petty Cash"), ("1020", "Undeposited Funds"), ("1030", "Payroll Clearing"), ("1040", "Money Market Savings"),
        ("1050", "Lakeshore Bank"), ("1060", "Business Checking"), ("1070", "Notes Receivable")]


@pytest.mark.parametrize("code, name", CASH, ids=[c[1] for c in CASH])
def test_a_debit_to_a_cash_or_receivable_account_is_not_a_basis_record(biz, code, name):
    """A 2026 sale billed on the invoice and deposited to (or owed on) a cash or receivable account: an income tax
    record of 2026, 7 years from the due date plus the margin."""
    _invoice_behind(biz, code, name, "fabrication job 2219", "2400", "4000")
    assert end_of(biz.conn, "d_purchase") == (date(2034, 4, 15) + GRACE, "ok")
    deletable_from(biz.conn, "d_purchase", date(2034, 4, 15) + GRACE + DAY)
