# The web app (apps/web) and the API contract (packages/contracts)

Backlog F-10, slice 1: the React application shell with sign-in, the firm workspace, clients with documents, the
intake inbox and the team screen. This document is the working notes for the people who continue it: the layout,
how to run and test it, what the API has to change next, what to wire at integration (Cloudflare, CI), the
decisions taken and the open risks. ADR-0005 holds the architecture decision; this is the operating manual.

## 1. Layout

```text
package.json                 npm workspace root: apps/web, packages/contracts (engines.node >=22, .nvmrc 22)
apps/web/                    the app: Vite 7 + React 19 + TypeScript 5.9 (strict, noUncheckedIndexedAccess,
                             noImplicitOverride, exactOptionalPropertyTypes)
  index.html                 one entry; production builds get a Content-Security-Policy meta tag (vite.config.ts)
  vite.config.ts             TanStack Router plugin (file routes, automatic code splitting), React, the CSP plugin,
                             __BUILD_SHA__ from ../../BUILD_SHA, dev/preview proxy to the API
  src/main.tsx               boot: read and strip the URL fragment, GET /api/auth/config, GET /api/me, mount
  src/app/                   App (providers), router (createAppRouter + RouterContext), queryClient, bootState
  src/api/                   the one API client (packages/contracts) wired to session, step-up and idle tracking
  src/auth/                  session (sessionStorage + memory), AuthProvider/AuthStore, can.ts (roles), guards.ts
                             (beforeLoad), StepUpProvider (TOTP dialog / provider redirect), IdleWarning, bridges
  src/shell/                 AppShell (skip link, nav by role, banners, focus on route change), ContextBar
                             (firm · entity · engagement · period · basis, state in typed search params),
                             contextState.ts (pure helpers), Banners, useBuildCheck
  src/ui/                    Button (disabled-with-reason), Dialog, Tooltip, Field, Chip, Card, Toast, DataTable
                             (TanStack Table + Virtual); ui/states: every required state and QueryBoundary
  src/routes/                file-based routes (TanStack Router); routeTree.gen.ts is generated and committed
  src/screens/               one component per screen; RouteError maps thrown errors to states
  src/queries/               query keys and queryOptions factories
  src/styles/                tokens.css (design tokens, dark, high contrast, reduced motion), base.css
  src/test/                  Vitest setup, MSW fixtures/handlers typed from the contract, renderApp, the states
                             contract test, the axe smoke test, interaction flows
  e2e/                       Playwright: servers.mjs (launcher), seed.py (platform seed), helpers.ts, *.spec.ts
  .size-limit.js             the 250 kB gzip budget, computed from Vite's manifest (entry + static imports)
  public/_headers            response headers the edge adds to the static assets (frame-ancestors, nosniff,
                             referrer policy, immutable caching of /assets); the page's CSP stays in the meta tag
packages/contracts/          the contract: openapi.json (written by scripts/export_openapi.py from the API's models),
                             src/schema.d.ts (generated from it by openapi-typescript), src/types.ts (names the
                             generated schemas the app uses; hand-written only for what the API does not declare),
                             src/client.ts (fetch layer with the session, step-up and error rules), src/endpoints.ts
                             (typed functions per route), scripts/generate.mjs, check.mjs (+ render.mjs),
                             snapshot.mjs (from a running server; superseded by the export script)
src/agentledger/api/schemas.py  the API's request and response models (pydantic v2): the source of openapi.json
scripts/export_openapi.py    writes openapi.json from the application without a server; --check is the drift gate
.github/workflows/web.yml    the web job, a reusable workflow called by release.yml (push to main) and ci.yml (PRs)
docs/WEB.md                  this file
```

Routes in this slice: `/sign-in`, `/sign-in/verify`, `/accept/$token`, `/` (dispatch by role), `/clients`,
`/clients/new`, `/clients/$clientId` (overview), `/clients/$clientId/documents`,
`/clients/$clientId/documents/$docId`, `/clients/$clientId/profile`, `/inbox`, `/team`, `/platform/firms`,
`/portal`. The previous interface stays reachable at `/legacy#/...` (the API serves it at `/legacy` and still at `/`
on its own port; at the edge the app owns `/`).

## 2. How to run

Prerequisites: Node 22+ (CI uses 22; `.nvmrc`), the Python virtualenv of the repository (`.venv`) for the API.

```sh
npm ci                                   # at the repository root (workspaces)
npm run -w apps/web dev                  # Vite on http://127.0.0.1:5173, proxying /api, /healthz, /static,
                                         # /legacy, /openapi.json to http://127.0.0.1:8740 (API_PORT overrides)
```

The dev server expects an API. For real multi-firm behaviour start one in non-dev mode (set
`AGENTLEDGER_MASTER_KEY`, `AGENTLEDGER_ALLOW_SQLITE=1` for a local SQLite trial, `AGENTLEDGER_IDENTITY=local`):
`.venv/Scripts/python.exe -m agentledger.cli serve --port 8740 --no-agents`. `--dev` (demo identities, no
sign-in) also works with the app for browsing screens, but the sign-in flows need the real mode.

Checks (all run in CI's `web` job, section 5):

```sh
npm run -w apps/web lint                 # eslint (typescript-eslint strict type-checked, react-hooks, jsx-a11y)
                                         # + prettier --check
npm run -w apps/web typecheck            # tsc -b (app, e2e, configs)
npm run -w apps/web test                 # vitest: unit, states contract, axe smoke, flows (143 tests)
npm run -w apps/web build                # tsc -b && vite build -> apps/web/dist (+ .vite/manifest.json)
npm run -w apps/web size                 # size-limit against the 250 kB gzip budget (needs a build)
npm run -w apps/web e2e                  # Playwright (starts the API and vite preview itself, section 3)
npm run -w packages/contracts test       # the fetch layer's rules (9 tests)
npm run -w packages/contracts typecheck
npm run -w packages/contracts check      # schema.d.ts is what openapi.json generates
```

The contract drift gate (CI runs exactly this; run it after any change to `src/agentledger/api/`):

```sh
.venv/Scripts/python.exe scripts/export_openapi.py   # packages/contracts/openapi.json from the API, no server needed
npm run -w packages/contracts generate               # src/schema.d.ts from openapi.json
git diff --exit-code packages/contracts              # anything to commit means the committed contract was stale
```

`python scripts/export_openapi.py --check` is the same gate for the JSON alone (exit 1 on drift, nothing written).

## 3. End-to-end tests

`npm run -w apps/web e2e` runs `playwright.config.ts`, whose `webServer` is `node e2e/servers.mjs`:

1. A fresh `AGENTLEDGER_HOME` in the OS temp directory (`E2E_HOME` overrides) with copies of `rules/`, `config/`,
   `golden/`, `domains/`, `playbooks/`, `evals/` and `pyproject.toml`, exactly what `tests/conftest.py`'s `home`
   fixture copies.
2. `e2e/seed.py`, run with the API's environment, does what `tests/test_tenancy.py` does through the `Platform`
   class: bootstrap administrator, login + TOTP enrolment, create firm `rivera-cpa`, invite, accept, enrol. It
   writes `<home>/e2e-seed.json` (emails, the shared e2e password, each account's TOTP secret and the TOTP step the
   seed used). Every spec has its own accounts because a one-time code is refused twice within its 30-second step.
3. The API in multi-firm mode (`python -m agentledger.cli serve --port 8740 --no-agents`, never `--dev`) with the
   suite's environment pins: `AGENTLEDGER_DATABASE=sqlite`, `AGENTLEDGER_ALLOW_SQLITE=1`, `AGENTLEDGER_IDENTITY=local`,
   `AGENTLEDGER_BLOBS=file`, `AGENTLEDGER_OLLAMA_URL=http://127.0.0.1:9`, `AGENTLEDGER_AGENTS=0`, a random
   `AGENTLEDGER_MASTER_KEY`; `AGENTLEDGER_DEV_AUTH` removed; bucket, WorkOS and database URLs blanked so nothing
   from a developer's `.env` is reached (`.env` is never read: the home is the temp directory).
4. `vite build` (`E2E_SKIP_BUILD=1` reuses `dist/`) and `vite preview` on 4173 (`E2E_WEB_PORT`), proxying to the
   API (`E2E_API_PORT`, 8740). Python: the repository's `.venv` (`E2E_PYTHON` overrides).

The launcher is plain Node (no shell, quoted paths) and kills both children when Playwright stops it (taskkill
`/T` on Windows). To debug by hand: `node e2e/servers.mjs` in `apps/web`, then `npx playwright test --ui`
(`reuseExistingServer` is on outside CI).

TOTP codes are computed in Node with `otpauth` (`e2e/helpers.ts`): the helper tracks the last step each account
used in this worker process and uses the next one, inside the API's one-step drift window, waiting for the clock
only when it must. Step-ups are forced in the `platform` spec with `page.route` (the first POST answers
`403 step_up_required`), because a real sign-in is fresh for five minutes; the step-up code itself goes to the
real `POST /api/auth/step-up`.

Specs: `signin` (enrolment with QR and manual key, wrong code, lockout after five wrong passwords, sign-out),
`invite` (firm admin invites a CPA; acceptance; enrolment; single use), `platform` (bootstrap admin signs in,
creates a firm through the step-up dialog, gets the invite link; 403 on client data), `clients` (create, context
bar, period in the URL, basis via Profile, search, forced error/forbidden/unreachable states), `documents`
(multi-file upload with a recognised form and an unreadable binary -> partial success; download through a signed
link as an attachment; versions; inbox filing), `keyboard` (sign in and reach a client's documents keyboard-only,
skip link, focus on route change), `fragments` (`#signin_error`, legacy `#/accept/<token>`, `#step_up=ok`,
`#link=ok`, a bad `#session=`). `@axe-core/playwright` runs on every page after load and in the forced states,
tags wcag2a, wcag2aa, wcag21aa, wcag22aa; serious and critical findings fail, the rest are printed. Projects:
`chromium-desktop` (all specs) and `chromium-mobile` (Pixel 7, the sign-in spec).

Results of the local run are in the hand-off report (see section 9 for the exact counts of the last run).

## 4. The API changes the first slice needed

Made in the follow-up (2026-10-09), after the slice shipped with honest fallbacks. What each one is now, and what is
still open.

1. **Typed models and operation ids** (`src/agentledger/api/schemas.py`, mypy-checked). Request models replace the
   `Body(...)` dicts of the sign-in, invitation, disable, link, firm, client, facts and assign routes; response
   models cover auth config, the login steps (`MfaStep | EnrolStep`), MFA and accept results, `Me` (with `firm`),
   firm users, invites, auth events, firms, health, links, clients (list, detail, create), the dashboard, packs, the
   documents page, the review queue, upload results (`IngestedDocument | DuplicateDocument`), assigned documents,
   versions, the CRM pipeline and tasks. `generate_unique_id_function` makes operation ids the handler names
   (`get_me`, `client_detail`, ...). `schema.d.ts` now carries every shape and `types.ts` names them; the hand-written
   types left are `IdpPurpose`, `EngagementStage`, `AccountingBasis`, `DocumentStatus`, `FirmStatus` and the error
   body. Still open: `endpoints.ts` as `createClient<paths>()` from openapi-fetch and `openapi-react-query` for the
   hooks (the middleware in `client.ts` already has that shape); request models for the routes outside this slice
   (grants, reviewer, SSO attestations, returns, evidence, CRM, business) which still take dicts and are `unknown` in
   the contract.
2. **`scripts/export_openapi.py`** writes `app.openapi()` (keys sorted, LF) to `packages/contracts/openapi.json`
   from the application imported in a throwaway home with the test suite's environment pins; `--check` exits 1 on
   drift. CI runs it, then `generate`, then `git diff --exit-code packages/contracts` (section 2). `snapshot.mjs`
   remains for a running server but is no longer how the snapshot is made.
3. **`/legacy`** serves the previous interface's page (the same page as `/`; its asset URLs are absolute `/static/...`),
   so the old screens stay reachable through the edge at `/legacy#/...` while the app owns `/`.
4. **Inline file route**: `GET /api/documents/{id}/file?inline=1` (a session header or the same signed `dl` link as a
   download) serves a PDF, PNG or JPEG with its real media type, `Content-Disposition: inline`,
   `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox; default-src 'none'` and `Cache-Control:
   private, no-store`. The allow-list checks the extension and the bytes' signature; anything else (HTML, SVG, XML,
   Office files, a PDF-named HTML) and every request without `inline=1` stays `attachment` + `application/octet-stream`
   (now also with `nosniff`). Tests upload HTML and SVG under their own and under image names and assert they are never
   inline. Still open: the app does not frame files yet (`DownloadButton` downloads); the viewer is slice 2's
   document-beside-the-field.
5. **Paged documents**: `GET /api/clients/{id}/documents?cursor=&limit=` (newest first, `received_at` then `id`
   descending, deleted excluded, limit 1 to 200, default 50) returns `{items, next_cursor, total}` with the same
   fields the detail embeds. The documents tab reads it through an infinite query (`queries.documents`, key under the
   client's so an upload invalidates it) with "Load more"; the detail's `documents` (100 most recent) remain for the
   overview count and the document page. Still open: a single-document route, so the document page stops saying "not
   among the 100 most recent".
6. **`firm` on `GET /api/me`**: `{id, name, status}` for firm users, `null` for platform administrators; the context
   bar shows the name. `GET /api/platform/firms/{id}` exists for a selected-firm context (platform administrators).
7. **`platform bootstrap-admin --password-env NAME` and `--password-stdin`**; the interactive prompt stays the default.
   The e2e seed still uses the `Platform` class (it needs it for the rest of the seed anyway).
8. **Smaller**: `GET /api/packs` lists the industry packs (`id, title, description, status, facts`; the new-client form
   still hard-codes the list, wiring it is open); `PATCH /api/clients/{id}/facts` removes a fact sent as `null` and the
   profile screen sends `null` for a blanked recorded fact; `POST /api/clients` validates `id` against the database's
   rule (`^[a-z0-9][a-z0-9_-]{0,63}$`, a 422 naming the field) and answers 409 for a duplicate; the app's own rule
   was changed to the same (it allowed dots, which PostgreSQL refuses, and required two characters).

## 5. Integration (done this round)

**wrangler.jsonc**: the `assets` block (top level and repeated in `env.staging` and `env.production`) serves
`apps/web/dist` with the single-page-application fallback; `run_worker_first` is `/api/*`, `/healthz`, `/legacy`,
`/legacy/*`, `/static/*`, `/openapi.json`, `/internal/*` (the union of the two earlier plans: the OpenAPI document
stays reachable, and `/internal` keeps its explicit 404 at the Worker instead of a 200 app shell). Everything else is
served by the assets binding before the Worker runs, so `edge/src/index.ts` only declares `ASSETS: Fetcher` and routes
nothing to it. The app calls `/api/*` relatively: one origin, no CORS. `npx wrangler deploy --dry-run --env staging`
lists `env.ASSETS` and the 39 files of `dist` (docs/DEPLOY.md, dry run record).

**Headers**: `apps/web/public/_headers` (copied into `dist`) adds to every path `Content-Security-Policy:
frame-ancestors 'none'`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`, and
`Cache-Control: public, max-age=31536000, immutable` to `/assets/*`. The page's policy itself has one source, the meta
tag `vite.config.ts` injects; the header carries only what a meta tag cannot (frame-ancestors). Two policies apply
together, which is the intended intersection. The same plugin injects `<meta name="agentledger-build">` with
`BUILD_SHA`, which `scripts/smoke.py` checks on `GET /` against the expected build (the new `web app` check; a `--dev`
API without assets in front passes only with `SMOKE_DEV=1`).

**CI**: `.github/workflows/web.yml` is a reusable workflow (`workflow_call`, input `build_sha`): Node 22 with the npm
cache, `npm ci`, Python 3.12, `pip install -e .`, the contract drift gate, contracts check/typecheck/test, web
lint/typecheck/test, the build (stamped when `build_sha` is given), size, `playwright install --with-deps chromium`,
the e2e run with `E2E_PYTHON=python` and `E2E_SKIP_BUILD=1` (the build under test is the one just made), then
`playwright-report` (always) and `web-dist` as artifacts; `timeout-minutes: 20`. `release.yml` calls it as job `web`
with `build_sha: github.sha`; `staging` needs it and both deploy jobs download `web-dist` into `apps/web/dist` before
`wrangler deploy`, so the assets shipped are the ones tested and the app's build equals the API's. `ci.yml` runs on
pull requests: the `verify` steps of release.yml (repeated, not shared, so release.yml stays the one place that
decides what is deployable; `minimum-versions` is left to main) and the same `web` job unstamped.

## 6. Decisions

- **TanStack Router** (file-based, typed params and search params), per ADR-0005; the context bar's state (period,
  engagement) is URL state validated by `clientSearchSchema`, so links carry the context and malformed values are
  dropped rather than failing the route. TanStack Query holds server state; mutations invalidate by the keys in
  `src/queries`.
- **Session in sessionStorage + memory**, no localStorage, no refresh token, no token in URLs. The API's own
  limits apply (30 min idle, 12 h absolute, 5 min freshness); the idle warning comes at 25 minutes of no API
  activity. Follow-up: an HttpOnly, SameSite=Strict session cookie set by the API (with CSRF protection for the
  mutating routes), which removes the token from JavaScript entirely; `client.ts` already treats the token as
  something it reads per request, so the change is confined to `session.ts` and the API.
- **CSP**: production builds carry a `Content-Security-Policy` meta tag (`script-src 'self'`, `connect-src
  'self'`, `img-src 'self' data: blob:`, `style-src 'self' 'unsafe-inline'`, `object-src 'none'`, `base-uri
  'self'`, `form-action 'self'`). `'unsafe-inline'` for styles is there because Radix's scroll lock injects a
  `<style>` element; a nonce-based policy at the edge can tighten it. `frame-ancestors` must come from the edge
  (section 5).
- **The 401 rule is by route, not by prefix.** The plan said "401 outside /api/auth/*"; literally applied, a 401
  on `/api/auth/users` or `/api/auth/invite` (session-bound routes that happen to live under /api/auth) would
  never end the session. `client.ts` exempts exactly the credential routes (login, mfa, accept, step-up, config,
  idp/start, logout), where a 401 means a wrong password or code.
- **Step-up**: local accounts get the TOTP dialog and the request is retried once; provider accounts are sent to
  the provider with the action remembered in sessionStorage, and on `#step_up=ok` a banner offers "Retry" (a link
  back to where they were; the request body is not replayed).
- **Authority actions are disabled, never hidden**, with the API's own reason as tooltip and accessible
  description (`Button disabledReason`); navigation a role cannot reach is hidden; route guards render the
  Forbidden state with the API's text before any client-scoped query, so platform administrators never trigger
  client reads. The API remains the enforcement point and its 403 is rendered when the two disagree.
- **Required states are components** under `src/ui/states`, chosen by `QueryBoundary` from the query status and
  `mapError`; the states contract test renders every route against 200/empty/401/403/404/409/410/500/network.
  Data already on screen stays visible with a Stale marker when a refetch fails or the browser is offline.
- **Uploads**: one request per file (the API takes one file); results are per part (a zip or email yields several).
  The API never refuses a file type: an unreadable file becomes a "needs review" document in the inbox at 0 %
  confidence, so the partial-success panel distinguishes filed / needs attention / already stored / failed.
- **Downloads** fetch the signed link's bytes and hand them to the browser as a download, so a 410 (deleted under
  retention, with its receipt) or a 409 (integrity check failed) is shown as a state instead of a JSON page.
- **TanStack Table v8** (`^8.21`), not v9: v9 (released with a new plugin API) would have cost the slice its
  time; the migration is mechanical and local to `DataTable.tsx`.
- **TypeScript 5.9 at the repository root** (was `^7.0.2`): the `typescript@7` package ships no JavaScript
  compiler API, and typescript-eslint's hoisted `ts-api-utils` resolved it and crashed. Pinning 5.9 only in the
  workspaces was not enough because of hoisting. `npx tsc --noEmit -p edge` passes under 5.9 (checked).
- **Vite 7**, as planned, with `@vitejs/plugin-react` 5 (the 6.x line requires Vite 8).
- **ESLint 9** (jsx-a11y does not yet declare ESLint 10 as a peer).
- **Platform firms screen has a "create firm" form** although the route list said "list only": the `platform`
  e2e spec (create a firm with step-up, show the invite link) needs it, and it mirrors the previous interface.
- **The firm name** comes from `me.firm.name`; the id is the fallback when a session has no firm record.
- **Response models: rows pass extras through, assembled payloads declare every key.** A model that is a table row
  (`Row`, `extra="allow"`) never filters a column the models do not know, so a migration cannot silently drop a
  field the previous interface reads; a payload a handler assembles (`Shape`) declares all its keys. Keys a handler
  sets only in some cases (`engaged` for staff, the business-only parts of a detail, the CPA parts of the dashboard)
  are optional and those routes serialize with `response_model_exclude_unset=True`, so the JSON keeps the previous
  key set exactly (absent, not null). The generator runs with `defaultNonNullable: false` so a defaulted field stays
  optional in TypeScript, as it is on the wire. Two normalisations are deliberate: SQLite's `disabled` 0/1 becomes a
  JSON boolean, and roles and client kinds are literal unions, so a malformed value is a 422 naming the field
  (previously a 403 "invalid role" or a 500).
- **`cursor`, not `after`**, names the documents page parameter, as this document specified; the cursor is opaque
  (`received_at` and `id` of the last item, base64) and a malformed one is a 400.
- **The inline allow-list checks both the extension and the bytes**: a `.pdf` whose bytes are not `%PDF-` is a download.
  Anything renderable as a document in the app's origin (HTML, SVG, XML) is never inline, whatever the request asks.
- **Client ids follow the database's rule**, not the looser one the form had: PostgreSQL's CHECK is the authority
  (`pg/migrations/0001_ledger_core.sql`) and SQLite stores get the same check from the API.
- **A reusable workflow for `web`, duplicated steps for `verify`** in `ci.yml`: the web job is new and identical in
  both places, so it is shared; `verify` carries release.yml's `deployable` output and its service containers, and
  keeping release.yml self-contained there was preferred over a second reusable workflow.
- **Theme tokens** continue the previous interface's palette so both can coexist; dark mode follows the system
  (or `data-theme`), `prefers-contrast: more` removes tints and strengthens lines, `prefers-reduced-motion`
  collapses durations to zero.

## 7. Accessibility notes

Keyboard: skip link, focus moves to the main region on every route change, dialogs trap and return focus, sortable
headers are buttons, every control has a label, disabled-with-reason buttons stay focusable so the reason can be
read. Live regions: toasts (`aria-live="polite"`; errors `role="alert"`), state panels are named regions
(`role="status"` or `"alert"`). Checked by vitest-axe on every screen (colour contrast excluded in jsdom) and by
`@axe-core/playwright` in the browser, including forced states.

## 8. Open risks

- The document page still finds its document among the detail's 100 most recent (section 4, item 5); the firm id and
  the "100 most recent" note on the documents tab are gone.
- A response that does not fit its model is a 500 (`ResponseValidationError`) where it used to be served as is. The
  models were typed from the tables and the handlers, the touched routes pass on SQLite and PostgreSQL, and `Row`
  models accept unknown columns; a column whose type changes (an integer becoming text) would still surface this way.
- `Content-Security-Policy: sandbox` on inline PDFs: Chrome has historically refused to run its PDF viewer in a
  sandboxed context without `allow-scripts`; whether the inline route renders PDFs in a sandboxed frame in current
  browsers is unverified until slice 2 frames one (PNG and JPEG are plain images and unaffected).
- A numeric fact stored as text (`employees: "7"` through the API) makes the client detail a 500 (`TypeError` in
  expression evaluation, pre-existing: the app sends numbers, the API does not type facts). Facts need a type rule.
- The `web` CI job runs on Linux for the first time: `playwright install --with-deps` needs `sudo` (present on
  GitHub's runners), the launcher is told `E2E_PYTHON=python`, the whole job has 20 minutes, and the e2e timing
  assumptions (TOTP steps, lockout) are the same as locally. The first run on GitHub is the real test of it.
- `_headers` and `run_worker_first` semantics are exercised by the first real deploy, not by the dry run.
- The step-up retry after a provider round trip is a link back, not a replay; forms are not restored.
- The initial JavaScript is 168 kB gzip of the 250 kB budget, most of it React, the router and the query library.
  Each screen's chunk is small (1 to 22 kB gzip; the verify screen carries the QR encoder, the forms carry zod).
- A duplicate client id is a 500 from the API (SQLite constraint) until the API validates it; the app validates the
  format and shows the API's text.
- The e2e run creates data in a temporary home that the next run deletes; nothing touches `state/` or `tenants/`
  in the repository.

## 9. Last local run (2026-10-09, after the API follow-up; Windows 11, Node 24, Python 3.13 venv; CI uses Node 22 / Python 3.12)

| Check | Result |
| --- | --- |
| `python scripts/export_openapi.py` + `npm run -w packages/contracts generate` + `check` | 106 paths, 53 schemas; `--check` clean; schema.d.ts matches openapi.json |
| `npm run -w packages/contracts test` / `typecheck` | 9 tests passed; ok |
| `npm run -w apps/web lint` | eslint 0 errors (1 warning: TanStack Table's `useReactTable` is not memoisable by the React Compiler lint, expected), prettier clean |
| `npm run -w apps/web typecheck` | ok, against the generated types |
| `npm run -w apps/web test` | 9 files, 143 tests passed (unit, states contract 12 routes × up to 9 outcomes incl. the paged documents route, axe smoke on 12 screens, flows) |
| `npm run -w apps/web build` | ok; entry 525.4 kB / 168.7 kB gzip; `dist/index.html` carries the CSP and build meta tags, `dist/_headers` is copied |
| `npm run -w apps/web size` | initial JavaScript 168.43 kB gzip of the 250 kB budget; initial CSS 3.12 kB gzip of 40 kB |
| `npm run -w apps/web e2e` (fresh servers) | 17 passed, 0 failed (13 chromium-desktop, 4 chromium-mobile), 34.8 s; axe 0 serious or critical findings |
| `ruff check src tests scripts`, `mypy` | clean (mypy: 60 files, `api/schemas.py` included) |
| Python, SQLite: `tests/test_health_and_smoke.py test_tenancy.py test_identity_workos.py test_security.py test_engagements.py test_reaudit_952ee96_api.py test_reaudit_fe75514.py test_audit_findings.py` | 67 passed, 1 skipped |
| Python, SQLite: `tests/test_web_contracts.py` (new) | 14 passed |
| Python, PostgreSQL (`AGENTLEDGER_DATABASE=postgres`, local container on 55432): the four named files + `test_web_contracts.py` + `test_engagements.py` | 50 passed, 1 skipped |
| `npx tsc --noEmit -p edge` | ok |
| `npx wrangler deploy --dry-run --outdir <tmp> --env staging` (placeholder credentials) | exit 0; `env.ASSETS (Assets)` listed, 39 files read from `apps/web/dist` |
| `.github/workflows/{ci,web,release}.yml` | parse (`yaml.safe_load`); `staging` needs `[verify, minimum-versions, web]` |

Re-running the suite against servers that are already up (`reuseExistingServer`) replays the data-changing specs on the same seed: the enrolment test finds its accounts enrolled and the lockout test finds them locked (15 minutes), so those four tests fail by design on a second run. `npm run -w apps/web e2e` always starts fresh servers with a fresh seed.
