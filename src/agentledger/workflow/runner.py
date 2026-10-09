"""The in-process workflow runner (ADR-0003 §5): the same step and event interface as Cloudflare Workflows, for tests
and the self-host profile (AGENTLEDGER_ORCHESTRATOR=local).

An instance is a flow function plus its memoised step results. Running it means calling the flow from the start:
finished steps replay their recorded results, the first unfinished one runs; a sleep, a wait or a retry delay parks
the instance (`_Suspend`) with a wake time, and `tick()` resumes it when the clock says so. Nothing outside the step
results is relied on, so an instance may be re-run any number of times (as Cloudflare does after an eviction).

`tick(conn)` is also the relay: it consumes undelivered outbox events of the firm (starting instances, signalling
waits) exactly as edge/src/relay.ts does from the Worker, so the two deployments behave the same.

Faults, for tests, are injected by step name before the step runs: `Timeout` (the command never reaches the domain),
`CrashAfterCommit` (the domain commits, the reply is lost: the Q03 window), and the provider faults of bus.py.
Orchestration state is persisted in `workflow_runs` (DbRuns) or kept in memory (MemoryRuns); the domain never reads it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, NoReturn, Protocol

from .. import audit
from ..db import one, rows, unit_of_work
from . import outbox
from .bus import CommandBus, Fault, InProcessBus
from .steps import NonRetryable, RetryConfig, WaitTimeout, seconds

FEDERAL = "US-FED"


# ------------------------------------------------------------------------------------------------- clocks and faults
class FakeClock:
    """A clock tests move by hand; the runner never sleeps for real."""

    def __init__(self, start: datetime | None = None):
        self.t = start or datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.t

    def advance(self, secs: float) -> datetime:
        self.t += timedelta(seconds=secs)
        return self.t


class WallClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass
class Timeout(Fault):
    """The step's call never reaches the domain (a network timeout)."""


@dataclass
class CrashAfterCommit(Fault):
    """The domain commits, the reply is lost (the process crashed, the connection dropped)."""


# ------------------------------------------------------------------------------------------------- runs
@dataclass
class Run:
    instance_id: str
    flow: str
    params: dict[str, Any]
    steps: dict[str, Any] = field(default_factory=dict)
    status: str = "running"            # running | sleeping | waiting | complete | errored
    wake_at: str | None = None
    waiting_for: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_row(self) -> dict[str, Any]:
        return {"instance_id": self.instance_id, "flow": self.flow, "params": json.dumps(self.params, sort_keys=True),
                "steps": json.dumps(self.steps, sort_keys=True, default=str), "status": self.status, "wake_at": self.wake_at,
                "waiting_for": self.waiting_for, "events": json.dumps(self.events, default=str),
                "result": json.dumps(self.result, default=str) if self.result is not None else None, "error": self.error,
                "created_at": self.created_at, "updated_at": self.updated_at}

    @classmethod
    def from_row(cls, r: dict[str, Any]) -> "Run":
        return cls(r["instance_id"], r["flow"], json.loads(r["params"]), json.loads(r["steps"] or "{}"), r["status"], r["wake_at"],
                   r["waiting_for"], json.loads(r["events"] or "[]"), json.loads(r["result"]) if r["result"] else None, r["error"],
                   r["created_at"] or "", r["updated_at"] or "")


class Runs(Protocol):
    def get(self, instance_id: str) -> Run | None: ...
    def put(self, run: Run) -> None: ...
    def all(self) -> list[Run]: ...


class MemoryRuns:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    def get(self, instance_id: str) -> Run | None:
        return self._runs.get(instance_id)

    def put(self, run: Run) -> None:
        self._runs[run.instance_id] = run

    def all(self) -> list[Run]:
        return list(self._runs.values())


class DbRuns:
    """`workflow_runs` in the firm store (db.py on SQLite, migration 0006 on PostgreSQL)."""

    def __init__(self, conn: Any):
        self.conn = conn

    def get(self, instance_id: str) -> Run | None:
        r = one(self.conn, "SELECT * FROM workflow_runs WHERE instance_id = ?", instance_id)
        return Run.from_row(r) if r else None

    def put(self, run: Run) -> None:
        row = run.to_row()
        with unit_of_work(self.conn):
            if one(self.conn, "SELECT 1 AS x FROM workflow_runs WHERE instance_id = ?", run.instance_id):
                cols = [k for k in row if k not in ("instance_id", "created_at")]
                self.conn.execute(f"UPDATE workflow_runs SET {', '.join(f'{k} = ?' for k in cols)} WHERE instance_id = ?",
                                  (*(row[k] for k in cols), run.instance_id))
            else:
                self.conn.execute(f"INSERT INTO workflow_runs ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))

    def all(self) -> list[Run]:
        return [Run.from_row(r) for r in rows(self.conn, "SELECT * FROM workflow_runs ORDER BY created_at, instance_id")]


# ------------------------------------------------------------------------------------------------- the step context
class _Suspend(BaseException):
    """The flow parked itself (sleep, wait, retry delay); `tick` resumes it later. A BaseException, so a flow's own
    `except Exception` (its handling of a failed step) never swallows the suspension."""


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


class _StepContext:
    def __init__(self, runner: "LocalRunner", run: Run):
        self.runner, self.run = runner, run

    def _now(self) -> datetime:
        return self.runner.clock.now()

    def _park(self, status: str, until: datetime, waiting_for: str | None = None) -> NoReturn:
        self.run.status, self.run.wake_at, self.run.waiting_for = status, _iso(until), waiting_for
        raise _Suspend()

    def do(self, name: str, fn: Callable[[], Any], retries: RetryConfig | None = None) -> Any:
        rec = self.run.steps.get(name) or {}
        if rec.get("state") == "done":
            return rec["result"]
        if rec.get("state") == "failed":
            raise NonRetryable(rec.get("error") or name)
        if rec.get("state") == "exhausted":      # replays deterministically: the flow saw this failure already
            raise RuntimeError(f"{name}: retries exhausted: {rec.get('error')}")
        attempts = int(rec.get("attempts") or 0)
        fault = self.runner.take_fault(name)
        try:
            if isinstance(fault, Timeout):
                raise TimeoutError(f"{name}: the call timed out")
            if fault is not None and not isinstance(fault, CrashAfterCommit):
                self.runner.bus_inject(fault)
            result = fn()
            if isinstance(fault, CrashAfterCommit):
                raise ConnectionError(f"{name}: the reply was lost after the commit")
        except NonRetryable as e:
            self.run.steps[name] = {"state": "failed", "attempts": attempts + 1, "error": str(e)}
            self.runner.trace.append(name)
            raise
        except Exception as e:
            attempts += 1
            cfg = retries or RetryConfig()
            if attempts > cfg.limit:
                self.run.steps[name] = {"state": "exhausted", "attempts": attempts, "error": str(e)}
                self.runner.trace.append(name)
                raise
            self.run.steps[name] = {"state": "retrying", "attempts": attempts, "error": str(e)}
            self._park("sleeping", self._now() + timedelta(seconds=cfg.delay_for(attempts)))
        self.run.steps[name] = {"state": "done", "attempts": attempts + 1, "result": result}
        self.runner.trace.append(name)
        return result

    def sleep(self, name: str, duration: timedelta | float) -> None:
        rec = self.run.steps.get(name) or {}
        if rec.get("state") == "done":
            return
        if rec.get("state") == "sleeping":
            until = datetime.fromisoformat(rec["until"])
            if self._now() >= until:
                self.run.steps[name] = {"state": "done"}
                self.runner.trace.append(name)
                return
            self._park("sleeping", until)
        until = self._now() + timedelta(seconds=seconds(duration))
        self.run.steps[name] = {"state": "sleeping", "until": _iso(until)}
        self._park("sleeping", until)

    def wait_for_event(self, name: str, type: str, timeout: timedelta | float) -> dict[str, Any]:
        rec = self.run.steps.get(name) or {}
        if rec.get("state") == "done":
            if rec.get("timed_out"):
                raise WaitTimeout(name)
            return rec["result"]
        for i, ev in enumerate(self.run.events):
            if ev.get("type") == type:
                self.run.events.pop(i)
                self.run.steps[name] = {"state": "done", "result": ev.get("payload") or {}}
                self.runner.trace.append(name)
                return self.run.steps[name]["result"]
        if rec.get("state") == "waiting":
            until = datetime.fromisoformat(rec["until"])
            if self._now() >= until:
                self.run.steps[name] = {"state": "done", "timed_out": True}
                self.runner.trace.append(name)
                raise WaitTimeout(name)
            self._park("waiting", until, type)
        until = self._now() + timedelta(seconds=seconds(timeout))
        self.run.steps[name] = {"state": "waiting", "until": _iso(until), "type": type}
        self._park("waiting", until, type)


# ------------------------------------------------------------------------------------------------- the runner
class LocalRunner:
    def __init__(self, bus: CommandBus, flows: Iterable[Any], *, clock: Any = None, runs: Runs | None = None,
                 faults: dict[str, list[Fault]] | None = None):
        self.bus = bus
        self.flows = {f.name: f for f in flows}
        self.clock = clock or WallClock()
        self.runs: Runs = runs if runs is not None else MemoryRuns()
        self.faults: dict[str, list[Fault]] = {k: list(v) for k, v in (faults or {}).items()}
        self.trace: list[str] = []

    # ---- faults
    def take_fault(self, step: str) -> Fault | None:
        queue = self.faults.get(step)
        return queue.pop(0) if queue else None

    def bus_inject(self, fault: Fault) -> None:
        inject = getattr(self.bus, "inject", None)
        if inject:
            inject(fault)

    # ---- instances
    def start(self, flow: str, instance_id: str, params: dict[str, Any]) -> bool:
        """Create an instance; False when one with this id already exists (Cloudflare `create` throws then: the
        relay treats either as delivered)."""
        if flow not in self.flows:
            raise KeyError(f"no flow {flow}")
        if self.runs.get(instance_id) is not None:
            return False
        now = audit.now()
        self.runs.put(Run(instance_id, flow, dict(params), created_at=now, updated_at=now))
        return True

    def send_event(self, instance_id: str, type: str, payload: dict[str, Any] | None = None) -> bool:
        run = self.runs.get(instance_id)
        if run is None or run.status in ("complete", "errored"):
            return False
        run.events.append({"type": type, "payload": dict(payload or {}), "at": _iso(self.clock.now())})
        if run.status == "waiting" and run.waiting_for == type:
            run.status, run.wake_at = "running", None
        run.updated_at = audit.now()
        self.runs.put(run)
        return True

    def get(self, instance_id: str) -> Run | None:
        return self.runs.get(instance_id)

    def runnable(self) -> list[Run]:
        now = self.clock.now()
        out = []
        for run in self.runs.all():
            if run.status == "running":
                out.append(run)
            elif run.status in ("sleeping", "waiting"):
                if run.wake_at and datetime.fromisoformat(run.wake_at) <= now:
                    out.append(run)
                elif run.status == "waiting" and any(e.get("type") == run.waiting_for for e in run.events):
                    out.append(run)
        return out

    def advance(self, run: Run) -> Run:
        """Run the flow from the start (finished steps replay) until it parks, completes or fails."""
        flow = self.flows[run.flow]
        run.status, run.wake_at, run.waiting_for = "running", None, None
        try:
            run.result = flow.run({"instance_id": run.instance_id, "params": run.params}, _StepContext(self, run))
            run.status = "complete"
        except _Suspend:
            pass
        except Exception as e:        # a NonRetryable, or retries exhausted and not handled by the flow
            run.status, run.error = "errored", f"{type(e).__name__}: {e}"
        run.updated_at = audit.now()
        self.runs.put(run)
        return run

    def tick(self, conn: Any = None, *, firm_id: str | None = None, limit: int = 100) -> dict[str, int]:
        """One pass: relay the firm's undelivered outbox events (when a store is given), then advance every instance
        that is due. Deterministic against a FakeClock: nothing here sleeps."""
        relayed = self.relay(conn, firm_id=firm_id, limit=limit) if conn is not None else 0
        advanced = 0
        for run in self.runnable():
            self.advance(run)
            advanced += 1
        return {"relayed": relayed, "advanced": advanced}

    def run_until_idle(self, conn: Any = None, *, firm_id: str | None = None, max_ticks: int = 50) -> int:
        """Tick until nothing is runnable (the clock does not move: parked instances stay parked)."""
        n = 0
        while n < max_ticks:
            n += 1
            out = self.tick(conn, firm_id=firm_id)
            if not out["advanced"] and not out["relayed"]:
                break
        return n

    # ---- the relay (mirrored by edge/src/relay.ts)
    def relay(self, conn: Any, *, firm_id: str | None = None, limit: int = 100) -> int:
        n = 0
        for ev in outbox.undelivered(conn, 0, limit):
            self.deliver(ev, firm_id=firm_id)
            outbox.mark_delivered(conn, ev["id"])
            n += 1
        return n

    def deliver(self, ev: dict[str, Any], *, firm_id: str | None = None) -> None:
        """What an outbox event means to the orchestrator: a queued submission starts an instance (a state one only
        once the federal return is accepted), a federal acceptance starts the linked state instances, a
        reconciliation signals the waiting instance. Everything else is information."""
        p = ev.get("payload") or {}
        kind = ev.get("event_type")
        if kind == "submission.queued":
            if not p.get("linked_to") or p.get("linked_accepted"):
                self.start_submission(p, firm_id)
        elif kind == "submission.accepted" and p.get("jurisdiction") == FEDERAL:
            for linked in p.get("linked") or []:
                self.start_submission({**p, "submission_id": linked["submission_id"], "attempt": linked.get("attempt", 1),
                                       "jurisdiction": linked.get("jurisdiction")}, firm_id)
        elif kind == "submission.reconciled":
            self.send_event(instance_id_for(p), "submission-reconciled", p)

    def start_submission(self, p: dict[str, Any], firm_id: str | None) -> bool:
        return self.start("submission", instance_id_for(p),
                          {"firm_id": firm_id, "return_id": p.get("return_id"), "submission_id": p["submission_id"]})


def instance_id_for(p: dict[str, Any]) -> str:
    """The instance of one attempt at one submission (an outbox payload or a filing_submissions row)."""
    return f"filing-{p.get('submission_id') or p['id']}-{int(p.get('attempt') or 1)}"


def local_runner(conn: Any, returns: Any, provider: Any = None, *, clock: Any = None, runs: Runs | None = None,
                 faults: dict[str, list[Fault]] | None = None) -> LocalRunner:
    """A runner over one firm's store with the submission flow, persisted in the store unless `runs` says otherwise."""
    from . import commands
    from .flows.submission import SubmissionFlow

    ctx = commands.Context(conn, returns, provider, clock=(clock or WallClock()).now)
    bus = InProcessBus(ctx)
    return LocalRunner(bus, [SubmissionFlow(bus)], clock=clock, runs=runs if runs is not None else DbRuns(conn), faults=faults)
