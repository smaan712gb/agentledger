"""Re-audit of 952ee96: the API routes added by the fixes. Who may record what retention counts from, confirm a
document's retention, move a filed document, and account for a document a return does not use."""

from __future__ import annotations

from test_engagements import firm  # noqa: F401  (fixture)
from test_return_workflow import add_doc
from test_tenancy import api  # noqa: F401  (fixture that firm depends on)

from agentledger import audit, db

W2 = b"Form W-2 Wage and Tax Statement 2026 Employer: Brightline LLC Employee SSN 400-00-0001 Wages 61,200.00"


def _filed_document(c, headers, client_id):
    data = W2 + f" Employer copy for {client_id}".encode()             # distinct bytes per client: not a duplicate
    up = c.post("/api/documents/upload", files={"file": ("w2-2026.txt", data, "text/plain")}, data={"client_id": client_id},
                headers=headers).json()[0]
    r = c.post(f"/api/documents/{up['id']}/assign", json={"client_id": client_id}, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "filed", r.text
    return up["id"]


def test_retention_inputs_need_a_credentialed_reviewer(firm):  # noqa: F811
    """Recording a filing or payment and confirming a document's year and class decide when evidence may be destroyed:
    a CPA only (firm staff and administrators are refused), with a note, and validated."""
    mod, c, p, ids = firm
    lee, sam, kim = p["lee"], p["sam"], p["kim"]
    assert c.post(f"/api/auth/users/{ids['sam']}/grants", json={"client_id": "ortiz-auto"}, headers=lee).status_code == 200
    doc = _filed_document(c, lee, "ortiz-auto")
    event = {"tax_year": 2026, "kind": "filed", "occurred_on": "2027-03-10", "form": "1120-S",
             "note": "IRS account transcript shows the 1120-S received 2027-03-10"}
    for who in (sam, kim):
        assert c.post("/api/clients/ortiz-auto/tax-year-events", json=event, headers=who).status_code == 403
        assert c.post(f"/api/documents/{doc}/retention", json={"tax_year": 2026, "retention_class": "tax_return_support",
                                                               "note": "2026 W-2, checked"}, headers=who).status_code == 403
    assert c.post("/api/clients/ortiz-auto/tax-year-events", json={**event, "kind": "lost"}, headers=lee).status_code == 400
    assert c.post("/api/clients/ortiz-auto/tax-year-events", json={**event, "note": "filed"}, headers=lee).status_code == 400
    assert c.post("/api/clients/ortiz-auto/tax-year-events", json=event, headers=lee).status_code == 200
    r = c.post(f"/api/documents/{doc}/retention", json={"tax_year": 2026, "retention_class": "forever", "note": "2026 W-2, checked"},
               headers=lee)
    assert r.status_code == 400
    r = c.post(f"/api/documents/{doc}/retention", json={"tax_year": 2026, "retention_class": "tax_return_support",
                                                       "note": "2026 W-2, checked against the employer's copy"}, headers=lee)
    assert r.status_code == 200 and r.json()["tax_year"] == 2026 and r.json()["retention_confirmed_at"]
    actions = [e["action"] for e in audit.events(mod.firm_context("rivera-cpa").conn, "ortiz-auto")]
    assert "evidence.tax_year_event" in actions and "evidence.retention_confirmed" in actions


def test_moving_a_filed_document_needs_a_reviewer_a_reason_and_no_hold(firm):  # noqa: F811
    """Filing from the review queue stays a preparer's action; moving a document already filed to a client is a
    reviewer's, with a reason on record, and is refused while a legal hold covers it."""
    mod, c, p, ids = firm
    lee, sam = p["lee"], p["sam"]
    for cid in ("ortiz-auto", "lakeside-fuel"):
        assert c.post(f"/api/auth/users/{ids['sam']}/grants", json={"client_id": cid}, headers=lee).status_code == 200
    doc = _filed_document(c, lee, "ortiz-auto")
    assert c.post(f"/api/documents/{doc}/assign", json={"client_id": "lakeside-fuel"}, headers=sam).status_code == 403
    r = c.post(f"/api/documents/{doc}/assign", json={"client_id": "lakeside-fuel"}, headers=lee)
    assert r.status_code == 400 and "needs a reason" in r.text
    assert c.post("/api/clients/ortiz-auto/holds", json={"reason": "IRS examination letter dated 2026-09-30"},
                  headers=lee).status_code == 200
    r = c.post(f"/api/documents/{doc}/assign", json={"client_id": "lakeside-fuel", "move_reason": "Lakeside's W-2, misfiled"},
               headers=lee)
    assert r.status_code == 400 and "legal hold" in r.text
    doc2 = _filed_document(c, lee, "lakeside-fuel")
    r = c.post(f"/api/documents/{doc2}/assign", json={"client_id": "ortiz-auto", "move_reason": "Ortiz Auto's W-2, misfiled"},
               headers=lee)
    assert r.status_code == 200 and r.json()["client_id"] == "ortiz-auto"
    moved = [e for e in audit.events(mod.firm_context("rivera-cpa").conn, "lakeside-fuel") if e["action"] == "document.moved"]
    assert moved and moved[0]["payload"]["reason"] == "Ortiz Auto's W-2, misfiled"


def test_accounting_for_a_document_over_the_api(firm):  # noqa: F811
    """A preparer accounts for a filed document the return does not use; the answer is validated and blocks review
    until given."""
    mod, c, p, ids = firm
    lee, sam = p["lee"], p["sam"]
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=lee).status_code == 200
    assert c.post(f"/api/auth/users/{ids['sam']}/grants", json={"client_id": "jordan-lee"}, headers=lee).status_code == 200
    conn = mod.firm_context("rivera-cpa").conn
    add_doc(conn, "d_nec_api", "jordan-lee", "1099-NEC", {"payer_name": "Gig Platform", "box1": "1200"})
    inputs = {"filing_status": "single", "taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0009",
                                                      "dob": "1990-01-01"}}
    r = c.post("/api/clients/jordan-lee/returns", json={"tax_year": 2026, "inputs": inputs}, headers=sam)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    c.post(f"/api/returns/{rid}/compute", json={}, headers=sam)
    r = c.post(f"/api/returns/{rid}/submit", json={}, headers=sam)
    assert r.status_code == 409 and "not on the return (d_nec_api)" in r.text
    url = f"/api/returns/{rid}/documents/d_nec_api/disposition"
    assert c.post(url, json={"disposition": "skip", "note": "not needed this year"}, headers=sam).status_code == 400
    assert c.post(url, json={"disposition": "not_applicable", "note": "n/a"}, headers=sam).status_code == 400
    assert c.post(f"/api/returns/{rid}/documents/d_missing/disposition",
                  json={"disposition": "not_applicable", "note": "not this client's document"}, headers=sam).status_code == 404
    r = c.post(url, json={"disposition": "entered_by_hand", "note": "entered on Schedule C line 1 from the 1099-NEC"}, headers=sam)
    assert r.status_code == 200 and r.json()[0]["disposition"] == "entered_by_hand"
    assert db.one(conn, "SELECT 1 AS x FROM return_document_uses WHERE return_id = ? AND document_id = 'd_nec_api'", rid)
