"""Second adversarial review of the 952ee96 re-audit fixes, paper filing after an e-file attempt: a signed return whose
transmission had started (no answer came, or the transmitter received it but the transition never happened) could be
marked filed on paper, two filings of one return. Asserted now: paper filing and void are refused for that reason
while an "activity_started:transmit" is not followed by "reconciled_not_submitted"; an attempt never received is
reconciled first, then filed on paper; one received is recorded as transmitted on retry, without sending again.
"""

from __future__ import annotations

import pytest
from test_return_workflow import fam, household  # noqa: F401  (fam is a fixture)

from agentledger import coverage
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError, UncertainOutcome

NEC_NOTE = "issued in error; the payer is sending a corrected 1099"
FILED_NOTE = "mailed by certified mail on 2027-04-10, receipt 7019 0000 0000"
STARTED = "an electronic transmission of this return was started: reconcile it with the transmitter first"
RECEIVED = {"submission_id": "00000020263000000042"}


def _signed(fam, monkeypatch):  # noqa: F811
    monkeypatch.setattr(coverage, "lookup", lambda cap, year, jurisdiction="US-FED", path=None: {"id": cap, "status": "filing-approved"})
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    R.confirm(rid, None, "maya")
    R.submit_for_review(rid, "maya")
    h = R.approve(rid, "lee", "cpa").facts["approved_hash"]
    R.request_signature(rid, "lee", "cpa")
    R.record_signature(rid, "taxpayer", method="kba_esign", return_hash=h, kba_transaction_id="kba-1")
    return R, rid, h


def _timed_out(key):
    raise TimeoutError("the transmitter did not answer")


def _never_sent_again(key):
    pytest.fail("the return was sent to the transmitter a second time")


def _attempt(R, rid, h, attempt):
    if attempt == "outcome-unknown":                     # the request left; the answer never came
        with pytest.raises(TimeoutError):
            R.transmit(rid, "lee", "cpa", efile_ready=True, submit=_timed_out)
    else:                                                # received (submission id on record); crash before the transition
        R.wf.activity(rid, "transmit", h, lambda: dict(RECEIVED), actor="lee")
    assert R.status(rid).status == "signed"


@pytest.mark.parametrize("attempt", ["outcome-unknown", "sent-not-recorded"])
def test_a_return_sent_electronically_is_not_marked_filed_on_paper(fam, monkeypatch, attempt):  # noqa: F811
    R, rid, h = _signed(fam, monkeypatch)
    _attempt(R, rid, h, attempt)
    with pytest.raises(TransitionError, match=STARTED):
        R.mark_paper_filed(rid, "lee", "cpa", FILED_NOTE)
    with pytest.raises(TransitionError, match=STARTED):
        R.void(rid, "lee", "cpa", "filed with other software on 2027-04-10, transcript on file")
    st = R.status(rid)
    assert st.status == "signed" and "mark_paper_filed" not in [e["event"] for e in st.history]


def test_an_attempt_never_received_is_reconciled_before_the_paper_filing(fam, monkeypatch):  # noqa: F811
    R, rid, h = _signed(fam, monkeypatch)
    _attempt(R, rid, h, "outcome-unknown")
    with pytest.raises(UncertainOutcome):                # never blindly resent: a person reconciles it
        R.transmit(rid, "lee", "cpa", efile_ready=True, submit=_never_sent_again)
    assert R.status(rid).status == "unknown"
    with pytest.raises(TransitionError, match="is not allowed while the workflow is 'unknown'"):
        R.mark_paper_filed(rid, "lee", "cpa", FILED_NOTE)
    assert R.reconcile_transmission(rid, "lee", "cpa", submitted=False,
                                    evidence="the transmitter has no submission for the idempotency key").status == "signed"
    assert R.mark_paper_filed(rid, "lee", "cpa", FILED_NOTE).status == "paper_filed"


def test_an_attempt_the_transmitter_received_is_recorded_as_transmitted(fam, monkeypatch):  # noqa: F811
    R, rid, h = _signed(fam, monkeypatch)
    _attempt(R, rid, h, "sent-not-recorded")
    st = R.transmit(rid, "lee", "cpa", efile_ready=True, submit=_never_sent_again)
    assert st.status == "transmitted" and st.facts["submission_id"] == RECEIVED["submission_id"]
    with pytest.raises(TransitionError, match="is not allowed while the workflow is 'transmitted'"):
        R.mark_paper_filed(rid, "lee", "cpa", FILED_NOTE)
