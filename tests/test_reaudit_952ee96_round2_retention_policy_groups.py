"""Second adversarial review of the retention fix (re-audit of 952ee96), the policy file: a class's group was whatever the
firm's policy said, so one edited line (property_basis moved to group income with 7 years, employment_tax moved to group
income, or employment_tax renamed in a policy without group keys, which then defaulted to income) made the closing
disclosure of a home still owned deletable from 2023-07-15 and a payroll register with no 2026 Form 941 or 940
deletable from 2034-07-15. Asserted now: load_policy refuses each edit with its reason (the shipped classes' groups are
pinned, any other class declares its group, a document type's class belongs to its group), a refused policy stops every
retention run, and under the shipped policy both records are kept.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from agentledger.evidence import records
from test_reaudit_952ee96_premature_retention import _event, confirm, due, end_of
from test_reaudit_952ee96_review_retention_later_year_support import put_doc
from test_return_workflow import fam  # noqa: F401  (fixture)


@pytest.fixture
def firm_policy(home, monkeypatch):
    """(shipped text, write): write(text) replaces the firm's copy of retention.yaml, which records.policy() reads, and
    returns what load_policy makes of it (the ValueError when it refuses it)."""
    cfg = home / "config" / "retention.yaml"
    shipped = cfg.read_text(encoding="utf-8")
    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))

    def write(text: str):
        cfg.write_text(text, encoding="utf-8")
        try:
            return records.load_policy(home / "config")
        except ValueError as e:
            return e

    return shipped, write


def _edit(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new)


def _refused(result, reason: str) -> None:
    assert isinstance(result, ValueError) and str(result) == reason, result


def test_property_basis_cannot_leave_the_basis_group(fam, firm_policy):  # noqa: F811
    shipped, write = firm_policy
    put_doc(fam.conn, fam.vault, "d_closing", "rivera", doc_type="Closing disclosure", tax_year=2015, received=date(2016, 1, 15))
    confirm(fam.conn, "d_closing", 2015, "property_basis")
    _event(fam.conn, "rivera", 2015, "filed", "2016-04-01", "1040", note="IRS account transcript: 2015 Form 1040 received 2016-04-01")
    reason = "retention class property_basis belongs to group basis; a policy cannot move it"
    _refused(write(_edit(shipped, "property_basis:\n    group: basis\n    years: null",
                         "property_basis:\n    group: income\n    years: 7")), reason)
    with pytest.raises(ValueError, match=re.escape(reason)):
        records.due_for_deletion(fam.conn, date(2026, 10, 8))           # no retention run under a refused policy
    write(shipped)
    assert end_of(fam.conn, "d_closing") == (None, "class property_basis is kept until a CPA releases it")
    assert "d_closing" not in due(fam.conn, date(2099, 1, 1))


def test_employment_tax_cannot_leave_the_employment_group(biz, firm_policy):
    shipped, write = firm_policy
    put_doc(biz.conn, biz.vault, "d_payroll", "acme", doc_type="Payroll report", tax_year=2026, received=date(2027, 1, 5))
    confirm(biz.conn, "d_payroll", 2026, "employment_tax")
    _event(biz.conn, "acme", 2026, "filed", "2027-03-10", "1120-S", note="IRS account transcript: 2026 Form 1120-S received 2027-03-10")
    _event(biz.conn, "acme", 2026, "owners_filed", "2027-04-12", "1040", note="shareholders' 2026 Forms 1040 received, transcripts")
    reason = "retention class employment_tax belongs to group employment; a policy cannot move it"
    _refused(write(_edit(shipped, "employment_tax:\n    group: employment\n    years: 7",
                         "employment_tax:\n    group: income\n    years: 7")), reason)
    with pytest.raises(ValueError, match=re.escape(reason)):
        records.due_for_deletion(biz.conn, date(2045, 1, 1))
    write(shipped)
    assert end_of(biz.conn, "d_payroll") == (None, "the 2026 employment tax returns are not all on record (no annual return "
                                                   "filed; no 941 for Q1, Q2, Q3, Q4): no limitation period runs (its "
                                                   "confirmed tax year)")
    assert "d_payroll" not in due(biz.conn, date(2045, 1, 1))


def test_an_old_format_policy_must_declare_the_group_of_a_renamed_class(biz, firm_policy):
    """A policy without group keys (the 952ee96 format, kept across the upgrade) loads with the shipped classes' groups;
    with employment_tax renamed payroll_tax it is refused, and no retention run starts."""
    shipped, write = firm_policy
    old = "".join(line for line in shipped.splitlines(keepends=True) if not line.startswith("    group: "))
    loaded = write(old)
    assert {name: c["group"] for name, c in loaded["classes"].items()} == records.SHIPPED_GROUPS
    renamed = _edit(_edit(old, "  employment_tax:\n", "  payroll_tax:\n"), "Payroll report: employment_tax", "Payroll report: payroll_tax")
    reason = "retention class payroll_tax: declare its group (income, employment, basis, firm)"
    _refused(write(renamed), reason)
    with pytest.raises(ValueError, match=re.escape(reason)):
        records.due_for_deletion(biz.conn, date(2045, 1, 1))


DOC_TYPES = [("Payroll report", "employment_tax", "employment"), ("Closing disclosure", "property_basis", "basis"),
             ("K-1", "property_basis", "basis"), ("1099-B", "property_basis", "basis")]


@pytest.mark.parametrize("doc_type, shipped_class, group", DOC_TYPES, ids=[d[0] for d in DOC_TYPES])
def test_a_document_types_class_belongs_to_its_group(firm_policy, doc_type, shipped_class, group):
    """Mapping the document type to an income class is refused; a firm's own class of the right group is accepted."""
    shipped, write = firm_policy
    line = f"  {doc_type}: {shipped_class}\n"
    _refused(write(_edit(shipped, line, f"  {doc_type}: tax_return_support\n")),
             f"document type {doc_type} needs a class of group {group}, not tax_return_support (income)")
    own = _edit(shipped, "default: tax_return_support\n",
                f"  firm_{group}:\n    group: {group}\n    years: {'null' if group == 'basis' else 10}\ndefault: tax_return_support\n")
    loaded = write(_edit(own, line, f"  {doc_type}: firm_{group}\n"))
    assert records.retention_for(loaded, doc_type, 2026, "2027-01-05")[0] == f"firm_{group}"
