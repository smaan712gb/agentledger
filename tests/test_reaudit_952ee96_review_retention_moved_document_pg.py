"""Review of the fix for finding 5 (re-audit of 952ee96), the moved-document repro of the PostgreSQL review (runs on
both backends). The first adversarial review found that once a W-2 relied on by Alex Rivera's late-filed 2026 return
(kept to 2039-06-01 plus the margin) was moved to another client with an on-time 2026 filing, retention was computed
from that client's filing and ended in 2034, five years early. Asserted now: the move is refused because a filed return
relied on the document, the W-2 stays Rivera's, and its retention end is unchanged by the other client's filing.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agentledger import db
from agentledger.intake.pipeline import assign
from agentledger.ledger import store
from agentledger.returns.store import Returns
from test_reaudit_952ee96_premature_retention import (GRACE, W2_2026, _event, confirm, deletable_from, due, end_of, on,
                                                      paper_file, receive, w2_2026_router)
from test_return_workflow import fam, household  # noqa: F401  (fixture)


def test_a_document_a_filed_return_relied_on_is_not_moved(fam):  # noqa: F811
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1), router=w2_2026_router())
    R = Returns(fam.conn, fam.kb, segregation=False)
    with on(date(2032, 6, 1)):                                    # Rivera's 2026 return, filed late, relied on the W-2
        rid = R.create("rivera", 2026, "maya", household())
        R.populate_from_documents(rid, "maya")
        paper_file(R, rid)
    assert db.one(fam.conn, "SELECT 1 AS x FROM return_document_uses WHERE return_id = ? AND document_id = ?", rid, w2["id"])
    confirm(fam.conn, w2["id"], 2026)
    assert end_of(fam.conn, w2["id"]) == (date(2039, 6, 1) + GRACE, "ok")          # the late filing governs

    store.add_client(fam.conn, id="ortiz", name="Dana Ortiz", kind="individual", emails=[], domain="general")
    with pytest.raises(ValueError, match=f"filed return {rid} relied on this document: it stays that return's evidence"):
        assign(fam.conn, fam.vault, w2["id"], "ortiz", "lee", move_reason="filed to the wrong household at intake")
    _event(fam.conn, "ortiz", 2026, "filed", "2027-04-10", "1040",
           note="IRS account transcript shows Dana Ortiz's 2026 return received 2027-04-10")
    d = db.one(fam.conn, "SELECT client_id, retention_confirmed_at FROM documents WHERE id = ?", w2["id"])
    assert d["client_id"] == "rivera" and d["retention_confirmed_at"]
    assert not db.one(fam.conn, "SELECT 1 AS x FROM document_moves WHERE document_id = ?", w2["id"])
    assert end_of(fam.conn, w2["id"]) == (date(2039, 6, 1) + GRACE, "ok")
    assert w2["id"] not in due(fam.conn, date(2035, 1, 1))
    deletable_from(fam.conn, w2["id"], date(2039, 6, 1) + GRACE + timedelta(days=1))
