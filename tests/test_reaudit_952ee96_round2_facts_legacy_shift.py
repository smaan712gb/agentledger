"""Second adversarial review of the 952ee96 re-audit fixes, legacy items (stored before identities were carried in the
data): a legacy item kept its document only at the same position with the same name, so removing an earlier item or
adding one before it made it a hand entry and the next population counted its document twice. Asserted now: a legacy
item keeps its document when matched by name and the values its document sourced, wherever it moved (else by position,
for an in-place edit), so population neither asks about it nor duplicates it and each document is counted once.
"""

from __future__ import annotations

import json

from test_return_workflow import fam, household  # noqa: F401  (fam is a fixture)

from agentledger.returns.store import Returns

NEC_NOTE = "issued in error; the payer is sending a corrected 1099"


def _legacy_return(R):
    """The Riveras' return as 952ee96 stored it: populated and confirmed, items without source_document."""
    scratch = R.create("rivera", 2026, "maya", household(), form="1040-SCRATCH")
    R.populate_from_documents(scratch, "maya")
    R.confirm(scratch, None, "maya")
    v = R.latest(scratch)
    inputs = json.loads(json.dumps(v["inputs"]))
    for key in ("w2s", "interest"):
        for item in inputs.get(key, []):
            item.pop("source_document", None)
    rid = R.create("rivera", 2026, "maya", inputs, provenance=json.loads(json.dumps(v["provenance"])), form="1040")
    R.compute(rid, "maya")
    assert R.unaccounted_documents(rid) == ["d_nec"]                 # legacy items are recognised by provenance
    return rid


def _edit(R, rid, change):
    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    change(edited)
    R.save_inputs(rid, edited, "maya")


def _items(R, rid, key, name):
    return [(i.get(name), i.get("wages") or i.get("interest"), i.get("source_document")) for i in R.latest(rid)["inputs"][key]]


def _approved(R, rid):
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"
    return R.latest(rid)["result"]["forms"]["f1040"]


def test_removing_an_earlier_legacy_w2_keeps_the_later_one_tied_to_its_document(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = _legacy_return(R)
    _edit(R, rid, lambda e: e.update(w2s=e["w2s"][1:]))             # the Lakeside W-2 (d_w2a) was issued in error
    assert _items(R, rid, "w2s", "employer_name") == [("City Schools", "41000", "d_w2b")]
    prov = R.latest(rid)["provenance"]
    assert prov["w2s[0].wages"]["document_id"] == "d_w2b" and not any(p.get("document_id") == "d_w2a" for p in prov.values())
    R.account_for_document(rid, "d_w2a", "not_applicable", "Lakeside Market issued this W-2 in error; W-2c shows no wages", "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    assert R.unaccounted_documents(rid) == []

    out = R.populate_from_documents(rid, "maya")
    assert out["conflicts"] == 0 and out["fields"] == 0
    assert _items(R, rid, "w2s", "employer_name") == [("City Schools", "41000", "d_w2b")]
    assert _approved(R, rid)["1a"] == "41000"                       # City Schools' wages counted once


def test_adding_an_account_before_a_legacy_1099int_keeps_it_tied_to_its_document(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = _legacy_return(R)
    harbor = {"owner": "taxpayer", "payer": "Harbor Credit Union", "interest": "50.00"}
    _edit(R, rid, lambda e: e.update(interest=[harbor] + e["interest"]))   # a second account, by hand, listed first
    expected = [("Harbor Credit Union", "50.00", None), ("First Bank", "312.40", "d_int")]
    assert _items(R, rid, "interest", "payer") == expected
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    assert R.unaccounted_documents(rid) == []

    out = R.populate_from_documents(rid, "maya")
    assert out["conflicts"] == 0 and out["fields"] == 0
    assert _items(R, rid, "interest", "payer") == expected
    assert _approved(R, rid)["2b"] == "362"                         # 312.40 + 50.00, First Bank counted once


def test_an_in_place_edit_of_a_legacy_w2_keeps_it_by_position(fam):  # noqa: F811
    """Its sourced values changed, so they no longer identify it: the same name at the same position does, and the
    edit is the preparer's value, which the next population raises as a conflict instead of adding the W-2 again."""
    R = Returns(fam.conn, fam.kb)
    rid = _legacy_return(R)
    _edit(R, rid, lambda e: e["w2s"][1].update(wages="43000"))       # City Schools: the W-2c shows 43,000
    assert _items(R, rid, "w2s", "employer_name") == [("Lakeside Market", "52000.00", "d_w2a"), ("City Schools", "43000", "d_w2b")]
    R.populate_from_documents(rid, "maya")
    assert [w.get("source_document") for w in R.latest(rid)["inputs"]["w2s"]] == ["d_w2a", "d_w2b"]
    [c] = R.conflicts(rid)
    assert (c["anchor"], c["current_value"], c["proposed_value"]) == ("w2s[d_w2b].wages", "43000", "41000")
