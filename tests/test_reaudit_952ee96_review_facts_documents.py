"""Review of the 952ee96 re-audit fixes, the documents behind a return. The first adversarial review found that a document
a person accounted for ("not applicable", "entered by hand") was put back on the return by the next population, and
an unreadable W-2 that did not apply could not be dismissed; that a document leaving the return (moved to another
client, re-dated) after the last population went unnoticed through review, approval, signature and transmission; and
that a filed W-2 with no known tax year was invisible to the "filed documents not on the return" gate. Asserted now:
population never puts back a document accounted for on the return; the leftover question about it is closed; every
gate refuses a return whose item's document left, for that reason; a yearless filed W-2 blocks review by name until a
person accounts for it.
"""

from __future__ import annotations

import json

import pytest
from test_reaudit_952ee96_stale_document_value import reassigned_to_another_client
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)

from agentledger import coverage
from agentledger.evidence import records
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

LAKESIDE = {"employer_name": "Lakeside Market", "recipient_tin_last4": "0001", "box1": "52,000.00", "box2": "4100.00",
            "box3": "52000", "box4": "3224", "box5": "52000", "box6": "754", "box12_D": "3000"}
NEC_NOTE = "issued in error; the payer is sending a corrected 1099"
LEFT = "whose document left the return"


def _inputs(R, rid):
    return json.loads(json.dumps(R.latest(rid)["inputs"]))


def _w2_sources(R, rid):
    return [w.get("source_document") for w in R.latest(rid)["inputs"]["w2s"]]


def _without(R, rid, doc):
    edited = _inputs(R, rid)
    edited["w2s"] = [w for w in edited["w2s"] if w.get("source_document") != doc]
    R.save_inputs(rid, edited, "maya")


# --------------------------------------------------------------------------- dispositions are respected
@pytest.mark.parametrize("disposition", ["not_applicable", "entered_by_hand"])
def test_a_document_accounted_for_is_not_put_back_by_the_next_population(fam, disposition):  # noqa: F811
    add_doc(fam.conn, "d_w2dup", "rivera", "W-2", LAKESIDE)      # the client uploaded a phone photo of the same W-2
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    _without(R, rid, "d_w2dup")
    assert R.unaccounted_documents(rid) == ["d_nec", "d_w2dup"]
    R.account_for_document(rid, "d_w2dup", disposition, "duplicate upload: phone photo of the Lakeside W-2 (d_w2a)", "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")

    add_doc(fam.conn, "d_1098", "rivera", "1098", {"box1": "9,100.00"})   # later: another document arrives
    out = R.populate_from_documents(rid, "maya")
    assert "d_w2dup" not in out["documents"] and "d_1098" in out["documents"]
    assert _w2_sources(R, rid) == ["d_w2a", "d_w2b"]
    R.confirm(rid, None, "maya")                                  # the routine "confirm all"
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"
    assert R.latest(rid)["result"]["forms"]["f1040"]["1a"] == "93000"      # the Lakeside W-2 counted once


def test_a_document_still_on_the_return_cannot_be_accounted_for(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    with pytest.raises(ValueError, match="this document is on the return"):
        R.account_for_document(rid, "d_w2b", "not_applicable", "the spouse's W-2 is filed separately this year", "maya")
    assert R.dispositions(rid) == [] and _w2_sources(R, rid) == ["d_w2a", "d_w2b"]


def test_an_unreadable_w2_that_does_not_apply_can_be_dismissed(fam):  # noqa: F811
    add_doc(fam.conn, "d_w2o", "rivera", "W-2", {"employer_name": "Other Person's Employer", "recipient_tin_last4": "7777",
                                                 "box1": "4O,OOO.OO", "box2": "2,000.00"})
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    [c] = [c for c in R.conflicts(rid) if c["anchor"] == "missing:w2s[d_w2o].wages"]
    _without(R, rid, "d_w2o")
    R.account_for_document(rid, "d_w2o", "not_applicable", "W-2 of the client's son (TIN ...7777), misfiled to this client", "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    with pytest.raises(ValueError, match="still missing"):        # nothing to keep: the item is gone
        R.resolve_conflict(rid, c["id"], "keep", "maya", note="the W-2 does not apply to this return")
    with pytest.raises(ValueError, match="the document has no value here"):
        R.resolve_conflict(rid, c["id"], "document", "maya", note="the W-2 does not apply to this return")

    R.populate_from_documents(rid, "maya")                        # the question about it is closed, the W-2 stays off
    assert R.conflicts(rid) == [] and _w2_sources(R, rid) == ["d_w2a", "d_w2b"]
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"


# --------------------------------------------------------------------------- a document that left the return
def _moved(fam):  # noqa: F811
    reassigned_to_another_client(fam, "d_w2b")            # pipeline.assign with a reason, as POST /api/documents/{id}/assign


def _redated(fam):  # noqa: F811
    records.confirm_retention(fam.conn, "d_w2b", tax_year=2025, retention_class="tax_return_support", actor="lee",
                              role="cpa", note="this is the 2025 W-2, checked against the employer's copy")


def _ready(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    # Once the spouse's W-2 leaves, the household's AGI is under the saver's credit limit while Alex's W-2 carries a
    # 401(k) deferral, so Form 8880's facts are stated here (they have nothing to do with the document question).
    base = household()
    savers = {"taxpayer": {**base["taxpayer"], "full_time_student": False}, "spouse": {**base["spouse"], "full_time_student": False},
              "retirement_savings": [{"owner": o, "testing_period_distributions": "0"} for o in ("taxpayer", "spouse")]}
    rid = R.create("rivera", 2026, "maya", {**base, **savers})
    R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    R.confirm(rid, None, "maya")
    return R, rid


@pytest.mark.parametrize("leave", [_moved, _redated], ids=["moved-to-another-client", "re-dated-to-2025"])
def test_review_is_refused_when_an_item_s_document_left_the_return(fam, leave):  # noqa: F811
    R, rid = _ready(fam)
    leave(fam)
    with pytest.raises(TransitionError, match=LEFT):
        R.submit_for_review(rid, "maya")
    assert R.status(rid).status == "preparing" and _w2_sources(R, rid) == ["d_w2a", "d_w2b"]

    R.populate_from_documents(rid, "maya")                        # the question is asked; the item is never dropped
    assert [c["anchor"] for c in R.conflicts(rid)] == ["orphan:w2s[d_w2b]"]
    assert _w2_sources(R, rid) == ["d_w2a", "d_w2b"]
    _without(R, rid, "d_w2b")                                     # a person removes it
    R.populate_from_documents(rid, "maya")
    assert R.conflicts(rid) == []
    assert R.submit_for_review(rid, "maya").status == "in_review"


def _filing_approved(monkeypatch):
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})


def test_signature_is_refused_when_a_document_left_after_approval(fam, monkeypatch):  # noqa: F811
    R, rid = _ready(fam)
    R.submit_for_review(rid, "maya")
    assert R.approve(rid, "lee", "cpa").status == "approved"
    _moved(fam)                                                   # after approval
    _filing_approved(monkeypatch)
    with pytest.raises(TransitionError, match=LEFT):
        R.request_signature(rid, "lee", "cpa")
    assert R.status(rid).status == "approved"


@pytest.mark.parametrize("filing", ["transmit", "paper"])
def test_filing_is_refused_when_a_document_left_after_signature(fam, monkeypatch, filing):  # noqa: F811
    R, rid = _ready(fam)
    R.submit_for_review(rid, "maya")
    approved = R.approve(rid, "lee", "cpa")
    _filing_approved(monkeypatch)
    R.request_signature(rid, "lee", "cpa")
    R.record_signature(rid, "taxpayer", method="kba_esign", return_hash=approved.facts["approved_hash"], kba_transaction_id="kba-1")
    _moved(fam)                                                   # after the taxpayer signed
    with pytest.raises(TransitionError, match=LEFT):
        if filing == "transmit":
            R.transmit(rid, "lee", "cpa", efile_ready=True, submit=lambda key: {"submission_id": "00000020263000000009"})
        else:
            R.mark_paper_filed(rid, "lee", "cpa", "mailed by certified mail on 2027-04-10, receipt 7019 0000 0000")
    assert R.status(rid).status == "signed" and _w2_sources(R, rid) == ["d_w2a", "d_w2b"]


# --------------------------------------------------------------------------- a filed W-2 without a tax year
def test_a_filed_w2_without_a_tax_year_blocks_the_clients_returns(fam):  # noqa: F811
    add_doc(fam.conn, "d_w2y", "rivera", "W-2", {"employer_name": "Harbor Freight Lines", "recipient_tin_last4": "0001",
                                                 "box1": "38,000.00", "box2": "3,100.00"}, year=None)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    R.confirm(rid, None, "maya")
    assert R.unaccounted_documents(rid) == ["d_w2y"]
    with pytest.raises(TransitionError, match=r"not on the return \(d_w2y\)"):
        R.submit_for_review(rid, "maya")
    assert R.status(rid).status == "preparing"

    R.account_for_document(rid, "d_w2y", "not_applicable", "the 2025 W-2 from Harbor Freight Lines, year printed on it", "maya")
    assert R.unaccounted_documents(rid) == []
    assert R.submit_for_review(rid, "maya").status == "in_review"
