"""The step protocol a flow is written against (ADR-0003), shared by the local runner (runner.py) and mirrored by the
Cloudflare Workflow (edge/src/workflows): `do` runs a named step at most once per instance and memoises its result,
retrying failures on a schedule; `sleep` parks the instance; `wait_for_event` parks it until the outside world
signals, or the timeout passes. A flow is a deterministic function of its step results: it may be re-run from the
start at any time and replays to the same place.

A `NonRetryable` failure stops the retries at once (the domain refused: a 409 from the internal endpoint); any other
exception is retried with exponential backoff up to the limit, then raised to the flow, which decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol


class NonRetryable(Exception):
    """A step failed for a reason a retry cannot change (the domain refused, the command conflicted)."""


class WaitTimeout(Exception):
    """`wait_for_event` ran out of time without the event."""


@dataclass(frozen=True)
class RetryConfig:
    """Cloudflare's defaults: five retries, ten seconds apart, exponential, ten minutes per attempt."""

    limit: int = 5
    delay: float = 10.0
    backoff: str = "exponential"       # exponential | linear | constant
    timeout: float = 600.0

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait before `attempt` + 1 (attempt counts failures so far, from 1)."""
        if self.backoff == "exponential":
            return self.delay * (2 ** (attempt - 1))
        if self.backoff == "linear":
            return self.delay * attempt
        return self.delay

    @classmethod
    def from_plan(cls, spec: dict[str, Any]) -> "RetryConfig":
        return cls(limit=int(spec.get("limit", 5)), delay=float(spec.get("delay_s", 10)), backoff=str(spec.get("backoff", "exponential")),
                   timeout=float(spec.get("timeout_s", 600)))


class Clock(Protocol):
    def now(self) -> datetime: ...


class Step(Protocol):
    def do(self, name: str, fn: Callable[[], Any], retries: RetryConfig | None = None) -> Any: ...
    def sleep(self, name: str, duration: timedelta | float) -> None: ...
    def wait_for_event(self, name: str, type: str, timeout: timedelta | float) -> dict[str, Any]: ...


class Flow(Protocol):
    name: str

    def run(self, event: dict[str, Any], step: Step) -> dict[str, Any]: ...


def seconds(d: timedelta | float | int) -> float:
    return d.total_seconds() if isinstance(d, timedelta) else float(d)
