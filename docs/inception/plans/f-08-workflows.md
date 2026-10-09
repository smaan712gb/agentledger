# F-08 plan: workflows adapter and return filing state machine

Read-only survey 2026-10-09; implementation in progress (slices 1–4 and 6 first, 5 last). Status in backlog.md.

## Decisions

- Domain commands are idempotent steps: `Command(name, idempotency_key, payload, actor, role)` executed in one unit
  of work under a per-command advisory lock; the receipt (reusing `db.run_command`'s tables), the domain event and
  an outbox row commit together; a replay returns the recorded result (`replayed=True`); a different payload or
  principal is a `CommandConflict`; a refused transition is not receipted. Steps are "ensure", not "do": a system
  command first reads the submission state and returns the recorded outcome if the transition already happened.
- Authority-bearing commands (approve, approve_release, reconcile, retransmit, void) are human-only; the Workflow
  may run only system commands (transmit_submission, lookup_submission, poll_acks, record_ack, mark_unknown,
  notify_operator).
- The outbox exists on both backends (SQLite tables; PostgreSQL `SECURITY DEFINER emit_event` so the app role keeps
  SELECT-only on `outbox`).
- A Workflow reaches the API through an authenticated internal endpoint on the container (`/internal/*`, Worker
  secret `AGENTLEDGER_WORKFLOW_TOKEN`, constant-time check, 403 on mismatch, 404 from the public edge); the
  container never holds Cloudflare or owner credentials.
- One local runner (`LocalRunner`: memoised steps, deterministic retries against a fake clock, fault injection,
  `wait_for_event`, outbox tick) serves the tests and the SQLite self-host profile; the Cloudflare Workflow mirrors
  the same ordered step names (`flows/steps.json`). Queues are deferred (not needed at return volumes).
- Return as the aggregate; the federal submission stays on the return's event stream (retention and the existing
  tests read `ack_accepted`, `mark_paper_filed`, `void` there); state submissions get their own streams in
  `filing_submissions`. New state `release_approved` (signed → release_approved, reviewer with step-up, per-
  jurisdiction coverage check); `transmit` by role "cpa" records the release in the same act (documented dual
  path), by role "system" requires `release_approved` and the matching hash.
- `unknown` handling: two-phase transmit with the planned SubmissionId recorded first; provider lookup by id; found
  → transmitted without resend; not found by the unknown deadline (24 h) → operator notice, wait for the CPA's
  reconcile; acks polled every 15 min (backoff to 1 h) until the ack deadline (72 h) → notice. Resubmission only by
  a human `retransmit`; US-FED never retransmitted once accepted; a federal rejection cancels linked state
  submissions; state instances start on the federal acceptance event.

## Cloudflare Workflows facts (2026-10-09)

`step.do` retries default 5 × exponential from 10 s, 10 min per attempt; `NonRetryableError`; `step.sleep` ≤ 365 d;
`waitForEvent` default 24 h; `create` refuses an existing id within retention (default 7 days); 10,000 steps per
instance; payloads ≤ 1 MiB (ids and hashes only — no taxpayer data leaves the container except to MeF); a Workflow
has every Worker binding (`env.API`); `workflows` bindings are per environment; `wrangler dev` runs them locally.

## Slices

1. Command contract and outbox — `workflow/commands.py`, `workflow/outbox.py`, `engine.py` (`State.to_dict`,
   `activity(started=)`), `db.py` outbox tables, migration `0006_filing.sql` part 1, `tests/test_workflow_commands.py`.
2. Local runner and submission flow — `workflow/steps.py`, `runner.py`, `bus.py`, `flows/submission.py`,
   `flows/steps.json`, `tests/test_workflow_runner.py`.
3. Filing state machine — `returns/store.py` (release_approved, transmit role split, `filing_summary`,
   `retransmit`), `returns/filing.py`, 0006 part 2 (`filing_submissions`, RLS replacement for `workflow_events`),
   API actions `release-approve` and `retransmit`, `filing` in `GET /api/returns/{rid}`,
   `tests/test_filing_state_machine.py` (property test), `tests/test_filing_submissions.py` (Q24).
4. Internal endpoint and credential — `api/internal.py`, edge header policy, `AGENTLEDGER_WORKFLOW_TOKEN` in
   `secrets.required`, `tests/test_internal_api.py`.
5. Cloudflare Workflow and relay — `edge/src/workflows/submission.ts`, `edge/src/relay.ts` (outbox cursor,
   instance creation, `sendEvent`, delivered marks; cron backstop plus relay-on-write header), `wrangler.jsonc`
   `workflows` binding per env, ADR-0003 amendment.
6. Self-host driver and CLI — `LocalRunner.tick` from the scheduler when `AGENTLEDGER_ORCHESTRATOR=local`,
   `agentledger workflows tick|list|signal`.

## Acceptance

- Q03: a command whose handler committed but whose reply was lost produces exactly one effect on retry (runner and
  HTTP paths); the outbox row is delivered once.
- Q23: lost response → `unknown` → lookup records transmitted without resend → ack → accepted; without lookup →
  operator notice → CPA reconciles → the flow resumes; paper filing and void refused meanwhile.
- Q24: federal and state are independent submissions with their own ids, acks and retransmission; the federal is
  never retransmitted; the CPA sees the rejection's business rules and the state retransmits alone.

Residuals: MeF SubmissionId assignment and lookup semantics are T2-02 (mocked here); state package hashes are
placeholders until T1-05; verify `workflows`/`triggers` inside env blocks and the Workflow → container cold start
on the first deploy.
