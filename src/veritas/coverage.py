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
FORM_IDS = {"ws_qdcg": None, "ws_sch_d_tax": None, "ws_social_security": None}


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
