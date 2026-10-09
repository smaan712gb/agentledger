"""Review of the fix for finding 5 (re-audit of 952ee96), employment tax returns. The first adversarial review found
that one quarter's Form 941 on record made every payroll record of the year deletable, including those of a quarter
whose 941 was never filed (6501(c)(3)), and that payroll records waited only for the 941s, never for the income tax
return their wage deduction supports (filed late, or never). Asserted now: payroll records wait for the 941 of every
quarter (or an annual return) and the 940 as well as the income tax return; a missing return keeps them, and once all
are on record the latest governs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

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


def test_one_quarters_941_does_not_cover_an_unfiled_quarter(biz):
    """The Q1 2026 Form 941 was filed (2026-04-30); the business stopped filing after that. The Q4 2026 payroll
    register is kept however late, also once the income tax side of the year is on record; when the delinquent 941s
    are filed (Q4 on 2033-02-01, after a notice) and the 940, the count runs from the latest of them."""
    put_doc(biz.conn, biz.vault, "d_payroll_q4", "acme", doc_type="Payroll report", tax_year=2026, received=date(2027, 1, 5))
    confirm(biz.conn, "d_payroll_q4", 2026, "employment_tax")
    _event(biz.conn, "acme", 2026, "filed", "2026-04-30", "941", period="Q1",
           note="IRS account transcript: Q1 2026 Form 941 received 2026-04-30; Q2-Q4 not filed")
    assert end_of(biz.conn, "d_payroll_q4")[0] is None and "d_payroll_q4" not in due(biz.conn, date(2045, 1, 1))
    _event(biz.conn, "acme", 2026, "filed", "2027-03-10", "1120-S", note="IRS account transcript: 2026 Form 1120-S received")
    _event(biz.conn, "acme", 2026, "owners_filed", "2027-04-12", "1040", note="shareholders' 2026 Forms 1040 received, transcripts")
    end, why = end_of(biz.conn, "d_payroll_q4")
    assert end is None and "no 941 for Q2, Q3, Q4" in why and "no limitation period runs" in why
    assert "d_payroll_q4" not in due(biz.conn, date(2045, 1, 1))
    _event(biz.conn, "acme", 2026, "filed", "2033-01-20", "941", period="Q2", note="delinquent Q2 2026 Form 941, transcript")
    _event(biz.conn, "acme", 2026, "filed", "2033-01-20", "941", period="Q3", note="delinquent Q3 2026 Form 941, transcript")
    assert "no 941 for Q4" in end_of(biz.conn, "d_payroll_q4")[1]
    _event(biz.conn, "acme", 2026, "filed", "2033-02-01", "941", period="Q4", note="delinquent Q4 2026 Form 941, transcript")
    assert "Form 940" in end_of(biz.conn, "d_payroll_q4")[1]
    _event(biz.conn, "acme", 2026, "filed", "2033-02-01", "940", note="delinquent 2026 Form 940, transcript")
    assert end_of(biz.conn, "d_payroll_q4") == (date(2040, 2, 1) + GRACE, "ok")             # 7 years from the last 941
    deletable_from(biz.conn, "d_payroll_q4", date(2040, 2, 1) + GRACE + DAY)


@pytest.mark.parametrize("filed_1120, day", [("2032-06-01", date(2035, 5, 31)), (None, date(2045, 1, 1))],
                         ids=["1120-filed-late", "1120-never-filed"])
def test_payroll_records_wait_for_the_income_tax_return_they_support(biz, filed_1120, day):
    """A C corporation's 2026 payroll register supports the wage deduction (journal entry) on its 2026 Form 1120; the
    four 941s are on time. Filed late on 2032-06-01: kept until 7 years after that filing (6501(a) alone runs to
    2035-06-01). Never filed: kept (6501(c)(3)). The 940 is needed either way."""
    store.add_client(biz.conn, id="harbor-co", name="Harbor Freight Lines Inc", kind="business", entity_type="c_corp",
                     tax_id_last4="7788", domain="general")
    onboard(biz.conn, Packs(biz.paths.domains), "harbor-co", "general")
    put_doc(biz.conn, biz.vault, "d_payroll_2026", "harbor-co", doc_type="Payroll report", tax_year=2026, received=date(2027, 1, 5))
    store.post(biz.conn, "harbor-co", date(2026, 12, 31), "2026 wages per payroll register",
               [Line("6000", Decimal("250000")), Line("1000", Decimal("-250000"))], source="payroll", actor="maya",
               document_id="d_payroll_2026")
    confirm(biz.conn, "d_payroll_2026", 2026, "employment_tax")
    for q, on in (("Q1", "2026-04-30"), ("Q2", "2026-07-31"), ("Q3", "2026-10-30"), ("Q4", "2027-01-29")):
        _event(biz.conn, "harbor-co", 2026, "filed", on, "941", period=q, note=f"IRS account transcript: {q} 2026 Form 941 received {on}")
    if filed_1120:
        _event(biz.conn, "harbor-co", 2026, "filed", filed_1120, "1120",
               note=f"IRS account transcript: 2026 Form 1120 received {filed_1120} (late)")
    end, why = end_of(biz.conn, "d_payroll_2026")
    assert end is None and ("Form 940" in why if filed_1120 else "no 2026 income tax return of harbor-co" in why)
    assert "d_payroll_2026" not in due(biz.conn, day)
    _event(biz.conn, "harbor-co", 2026, "filed", "2027-01-29", "940", note="IRS account transcript: 2026 Form 940 received")
    assert "d_payroll_2026" not in due(biz.conn, day)
    if filed_1120:
        assert end_of(biz.conn, "d_payroll_2026") == (date(2039, 6, 1) + GRACE, "ok")     # the late 1120 governs
        deletable_from(biz.conn, "d_payroll_2026", date(2039, 6, 1) + GRACE + DAY)
    else:
        end, why = end_of(biz.conn, "d_payroll_2026")
        assert end is None and "no 2026 income tax return of harbor-co" in why and "6501(c)(3)" in why
        assert "d_payroll_2026" not in due(biz.conn, date(2099, 1, 1))
