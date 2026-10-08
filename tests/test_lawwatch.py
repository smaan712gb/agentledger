"""Continuous compliance: state and legislation watchers, IRS form revisions, coverage flags and release gating."""

import json
from datetime import date

import pytest

from veritas import coverage
from veritas.foundry.agents import lawwatch
from veritas.regwatch import states as st
from veritas.regwatch.documents import Document
from veritas.release import classify


def test_registry_covers_all_states_and_dc(home):
    js = st.load_jurisdictions(home)
    assert len(js) == 51 and {"CA", "NY", "TX", "DC", "WY"} <= {j["code"] for j in js}
    assert all(st.primary(j) for j in js)
    assert "ftb.ca.gov" in st.state_domains(home) and "tax.ny.gov" in st.state_domains(home)


def test_state_watch_rotates_and_alerts_without_page(foundry, monkeypatch):
    calls = []
    monkeypatch.setattr(st, "discover_news_pages", lambda url: [] if "alabama" in url else [url + "news"])
    monkeypatch.setattr(st, "page_items", lambda url, source, keywords=None: calls.append((url, source)) or iter(()))
    rec = foundry.run("state-watch")
    assert rec["ok"], rec
    mem = json.loads((foundry.paths.state / "state_watch.json").read_text())
    first = set(mem)
    assert len(first) == 8
    assert any(a["type"] == "state_watch_no_page" and a["state"] == "AL" for a in rec["alerts"])
    rec = foundry.run("state-watch")
    mem = json.loads((foundry.paths.state / "state_watch.json").read_text())
    assert len(mem) == 16 and first < set(mem)          # the next run takes the next least-recently-checked states
    assert all(src.startswith("state-") for _, src in calls)


def test_enacted_law_flags_coverage_and_reports_missing_keys(foundry, monkeypatch):
    def tx_laws(code, since):
        if code == "TX":
            yield Document(key="openstates:tx-1", source="openstates-tx", title="TX HB 9: relating to the franchise tax for 2027",
                           url="https://capitol.texas.gov/BillLookup/History.aspx?LegSess=89R&Bill=HB9", published="2026-09-30",
                           doc_type="Enacted state law")

    def no_key(since):
        raise RuntimeError("DATA_GOV_API_KEY not set")

    monkeypatch.setattr(st, "openstates_enacted", tx_laws)
    monkeypatch.setattr(st, "govinfo_public_laws", no_key)
    rec = foundry.run("legislation-watch")
    assert any(a["type"] == "law_enacted" and a["jurisdiction"] == "US-TX" and a["years"] == [2027] for a in rec["alerts"])
    assert any(a["type"] == "source_unavailable" for a in rec["alerts"])
    flags = coverage.active_flags(foundry.paths.root, jurisdiction="US-TX", year=2027)
    assert len(flags) == 1 and "franchise tax" in flags[0]["reason"]
    rec = foundry.run("legislation-watch")                      # the same law is not flagged twice
    assert len(coverage.active_flags(foundry.paths.root, jurisdiction="US-TX")) == 1


def test_irs_form_revision_detected_after_baseline(foundry, monkeypatch):
    version = {"n": 1}

    class R:
        def __init__(self, url):
            self.status_code = 200 if "f1040s1a" in url and "dft" in url else 404
            self.content = b"%PDF-1.7 schedule 1-A v" + str(version["n"]).encode()

    monkeypatch.setattr(lawwatch.httpx, "get", lambda url, **kw: R(url))
    monkeypatch.setattr(lawwatch, "_form_year", lambda pdf: 2026)
    rec = foundry.run("irs-forms-watch")
    assert rec["stats"]["changed"] == 0                          # first sighting is the baseline
    version["n"] = 2
    rec = foundry.run("irs-forms-watch")
    assert rec["stats"]["changed"] == 1
    alert = next(a for a in rec["alerts"] if a["type"] == "irs_form_changed")
    assert alert["form"] == "sch_1a" and alert["year"] == 2026
    assert coverage.active_flags(foundry.paths.root, jurisdiction="US-FED", year=2026)[0]["form"] == "sch_1a"


def test_flags_block_filing_until_owner_clears_with_evidence(foundry):
    from test_return_workflow import add_doc, household

    from veritas.ledger import store
    from veritas.returns.store import Returns

    store.add_client(foundry.conn, id="rivera", name="Rivera", kind="individual", emails=[], tax_id_last4="0001", domain="general",
                     facts={"taxpayer_ssn_last4": "0001"})
    add_doc(foundry.conn, "d1", "rivera", "W-2", {"box1": "60000", "box2": "6000"})
    R = Returns(foundry.conn, foundry.kb)
    rid = R.create("rivera", 2026, "maya", {**household(), "filing_status": "single", "spouse": None})
    f = coverage.flag(foundry.paths.root, jurisdiction="US-FED", years=[2026], reason="P.L. 119-99 changes the 2026 brackets",
                      source="https://www.govinfo.gov/app/details/PLAW-119publ99", raised_by="legislation-watch")
    R.populate_from_documents(rid, "maya")
    cov = R.latest(rid)["result"]["coverage"]
    assert any(b.get("flag") == f["id"] for b in cov["filing_blockers"])
    with pytest.raises(ValueError):
        coverage.clear_flag(foundry.paths.root, f["id"], by="owner", note="done", evidence=[])
    coverage.clear_flag(foundry.paths.root, f["id"], by="owner", note="brackets updated",
                        evidence=["tests/test_returns_1040.py::test_rate_schedule_reproduces_published_tables", "commit abc123"])
    R.compute(rid, "maya")
    assert not any(b.get("flag") for b in R.latest(rid)["result"]["coverage"]["filing_blockers"])


def _adopted(home, pid, mode, risk):
    d = home / "state" / "proposals"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps({"id": pid, "kind": "rule_change", "status": "adopted", "risk": risk,
                                               "decision": {"mode": mode}}), encoding="utf-8")


def test_release_classification(home):
    _adopted(home, "p1", "auto", "low")
    d = classify(home, ["rules/us-fed/individual/us_fed.individual.tax_brackets.yaml", "rules/CHANGELOG.md"], today=date(2026, 11, 20))
    assert d.verdict == "auto" and d.categories == ["routine_indexed_value"]
    d = classify(home, ["rules/x.yaml", "src/veritas/db.py"], today=date(2026, 11, 20))
    assert d.verdict == "review" and any("protected" in r for r in d.reasons)
    d = classify(home, ["coverage/coverage.yaml"], today=date(2026, 11, 20))
    assert d.verdict == "review"
    d = classify(home, ["rules/x.yaml"], today=date(2027, 4, 14))
    assert d.verdict == "review" and any("freeze" in r for r in d.reasons)
    _adopted(home, "p2", "human", "medium")
    d = classify(home, ["rules/x.yaml"], today=date(2026, 11, 20))
    assert d.verdict == "review" and any("p2" in r for r in d.reasons)
