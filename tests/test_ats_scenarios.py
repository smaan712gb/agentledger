"""ATS scenario harness (backlog T2-04): the IRS Assurance Testing System scenarios as independent fixtures for the 1040 engine.

Each fixture (tests/fixtures/ats/scenario-NN.json, written by scripts/ats_fixtures.py from the IRS scenario PDFs) pins the
engine's outcome on one scenario: a line that matches must keep matching, and each known gap (a line that differs, or that
the engine cannot produce) must stay exactly as recorded, so that a gap closing silently fails here and the fixture is
re-recorded deliberately (python -I scripts/ats_fixtures.py --record). The fixtures are JSON, so these tests need neither the
PDFs (kept untracked in docs/irs) nor a store: the engine runs alone, as in tests/test_returns_1040.py."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from agentledger.returns import ats
from agentledger.returns.model import IndividualReturn

REPO = Path(__file__).resolve().parent.parent
FIXTURES = sorted((REPO / "tests" / "fixtures" / "ats").glob("scenario-*.json"))
PDF_DIR = REPO / "docs" / "irs" / "ats-ty2026"
SCENARIOS = {1, 2, 3, 4, 5, 6, 7, 12, 13, 14}   # the TY2026 scenarios downloaded on 2026-10-08 (docs/ATS.md)
ROLES = {"result", "input", "ambiguous", "info"}


def _ids(path: Path) -> str:
    return path.stem


def test_every_downloaded_scenario_has_a_fixture():
    assert {ats.load(p)["scenario"] for p in FIXTURES} == SCENARIOS


@pytest.mark.parametrize("path", FIXTURES, ids=_ids)
def test_scenario_outcome_is_as_recorded(path):
    fixture = ats.load(path)
    report = ats.run(fixture)
    problems = ats.check(report, fixture.get("recorded"))
    assert not problems, "\n".join(problems)
    assert not report.by_status("echo_mismatch"), "an input of the fixture no longer maps onto the engine"
    # Every gap the engine shows is explained: a blocked line by its reason, a differing line by a reviewed cause.
    assert all(o.reason for o in report.by_status("blocked"))
    assert all(o.cause for o in report.by_status("differs")), [o.key for o in report.by_status("differs") if not o.cause]


@pytest.mark.parametrize("path", FIXTURES, ids=_ids)
def test_fixture_is_well_formed(path):
    fixture = ats.load(path)
    pages = fixture["source"]["page_count"]
    assert len(fixture["source"]["sha256"]) == 64 and fixture["source"]["extracted_on"]
    assert "not real people" in fixture["identities"]
    if fixture["return"] is not None:
        IndividualReturn.model_validate(fixture["return"])
    keys = [ats.line_key(e) for e in fixture["expected"]]
    assert len(keys) == len(set(keys)), "two expected entries share a line key"
    for e in fixture["expected"]:
        assert 1 <= e["page"] <= pages and e["role"] in ROLES and isinstance(e["value"], int)
        if e["role"] == "input":
            assert e["engine"] is not None, f"{ats.line_key(e)} is an input on a form the engine does not compute"
    for item in fixture["provenance"] + fixture["unmodelled"] + fixture["ambiguities"]:
        assert 1 <= item["page"] <= pages
    for a in fixture["assumptions"]:
        assert a["reason"] and a["path"]
    # An assumption the model forces must not decide a scored line: its alternatives change none of them.
    assert all(not changed for changed in fixture["recorded"]["sensitivity"].values()), fixture["recorded"]["sensitivity"]


def test_a_closed_gap_and_a_regression_are_both_reported():
    fixture = ats.load(next(p for p in FIXTURES if ats.load(p)["scenario"] == 14))
    report = ats.run(fixture)
    recorded = json.loads(json.dumps(fixture["recorded"]))
    gap_key = next(iter(recorded["known_gaps"]))
    match_key = recorded["matched"][0]
    recorded["matched"].remove(match_key)
    recorded["known_gaps"][match_key] = {"status": "differs", "expected": 1, "engine": 2}
    del recorded["known_gaps"][gap_key]
    recorded["matched"].append(gap_key)
    problems = ats.check(report, recorded)
    assert any(p.startswith(f"{match_key}: the gap closed") for p in problems)
    assert any(p.startswith(f"{gap_key}: regression") for p in problems)


def test_engine_keys_record_both_names_where_they_differ():
    assert ats.engine_key("sch_d", "1a", "d") == {"form": "sch_d", "line": "1ad"}
    assert ats.engine_key("sch_8812", "4") == {"form": "sch_8812", "fact": "4"}
    assert ats.engine_key("sch_c", "28", instance="1") == {"form": "sch_c[1]", "line": "28"}
    assert ats.engine_key("sch_se", "12", instance="spouse") == {"form": "sch_se[spouse]", "line": "12"}
    assert ats.engine_key("sch_f", "34") is None


def test_fixtures_reproduce_from_the_pdfs():
    """Where the owner keeps the scenario PDFs, a fresh extraction gives the committed fixtures (the recorded outcome and the
    extraction date aside). CI has no docs/irs: the JSON fixtures above stand on their own."""
    pdfs = sorted(PDF_DIR.glob("*.pdf")) if PDF_DIR.is_dir() else []
    if not pdfs:
        pytest.skip(f"no ATS scenario PDFs in {PDF_DIR} (untracked; downloaded by the owner): the JSON fixtures are tested above")
    spec = importlib.util.spec_from_file_location("ats_fixtures", REPO / "scripts" / "ats_fixtures.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["ats_fixtures"] = module   # its dataclasses resolve their annotations through sys.modules
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop("ats_fixtures", None)
        raise
    for number, pdf in sorted(module.scenario_files().items()):
        committed = REPO / "tests" / "fixtures" / "ats" / f"scenario-{number:02d}.json"
        assert committed.exists(), f"{pdf.name} has no fixture: run python -I scripts/ats_fixtures.py"
        fresh = module.build(module.extract(pdf, number))
        assert module._comparable(fresh) == module._comparable(ats.load(committed)), (
            f"{pdf.name} no longer gives {committed.name}: regenerate with python -I scripts/ats_fixtures.py and review the diff")
