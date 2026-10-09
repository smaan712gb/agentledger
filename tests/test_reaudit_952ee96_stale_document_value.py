"""Re-audit of 952ee96, finding 2: resolving a fact conflict can apply an outdated document value. Regression tests:
each one applied an outdated value (or crashed) on 952ee96. What 952ee96 did:

A fact conflict stores the document's value once, when it is raised (returns/facts.py:238-242). Later populations
never refresh or retire it: a new proposal for an (anchor, document) pair that already has an open conflict is skipped
(facts.py:235-237), and populate_from_documents only ever raises conflicts (returns/store.py:302-312). Then
resolve_conflict(choice="document") writes that stored value without re-reading the document or checking that it is
still a live, filed document of this return's client and tax year (store.py:332-338), and records provenance saying the
document says it (store.py:336-337).

Every test asserts the safe behaviour: a "document" resolution never applies a value the documents do not say at
resolution time; a clean refusal is acceptable, and the outdated conflict is then closed as superseded.

Changed since the reproduction, on purpose: moving a filed document to another client needs a reason
(pipeline.assign), and a document is deleted under retention only after a person confirms its tax year and class and
a filing for that year is on record (evidence/records.py: retention_end); the helpers below do both.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)

from agentledger.evidence import records
from agentledger.intake.pipeline import assign
from agentledger.ledger import store
from agentledger.returns import facts
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

REFUSED = (KeyError, ValueError, PermissionError, TransitionError)   # refusing a stale conflict cleanly is safe


# --------------------------------------------------------------------------- helpers
def _return_with_conflict(fam, employer="Lakeside Market", typed="52500.00"):  # noqa: F811
    """Populate the Riveras' 2026 return, let the preparer type over one W-2's box 1, re-populate: one open conflict."""
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    next(w for w in edited["w2s"] if w["employer_name"] == employer)["wages"] = typed
    R.save_inputs(rid, edited, "maya")
    R.populate_from_documents(rid, "maya")
    [c] = R.conflicts(rid)
    return R, rid, c


def _take_document(R, rid, anchor, document_id=None):
    """The reviewer chooses "use the document's value" on the open conflict(s) for `anchor`."""
    for c in R.conflicts(rid):
        if c["anchor"] == anchor and document_id in (None, c["document_id"]):
            try:
                R.resolve_conflict(rid, c["id"], "document", "lee", note="use the document")
            except REFUSED:
                pass


def _wages(R, rid, employer):
    return Decimal(next(w for w in R.latest(rid)["inputs"]["w2s"] if w.get("employer_name") == employer)["wages"])


def _mortgage_interest(R, rid):
    return Decimal(R.latest(rid)["inputs"]["itemized"]["mortgage_interest_1098"])


def _edit(R, rid, change):
    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    change(edited)
    R.save_inputs(rid, edited, "maya")
    return edited


def _reread(conn, doc_id, **boxes):
    """The document's extracted fields change (a re-extraction or a correction). No code path rewrites
    documents.fields at 952ee96; the F-07 merge expects it ("a re-extraction", facts.py:10 and 80-83)."""
    fields = json.loads(conn.execute("SELECT fields FROM documents WHERE id = ?", (doc_id,)).fetchone()[0])
    for box, value in boxes.items():
        if value is None:
            fields.pop(box, None)
        else:
            fields[box] = value
    conn.execute("UPDATE documents SET fields = ? WHERE id = ?", (json.dumps(fields), doc_id))


def _received(conn, doc_id, day):
    """Fix the order documents are read in (ORDER BY received_at)."""
    conn.execute("UPDATE documents SET received_at = ? WHERE id = ?", (f"{day}T00:00:00+00:00", doc_id))


def _store_evidence(fam, doc_id):  # noqa: F811
    """Give a fixture document real evidence: content-addressed bytes and a version, as intake stores it (F-06)."""
    data = f"scan of {doc_id}".encode()
    loc = fam.vault.put(data)
    fam.conn.execute("UPDATE documents SET vault_path = ? WHERE id = ?", (loc, doc_id))
    records.add_version(fam.conn, doc_id, loc, hashlib.sha256(data).hexdigest(), len(data), "intake-agent")
    return loc


def reassigned_to_another_client(fam, doc_id):  # noqa: F811
    """intake.pipeline.assign (POST /api/documents/{id}/assign) re-files even an already filed document
    (intake/pipeline.py:231-245): the W-2 was another client's."""
    store.add_client(fam.conn, id="ortiz", name="Dana Ortiz", kind="individual", emails=[], domain="general")
    _store_evidence(fam, doc_id)
    assign(fam.conn, fam.vault, doc_id, "ortiz", "lee", move_reason="Dana Ortiz's document, misfiled at intake")


def moved_to_another_tax_year(fam, doc_id):  # noqa: F811
    """It is the 2025 W-2. No code path re-dates a document at 952ee96; an operator correction does."""
    fam.conn.execute("UPDATE documents SET tax_year = 2025 WHERE id = ?", (doc_id,))


def deleted_under_retention(fam, doc_id):  # noqa: F811
    """evidence.records.purge_expired (POST /api/evidence/purge) once the document's retention has ended: a CPA
    confirmed its tax year and class, the 2026 return is on record as filed, and the run happens after the end."""
    loc = _store_evidence(fam, doc_id)
    records.confirm_retention(fam.conn, doc_id, tax_year=2026, retention_class="tax_return_support", actor="lee", role="cpa",
                              note="2026 W-2, checked against the employer's copy")
    records.record_tax_event(fam.conn, "rivera", 2026, "filed", "2027-04-10", actor="lee", role="cpa", form="1040",
                             note="IRS account transcript shows the return received 2027-04-10")
    doc = fam.conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    end, why = records.retention_end(fam.conn, records.policy(), dict(doc))
    assert end is not None, why
    receipts = records.purge_expired(fam.conn, fam.vault, end + timedelta(days=1), actor="lee", role="cpa",
                                     attested=True, reason="annual retention review")
    assert [r["document_id"] for r in receipts] == [doc_id] and not fam.vault.exists(loc)     # the evidence is gone


# --------------------------------------------------------------------------- (a) the document says something else now
@pytest.mark.parametrize("box1", ["53,000.00", "52,500.00", None],
                         ids=["now-53000-reproposal-dropped", "now-agrees-with-preparer", "box1-no-longer-read"])
def test_a_reread_document_is_not_resolved_to_its_old_value(fam, box1):  # noqa: F811
    """Path (a). Conflict: the preparer typed 52,500.00 over W-2 d_w2a's box 1 of 52,000.00. The W-2 is then re-read
    and box 1 says 53,000.00, or 52,500.00, or nothing, and the preparer re-populates the return.
    * 53,000.00 is proposed again but dropped: an open conflict already exists for (anchor, document)
      (facts.py:235-237), so the stored proposal stays 52,000.00.
    * 52,500.00 (now agrees, facts.py:74-79) or no box 1 (never merged, facts.py:122-129) produce no conflict at all,
      and nothing retires the old one, which still blocks review.
    Taking the document's value then writes 52,000.00 (store.py:335), which d_w2a no longer says."""
    R, rid, c = _return_with_conflict(fam)
    assert (c["document_id"], c["proposed_value"]) == ("d_w2a", "52000.00")
    _reread(fam.conn, "d_w2a", box1=box1)
    R.populate_from_documents(rid, "maya")
    _take_document(R, rid, c["anchor"])
    assert _wages(R, rid, "Lakeside Market") != Decimal("52000"), "a value the document no longer says was applied"


# --------------------------------------------------------------------------- (b) the document is no longer this return's evidence
@pytest.mark.parametrize("change", [reassigned_to_another_client, moved_to_another_tax_year], ids=lambda f: f.__name__)
def test_a_document_that_left_the_return_is_not_applied(fam, change):  # noqa: F811
    """Path (b). While the conflict on W-2 d_w2a is open, the W-2 is reassigned to another client
    (intake/pipeline.py:231-245), re-dated to 2025, or deleted under retention (evidence/records.py:120-147).
    resolve_conflict never looks at the document again (store.py:332-338), so "document" still writes its 52,000.00
    into this client's 2026 return. Re-populating first does not help: the item is kept as
    "document_no_longer_provides", the conflict stays open, and a deleted document is even read again (next test)."""
    R, rid, c = _return_with_conflict(fam)
    change(fam, "d_w2a")
    _take_document(R, rid, c["anchor"])
    assert _wages(R, rid, "Lakeside Market") != Decimal("52000"), f"the value of a document {change.__name__} was applied"


def test_a_document_an_unfiled_return_relies_on_is_never_deleted(fam):  # noqa: F811
    """The third way a document could leave a return with a conflict open (deletion under retention) no longer
    exists: a return in preparation keeps every document it relied on, whatever filings are on record."""
    R, rid, c = _return_with_conflict(fam)
    _store_evidence(fam, "d_w2a")
    records.confirm_retention(fam.conn, "d_w2a", tax_year=2026, retention_class="tax_return_support", actor="lee", role="cpa",
                              note="2026 W-2, checked against the employer's copy")
    records.record_tax_event(fam.conn, "rivera", 2026, "filed", "2027-04-10", actor="lee", role="cpa", form="1040",
                             note="IRS account transcript shows a return received 2027-04-10")
    doc = dict(fam.conn.execute("SELECT * FROM documents WHERE id = 'd_w2a'").fetchone())
    end, why = records.retention_end(fam.conn, records.policy(), doc)
    assert end is None and f"return {rid}" in why and "not filed" in why
    assert "d_w2a" not in {d["id"] for d in records.due_for_deletion(fam.conn, date(2099, 1, 1))}


def test_a_document_deleted_under_retention_does_not_populate_a_return(fam):  # noqa: F811
    """Path (b), second root cause. documents.populate selects filed documents by client and year only
    (returns/documents.py:104-105), and purge_expired only stamps deleted_at, leaving the extracted fields in place
    (evidence/records.py:132). A document deleted under retention therefore still feeds any return populated after
    the purge (an amendment, or a late return for an old year) and is cited in its package as a source whose
    evidence no longer exists."""
    deleted_under_retention(fam, "d_w2a")
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    out = R.populate_from_documents(rid, "maya")
    assert "d_w2a" not in out["documents"], "a document deleted under retention populated the return"


# --------------------------------------------------------------------------- (c) summed amounts
def test_a_summed_amount_is_not_applied_after_another_document_leaves(fam):  # noqa: F811
    """Path (c). Mortgage interest is the sum of every 1098 but is cited to the last one read
    (returns/documents.py:117-119), so the conflict's proposed 9,900.00 includes d_1098a's 9,100.00 although the
    conflict names only d_1098b. d_1098a turns out to be another client's 1098 and is reassigned. d_1098b itself is
    unchanged, so even a check of the cited document would pass, yet "document" applies 9,900.00 while this client's
    documents now say 800.00."""
    add_doc(fam.conn, "d_1098a", "rivera", "1098", {"box1": "9,100.00"})
    add_doc(fam.conn, "d_1098b", "rivera", "1098", {"box1": "800.00"})
    _received(fam.conn, "d_1098b", "2026-03-01")
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    _edit(R, rid, lambda i: i["itemized"].update(mortgage_interest_1098="9950.00"))
    R.populate_from_documents(rid, "maya")
    [c] = R.conflicts(rid)
    assert (c["document_id"], c["proposed_value"]) == ("d_1098b", "9900.00")
    reassigned_to_another_client(fam, "d_1098a")
    _take_document(R, rid, c["anchor"])
    assert _mortgage_interest(R, rid) != Decimal("9900"), "an amount from a document that left the return was applied"


def test_a_new_document_does_not_leave_a_stale_conflict_on_a_summed_amount(fam):  # noqa: F811
    """Path (c). A second 1098 arriving changes the document the summed amount is cited to, so re-population opens a
    second conflict on the same field (open conflicts are matched by anchor and document, facts.py:235-236) instead of
    superseding the first. Taking the documents' total (9,900.00) on the new conflict and then the document's value on
    the old one puts back 9,100.00, the outdated proposal."""
    add_doc(fam.conn, "d_1098a", "rivera", "1098", {"box1": "9,100.00"})
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    _edit(R, rid, lambda i: i["itemized"].update(mortgage_interest_1098="9000.00"))
    R.populate_from_documents(rid, "maya")
    [c] = R.conflicts(rid)
    assert (c["document_id"], c["proposed_value"]) == ("d_1098a", "9100.00")
    add_doc(fam.conn, "d_1098b", "rivera", "1098", {"box1": "800.00"})
    _received(fam.conn, "d_1098b", "2026-03-01")
    R.populate_from_documents(rid, "maya")
    _take_document(R, rid, c["anchor"], "d_1098b")
    _take_document(R, rid, c["anchor"], "d_1098a")
    assert _mortgage_interest(R, rid) == Decimal("9900"), "an outdated document value replaced the documents' total"


# --------------------------------------------------------------------------- (c) where the value lands
@pytest.mark.parametrize("employer, typed", [("Lakeside Market", "52500.00"), ("City Schools", "40000")],
                         ids=["first-w2-removed-value-lands-on-the-other", "last-w2-removed-stale-path-crashes"])
def test_a_document_value_never_lands_on_another_item(fam, employer, typed):  # noqa: F811
    """Path (c), related. With a conflict open on one W-2, the preparer deletes that W-2 from the return by hand.
    Hand edits keep provenance keyed by list position (facts.py:180-188, via store.py:288-289), so the remaining W-2
    inherits the removed one's document; facts.locate trusts it (facts.py:269-278) and "document" writes Lakeside's
    52,000.00 into City Schools' W-2. When no item matches, resolve_conflict falls back to the position stored when
    the conflict was raised (store.py:334), which no longer exists: IndexError in facts.set_path (facts.py:286), an
    HTTP 500. Either way the W-2 that stays must not change."""
    _received(fam.conn, "d_w2b", "2026-02-02")                    # read order: Lakeside Market, then City Schools
    R, rid, c = _return_with_conflict(fam, employer, typed)
    edited = _edit(R, rid, lambda i: i.update(w2s=[w for w in i["w2s"] if w["employer_name"] != employer]))
    _take_document(R, rid, c["anchor"])
    assert R.latest(rid)["inputs"]["w2s"] == edited["w2s"], "the document's value was written into another W-2"


# --------------------------------------------------------------------------- the outdated conflict is retired, not left open
def test_a_reread_document_supersedes_the_conflict_and_raises_its_new_value(fam):  # noqa: F811
    """Re-population closes the conflict whose proposal the document no longer makes (superseded) and opens one for
    what it says now; taking the document's value then applies the new value, and the history shows each step."""
    R, rid, c = _return_with_conflict(fam)
    _reread(fam.conn, "d_w2a", box1="53,000.00")
    out = R.populate_from_documents(rid, "maya")
    assert out["superseded"] == 1
    [new] = R.conflicts(rid)
    assert (new["anchor"], new["proposed_value"]) == (c["anchor"], "53000.00") and new["id"] != c["id"]
    closed = fam.conn.execute("SELECT resolution FROM fact_conflicts WHERE id = ?", (c["id"],)).fetchone()
    assert closed["resolution"] == "superseded"
    R.resolve_conflict(rid, new["id"], "document", "lee", note="the corrected W-2 is right")
    assert _wages(R, rid, "Lakeside Market") == Decimal("53000")
    assert [h["source"] for h in facts.history(fam.conn, R.sealer, rid, c["anchor"])] == ["document", "preparer", "resolution"]


def test_a_document_that_agrees_now_retires_the_conflict(fam):  # noqa: F811
    """The document is re-read and now agrees with the preparer: nothing is left to decide, so the open conflict is
    closed as superseded and no longer blocks review."""
    R, rid, c = _return_with_conflict(fam)
    _reread(fam.conn, "d_w2a", box1="52,500.00")
    assert R.populate_from_documents(rid, "maya")["superseded"] == 1
    assert R.conflicts(rid) == []
    assert _wages(R, rid, "Lakeside Market") == Decimal("52500")


def test_taking_a_value_that_changed_since_the_conflict_is_refused_and_re_raised(fam):  # noqa: F811
    """No re-population in between: the reviewer takes the document's value after the document changed. The value is
    re-read at resolution time, so the request is refused, the conflict superseded, and a new one shows the value the
    document gives now."""
    R, rid, c = _return_with_conflict(fam)
    _reread(fam.conn, "d_w2a", box1="53,000.00")
    with pytest.raises(ValueError, match="now say 53000.00"):
        R.resolve_conflict(rid, c["id"], "document", "lee", note="use the document")
    [new] = R.conflicts(rid)
    assert new["proposed_value"] == "53000.00" and _wages(R, rid, "Lakeside Market") == Decimal("52500")


def test_a_document_that_left_the_return_turns_its_item_into_a_blocking_question(fam):  # noqa: F811
    """A W-2 moved to another client while its item stays on the return: re-population keeps the item (nothing is
    silently removed) and asks a person (an orphan conflict that blocks review). Only 'keep' with a reason, or removing
    the item, answers it; 'document' is refused."""
    R, rid, _ = _return_with_conflict(fam)
    reassigned_to_another_client(fam, "d_w2a")
    R.populate_from_documents(rid, "maya")
    [orphan] = [x for x in R.conflicts(rid) if x["anchor"].startswith("orphan:")]
    assert orphan["anchor"] == "orphan:w2s[d_w2a]"
    with pytest.raises(ValueError, match="no longer belongs"):
        R.resolve_conflict(rid, orphan["id"], "document", "lee")
    with pytest.raises(ValueError, match="say why"):
        R.resolve_conflict(rid, orphan["id"], "keep", "lee", note="ok")
    R.confirm(rid, None, "maya")
    with pytest.raises(TransitionError, match="whose document left the return"):
        R.submit_for_review(rid, "maya")


def test_a_held_document_cannot_be_moved_to_another_client(fam):  # noqa: F811
    """A legal hold names the client: moving one of its documents away would take it out of the hold."""
    store.add_client(fam.conn, id="ortiz", name="Dana Ortiz", kind="individual", emails=[], domain="general")
    records.place_hold(fam.conn, client_id="rivera", reason="IRS examination letter dated 2026-09-30", actor="lee", role="cpa")
    with pytest.raises(ValueError, match="legal hold"):
        assign(fam.conn, fam.vault, "d_w2a", "ortiz", "lee", move_reason="Dana Ortiz's document, misfiled at intake")
    assert fam.conn.execute("SELECT client_id FROM documents WHERE id = 'd_w2a'").fetchone()["client_id"] == "rivera"
    with pytest.raises(ValueError, match="needs a reason"):
        assign(fam.conn, fam.vault, "d_w2b", "ortiz", "lee")
