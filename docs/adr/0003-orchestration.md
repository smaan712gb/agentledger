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
