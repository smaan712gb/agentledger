"""The internal endpoint the workflow runtime calls (ADR-0003, backlog F-08): the HTTP transport of the command
envelope (workflow/bus.py), the outbox for the Worker relay (edge/src/relay.ts) and a return's filing picture.

Reachable only through the `API` Durable Object: the edge Worker answers 404 to `/internal/*` from the internet, and
the container holds no Cloudflare credentials, so the Workflow calls in, never the other way round. The caller
presents AGENTLEDGER_WORKFLOW_TOKEN (a Worker secret, 32+ characters, compared in constant time and read per request,
exactly like the smoke token) as the bearer; anything else is 403, never 401, so the smoke principal's sweep sees
the same closed door as everywhere else. The principal is `workflow:<instance_id>`, role "system", and opens the
firm the envelope names firm-wide: it runs only system commands (a person's decisions are refused with 403), and the
domain's guards still apply to each.

Status codes the Workflow relies on: 409 `{"code": "transition_refused" | "command_conflict"}` is final (the step
stops: NonRetryableError); 404 and 403 are final too; 5xx and timeouts are retried.
"""

from __future__ import annotations

import hmac
import os
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from ..db import CommandConflict
from ..workflow import commands, outbox
from ..workflow.commands import Command, Context
from ..workflow.engine import TransitionError

TOKEN_VAR = "AGENTLEDGER_WORKFLOW_TOKEN"
MARK_HEADER = "X-AgentLedger-Workflow"        # set by the Workflow, stripped from anything arriving from the internet
OUTBOX_HEADER = "X-AgentLedger-Outbox"        # on a response whose request emitted outbox events: the edge relays at once


def token_ok(token: str) -> bool:
    expected = os.environ.get(TOKEN_VAR, "")
    return len(expected) >= 32 and bool(token) and hmac.compare_digest(expected.encode(), token.encode())


def workflow_principal(authorization: str = Header(default="")) -> dict[str, Any]:
    if not token_ok(authorization.removeprefix("Bearer ").strip()):
        raise HTTPException(403, "the internal endpoint answers the workflow runtime only")
    return {"role": commands.SYSTEM}


def _refused(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status, {"code": code, "detail": detail})


def build_router(open_firm: Callable[[str], tuple[Any, Any]], provider_for: Callable[[str], Any],
                 active_firms: Callable[[], list[str]]) -> APIRouter:
    """`open_firm(firm_id)` -> (AppContext scoped firm-wide, Returns); `provider_for(firm_id)` -> the transmitter
    adapter or None; `active_firms()` -> the firm ids the relay polls. All live in app.py, which owns tenants and keys."""
    router = APIRouter(prefix="/internal", include_in_schema=False)

    def firm(firm_id: str) -> tuple[Any, Any]:
        if not firm_id:
            raise _refused(404, "not_found", "firm_id is required")
        return open_firm(firm_id)

    @router.get("/firms")
    def firms(_p: dict[str, Any] = Depends(workflow_principal)) -> dict[str, Any]:
        """The firms whose outboxes the relay polls (ids only)."""
        return {"firms": active_firms()}

    @router.post("/commands")
    def run_command(body: dict[str, Any] = Body(...), _p: dict[str, Any] = Depends(workflow_principal)) -> dict[str, Any]:
        name, key = str(body.get("name") or ""), str(body.get("idempotency_key") or "")
        instance, payload = str(body.get("instance_id") or ""), body.get("payload") or {}
        if not name or not key or not instance or not isinstance(payload, dict):
            raise _refused(422, "invalid", "the envelope is {firm_id, name, idempotency_key, payload, instance_id}")
        spec = commands.registry().get(name)
        if spec is None:
            raise _refused(404, "unknown_command", f"no command {name}")
        if spec.human:
            raise _refused(403, "human_only", f"{name} is a person's decision; the workflow may not run it")
        firm_id = str(body.get("firm_id") or "")
        ctx, returns = firm(firm_id)
        cmd = Command(name, key, payload, f"workflow:{instance}", commands.SYSTEM)
        try:
            return commands.dispatch(Context(ctx.conn, returns, provider_for(firm_id)), cmd).to_dict()
        except TransitionError as e:
            raise _refused(409, "transition_refused", str(e))
        except CommandConflict as e:
            raise _refused(409, "command_conflict", str(e))
        except commands.NotPermitted as e:
            raise _refused(403, "human_only", str(e))
        except KeyError as e:
            raise _refused(404, "not_found", str(e))
        except ValueError as e:
            raise _refused(422, "invalid", str(e))

    @router.get("/returns/{rid}/filing")
    def filing(rid: str, firm_id: str = Query(default=""), _p: dict[str, Any] = Depends(workflow_principal)) -> dict[str, Any]:
        _ctx, returns = firm(firm_id)
        try:
            returns.get(rid)
        except KeyError:
            raise _refused(404, "not_found", "return not found")
        return returns.filing_summary(rid)

    @router.get("/outbox")
    def undelivered(firm_id: str = Query(default=""), after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=1000),
                    _p: dict[str, Any] = Depends(workflow_principal)) -> dict[str, Any]:
        ctx, _returns = firm(firm_id)
        events = outbox.undelivered(ctx.conn, after, limit)
        return {"events": events, "next": events[-1]["id"] if events else after}

    @router.post("/outbox/{event_id}/delivered")
    def delivered(event_id: int, body: dict[str, Any] = Body(default={}), _p: dict[str, Any] = Depends(workflow_principal)) -> dict[str, Any]:
        ctx, _returns = firm(str(body.get("firm_id") or ""))
        try:
            return {"id": event_id, "delivered": outbox.mark_delivered(ctx.conn, event_id)}
        except KeyError:
            raise _refused(404, "not_found", f"no outbox event {event_id}")

    return router


class OutboxMarker:
    """ASGI middleware: a response whose request emitted outbox events carries X-AgentLedger-Outbox: 1, so the edge
    runs the relay at once (edge/src/index.ts) instead of waiting for its cron. The marker is a list set on a
    context variable per request; `outbox.emit` appends to it from whichever thread serves the request."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        emitted: list[int] = []
        token = outbox.EMITTED.set(emitted)

        async def send_marked(message: Any) -> None:
            if message["type"] == "http.response.start" and emitted:
                headers = list(message.get("headers") or [])
                headers.append((OUTBOX_HEADER.lower().encode(), b"1"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_marked)
        finally:
            outbox.EMITTED.reset(token)
