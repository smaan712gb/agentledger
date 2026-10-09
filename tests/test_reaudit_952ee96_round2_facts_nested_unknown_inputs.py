"""Second adversarial review of the 952ee96 re-audit fixes, unknown inputs below the top level: a key the model does not
know inside a list item or a group (a W-2 "box10", "payments.estimated_tax_payment") was stored in the reviewed package
and never computed, because only top-level keys were checked. Asserted now: unknown keys are refused at every depth on
a person's edit and on create (InputRejected naming the key, HTTP 400 over the API) with nothing stored, while
free-form amount maps such as W-2 box 12 still take their codes.
"""

from __future__ import annotations

import pytest
from test_engagements import firm  # noqa: F401  (fixture)
from test_tenancy import api  # noqa: F401  (fixture that firm depends on)

from agentledger.ledger import store
from agentledger.returns.facts import InputRejected
from agentledger.returns.store import Returns

JORDAN = {"tax_year": 2026, "filing_status": "single",
          "taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0009", "dob": "1990-01-01"}}
W2 = {"employer_name": "Night Shift Co", "wages": "60000", "federal_withholding": "6000", "ss_wages": "60000", "ss_tax": "3720",
      "medicare_wages": "60000", "medicare_tax": "870"}
CASES = {   # what is sent, and the unknown key the refusal names
    "w2-box10-under-its-box-name": ({"w2s": [{**W2, "box10": "5000"}], "dependent_care_expenses": "6000",
                                     "dependent_care_qualifying_persons": 2}, "box10"),
    "payments-misspelled": ({"w2s": [W2], "payments": {"estimated_tax_payment": "4000"}}, "payments.estimated_tax_payment"),
    "taxpayer-unknown-field": ({"taxpayer": {**JORDAN["taxpayer"], "middle_name": "Quinn"}}, "taxpayer.middle_name"),
    "dependent-unknown-field": ({"dependents": [{"first_name": "Kai", "dob": "2015-01-01", "relationship": "son",
                                                 "school_grade": "5"}]}, "school_grade"),
}
UNKNOWN = "unknown return input(s)"


@pytest.fixture
def solo(foundry):
    store.add_client(foundry.conn, id="jordan", name="Jordan Lee", kind="individual", emails=[], tax_id_last4="0009",
                     domain="general", facts={"taxpayer_ssn_last4": "0009", "taxpayer_name": "Jordan Lee"})
    return foundry


@pytest.mark.parametrize("extra,key", list(CASES.values()), ids=list(CASES))
def test_an_unknown_input_inside_an_item_or_group_is_refused_on_edit(solo, extra, key):
    R = Returns(solo.conn, solo.kb)
    rid = R.create("jordan", 2026, "maya", JORDAN)
    before = R.latest(rid)
    with pytest.raises(InputRejected) as e:
        R.save_inputs(rid, {**JORDAN, **extra}, "maya")
    assert UNKNOWN in str(e.value) and key in str(e.value), str(e.value)
    after = R.latest(rid)
    assert after["version"] == before["version"] and after["inputs"] == before["inputs"]     # nothing stored


@pytest.mark.parametrize("extra,key", list(CASES.values()), ids=list(CASES))
def test_an_unknown_input_inside_an_item_or_group_is_refused_on_create(solo, extra, key):
    R = Returns(solo.conn, solo.kb)
    with pytest.raises(InputRejected) as e:
        R.create("jordan", 2026, "maya", {**JORDAN, **extra})
    assert UNKNOWN in str(e.value) and key in str(e.value), str(e.value)
    assert R.for_client("jordan") == []


def test_box12_codes_are_still_taken(solo):
    R = Returns(solo.conn, solo.kb)
    rid = R.create("jordan", 2026, "maya", JORDAN)
    R.save_inputs(rid, {**JORDAN, "w2s": [{**W2, "box12": {"D": "3000", "W": "1200"}}]}, "maya")
    assert R.latest(rid)["inputs"]["w2s"][0]["box12"] == {"D": "3000", "W": "1200"}


def test_http_an_unknown_nested_input_is_a_400(firm):  # noqa: F811
    mod, c, p, ids = firm
    admin, sam, lee = p["admin"], p["sam"], p["lee"]
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=admin).status_code == 200
    assert c.post(f"/api/auth/users/{ids['sam']}/grants", json={"client_id": "jordan-lee"}, headers=lee).status_code == 200
    r = c.post("/api/clients/jordan-lee/returns", json={"tax_year": 2026, "inputs": JORDAN}, headers=sam)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    for extra, key in CASES.values():
        put = c.put(f"/api/returns/{rid}/inputs", json={**JORDAN, **extra}, headers=sam)
        assert put.status_code == 400 and UNKNOWN in put.json()["detail"] and key in put.json()["detail"], put.text
    created = c.post("/api/clients/jordan-lee/returns", json={"tax_year": 2025, "inputs": {**JORDAN, "tax_year": 2025,
                                                                                          **CASES["payments-misspelled"][0]}},
                     headers=sam)
    assert created.status_code == 400 and "payments.estimated_tax_payment" in created.json()["detail"], created.text
    final = c.get(f"/api/returns/{rid}", headers=lee).json()
    assert final["version"] == 1 and final["inputs"].get("w2s", []) == [] and "payments" not in final["inputs"]
    assert [x["tax_year"] for x in c.get("/api/clients/jordan-lee/returns", headers=lee).json()] == [2026]
