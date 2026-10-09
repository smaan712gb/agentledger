"""One instance per submission (ADR-0003, backlog F-08): transmit once, chase a lost answer, poll for the
acknowledgement, and ask a person when the rules run out. Every step is a system command (workflow/commands.py); the
flow never approves, releases, reconciles or resends.

    transmit ─ unknown ─► lookup-wait-n / lookup-n ... ─ still unknown ─► notify-unknown ─► wait-reconciled-n ──┐
        │                                                                                    (a person decides)  │
        ▼ transmitted ◄──────────────────────────────────────────────────────────── reconciled: received ◄──────┘
    poll-wait-n / poll-ack-n ... ─ no acknowledgement by the deadline ─► notify-unacknowledged ─► hourly polls
        │
        ▼ accepted | rejected (rejected: notify-rejected)

Step names and timings come from flows/steps.json, which the Cloudflare Workflow (edge/src/workflows/submission.ts)
mirrors; the orchestrator keeps no business state, the return and its submissions do.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from ..bus import CommandBus
from ..steps import NonRetryable, RetryConfig, Step, WaitTimeout
from . import expand, plan

FINAL = ("accepted", "rejected", "cancelled", "superseded")


class SubmissionFlow:
    name = "submission"

    def __init__(self, bus: CommandBus, spec: dict[str, Any] | None = None):
        self.bus = bus
        self.spec = spec or plan()
        self.retries = RetryConfig.from_plan(self.spec["retries"]["command"])

    def run(self, event: dict[str, Any], step: Step) -> dict[str, Any]:
        p = event["params"]
        sid, firm = str(p["submission_id"]), p.get("firm_id")
        instance = str(event["instance_id"])

        def command(name: str, suffix: str, **payload: Any) -> dict[str, Any]:
            # Keyed by the instance (one per attempt at a submission): a later attempt is a new command, never a replay.
            return self.bus.command(name, f"{instance}:{suffix}", {"submission_id": sid, **payload}, instance_id=instance, firm_id=firm)

        # 1. Transmit once. Retries exhausted (the transmitter never answered): the submission is unknown.
        try:
            out = step.do("transmit", lambda: command("transmit_submission", "transmit"), retries=self.retries)
        except NonRetryable:
            raise
        except Exception:
            out = step.do("mark-unknown", lambda: command("mark_unknown", "mark-unknown", note="no answer from the transmitter after retries"),
                          retries=self.retries)
        status = str(out["status"])

        # 2. Unknown: ask the transmitter by the planned id, with backoff, until the deadline; then a person.
        if status == "unknown":
            for n, delay in enumerate(self.spec["lookup"]["intervals_s"], 1):
                step.sleep(f"lookup-wait-{n}", delay)
                out = step.do(f"lookup-{n}", partial(command, "lookup_submission", f"lookup-{n}"), retries=self.retries)
                status = str(out["status"])
                if status != "unknown":
                    break
            n = 0
            if status == "unknown":
                step.do("notify-unknown", lambda: command("notify_operator", "notify-unknown", reason="transmission outcome unknown"),
                        retries=self.retries)
            while status == "unknown":
                n += 1
                try:
                    step.wait_for_event(f"wait-reconciled-{n}", "submission-reconciled", self.spec["reconcile_wait_s"])
                except WaitTimeout:
                    continue
                out = step.do(f"after-reconcile-{n}", partial(command, "lookup_submission", f"after-reconcile-{n}"), retries=self.retries)
                status = str(out["status"])
            if status == "queued":            # never received: a person releases it again, under a new planned id
                return {"outcome": "reconciled_not_submitted", "submission_id": sid}

        # 3. Transmitted: poll for the acknowledgement on the schedule, then tell a person and keep polling.
        if status == "transmitted":
            schedule = expand(self.spec["poll"]["schedule"])
            after = self.spec["poll"]["after_notice"]
            for n, delay in enumerate(schedule + [float(after["every_s"])] * int(after["times"]), 1):
                if n == len(schedule) + 1:
                    step.do("notify-unacknowledged", lambda: command("notify_operator", "notify-unacknowledged",
                                                                     reason="no acknowledgement within the deadline"), retries=self.retries)
                step.sleep(f"poll-wait-{n}", delay)
                out = step.do(f"poll-ack-{n}", partial(command, "poll_acks", f"poll-ack-{n}"), retries=self.retries)
                status = str(out["status"])
                if status in FINAL:
                    break
        if status == "rejected":
            step.do("notify-rejected", lambda: command("notify_operator", "notify-rejected", reason="rejected"), retries=self.retries)
        return {"outcome": status, "submission_id": sid}
