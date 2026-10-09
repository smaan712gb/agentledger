"""Published coverage registry (spec §7, ADR-0008), enforced by the API and the return workflow."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

STATES = ("unsupported", "suspended", "manual-assisted", "planning-only", "preparation-validated", "export-validated",
          "filing-approved")
RANK = {"unsupported": 0, "suspended": 0, "planning-only": 1, "manual-assisted": 2, "preparation-validated": 3,
        "export-validated": 3, "filing-approved": 4}
ROOT = Path(__file__).resolve().parents[2]

# Engine form keys -> registry ids (instances such as "sch_c[1]" share their base id).
FORM_IDS = {"ws_qdcg": None, "ws_sch_d_tax": None, "ws_social_security": None, "ws_capital_loss_carryover": None,
            "ws_ira_deduction": None, "ws_roth_contribution": None}


@lru_cache(maxsize=4)
def load(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else ROOT / "coverage" / "coverage.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    for c in data["capabilities"]:
        if c["status"] not in STATES:
            raise ValueError(f"coverage {c['id']}: unknown status {c['status']}")
    return data


def lookup(cap_id: str, year: int, jurisdiction: str = "US-FED", path: str | None = None) -> dict[str, Any]:
    for c in load(path)["capabilities"]:
        if c["id"] == cap_id and c["jurisdiction"] == jurisdiction and year in c.get("years", []):
            return c
    return {"id": cap_id, "status": "unsupported", "jurisdiction": jurisdiction, "years": [year], "name": cap_id}


def form_id(engine_key: str) -> str | None:
    base = engine_key.split("[")[0]
    return FORM_IDS.get(base, base)


def check_forms(forms: list[str], year: int, *, need: str, path: str | None = None) -> list[dict[str, Any]]:
    """Every form below the required level, with its status and published limits."""
    out = []
    for key in forms:
        fid = form_id(key)
        if fid is None:
            continue
        c = lookup(fid, year, path=path)
        if RANK[c["status"]] < RANK[need]:
            out.append({"form": fid, "status": c["status"], "need": need, "limits": c.get("limits", [])})
    return out


# ------------------------------------------------------------------ coverage flags
# A flag says: "something changed that may make this jurisdiction/year/form wrong". It never stops preparation
# (the preparer sees a review warning) but it blocks filing until the tax-content owner clears it.

def _flags_path(root: Path) -> Path:
    return Path(root) / "state" / "coverage_flags.json"


def _read_flags(root: Path) -> list[dict[str, Any]]:
    import json

    p = _flags_path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def flag(root: Path, *, jurisdiction: str, years: list[int], reason: str, source: str, raised_by: str,
         form: str | None = None) -> dict[str, Any]:
    import hashlib
    import json
    from datetime import datetime, timezone

    fid = "cf_" + hashlib.sha1(f"{jurisdiction}|{form}|{sorted(years)}|{source}".encode()).hexdigest()[:10]
    flags = _read_flags(root)
    if any(x["id"] == fid for x in flags):
        return {"id": fid, "new": False}
    flags.append({"id": fid, "jurisdiction": jurisdiction, "form": form, "years": sorted(years), "reason": reason[:300],
                  "source": source, "raised_by": raised_by, "raised_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "cleared": None})
    p = _flags_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(flags, indent=1), encoding="utf-8")
    return {"id": fid, "new": True}


def active_flags(root: Path, *, jurisdiction: str | None = None, year: int | None = None) -> list[dict[str, Any]]:
    out = []
    for x in _read_flags(root):
        if x.get("cleared"):
            continue
        if jurisdiction and x["jurisdiction"] != jurisdiction:
            continue
        if year is not None and x["years"] and year not in x["years"]:
            continue
        out.append(x)
    return out


def clear_flag(root: Path, flag_id: str, *, by: str, note: str, evidence: list[str]) -> dict[str, Any]:
    """Only the tax-content owner clears a flag, with evidence that the change is implemented and tested."""
    import json
    from datetime import datetime, timezone

    if not note.strip() or not evidence:
        raise ValueError("clearing a coverage flag needs a note and evidence (tests, fixtures, commit)")
    flags = _read_flags(root)
    for x in flags:
        if x["id"] == flag_id:
            x["cleared"] = {"by": by, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "note": note,
                            "evidence": evidence}
            _flags_path(root).write_text(json.dumps(flags, indent=1), encoding="utf-8")
            return x
    raise KeyError(flag_id)
