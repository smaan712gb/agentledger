"""Review of the fix for finding 5 (re-audit of 952ee96), what load_policy lets a firm configure. The first adversarial
review edited the shipped policy to values load_policy accepted and showed records deletable inside a required period:
preparer_copy 3 (short of 6107(b)'s July-June return period), engagement_and_correspondence 3 applied to a contract
behind a 2026 revenue entry, a finite property_basis class for the closing disclosure of a home still owned, and a
renamed employment class counted from the income tax return without any Form 941. Asserted now, with the edits adapted
to the new retention.yaml: preparer_copy 3 and a finite basis class are refused; a 3-year correspondence class still
keeps a record behind a tax year 7 years from that year's due date; a renamed class keeps its employment group.
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
from test_return_workflow import fam  # noqa: F401  (fixture)

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


@pytest.fixture
def edit_policy(home, monkeypatch):
    """Apply text replacements to the firm's copy of retention.yaml; returns what load_policy makes of it (the
    ValueError when it refuses). records.policy() reads the firm's copy."""

    def apply(*pairs: tuple[str, str]) -> dict | ValueError:
        cfg = home / "config" / "retention.yaml"
        text = cfg.read_text(encoding="utf-8")
        for old, new in pairs:
            assert old in text, old
            text = text.replace(old, new)
        cfg.write_text(text, encoding="utf-8")
        monkeypatch.setenv("AGENTLEDGER_HOME", str(home))
        try:
            return records.load_policy(home / "config")
        except ValueError as e:
            return e

    return apply


def test_preparer_copy_floor_covers_the_6107_return_period(fam, edit_policy):  # noqa: F811
    """preparer_copy 3 is refused (floor 4: 3 years after the close of a July-June return period is at most 4 years
    after the return was presented). At 4, the firm's copy of the 2026 Form 1040, presented for signature on
    2027-09-10 and filed on extension on 2027-09-15, is kept 7 years from the filing (an income tax record), well past
    2031-06-30."""
    refused = edit_policy(("preparer_copy:\n    group: income\n    years: 7", "preparer_copy:\n    group: income\n    years: 3"))
    assert isinstance(refused, ValueError) and "statutory minimum is 4" in str(refused)
    pol = edit_policy(("preparer_copy:\n    group: income\n    years: 3", "preparer_copy:\n    group: income\n    years: 4"))
    assert pol["classes"]["preparer_copy"]["years"] == 4
    put_doc(fam.conn, fam.vault, "d_prep_copy", "rivera", doc_type="Other", tax_year=2026, received=date(2027, 9, 15))
    confirm(fam.conn, "d_prep_copy", 2026, "preparer_copy")
    _event(fam.conn, "rivera", 2026, "filed", "2027-09-15", "1040",
           note="IRS account transcript: 2026 Form 1040 received 2027-09-15 (extension on file)")
    assert "d_prep_copy" not in due(fam.conn, date(2031, 6, 30))
    assert end_of(fam.conn, "d_prep_copy") == (date(2034, 9, 15) + GRACE, "ok")
    deletable_from(fam.conn, "d_prep_copy", date(2034, 9, 15) + GRACE + DAY)


def test_contract_class_floor_applies_to_a_record_that_supports_a_tax_year(fam, edit_policy):  # noqa: F811
    """engagement_and_correspondence 3 (accepted). A C corporation's 2026 sales contract, intake class
    engagement_and_correspondence, supports a 2026 revenue entry; the 2026 Form 1120 is filed 2027-04-10. Kept as an
    income tax record of 2026: 7 years from the due date (2034-04-15) plus the margin, past 6501(e) (2033-04-15)."""
    pol = edit_policy(("engagement_and_correspondence:\n    group: firm\n    years: 7",
                       "engagement_and_correspondence:\n    group: firm\n    years: 3"))
    assert pol["classes"]["engagement_and_correspondence"]["years"] == 3
    store.add_client(fam.conn, id="harbor-co", name="Harbor Freight Lines Inc", kind="business", entity_type="c_corp",
                     tax_id_last4="7788", domain="general")
    onboard(fam.conn, Packs(fam.paths.domains), "harbor-co", "general")
    put_doc(fam.conn, fam.vault, "d_contract", "harbor-co", doc_type="Contract", received=date(2026, 5, 1))
    assert records.retention_for(records.policy(), "Contract", None, "2026-05-01")[0] == "engagement_and_correspondence"
    store.post(fam.conn, "harbor-co", date(2026, 5, 1), "freight contract, first billing",
               [Line("1100", Decimal("250000")), Line("4000", Decimal("-250000"))], source="invoice", actor="maya",
               document_id="d_contract")
    confirm(fam.conn, "d_contract", None, "engagement_and_correspondence")
    assert end_of(fam.conn, "d_contract")[0] is None                         # the entry's year has no filing yet
    _event(fam.conn, "harbor-co", 2026, "filed", "2027-04-10", "1120", note="IRS account transcript: 2026 Form 1120 received 2027-04-10")
    assert "d_contract" not in due(fam.conn, date(2033, 4, 14))
    assert end_of(fam.conn, "d_contract") == (date(2034, 4, 15) + GRACE, "ok")
    deletable_from(fam.conn, "d_contract", date(2034, 4, 15) + GRACE + DAY)


def test_finite_basis_class_is_refused(fam, edit_policy):  # noqa: F811
    """property_basis 7 is refused: basis records are kept until a CPA releases them. Under the shipped policy the 2015
    closing disclosure of the home the clients still own stays, however old."""
    refused = edit_policy(("property_basis:\n    group: basis\n    years: null", "property_basis:\n    group: basis\n    years: 7"))
    assert isinstance(refused, ValueError) and "kept until a CPA releases them (years: null)" in str(refused)
    edit_policy(("property_basis:\n    group: basis\n    years: 7", "property_basis:\n    group: basis\n    years: null"))
    put_doc(fam.conn, fam.vault, "d_closing", "rivera", doc_type="Closing disclosure", tax_year=2015, received=date(2016, 1, 15))
    assert records.retention_for(records.policy(), "Closing disclosure", 2015, "2016-01-15") == ("property_basis", None)
    confirm(fam.conn, "d_closing", 2015, "property_basis")
    _event(fam.conn, "rivera", 2015, "filed", "2016-04-01", "1040", note="IRS account transcript: 2015 Form 1040 received 2016-04-01")
    assert end_of(fam.conn, "d_closing") == (None, "class property_basis is kept until a CPA releases it")
    assert "d_closing" not in due(fam.conn, date(2026, 10, 8))
    assert "d_closing" not in due(fam.conn, date(2099, 1, 1))


def test_renamed_employment_class_keeps_its_group(biz, edit_policy):
    """employment_tax renamed payroll_tax (its group: employment kept, 7 years accepted). The 2026 payroll register;
    the 2026 Form 1120-S filed 2027-03-10 and the shareholders' returns; no 2026 Form 941 ever filed: kept."""
    pol = edit_policy(("  employment_tax:\n    group: employment\n    years: 7", "  payroll_tax:\n    group: employment\n    years: 7"),
                      ("Payroll report: employment_tax", "Payroll report: payroll_tax"))
    assert pol["classes"]["payroll_tax"]["group"] == "employment"
    put_doc(biz.conn, biz.vault, "d_payroll", "acme", doc_type="Payroll report", tax_year=2026, received=date(2027, 1, 5))
    confirm(biz.conn, "d_payroll", 2026, "payroll_tax")
    _event(biz.conn, "acme", 2026, "filed", "2027-03-10", "1120-S", note="IRS account transcript: 2026 Form 1120-S received 2027-03-10")
    _event(biz.conn, "acme", 2026, "owners_filed", "2027-04-12", "1040", note="shareholders' 2026 Forms 1040 received, transcripts")
    end, why = end_of(biz.conn, "d_payroll")
    assert end is None and "no 941 for Q1, Q2, Q3, Q4" in why
    assert "d_payroll" not in due(biz.conn, date(2045, 1, 1))
