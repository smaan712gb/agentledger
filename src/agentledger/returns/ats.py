"""ATS scenario harness (backlog T2-04): the IRS Assurance Testing System scenarios as independent fixtures for the 1040 engine.

`scripts/ats_fixtures.py` turns each TY2026 Form 1040 ATS scenario PDF (Publication 1436) into a JSON fixture under
tests/fixtures/ats: the facts the scenario states in the IndividualReturn shape, the facts the model cannot hold, the
assumptions the model forces, and every amount the scenario's forms print on a numbered line. This module runs a fixture
through `compute_individual` with the project's rules and reports, line by line:

- `matched`: the engine computes the amount the scenario prints (`absent` when the engine leaves the line out and the
  scenario prints zero, which is the engine's zero by `Result.line`);
- `differs`: the engine computes the line and gets another amount (both are reported);
- `blocked`: the engine cannot produce the line for this return: the form is not modelled (with its coverage status), the
  line is one the engine never computes, or the return raises blocking diagnostics (their codes are reported);
- `echo`: the scenario's amount is an input the fixture feeds to the engine (a Schedule C expense, a Schedule A entry); it
  is compared only to prove the mapping, never counted as a match (`input_unshown` when the engine takes the input but
  shows no such form for the return, as Schedule A when the standard deduction is larger);
- `ambiguous`: the scenario's own figures contradict each other on this line (see the fixture's `ambiguities`); reported,
  not scored.

Nothing is corrected: an engine amount is never replaced by the scenario's, and the scenario's never by the engine's. A
scenario's figures are not necessarily right either: the ATS scenarios exist to test e-file transmission, so a difference is
a finding for the tax-content owner, not proof of an engine error. The outcome recorded in a fixture (`recorded`) pins today's
state: `check` reports a match that regresses, a gap that closes without the fixture being re-recorded, and lines that were
never recorded.
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..calc.engine import Ctx
from ..kb.store import KnowledgeBase
from .individual import Result, compute_individual
from .model import IndividualReturn

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "fixtures" / "ats"
RULES = REPO / "rules"
TAX_YEAR = 2026

# Scenario form ids (scripts/ats_fixtures.py) that the engine computes, and the engine's form key for each. "{n}" is the
# 1-based instance (the first Schedule C is sch_c[1]); "{owner}" the person a Schedule SE belongs to.
ENGINE_FORMS: dict[str, str] = {
    "f1040": "f1040", "sch_1": "sch_1", "sch_1a": "sch_1a", "sch_2": "sch_2", "sch_3": "sch_3", "sch_3a": "sch_3a",
    "sch_8812": "sch_8812", "sch_a": "sch_a", "sch_b": "sch_b", "sch_c": "sch_c[{n}]", "sch_d": "sch_d", "sch_e": "sch_e",
    "sch_se": "sch_se[{owner}]", "f2441": "f2441", "f8863": "f8863", "f8995": "f8995", "f6251": "f6251", "f8959": "f8959",
    "f8960": "f8960",
}
# Scenario forms the engine does not compute: the coverage registry id (coverage/coverage.yaml) when it lists one, and why.
NOT_MODELLED: dict[str, tuple[str | None, str]] = {
    "sch_f": ("sch_f", "Schedule F (profit or loss from farming) is not supported"),
    "sch_h": (None, "Schedule H is not computed: household employment taxes are an entered amount (household_employment_taxes)"),
    "sch_eic": (None, "Schedule EIC lists the qualifying children; the engine keeps them as facts, not lines"),
    "f4835": (None, "Form 4835 (farm rental income) is not modelled"),
    "f5695": (None, "Form 5695 (residential energy credits) is not modelled"),
    "f8888": (None, "Form 8888 (allocation of refund) is not modelled"),
    "f8283": (None, "Form 8283 (noncash charitable contributions) is not modelled"),
    "f8862": (None, "Form 8862 (claiming credits after disallowance) is not modelled"),
    "f3800": (None, "Form 3800 (general business credit) is not modelled"),
    "f3800_sch_a": (None, "Schedule A (Form 3800), the transfer election statement, is not modelled"),
    "f4136": (None, "Form 4136 (credit for federal tax paid on fuels) is not modelled"),
    "f4136_sch_a": (None, "Schedule A (Form 4136) is not modelled"),
    "f1062": (None, "Form 1062 (deferral of tax on the sale of qualified farmland, IRC §1062) is not modelled"),
    "f1062_sch_a": (None, "Schedule A (Form 1062) is not modelled"),
    "f4562b": (None, "Form 4562-B (amortization) is not modelled"),
    "f7205": (None, "Form 7205 (energy efficient commercial buildings deduction) is not modelled"),
    "f7207": (None, "Form 7207 (advanced manufacturing production credit) is not modelled"),
    "f7220": (None, "Form 7220 (prevailing wage and apprenticeship verification) is not modelled"),
    "f4868": (None, "Form 4868 (extension of time to file) is not modelled (backlog T2-05)"),
    "w2g": (None, "Form W-2G has no input of its own; winnings enter as Schedule 1 line 8b"),
}
# Lines of computed forms that the engine never produces, with the form or fact they need.
NOT_COMPUTED: dict[tuple[str, str], str] = {
    ("f1040", "24b"): "Form 1062 (IRC §1062 deferral) is not modelled",
    ("f1040", "30"): "Form 8839 (adoption credit) is not modelled",
    ("f1040", "38"): "Form 2210 (estimated tax penalty) is not computed",
    ("sch_1", "6"): "Schedule F (farm income) is not supported",
    ("sch_1", "14"): "moving expenses (members of the Armed Forces, Form 3903) are not modelled",
    ("sch_2", "9"): "Schedule H is not computed (household employment taxes are an entered amount)",
    ("sch_3", "5a"): "Form 5695 (residential clean energy credit) is not modelled",
    ("sch_3", "5b"): "Form 5695 (energy efficient home improvement credit) is not modelled",
    ("sch_3", "6a"): "Form 3800 (general business credit) is not modelled",
    ("sch_3", "12"): "Form 4136 (fuel tax credit) is not modelled",
    ("sch_3", "13e"): "Form 1062 (section 1062 net tax liability) is not modelled",
    ("sch_3", "13z"): "other payments and refundable credits are not modelled",
}
SCHEDULE_D_COLUMN_LINES = {"1a", "1b", "2", "3", "8a", "8b", "9", "10"}
COUNT_LINES = {("sch_8812", "4"), ("sch_8812", "6")}   # counts the engine keeps as facts, not amounts


def engine_key(form: str, line: str, column: str | None = None, instance: str | None = None) -> dict[str, str] | None:
    """Where the engine keeps a scenario line: {"form", "line"} or {"form", "fact"}; None when the engine does not compute
    the form (NOT_MODELLED). Schedule D columns are part of the engine's line key ("1a" column d is "1ad")."""
    if form not in ENGINE_FORMS:
        return None
    key = ENGINE_FORMS[form]
    if "{n}" in key:
        key = key.replace("{n}", instance or "1")
    if "{owner}" in key:
        key = key.replace("{owner}", instance or "taxpayer")
    if (form, line) in COUNT_LINES:
        return {"form": key, "fact": line}
    if form == "sch_d" and line in SCHEDULE_D_COLUMN_LINES and column:
        return {"form": key, "line": f"{line}{column}"}
    if column:
        return {"form": key, "line": f"{line}.{column}"}
    return {"form": key, "line": line}


def line_key(entry: dict[str, Any]) -> str:
    """The stable name of a scenario line in reports and recorded outcomes: form[#instance]:line[.column][~inline]."""
    form = entry["form"] + (f"#{entry['instance']}" if entry.get("instance") else "")
    line = entry["line"] + (f".{entry['column']}" if entry.get("column") else "")
    return f"{form}:{line}" + ("~inline" if entry.get("inline") else "")


def default_ctx() -> Ctx:
    return Ctx(KnowledgeBase(RULES))


def fixture_paths(directory: Path = FIXTURES) -> list[Path]:
    return sorted(directory.glob("scenario-*.json"))


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class LineOutcome:
    key: str
    form: str
    line: str
    page: int
    expected: int
    engine: int | None          # the engine's amount (None when the engine has no such form or line)
    status: str                 # matched, differs, blocked, echo, echo_mismatch, input_unshown, ambiguous, info
    role: str                   # result, input, ambiguous, info
    reason: str | None = None   # for blocked: form_not_modelled, line_not_computed, blocking_diagnostics
    detail: str | None = None
    codes: list[str] = field(default_factory=list)
    absent: bool = False        # the engine did not produce the line (its value is zero by Result.line)
    cause: str | None = None    # the reviewed explanation of a gap (the fixture's gap_causes); never compared

    def gap(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status, "expected": self.expected, "engine": self.engine}
        if self.reason:
            out["reason"] = self.reason
        if self.codes:
            out["codes"] = self.codes
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class ScenarioReport:
    scenario: int
    title: str
    computed: bool
    lines: list[LineOutcome]
    blocking: list[str]                      # codes of the engine's error diagnostics for the return
    warnings: list[str]                      # codes of its warnings
    unmodelled: list[dict[str, Any]]
    assumptions: list[dict[str, Any]]
    sensitivity: dict[str, list[str]]        # assumption path -> scored lines whose engine amount changes under an alternative
    engine_summary: dict[str, str] = field(default_factory=dict)

    def by_status(self, status: str) -> list[LineOutcome]:
        return [o for o in self.lines if o.status == status]

    def counts(self) -> dict[str, int]:
        c = Counter(o.status for o in self.lines)
        return {s: c.get(s, 0) for s in ("matched", "differs", "blocked", "echo", "echo_mismatch", "input_unshown", "ambiguous",
                                         "info")}

    def top_codes(self, n: int = 5) -> list[tuple[str, int]]:
        c: Counter[str] = Counter()
        for o in self.lines:
            if o.status == "blocked":
                c.update(o.codes or [o.reason or "?"])
        return c.most_common(n)

    def recorded(self) -> dict[str, Any]:
        """The outcome to pin in the fixture: every scored line's status, the gaps with both amounts and their reasons."""
        return {
            "matched": sorted(o.key for o in self.lines if o.status == "matched"),
            "known_gaps": {o.key: o.gap() for o in sorted(self.lines, key=lambda o: o.key) if o.status in ("differs", "blocked")},
            "echo": sorted(o.key for o in self.lines if o.status == "echo"),
            "input_unshown": sorted(o.key for o in self.lines if o.status == "input_unshown"),
            "ambiguous": sorted(o.key for o in self.lines if o.status == "ambiguous"),
            "blocking": self.blocking,
            "sensitivity": self.sensitivity,
            "engine_summary": self.engine_summary,
        }


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    parts = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    cur: Any = data
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part.startswith("["):
            idx = int(part[1:-1])
            if last:
                cur[idx] = value
            else:
                cur = cur[idx]
        elif last:
            cur[part] = value
        else:
            cur = cur.setdefault(part, {})


def _amount(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(Decimal(str(value)))
    except ArithmeticError:
        return None


def engine_value(result: Result, where: dict[str, str]) -> tuple[int | None, bool]:
    """(amount, produced): the engine's amount for a line or a fact, and whether the engine produced it at all."""
    if "fact" in where:
        facts = result.sheets.facts.get(where["form"], {})
        if where["fact"] in facts:
            return _amount(facts[where["fact"]]), True
        return None, False
    lines = result.forms.get(where["form"], {})
    if where["line"] in lines:
        return int(lines[where["line"]]), True
    return 0, False


def _coverage_status(form: str) -> str:
    registry_id, _ = NOT_MODELLED.get(form, (None, ""))
    if registry_id is None:
        return "not in the coverage registry"
    try:
        from ..coverage import lookup

        return str(lookup(registry_id, TAX_YEAR)["status"])
    except Exception:  # the registry is informational here
        return "unknown"


def _outcome(entry: dict[str, Any], result: Result | None, blocking: list[str]) -> LineOutcome:
    key = line_key(entry)
    base = dict(key=key, form=entry["form"], line=entry["line"], page=entry["page"], expected=int(entry["value"]))
    role = entry.get("role", "result")
    where = entry.get("engine")
    if result is None:
        _, why = NOT_MODELLED.get(entry["form"], (None, "the scenario has no Form 1040 for the engine to compute"))
        return LineOutcome(**base, engine=None, status="blocked", role=role, reason="form_not_modelled",
                           detail=f"{why} (coverage: {_coverage_status(entry['form'])}); the scenario has no Form 1040")
    if role == "info":
        value, produced = engine_value(result, where) if where else (None, False)
        return LineOutcome(**base, engine=value if produced else None, status="info", role=role)
    if where is None:
        _, why = NOT_MODELLED.get(entry["form"], (None, f"{entry['form']} is not modelled"))
        status = "blocked" if role == "result" else role
        if role == "input":
            status = "echo_mismatch"   # an input must map onto a modelled form
        return LineOutcome(**base, engine=None, status=status, role=role, reason="form_not_modelled",
                           detail=f"{why} (coverage: {_coverage_status(entry['form'])})")
    value, produced = engine_value(result, where)
    if role == "input":
        if not produced and where.get("form") not in result.forms and value != base["expected"]:
            return LineOutcome(**base, engine=None, status="input_unshown", role=role, absent=True,
                               detail=f"the engine shows no {where['form']} for this return")
        return LineOutcome(**base, engine=value, status="echo" if value == base["expected"] else "echo_mismatch", role=role,
                           absent=not produced)
    if role == "ambiguous":
        return LineOutcome(**base, engine=value if produced else None, status="ambiguous", role=role, absent=not produced,
                           detail=entry.get("note"))
    if value == base["expected"]:
        return LineOutcome(**base, engine=value, status="matched", role=role, absent=not produced)
    not_computed = NOT_COMPUTED.get((entry["form"], entry["line"]))
    if not produced and not_computed:
        return LineOutcome(**base, engine=None, status="blocked", role=role, reason="line_not_computed", detail=not_computed,
                           absent=True)
    if blocking:
        return LineOutcome(**base, engine=value if produced else None, status="blocked", role=role,
                           reason="blocking_diagnostics", codes=blocking, absent=not produced)
    return LineOutcome(**base, engine=value, status="differs", role=role, absent=not produced,
                       detail=not_computed)


def compute(fixture: dict[str, Any], ctx: Ctx | None = None) -> Result | None:
    if fixture.get("return") is None:
        return None
    return compute_individual(ctx or default_ctx(), IndividualReturn.model_validate(fixture["return"]))


def run(fixture: dict[str, Any], ctx: Ctx | None = None) -> ScenarioReport:
    """The scenario's outcome on today's engine and rules. Never edits the fixture."""
    ctx = ctx or default_ctx()
    result = compute(fixture, ctx)
    blocking = sorted({d.code for d in result.blocking}) if result else []
    warnings = sorted({d.code for d in result.diagnostics if d.severity == "warning"}) if result else []
    lines = [_outcome(e, result, blocking) for e in fixture.get("expected", [])]
    causes = [(re.compile(c["lines"]), c["cause"]) for c in fixture.get("gap_causes", [])]
    for o in lines:
        if o.status in ("differs", "blocked"):
            o.cause = next((text for pattern, text in causes if pattern.fullmatch(o.key)), None)
    sensitivity: dict[str, list[str]] = {}
    if result is not None:
        scored = [(line_key(e), e["engine"]) for e in fixture.get("expected", [])
                  if e.get("engine") and e.get("role", "result") == "result"]
        for a in fixture.get("assumptions", []):
            changed: set[str] = set()
            for alternative in a.get("alternatives", []):
                variant = copy.deepcopy(fixture)
                _set_path(variant["return"], a["path"], alternative)
                other = compute(variant, ctx)
                assert other is not None
                for key, where in scored:
                    if engine_value(result, where) != engine_value(other, where):
                        changed.add(key)
            if a.get("alternatives"):
                sensitivity[a["path"]] = sorted(changed)
    summary = {}
    if result is not None:
        summary = {k: str(v) for k, v in result.to_dict()["summary"].items()}
    return ScenarioReport(fixture["scenario"], fixture.get("title", ""), result is not None, lines, blocking, warnings,
                          fixture.get("unmodelled", []), fixture.get("assumptions", []), sensitivity, summary)


def check(report: ScenarioReport, recorded: dict[str, Any] | None) -> list[str]:
    """Differences between today's outcome and the one recorded in the fixture: a regression (a recorded match that no
    longer matches), a closed or changed gap (re-record the fixture deliberately), an input that no longer maps onto the
    engine, a line never recorded, a changed sensitivity to an assumption."""
    if not recorded:
        return [f"scenario {report.scenario}: no recorded outcome; run python -I scripts/ats_fixtures.py --record"]
    problems: list[str] = []
    matched = set(recorded.get("matched", []))
    gaps: dict[str, dict[str, Any]] = recorded.get("known_gaps", {})
    echo = set(recorded.get("echo", []))
    unshown = set(recorded.get("input_unshown", []))
    ambiguous = set(recorded.get("ambiguous", []))
    for o in report.lines:
        if o.status == "info":
            continue
        if o.status == "echo_mismatch":
            problems.append(f"{o.key}: the input mapping no longer reproduces the scenario's {o.expected} (engine {o.engine})")
        elif o.key in matched:
            if o.status != "matched":
                problems.append(f"{o.key}: regression, recorded as matched ({o.expected}) and now {o.status} (engine {o.engine})")
        elif o.key in gaps:
            was = gaps[o.key]
            if o.status == "matched":
                problems.append(f"{o.key}: the gap closed ({was['status']}, engine {was.get('engine')}) and now matches "
                                f"{o.expected}; re-record the fixture")
            elif o.gap() != was:
                problems.append(f"{o.key}: the gap changed from {was} to {o.gap()}; re-record the fixture")
        elif o.key in echo:
            if o.status != "echo":
                problems.append(f"{o.key}: recorded as an input echo and now {o.status}")
        elif o.key in unshown:
            if o.status != "input_unshown":
                problems.append(f"{o.key}: recorded as an input the engine does not show and now {o.status} (engine {o.engine})")
        elif o.key in ambiguous:
            if o.status != "ambiguous":
                problems.append(f"{o.key}: recorded as ambiguous and now {o.status}")
        else:
            problems.append(f"{o.key}: not in the recorded outcome (now {o.status}); re-record the fixture")
    if report.sensitivity != recorded.get("sensitivity", {}):
        problems.append(f"sensitivity to the assumptions changed: {recorded.get('sensitivity')} -> {report.sensitivity}")
    if report.blocking != recorded.get("blocking", []):
        problems.append(f"the return's blocking diagnostics changed: {recorded.get('blocking')} -> {report.blocking}; "
                        f"re-record the fixture")
    return problems


def table(reports: list[ScenarioReport], fixtures: list[dict[str, Any]]) -> str:
    """The per-scenario summary for docs/ATS.md (markdown)."""
    rows = ["| Scenario | Taxpayer | Forms in the scenario | Matched | Differ | Blocked | Inputs echoed (not shown) | Ambiguous | "
            "Blocked lines by reason | Engine errors on the return | Unmodelled facts |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r, f in zip(reports, fixtures):
        c = r.counts()
        absent = sum(1 for o in r.lines if o.status == "matched" and o.absent)
        top = ", ".join(f"`{code}` ({n})" for code, n in r.top_codes(3)) or "none"
        errors = ", ".join(f"`{code}`" for code in r.blocking) or "none"
        matched = f"{c['matched']}" + (f" ({absent} zero lines the engine leaves out)" if absent else "")
        inputs = f"{c['echo']}" + (f" ({c['input_unshown']})" if c["input_unshown"] else "")
        forms = ", ".join(f.get("forms_listed", []))
        rows.append(f"| {r.scenario} | {f.get('taxpayer', '')} | {forms} | {matched} | {c['differs']} | {c['blocked']} | {inputs} | "
                    f"{c['ambiguous']} | {top} | {errors} | {len(r.unmodelled)} |")
    return "\n".join(rows)
