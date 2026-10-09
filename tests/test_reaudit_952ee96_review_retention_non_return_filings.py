"""Review of the fix for finding 5 (re-audit of 952ee96), what counts as an income tax return filed. The first
adversarial review found that recording an extension (4868), a state return, a gift tax return, an information return
transmittal, an FBAR or a 941-SS as "filed" started the income tax count for the 2026 W-2 of a client whose Form 1040
was never filed (6501(c)(3)), making it deletable from 2034-07-15. Asserted now: each such recording is refused, a
941-SS needs its quarter and is an employment return, and none of them makes the W-2 deletable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest

from agentledger import db
from agentledger.evidence import records
from test_reaudit_952ee96_premature_retention import confirm, due, end_of
from test_return_workflow import fam  # noqa: F401  (fixture)


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


CASES = [
    ("4868", "2027-10-15", "Form 4868 extension accepted 2027-04-10; extended due date 2027-10-15"),
    ("CA 540", None, "FTB confirmation: 2026 California Form 540 received 2027-04-10"),
    ("709", None, "IRS transcript: 2026 Form 709 gift tax return received 2027-04-10"),
    ("1096", None, "Form 1096 transmittal of the household employer's 1099-NEC, received 2027-04-10"),
    ("FinCEN 114", None, "BSA e-filing acknowledgement: 2026 FBAR received 2027-04-10"),
]


def _unfiled_w2(f) -> None:
    put_doc(f.conn, f.vault, "d_w2_unfiled", "rivera", doc_type="W-2", tax_year=2026, received=date(2027, 2, 1))
    confirm(f.conn, "d_w2_unfiled", 2026)
    end, why = end_of(f.conn, "d_w2_unfiled")
    assert end is None and "6501(c)(3)" in why                    # no 2026 return on record: kept


def _still_kept(f) -> None:
    end, why = end_of(f.conn, "d_w2_unfiled")
    assert end is None and "no 2026 income tax return of rivera" in why and "6501(c)(3)" in why
    assert "d_w2_unfiled" not in due(f.conn, date(2045, 1, 1))


@pytest.mark.parametrize("form, due_on, note", CASES, ids=[c[0] for c in CASES])
def test_only_an_income_tax_return_starts_the_income_tax_count(fam, form, due_on, note):  # noqa: F811
    _unfiled_w2(fam)
    with pytest.raises(ValueError, match="is not a federal income or employment tax return"):
        records.record_tax_event(fam.conn, "rivera", 2026, "filed", "2027-04-10", form=form, due_on=due_on, actor="lee",
                                 role="cpa", note=note)
    assert not db.one(fam.conn, "SELECT 1 AS x FROM tax_year_events WHERE client_id = 'rivera'")
    _still_kept(fam)


def test_a_941_ss_is_a_quarterly_employment_return(fam):  # noqa: F811
    """Without its quarter the 941-SS is refused; with it, it is recorded as an employment tax return, which never
    starts the count of an income tax record."""
    _unfiled_w2(fam)
    note = "IRS transcript: Form 941-SS received 2027-04-10"
    with pytest.raises(ValueError, match="Form 941-SS is quarterly: give the period"):
        records.record_tax_event(fam.conn, "rivera", 2026, "filed", "2027-04-10", form="941-SS", actor="lee", role="cpa", note=note)
    _still_kept(fam)
    records.record_tax_event(fam.conn, "rivera", 2026, "filed", "2027-04-10", form="941-SS", period="Q4", actor="lee",
                             role="cpa", note=note)
    assert [e["group"] for e in records.tax_year_events(fam.conn, "rivera", 2026)] == ["employment"]
    _still_kept(fam)
