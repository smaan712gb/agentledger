"""Second adversarial review of the 952ee96 re-audit fixes, a document item's owner: population wrote a W-2's owner (the
recipient TIN decides it on a joint return) without provenance, so moving the spouse's W-2 to the taxpayer by hand was
never a conflict and the resulting "excess" social security was refunded on an approved return. Asserted now: the
owner carries provenance when the document's TIN or name identifies it (confirm-all confirms it); a contradicting edit
is the preparer's, blocks every gate until decided, and taking the document's value restores the owner and the refund.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)

from agentledger.ledger import store
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

COUPLE = {"tax_year": 2026, "filing_status": "mfj",
          "taxpayer": {"first_name": "Robin", "last_name": "Park", "ssn": "400-00-0005", "dob": "1980-01-01"},
          "spouse": {"first_name": "Avery", "last_name": "Park", "ssn": "400-00-0006", "dob": "1981-01-01"}}


def _w2(employer, last4):
    return {"employer_name": employer, "recipient_tin_last4": last4, "box1": "150,000.00", "box2": "25,000.00",
            "box3": "150,000.00", "box4": "9,300.00", "box5": "150,000.00", "box6": "2,175.00"}


def _owners(v):
    return {w["source_document"]: (w["owner"], v["provenance"].get(f"w2s[{j}].owner")) for j, w in enumerate(v["inputs"]["w2s"])}


def _refused_for(R, rid, reason):
    with pytest.raises(TransitionError) as e:
        R.submit_for_review(rid, "maya")
    assert reason in str(e.value), f"review was refused, but not for the reason under test: {e.value}"
    assert R.status(rid).status == "preparing"


def test_an_owner_edit_that_contradicts_the_w2_is_a_conflict(foundry):
    store.add_client(foundry.conn, id="park", name="Robin and Avery Park", kind="individual", emails=[], tax_id_last4="0005",
                     domain="general", facts={"taxpayer_ssn_last4": "0005", "spouse_ssn_last4": "0006",
                                              "taxpayer_name": "Robin Park", "spouse_name": "Avery Park"})
    add_doc(foundry.conn, "d_tp", "park", "W-2", _w2("Harbor Logistics", "0005"))
    add_doc(foundry.conn, "d_sp", "park", "W-2", _w2("Summit Health", "0006"))
    R = Returns(foundry.conn, foundry.kb)
    rid = R.create("park", 2026, "maya", COUPLE)
    R.populate_from_documents(rid, "maya")
    assert _owners(R.latest(rid)) == {
        "d_tp": ("taxpayer", {"document_id": "d_tp", "box": "recipient", "value": "taxpayer", "confirmed": False}),
        "d_sp": ("spouse", {"document_id": "d_sp", "box": "recipient", "value": "spouse", "confirmed": False})}
    R.confirm(rid, None, "maya")                                                     # the routine "confirm all"
    assert all(p["confirmed"] and p["confirmed_by"] == "maya" for _, p in _owners(R.latest(rid)).values())
    refund_before = Decimal(R.latest(rid)["result"]["summary"]["refund"])

    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    next(w for w in edited["w2s"] if w["source_document"] == "d_sp")["owner"] = "taxpayer"     # Avery's W-2 (TIN ...0006)
    R.save_inputs(rid, edited, "maya")
    owner, prov = _owners(R.latest(rid))["d_sp"]
    assert owner == "taxpayer" and (prov["source"], prov["previous_document"], prov["previous_value"]) == ("preparer", "d_sp", "spouse")
    assert Decimal(R.latest(rid)["result"]["summary"]["refund"]) > refund_before     # the "excess" social security
    _refused_for(R, rid, "1 value(s) disagree with the documents and were never decided")

    out = R.populate_from_documents(rid, "maya")
    [c] = R.conflicts(rid)
    assert out["conflicts"] == 1
    assert (c["anchor"], c["current_value"], c["proposed_value"], c["current_source"]) == (
        "w2s[d_sp].owner", "taxpayer", "spouse", "preparer")
    _refused_for(R, rid, "1 fact conflict(s) between documents and the return must be resolved")

    R.resolve_conflict(rid, c["id"], "document", "lee", note="Avery's W-2: recipient TIN ...0006")
    assert {d: o for d, (o, _) in _owners(R.latest(rid)).items()} == {"d_tp": "taxpayer", "d_sp": "spouse"}
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"
    assert Decimal(R.latest(rid)["result"]["summary"]["refund"]) == refund_before


def test_an_owner_named_by_the_recipient_name_carries_provenance(fam):  # noqa: F811
    """The 1099-INT names "Alex Rivera" and no TIN: the name identifies the taxpayer, so the owner is the document's."""
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    v = R.latest(rid)
    [j] = [j for j, i in enumerate(v["inputs"]["interest"]) if i["source_document"] == "d_int"]
    assert v["inputs"]["interest"][j]["owner"] == "taxpayer"
    assert v["provenance"][f"interest[{j}].owner"] == {"document_id": "d_int", "box": "recipient", "value": "taxpayer",
                                                       "confirmed": False}
