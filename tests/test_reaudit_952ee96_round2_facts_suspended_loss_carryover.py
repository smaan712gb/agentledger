"""Second adversarial review of the 952ee96 re-audit fixes, carryovers: a suspended rental loss carried forward
(`prior_year_unallowed_loss`) matched neither "*_carryover" nor "*_carryforward", so no carryover was recorded and the
years it comes from were not kept open. Asserted now: carryovers are an explicit list (store.CARRYOVER_INPUTS, plus an
NOL on Schedule 1 line 8a) recorded for every version in return_retention_facts (kind 'carryover'), and every model
field whose name says carryover, carryforward, prior_year or unallowed is in that list.
"""

from __future__ import annotations

import typing

import pytest
from pydantic import BaseModel
from test_return_workflow import fam, household  # noqa: F401  (fam is a fixture)

from agentledger.returns.model import IndividualReturn
from agentledger.returns.store import CARRYOVER_INPUTS, Returns, carryovers

RENTAL = {"address": "12 Shore Rd", "rents": "18000", "expenses": {"repairs": "2000"}, "depreciation": "6000"}
WORDS = ("carryover", "carryforward", "prior_year", "unallowed")


def _recorded(fam, rid):  # noqa: F811
    rows = fam.conn.execute("SELECT version, detail FROM return_retention_facts WHERE return_id = ? AND kind = 'carryover' "
                            "ORDER BY version", (rid,)).fetchall()
    return [(r["version"], r["detail"]) for r in rows]


def _versions(fam, rid):  # noqa: F811
    return [r["version"] for r in fam.conn.execute("SELECT version FROM tax_return_versions WHERE return_id = ? ORDER BY version",
                                                   (rid,)).fetchall()]


@pytest.mark.parametrize("inputs,carried", [
    ({"rentals": [{**RENTAL, "prior_year_unallowed_loss": "9000"}]}, "rentals[0].prior_year_unallowed_loss"),
    ({"other_income": {"8a": "-12000"}}, "other_income.8a"),
    ({"other_income": {"a": "-12000"}}, "other_income.a"),
], ids=["suspended-rental-loss", "nol-line-8a", "nol-labelled-a"])
def test_a_carryover_used_is_recorded_for_every_version(fam, inputs, carried):  # noqa: F811
    full = {**household(), **inputs}
    assert carryovers(full) == [carried]
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", full)
    R.compute(rid, "maya")
    assert _recorded(fam, rid) == [(v, carried) for v in _versions(fam, rid)] and len(_versions(fam, rid)) == 2


def test_a_return_without_a_carryover_records_none(fam):  # noqa: F811
    full = {**household(), "rentals": [RENTAL], "other_income": {"8z": "500"}}     # other income, not an NOL
    assert carryovers(full) == []
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", full)
    R.compute(rid, "maya")
    assert _recorded(fam, rid) == []


def _fields(model: type[BaseModel], seen: set[type] | None = None) -> set[str]:
    """Every field name of the return model, at any depth."""
    seen = seen if seen is not None else set()
    if model in seen:
        return set()
    seen.add(model)
    out = set(model.model_fields)
    for f in model.model_fields.values():
        stack = [f.annotation]
        while stack:
            ann = stack.pop()
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                out |= _fields(ann, seen)
            stack.extend(typing.get_args(ann))
    return out


def test_every_carryover_field_of_the_model_is_recorded():
    names = _fields(IndividualReturn)
    named_so = {n for n in names if any(w in n for w in WORDS)}
    assert "prior_year_unallowed_loss" in named_so and "charity_carryover" in named_so      # the walk reaches nested models
    assert named_so <= CARRYOVER_INPUTS, f"carryover-like inputs not recorded as carryovers: {sorted(named_so - CARRYOVER_INPUTS)}"
    assert CARRYOVER_INPUTS <= names, f"CARRYOVER_INPUTS names no model field: {sorted(CARRYOVER_INPUTS - names)}"
