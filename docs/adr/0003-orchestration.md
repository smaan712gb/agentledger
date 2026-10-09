# ADR-0003: Cloudflare Workflows orchestrates; domain state machines live in PostgreSQL

Status: Accepted, subject to the F-02 limits spike. Fallback: Temporal Cloud, with workers in Containers.
Spec references: §4 (one orchestration system), §9 (filing state machine), §12 (agent task lifecycle),
§15 (outbox), Q02, Q03, Q23, Q25, Q33.

## Problem the spec warns about

Two systems must not own the same workflow state (spec §4). We have two kinds of state that are easy to
confuse:

- **Domain state.** Where a return, close run, payment or proposal is in its business lifecycle (draft,
  in review, awaiting signature, submitted, accepted). Reports and authorization need it, and it is
  evidence. It belongs with the financial records.
- **Orchestration state.** Which step of a long-running process is executing, its timers, retries and
  waits on outside events (a signature, an IRS acknowledgement, a bank file).

## Decision

1. **Domain state machines are aggregates in PostgreSQL.** `src/agentledger/workflow/engine.py` already
   implements this as append-only, hash-chained events with deterministic guards and replay. It is ported
   to PostgreSQL tables (ADR-0002). Transitions happen only through domain commands that re-check
   permissions, versions and the payload hash at commit.
2. **Cloudflare Workflows owns orchestration only.** A workflow instance drives a process by calling domain
   commands. Each `step.do` is one command, keyed by `command_id` so a retry is idempotent. The workflow
   waits on outside events with `step.waitForEvent`. It never stores business status the domain does not
   also hold, and the UI reads status from the domain, not from the workflow.
3. **Activities with side effects are uncertain-outcome aware** (spec §9, §15). Before retrying a
   transmission or payment, a step looks up the original command's provider status. An unknown result
   moves the domain aggregate to `unknown`, and only reconciliation leaves that state.
4. **LLM, OCR and browser work** run inside steps that call worker containers, with budgets and timeouts.
   Workflow code itself stays deterministic.
5. **Local development** uses an in-process runner with the same step and event interface, so tests do not
   need Cloudflare.

## Why not Temporal now

Temporal satisfies the spec. On Cloudflare it would add a cluster or SaaS contract, plus long-polling
workers that fit poorly on scale-to-zero containers. Cloudflare Workflows gives the same primitives on the
platform the owner chose. If the F-02 spike shows a blocking limit (step count, state size, retention or
observability), switch to Temporal Cloud. Because orchestration only calls domain commands, the switch is
contained.

## Amendment 2026-10-09 (backlog F-08): what was built, and the facts it rests on

Cloudflare Workflows, as documented on 2026-10-09: `step.do(name, config?, fn)` with default retries of five, ten
seconds apart, exponential, and ten minutes per attempt; `NonRetryableError` stops retries; steps must be idempotent
(check before the non-idempotent call) and may not rely on state outside a step; results under 1 MiB; `step.sleep` up
to a year; `step.waitForEvent(name, {type, timeout})` (24 hours by default) answered by `instance.sendEvent`;
`create({id <= 100 chars, params, retention})` throws when the id exists within the retention window; 10,000 steps per
instance by default, 50,000 concurrent instances, 100 creates per second per Workflow, seven days of completed-instance
retention by default; a Workflow class sees every Worker binding, so it reaches the `API` Durable Object directly;
`workflows` and `triggers.crons` are non-inheritable per environment. From outside Workers, creating instances needs an
account API token, which the container must never hold.

Decisions:

1. **One instance per attempt at one submission** (`filing-<submission id>-<attempt>`), not per return: a return
   with a state filing is two to three instances, each about 1 transmit + 20-50 acknowledgement polls + a few
   notices. A submission re-queued after a "never received" reconciliation is a new attempt and a new instance, so an
   id completed within the retention window never blocks a later attempt.
2. **The Worker relays the outbox; no Queues yet.** The domain writes `submission.*` and `filing.attention` events
   in the transaction of the change (`workflow/outbox.py`, `emit_event` on PostgreSQL). `edge/src/relay.ts` polls
   `GET /internal/outbox` per firm, creates instances ("already exists" counts as delivered), signals
   `submission-reconciled`, and marks each event delivered; it runs from a one-minute cron and right after any API
   response that carries `X-AgentLedger-Outbox`. The Python runner's `tick()` applies the same rules, so the
   self-host profile and Cloudflare behave alike. Queues would add nothing at return volumes; the relay stays an
   abstraction that could be fed from one.
3. **The container is reached, never the other way round.** The Workflow and the relay call
   `POST /internal/commands`, `GET /internal/outbox`, `POST /internal/outbox/{id}/delivered`,
   `GET /internal/returns/{rid}/filing` and `GET /internal/firms` through the Durable Object with
   `AGENTLEDGER_WORKFLOW_TOKEN`; the edge answers 404 for `/internal/*` from the internet. The container holds no
   Cloudflare credential. The endpoint runs system commands only (transmit, lookup, poll and record acknowledgements,
   mark unknown, notify); approve, release, reconcile, retransmit and void are a person's and are refused with 403.
4. **Commands are receipts plus an "ensure".** A command is `{name, idempotency_key, payload}` from a principal; the
   receipt's hash covers payload, actor and role; a replay returns the recorded result, a conflict is 409, a refusal
   (`TransitionError`) is 409 and never retried. Every system command reads the aggregate first and returns the
   recorded outcome when the transition already happened. The transmit command runs the two-phase activity in a
   `prepare` phase outside the receipt transaction: a provider error must not roll the "started" marker back, or a
   retry would send again.
5. **Payloads carry ids and hashes only.** Outbox payloads, command results (`State.to_dict` names the facts it
   exports) and Workflow params hold submission ids, return ids, jurisdictions, attempt numbers, statuses and codes;
   taxpayer data never leaves the container except to the transmitter.
6. **Retention and limits.** Instances are created with seven days of success and error retention; the `FILING`
   binding sets `limits.steps` to 2,500 (the longest run, 29 days of hourly polls after the deadline notice, is about
   1,600 steps); command steps retry ten times, 30 seconds apart, exponential, five minutes per attempt. Step names
   and timings live in `workflow/flows/steps.json`, imported by both the Python flow and the TypeScript Workflow.
7. **The domain keeps the states.** The return stays the aggregate (`release_approved` added; `transmit`,
   `ack_accepted`, `outcome_unknown` and the reconciliations stay on its stream for retention and the paper-filing
   guards); each submission has its row and its own hash-chained stream. A CPA may still transmit directly, which
   approves the release in the same act and is recorded as such; the workflow transmits only a release a CPA
   approved. The Workflow never resends: a lost answer is looked up by the planned SubmissionId, then handed to a
   person.

Not verified yet: a live run against a staging container (the 7-day retention request, step timeouts against a cold
container, the relay's behaviour when a firm's endpoint is down). The F-02 limits spike is answered for the return
flow by the sizes above; the close-run flow is still to be measured.
