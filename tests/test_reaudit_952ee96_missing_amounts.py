"""Re-audit of 952ee96, finding 1: missing W-2 amounts can pass review. Regression tests: each path below passed review
on 952ee96.

F-07 promises that "a missing required amount is a blocking item, never zero" (docs/inception/backlog.md F-07; the
facts.py docstring: "an amount a document should carry but does not is reported as missing (it is never taken as
zero)"; facts.REQUIRED: "a W-2 without wages is not a $0 W-2"). The input model, however, defaults every amount to zero
(model.W2.wages = Z, model.py:60), so a W-2 whose `wages` key is absent validates and is computed as a $0 W-2 whose
withholding is still credited (individual.py:237 line 1a, :1415 line 25a). The only guard is the `missing:` fact
conflict that populate_from_documents raises, and nothing else checks it again. These paths get around that guard:

  (a)  the missing-amount conflict is resolved with "keep" while the wages are still empty;
  (a*) the same for every other REQUIRED form (1099-INT, 1099-DIV, 1099-R, SSA-1099, 1099-G);
  (b)  a W-2 entered by hand without wages (save_inputs = PUT /inputs, or create = POST /returns) is never flagged;
  (b') a hand edit that removes a document W-2's wages becomes a confirmed preparer value and is never re-checked;
  (c)  a "keep" exempts that amount from every later population, even after the amount is cleared again;
  (d)  approve re-checks nothing but segregation and the package hash, so such a return is approved as well
       (also shown over the production HTTP API, with a separate preparer and reviewer);
  (e)  a W-2 document none of whose amounts can be read is left off the return with a non-blocking note;
  (f)  related: review never reconciles the return with the client's filed W-2 documents.

Each test asserts the safe behaviour. Where a fix could reasonably refuse an earlier step instead (keeping an empty
amount, saving a W-2 without wages), that refusal is accepted as the safe outcome. A refused review must name the
cause under test (the missing amount; for (f) the documents not on the return), so another blocker, such as the
fixture's unused 1099-NEC, can never make a test pass.
"""

from __future__ import annotations

import json

import pytest
from test_engagements import firm  # noqa: F401  (fixture)
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)
from test_tenancy import api  # noqa: F401  (fixture that firm depends on)

from agentledger.ledger import store
from agentledger.returns import facts
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

# A W-2 on which box 1 could not be read: only the federal income tax withheld (box 2) is on the document.
NIGHT_SHIFT = {"employer_name": "Night Shift Co", "recipient_tin_last4": "0001", "box2": "300"}
JORDAN = {"tax_year": 2026, "filing_status": "single",
          "taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0009", "dob": "1990-01-01"}}


@pytest.fixture
def solo(foundry):
    """A single filer with no documents on file, so only the hand entry is in play."""
    store.add_client(foundry.conn, id="jordan", name="Jordan Lee", kind="individual", emails=[], tax_id_last4="0009",
                     domain="general", facts={"taxpayer_ssn_last4": "0009", "taxpayer_name": "Jordan Lee"})
    return foundry


def _evidence(R, rid, lst="w2s", fld="wages"):
    v = R.latest(rid)
    res = v["result"] or {}
    f1040 = (res.get("forms") or {}).get("f1040") or {}
    s = res.get("summary") or {}
    items = [(x.get("employer_name") or x.get("payer") or x.get("owner"), x.get(fld, "<absent>")) for x in v["inputs"].get(lst, [])]
    return (f"{lst} (name, {fld}): {items}; Form 1040 line 1a (W-2 wages) = {f1040.get('1a', '0')}, line 25a (W-2 "
            f"withholding) = {f1040.get('25a', '0')}; AGI {s.get('agi')}, payments {s.get('payments')}, refund {s.get('refund')}")


def _must_not_reach_review(R, rid, path, lst="w2s", fld="wages", actor="maya", expect="lack a required amount"):
    """The safe outcome: submitting for review is refused, for the reason under test, while an item lacks its required
    amount."""
    try:
        st = R.submit_for_review(rid, actor)
    except TransitionError as e:
        assert R.status(rid).status == "preparing"
        assert expect in str(e), f"path {path}: review was refused, but not for the reason under test: {e}"
        return
    pytest.fail(f"path {path}: an item without its required amount reached '{st.status}'. {_evidence(R, rid, lst, fld)}")


def _missing(R, rid, anchor):
    found = [c for c in R.conflicts(rid) if c["anchor"] == anchor]
    assert len(found) == 1, f"expected one open conflict {anchor}, got {[c['anchor'] for c in R.conflicts(rid)]}"
    return found[0]


# --------------------------------------------------------------------------------------------- (a) keep while empty
def test_a_keep_on_a_missing_amount_while_the_wages_are_still_empty(fam):  # noqa: F811
    """Path (a). store.resolve_conflict (store.py:322-343) closes a `missing:` conflict with "keep" without checking
    that the amount was entered: only "document" is refused (store.py:330-331, "enter the amount, then keep it"), and
    "keep" never looks at the field (and facts.locate could not find it: it returns None for every missing anchor,
    facts.py:271-272). _review_context (store.py:433-442) then counts no open conflict and has no required-amount check
    of its own, and _g_review (store.py:103-115) lets the return into review with the W-2 computed as $0 wages while its
    $300 withholding is credited."""
    add_doc(fam.conn, "d_w2c", "rivera", "W-2", NIGHT_SHIFT)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    missing = _missing(R, rid, "missing:w2s[d_w2c].wages")
    try:
        R.resolve_conflict(rid, missing["id"], "keep", "maya", note="the employer will send a W-2c")
    except ValueError:
        pass                     # a fix may refuse to keep an amount that is still empty: the conflict then stays open
    R.confirm(rid, None, "maya")
    _must_not_reach_review(R, rid, "(a)")


OTHER_FORMS = [  # (document type, input list, REQUIRED field, fields with only the withholding readable)
    ("1099-INT", "interest", "interest", {"payer_name": "Harbor Bank", "recipient_tin_last4": "0001", "box4": "50"}),
    ("1099-DIV", "dividends", "ordinary", {"payer_name": "Index Fund", "recipient_tin_last4": "0001", "box4": "50"}),
    ("1099-R", "retirement", "gross_distribution", {"payer_name": "Pension Plan", "recipient_tin_last4": "0001", "box4": "50"}),
    ("SSA-1099", "social_security", "net_benefits", {"recipient_tin_last4": "0001", "box6": "50"}),
    ("1099-G", "unemployment", "amount", {"recipient_tin_last4": "0001", "box4": "50"}),
]


@pytest.mark.parametrize("doc_type,lst,fld,fields", OTHER_FORMS, ids=[f[0] for f in OTHER_FORMS])
def test_a_star_the_same_for_every_required_form(fam, doc_type, lst, fld, fields):  # noqa: F811
    """Path (a*). The same resolve_conflict / _review_context gap for every entry of facts.REQUIRED (facts.py:27-28):
    each of these amounts also defaults to zero in the model (model.py:86, 98, 131, 143, 149)."""
    add_doc(fam.conn, "d_x", "rivera", doc_type, fields)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    missing = _missing(R, rid, f"missing:{lst}[d_x].{fld}")
    try:
        R.resolve_conflict(rid, missing["id"], "keep", "maya")
    except ValueError:
        pass
    R.confirm(rid, None, "maya")
    _must_not_reach_review(R, rid, f"(a*) {doc_type}", lst, fld)


# --------------------------------------------------------------------------------------------- (b) entered by hand
@pytest.mark.parametrize("entry", ["save_inputs", "create"])
def test_b_a_w2_entered_by_hand_without_wages(solo, entry):
    """Path (b). facts.REQUIRED is enforced only inside facts.merge_population, and only for items linked to a
    document (`if req and doc and ...`, facts.py:141-147). save_inputs (store.py:279-294, the API's PUT
    /api/returns/{id}/inputs, app.py:978) only validates the model, which accepts the absent wages as 0 (model.py:60),
    and create (store.py:222-236, POST /api/clients/{id}/returns, app.py:949) stores the inputs without even that.
    Nothing before review asks for the wages, so a W-2 typed in without box 1 is a $0 W-2 whose $300 withholding is
    refunded."""
    R = Returns(solo.conn, solo.kb)
    inputs = {**JORDAN, "w2s": [{"owner": "taxpayer", "employer_name": "Night Shift Co", "federal_withholding": "300"}]}
    try:
        if entry == "create":
            rid = R.create("jordan", 2026, "maya", inputs)
            R.compute(rid, "maya")
        else:
            rid = R.create("jordan", 2026, "maya", JORDAN)
            R.save_inputs(rid, inputs, "maya")
    except ValueError:
        return                   # a fix may refuse a W-2 without wages outright (pydantic's ValidationError is a ValueError)
    _must_not_reach_review(R, rid, f"(b) via {entry}")


def test_b_prime_a_hand_edit_that_removes_a_document_w2s_wages(fam):  # noqa: F811
    """Path (b'). facts.mark_edits (facts.py:180-188) records the removal of the wages of the W-2 from d_w2a as a
    preparer value with confirmed=True, so even the unconfirmed-amount gate is satisfied; save_inputs (store.py:279-294)
    checks nothing, and nothing re-populates before review. The W-2 goes to review with no wages while its $4,100
    withholding stays on line 25a."""
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.confirm(rid, None, "maya")
    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    del next(w for w in edited["w2s"] if w.get("employer_name") == "Lakeside Market")["wages"]
    try:
        R.save_inputs(rid, edited, "maya")
    except ValueError:
        return
    _must_not_reach_review(R, rid, "(b')")


# --------------------------------------------------------------------------------------------- (c) keep is permanent
def test_c_a_keep_hides_the_missing_amount_from_every_later_population():
    """Path (c), the mechanism. merge_population skips the missing_value issue whenever (anchor, document, "None") is
    in resolved_keep (facts.py:144), which facts.resolved_keeps (facts.py:255-259) fills from every conflict ever
    closed as "kept". The exemption is not tied to the amount that was kept: it can only matter when the amount is
    empty, so it silences exactly the case the check exists for. The issue is neither raised nor reported."""
    pop = {"w2s": [{"owner": "taxpayer", "employer_name": "Night Shift Co", "federal_withholding": "300"}]}
    pop_prov = {"w2s[0].federal_withholding": {"document_id": "d1", "box": "box2", "value": "300"}}
    inputs, prov, _, issues, _ = facts.merge_population({}, {}, pop, pop_prov)
    assert [i["anchor"] for i in issues] == ["missing:w2s[d1].wages"]          # the first population reports it
    kept = {("missing:w2s[d1].wages", "d1", "None")}                           # what resolved_keeps holds after "keep"
    again, _, _, issues, _ = facts.merge_population(inputs, prov, pop, pop_prov, kept)
    assert "wages" not in again["w2s"][0]
    assert [i["code"] for i in issues] == ["missing_value"], (
        f"path (c): the W-2 still has no wages, but after a 'keep' the population reports {issues}")


def test_c_enter_keep_then_clear_is_never_raised_again(fam):  # noqa: F811
    """Path (c), end to end: the documented flow ("enter the amount, then keep it", store.py:331), then the amount is
    cleared again. The re-population is silenced by the earlier keep (facts.py:144), so no conflict blocks review and
    the W-2 goes to review without wages."""
    add_doc(fam.conn, "d_w2c", "rivera", "W-2", NIGHT_SHIFT)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    missing = _missing(R, rid, "missing:w2s[d_w2c].wages")
    entered = json.loads(json.dumps(R.latest(rid)["inputs"]))
    next(w for w in entered["w2s"] if w.get("employer_name") == "Night Shift Co")["wages"] = "30000"
    R.save_inputs(rid, entered, "maya")
    R.resolve_conflict(rid, missing["id"], "keep", "maya", note="typed in from the paper W-2")
    cleared = json.loads(json.dumps(R.latest(rid)["inputs"]))
    del next(w for w in cleared["w2s"] if w.get("employer_name") == "Night Shift Co")["wages"]
    try:
        R.save_inputs(rid, cleared, "maya")
    except ValueError:
        return
    out = R.populate_from_documents(rid, "maya")                               # re-populated before submitting
    R.confirm(rid, None, "maya")
    _must_not_reach_review(R, rid, f"(c) [re-population: {out['conflicts']} open conflict(s), issue codes "
                                   f"{sorted({i['code'] for i in out['issues']})}]")


# --------------------------------------------------------------------------------------------- (d) approve
def test_d_approve_rechecks_nothing(fam):  # noqa: F811
    """Path (d). _g_approve (store.py:118-124) checks segregation of duties and the package hash only, and approve
    (store.py:453-457) passes it nothing else. A return that reached review with a W-2 lacking wages (here by path (a))
    is approved by a second CPA: the $0 W-2 and the conflict closed as "kept" are not looked at again."""
    add_doc(fam.conn, "d_w2c", "rivera", "W-2", NIGHT_SHIFT)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    missing = _missing(R, rid, "missing:w2s[d_w2c].wages")
    with pytest.raises(ValueError, match="still missing"):
        R.resolve_conflict(rid, missing["id"], "keep", "maya")              # keeping an empty amount is refused
    R.confirm(rid, None, "maya")
    try:
        R.submit_for_review(rid, "maya")
    except TransitionError as e:
        assert "lack a required amount" in str(e)
        return                   # stopped before review, so it cannot be approved
    try:
        st = R.approve(rid, "lee", "cpa")
    except TransitionError:
        return
    pytest.fail(f"path (d): a second CPA approved a return whose W-2 has no wages; status '{st.status}'. {_evidence(R, rid)}")


def test_a_and_d_over_the_production_api_with_a_separate_reviewer(firm):  # noqa: F811
    """Paths (a) and (d) over HTTP in multi-firm mode (MFA sign-in, segregation of duties on): staff member Sam
    populates, keeps the missing-amount conflict without entering the wages (app.py:1008), confirms and submits
    (app.py:1073); CPA Lee approves (app.py:1075). The return is approved with $0 wages and a $300 refund."""
    mod, c, p, ids = firm
    admin, sam, lee = p["admin"], p["sam"], p["lee"]
    assert c.post("/api/clients", json={"id": "jordan-lee", "name": "Jordan Lee", "kind": "individual"}, headers=admin).status_code == 200
    assert c.post(f"/api/auth/users/{ids['sam']}/grants", json={"client_id": "jordan-lee"}, headers=lee).status_code == 200
    add_doc(mod.A(c.get("/api/me", headers=lee).json()).conn, "d_w2x", "jordan-lee", "W-2", {"employer_name": "Night Shift Co", "box2": "300"})
    r = c.post("/api/clients/jordan-lee/returns", json={"tax_year": 2026, "inputs": {k: v for k, v in JORDAN.items() if k != "tax_year"}},
               headers=sam)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert c.post(f"/api/returns/{rid}/populate", headers=sam).json()["conflicts"] == 1
    [conflict] = c.get(f"/api/returns/{rid}/conflicts", headers=sam).json()
    assert conflict["anchor"] == "missing:w2s[d_w2x].wages"
    keep = c.post(f"/api/returns/{rid}/conflicts/{conflict['id']}", json={"choice": "keep", "note": "chasing a W-2c"}, headers=sam)
    c.post(f"/api/returns/{rid}/confirm", json={}, headers=sam)
    submit = c.post(f"/api/returns/{rid}/submit", json={}, headers=sam)
    approve = c.post(f"/api/returns/{rid}/approve", headers=lee)
    final = c.get(f"/api/returns/{rid}", headers=lee).json()
    w2s = [(w.get("employer_name"), w.get("wages", "<absent>")) for w in final["inputs"].get("w2s", [])]
    assert keep.status_code == 400 and "still missing" in keep.text, keep.text
    assert submit.status_code == 409 and "required amount" in submit.text, submit.text
    assert final["status"] not in ("in_review", "approved"), (
        f"paths (a)+(d) over HTTP: keep -> {keep.status_code}, submit -> {submit.status_code}, approve by a second person "
        f"-> {approve.status_code}; status '{final['status']}', W-2s (name, wages) {w2s}, summary {final['summary']}")


# --------------------------------------------------------------------------------------------- (e) unreadable W-2
def test_e_a_w2_document_with_no_readable_amount_is_left_off_the_return(fam):  # noqa: F811
    """Path (e). documents.populate skips a box 1 that is not a number with a `not_a_number` note (documents.py:166-169)
    and drops a W-2 with no amount left with a `no_amounts` note (documents.py:178-182). Only `missing_value` issues
    become blocking conflicts (store.py:304-307); these notes are returned once (store.py:316) and stored nowhere, so
    the return goes to review without this W-2 at all."""
    add_doc(fam.conn, "d_w2u", "rivera", "W-2", {"employer_name": "Harbor Freight Lines", "recipient_tin_last4": "0001",
                                                 "box1": "52,OOO.OO"})          # OCR read the zeros as letters
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    out = R.populate_from_documents(rid, "maya")
    R.confirm(rid, None, "maya")
    notes = sorted(i["code"] for i in out["issues"] if i.get("document_id") == "d_w2u")
    _must_not_reach_review(R, rid, f"(e) [W-2 d_w2u (Harbor Freight Lines) is not on the return; populate notes {notes}, "
                                   f"{out['conflicts']} conflict(s)]")


# --------------------------------------------------------------------------------------------- (f) related
def test_f_related_filed_w2_documents_are_never_reconciled_at_review(fam):  # noqa: F811
    """Path (f), a related completeness gap rather than the F-07 mechanism: nothing requires a population, and
    _review_context (store.py:433-442) never compares the return with the client's filed W-2s for the year (the
    package only lists the documents it relied on, store.py:197-202). A return without any W-2 goes to review while
    two filed W-2s (d_w2a, d_w2b: $93,000 of wages) are on file."""
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.compute(rid, "maya")
    _must_not_reach_review(R, rid, "(f) [filed W-2s d_w2a and d_w2b are not on the return]", expect="not on the return")


# --------------------------------------------------------------------------------------------- the paths that must work
def test_entering_the_amount_then_keeping_it_lets_the_return_through(fam):  # noqa: F811
    """The documented flow: the preparer enters the wages from the paper W-2 and keeps them; with the unused 1099-NEC
    accounted for, the return goes to review, and the kept amount is checked again (not exempted) at every step."""
    add_doc(fam.conn, "d_w2c", "rivera", "W-2", NIGHT_SHIFT)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    missing = _missing(R, rid, "missing:w2s[d_w2c].wages")
    entered = json.loads(json.dumps(R.latest(rid)["inputs"]))
    next(w for w in entered["w2s"] if w.get("employer_name") == "Night Shift Co")["wages"] = "30000"
    R.save_inputs(rid, entered, "maya")
    R.resolve_conflict(rid, missing["id"], "keep", "maya", note="typed in from the paper W-2")
    R.account_for_document(rid, "d_nec", "not_applicable", "issued in error; the payer is sending a corrected 1099", "maya")
    assert R.populate_from_documents(rid, "maya")["conflicts"] == 0         # the kept amount is not asked again
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"
    w2 = next(w for w in R.latest(rid)["inputs"]["w2s"] if w.get("employer_name") == "Night Shift Co")
    assert w2["wages"] == "30000" and w2["source_document"] == "d_w2c"


def test_dispositions_are_part_of_the_reviewed_package(fam):  # noqa: F811
    """A filed document the return does not use is accounted for with a reason; the disposition is on record, cannot
    be changed, and is part of the package the reviewer approves (changing it would change the hash)."""
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    assert R.unaccounted_documents(rid) == ["d_nec"]
    with pytest.raises(ValueError, match="entered_by_hand or not_applicable"):
        R.account_for_document(rid, "d_nec", "ignore", "not needed for this year", "maya")
    with pytest.raises(ValueError, match="say why"):
        R.account_for_document(rid, "d_nec", "not_applicable", "n/a", "maya")
    with pytest.raises(KeyError):
        R.account_for_document(rid, "d_unknown", "not_applicable", "not a document of this client", "maya")
    before = R.current_package_hash(rid)
    R.account_for_document(rid, "d_nec", "entered_by_hand", "entered on Schedule C line 1 from the 1099-NEC", "maya")
    assert R.unaccounted_documents(rid) == [] and R.current_package_hash(rid) != before
    [d] = R.dispositions(rid)
    assert (d["document_id"], d["disposition"], d["actor"]) == ("d_nec", "entered_by_hand", "maya")
    with pytest.raises(Exception):
        fam.conn.execute("UPDATE return_document_dispositions SET disposition = 'not_applicable'")
    with pytest.raises(Exception):
        fam.conn.execute("DELETE FROM return_document_dispositions")
