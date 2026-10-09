"""Second adversarial review of the 952ee96 re-audit fixes, provenance: a hand edit of a value a conflict resolution had
taken from the 1099-INT kept the resolution's provenance, so the return and the append-only fact log attributed the
preparer's entry to the document. Asserted now: the edit is the preparer's (previous_source "resolution", the document
and its value on record), the fact log's newest assertion is the preparer's, and the disagreement with the document
blocks review until re-population raises it as a conflict.
"""

from __future__ import annotations

import json

import pytest
from test_return_workflow import fam, household  # noqa: F401  (fam is a fixture)

from agentledger.returns import facts
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

NEC_NOTE = "issued in error; the payer is sending a corrected 1099"
ANCHOR = "interest[d_int].interest"


def _set_interest(R, rid, value):
    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    next(i for i in edited["interest"] if i["source_document"] == "d_int")["interest"] = value
    R.save_inputs(rid, edited, "maya")


def _field(R, rid):
    v = R.latest(rid)
    j = next(j for j, i in enumerate(v["inputs"]["interest"]) if i["source_document"] == "d_int")
    return v["inputs"]["interest"][j]["interest"], v["provenance"][f"interest[{j}].interest"]


def test_a_hand_edit_after_a_document_resolution_is_the_preparers(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    _set_interest(R, rid, "300.00")
    R.populate_from_documents(rid, "maya")
    [c] = R.conflicts(rid)
    R.resolve_conflict(rid, c["id"], "document", "lee", note="use the 1099-INT")          # 312.40, from d_int
    value, prov = _field(R, rid)
    assert value == "312.40" and prov["source"] == "resolution" and prov["document_id"] == "d_int"

    _set_interest(R, rid, "300.00")                                                       # later, by hand
    value, prov = _field(R, rid)
    assert value == "300.00"
    assert prov == {"source": "preparer", "edited_by": "maya", "previous_document": "d_int", "previous_value": "312.40",
                    "previous_source": "resolution", "confirmed": True}
    last = facts.history(fam.conn, R.sealer, rid, ANCHOR)[-1]
    assert (last["source"], last["source_ref"], last["value"], last["asserted_by"]) == ("preparer", "maya", "300.00", "maya")
    assert last["supersedes"] is not None                                                 # the resolution's assertion stays

    R.confirm(rid, None, "maya")
    with pytest.raises(TransitionError) as e:
        R.submit_for_review(rid, "maya")
    assert "1 value(s) disagree with the documents and were never decided" in str(e.value), str(e.value)
    R.populate_from_documents(rid, "maya")
    [c] = R.conflicts(rid)
    assert (c["anchor"], c["current_value"], c["proposed_value"], c["current_source"]) == (ANCHOR, "300.00", "312.40", "preparer")
    with pytest.raises(TransitionError) as e:
        R.submit_for_review(rid, "maya")
    assert "1 fact conflict(s) between documents and the return must be resolved" in str(e.value), str(e.value)
