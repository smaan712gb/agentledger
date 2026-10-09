"""How a flow reaches the domain: a command bus carrying the JSON envelope `{name, idempotency_key, payload}` on
behalf of a workflow instance. In process (tests, the self-host profile) the bus dispatches straight into
workflow/commands.py; the Cloudflare Workflow carries the same envelope over HTTP to POST /internal/commands
(api/internal.py), and maps the endpoint's 409 to NonRetryable exactly as `InProcessBus` maps the exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..db import CommandConflict
from . import commands
from .engine import TransitionError
from .steps import NonRetryable


class CommandBus(Protocol):
    def command(self, name: str, idempotency_key: str, payload: dict[str, Any], *, instance_id: str,
                firm_id: str | None = None) -> dict[str, Any]: ...


@dataclass
class Fault:
    """A fault injected into the provider by the local runner before a step runs (see runner.py)."""


@dataclass
class ProviderUnknown(Fault):
    """The transmitter receives the submission and never answers (the Q23 window)."""


@dataclass
class Reject(Fault):
    """The jurisdiction rejects the submission with these business-rule codes."""

    codes: list[str] = field(default_factory=list)


class InProcessBus:
    """Dispatches commands into one firm's store as the principal `workflow:<instance_id>`, role "system"."""

    def __init__(self, ctx: commands.Context):
        self.ctx = ctx
        self.calls: list[tuple[str, str]] = []

    def inject(self, fault: Fault) -> None:
        provider = self.ctx.provider
        if provider is None:
            return
        if isinstance(fault, ProviderUnknown):
            provider.arm("timeout")
        elif isinstance(fault, Reject):
            provider.outcome, provider.codes = "rejected", list(fault.codes)

    def command(self, name: str, idempotency_key: str, payload: dict[str, Any], *, instance_id: str,
                firm_id: str | None = None) -> dict[str, Any]:
        self.calls.append((name, idempotency_key))
        cmd = commands.Command(name, idempotency_key, payload, f"workflow:{instance_id}", commands.SYSTEM)
        try:
            return commands.dispatch(self.ctx, cmd).result
        except (TransitionError, CommandConflict, commands.NotPermitted, KeyError, ValueError) as e:
            raise NonRetryable(f"{name}: {e}") from e
