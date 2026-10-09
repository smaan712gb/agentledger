"""Review of the 952ee96 re-audit fixes, fact conflicts. The first adversarial review found that W-2 box 12 amounts and the
attributes population wrote without provenance (a 1099-R's box 7 code) never took part in "facts never overwrite": an
edit that disagreed with the document was silently kept and a re-read was ignored; that a "keep" decision exempted
every later value of the field, so a typo after it passed as agreeing with the W-2; and that on PostgreSQL two
overlapping populations could leave two open conflicts for one field. Asserted now: each box 12 code, box 7 code,
tipped occupation code and IRA/SEP/SIMPLE checkbox is a sourced fact (a disagreement is a conflict, a re-read is
applied); a keep covers the kept value only, at re-population and at every gate; the database keeps one open
conflict per field, also when two populations overlap.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)

from agentledger import db
from agentledger.returns import facts
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

NEC_NOTE = "issued in error; the payer is sending a corrected 1099"
OVERTIME_W2 = {"employer_name": "Depot Logistics", "recipient_tin_last4": "0001", "box1": "40000", "box2": "3000",
               "box3": "40000", "box4": "2480", "box5": "40000", "box6": "580", "box12_TT": "8000", "box14b": "112"}
RETIREMENT = {"payer_name": "Old Employer 401(k) Plan", "recipient_tin_last4": "0001", "box1": "20,000.00",
              "box2a": "20,000.00", "box4": "4,000.00", "box7": "1", "ira_sep_simple": "X"}
UNDECIDED = "disagree with the documents and were never decided"
CONFLICTS = "fact conflict(s) between documents and the return must be resolved"


def _inputs(R, rid):
    return json.loads(json.dumps(R.latest(rid)["inputs"]))


def _setup(fam, doc_type="W-2", fields=OVERTIME_W2, doc="d_w2t"):  # noqa: F811
    add_doc(fam.conn, doc, "rivera", doc_type, fields)
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    R.confirm(rid, None, "maya")
    return R, rid


def _edit(R, rid, lst, doc, fn):
    edited = _inputs(R, rid)
    fn(next(i for i in edited[lst] if i.get("source_document") == doc))
    R.save_inputs(rid, edited, "maya")


def _refused_for(R, rid, reason):
    R.confirm(rid, None, "maya")
    with pytest.raises(TransitionError) as e:
        R.submit_for_review(rid, "maya")
    assert reason in str(e.value), f"review was refused, but not for the reason under test: {e.value}"


def _conflicts(R, rid):
    return [(c["anchor"], c["current_value"], c["proposed_value"]) for c in R.conflicts(rid)]


# --------------------------------------------------------------------------- box 12 and other coded attributes
def test_box1_edit_is_a_conflict_for_contrast(fam):  # noqa: F811
    R, rid = _setup(fam)
    _edit(R, rid, "w2s", "d_w2t", lambda w: w.update(wages="45000"))
    _refused_for(R, rid, UNDECIDED)                       # the gate's dry run sees it before any re-population
    R.populate_from_documents(rid, "maya")
    assert _conflicts(R, rid) == [("w2s[d_w2t].wages", "45000", "40000")]


def test_box12_edit_that_disagrees_with_the_w2_is_a_conflict(fam):  # noqa: F811
    R, rid = _setup(fam)
    _edit(R, rid, "w2s", "d_w2t", lambda w: w["box12"].update(TT="12500"))     # the W-2 says 8,000
    _refused_for(R, rid, UNDECIDED)
    R.populate_from_documents(rid, "maya")
    assert _conflicts(R, rid) == [("w2s[d_w2t].box12.TT", "12500", "8000")]
    _refused_for(R, rid, CONFLICTS)
    [c] = R.conflicts(rid)
    R.resolve_conflict(rid, c["id"], "document", "lee", note="the W-2 shows 8,000 in box 12 TT")
    w = next(w for w in R.latest(rid)["inputs"]["w2s"] if w.get("source_document") == "d_w2t")
    assert w["box12"] == {"TT": "8000"}
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"


def test_box12_reread_is_applied_or_raised(fam):  # noqa: F811
    """The same stimulus as the finding-2 regression tests (the document's extracted fields change): like box 1, a box
    12 amount re-read from the same document is applied as a correction of itself, its provenance citing the new
    value, and must be confirmed again."""
    R, rid = _setup(fam)
    fam.conn.execute("UPDATE documents SET fields = ? WHERE id = 'd_w2t'", (json.dumps({**OVERTIME_W2, "box12_TT": "2000"}),))
    _refused_for(R, rid, "the documents now provide 1 value(s) not on the return")
    R.populate_from_documents(rid, "maya")
    v = R.latest(rid)
    j, w = next((j, w) for j, w in enumerate(v["inputs"]["w2s"]) if w.get("source_document") == "d_w2t")
    assert w["box12"] == {"TT": "2000"} and R.conflicts(rid) == []
    p = v["provenance"][f"w2s[{j}].box12.TT"]
    assert (p["document_id"], p["value"], p["confirmed"]) == ("d_w2t", "2000", False)


ATTRIBUTES = [
    ("1099-R", RETIREMENT, "d_r", "retirement", "distribution_code", "7", "1"),
    ("1099-R", RETIREMENT, "d_r", "retirement", "ira_sep_simple", False, True),
    ("W-2", OVERTIME_W2, "d_w2t", "w2s", "tipped_occupation_code", 305, 112),
]


@pytest.mark.parametrize("doc_type,fields,doc,lst,field,typed,on_document", ATTRIBUTES,
                         ids=["1099-R-box7-code", "1099-R-ira-sep-simple", "W-2-box14b-tipped-occupation"])
def test_an_attribute_edit_that_disagrees_with_the_document_is_a_conflict(fam, doc_type, fields, doc, lst, field,  # noqa: F811
                                                                          typed, on_document):
    """Same mechanism for every attribute population writes: the document's box 7 says "1" (early distribution, 10%
    additional tax); the preparer changes it to "7"; the change is a conflict, not silently kept."""
    R, rid = _setup(fam, doc_type, fields, doc)
    item = next(i for i in R.latest(rid)["inputs"][lst] if i.get("source_document") == doc)
    assert item[field] == on_document
    _edit(R, rid, lst, doc, lambda i: i.update({field: typed}))
    _refused_for(R, rid, UNDECIDED)
    R.populate_from_documents(rid, "maya")
    assert _conflicts(R, rid) == [(f"{lst}[{doc}].{field}", typed, on_document)]
    _refused_for(R, rid, CONFLICTS)


# --------------------------------------------------------------------------- a keep covers the kept value only
def _set_wages(R, rid, employer, wages):
    edited = _inputs(R, rid)
    next(w for w in edited["w2s"] if w["employer_name"] == employer)["wages"] = wages
    R.save_inputs(rid, edited, "maya")


def test_a_keep_does_not_silence_a_later_different_value(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    R.populate_from_documents(rid, "maya")
    R.account_for_document(rid, "d_nec", "not_applicable", NEC_NOTE, "maya")
    _set_wages(R, rid, "Lakeside Market", "52500.00")
    R.populate_from_documents(rid, "maya")
    [c] = R.conflicts(rid)
    assert (c["anchor"], c["proposed_value"], c["current_value"]) == ("w2s[d_w2a].wages", "52000.00", "52500.00")
    R.resolve_conflict(rid, c["id"], "keep", "lee", note="December bonus paid through payroll; W-2c requested")
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"           # the decision covers 52,500.00

    _set_wages(R, rid, "Lakeside Market", "5250.00")          # later: a typo, or any other value
    _refused_for(R, rid, UNDECIDED)                           # every gate re-checks, without a re-population
    out = R.populate_from_documents(rid, "maya")
    assert out["conflicts"] == 1
    assert _conflicts(R, rid) == [("w2s[d_w2a].wages", "5250.00", "52000.00")]
    _refused_for(R, rid, CONFLICTS)

    _set_wages(R, rid, "Lakeside Market", "52500.00")         # back to the value that was decided
    R.populate_from_documents(rid, "maya")
    assert R.conflicts(rid) == []                             # the open question was superseded, not asked again
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"


# --------------------------------------------------------------------------- one open conflict per field
CONFLICT = {"path": "w2s[0].wages", "anchor": "w2s[d_w2a].wages", "current": "52000.00", "proposed": "53000.00",
            "document_id": "d_w2a", "box": "box1", "current_source": "preparer"}


def test_the_database_keeps_one_open_conflict_per_field(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    first = facts.raise_conflicts(fam.conn, R.sealer, rid, [dict(CONFLICT)], "maya")
    again = facts.raise_conflicts(fam.conn, R.sealer, rid, [dict(CONFLICT), {**CONFLICT, "proposed": "54000.00"}], "maya")
    assert len(first) == 1 and again == []
    assert [c["id"] for c in R.conflicts(rid)] == first
    with pytest.raises(sqlite3.IntegrityError):               # not only the code: the database refuses a second one
        with db.unit_of_work(fam.conn):
            fam.conn.execute("INSERT INTO fact_conflicts (return_id, path, anchor, document_id, box, current_value, "
                             "proposed_value, current_source, raised_by, raised_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (rid, "w2s[0].wages", "w2s[d_w2a].wages", "d_w2b", "box1", "{}", "{}", "preparer", "maya",
                              "2026-02-01T00:00:00+00:00"))
    facts.close_conflict(fam.conn, first[0], resolution="superseded", actor="maya", note="re-read")
    assert len(facts.raise_conflicts(fam.conn, R.sealer, rid, [dict(CONFLICT)], "maya")) == 1   # a new question, once


@pytest.mark.skipif(db.backend() != "postgres",
                    reason="PostgreSQL interleaving; SQLite serializes unit_of_work with BEGIN IMMEDIATE")
def test_overlapping_populations_leave_one_open_conflict_per_field(fam, monkeypatch):  # noqa: F811
    """Two populations of one return (a double click, an agent and a preparer) inside their transactions at the same
    time, before either writes: both insert, the partial unique index decides, and nothing fails."""
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    real = facts.unit_of_work
    both_open = threading.Barrier(2, timeout=15)
    overlapped: list[int] = []

    @contextlib.contextmanager
    def unit_of_work(conn):
        with real(conn) as raw:
            both_open.wait()                       # both transactions have begun before either inserts
            overlapped.append(1)
            yield raw

    monkeypatch.setattr(facts, "unit_of_work", unit_of_work)
    errors: list[Exception] = []
    raised: list[list[int]] = []

    def populate_conflicts():
        try:
            raised.append(facts.raise_conflicts(fam.conn, R.sealer, rid, [dict(CONFLICT)], "maya"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=populate_conflicts) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    monkeypatch.setattr(facts, "unit_of_work", real)

    open_ = [c["id"] for c in facts.open_conflicts(fam.conn, R.sealer, rid) if c["anchor"] == CONFLICT["anchor"]]
    assert len(overlapped) == 2                    # the two transactions really overlapped
    assert not errors and len(open_) == 1, f"open conflicts for {CONFLICT['anchor']}: {open_}; errors: {errors}"
    assert sorted(len(r) for r in raised) == [0, 1]
