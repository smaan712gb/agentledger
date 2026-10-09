"""Second adversarial review of the 952ee96 re-audit fixes, intake grounding: a box whose number does not appear in the
document's text layer (a scanned W-2 box 10 read as "5,OOO.OO") was dropped before anything was stored, so the return
took it as zero and overstated the dependent care credit. Asserted now: intake keeps it in fields["_unverified"],
population reports it as unreadable and the W-2's dependent care benefits as missing, review is refused for that
reason, and once a person enters box 10 the return goes through with the lower credit.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from agentledger.intake.classify import KV, Classification
from agentledger.intake.pipeline import assign, ingest
from agentledger.ledger import store
from agentledger.returns.store import Returns
from agentledger.workflow.engine import TransitionError

JORDAN = {"tax_year": 2026, "filing_status": "single",
          "taxpayer": {"first_name": "Jordan", "last_name": "Lee", "ssn": "400-00-0009", "dob": "1990-01-01"},
          "dependent_care_expenses": "6000", "dependent_care_qualifying_persons": 2}
TEXT = ("Form W-2 Wage and Tax Statement 2026\nEmployee: Jordan Lee SSN XXX-XX-0009\nEmployer: Night Shift Co\n"
        "1 Wages, tips, other compensation 60,000.00\n2 Federal income tax withheld 6,000.00\n"
        "3 Social security wages 60,000.00\n4 Social security tax withheld 3,720.00\n5 Medicare wages and tips 60,000.00\n"
        "6 Medicare tax withheld 870.00\n10 Dependent care benefits 5,OOO.OO\n")
READ = {"employer_name": "Night Shift Co", "recipient_tin_last4": "0009", "box1": "60,000.00", "box2": "6,000.00",
        "box3": "60,000.00", "box4": "3,720.00", "box5": "60,000.00", "box6": "870.00", "box10": "5,000.00"}


def _credit(R, rid):
    return Decimal(R.latest(rid)["result"]["forms"].get("f2441", {}).get("11", "0"))


def test_a_box_dropped_by_grounding_is_missing_until_entered(foundry):
    store.add_client(foundry.conn, id="jordan", name="Jordan Lee", kind="individual", emails=[], tax_id_last4="0009",
                     domain="general", facts={"taxpayer_ssn_last4": "0009", "taxpayer_name": "Jordan Lee"})
    foundry.router.responses["classify"] = Classification(
        doc_type="W-2", tax_year=2026, party_names=["Jordan Lee"], tin_last4=["0009"],
        fields=[KV(name=k, value=v) for k, v in READ.items()], summary="2026 W-2 from Night Shift Co", confidence=0.97)
    [got] = ingest(foundry.conn, foundry.router, foundry.vault, "w2-night-shift.txt", TEXT.encode(), channel="upload",
                   client_hint="jordan")
    doc = got["id"]
    if foundry.conn.execute("SELECT status FROM documents WHERE id = ?", (doc,)).fetchone()["status"] != "filed":
        assign(foundry.conn, foundry.vault, doc, "jordan", "lee")          # a person files it from the review queue
    raw = foundry.conn.execute("SELECT fields FROM documents WHERE id = ?", (doc,)).fetchone()["fields"]
    stored = raw if isinstance(raw, dict) else json.loads(raw)
    assert "box10" not in stored and stored["_unverified"] == {"box10": "5,000.00"}
    assert {k: stored[k] for k in READ if k != "box10"} == {k: v for k, v in READ.items() if k != "box10"}

    R = Returns(foundry.conn, foundry.kb)
    rid = R.create("jordan", 2026, "maya", JORDAN)
    out = R.populate_from_documents(rid, "maya")
    unreadable = [i["message"] for i in out["issues"] if i["document_id"] == doc and i["code"] == "unreadable"]
    assert len(unreadable) == 1 and "box10 = '5,000.00' could not be read" in unreadable[0], unreadable
    [w2] = R.latest(rid)["inputs"]["w2s"]
    assert "dependent_care_benefits" not in w2                             # never taken as zero
    anchor = f"missing:w2s[{doc}].dependent_care_benefits"
    assert [c["anchor"] for c in R.conflicts(rid)] == [anchor]
    R.confirm(rid, None, "maya")
    with pytest.raises(TransitionError) as e:
        R.submit_for_review(rid, "maya")
    assert "1 item(s) lack a required amount" in str(e.value), str(e.value)
    as_if_zero = _credit(R, rid)

    edited = json.loads(json.dumps(R.latest(rid)["inputs"]))
    edited["w2s"][0]["dependent_care_benefits"] = "5000.00"              # from the paper copy
    R.save_inputs(rid, edited, "maya")
    [c] = R.conflicts(rid)
    R.resolve_conflict(rid, c["id"], "keep", "maya", note="box 10 reads 5,000.00 on the paper copy")
    R.populate_from_documents(rid, "maya")
    assert R.conflicts(rid) == []                                          # answered once, not asked again
    R.confirm(rid, None, "maya")
    assert R.submit_for_review(rid, "maya").status == "in_review"
    assert R.approve(rid, "lee", "cpa").status == "approved"
    assert _credit(R, rid) < as_if_zero                                    # the excluded benefits cut the expense limit
