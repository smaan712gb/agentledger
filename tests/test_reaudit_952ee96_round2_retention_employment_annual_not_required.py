"""Second adversarial review of the retention fix (re-audit of 952ee96), employment tax returns: a "not required" record
for an annual employment return was taken for an annual return on record (and Schedule H or CT-1 for the 940), so an
accurate statement such as "acme is not a railroad employer: no Form CT-1" made acme's 2026 payroll register deletable
from 2034-07-15 although some quarters' Form 941 were never filed (IRC 6501(c)(3); Treas. Reg. 31.6001-1(e)(2)).
Asserted now: Schedule H is refused for a business; an annual form counts only when filed; "not required" counts for a
941 quarter and for the 940; the register is kept until every quarter and the 940 are on record, then counted from the
latest filing.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agentledger import db
from agentledger.evidence import records
from test_reaudit_952ee96_premature_retention import GRACE, _event, confirm, deletable_from, due, end_of
from test_reaudit_952ee96_review_retention_later_year_support import put_doc
from test_return_workflow import fam  # noqa: F401  (fixture)

DAY = timedelta(days=1)
ON_TIME = {"Q1": "2026-04-30", "Q2": "2026-07-31", "Q3": "2026-10-30", "Q4": "2027-01-29"}
NO_940 = ("no 2026 Form 940 (federal unemployment tax) is on record as filed or not required: no limitation period runs "
          "(its confirmed tax year)")


def _payroll_year(f, quarters: tuple[str, ...], with_940: bool) -> None:
    """acme (S corporation): the 2026 payroll register confirmed, its 2026 Form 1120-S and the shareholders' returns on
    record, the 941 of each of `quarters` filed on time, and the 940 when `with_940`."""
    put_doc(f.conn, f.vault, "d_payroll", "acme", doc_type="Payroll report", tax_year=2026, received=date(2027, 1, 5))
    confirm(f.conn, "d_payroll", 2026, "employment_tax")
    _event(f.conn, "acme", 2026, "filed", "2027-03-10", "1120-S", note="IRS account transcript: 2026 Form 1120-S received")
    _event(f.conn, "acme", 2026, "owners_filed", "2027-04-12", "1040", note="shareholders' 2026 Forms 1040 received, transcripts")
    for q in quarters:
        _event(f.conn, "acme", 2026, "filed", ON_TIME[q], "941", period=q,
               note=f"IRS account transcript: {q} 2026 Form 941 received {ON_TIME[q]}")
    if with_940:
        _event(f.conn, "acme", 2026, "filed", "2027-01-29", "940", note="IRS account transcript: 2026 Form 940 received")


def _kept_without(conn, missing: list[str]) -> None:
    assert end_of(conn, "d_payroll") == (None, "the 2026 employment tax returns are not all on record (no annual return "
                                               f"filed; no 941 for {', '.join(missing)}): no limitation period runs "
                                               "(its confirmed tax year)")
    assert "d_payroll" not in due(conn, date(2099, 1, 1))


def test_schedule_h_is_refused_for_a_business(biz):
    """The review's Schedule H case: recording it for acme, as not required or as filed, is refused with the reason and
    leaves nothing on record; the register stays kept for the 941s of Q3 and Q4."""
    _payroll_year(biz, ("Q1", "Q2"), with_940=False)
    for kind in ("not_required", "filed"):
        with pytest.raises(ValueError, match=r"^Schedule H is a household employer's return \(with Form 1040\): a business "
                                             r"files 941 and 940$"):
            _event(biz.conn, "acme", 2026, kind, "2027-01-15", "SCHEDULE H",
                   note="acme is a business, not a household employer: no Schedule H")
    assert not db.one(biz.conn, "SELECT 1 AS x FROM tax_year_events WHERE form = 'SCHEDULE H'")
    _kept_without(biz.conn, ["Q3", "Q4"])


def test_a_household_employer_files_schedule_h(fam):  # noqa: F811
    """The Riveras' 2026 nanny payroll register: Schedule H filed with their 2026 Form 1040 on 2027-04-10 is the annual
    employment return and the FUTA return; 7 years from the due date plus the margin."""
    put_doc(fam.conn, fam.vault, "d_nanny_payroll", "rivera", doc_type="Payroll report", tax_year=2026, received=date(2027, 1, 20))
    confirm(fam.conn, "d_nanny_payroll", 2026, "employment_tax")
    _event(fam.conn, "rivera", 2026, "filed", "2027-04-10", "1040", note="IRS account transcript: 2026 Form 1040 received 2027-04-10")
    _event(fam.conn, "rivera", 2026, "filed", "2027-04-10", "Schedule H", note="2026 Schedule H (household employee) filed with the 1040")
    assert end_of(fam.conn, "d_nanny_payroll") == (date(2034, 4, 15) + GRACE, "ok")
    deletable_from(fam.conn, "d_nanny_payroll", date(2034, 4, 15) + GRACE + DAY)


CASES = [
    # (annual form recorded as not required, its note, quarters whose 941 was filed on time, 940 filed?)
    ("944", "acme was never notified to file Form 944; it files Form 941", ("Q1",), True),
    ("CT-1", "acme is not a railroad employer: no Form CT-1", ("Q1", "Q2", "Q3"), False),
    ("943", "acme has no farmworkers: no Form 943", ("Q1",), True),
]


@pytest.mark.parametrize("form, note, quarters, with_940", CASES, ids=[c[0] for c in CASES])
def test_an_annual_form_not_required_does_not_cover_unfiled_941_quarters(biz, form, note, quarters, with_940):
    """The statement changes nothing: the register is kept for the quarters never filed (and, with CT-1, for the 940);
    the delinquent 941s filed on 2033-02-01 after an IRS notice (and the 940) start the count: 7 years plus the margin."""
    _payroll_year(biz, quarters, with_940)
    missing = [q for q in records.QUARTERS if q not in quarters]
    _kept_without(biz.conn, missing)
    _event(biz.conn, "acme", 2026, "not_required", "2027-01-15", form, note=note)
    _kept_without(biz.conn, missing)
    for q in missing:
        _event(biz.conn, "acme", 2026, "filed", "2033-02-01", "941", period=q,
               note=f"delinquent {q} 2026 Form 941 filed after an IRS notice, transcript")
    if not with_940:
        assert end_of(biz.conn, "d_payroll") == (None, NO_940)              # a CT-1 not required is no 940 either
        _event(biz.conn, "acme", 2026, "filed", "2033-02-01", "940", note="delinquent 2026 Form 940 filed after an IRS notice")
    assert end_of(biz.conn, "d_payroll") == (date(2040, 2, 1) + GRACE, "ok")
    deletable_from(biz.conn, "d_payroll", date(2040, 2, 1) + GRACE + DAY)


def test_not_required_counts_for_a_941_quarter_and_for_the_940(biz):
    """acme's first 2026 payroll was a part-time employee from 2026-10-05 to 2026-12-18: no Form 941 was required for
    Q1 to Q3 and no Form 940 (1,200.00 of wages, under 1,500 in every quarter, fewer than 20 weeks), each recorded as
    not required; the Q4 941 was filed late on 2028-03-01. Kept until all of them are on record, then 7 years from the
    late Q4 filing plus the margin."""
    _payroll_year(biz, (), with_940=False)
    _event(biz.conn, "acme", 2026, "filed", "2028-03-01", "941", period="Q4", note="Q4 2026 Form 941 received 2028-03-01 (late), transcript")
    _kept_without(biz.conn, ["Q1", "Q2", "Q3"])
    for q in ("Q1", "Q2", "Q3"):
        _event(biz.conn, "acme", 2026, "not_required", "2027-02-15", "941", period=q,
               note=f"no wages paid in {q} 2026: the first payroll was 2026-10-09")
    assert end_of(biz.conn, "d_payroll") == (None, NO_940)
    _event(biz.conn, "acme", 2026, "not_required", "2027-02-15", "940",
           note="2026 wages 1,200.00, under 1,500 in every quarter of 2025 and 2026, fewer than 20 weeks: no Form 940")
    assert end_of(biz.conn, "d_payroll") == (date(2035, 3, 1) + GRACE, "ok")
    deletable_from(biz.conn, "d_payroll", date(2035, 3, 1) + GRACE + DAY)
