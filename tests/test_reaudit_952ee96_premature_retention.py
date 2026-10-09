"""Re-audit of 952ee96, finding 5: retention rules can allow premature deletion of older tax records. Regression
tests: on 952ee96 each path (a) to (i) made a record deletable inside its limitation period.

On 952ee96 retention was decided once, at intake (intake/pipeline.py:93-94), by evidence/records.py:retention_for, which counts a
fixed number of years from 31 December of the document's tax year, or from the received date when no tax year was
detected (records.py:54). Nothing recomputes it afterwards: filing, late filing, amending and assigning a return or a
document never touch documents.retain_until, and due_for_deletion (records.py:110-117) compares only that date (and
legal holds) with today. purge_expired (records.py:120-147) deletes whatever due_for_deletion returns.

The statutory periods are counted from other events: IRC 6501(a) three years after the return was FILED (an early
return is treated as filed on the due date, 6501(b)(1)); 6501(e) six years after filing for a >25% omission; 6511(d)(1)
seven years from the DUE DATE for worthless securities and bad debts; 6511(a) two years from the date tax was PAID;
6501(c)(3) no limit when no return was filed; basis records until the disposition year's period ends.

Retention is now computed when deletion is considered (evidence/records.py: retention_end): a person confirms each
document's tax year and class; every year the document supports (its year, the returns and journal entries that
relied on it) needs a matching return on record as filed or not required; the period runs from the later of the
latest filing and the due date, two years past any payment, plus a margin. Paths (a) to (i) assert the safe outcome
as written for 952ee96; the tests after them confirm the documents and check the exact dates, so the protection is
the statutory arithmetic and not only the missing confirmation.

Dates are simulated (audit.clock for intake and audit records, a frozen clock for the dates the workflow stamps on
return events), so nothing depends on today's date.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from conftest import FakeRouter
from test_return_workflow import fam, household  # noqa: F401  (fixture)

from agentledger import audit, coverage, db
from agentledger.evidence import records
from agentledger.intake import pipeline
from agentledger.intake.classify import KV, Classification
from agentledger.ledger import store as ledger
from agentledger.ledger.store import Line
from agentledger.returns import store as returns_store
from agentledger.returns.store import Returns
from agentledger.workflow import engine as wf_engine


# --------------------------------------------------------------------------------------------------- helpers
@contextmanager
def on(day: date):
    """Act as if it were `day`: intake receipt dates and audit records read audit.clock; the workflow and the return
    store stamp events (such as mark_paper_filed, i.e. the filing date) with datetime.now, frozen here."""
    at = datetime(day.year, day.month, day.day, 15, 0, tzinfo=timezone.utc)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return at if tz is not None else at.replace(tzinfo=None)

    with audit.clock(at), pytest.MonkeyPatch.context() as mp:
        mp.setattr(wf_engine, "datetime", Frozen)
        mp.setattr(returns_store, "datetime", Frozen)
        yield


def receive(f, name: str, data: bytes, *, received: date, router=None, client: str = "rivera") -> dict:
    """The real intake path (pipeline.ingest), on a simulated receipt date. Returns the stored documents row."""
    with on(received):
        out = pipeline.ingest(f.conn, router, f.vault, name, data, channel="upload", client_hint=client)
    assert len(out) == 1 and not out[0].get("duplicate")
    return dict(f.conn.execute("SELECT * FROM documents WHERE id = ?", (out[0]["id"],)).fetchone())


def due(conn, day: date) -> set[str]:
    return {d["id"] for d in records.due_for_deletion(conn, day)}


def paper_file(R: Returns, rid: str, actor: str = "maya"):
    """Review, sign and paper-file a return through the real workflow (as tests/test_audit_findings.py does). Filed
    documents the return does not use (the fixture's 1099-NEC) are accounted for first, as review requires."""
    for doc in R.unaccounted_documents(rid):
        R.account_for_document(rid, doc, "not_applicable", "not part of this return (test household)", actor)
    R.confirm(rid, None, actor)
    R.submit_for_review(rid, actor)
    st = R.approve(rid, actor, "cpa")
    R.request_signature(rid, actor, "cpa")
    R.record_signature(rid, "taxpayer", method="wet_signature", return_hash=st.facts["approved_hash"])
    return R.mark_paper_filed(rid, actor, "cpa", "filed on paper, mailed by certified mail")


def why(doc: dict) -> str:
    return f"{doc['doc_type']} {doc['id']} (tax year {doc['tax_year']}, class {doc['retention_class']}, retain_until {doc['retain_until']})"


W2_2018 = (b"Form W-2 Wage and Tax Statement 2018\nEmployer: Pine Harbor Logistics\nEmployee: Alex Rivera  SSN XXX-XX-0001\n"
           b"1 Wages, tips, other compensation 48,000.00\n2 Federal income tax withheld 3,900.00\n")
W2_2026 = (b"Form W-2 Wage and Tax Statement 2026\nEmployer: Harbor Freight Lines\nEmployee: Alex Rivera  SSN XXX-XX-0001\n"
           b"1 Wages, tips, other compensation 61200.00\n2 Federal income tax withheld 5100.00\n")


def w2_2026_router() -> FakeRouter:
    """A model reading of W2_2026 with its boxes, so the return can be populated from it."""
    return FakeRouter({"classify": lambda user: Classification(
        doc_type="W-2", tax_year=2026, party_names=["Alex Rivera"], tin_last4=["0001"],
        fields=[KV(name="employer_name", value="Harbor Freight Lines"), KV(name="recipient_tin_last4", value="0001"),
                KV(name="box1", value="61200.00"), KV(name="box2", value="5100.00")],
        summary="2026 W-2 from Harbor Freight Lines", confidence=0.95)})


# --------------------------------------------------------------------------------------------------- (a)
def test_a_old_year_record_received_today_is_purged_the_same_day(fam):  # noqa: F811
    """Path (a): intake -> retention_for counts from 31 December of the document's tax year and never from the date
    the firm received it (records.py:54; pipeline.py:93-94), so a 2018 W-2 received on 2026-10-08 is stored with
    retain_until 2025-12-31, is listed by due_for_deletion and is destroyed by purge_expired on the day it arrives.
    The firm cannot know at that moment whether, when or why (non-filer notice, examination) the 2018 return is still
    open; a record must not be deletable on the day it is received."""
    today = date(2026, 10, 8)
    w2 = receive(fam, "w2-2018-pine-harbor.txt", W2_2018, received=today)
    assert w2["status"] == "filed" and w2["tax_year"] == 2018
    listed = w2["id"] in due(fam.conn, today)
    receipts = records.purge_expired(fam.conn, fam.vault, today, actor="maya", role="cpa", attested=True, reason="annual retention review")
    assert not listed and not receipts and fam.vault.exists(w2["vault_path"]), (
        f"{why(w2)} was received on {today} and on the same day due_for_deletion listed it ({listed}) and purge_expired "
        f"deleted it (receipts {[r['document_id'] for r in receipts]}; bytes still stored: {fam.vault.exists(w2['vault_path'])})")


# --------------------------------------------------------------------------------------------------- (b)
def test_b_late_filed_return_records_deletable_inside_the_assessment_period(fam):  # noqa: F811
    """Path (b): a late-filed return. The 2026 return is prepared from the client's W-2 and paper-filed on 2032-06-01
    through the real workflow (returns/store.py: mark_paper_filed, store.py:159). No filing transition updates
    documents.retain_until and due_for_deletion never consults the return, so the W-2 keeps retain_until 2033-12-31
    (counted from the tax year), while IRC 6501(a) lets the IRS assess the late return until 2035-06-01, three years
    after it was filed (6501(e) would allow until 2038-06-01). purge_expired on 2035-05-31 destroys the W-2 that the
    filed return cites. Any 2026 return filed after 2027-12-31 already outlives retention under 6501(e), and any filed
    after 2030-12-31 under 6501(a)."""
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1), router=w2_2026_router())
    assert w2["status"] == "filed" and w2["tax_year"] == 2026
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2032, 6, 1)):                                   # filed five years late
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
        st = paper_file(R, rid)
    assert st.status == "paper_filed" and st.history[-1]["at"].startswith("2032-06-01")
    assert w2["id"] in {p.get("document_id") for p in R.latest(rid)["provenance"].values()}   # the filed return relies on it
    day = date(2035, 5, 31)
    receipts = records.purge_expired(fam.conn, fam.vault, day, actor="lee", role="cpa", attested=True, reason="annual retention review")
    assert w2["id"] not in {r["document_id"] for r in receipts} and fam.vault.exists(w2["vault_path"]), (
        f"the 2026 return was filed on 2032-06-01 and may be assessed until 2035-06-01 (IRC 6501(a)), but purge_expired "
        f"on {day} deleted {why(w2)}, a source document of that filed return")


# --------------------------------------------------------------------------------------------------- (c)
def test_c_unfiled_year_records_become_deletable(fam):  # noqa: F811
    """Path (c): a year for which no return was filed. The client handed over the 2018 W-2 in February 2019 but the
    2018 return was never filed; the firm has now opened the delinquent 2018 return, still 'preparing'. With no return
    filed the IRS may assess at any time (IRC 6501(c)(3)), but retention_for counted 7 years from 2018-12-31 and
    due_for_deletion (records.py:110-117) ignores filing status, so the W-2 is due for deletion on 2026-10-08."""
    w2 = receive(fam, "w2-2018-pine-harbor.txt", W2_2018, received=date(2019, 2, 1))
    assert w2["status"] == "filed" and w2["tax_year"] == 2018
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2026, 9, 1)):
        rid = R.create("rivera", 2018, "maya", {**household(), "tax_year": 2018})
    assert R.status(rid).status == "preparing"                    # AgentLedger itself records the year as unfiled
    day = date(2026, 10, 8)
    assert w2["id"] not in due(fam.conn, day), (
        f"no 2018 return has been filed (the 2018 return {rid} is {R.status(rid).status}); IRC 6501(c)(3) has no limit, "
        f"but {why(w2)} is due for deletion on {day}")


# --------------------------------------------------------------------------------------------------- (d)
def test_d_amended_return_does_not_extend_retention(fam, monkeypatch):  # noqa: F811
    """Path (d): amended returns. The 2026 return is filed on time (2027-04-01). On 2033-03-01, inside the six-year
    period for an omission of more than 25% of gross income (IRC 6501(e), to 2033-04-15), the firm amends it through
    start_amendment (store.py:376-385) and paper-files the Form 1040-X reporting the omitted income, with the
    additional tax paid. Tax paid on 2033-03-01 may be claimed back until 2035-03-01 (IRC 6511(a): two years from
    payment; the IRS tells taxpayers to keep records 2 years from the date the tax was paid), so the year's records
    must survive until then. Neither the amendment nor its filing changes documents.retain_until (still 2033-12-31),
    so the W-2 is due for deletion on 2035-02-28. (coverage.lookup is patched only so the 1040-X, 'unsupported' in
    today's registry, can pass the review gate, as tests/test_return_workflow.py does for transmission.)"""
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1), router=w2_2026_router())
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2027, 4, 1)):                                   # the original, filed on time
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
        paper_file(R, rid)
    with on(date(2033, 3, 1)):                                   # the amendment, with the omitted income and more tax
        am = R.start_amendment(rid, "maya")
        inputs = R.latest(am)["inputs"]
        inputs["interest"].append({"owner": "taxpayer", "payer": "Overseas Savings Bank", "interest": "40000.00"})   # the omitted income
        inputs["amendment"] = {"explanation": "omitted 1099-INT: Overseas Savings Bank interest of 40,000", "paid_with_original_return": "0"}
        R.save_inputs(am, inputs, "maya")
        st = paper_file(R, am)
    assert R.get(am)["form"] == "1040-X" and R.get(am)["amends"] == rid and st.status == "paper_filed"
    assert Decimal(R.latest(am)["result"]["summary"]["total_tax"]) > Decimal(R.latest(rid)["result"]["summary"]["total_tax"])
    day = date(2035, 2, 28)
    assert w2["id"] not in due(fam.conn, day), (
        f"the 1040-X filed and paid on 2033-03-01 keeps a refund claim open until 2035-03-01 (IRC 6511(a)), "
        f"but {why(w2)} is due for deletion on {day}")


# --------------------------------------------------------------------------------------------------- (e)
B_2026 = (b"Form 1099-B 2026 Proceeds From Broker and Barter Exchange Transactions\nPayer: Lakeshore Brokerage\n"
          b"Recipient: Alex Rivera  TIN XXX-XX-0001\n1a 500 sh Northwind Biotech Inc - worthless security removal\n"
          b"1d Proceeds 0.00\n1e Cost or other basis 18,000.00\n")


def test_e_worthless_security_claim_window_outlives_retention(fam):  # noqa: F811
    """Path (e): the policy arithmetic itself (config/retention.yaml:10-17 and records.py:54). The policy claims that
    7 years after the end of the tax year covers the 7-year rule, but IRC 6511(d)(1) counts 7 years from the DUE DATE
    of the return (2027-04-15 for 2026), so a claim for a 2026 worthless-security loss may be filed until 2034-04-15
    while the 1099-B that supports it (retain_until 2033-12-31) is due for deletion from 2034-01-01. This holds for
    every on-time and timely extended return."""
    b = receive(fam, "1099b-2026-lakeshore.txt", B_2026, received=date(2027, 2, 10))
    assert b["doc_type"] == "1099-B" and b["tax_year"] == 2026 and b["status"] == "filed"
    day = date(2034, 4, 14)
    assert b["id"] not in due(fam.conn, day), (
        f"a 6511(d) claim for 2026 may be filed until 2034-04-15, but {why(b)} is due for deletion on {day}")


# --------------------------------------------------------------------------------------------------- (f)
def test_f_policy_guard_allows_three_years_from_year_end(fam, home, monkeypatch):  # noqa: F811
    """Path (f): the guard in load_policy (records.py:28-34) refuses only classes shorter than 3 years and compares
    them with the statutory 3 years although the policy counts from 31 December of the tax year while IRC 6501(a)
    counts from filing (an on-time 2026 return is treated as filed on 2027-04-15, 6501(b)(1)). retention.yaml:11 says
    shortening below 7 is refused; it is not. With tax_return_support set to 3 years (accepted), a 2026 W-2 gets
    retain_until 2029-12-31 and is due for deletion before the assessment period ends on 2030-04-15."""
    cfg = home / "config" / "retention.yaml"
    text = cfg.read_text(encoding="utf-8").replace('years: 7\n    basis: "IRC §6501(a)', 'years: 3\n    basis: "IRC §6501(a)')
    assert "years: 3" in text
    cfg.write_text(text, encoding="utf-8")
    try:
        records.load_policy(home / "config")
    except ValueError:
        return                                                   # the shortened policy is refused: safe
    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))            # records.policy() now reads the firm's policy
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1))
    assert w2["tax_year"] == 2026 and w2["status"] == "filed"
    day = date(2030, 4, 14)
    assert w2["id"] not in due(fam.conn, day), (
        f"load_policy accepted a 3-year class counted from the end of the tax year; the on-time 2026 return may be "
        f"assessed until 2030-04-15 (IRC 6501(a)), but {why(w2)} is due for deletion on {day}")


# --------------------------------------------------------------------------------------------------- (g)
RECEIPT = (b"Lakeside Goodwill Donation Center\nDONATION RECEIPT\n03/01/26\nDonor: Alex Rivera\n"
           b"Household goods, 6 boxes\nTotal value of donated items $450.00\nThank you for your donation\n")


def test_g_undated_record_counted_from_receipt(fam):  # noqa: F811
    """Path (g): the received-date fallback (records.py:54). When intake finds no tax year (here the receipt prints a
    two-digit year, which the detector ignores, classify.py:59), retention counts 7 years from the moment the record
    arrived, before the return it supports is even filed. A charitable receipt received 2026-03-01 gets retain_until
    2033-03-01, earlier than the policy's own basis for that year (2033-12-31) and earlier than the six-year period of
    the on-time 2026 return (IRC 6501(e), 2033-04-15)."""
    r = receive(fam, "goodwill-receipt.txt", RECEIPT, received=date(2026, 3, 1))
    assert r["doc_type"] == "Receipt" and r["tax_year"] is None and r["status"] == "filed"
    day = date(2033, 4, 14)
    assert r["id"] not in due(fam.conn, day), (
        f"a record received 2026-03-01 supports the 2026 return (six-year period to 2033-04-15), "
        f"but {why(r)} is due for deletion on {day}")


# --------------------------------------------------------------------------------------------------- (h)
STATEMENT = (b"First Lakeshore Bank - Personal Checking\nAccount holder: Alex and Sam Rivera\n"
             b"Statement period 12/01/26 - 12/31/26\nOpening balance $10,500.00\nCheck 2011  250.00\n"
             b"Check 2012  310.00\nCheck 2013  95.00\nClosing balance $9,845.00\n")


def test_h_misdetected_tax_year_purges_a_current_record_on_arrival(fam):  # noqa: F811
    """Path (h): retention trusts an unconfirmed, heuristic tax year (pipeline.py:86 falls back to the most frequent
    number between 2010 and 2039 in the text, classify.py:59,95-96, whenever the model is absent, unavailable or gives
    no year). A December 2026 bank statement whose cheque numbers 2011-2013 are read as its year is stored with
    retain_until 2018-12-31 and is due for deletion on the day it arrives (2027-01-05). Nobody confirms the year and
    nothing can correct it afterwards (no code path updates documents.tax_year or retain_until)."""
    s = receive(fam, "dec-2026-statement.txt", STATEMENT, received=date(2027, 1, 5))
    assert s["doc_type"] == "Bank statement" and s["status"] == "filed"
    day = date(2027, 1, 5)
    assert s["id"] not in due(fam.conn, day), (
        f"a December 2026 statement received on {day} is due for deletion the same day: {why(s)}")


# --------------------------------------------------------------------------------------------------- (i)
CLOSING = (b"Closing Disclosure\nThis form is a statement of final loan terms and closing costs.\n"
           b"Date Issued 06/10/2015  Closing Date 06/15/2015\nProperty 41 Elm Street, Lakeside\n"
           b"Sale Price $350,000.00\nBorrower Alex Rivera  Seller Pat Moore\nCash to Close $72,450.18\n")


def test_i_basis_record_class_is_unreachable_from_intake(fam):  # noqa: F811
    """Path (i): property basis. retention.yaml:27-29,47-48 keeps a Closing disclosure / Settlement statement until a
    CPA releases it (basis is needed until the disposition year's limitation period ends), but intake can never produce
    those doc types: they are not in the classifier's DocType literal (classify.py:13-20, which the router validates)
    and no detector emits them. The 2015 closing disclosure of a home still owned falls to the default class,
    tax_return_support, even after a CPA assigns it from the review queue (pipeline.assign keeps the intake retention),
    and is due for deletion from 2023-01-01. The same default applies to other basis records (K-1, Capital account
    statement, Trade confirmation, asset invoices)."""
    with on(date(2016, 1, 15)):
        out = pipeline.ingest(fam.conn, None, fam.vault, "closing-disclosure-41-elm.txt", CLOSING, channel="upload",
                              client_hint="rivera")[0]
    assert out["status"] == "needs_review" and out["tax_year"] == 2015
    pipeline.assign(fam.conn, fam.vault, out["id"], "rivera", "maya")
    cd = dict(fam.conn.execute("SELECT * FROM documents WHERE id = ?", (out["id"],)).fetchone())
    assert cd["status"] == "filed" and cd["client_id"] == "rivera"
    day = date(2026, 10, 8)
    assert cd["id"] not in due(fam.conn, day), (
        f"the closing disclosure for a home still owned (basis record, class property_basis is 'until released') is "
        f"due for deletion on {day}: {why(cd)}")


# --------------------------------------------------------------------------------------------------- the arithmetic
GRACE = timedelta(days=records.GRACE_DAYS)


def confirm(conn, doc_id: str, year: int | None, cls: str = "tax_return_support"):
    records.confirm_retention(conn, doc_id, tax_year=year, retention_class=cls, actor="lee", role="cpa",
                              note="year and class checked against the document")


def end_of(conn, doc_id: str):
    doc = dict(conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone())
    return records.retention_end(conn, records.policy(), doc)


def deletable_from(conn, doc_id: str, first: date) -> None:
    """Kept on the day before `first`, due from `first`."""
    assert doc_id not in due(conn, first - timedelta(days=1)), f"{doc_id} is due before {first}"
    assert doc_id in due(conn, first), f"{doc_id} is not due on {first}: {end_of(conn, doc_id)}"


def test_on_time_return_counts_from_the_due_date(fam):  # noqa: F811
    """Filed early (2027-04-01) through the workflow: treated as filed on the due date, 2027-04-15 (6501(b)(1)); the
    W-2 is kept 7 years from then (6511(d)(1)) plus the margin."""
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1), router=w2_2026_router())
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2027, 4, 1)):
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
        paper_file(R, rid)
    assert end_of(fam.conn, w2["id"]) == (None, "a person has not confirmed its tax year and retention class")
    confirm(fam.conn, w2["id"], 2026)
    deletable_from(fam.conn, w2["id"], date(2034, 4, 15) + GRACE + timedelta(days=1))


def test_late_return_counts_from_its_filing(fam):  # noqa: F811
    """Path (b) confirmed: filed 2032-06-01, kept until 7 years after that filing plus the margin."""
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1), router=w2_2026_router())
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2032, 6, 1)):
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
        paper_file(R, rid)
    confirm(fam.conn, w2["id"], 2026)
    deletable_from(fam.conn, w2["id"], date(2039, 6, 1) + GRACE + timedelta(days=1))


def test_amendment_and_payment_extend_retention(fam, monkeypatch):  # noqa: F811
    """Path (d) confirmed: the 1040-X paper-filed on 2033-03-01 restarts the count from that filing; a payment recorded
    later (2041-01-10) keeps the records two years past it (6511(a))."""
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1), router=w2_2026_router())
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2027, 4, 1)):
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
        paper_file(R, rid)
    with on(date(2033, 3, 1)):
        am = R.start_amendment(rid, "maya")
        inputs = R.latest(am)["inputs"]
        inputs["interest"].append({"owner": "taxpayer", "payer": "Overseas Savings Bank", "interest": "40000.00"})   # the omitted income
        inputs["amendment"] = {"explanation": "omitted 1099-INT: Overseas Savings Bank interest of 40,000", "paid_with_original_return": "0"}
        R.save_inputs(am, inputs, "maya")
        paper_file(R, am)
    confirm(fam.conn, w2["id"], 2026)
    deletable_from(fam.conn, w2["id"], date(2040, 3, 1) + GRACE + timedelta(days=1))
    records.record_tax_event(fam.conn, "rivera", 2026, "payment", "2041-01-10", actor="lee", role="cpa", form="1040",
                             note="IRS account transcript: payment posted 2041-01-10")
    deletable_from(fam.conn, w2["id"], date(2043, 1, 10) + GRACE + timedelta(days=1))


def test_unfiled_year_is_kept_until_a_filing_is_recorded(fam):  # noqa: F811
    """Path (c) confirmed: no 2018 return on record, so the W-2 is never due, however old. A CPA records the late
    filing with its evidence; the count starts from it."""
    w2 = receive(fam, "w2-2018-pine-harbor.txt", W2_2018, received=date(2019, 2, 1))
    confirm(fam.conn, w2["id"], 2018)
    end, why = end_of(fam.conn, w2["id"])
    assert end is None and "6501(c)(3)" in why and w2["id"] not in due(fam.conn, date(2099, 1, 1))
    with pytest.raises(records.RetentionError):
        records.record_tax_event(fam.conn, "rivera", 2018, "filed", "2026-11-02", actor="sam", role="staff", form="1040",
                                 note="IRS account transcript shows the return received")
    with pytest.raises(records.RetentionError):
        records.record_tax_event(fam.conn, "rivera", 2018, "filed", "2026-11-02", actor="lee", role="cpa", form="1040", note="filed")
    with pytest.raises(ValueError):
        records.record_tax_event(fam.conn, "rivera", 2018, "lost", "2026-11-02", actor="lee", role="cpa", form="1040",
                                 note="IRS account transcript shows the return received")
    records.record_tax_event(fam.conn, "rivera", 2018, "filed", "2026-11-02", actor="lee", role="cpa", form="1040",
                             note="IRS account transcript shows the return received 2026-11-02")
    deletable_from(fam.conn, w2["id"], date(2033, 11, 2) + GRACE + timedelta(days=1))
    with pytest.raises(Exception):
        fam.conn.execute("DELETE FROM tax_year_events")                     # what retention counted from stays on record


def test_worthless_security_window_is_covered(fam):  # noqa: F811
    """Path (e) confirmed: the 1099-B of 2026 is kept past 2034-04-15, the end of the 6511(d)(1) claim window."""
    b = receive(fam, "1099b-2026-lakeshore.txt", B_2026, received=date(2027, 2, 10))
    confirm(fam.conn, b["id"], 2026)
    records.record_tax_event(fam.conn, "rivera", 2026, "filed", "2027-03-20", actor="lee", role="cpa", form="1040",
                             note="IRS account transcript shows the return received 2027-03-20")
    end, _ = end_of(fam.conn, b["id"])
    assert end == date(2034, 4, 15) + GRACE
    assert b["id"] not in due(fam.conn, date(2034, 4, 15))


def _event(conn, client, year, kind, on, form, period=None, note="IRS account transcript shows it received"):
    records.record_tax_event(conn, client, year, kind, on, actor="lee", role="cpa", form=form, period=period, note=note)


def test_payroll_records_count_from_every_employment_and_income_return(biz):
    """Payroll records wait for every employment tax return of the year (each quarter's 941, the 940) and for the
    income tax return their wage deduction supports; for a pass-through entity, also for its owners' returns. A
    late employment tax payment keeps them 4 years past it."""
    from test_evidence import _doc

    _doc(biz.conn, biz.vault, "doc_payroll", "acme", b"2026 payroll register", "2027-01-01", doc_type="Payroll report")
    confirm(biz.conn, "doc_payroll", 2026, "employment_tax")
    _event(biz.conn, "acme", 2026, "filed", "2027-03-10", "1120-S")
    assert "income through to its owners" in end_of(biz.conn, "doc_payroll")[1]          # an S corporation
    _event(biz.conn, "acme", 2026, "owners_filed", "2027-04-12", "1040", note="shareholders' 1040s on file, transcripts")
    assert "no 941 for Q1, Q2, Q3, Q4" in end_of(biz.conn, "doc_payroll")[1]
    for q, on in (("Q1", "2026-04-30"), ("Q2", "2026-07-31"), ("Q3", "2026-10-30")):
        _event(biz.conn, "acme", 2026, "filed", on, "941", period=q)
    assert "no 941 for Q4" in end_of(biz.conn, "doc_payroll")[1]                         # one quarter never filed
    _event(biz.conn, "acme", 2026, "filed", "2027-01-29", "941", period="Q4")
    assert "Form 940" in end_of(biz.conn, "doc_payroll")[1]
    _event(biz.conn, "acme", 2026, "filed", "2027-01-29", "940")
    assert end_of(biz.conn, "doc_payroll")[0] == date(2034, 4, 15) + GRACE
    _event(biz.conn, "acme", 2026, "payment", "2031-06-01", "941", period="Q4", note="Q4 balance paid 2031-06-01, transcript")
    assert end_of(biz.conn, "doc_payroll")[0] == date(2035, 6, 1) + GRACE                 # 4 years past the payment


def test_only_federal_returns_count_and_quarters_are_required(biz, fam):  # noqa: F811
    """An extension, a state return or an information return does not start a limitation period; a 941 needs its
    quarter; an individual's return is a 1040 and a business's owners' 1040 is recorded as owners_filed."""
    for form in ("4868", "CA 540", "709", "1096", "FinCEN 114"):
        with pytest.raises(ValueError, match="not a federal income or employment tax return"):
            _event(biz.conn, "acme", 2026, "filed", "2027-04-10", form)
    with pytest.raises(ValueError, match="quarterly"):
        _event(biz.conn, "acme", 2026, "filed", "2027-01-29", "941")
    with pytest.raises(ValueError, match="annual"):
        _event(biz.conn, "acme", 2026, "filed", "2027-01-29", "940", period="Q4")
    with pytest.raises(ValueError, match="owners_filed"):
        _event(biz.conn, "acme", 2026, "filed", "2027-04-10", "1040")
    with pytest.raises(ValueError, match="individual"):
        _event(fam.conn, "rivera", 2026, "filed", "2027-04-10", "1120-S")
    assert records.normal_form("1040x") == ("1040", True) and records.form_group("941-X") == "employment"


def test_a_journal_entry_adds_its_year(biz):
    """A receipt confirmed as having no tax year, but supporting a 2026 journal entry, waits for a 2026 filing (and,
    for an S corporation, its shareholders' returns)."""
    from test_evidence import _doc

    _doc(biz.conn, biz.vault, "doc_supplies", "acme", b"Office Depot receipt", "2025-01-01")
    assert "doc_supplies" in due(biz.conn, date(2026, 10, 8))               # no year, received long ago: due
    ledger.post(biz.conn, "acme", date(2026, 3, 2), "office supplies", [Line("1000", Decimal("-45")), Line("4000", Decimal("45"))],
                source="receipt", actor="maya", document_id="doc_supplies")
    end, why = end_of(biz.conn, "doc_supplies")
    assert end is None and "journal entry" in why
    _event(biz.conn, "acme", 2026, "filed", "2027-03-10", "1120-S")
    _event(biz.conn, "acme", 2026, "owners_filed", "2032-06-01", "1040", note="the shareholder's 1040 was filed late, transcript")
    assert end_of(biz.conn, "doc_supplies")[0] == date(2039, 6, 1) + GRACE   # counted from the owners' late return


def test_a_capitalized_cost_is_a_basis_record(biz):
    """An invoice behind a debit to Equipment supports basis until the asset is disposed of: kept until released."""
    from test_evidence import _doc

    _doc(biz.conn, biz.vault, "doc_lathe", "acme", b"Invoice: CNC lathe", "2025-01-01", doc_type="Invoice")
    ledger.post(biz.conn, "acme", date(2018, 3, 2), "CNC lathe", [Line("1500", Decimal("48000")), Line("1000", Decimal("-48000"))],
                source="invoice", actor="maya", document_id="doc_lathe")
    end, why = end_of(biz.conn, "doc_lathe")
    assert end is None and "capitalized cost (Equipment)" in why


def test_a_return_that_relied_on_a_document_keeps_its_year(fam):  # noqa: F811
    """The 2026 W-2 is confirmed, wrongly, as a 2025 document: the 2026 return that relied on it still counts. A
    confirmation can add a year to wait for, never remove one the records show."""
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1), router=w2_2026_router())
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2032, 6, 1)):                                    # filed late
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
        paper_file(R, rid)
    assert db.one(fam.conn, "SELECT 1 AS x FROM return_document_uses WHERE return_id = ? AND document_id = ?", rid, w2["id"])
    confirm(fam.conn, w2["id"], 2025)
    end, why = end_of(fam.conn, w2["id"])
    assert end is None and "2025" in why                          # no 2025 return on record
    records.record_tax_event(fam.conn, "rivera", 2025, "filed", "2026-04-10", actor="lee", role="cpa", form="1040",
                             note="IRS account transcript shows the 2025 return received 2026-04-10")
    assert end_of(fam.conn, w2["id"])[0] == date(2039, 6, 1) + GRACE         # the late 2026 filing still governs


def test_confirming_retention_is_a_recorded_cpa_decision(fam):  # noqa: F811
    """Paths (g), (h) and (i) confirmed: a heuristic year never drives deletion; a CPA confirms the year (or none) and
    the class, with a note, in the audit chain. A basis record stays until released whatever its age."""
    s = receive(fam, "dec-2026-statement.txt", STATEMENT, received=date(2027, 1, 5))
    with pytest.raises(records.RetentionError):
        records.confirm_retention(fam.conn, s["id"], tax_year=2026, retention_class="tax_return_support", actor="sam",
                                  role="staff", note="checked the statement period")
    with pytest.raises(records.RetentionError):
        records.confirm_retention(fam.conn, s["id"], tax_year=2026, retention_class="tax_return_support", actor="lee",
                                  role="cpa", note="ok")
    with pytest.raises(ValueError):
        records.confirm_retention(fam.conn, s["id"], tax_year=2026, retention_class="forever", actor="lee", role="cpa",
                                  note="checked the statement period")
    records.confirm_retention(fam.conn, s["id"], tax_year=2026, retention_class="tax_return_support", actor="lee", role="cpa",
                              note="statement period is December 2026")
    d = db.one(fam.conn, "SELECT tax_year, retention_confirmed_by FROM documents WHERE id = ?", s["id"])
    assert (d["tax_year"], d["retention_confirmed_by"]) == (2026, "lee")
    [e] = [e for e in audit.events(fam.conn, "rivera") if e["action"] == "evidence.retention_confirmed"]
    assert e["payload"]["tax_year"] == [s["tax_year"], 2026]
    with on(date(2016, 1, 15)):
        cd = pipeline.ingest(fam.conn, None, fam.vault, "closing-disclosure-41-elm.txt", CLOSING, channel="upload",
                             client_hint="rivera")[0]
    pipeline.assign(fam.conn, fam.vault, cd["id"], "rivera", "maya")
    confirm(fam.conn, cd["id"], 2015, "property_basis")
    assert end_of(fam.conn, cd["id"]) == (None, "class property_basis is kept until a CPA releases it")
