"""Autonomy and its guardrails: regwatch drafting, Sentinel verification, policy, rollback, staleness."""

import json
from datetime import date

from conftest import FakeRouter
from veritas.foundry.agents.regwatch import ingest_document
from veritas.foundry.agents.staleness import scan
from veritas.foundry.verify import verify_rule_change
from veritas.kb.store import KnowledgeBase
from veritas.regwatch.draft import Draft, ProposedValue, prefilter
from veritas.regwatch.documents import Document

REV_PROC = """Rev. Proc. 2026-41
SECTION 3. 2027 ADJUSTED ITEMS
.15 Standard Deduction.
(1) In general. For taxable years beginning in 2027, the standard deduction amounts under § 63(c)(2) are as follows:
Married Individuals Filing Joint Returns and Surviving Spouses $33,200
Heads of Households $24,950
Unmarried Individuals (other than Surviving Spouses and Heads of Households) $16,600
Married Individuals Filing Separate Returns $16,600
"""
URL = "https://www.irs.gov/pub/irs-drop/rp-26-41.pdf"


def draft(values=None, quotes=None, effective="2027-01-01"):
    values = values or {"single": 16600, "mfj": 33200, "mfs": 16600, "hoh": 24950}
    quotes = quotes or ["Married Individuals Filing Joint Returns and Surviving Spouses $33,200", "Heads of Households $24,950",
                        "Unmarried Individuals (other than Surviving Spouses and Heads of Households) $16,600",
                        "Married Individuals Filing Separate Returns $16,600"]
    return Draft(relevant=True, summary="2027 standard deduction amounts.", new_rules=[], code_change_needed=[], confidence=0.95,
                 changes=[ProposedValue(rule_id="us_fed.individual.standard_deduction", value_json=json.dumps(values),
                                        effective_from=effective, effective_to=effective[:4] + "-12-31",
                                        citation="Rev. Proc. 2026-41 §3.15", evidence_quotes=quotes, rationale="annual indexing")])


def test_grounded_routine_update_is_auto_adopted_and_reversible(foundry):
    foundry.router = FakeRouter({"draft": draft()})
    res = ingest_document(foundry, "Rev. Proc. 2026-41", URL, REV_PROC)
    p = foundry.load(res.proposals[0])
    assert p.verified and p.risk == "low" and p.status == "adopted"
    assert p.decision["by"] == "auto-policy"
    assert foundry.kb.resolve("us_fed.individual.standard_deduction", date(2027, 6, 1)).value["mfj"] == 33200
    prov = foundry.kb.get("us_fed.individual.standard_deduction").value_on(date(2027, 6, 1)).provenance
    assert prov.adopted_via == p.id and prov.approved_by == "auto-policy" and prov.url == URL
    assert "Rev. Proc. 2026-41" in (foundry.paths.rules / "CHANGELOG.md").read_text(encoding="utf-8")
    foundry.rollback(p.id, actor="maya", note="testing rollback")
    assert foundry.kb.try_resolve("us_fed.individual.standard_deduction", date(2027, 6, 1)) is None


def test_hallucinated_number_is_blocked(foundry):
    # The model claims $33,400 but the document says $33,200.
    bad = draft(values={"single": 16600, "mfj": 33400, "mfs": 16600, "hoh": 24950})
    foundry.router = FakeRouter({"draft": bad})
    p = foundry.load(ingest_document(foundry, "Rev. Proc. 2026-41", URL, REV_PROC).proposals[0])
    assert p.status == "pending" and p.risk == "critical"
    assert not next(c for c in p.checks if c.name.startswith("numbers_grounded")).ok
    assert foundry.kb.try_resolve("us_fed.individual.standard_deduction", date(2027, 6, 1)) is None


def test_fabricated_quote_is_blocked(home):
    kb = KnowledgeBase(home / "rules")
    d = draft(quotes=["The standard deduction for married couples is $33,200 and singles $16,600, heads $24,950."])
    payload = {"changes": [{**d.changes[0].model_dump(exclude={"value_json"}), "value": json.loads(d.changes[0].value_json)}]}
    checks, risk, _, _ = verify_rule_change(kb, payload, REV_PROC, URL, ["irs.gov"], [])
    assert risk == "critical" and not next(c for c in checks if c.name.startswith("quotes_verbatim")).ok


def test_unofficial_source_never_auto_adopts(foundry):
    foundry.router = FakeRouter({"draft": draft()})
    p = foundry.load(ingest_document(foundry, "Blog post", "https://taxblog.example.com/2027", REV_PROC).proposals[0])
    assert p.status == "pending" and p.risk == "critical"


def test_amending_a_value_in_force_needs_a_human(foundry):
    amended = draft(values={"single": 16100, "mfj": 32200, "mfs": 16100, "hoh": 24150}, effective="2026-01-01",
                    quotes=["$32,200", "$16,100", "$24,150"])
    text = "Corrected amounts for 2026: joint $32,200; single $16,100; head of household $24,150."
    foundry.router = FakeRouter({"draft": amended})
    p = foundry.load(ingest_document(foundry, "Correction", URL, text).proposals[0])
    assert p.risk in ("high", "critical") and p.status == "pending"


def test_local_failure_escalates_once_then_stops(foundry):
    bad = draft(values={"single": 1, "mfj": 2, "mfs": 3, "hoh": 4})
    foundry.router = FakeRouter({("draft", False): bad, ("draft", True): draft()})
    p = foundry.load(ingest_document(foundry, "Rev. Proc. 2026-41", URL, REV_PROC).proposals[0])
    assert foundry.router.calls == [("draft", False), ("draft", True)]
    assert p.verified


def test_prefilter_drops_paperwork_noise(home):
    kb = KnowledgeBase(home / "rules")
    noise = Document(key="fr:1", source="fr", title="Agency Information Collection Activities; Comment Request on Form 8881", url="x")
    signal = Document(key="fr:2", source="fr", title="Inflation adjustments for 2027", url="x", abstract="standard deduction")
    assert prefilter(noise, kb)[0] is False and prefilter(signal, kb)[0] is True


def test_staleness_knows_what_is_due(home):
    alerts = scan(KnowledgeBase(home / "rules"), date(2026, 10, 8))
    by = {(a["rule_id"], a.get("tax_year")): a for a in alerts}
    assert by[("us_fed.payroll.social_security_wage_base", 2027)]["status"] == "due_soon"
    assert by[("us_fed.individual.standard_deduction", 2027)]["status"] == "due_soon"
    assert any(a["status"] == "sunset" and a["rule_id"] == "us_fed.excise.motor_fuel_rate" for a in alerts)
    late = scan(KnowledgeBase(home / "rules"), date(2026, 12, 1))
    assert {(a["rule_id"], a["status"]) for a in late} >= {("us_fed.individual.standard_deduction", "overdue")}


def test_protected_paths_block_auto_merge(foundry):
    from veritas.foundry.core import Check, Proposal

    p = Proposal(kind="code_change", agent="engineer", title="tweak guardrail", summary="", risk="low",
                 payload={"touches_protected": True}, checks=[Check(name="tests", ok=True)])
    assert foundry.submit(p).status == "pending"
