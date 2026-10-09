"""Flows: deterministic functions of their step results, run by the local runner or mirrored as Cloudflare Workflows.
Step names and timings come from steps.json, one source for both runtimes."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def plan() -> dict[str, Any]:
    return json.loads((Path(__file__).with_name("steps.json")).read_text(encoding="utf-8"))


def expand(schedule: list[dict[str, Any]]) -> list[float]:
    """[{every_s, times}, ...] -> the list of sleeps, in order."""
    out: list[float] = []
    for part in schedule:
        out += [float(part["every_s"])] * int(part["times"])
    return out
