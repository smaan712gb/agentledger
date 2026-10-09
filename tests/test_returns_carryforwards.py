"""T1-01 S1b: carryforwards persisted with each return version (return_carryforwards, sealed, append-only), the roll-forward
into the next year's prior_year group, the prior year's filed return among the documents a return must account for, and
the backfill of versions computed before the table existed. Runs on SQLite and, with AGENTLEDGER_DATABASE=postgres, on
PostgreSQL (migration 0007)."""

import json
from decimal import Decimal

import pytest
from test_return_workflow import add_doc, fam, household  # noqa: F401  (fam is a fixture)

from agentledger import audit, coverage
from agentledger.calc.engine import Ctx
from agentledger.db import unit_of_work
from agentledger.returns.individual import compute_individual
from agentledger.returns.model import IndividualReturn
from agentledger.returns.store import (CARRYFORWARDS_INDEXED, ENGINE_VERSION, SCHEMA, Returns, Sealer, carryforward_name,
                                       carryforward_row, carryovers, input_hash)
from agentledger.workflow.engine import TransitionError

CARRY = {**household(), "w2s": [{"owner": "taxpayer", "wages": "60000"}], "prior_year": {"capital_loss_carryover_long": "10000",
                                                                                        "traditional_ira_basis": "300",
                                                                                        "spouse_roth_ira_basis": "2500"}}


def rows(conn, rid):
    return [tuple(r) for r in conn.execute("SELECT version, kind, detail, amount FROM return_carryforwards WHERE return_id = ? "
                                           "ORDER BY version, kind, detail", (rid,)).fetchall()]


def test_each_version_records_its_carryforwards_sealed_and_keyed_by_kind_and_owner(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", CARRY)
    assert rows(fam.conn, rid) == []                                     # version 1 was never computed
    R.compute(rid, "maya")
    v = R.latest(rid)
    assert v["version"] == 2 and v["result"]["pinned"]["engine"] == ENGINE_VERSION
    got = rows(fam.conn, rid)
    # A stated prior basis carries unchanged (the spouse's Roth basis, the taxpayer's traditional basis); no HSA, so no
    # last-month-rule amount is recorded.
    assert [(k, d) for _, k, d, _ in got] == [("capital_loss_carryover_long", ""), ("capital_loss_carryover_short", ""),
                                             ("roth_ira_basis", "spouse"), ("traditional_ira_basis", "taxpayer")]
    assert all(version == 2 for version, _, _, _ in got)
    assert all(a.startswith("{") for _, _, _, a in got)                  # sealed by the firm key (plain JSON in single-firm mode)
    assert R.carryforwards(rid) == {"capital_loss_carryover_long": Decimal(7000), "capital_loss_carryover_short": Decimal(0),
                                    "spouse_roth_ira_basis": Decimal(2500), "traditional_ira_basis": Decimal(300)}
    assert R.carryforwards(rid, 1) == {}
    R.save_inputs(rid, {**CARRY, "prior_year": {**CARRY["prior_year"], "capital_loss_carryover_long": "2000"}}, "maya")
    assert R.carryforwards(rid)["capital_loss_carryover_long"] == 0 and R.carryforwards(rid, 2)["capital_loss_carryover_long"] == 7000
    assert len(rows(fam.conn, rid)) == 8                                 # the earlier version's rows are untouched


def test_the_table_is_append_only(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", CARRY)
    R.compute(rid, "maya")
    before = rows(fam.conn, rid)
    for sql in ("UPDATE return_carryforwards SET amount = 'x' WHERE return_id = ?", "DELETE FROM return_carryforwards WHERE return_id = ?"):
        with pytest.raises(Exception):
            with unit_of_work(fam.conn):
                fam.conn.execute(sql, (rid,))
    assert rows(fam.conn, rid) == before


def test_kind_and_owner_round_trip():
    assert carryforward_row("capital_loss_carryover_long") == ("capital_loss_carryover_long", "")
    assert carryforward_row("traditional_ira_basis") == ("traditional_ira_basis", "taxpayer")
    assert carryforward_row("spouse_roth_conversion_basis") == ("roth_conversion_basis", "spouse")
    for key in ("capital_loss_carryover_short", "traditional_ira_basis", "spouse_traditional_ira_basis", "roth_ira_basis", "spouse_roth_ira_basis",
                "roth_conversion_basis", "spouse_roth_conversion_basis", "hsa_last_month_rule_excess", "spouse_hsa_last_month_rule_excess"):
        assert carryforward_name(*carryforward_row(key)) == key


def _file(R, rid, monkeypatch):
    """Take a return through review, approval and signature to paper_filed (a filed return in FILED)."""
    R.account_for_document(rid, "d_nec", "not_applicable", "issued in error; the payer is sending a corrected 1099", "maya")
    R.confirm(rid, None, "maya")
    R.submit_for_review(rid, "maya")
    st = R.approve(rid, "lee", "cpa")
    R.request_signature(rid, "lee", "cpa")
    R.record_signature(rid, "taxpayer", method="kba_esign", return_hash=st.facts["approved_hash"], kba_transaction_id="kba-1")
    return R.mark_paper_filed(rid, "lee", "cpa", "mailed by certified mail on 2027-04-10, receipt 7001 2345")


def test_roll_forward_builds_the_next_years_prior_year_group_from_a_filed_return(fam, monkeypatch):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", CARRY)
    R.populate_from_documents(rid, "maya")                               # the documents' W-2s and 1099-INT join the hand-entered one
    with pytest.raises(TransitionError, match="only a filed return"):
        R.roll_forward(rid)
    estimate = R.roll_forward(rid, require_filed=False)
    assert estimate["prior_year"]["capital_loss_carryover_long"] == "7000"
    assert _file(R, rid, monkeypatch).status == "paper_filed"
    out = R.roll_forward(rid)
    v = R.latest(rid)
    assert out["client_id"] == "rivera" and out["tax_year"] == 2027 and out["from_return"] == rid and out["version"] == v["version"]
    summary = v["result"]["summary"]
    assert out["prior_year"] == {"filing_status": "mfj", "agi": summary["agi"], "tax": summary["total_tax"], "capital_loss_carryover_long": "7000",
                                 "capital_loss_carryover_short": "0", "spouse_roth_ira_basis": "2500", "traditional_ira_basis": "300"}
    assert out["provenance"]["prior_year.capital_loss_carryover_long"] == {"source": "return", "return_id": rid, "version": v["version"],
                                                                          "box": "capital_loss_carryover_long", "value": "7000", "confirmed": False}
    # The block is a valid prior_year group of a 2027 return, and every carried amount is recorded as a carryover.
    IndividualReturn.model_validate({**household(), "tax_year": 2027, "prior_year": out["prior_year"]})
    assert carryovers({"prior_year": out["prior_year"]}) == ["prior_year.capital_loss_carryover_long", "prior_year.spouse_roth_ira_basis",
                                                             "prior_year.traditional_ira_basis"]


def test_roll_forward_needs_a_computed_return(fam):  # noqa: F811
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    with pytest.raises(ValueError, match="compute"):
        R.roll_forward(rid, require_filed=False)


def test_the_prior_years_filed_return_is_a_document_the_return_must_account_for(fam):  # noqa: F811
    add_doc(fam.conn, "d_1040_2025", "rivera", "Prior-year return", {"line11": "88000", "capital_loss_carryover_long": "10000"}, year=2025)
    add_doc(fam.conn, "d_1040_2024", "rivera", "Prior-year return", {"line11": "80000"}, year=2024)   # two years back: not this return's
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", household())
    assert "d_1040_2025" in R.unaccounted_documents(rid) and "d_1040_2024" not in R.unaccounted_documents(rid)
    with pytest.raises(TransitionError, match="not on the return"):
        R.submit_for_review(rid, "maya")
    R.populate_from_documents(rid, "maya")
    assert "d_1040_2025" not in R.unaccounted_documents(rid)             # read into the prior_year group with provenance
    other = R.create("rivera", 2026, "maya", {**household(), "tax_year": 2026}, form="1040-X")
    R.account_for_document(other, "d_1040_2025", "entered_by_hand", "carryover entered from the 2025 return as filed", "maya")
    assert "d_1040_2025" not in R.unaccounted_documents(other)


def test_versions_computed_before_the_table_existed_are_backfilled_once(fam):  # noqa: F811
    """A store from engine 1040-2026.2: a version whose sealed result reports Result.carryforwards but with no
    return_carryforwards rows, written before any Returns() was constructed on the store (the raw insert path; the
    store's own schema on SQLite, the migrations on PostgreSQL)."""
    fam.conn.executescript(SCHEMA)                                       # SQLite only; a no-op on PostgreSQL
    result = compute_individual(Ctx(fam.kb), IndividualReturn.model_validate(CARRY)).to_dict()
    sealer, now = Sealer(), audit.now()
    with unit_of_work(fam.conn):
        fam.conn.execute("INSERT INTO tax_returns (id, client_id, tax_year, form, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
                         ("ret_old", "rivera", 2026, "1040", now, "maya"))
        fam.conn.execute("INSERT INTO tax_return_versions (return_id, version, inputs, provenance, result, summary, diagnostics, input_hash, "
                         "created_at, created_by, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("ret_old", 1, sealer.seal(CARRY, "return-inputs:ret_old"), sealer.seal({}, "return-provenance:ret_old"),
                          sealer.seal(result, "return-result:ret_old"), json.dumps(result["summary"]), "[]", input_hash(CARRY), now, "maya", "computed"))
    assert rows(fam.conn, "ret_old") == [] and fam.conn.execute("SELECT 1 FROM kv WHERE key = ?", (CARRYFORWARDS_INDEXED,)).fetchone() is None
    R = Returns(fam.conn, fam.kb)                                        # the first construction indexes the old version, once
    assert R.carryforwards("ret_old", 1) == {"capital_loss_carryover_long": Decimal(7000), "capital_loss_carryover_short": Decimal(0),
                                             "spouse_roth_ira_basis": Decimal(2500), "traditional_ira_basis": Decimal(300)}
    assert fam.conn.execute("SELECT 1 FROM kv WHERE key = ?", (CARRYFORWARDS_INDEXED,)).fetchone()
    Returns(fam.conn, fam.kb)                                            # again: nothing is duplicated or rewritten
    assert len(rows(fam.conn, "ret_old")) == 4


def test_worksheets_are_not_registry_forms():
    assert coverage.form_id("ws_ira_deduction") is None and coverage.form_id("ws_roth_contribution") is None
    assert coverage.form_id("f8889[spouse]") == "f8889" and coverage.form_id("f8606[taxpayer]") == "f8606"
