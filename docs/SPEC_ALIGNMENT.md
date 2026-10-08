# Alignment with the production specification (v1.0, 2026-10-08)

This compares `unified-accounting-tax-production-spec.md` with the AgentLedger codebase as of commit `67cb5ec`.
Each item records a decision: **adopt** (the spec's approach is better and we switch), **keep** (our design
already meets the requirement or does better), or **defer** (adopt at a named milestone).

## Already consistent

| Spec requirement | AgentLedger today |
|---|---|
| Deterministic calculations; AI proposes, never decides (§1, §12) | Calculators and the Sentinel verifier are code. Models draft only. Guards are deterministic. |
| PolicyEngine is a reference, not a filing engine (§2, §9) | Used only as an independent cross-check oracle (`returns/oracle.py`) |
| Beancount never a second writable ledger (§2) | Export only |
| BOI domestic exemption; §7216 consent; separate MeF roles (§2) | Encoded in rules and `docs/GO_LIVE.md` Part A |
| Provenance on every figure (§1, §6) | Rule traces on every calculation; per-box document provenance on returns |
| Approval bound to an exact payload hash; edits invalidate approval and signature (§9, §12) | `approved_hash`, `signed_hash`, `reopen` in the return workflow |
| Preparer/reviewer segregation; agents never approve their own proposals (§12, §14) | Enforced by workflow guards and Foundry policy |
| Never blindly retransmit; one effect per command (§9, §15) | Transmission runs as a recorded activity under an idempotency key |
| Unsupported coverage blocks filing (§7, Q19) | `error` diagnostics block submission for review |
| Independent tax validation, not self-derived expectations (§19) | Hand-worked expected values plus a PolicyEngine cross-check |
| Tenant isolation tested on every path, including exports and URLs (§14, Q12) | Database-per-firm isolation; signed links; `tests/test_tenancy.py` |

## Keep (our design meets or exceeds the requirement)

- **Isolation model.** The spec asks for PostgreSQL row-level security as defense in depth. We use a separate
  database, vault and data key per firm, and offboarding crypto-shreds the firm's data. This is a stronger
  boundary than shared tables with RLS. When we move to PostgreSQL, each firm gets its own database (or
  schema), and RLS is added inside it for engagement-level grants.
- **Regulation as code with RegWatch, the Staleness Hunter and the Sentinel.** The spec describes a manual
  annual content release. We keep the automated monitoring and verification, and adopt the spec's human
  sign-off rule below.
- **Durable workflows without Temporal (for now).** Our event-sourced engine gives the guarantees the spec
  asks of Temporal: durable pauses, deterministic replay, and activities recorded under idempotency keys.
  It has one fewer system to operate. Revisit when we need distributed workers, timers at volume, or
  cross-service sagas. The `Engine` interface is small enough to back with Temporal later.
- **Native 1040 engine.** The spec recommends a contracted provider first, with a native engine as a gated
  program. The owner chose native. We keep native for the published coverage and adopt the export
  handoff for everything outside it (below).

## Adopt now (milestone M3.5)

1. **Coverage registry (§7).** Record a status per capability, tax year, jurisdiction and form:
   unsupported, manual-assisted, planning-only, preparation-validated, export-validated, filing-approved
   or suspended. The API and UI enforce it, and it is published at `GET /api/coverage`.
2. **Calculation pinning (§6, Q39).** Every computed return version stores the knowledge-base version
   and the engine version. A filed return reproduces from its pinned rules, and a rule change creates
   recalculation candidates, never silent changes.
3. **Facts are not overwritten (§6, W05).** Re-populating from documents detects conflicts with
   existing values and raises an exception rather than letting the last upload win. A missing value
   stays missing; it never becomes zero.
4. **Filing state machine (§9).** Add an `unknown` state after a timeout or missing receipt, recovered
   only by reconciling with the provider. Federal and each state submission get independent states.
   Add a release-approval step before queueing. Extensions are separate from returns.
5. **Rule changes need a named tax-content owner in production (§9).** Auto-adoption stays available
   in dev and for model swaps. In multi-firm mode, statutory rule changes need platform reviewer
   approval even when verified.
6. **Fuel excise and 835 corrections (§2, §10).** The fuel excise payable applies only when the entity
   is the liable party; otherwise excise stays in cost. The 835 posting covers patient responsibility,
   reversals and provider-level adjustments.
7. **Export handoff for uncovered returns (§9).** Anything outside native coverage exports a validated
   trial balance or workpaper set to the firm's existing tax software. No invented APIs.

## Defer (adopt at a named milestone)

| Item | Milestone | Note |
|---|---|---|
| PostgreSQL as transactional authority, posting functions that enforce balance at the commit boundary, transactional outbox, durable command ids (§6, §15, Q01–Q05) | M12, before the first paying firm | SQLite per firm is acceptable for pilots on encrypted volumes |
| React/TypeScript frontend for the 16-screen inventory (§3, §4) | M3.6 | The no-build UI does not scale to accounting grids, keyboard work and the full set of UI states |
| Managed OIDC identity and enterprise SSO (§4, C01) | M2b | Keep Argon2 + TOTP as the default; federate through an open-source IdP (Keycloak or Zitadel) |
| Audit chain anchoring to independent storage (§14) | M12 | Daily chain heads to object-locked storage, so even a DBA cannot rewrite history undetected |
| Acceptance suite Q01–Q40 as release gates (§19) | ongoing | Map each Q test to a test file; several already exist |
| AR/AP payments workflow with uncertain-outcome recovery (§8, W06) | A1 | Not started |
| Named owners: tax technical lead, accounting lead, security owner (§20) | business | Software cannot fill these roles |
