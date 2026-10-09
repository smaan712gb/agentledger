"""The self-host profile files a return end to end through the API's scheduler tick (backlog F-08, slice 6):
AGENTLEDGER_ORCHESTRATOR=local runs the in-process runner over each firm's store; the mock transmitter stands in for
MeF in the local demo. The CLI shows and drives the same instances.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from test_internal_api import released
from typer.testing import CliRunner

from agentledger.db import count
from agentledger.workflow import runner as runner_mod
from agentledger.workflow.flows import expand, plan


@pytest.fixture
def api(home, monkeypatch):
    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))
    monkeypatch.setenv("AGENTLEDGER_AGENTS", "0")
    monkeypatch.setenv("AGENTLEDGER_DEV_AUTH", "1")
    monkeypatch.setenv("AGENTLEDGER_ORCHESTRATOR", "local")
    monkeypatch.setenv("AGENTLEDGER_MEF_PROVIDER", "mock")
    import agentledger.api.app as api_mod

    importlib.reload(api_mod)
    return api_mod


class MovableClock:
    def __init__(self):
        self.t = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

    def now(self):
        return self.t


def test_the_scheduler_tick_files_a_released_return(api, monkeypatch):
    clock = MovableClock()
    monkeypatch.setattr(runner_mod, "WallClock", lambda: clock)       # the scheduler's runner reads this clock
    R, rid, subs = released(api, monkeypatch, jurisdictions=("US-FED", "US-CA"))
    assert api.orchestrator() == "local"
    out = api.tick_workflows()                                        # relays submission.queued, transmits the federal return
    assert out["dev"]["relayed"] >= 1 and out["dev"]["advanced"] == 1
    assert R.status(rid).status == "transmitted"
    assert count(R.conn, "SELECT count(*) FROM workflow_runs") == 1    # the state instance waits for the federal acceptance
    clock.t += timedelta(seconds=expand(plan()["poll"]["schedule"])[0])
    api.tick_workflows()                                              # the acknowledgement: accepted
    assert R.status(rid).status == "accepted"
    api.tick_workflows()                                              # submission.accepted(US-FED): the state instance starts and transmits
    clock.t += timedelta(seconds=expand(plan()["poll"]["schedule"])[0])
    api.tick_workflows()
    api.tick_workflows()
    summary = R.filing_summary(rid)
    assert summary["complete"] is True and {s["jurisdiction"]: s["status"] for s in summary["submissions"]} == {"US-FED": "accepted", "US-CA": "accepted"}
    assert count(R.conn, "SELECT count(*) FROM workflow_runs WHERE status = 'complete'") == 2
    assert count(R.conn, "SELECT count(*) FROM outbox o LEFT JOIN outbox_delivery d ON d.outbox_id = o.id WHERE d.outbox_id IS NULL") == 0
    # The CLI reads the same store.
    from agentledger import cli

    wide = CliRunner(env={"COLUMNS": "240"})                           # rich would otherwise elide the table's cells
    res = wide.invoke(cli.app, ["workflows", "list"])
    assert res.exit_code == 0, res.output
    assert res.output.count("complete") == 2 and subs["US-FED"]["id"] in res.output
    res = wide.invoke(cli.app, ["workflows", "tick"])
    assert res.exit_code == 0 and "relayed 0 event(s), advanced 0 instance(s)" in res.output
    res = wide.invoke(cli.app, ["workflows", "signal", "filing-nope-1", "submission-reconciled", "--payload", json.dumps({})])
    assert res.exit_code == 1


def test_without_the_local_orchestrator_the_scheduler_leaves_filings_to_the_platform(api, monkeypatch):
    monkeypatch.setenv("AGENTLEDGER_ORCHESTRATOR", "")
    assert api.orchestrator() == ""
    R, rid, subs = released(api, monkeypatch, jurisdictions=("US-FED",))
    assert count(R.conn, "SELECT count(*) FROM outbox") >= 1 and count(R.conn, "SELECT count(*) FROM workflow_runs") == 0
