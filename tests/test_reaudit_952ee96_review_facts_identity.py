"""Review of the 952ee96 re-audit fixes, document identities. The first adversarial review found that `source_document`,
the key that ties a list item to its document, was trusted as sent by the client: an unknown top-level input carrying
document ids was stored, never computed, and counted the W-2s as "on the return" (review and approval with no W-2); an
item copied with its document's identity was dropped by the next population, or received another payer's document
value from a conflict resolution; and returns stored before identities were carried in the data lost them on the
first unrelated edit, after which the next population counted every W-2 twice. Asserted now: unknown inputs and
hand-made, copied or foreign identities are refused (InputRejected, HTTP 400) with nothing stored; a second account
entered by hand is kept with its own amount and no document provenance; legacy items keep their documents across an
edit.
"""

from __future__ import annotations

import json

import pytest
from test_engagements import firm  # noqa: F401  (fixture)
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)
from test_tenancy import api  # noqa: F401  (fixture that firm depends on)

from agentledger.returns.facts import InputRejected
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

NOT_ON_RETURN = "not on the return"
TWICE = "appears more than once"
JORDAN = {"filing_status": "single",
          "taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0009", "dob": "1990-01-01"}}


def _inputs(R, rid):
    return json.loads(json.dumps(R.latest(rid)["inputs"]))


def _interest(R, rid):
    return [(i["payer"], i["interest"]) for i in R.latest(rid)["inputs"]["interest"]]


def _second_account(edited, *, with_identity):
    """The preparer adds a second account's 1099-INT by copying the First Bank item and editing payer and amount; the
    copy is listed first. With its identity it claims First Bank's document; without it, it is a hand entry."""
    orig = next(i for i in edited["interest"] if i["payer"] == "First Bank")
    copy = {**orig, "payer": "Harbor Credit Union", "interest": "50.00"}
    if not with_identity:
        copy.pop("source_document")
    edited["interest"] = [copy] + edited["interest"]
    return edited


# --------------------------------------------------------------------------- stray and hand-made identities
@pytest.mark.parametrize("via", ["save_inputs", "create"])
def test_a_stray_source_document_does_not_account_for_a_filed_w2(fam, via):  # noqa: F811
    stray = {**household(), "worksheet": [{"source_document": d} for d in ("d_w2a", "d_w2b", "d_int", "d_nec")]}
    R = Returns(fam.conn, fam.kb)
    if via == "create":
        with pytest.raises(InputRejected, match=r"unknown return input\(s\): worksheet"):
            R.create("rivera", 2026, "maya", stray)
        assert R.for_client("rivera") == []                  # nothing stored
        rid = R.create("rivera", 2026, "maya", household())
    else:
        rid = R.create("rivera", 2026, "maya", household())
        before = R.latest(rid)["version"]
        with pytest.raises(InputRejected, match=r"unknown return input\(s\): worksheet"):
            R.save_inputs(rid, stray, "maya")
        assert R.latest(rid)["version"] == before and "worksheet" not in R.latest(rid)["inputs"]
    R.compute(rid, "maya")
    assert R.unaccounted_documents(rid) == ["d_int", "d_nec", "d_w2a", "d_w2b"]
    with pytest.raises(TransitionError, match=NOT_ON_RETURN):
        R.submit_for_review(rid, "maya")


HAND_MADE = {
    "w2-claims-a-document": {"w2s": [{"employer_name": "Lakeside Market", "wages": "52000", "source_document": "d_w2a"}]},
    "identity-in-another-list": {"businesses": [{"name": "Rivera Consulting", "gross_receipts": "1500",
                                                 "source_document": "d_nec"}]},
    "identity-of-another-form": {"interest": [{"payer": "First Bank", "interest": "312.40", "source_document": "d_w2a"}]},
}


@pytest.mark.parametrize("extra", list(HAND_MADE.values()), ids=list(HAND_MADE))
@pytest.mark.parametrize("via", ["save_inputs", "create"])
def test_a_hand_made_identity_is_refused(fam, via, extra):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    if via == "create":
        with pytest.raises(InputRejected, match="added by populating from documents"):
            R.create("rivera", 2026, "maya", {**household(), **extra})
        assert R.for_client("rivera") == []
    else:
        rid = R.create("rivera", 2026, "maya", household())
        with pytest.raises(InputRejected, match="added by populating from documents"):
            R.save_inputs(rid, {**household(), **extra}, "maya")
        assert R.unaccounted_documents(rid) == ["d_int", "d_nec", "d_w2a", "d_w2b"]


# --------------------------------------------------------------------------- a copied identity
def test_a_copied_item_keeps_its_own_amount_and_is_never_dropped(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.confirm(rid, None, "maya")
    before = R.latest(rid)["version"]
    with pytest.raises(InputRejected, match=TWICE):
        R.save_inputs(rid, _second_account(_inputs(R, rid), with_identity=True), "maya")
    assert R.latest(rid)["version"] == before and _interest(R, rid) == [("First Bank", "312.40")]

    R.save_inputs(rid, _second_account(_inputs(R, rid), with_identity=False), "maya")
    v = R.latest(rid)
    i_cu = next(j for j, i in enumerate(v["inputs"]["interest"]) if i["payer"] == "Harbor Credit Union")
    prov = v["provenance"].get(f"interest[{i_cu}].interest")
    assert prov is None or (prov.get("source") == "preparer" and not prov.get("document_id")), prov
    R.populate_from_documents(rid, "maya")                     # e.g. after another document arrived
    assert sorted(_interest(R, rid)) == [("First Bank", "312.40"), ("Harbor Credit Union", "50.00")]
    assert R.conflicts(rid) == []


def test_a_document_resolution_never_writes_into_another_payers_item(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    edited = _inputs(R, rid)
    next(i for i in edited["interest"] if i["payer"] == "First Bank")["interest"] = "300.00"
    R.save_inputs(rid, edited, "maya")
    R.populate_from_documents(rid, "maya")
    [c] = R.conflicts(rid)
    assert (c["anchor"], c["proposed_value"]) == ("interest[d_int].interest", "312.40")
    with pytest.raises(InputRejected, match=TWICE):            # while the conflict is open
        R.save_inputs(rid, _second_account(_inputs(R, rid), with_identity=True), "maya")
    R.save_inputs(rid, _second_account(_inputs(R, rid), with_identity=False), "maya")
    R.resolve_conflict(rid, c["id"], "document", "lee", note="use the 1099-INT")
    assert dict(_interest(R, rid)) == {"Harbor Credit Union": "50.00", "First Bank": "312.40"}


# --------------------------------------------------------------------------- returns stored before identities were carried
def _legacy(R, rid):
    """The same return as 952ee96 stored it: no source_document in the items."""
    v = R.latest(rid)
    inputs = json.loads(json.dumps(v["inputs"]))
    for key in ("w2s", "interest"):
        for item in inputs.get(key, []):
            item.pop("source_document", None)
    return inputs, json.loads(json.dumps(v["provenance"]))


def test_a_hand_edit_keeps_legacy_items_tied_to_their_documents(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    scratch = R.create("rivera", 2026, "maya", household(), form="1040-SCRATCH")
    R.populate_from_documents(scratch, "maya")
    R.confirm(scratch, None, "maya")
    inputs, prov = _legacy(R, scratch)
    rid = R.create("rivera", 2026, "maya", inputs, provenance=prov, form="1040")   # stored before the upgrade
    R.compute(rid, "maya")
    assert R.unaccounted_documents(rid) == ["d_nec"]               # legacy items are recognised by provenance

    edited = _inputs(R, rid)
    edited["payments"] = {"estimated_tax_payments": "500"}
    R.save_inputs(rid, edited, "maya")                             # an unrelated edit
    after = R.latest(rid)["provenance"]
    lost = sorted(k for k in prov if k.startswith(("w2s[", "interest[")) and k not in after)
    assert lost == []
    assert R.unaccounted_documents(rid) == ["d_nec"]
    assert [w.get("source_document") for w in R.latest(rid)["inputs"]["w2s"]] == ["d_w2a", "d_w2b"]

    R.populate_from_documents(rid, "maya")
    w2s = [(w.get("employer_name"), w.get("wages"), w.get("source_document")) for w in R.latest(rid)["inputs"]["w2s"]]
    assert w2s == [("Lakeside Market", "52000.00", "d_w2a"), ("City Schools", "41000", "d_w2b")]
    assert _interest(R, rid) == [("First Bank", "312.40")] and R.conflicts(rid) == []


# --------------------------------------------------------------------------- over the production HTTP API
def _setup(firm):  # noqa: F811
    mod, c, p, ids = firm
    admin, sam, lee = p["admin"], p["sam"], p["lee"]
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=admin).status_code == 200
    assert c.post(f"/api/auth/users/{ids['sam']}/grants", json={"client_id": "jordan-lee"}, headers=lee).status_code == 200
    conn = mod.A(c.get("/api/me", headers=lee).json()).conn
    add_doc(conn, "d_w2x", "jordan-lee", "W-2", {"employer_name": "Night Shift Co", "box1": "60,000.00", "box2": "6,000.00"})
    add_doc(conn, "d_intx", "jordan-lee", "1099-INT", {"payer_name": "First Bank", "box1": "312.40"})
    r = c.post("/api/clients/jordan-lee/returns", json={"tax_year": 2026, "inputs": JORDAN}, headers=sam)
    assert r.status_code == 200, r.text
    return c, sam, lee, r.json()["id"]


def test_http_a_stray_source_document_hides_a_filed_w2_from_review(firm):  # noqa: F811
    c, sam, lee, rid = _setup(firm)
    body = {**JORDAN, "tax_year": 2026, "worksheet": [{"source_document": "d_w2x"}, {"source_document": "d_intx"}]}
    put = c.put(f"/api/returns/{rid}/inputs", json=body, headers=sam)
    assert put.status_code == 400 and "unknown return input(s): worksheet" in put.json()["detail"], put.text
    hidden = {**JORDAN, "tax_year": 2026, "dependents": [{"first_name": "Kai", "dob": "2015-01-01", "relationship": "son",
                                                         "source_document": "d_w2x"}]}
    put = c.put(f"/api/returns/{rid}/inputs", json=hidden, headers=sam)
    assert put.status_code == 400 and "added by populating from documents" in put.json()["detail"], put.text
    created = c.post("/api/clients/jordan-lee/returns", json={"tax_year": 2025, "inputs": {**JORDAN, "worksheet": []}},
                     headers=sam)
    assert created.status_code == 400 and "unknown return input" in created.json()["detail"], created.text

    assert c.post(f"/api/returns/{rid}/compute", json={}, headers=sam).status_code == 200
    submit = c.post(f"/api/returns/{rid}/submit", json={}, headers=sam)
    assert submit.status_code == 409 and NOT_ON_RETURN in submit.json()["detail"], submit.text
    assert c.post(f"/api/returns/{rid}/approve", headers=lee).status_code == 409
    final = c.get(f"/api/returns/{rid}", headers=lee).json()
    assert final["status"] == "preparing" and "worksheet" not in final["inputs"] and "dependents" not in final["inputs"]


def test_http_a_copied_item_is_silently_dropped_by_the_next_population(firm):  # noqa: F811
    c, sam, lee, rid = _setup(firm)
    assert c.post(f"/api/returns/{rid}/populate", headers=sam).status_code == 200
    got = c.get(f"/api/returns/{rid}", headers=sam).json()["inputs"]
    put = c.put(f"/api/returns/{rid}/inputs", json=_second_account(json.loads(json.dumps(got)), with_identity=True), headers=sam)
    assert put.status_code == 400 and TWICE in put.json()["detail"], put.text
    assert c.post(f"/api/returns/{rid}/populate", headers=sam).status_code == 200
    after = c.get(f"/api/returns/{rid}", headers=sam).json()["inputs"]["interest"]
    assert [(i["payer"], i["interest"]) for i in after] == [("First Bank", "312.40")]

    put = c.put(f"/api/returns/{rid}/inputs", json=_second_account(json.loads(json.dumps(got)), with_identity=False), headers=sam)
    assert put.status_code == 200, put.text
    assert c.post(f"/api/returns/{rid}/populate", headers=sam).status_code == 200
    after = c.get(f"/api/returns/{rid}", headers=sam).json()["inputs"]["interest"]
    assert sorted((i["payer"], i["interest"]) for i in after) == [("First Bank", "312.40"), ("Harbor Credit Union", "50.00")]
    assert c.get(f"/api/returns/{rid}/conflicts", headers=sam).json() == []
