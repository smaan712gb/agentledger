"""Facts never overwrite (backlog F-07, acceptance test Q16).

Re-populating a return from its documents must not overwrite a preparer's entry or another document's value: it
raises a fact conflict, keeps the current value, and blocks review until a person decides. Every accepted value is an
assertion with its source and the assertion it superseded; a missing amount is reported, never taken as zero.
"""

from __future__ import annotations

import json

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)

from agentledger.returns import facts
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

DOC = lambda doc, box, value: {"document_id": doc, "box": box, "value": value}  # noqa: E731


# --------------------------------------------------------------------------- the merge rules
def test_empty_fields_take_document_values_and_same_document_can_correct_itself():
    pop = {"w2s": [{"owner": "taxpayer", "wages": "52000"}]}
    inputs, prov, conflicts, _, changed = facts.merge_population({}, {}, pop, {"w2s[0].wages": DOC("d1", "box1", "52000")})
    assert inputs["w2s"][0]["wages"] == "52000" and prov["w2s[0].wages"]["document_id"] == "d1" and not conflicts
    assert changed == ["w2s[0].wages"]
    again, prov2, conflicts, _, _ = facts.merge_population(
        inputs, prov, {"w2s": [{"owner": "taxpayer", "wages": "52500"}]}, {"w2s[0].wages": DOC("d1", "box1", "52500")})
    assert again["w2s"][0]["wages"] == "52500" and not conflicts            # re-read of the same document


def test_preparer_entries_and_other_documents_are_never_overwritten():
    cur = {"w2s": [{"owner": "taxpayer", "wages": "52000"}], "itemized": {"mortgage_interest_1098": "9100"}}
    prov = {"w2s[0].wages": {"source": "preparer", "edited_by": "maya", "previous_document": "d1", "confirmed": True},
            "itemized.mortgage_interest_1098": DOC("d_1098a", "box1", "9100")}
    pop = {"w2s": [{"owner": "taxpayer", "wages": "51000"}], "itemized": {"mortgage_interest_1098": "9900"}}
    pop_prov = {"w2s[0].wages": DOC("d1", "box1", "51000"), "itemized.mortgage_interest_1098": DOC("d_1098b", "box1", "9900")}
    inputs, prov2, conflicts, _, changed = facts.merge_population(cur, prov, pop, pop_prov)
    assert inputs["w2s"][0]["wages"] == "52000" and inputs["itemized"]["mortgage_interest_1098"] == "9100"
    assert sorted(c["anchor"] for c in conflicts) == ["itemized.mortgage_interest_1098", "w2s[d1].wages"]
    assert changed == []


def test_hand_entered_items_are_kept_and_document_items_match_by_document_not_position():
    cur = {"w2s": [{"owner": "spouse", "wages": "800", "employer_name": "Babysitting (no W-2)"},
                   {"owner": "taxpayer", "wages": "52000"}, {"owner": "spouse", "wages": "41000"}]}
    prov = {"w2s[1].wages": DOC("d_a", "box1", "52000"), "w2s[2].wages": DOC("d_b", "box1", "41000")}
    pop = {"w2s": [{"owner": "spouse", "wages": "41000"}, {"owner": "taxpayer", "wages": "52000"}]}   # order changed
    pop_prov = {"w2s[0].wages": DOC("d_b", "box1", "41000"), "w2s[1].wages": DOC("d_a", "box1", "52000")}
    inputs, prov2, conflicts, issues, _ = facts.merge_population(cur, prov, pop, pop_prov)
    assert [w["wages"] for w in inputs["w2s"]] == ["800", "41000", "52000"] and not conflicts
    assert "w2s[0].wages" not in prov2 and prov2["w2s[1].wages"]["document_id"] == "d_b"
    # A document that stops populating its item: the item stays, with a note.
    inputs, prov3, _, issues, _ = facts.merge_population(inputs, prov2, {"w2s": [{"owner": "spouse", "wages": "41000"}]},
                                                         {"w2s[0].wages": DOC("d_b", "box1", "41000")})
    assert len(inputs["w2s"]) == 3 and any(i["code"] == "document_no_longer_provides" for i in issues)


def test_a_missing_amount_is_reported_not_zero():
    pop = {"w2s": [{"owner": "taxpayer", "employer_name": "Lakeside", "federal_withholding": "4100"}]}
    pop_prov = {"w2s[0].federal_withholding": DOC("d1", "box2", "4100")}
    inputs, prov, _, issues, _ = facts.merge_population({}, {}, pop, pop_prov)
    assert "wages" not in inputs["w2s"][0]
    assert [i["code"] for i in issues] == ["missing_value"] and i_path(issues) == "w2s[0].wages"
    inputs["w2s"][0]["wages"] = "50000"                                         # the preparer types it in
    _, _, _, issues, _ = facts.merge_population(inputs, prov, pop, pop_prov)
    assert not [i for i in issues if i["code"] == "missing_value"]


def i_path(issues):
    return issues[0]["path"]


# --------------------------------------------------------------------------- end to end through the return
def test_repopulation_raises_conflicts_blocks_review_and_records_history(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    v = R.latest(rid)
    assert v["inputs"]["w2s"][0]["wages"] == "52000.00"
    # The preparer corrects the wages by hand (the client's W-2 had a typo); the document's value is kept on record.
    edited = json.loads(json.dumps(v["inputs"]))
    edited["w2s"][0]["wages"] = "52500.00"
    R.save_inputs(rid, edited, "maya")
    assert R.latest(rid)["provenance"]["w2s[0].wages"]["source"] == "preparer"
    out = R.populate_from_documents(rid, "maya")
    assert out["conflicts"] == 1 and R.latest(rid)["inputs"]["w2s"][0]["wages"] == "52500.00"   # not overwritten
    [c] = R.conflicts(rid)
    assert (c["current_value"], c["proposed_value"], c["document_id"]) == ("52500.00", "52000.00", "d_w2a")
    R.confirm(rid, None, "maya")
    with pytest.raises(TransitionError, match="fact conflict"):
        R.submit_for_review(rid, "maya", explanation="")
    R.resolve_conflict(rid, c["id"], "keep", "maya", note="W-2c requested from the employer")
    assert R.populate_from_documents(rid, "maya")["conflicts"] == 0              # a decision is not asked again
    hist = facts.history(fam.conn, R.sealer, rid, "w2s[0].wages")
    assert [(h["source"], h["value"]) for h in hist] == [("document", "52000.00"), ("preparer", "52500.00")]
    assert hist[1]["supersedes"] == hist[0]["id"]


def test_taking_the_document_value_supersedes_the_entry(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    edited["w2s"][1]["wages"] = "40000"
    R.save_inputs(rid, edited, "maya")
    R.populate_from_documents(rid, "maya")
    [c] = R.conflicts(rid)
    with pytest.raises(ValueError):
        R.resolve_conflict(rid, c["id"], "overwrite-everything", "maya")
    R.resolve_conflict(rid, c["id"], "document", "maya", note="client confirmed the W-2 is right")
    v = R.latest(rid)
    assert v["inputs"]["w2s"][1]["wages"] == "41000" and v["provenance"]["w2s[1].wages"]["source"] == "resolution"
    assert [h["source"] for h in facts.history(fam.conn, R.sealer, rid, "w2s[1].wages")] == ["document", "preparer", "resolution"]
    with pytest.raises(KeyError):
        R.resolve_conflict(rid, c["id"], "keep", "maya")                         # resolved once


def test_a_w2_without_wages_blocks_until_a_person_decides(fam):  # noqa: F811
    add_doc(fam.conn, "d_w2c", "rivera", "W-2", {"employer_name": "Night Shift Co", "recipient_tin_last4": "0001", "box2": "300"})
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    out = R.populate_from_documents(rid, "maya")
    missing = [c for c in R.conflicts(rid) if c["anchor"].startswith("missing:")]
    assert len(missing) == 1 and missing[0]["document_id"] == "d_w2c" and out["conflicts"] >= 1
    w2 = next(w for w in R.latest(rid)["inputs"]["w2s"] if w.get("employer_name") == "Night Shift Co")
    assert "wages" not in w2                                                     # not a $0 W-2
    with pytest.raises(ValueError):
        R.resolve_conflict(rid, missing[0]["id"], "document", "maya")            # the document has nothing to take
