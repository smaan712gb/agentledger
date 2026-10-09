"""Second adversarial review of the retention fix (re-audit of 952ee96), usability: a basis record (or a record behind a
capitalized cost) could not be released once the property was sold, an AgentLedger return filed with other software or
never needed blocked its documents for good, "1120S", "1040SR" or "941SS" were refused as unknown forms, and entity
types typed "C corporation" or "C-Corp" were taken for pass-through entities. Asserted now: records.release_basis and
POST /api/documents/{id}/basis-release make the record a tax record of the disposition year (kept until that year's
returns are on record, then counted from them); Returns.void and POST /api/returns/{rid}/void end the block without
counting as a filing or voiding a "not required" record; the hyphen-less spellings are the forms they name; both entity
types are C corporations.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from agentledger import audit, db
from agentledger.domains.packs import Packs
from agentledger.domains.service import onboard
from agentledger.evidence import records
from agentledger.ledger import store
from agentledger.ledger.store import Line
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError
from test_engagements import firm  # noqa: F401  (fixture)
from test_reaudit_952ee96_premature_retention import GRACE, _event, confirm, deletable_from, due, end_of, on
from test_reaudit_952ee96_review_retention_later_year_support import put_doc
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fixture)
from test_tenancy import api  # noqa: F401  (fixture that firm depends on)

DAY = timedelta(days=1)
NOT_FILED = "file it, or void it and record where it was filed"


def _no_return(client_id: str, year: int, why: str) -> str:
    return (f"no {year} income tax return of {client_id} is on record as filed or not required: no limitation period runs "
            f"(IRC 6501(c)(3)) ({why})")


# ------------------------------------------------------------------------------------------------- basis release
def test_release_basis_makes_a_basis_record_a_tax_record_of_the_disposition_year(fam):  # noqa: F811
    """The 2015 closing disclosure of 41 Elm Street (property_basis) is kept until released. The home is sold on
    2031-06-30: once a CPA records it, the record waits for the 2031 return (filed 2032-04-10), then 7 years from its
    due date plus the margin."""
    put_doc(fam.conn, fam.vault, "d_closing", "rivera", doc_type="Closing disclosure", tax_year=2015, received=date(2016, 1, 15))
    confirm(fam.conn, "d_closing", 2015, "property_basis")
    _event(fam.conn, "rivera", 2015, "filed", "2016-04-01", "1040", note="IRS account transcript: 2015 Form 1040 received 2016-04-01")
    assert end_of(fam.conn, "d_closing") == (None, "class property_basis is kept until a CPA releases it")
    note = "41 Elm Street sold 2031-06-30, closing statement on file"
    with pytest.raises(records.RetentionError, match="releasing a basis record needs a CPA"):
        records.release_basis(fam.conn, "d_closing", disposed_tax_year=2031, actor="sam", role="staff", note=note)
    with pytest.raises(records.RetentionError, match="record the disposition"):
        records.release_basis(fam.conn, "d_closing", disposed_tax_year=2031, actor="lee", role="cpa", note="sold")
    with pytest.raises(KeyError):
        records.release_basis(fam.conn, "d_missing", disposed_tax_year=2031, actor="lee", role="cpa", note=note)
    records.release_basis(fam.conn, "d_closing", disposed_tax_year=2031, actor="lee", role="cpa", note=note)
    assert end_of(fam.conn, "d_closing") == (None, _no_return("rivera", 2031, "basis released: property disposed of in 2031"))
    assert "d_closing" not in due(fam.conn, date(2099, 1, 1))                # never deleted on the release alone
    _event(fam.conn, "rivera", 2031, "filed", "2032-04-10", "1040", note="IRS account transcript: 2031 Form 1040 received 2032-04-10")
    assert end_of(fam.conn, "d_closing") == (date(2039, 4, 15) + GRACE, "ok")
    deletable_from(fam.conn, "d_closing", date(2039, 4, 15) + GRACE + DAY)
    [e] = [e for e in audit.events(fam.conn, "rivera") if e["action"] == "evidence.basis_released"]
    assert e["payload"] == {"document_id": "d_closing", "disposed_tax_year": 2031, "note": note}


def test_release_basis_of_a_record_behind_a_capitalized_cost(biz):
    """acme's 2026 invoice for a CNC router (debit 1500 Equipment) is kept as a basis record; the router is sold in 2031.
    Released, it waits for the 2031 Form 1120-S and the shareholders' returns, then 7 years from their due date plus
    the margin."""
    put_doc(biz.conn, biz.vault, "d_invoice_cnc", "acme", doc_type="Invoice", received=date(2026, 3, 5))
    store.post(biz.conn, "acme", date(2026, 3, 2), "CNC router, Lakeshore Machine invoice 8841",
               [Line("1500", Decimal("84000")), Line("1000", Decimal("-84000"))], source="invoice", actor="maya",
               document_id="d_invoice_cnc")
    confirm(biz.conn, "d_invoice_cnc", 2026)
    _event(biz.conn, "acme", 2026, "filed", "2027-03-10", "1120-S", note="IRS account transcript: 2026 Form 1120-S received 2027-03-10")
    _event(biz.conn, "acme", 2026, "owners_filed", "2027-04-12", "1040", note="shareholders' 2026 Forms 1040 received, transcripts")
    assert end_of(biz.conn, "d_invoice_cnc") == (
        None, "it supports a capitalized cost (Equipment): basis records are kept until a CPA releases them")
    records.release_basis(biz.conn, "d_invoice_cnc", disposed_tax_year=2031, actor="lee", role="cpa",
                          note="CNC router sold 2031-08-15 to Pine Machine Works, bill of sale on file")
    released = "basis released: property disposed of in 2031"
    assert end_of(biz.conn, "d_invoice_cnc") == (None, _no_return("acme", 2031, released))
    _event(biz.conn, "acme", 2031, "filed", "2032-03-10", "1120-S", note="IRS account transcript: 2031 Form 1120-S received 2032-03-10")
    assert end_of(biz.conn, "d_invoice_cnc") == (None, "acme passes its 2031 income through to its owners, whose returns are "
                                                       f"not on record as filed (record them as owners_filed) ({released})")
    _event(biz.conn, "acme", 2031, "owners_filed", "2032-04-12", "1040", note="shareholders' 2031 Forms 1040 received, transcripts")
    assert end_of(biz.conn, "d_invoice_cnc") == (date(2039, 4, 15) + GRACE, "ok")
    deletable_from(biz.conn, "d_invoice_cnc", date(2039, 4, 15) + GRACE + DAY)


# ------------------------------------------------------------------------------------------------- void
def test_void_a_return_filed_with_other_software(fam):  # noqa: F811
    """The Riveras' 2026 return, prepared in AgentLedger from their W-2s, was filed with other software on 2027-04-10.
    A CPA voids it with the reason: the W-2 stops waiting for it, yet stays kept until that filing is recorded."""
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2027, 3, 1)):
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
    confirm(fam.conn, "d_w2a", 2026)
    assert end_of(fam.conn, "d_w2a") == (None, f"return {rid} (2026), which relied on it, is not filed ({NOT_FILED})")
    note = "filed with other software on 2027-04-10, IRS transcript on file"
    with pytest.raises(TransitionError, match="'void' requires one of: cpa"):
        R.void(rid, "sam", "staff", note)
    with pytest.raises(TransitionError, match="say why the return is void"):
        R.void(rid, "lee", "cpa", "void")
    assert R.void(rid, "lee", "cpa", note).status == "void"
    with pytest.raises(TransitionError, match="'void' is not allowed while the workflow is 'void'"):
        R.void(rid, "lee", "cpa", note)
    assert end_of(fam.conn, "d_w2a") == (None, _no_return("rivera", 2026, f"its confirmed tax year; return {rid} relied on it"))
    assert "d_w2a" not in due(fam.conn, date(2099, 1, 1))
    _event(fam.conn, "rivera", 2026, "filed", "2027-04-10", "1040",
           note="IRS account transcript: 2026 Form 1040 (other software) received 2027-04-10")
    assert end_of(fam.conn, "d_w2a") == (date(2034, 4, 15) + GRACE, "ok")
    deletable_from(fam.conn, "d_w2a", date(2034, 4, 15) + GRACE + DAY)


def test_a_void_return_does_not_void_a_not_required_record(fam):  # noqa: F811
    """Pat Moore's only 2026 income is 312.40 of interest: the CPA started a return from the 1099-INT, then found none
    was required and recorded so on 2027-05-01. While the return is held the record does not count (the 1099-INT waits
    for the return, the bank statement for a filing); once it is void both count from the record: 7 years plus the
    margin."""
    store.add_client(fam.conn, id="pat-moore", name="Pat Moore", kind="individual", tax_id_last4="0031", domain="general")
    put_doc(fam.conn, fam.vault, "d_int_moore", "pat-moore", doc_type="1099-INT", tax_year=2026, received=date(2027, 2, 1),
            fields={"payer_name": "First Bank", "box1": "312.40"})
    put_doc(fam.conn, fam.vault, "d_statement", "pat-moore", doc_type="Bank statement", tax_year=2026, received=date(2027, 1, 8))
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2027, 3, 1)):
        rid = R.create("pat-moore", 2026, "maya", {"tax_year": 2026, "filing_status": "single", "taxpayer": {
            "first_name": "Pat", "last_name": "Moore", "ssn": "400-00-0031", "dob": "1950-02-01"}})
        R.populate_from_documents(rid, "maya")
    confirm(fam.conn, "d_int_moore", 2026)
    confirm(fam.conn, "d_statement", 2026)
    _event(fam.conn, "pat-moore", 2026, "not_required", "2027-05-01", "1040",
           note="2026 gross income 312.40, below the filing threshold: no return required")
    assert end_of(fam.conn, "d_int_moore") == (None, f"return {rid} (2026), which relied on it, is not filed ({NOT_FILED})")
    assert end_of(fam.conn, "d_statement") == (None, _no_return("pat-moore", 2026, "its confirmed tax year"))
    R.void(rid, "lee", "cpa", "no 2026 return required (gross income below the filing threshold), recorded")
    for doc in ("d_int_moore", "d_statement"):
        assert end_of(fam.conn, doc) == (date(2034, 5, 1) + GRACE, "ok")
        deletable_from(fam.conn, doc, date(2034, 5, 1) + GRACE + DAY)


# ------------------------------------------------------------------------------------------------- spellings
def test_forms_typed_without_hyphens_are_the_forms_they_name(biz):
    """Pacific Marine Services Inc (an S corporation employing in Guam) records its 2026 returns as typed: "1120S",
    the shareholder's "1040SR", four "941SS" (Q4 filed late on 2029-02-01) and the 940. Each is stored as the form it
    names and counted: 7 years from the late Q4 Form 941-SS plus the margin."""
    assert [records.normal_form(f) for f in ("1120S", "1120 s", "1040SR", "941SS", "941ss-x", "1120SX")] == [
        ("1120-S", False), ("1120-S", False), ("1040-SR", False), ("941-SS", False), ("941-SS", True), ("1120-S", True)]
    store.add_client(biz.conn, id="pacific-marine", name="Pacific Marine Services Inc", kind="business", entity_type="corporation",
                     tax_id_last4="6610", domain="general", facts={"s_election": "2021-01-01"})
    onboard(biz.conn, Packs(biz.paths.domains), "pacific-marine", "general")
    put_doc(biz.conn, biz.vault, "d_payroll", "pacific-marine", doc_type="Payroll report", tax_year=2026, received=date(2027, 1, 5))
    confirm(biz.conn, "d_payroll", 2026, "employment_tax")
    _event(biz.conn, "pacific-marine", 2026, "filed", "2027-03-10", "1120S", note="IRS account transcript: 2026 Form 1120-S received")
    _event(biz.conn, "pacific-marine", 2026, "owners_filed", "2027-04-12", "1040SR", note="shareholder's 2026 Form 1040-SR, transcript")
    for q, on_ in (("Q1", "2026-04-30"), ("Q2", "2026-07-31"), ("Q3", "2026-10-30"), ("Q4", "2029-02-01")):
        _event(biz.conn, "pacific-marine", 2026, "filed", on_, "941SS", period=q, note=f"IRS account transcript: {q} 2026 Form 941-SS")
    _event(biz.conn, "pacific-marine", 2026, "filed", "2027-01-29", "940", note="IRS account transcript: 2026 Form 940 received")
    assert [r["form"] for r in db.rows(biz.conn, "SELECT form FROM tax_year_events WHERE client_id = 'pacific-marine' ORDER BY id")] == [
        "1120-S", "1040-SR", "941-SS", "941-SS", "941-SS", "941-SS", "940"]
    assert end_of(biz.conn, "d_payroll") == (date(2036, 2, 1) + GRACE, "ok")
    deletable_from(biz.conn, "d_payroll", date(2036, 2, 1) + GRACE + DAY)


CONSOLIDATED = "member of the Harbor Holdings affiliated group: included in the parent's consolidated 2026 Form 1120 filed 2027-04-10"
DISREGARDED = "single-member LLC disregarded for income tax: no entity return; the owner reports it on Schedule C"
ENTITY_TYPES = [
    # (entity type as typed, the 2026 income return recorded as not required, its note, pass-through?)
    ("C corporation", "1120", CONSOLIDATED, False),
    ("C-Corp", "1120", CONSOLIDATED, False),
    ("501(c)(3)", "990-T", "gross unrelated business income under 1,000: no 2026 Form 990-T required", False),
    ("LLC", "1065", DISREGARDED, True),
    (None, "1065", DISREGARDED, True),                                    # unknown: the safe direction
]


@pytest.mark.parametrize("entity_type, form, note, pass_through", ENTITY_TYPES, ids=[str(e[0]) for e in ENTITY_TYPES])
def test_entity_types_typed_as_c_corporations(biz, entity_type, form, note, pass_through):
    """With no filed income return to decide, the entity type does: a C corporation or exempt organization counts from
    the "not required" record (2027-04-10: 7 years from the due date plus the margin); anything else waits for its
    owners' returns."""
    store.add_client(biz.conn, id="harbor-sub", name="Harbor Logistics", kind="business", entity_type=entity_type,
                     tax_id_last4="8801", domain="general")
    onboard(biz.conn, Packs(biz.paths.domains), "harbor-sub", "general")
    put_doc(biz.conn, biz.vault, "d_invoice", "harbor-sub", doc_type="Invoice", tax_year=2026, received=date(2026, 9, 14))
    confirm(biz.conn, "d_invoice", 2026)
    _event(biz.conn, "harbor-sub", 2026, "not_required", "2027-04-10", form, note=note)
    if pass_through:
        assert end_of(biz.conn, "d_invoice") == (None, "harbor-sub passes its 2026 income through to its owners, whose returns "
                                                       "are not on record as filed (record them as owners_filed) (its "
                                                       "confirmed tax year)")
        assert "d_invoice" not in due(biz.conn, date(2099, 1, 1))
    else:
        assert end_of(biz.conn, "d_invoice") == (date(2034, 4, 15) + GRACE, "ok")
        deletable_from(biz.conn, "d_invoice", date(2034, 4, 15) + GRACE + DAY)


# ------------------------------------------------------------------------------------------------- the API routes
def _individual(c, lee, sam_id: str) -> None:
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=lee).status_code == 200
    assert c.post(f"/api/auth/users/{sam_id}/grants", json={"client_id": "jordan-lee"}, headers=lee).status_code == 200


def _filed_on_record(c, lee, year: int, on_: str) -> None:
    r = c.post("/api/clients/jordan-lee/tax-year-events", headers=lee, json={
        "tax_year": year, "kind": "filed", "occurred_on": on_, "form": "1040",
        "note": f"IRS account transcript: {year} Form 1040 received {on_}"})
    assert r.status_code == 200, r.text


def _retention(c, lee, doc_id: str) -> tuple[str | None, str]:
    r = c.get(f"/api/documents/{doc_id}/retention", headers=lee)
    assert r.status_code == 200, r.text
    return r.json()["retention_end"], r.json()["reason"]


def test_basis_release_over_the_api(firm):  # noqa: F811
    """A credentialed reviewer only (staff and an administrator are refused), with the disposition year and a note;
    the answer is the document's retention, now waiting for the disposition year's return."""
    mod, c, p, ids = firm
    lee, sam, kim = p["lee"], p["sam"], p["kim"]
    _individual(c, lee, ids["sam"])
    conn = mod.firm_context("rivera-cpa").conn
    add_doc(conn, "d_closing_api", "jordan-lee", "Closing disclosure", {}, year=2015)
    r = c.post("/api/documents/d_closing_api/retention", headers=lee, json={
        "tax_year": 2015, "retention_class": "property_basis", "note": "2015 closing disclosure of 12 Birch Lane, checked"})
    assert r.status_code == 200 and r.json()["reason"] == "class property_basis is kept until a CPA releases it"
    _filed_on_record(c, lee, 2015, "2016-04-01")
    url = "/api/documents/d_closing_api/basis-release"
    body = {"disposed_tax_year": 2031, "note": "12 Birch Lane sold 2031-06-30, closing statement on file"}
    for who in (sam, kim):
        assert c.post(url, json=body, headers=who).status_code == 403
    assert c.post(url, json={"note": body["note"]}, headers=lee).status_code == 400            # no disposition year
    assert c.post(url, json={**body, "note": "sold"}, headers=lee).status_code == 400
    assert c.post("/api/documents/d_missing/basis-release", json=body, headers=lee).status_code == 404
    r = c.post(url, json=body, headers=lee)
    assert r.status_code == 200, r.text
    assert (r.json()["retention_end"], r.json()["reason"]) == (None, _no_return("jordan-lee", 2031, "basis released: property "
                                                                                                   "disposed of in 2031"))
    _filed_on_record(c, lee, 2031, "2032-04-10")
    assert _retention(c, lee, "d_closing_api") == ((date(2039, 4, 15) + GRACE).isoformat(), "ok")
    assert "evidence.basis_released" in [e["action"] for e in audit.events(conn, "jordan-lee")]


def test_void_over_the_api(firm):  # noqa: F811
    """Voiding needs a credentialed reviewer and a reason, and happens once; the W-2 the return relied on then waits
    for the year's filing on record instead of the void return."""
    mod, c, p, ids = firm
    lee, sam, kim = p["lee"], p["sam"], p["kim"]
    _individual(c, lee, ids["sam"])
    conn = mod.firm_context("rivera-cpa").conn
    add_doc(conn, "d_w2_api", "jordan-lee", "W-2", {"employer_name": "Brightline LLC", "box1": "61200", "box2": "5100"})
    inputs = {"filing_status": "single", "taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0009",
                                                      "dob": "1990-01-01"}}
    r = c.post("/api/clients/jordan-lee/returns", json={"tax_year": 2026, "inputs": inputs}, headers=sam)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert c.post(f"/api/returns/{rid}/populate", headers=sam).status_code == 200
    r = c.post("/api/documents/d_w2_api/retention", headers=lee, json={
        "tax_year": 2026, "retention_class": "tax_return_support", "note": "2026 W-2, checked against the employer's copy"})
    assert r.status_code == 200 and r.json()["reason"] == f"return {rid} (2026), which relied on it, is not filed ({NOT_FILED})"
    url = f"/api/returns/{rid}/void"
    note = {"note": "filed with other software on 2027-04-10, IRS transcript on file"}
    for who in (sam, kim):
        assert c.post(url, json=note, headers=who).status_code == 403
    r = c.post(url, json={"note": "void"}, headers=lee)
    assert r.status_code == 409 and "say why the return is void" in r.text
    r = c.post(url, json=note, headers=lee)
    assert r.status_code == 200 and r.json() == {"status": "void"}
    r = c.post(url, json=note, headers=lee)
    assert r.status_code == 409 and "not allowed while the workflow is 'void'" in r.text
    assert _retention(c, lee, "d_w2_api") == (None, _no_return("jordan-lee", 2026, f"its confirmed tax year; return {rid} relied on it"))
    _filed_on_record(c, lee, 2026, "2027-04-10")
    assert _retention(c, lee, "d_w2_api") == ((date(2034, 4, 15) + GRACE).isoformat(), "ok")
