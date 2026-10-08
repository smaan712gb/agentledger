"""Deterministic core: rules, calculators, ledger, M-1, integrity, domain packs, expressions."""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from agentledger.calc.engine import Ctx
from agentledger.calc.federal import Asset, bonus_rate, run_calc, section_179_allowed, tax_depreciation
from agentledger.expr import ExprError, evaluate
from agentledger.foundry.verify import load_golden, run_golden
from agentledger.kb.model import Rule
from agentledger.kb.store import KnowledgeBase, MissingValue
from agentledger.ledger import m1, store
from agentledger.ledger.store import LedgerError, Line


def test_every_golden_scenario_passes(home):
    res = run_golden(KnowledgeBase(home / "rules"), load_golden(home / "golden" / "scenarios.yaml"))
    assert res and all(v["ok"] for v in res.values()), {k: v for k, v in res.items() if not v["ok"]}


def test_missing_value_is_never_guessed(home):
    kb = KnowledgeBase(home / "rules")
    with pytest.raises(MissingValue):
        kb.resolve("us_fed.individual.standard_deduction", date(2031, 12, 31))


def test_overlapping_values_are_rejected():
    with pytest.raises(ValueError, match="overlaps"):
        Rule.model_validate({"id": "x.y", "title": "t", "jurisdiction": "US-FED", "category": "c", "value_type": "money",
                             "citation": "c", "values": [
                                 {"effective_from": "2026-01-01", "effective_to": "2026-12-31", "value": 1, "provenance": {"source": "a"}},
                                 {"effective_from": "2026-06-01", "value": 2, "provenance": {"source": "b"}}]})


def test_calculation_carries_its_authority(home):
    ctx = Ctx(KnowledgeBase(home / "rules"))
    assert section_179_allowed(ctx, 2026, 3_000_000, 4_500_000) == Decimal(2_150_000)
    assert {t.rule_id for t in ctx.trace} == {"us_fed.depreciation.section_179_limit", "us_fed.depreciation.section_179_phaseout_threshold"}
    assert all(t.source for t in ctx.trace)


def test_bonus_depends_on_acquisition_date(home):
    ctx = Ctx(KnowledgeBase(home / "rules"))
    assert bonus_rate(ctx, date(2024, 12, 1), date(2025, 2, 1)) == Decimal("0.4")
    assert bonus_rate(ctx, date(2025, 1, 20), date(2025, 1, 25)) == Decimal("1.0")


def test_tax_depreciation_first_year(home):
    ctx = Ctx(KnowledgeBase(home / "rules"))
    a = Asset("a", Decimal(10000), date(2024, 6, 1), date(2024, 6, 1), 5, 5)
    # 60% bonus on 10,000 = 6,000; MACRS 20% on remaining 4,000 = 800
    assert tax_depreciation(ctx, a, 2024) == Decimal("6800.00")


def test_ledger_rejects_unbalanced_and_is_append_only(biz):
    conn = biz.conn
    with pytest.raises(LedgerError, match="balance"):
        store.post(conn, "acme", date(2026, 1, 5), "bad", [Line("1000", Decimal(10)), Line("4000", Decimal(-9))], source="t", actor="t")
    eid = store.post(conn, "acme", date(2026, 1, 5), "sale", [Line("1000", Decimal(10)), Line("4000", Decimal(-10))], source="t", actor="t")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE postings SET amount = '99' WHERE entry_id = ?", (eid,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM entries WHERE id = ?", (eid,))
    rid = store.reverse(conn, "acme", eid, date(2026, 1, 6), "duplicate", "t")
    assert store.entry(conn, "acme", eid)["reversed_by"] == rid
    assert store.verify_chain(conn, "acme")["ok"]


def test_tampering_below_the_app_is_detected(biz):
    conn = biz.conn
    store.post(conn, "acme", date(2026, 1, 5), "sale", [Line("1000", Decimal(10)), Line("4000", Decimal(-10))], source="t", actor="t")
    conn.execute("DROP TRIGGER entries_no_update")
    conn.execute("UPDATE entries SET memo = 'edited' WHERE client_id = 'acme'")
    assert store.verify_chain(conn, "acme")["ok"] is False


def test_m1_flows_from_ledger_and_rules(biz):
    conn, kb = biz.conn, biz.kb
    p = lambda memo, lines: store.post(conn, "acme", date(2026, 3, 1), memo, lines, source="t", actor="t")
    p("revenue", [Line("1000", Decimal(100000)), Line("4000", Decimal(-100000))])
    p("client dinner", [Line("6200", Decimal(1000), "meals"), Line("1000", Decimal(-1000))])
    p("fine", [Line("6700", Decimal(300), "fines_penalties"), Line("1000", Decimal(-300))])
    p("muni interest", [Line("1000", Decimal(500)), Line("4910", Decimal(-500), "municipal_interest")])
    res = m1.compute(conn, Ctx(kb), "acme", 2026)
    lines = {l.line: l.amount for l in res.lines}
    assert lines["1"] == Decimal(99200)
    assert lines["5c"] == Decimal(500)  # 50% of meals
    assert lines["5"] == Decimal(300)
    assert lines["7"] == Decimal(500)
    assert lines["10"] == Decimal(99200 + 500 + 300 - 500)


def test_integrity_findings_resolution_rules(biz):
    from agentledger.integrity.checks import list_findings, resolve, run_all

    conn = biz.conn
    store.post(conn, "acme", date(2026, 4, 2), "Golf outing with customers", [Line("6200", Decimal(900), "meals"), Line("1000", Decimal(-900))],
               source="t", actor="t")
    conn.execute("INSERT INTO info_returns (client_id, form, tax_year, payer, amount) VALUES ('acme','1099-NEC',2026,'X','5000')")
    found = run_all(conn, biz.kb, "acme", 2026)
    checks = {f["check_id"] for f in found}
    assert {"classification.entertainment_as_meals", "substantiation.missing_receipt", "income.info_return_mismatch"} <= checks
    f = next(x for x in found if x["check_id"] == "income.info_return_mismatch")
    with pytest.raises(PermissionError):
        resolve(conn, f["id"], "owner", "client", "accepted_risk", "I don't think this matters")
    with pytest.raises(ValueError):
        resolve(conn, f["id"], "owner", "client", "explained", "ok")
    resolve(conn, f["id"], "owner", "client", "explained", "Deposit went to my personal account by mistake; moving it now.")
    assert next(x for x in list_findings(conn, "acme") if x["id"] == f["id"])["status"] == "resolved"
    assert len(run_all(conn, biz.kb, "acme", 2026)) == len(found)  # re-running never duplicates


def test_every_domain_pack_verifies(home):
    from agentledger.domains.packs import Packs, verify_pack

    packs, kb = Packs(home / "domains"), KnowledgeBase(home / "rules")
    assert len(packs.packs) >= 7
    for p in packs.packs.values():
        bad = [c for c in verify_pack(p, packs, kb) if not c["ok"]]
        assert not bad, (p.id, bad)


def test_domain_rules_fire(biz):
    from agentledger.domains.packs import Packs
    from agentledger.domains.service import domain_findings, onboard, post_template
    from agentledger.ledger import store

    packs = Packs(biz.paths.domains)
    store.add_client(biz.conn, id="gas", name="Gas Co", kind="business", domain="gas_station")
    onboard(biz.conn, packs, "gas", "gas_station")
    store.post(biz.conn, "gas", date(2026, 5, 1), "Lottery scratch sales", [Line("1000", Decimal(500)), Line("4200", Decimal(-500))],
               source="t", actor="t")
    post_template(biz.conn, packs, biz.kb, "gas", "fuel_shrink", {"tank": "T1", "book_gallons": 1000, "measured_gallons": 900,
                                                                  "cost_per_gallon": 3}, date(2026, 5, 2), actor="t")
    store.post(biz.conn, "gas", date(2026, 5, 3), "fuel cogs", [Line("5100", Decimal(10000)), Line("1210", Decimal(-10000))], source="t", actor="t")
    ids = {f.check_id for f in domain_findings(biz.conn, packs, "gas", 2026)}
    assert {"domain.lottery_as_sales", "domain.shrink_ratio"} <= ids


def test_expression_engine_is_safe():
    assert evaluate("a + b * 2", {"a": 1, "b": 2}) == Decimal(5)
    assert evaluate("kind == 'business' and n > 3", {"kind": "business", "n": 4}) is True
    for bad in ("__import__('os')", "a.__class__", "open('x')", "[x for x in a]", "lambda: 1"):
        with pytest.raises(ExprError):
            evaluate(bad, {"a": 1})
