"""Review of the fix for finding 5 (re-audit of 952ee96), employment tax payments. The first adversarial review found
that payroll records were kept only two years past a late employment tax payment (the 6511(a) refund rule), short of
Treas. Reg. 31.6001-1(e)(2), four years after the tax is due or paid, whichever is later, with the shipped policy and at
the accepted 4-year floor. Asserted now: the register is never due while a return the year needs is missing; once the
year is on record a late 941 payment keeps it four years past the payment plus the margin, at both policy values.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from agentledger.evidence import records
from test_reaudit_952ee96_premature_retention import GRACE, _event, confirm, deletable_from, due, end_of

DAY = timedelta(days=1)


def put_doc(conn, vault, doc_id: str, client_id: str, *, doc_type: str, received: date, tax_year: int | None = None,
            fields: dict | None = None) -> None:
    """A filed document received on `received` (what intake stores, without the classifier)."""
    data = f"{doc_id} {doc_type} received {received}".encode()
    loc = vault.put(data)
    sha = hashlib.sha256(data).hexdigest()
    conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, vault_path, "
                 "fields, doc_type, tax_year) VALUES (?, ?, ?, ?, 'text/plain', 'upload', ?, 'filed', ?, ?, ?, ?)",
                 (doc_id, client_id, sha, f"{doc_id}.txt", f"{received.isoformat()}T15:00:00+00:00", loc, json.dumps(fields or {}),
                  doc_type, tax_year))
    records.add_version(conn, doc_id, loc, sha, len(data), "intake-agent")


def file_941s(conn, client_id: str, year: int) -> None:
    """All four Forms 941 of the year filed on time, recorded from the IRS account transcript."""
    for q, on in (("Q1", f"{year}-04-30"), ("Q2", f"{year}-07-31"), ("Q3", f"{year}-10-30"), ("Q4", f"{year + 1}-01-29")):
        _event(conn, client_id, year, "filed", on, "941", period=q, note=f"IRS account transcript: {q} {year} Form 941 received {on}")


def close_the_year(conn, client_id: str = "acme", year: int = 2026) -> None:
    """The rest of what acme's year needs: the 940, the S corporation's 1120-S and its shareholders' returns, on time."""
    _event(conn, client_id, year, "filed", f"{year + 1}-01-29", "940", note=f"IRS account transcript: {year} Form 940 received")
    _event(conn, client_id, year, "filed", f"{year + 1}-03-10", "1120-S", note=f"IRS account transcript: {year} Form 1120-S received")
    _event(conn, client_id, year, "owners_filed", f"{year + 1}-04-12", "1040",
           note=f"shareholders' {year} Forms 1040 received, account transcripts on file")


def test_late_paid_941_tax_keeps_payroll_records_four_years_past_payment(biz):
    """Shipped policy (employment_tax: 7). Q4 2026 payroll register received 2027-01-05; the four 2026 Forms 941 filed
    on time; the Q4 balance paid on 2031-06-01 under an installment agreement. Reg. 31.6001-1(e)(2): kept until at
    least 2035-06-01."""
    put_doc(biz.conn, biz.vault, "doc_payroll_q4", "acme", doc_type="Payroll report", received=date(2027, 1, 5))
    confirm(biz.conn, "doc_payroll_q4", 2026, "employment_tax")
    file_941s(biz.conn, "acme", 2026)
    _event(biz.conn, "acme", 2026, "payment", "2031-06-01", "941", period="Q4",
           note="IRS account transcript: Q4 2026 Form 941 balance paid 2031-06-01 (installment agreement)")
    end, why = end_of(biz.conn, "doc_payroll_q4")
    assert end is None and "no 2026 income tax return of acme" in why and "6501(c)(3)" in why   # the 941s alone: kept
    assert "doc_payroll_q4" not in due(biz.conn, date(2045, 1, 1))
    close_the_year(biz.conn)
    assert end_of(biz.conn, "doc_payroll_q4") == (date(2035, 6, 1) + GRACE, "ok")             # 4 years past the payment
    assert "doc_payroll_q4" not in due(biz.conn, date(2035, 6, 1) - DAY)
    deletable_from(biz.conn, "doc_payroll_q4", date(2035, 6, 1) + GRACE + DAY)


def test_late_paid_941_tax_at_the_policy_floor(biz, home, monkeypatch):
    """Floor policy (employment_tax: 4, accepted). The Q4 2026 941 tax paid a year late, on 2028-03-01: kept until at
    least 2032-03-01 (Reg. 31.6001-1(e)(2)); the income tax return the wages are deducted on keeps the register 7 years
    from its due date (2034-04-15). A later payment (2031-06-01) keeps it 4 years past that payment."""
    cfg = home / "config" / "retention.yaml"
    text = cfg.read_text(encoding="utf-8")
    old = "employment_tax:\n    group: employment\n    years: 7"
    assert old in text
    cfg.write_text(text.replace(old, "employment_tax:\n    group: employment\n    years: 4"), encoding="utf-8")
    records.load_policy(home / "config")                          # accepted: 4 is the floor for employment_tax
    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))
    assert records.policy()["classes"]["employment_tax"]["years"] == 4
    put_doc(biz.conn, biz.vault, "doc_payroll_q4", "acme", doc_type="Payroll report", received=date(2027, 1, 5))
    confirm(biz.conn, "doc_payroll_q4", 2026, "employment_tax")
    file_941s(biz.conn, "acme", 2026)
    _event(biz.conn, "acme", 2026, "payment", "2028-03-01", "941", period="Q4",
           note="IRS account transcript: Q4 2026 Form 941 balance paid 2028-03-01")
    assert end_of(biz.conn, "doc_payroll_q4")[0] is None                                      # the year is not closed
    close_the_year(biz.conn)
    assert "doc_payroll_q4" not in due(biz.conn, date(2032, 3, 1) - DAY)
    assert end_of(biz.conn, "doc_payroll_q4") == (date(2034, 4, 15) + GRACE, "ok")
    deletable_from(biz.conn, "doc_payroll_q4", date(2034, 4, 15) + GRACE + DAY)
    _event(biz.conn, "acme", 2026, "payment", "2031-06-01", "941", period="Q4",
           note="IRS account transcript: Q4 2026 Form 941 penalty and interest paid 2031-06-01")
    assert end_of(biz.conn, "doc_payroll_q4") == (date(2035, 6, 1) + GRACE, "ok")
    deletable_from(biz.conn, "doc_payroll_q4", date(2035, 6, 1) + GRACE + DAY)
