"""Review of the fix for finding 5 (re-audit of 952ee96), returns filed before return_document_uses existed. The first
adversarial review found that a return filed before the index existed was never indexed (its provenance is sealed and
a filed return is never saved again), so a W-2 it relied on, confirmed with a wrong year, became deletable from
2034-05-03 while that late-filed 2026 return stayed assessable to 2035-06-01. Asserted now, on a store in the
pre-upgrade state (no index rows, no backfill): retention runs refuse until the returns are indexed; opening the
returns indexes them, and the filed return's year governs again.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agentledger import db
from agentledger.evidence import records
from agentledger.returns.store import Returns, relied_on
from test_reaudit_952ee96_premature_retention import (GRACE, W2_2026, _event, confirm, deletable_from, due, end_of, on,
                                                      paper_file, receive, w2_2026_router)
from test_return_workflow import fam, household  # noqa: F401  (fixture)


def test_a_return_filed_before_the_upgrade_still_keeps_its_year(fam):  # noqa: F811
    w2 = receive(fam, "w2-2026-harbor.txt", W2_2026, received=date(2027, 2, 1), router=w2_2026_router())
    with pytest.MonkeyPatch.context() as mp:                      # as on 952ee96: no index, nothing to backfill it
        mp.setattr(Returns, "_record_uses", lambda self, rid, documents, version: None)
        mp.setattr(Returns, "_record_retention_facts", lambda self, rid, inputs, result, version: None)
        mp.setattr(Returns, "_backfill_uses", lambda self: None)
        R = Returns(fam.conn, fam.kb, segregation=False)
        with on(date(2032, 6, 1)):
            rid = R.create("rivera", 2026, "maya", household())
            R.populate_from_documents(rid, "maya")
            assert paper_file(R, rid).status == "paper_filed"
    v = R.latest(rid)
    assert w2["id"] in relied_on(v["inputs"], v["provenance"])     # the filed return's sealed record shows it
    assert not db.one(fam.conn, "SELECT 1 AS x FROM return_document_uses WHERE document_id = ?", w2["id"])
    assert not records.uses_indexed(fam.conn)
    confirm(fam.conn, w2["id"], 2025)                              # a wrong year, as in the existing regression test
    _event(fam.conn, "rivera", 2025, "filed", "2026-04-10", "1040", note="IRS account transcript shows the 2025 return received 2026-04-10")
    day = date(2035, 6, 1) - timedelta(days=1)
    with pytest.raises(records.RetentionError, match="not indexed yet"):
        records.due_for_deletion(fam.conn, day)
    with pytest.raises(records.RetentionError, match="not indexed yet"):
        records.purge_expired(fam.conn, fam.vault, day, actor="lee", role="cpa", attested=True, reason="annual retention review")
    assert fam.vault.exists(w2["vault_path"])

    Returns(fam.conn, fam.kb, segregation=False)                    # the upgraded code opens the returns: indexed
    assert records.uses_indexed(fam.conn)
    assert db.one(fam.conn, "SELECT 1 AS x FROM return_document_uses WHERE return_id = ? AND document_id = ?", rid, w2["id"])
    assert w2["id"] not in due(fam.conn, day)
    assert end_of(fam.conn, w2["id"]) == (date(2039, 6, 1) + GRACE, "ok")      # the late 2026 filing governs
    deletable_from(fam.conn, w2["id"], date(2039, 6, 1) + GRACE + timedelta(days=1))
