# ADR-0010: Continuous compliance agents and autonomous release

Status: Accepted (owner request 2026-10-08: "all states; agents keep current with all state and IRS changes,
with proper testing, autonomously deploying"). Spec references: §9 (annual tax-content operation), §13
(connector status), §18 (deployments), §20 (gates).

## Watchers

| Agent | Watches | Cadence |
|---|---|---|
| `regwatch-federal-register` | IRS, Treasury and FinCEN rules and notices | 12 h |
| `regwatch-irs-newsroom` | IRS news releases (new figures, relief) | 12 h |
| `staleness-hunter` | Every indexed parameter's due date; hunts sources when one is late | 24 h |
| `state-watch` | **All 50 states and DC.** Each tax agency's newsroom and guidance pages, discovered from the agency home page and verified, 8 states per run, so every state is covered daily. Agencies come from `config/jurisdictions.yaml`, built from the Federation of Tax Administrators directory and protected. | 3 h |
| `legislation-watch` | **Enacted tax law** in all states (Open States) and Congress (GovInfo public laws) | 12 h |
| `irs-forms-watch` | New drafts and revisions of every IRS form and instruction we compute (`config/irs_forms.yaml`) | 24 h |
| `model-scout`, `repo-scout`, `dependency-watch` | Models, open-source stack, dependencies | existing |

Every finding goes through the same pipeline: draft → Sentinel verification (official domain, verbatim
quotes, numbers inside the quotes, bounds, golden regression) → proposal.

## Never silently wrong

A finding that could make a computation wrong raises a **coverage flag** for its jurisdiction, year and form.
Examples: an enacted law, or a revised form. A flag does not stop preparation; the preparer sees a review
warning. It **blocks filing** until the change is implemented, tested and cleared, and clearing needs a note and
evidence.

## Testing on every change

- Full suite.
- Golden whole-return scenarios.
- PolicyEngine cross-checks, which also cover every state income tax as state packages arrive.
- New fixtures taken from the authority's own examples. The Researcher adds a fixture only if the engine
  already reproduces the authority's number.

## Autonomous release (`config/release_policy.yaml`, protected)

- The scheduled workforce opens a pull request.
- `veritas release classify` decides whether it ships without a person:
  - **Auto-release:** routine indexed values (verbatim, bounded, official, all tests green), eval-won model
    promotions, and patch dependencies.
  - **One-click approval by the tax-content owner:** statutory changes, form revisions and coverage rises.
    The agents prepare everything (draft, tests, impact list, PR). This keeps the sign-off the spec requires
    (§9) and that firms rely on professionally.
  - Code changes need platform-admin approval until the Engineer's record is measured.
- Deadline freezes: no automatic releases in the 72 hours before filing deadlines.
- `release.yml` on every merge: verify → staging deploy → smoke tests (health, coverage, a golden return
  through the API) → gradual production rollout (10 % → 50 % → 100 %, 15-minute soaks) → automatic rollback
  if a smoke check fails.

## Needs from the owner

- `OPENSTATES_API_KEY` and `DATA_GOV_API_KEY` (both free), plus the Cloudflare credentials.
- The named tax-content owner in `release_policy.yaml`.
- The GitHub push. The deploy jobs activate once `wrangler.jsonc` exists (backlog F-02).
