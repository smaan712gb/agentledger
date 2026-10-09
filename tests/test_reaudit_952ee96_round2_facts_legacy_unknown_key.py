"""Second adversarial review of the 952ee96 re-audit fixes, legacy unknown inputs: a return stored with a key the model
does not know (952ee96 stored PUT /inputs bodies as sent, e.g. `worksheet`) could no longer be populated or take a
document's value, and once filed could never be amended, because the refusal ran on internal saves too. Asserted now:
population, a document resolution and an amendment keep the stored key and work; a person's edit that still carries it
is refused (InputRejected naming it, nothing stored), and the same edit without it is accepted.
"""

from __future__ import annotations

import json

import pytest
from test_return_workflow import fam, household  # noqa: F401  (fam is a fixture)

from agentledger.returns.facts import InputRejected
from agentledger.returns.store import Returns

NEC_NOTE = "issued in error; the payer is sending a corrected 1099"
LEGACY = {"tab": "income"}


def _legacy_key(R, rid):
    """A version as 952ee96 stored a PUT /inputs body that carried a key of the client's own (UI state)."""
    v = R.latest(rid)
    R._save(rid, {**json.loads(json.dumps(v["inputs"])), "worksheet": LEGACY}, v["provenance"], "maya", "edited")
    R.compute(rid, "maya")


def _set_interest(R, rid, value):
    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    next(i for i in edited["interest"] if i["source_document"] == "d_int")["interest"] = value
    R.save_inputs(rid, edited, "maya")


def test_a_legacy_return_with_an_unknown_key_can_still_be_populated(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    _legacy_key(R, rid)
    out = R.populate_from_documents(rid, "maya")
    v = R.latest(rid)
    assert set(out["documents"]) == {"d_w2a", "d_w2b", "d_int"} and out["conflicts"] == 0
    assert v["inputs"]["worksheet"] == LEGACY and [w["source_document"] for w in v["inputs"]["w2s"]] == ["d_w2a", "d_w2b"]


def test_a_legacy_return_can_take_a_documents_value(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    _set_interest(R, rid, "300.00")
    R.populate_from_documents(rid, "maya")
    _legacy_key(R, rid)
    [c] = R.conflicts(rid)
    assert R.resolve_conflict(rid, c["id"], "document", "lee", note="use the 1099-INT") == {"open": 0}
    v = R.latest(rid)
    assert [i["interest"] for i in v["inputs"]["interest"]] == ["312.40"] and v["inputs"]["worksheet"] == LEGACY


def test_a_filed_legacy_return_with_an_unknown_key_can_be_amended(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    _legacy_key(R, rid)
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    R.confirm(rid, None, "maya")
    R.submit_for_review(rid, "maya")
    h = R.approve(rid, "lee", "cpa").facts["approved_hash"]
    R.request_signature(rid, "lee", "cpa")
    R.record_signature(rid, "taxpayer", method="wet_signature", return_hash=h)
    assert R.mark_paper_filed(rid, "lee", "cpa", "mailed by certified mail on 2027-04-10, receipt 7019 0000 0000").status == "paper_filed"
    amended = R.start_amendment(rid, "lee")
    assert R.latest(amended)["inputs"]["worksheet"] == LEGACY          # copied as it was filed
    out = R.populate_from_documents(amended, "maya")
    assert out["conflicts"] == 0 and out["fields"] == 0
    assert [w["source_document"] for w in R.latest(amended)["inputs"]["w2s"]] == ["d_w2a", "d_w2b"]


def test_a_persons_edit_is_refused_while_it_carries_the_legacy_key(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    _legacy_key(R, rid)
    before = R.latest(rid)
    edited = json.loads(json.dumps(before["inputs"]))
    edited["payments"] = {"estimated_tax_payments": "500"}
    with pytest.raises(InputRejected, match=r"unknown return input\(s\): worksheet"):
        R.save_inputs(rid, edited, "maya")                            # the body as read back, key included
    assert R.latest(rid)["version"] == before["version"]              # nothing stored
    edited.pop("worksheet")
    R.save_inputs(rid, edited, "maya")
    v = R.latest(rid)
    assert "worksheet" not in v["inputs"] and v["inputs"]["payments"] == {"estimated_tax_payments": "500"}
