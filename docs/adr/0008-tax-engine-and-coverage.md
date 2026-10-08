# ADR-0008: Native tax engines behind a published coverage registry

Status: Accepted (the owner chose native, full scope). Spec references: §7 (coverage states), §9
(build-versus-integrate, annual content operation), Q18–Q24, Q39.

## Decision

- **Native packages.** Tax calculations are native, versioned packages: the 2026 federal 1040 core
  exists today. Business returns (1120-S, 1065 with K-1s), the first states and 1099 information returns
  follow as separate packages.
- **Published coverage.** Each package publishes its coverage per form, schedule, line family, tax year
  and jurisdiction, using the spec's states:
  - `unsupported`
  - `manual-assisted`
  - `planning-only`
  - `preparation-validated`
  - `export-validated`
  - `filing-approved`
  - `suspended`

  The registry (`coverage/coverage.yaml` plus the `/api/coverage` endpoint) is enforced by the API and UI:
  - preparation of an unsupported item is blocked or explicitly manual-assisted
  - a filing release requires `filing-approved` for every included form and jurisdiction
- **Status ladder.** A package moves up only with evidence: independent fixtures (worked by hand or from
  official examples, never derived from the engine), PolicyEngine cross-checks where they apply, IRS ATS
  scenarios for `filing-approved`, and a named tax-content owner's sign-off.
- **Pinned runs.** Every calculation run pins the knowledge-base version, package version, inputs and
  elections. Filed returns reproduce exactly. A rule change creates recalculation candidates, never silent
  changes.
- **Export fallback.** Anything outside native coverage exports validated trial balances and workpapers to
  the firm's existing tax software. This is labeled an export, never an integration (spec §9, §13). No
  invented partner APIs.
- **Rule changes need a named reviewer in production.** RegWatch and the Sentinel continue to find,
  verify and draft rule changes. Production adoption requires the tax-content owner's sign-off
  (`foundry.yaml` auto-adopt is disabled for statutory rules outside dev).
