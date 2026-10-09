# F-10 plan: React app shell

Read-only analysis 2026-10-09; slice 1 in progress. Status in backlog.md. Runbook for the app: docs/WEB.md.

## Facts and decisions

- The production spec text is not in the repo; ADR-0005 is the in-repo statement of the shell: three workspaces
  (business, practice, client/investor portal) in one shell; firm / entity / engagement / period / basis always
  visible; required states (loading, empty, stale with freshness, permission-denied, partial-success, recovery);
  drafts autosave; edits carry a version; TanStack Router/Query/Table + Radix; typed clients in
  `packages/contracts`; Playwright per workflow + axe (Q38). The backlog ticket says React Router; the accepted ADR
  says TanStack Router and is followed (typed search params carry the context bar).
- Layout: `apps/web` (Vite 7 + React 19 + TypeScript strict), `packages/contracts` (OpenAPI snapshot, generated
  types, client), root `package.json` as the npm workspace root; `edge/` stays. F-03's directory moves follow
  slice 1 in their own change.
- Components: Radix Primitives (unstyled) + CSS Modules + tokens; no Tailwind, no runtime CSS-in-JS; forms with
  react-hook-form + zod; decimal strings and `Intl` for money and dates; eslint (typescript-eslint, react-hooks,
  jsx-a11y) + prettier (LF); `size-limit` 250 kB gzip initial; route-level splitting; build id from `BUILD_SHA`
  compared with `/healthz` for the "new version available" state.
- Serving: Workers static assets from `apps/web/dist` with SPA fallback and `run_worker_first` for `/api/*`,
  `/healthz`, `/legacy*`, `/static/*` (the IdP callback navigation must reach the Worker); same origin as the API
  (no CORS; the `agentledger_idp` cookie and `/#session=` redirect stay same-origin); `_headers` carries the CSP
  for `index.html` and immutable caching for `/assets/*`. The no-build UI remains at `/legacy` until each screen is
  replaced, then is deleted.
- Auth in the SPA: bearer token in sessionStorage + memory (no localStorage, no refresh token); strict CSP; 401 →
  sign-in with return path; 403 `step_up_required` → TOTP dialog and one retry (WorkOS: provider round trip and a
  "Retry"); idle warning at ~25 min; follow-up API ticket: HttpOnly `SameSite=Strict` session cookie minted by the
  API with an Origin check, keeping bearer tokens for agents and MCP.
- Generated client: `openapi-typescript` + `openapi-fetch` + `openapi-react-query`; a CI drift gate regenerates
  from the exported `/openapi.json` and fails on diff. The API must gain pydantic response and request models
  (`api/schemas.py`) and `generate_unique_id_function` for useful types — per slice.
- Required API changes (slice 1 follow-up, after F-08 lands): typed models and operationIds; `GET /legacy`; an
  inline file route (`?inline=1`) limited to PDF/PNG/JPEG with `Content-Disposition: inline`, `nosniff` and
  `Content-Security-Policy: sandbox; default-src 'none'`; paginated `GET /api/clients/{id}/documents`; `firm` on
  `/api/me`; `bootstrap-admin --password-env`; `scripts/export_openapi.py`; a smoke check that `GET /` serves the
  built app with the expected build.
- Testing: Vitest + Testing Library + MSW; a "states contract" test rendering every route under 200-empty, 401,
  403, 404, 409, 410, 500 and a network error; Playwright against the API in non-dev multi-firm mode seeded by a
  bootstrap script, TOTP computed in Node; axe on every page and forced state (WCAG 2.2 AA; fail on
  serious/critical); projects chromium desktop and mobile. CI: a `web` job (contracts drift, lint, typecheck,
  unit, build, e2e) feeding the deploy jobs the tested `dist`; add a `pull_request` workflow (none exists).
- Extraction-dependent e2e (fact conflicts) needs an env-gated fixture router (`AGENTLEDGER_AI_FIXTURES`), refused
  outside dev/e2e like `DEV`.

## Slices

1. Sign-in, firm workspace, clients list/detail with documents, inbox, team (plus the foundation: shell, context
   bar, states, auth, contracts, tests) — in progress; API-side typing follows.
2. Return workspace: review queue, `/returns/:rid` with status, allowed actions, waiting-on, diagnostics,
   crosscheck; `/returns/:rid/review` field list from the `IndividualReturn` schema with provenance chips, the
   document open beside the field (no coordinates yet), conflicts panel, fact history, dispositions; Frozen when
   hash-bound; `unknown` transmission recovery.
3. Platform console and firm administration: firms (create with step-up, invite link, provisioning / offboarding
   status), SSO-MFA attestations, sign-in activity; team grants, reviewer authority, disable. API gap: no
   offboarding route yet.
4. Client portal: tasks, uploads, return outcome, activity; mobile project.

## Risks

Spec absent from the repo; bundle size (no pdf.js until coordinates exist); auth storage until the cookie ticket;
the inline document route must ship with the sandbox CSP and allow-list; `assets` inheritance in wrangler env
blocks unverified until a dry run; cold container (asset-first loads and the "waking the service" state); Windows
line endings (LF pins); Node 24 locally vs 22 in CI; TypeScript 7 tooling support (pin 5.9 in the web workspace if
needed).
