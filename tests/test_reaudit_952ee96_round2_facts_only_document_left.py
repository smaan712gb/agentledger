"""Second adversarial review of the 952ee96 re-audit fixes, the only document of its kind leaving the return: a list the
population no longer provides at all (a single filer's only W-2, moved or re-dated) and an amount summed from documents
(1098 mortgage interest and premiums, 1098-E student loan interest) were never checked for a departed document, so the
return was approved with another taxpayer's or year's amounts. Asserted now: every gate refuses it for that reason,
before re-population ("re-populate, then remove each one") and after it (one `orphan:` conflict per item or amount);
nothing is dropped silently, and once a person removes it, or keeps it with a reason, the return goes through.
"""

from __future__ import annotations

import json

import pytest
from test_reaudit_952ee96_stale_document_value import reassigned_to_another_client
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)

from agentledger.evidence import records
from agentledger.ledger import store
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

NEC_NOTE = "issued in error; the payer is sending a corrected 1099"
JORDAN = {"tax_year": 2026, "filing_status": "single",
          "taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0009", "dob": "1990-01-01"}}
LEFT = "whose document left the return (moved, re-dated or deleted)"
BEFORE = f"{LEFT}: re-populate, then remove each one"             # the dry run of re-population, at every gate
AFTER = f"{LEFT}: remove each one or keep it with a reason"        # the orphan conflicts re-population raised
KEEP_NOTE = "the client confirms this amount is theirs; the issuer's copy is on file"


def _moved(fam, doc):  # noqa: F811
    reassigned_to_another_client(fam, doc)               # pipeline.assign: "Dana Ortiz's document, misfiled at intake"


def _redated(fam, doc):  # noqa: F811
    records.confirm_retention(fam.conn, doc, tax_year=2025, retention_class="tax_return_support", actor="lee",
                              role="cpa", note="this is the 2025 form, checked against the issuer's copy")


LEAVE = pytest.mark.parametrize("leave", [_moved, _redated], ids=["moved-to-another-client", "re-dated-to-2025"])
DECIDE = pytest.mark.parametrize("decision", ["remove", "keep"])


def _refused_for(R, rid, reason):
    with pytest.raises(TransitionError) as e:
        R.submit_for_review(rid, "maya")
    assert reason in str(e.value), f"review was refused, but not for the reason under test: {e.value}"
    assert R.status(rid).status == "preparing"


def _keep_orphans(R, rid):
    for c in R.conflicts(rid):
        with pytest.raises(ValueError, match="that document no longer belongs to this return"):
            R.resolve_conflict(rid, c["id"], "document", "lee", note=KEEP_NOTE)
        with pytest.raises(ValueError, match="say why the item stays"):
            R.resolve_conflict(rid, c["id"], "keep", "lee", note="keep")
        R.resolve_conflict(rid, c["id"], "keep", "lee", note=KEEP_NOTE)


def _goes_through(R, rid):
    R.populate_from_documents(rid, "maya")
    assert R.conflicts(rid) == []                                    # nothing is asked again
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"


@LEAVE
@DECIDE
def test_a_single_filers_only_w2_that_left_blocks_every_gate(fam, leave, decision):  # noqa: F811
    store.add_client(fam.conn, id="jordan", name="Jordan Lee", kind="individual", emails=[], tax_id_last4="0009",
                     domain="general", facts={"taxpayer_ssn_last4": "0009", "taxpayer_name": "Jordan Lee"})
    add_doc(fam.conn, "d_w2j", "jordan", "W-2", {"employer_name": "Night Shift Co", "recipient_tin_last4": "0009",
                                                 "box1": "60,000.00", "box2": "9,000.00", "box3": "60,000.00",
                                                 "box4": "3,720.00", "box5": "60,000.00", "box6": "870.00"})
    R = Returns(fam.conn, fam.kb)
    rid = R.create("jordan", 2026, "maya", JORDAN)
    R.populate_from_documents(rid, "maya")
    R.confirm(rid, None, "maya")
    leave(fam, "d_w2j")                                   # after the last population
    _refused_for(R, rid, f"1 item(s) {BEFORE}")

    R.populate_from_documents(rid, "maya")
    assert [c["anchor"] for c in R.conflicts(rid)] == ["orphan:w2s[d_w2j]"]
    assert [w["source_document"] for w in R.latest(rid)["inputs"]["w2s"]] == ["d_w2j"]      # never dropped silently
    _refused_for(R, rid, f"1 item(s) {AFTER}")

    if decision == "remove":
        edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
        edited["w2s"] = []
        R.save_inputs(rid, edited, "maya")
    else:
        _keep_orphans(R, rid)
    _goes_through(R, rid)
    v = R.latest(rid)
    assert [w.get("source_document") for w in v["inputs"]["w2s"]] == ([] if decision == "remove" else ["d_w2j"])
    assert v["result"]["forms"]["f1040"]["1a"] == ("0" if decision == "remove" else "60000")


CASES = [("1098", {"box1": "36,400.00", "box5": "1,200.00"}, "itemized", ("mortgage_interest_1098", "mortgage_insurance_premiums")),
         ("1098-E", {"box1": "2,500.00"}, "adjustments", ("student_loan_interest_paid",))]


@LEAVE
@DECIDE
@pytest.mark.parametrize("doc_type,fields,group,amounts", CASES, ids=[c[0] for c in CASES])
def test_a_summed_amount_whose_document_left_blocks_every_gate(fam, doc_type, fields, group, amounts, leave, decision):  # noqa: F811
    add_doc(fam.conn, "d_sum", "rivera", doc_type, fields)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    R.confirm(rid, None, "maya")
    entered = {f: R.latest(rid)["inputs"][group][f] for f in amounts}
    leave(fam, "d_sum")                                   # after the last population
    _refused_for(R, rid, f"{len(amounts)} item(s) {BEFORE}")

    R.populate_from_documents(rid, "maya")
    assert sorted(c["anchor"] for c in R.conflicts(rid)) == sorted(f"orphan:{group}.{f}" for f in amounts)
    assert {f: R.latest(rid)["inputs"][group][f] for f in amounts} == entered              # never dropped silently
    _refused_for(R, rid, f"{len(amounts)} item(s) {AFTER}")

    if decision == "remove":
        edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
        edited[group] = {k: v for k, v in edited[group].items() if k not in amounts}
        R.save_inputs(rid, edited, "maya")
    else:
        _keep_orphans(R, rid)
    _goes_through(R, rid)
    kept = {f: v for f, v in (R.latest(rid)["inputs"].get(group) or {}).items() if f in amounts}
    assert kept == ({} if decision == "remove" else entered)
