"""Second adversarial review of the 952ee96 re-audit fixes, amendments: start_amendment did not copy the filed return's
dispositions, so the 1040-X's first population put back the duplicate W-2 the filed return had recorded as not
applicable, and the wages were counted twice. Asserted now: the amendment carries each disposition (its note suffixed
"(carried from <rid>)"), asks about none of those documents, leaves the duplicate off, and is approved with the wages
counted once.
"""

from __future__ import annotations

import json

from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)

from agentledger import coverage
from agentledger.returns.store import Returns

LAKESIDE = {"employer_name": "Lakeside Market", "recipient_tin_last4": "0001", "box1": "52,000.00", "box2": "4100.00",
            "box3": "52000", "box4": "3224", "box5": "52000", "box6": "754", "box12_D": "3000"}
NEC_NOTE = "issued in error; the payer is sending a corrected 1099"
DUP_NOTE = "duplicate upload: phone photo of the Lakeside W-2 (d_w2a)"


def _w2_sources(R, rid):
    return [w.get("source_document") for w in R.latest(rid)["inputs"]["w2s"]]


def _paper_filed(fam):  # noqa: F811
    add_doc(fam.conn, "d_w2dup", "rivera", "W-2", LAKESIDE)      # a phone photo of the same W-2
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    edited["w2s"] = [w for w in edited["w2s"] if w.get("source_document") != "d_w2dup"]
    R.save_inputs(rid, edited, "maya")
    R.account_for_document(rid, "d_w2dup", "not_applicable", DUP_NOTE, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    R.confirm(rid, None, "maya")
    R.submit_for_review(rid, "maya")
    approved = R.approve(rid, "lee", "cpa")
    R.request_signature(rid, "lee", "cpa")
    R.record_signature(rid, "taxpayer", method="wet_signature", return_hash=approved.facts["approved_hash"])
    assert R.mark_paper_filed(rid, "lee", "cpa", "mailed by certified mail on 2027-04-10, receipt 7019 0000 0000").status == "paper_filed"
    assert R.latest(rid)["result"]["forms"]["f1040"]["1a"] == "93000"
    return R, rid


def test_an_amendment_keeps_what_the_filed_return_accounted_for(fam, monkeypatch):  # noqa: F811
    R, rid = _paper_filed(fam)
    filed = [(d["document_id"], d["disposition"], d["note"]) for d in R.dispositions(rid)]
    assert filed == [("d_w2dup", "not_applicable", DUP_NOTE), ("d_nec", "not_applicable", NEC_NOTE)]
    # Form 1040-X is "unsupported" in today's coverage registry (a blocking diagnostic at review); simulate the
    # registry in which it is supported, as test_return_workflow does for filing.
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})

    amended = R.start_amendment(rid, "lee")
    carried = [(d["document_id"], d["disposition"], d["note"]) for d in R.dispositions(amended)]
    assert carried == [(doc, how, f"{note} (carried from {rid})") for doc, how, note in filed]
    assert [(d["document_id"], d["disposition"], d["note"]) for d in R.dispositions(rid)] == filed
    assert R.unaccounted_documents(amended) == []                 # nothing the filed return accounted for is asked again

    out = R.populate_from_documents(amended, "maya")
    assert "d_w2dup" not in out["documents"] and out["conflicts"] == 0
    assert _w2_sources(R, amended) == ["d_w2a", "d_w2b"]
    R.confirm(amended, None, "maya")
    assert R.submit_for_review(amended, "maya").status == "in_review"
    assert R.approve(amended, "lee", "cpa").status == "approved"
    assert R.latest(amended)["result"]["forms"]["f1040"]["1a"] == "93000"       # the Lakeside wages counted once
