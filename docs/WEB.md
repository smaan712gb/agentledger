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
packages/contracts/          the contract: openapi.json (snapshot), src/types.ts (hand-maintained shapes),
                             src/schema.d.ts (generated, all-unknown today), src/client.ts (fetch layer with the
                             session, step-up and error rules), src/endpoints.ts (typed functions per route),
                             scripts/snapshot.mjs, generate.mjs, check.mjs (+ render.mjs)
docs/WEB.md                  this file
```

Routes in this slice: `/sign-in`, `/sign-in/verify`, `/accept/$token`, `/` (dispatch by role), `/clients`,
`/clients/new`, `/clients/$clientId` (overview), `/clients/$clientId/documents`,
`/clients/$clientId/documents/$docId`, `/clients/$clientId/profile`, `/inbox`, `/team`, `/platform/firms`,
`/portal`. The previous interface stays reachable (the Python API serves it at `/` today; `/legacy` once the API
change below lands).

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

## 4. The API changes the next slice needs

None of these were made this round (the Python side was being edited concurrently); the app does the honest
fallback in each case and says so on screen where it matters.

1. **Typed response models and operationIds.** Every handler in `src/agentledger/api/app.py` returns
   `dict[str, Any]`, so `/openapi.json` carries no response schemas and `packages/contracts/src/schema.d.ts` is all
   `unknown`. Give each route a pydantic response model and an `operation_id`; then `schema.d.ts` replaces
   `src/types.ts`, `endpoints.ts` becomes `createClient<paths>()` from openapi-fetch (the middleware in `client.ts`
   has the same shape) and `openapi-react-query` can type the hooks. Until then `types.ts` is the contract and its
   header lists where each shape was read from.
2. **`scripts/export_openapi.py`**: write `app.openapi()` sorted with LF to `packages/contracts/openapi.json`
   without a running server, so CI can run `npm run -w packages/contracts check` after `generate` and fail on
   drift. Today `scripts/snapshot.mjs` fetches it from a running API (how the committed snapshot was made).
3. **`/legacy`**: serve `src/agentledger/web` at `/legacy` (and `/static` as now) so the SPA can own `/`. The
   Worker routes `/legacy*` to the container (section 5). The app links to `/legacy` from its boot failure screen
   and the `<noscript>` text.
4. **Sandboxed inline file route**: `GET /api/documents/{id}/file` sends `Content-Disposition: attachment` and
   `application/octet-stream`. A viewer needs an inline variant with the real media type, `Content-Security-Policy:
   sandbox` and `X-Content-Type-Options: nosniff`, served from a path the app can frame. Until then files are
   downloaded, never rendered (`DownloadButton`).
5. **Paginated documents list**: `GET /api/clients/{id}` embeds the 100 most recent documents; the documents tab
   reads them from there and says so. Add `GET /api/clients/{id}/documents?cursor=&limit=` (and a `total`), then the
   tab stops depending on the detail payload and the document page stops saying "not among the 100 most recent".
6. **`firm` on `GET /api/me`** (`{id, name, status}`): the context bar shows `firm_id` because the name is not
   there. Platform administrators also need `GET /api/platform/firms/{id}` for a selected-firm context.
7. **`platform bootstrap-admin --password-env NAME`** (or `--password-stdin`): the CLI prompts interactively, so
   the e2e seed calls `Platform.bootstrap_admin` from Python instead of the CLI. A non-interactive option lets the
   seed (and operators) use the CLI.
8. Smaller: `GET /api/packs` (the industry packs; the new-client form hard-codes the shipped list),
   `PATCH /api/clients/{id}/facts` cannot remove a recorded fact (the merge keeps it; the profile screen says a
   blank does not clear), and `POST /api/clients` has no validation of `id` (a duplicate is a 500 from SQLite; the
   app validates the format client-side).

## 5. Integration to add later (not done this round)

**wrangler.jsonc** (per environment, next to the container): serve `apps/web/dist` as static assets with SPA
fallback, and let the Worker forward `/api/*`, `/healthz`, `/legacy*`, `/static/*`, `/openapi.json` to the
container. The app calls `/api/*` relatively, so everything is one origin and no CORS is involved.

```jsonc
"assets": {
  "directory": "./apps/web/dist",
  "binding": "ASSETS",
  "not_found_handling": "single-page-application",
  "run_worker_first": ["/api/*", "/healthz", "/legacy", "/legacy/*", "/static/*", "/openapi.json"]
}
```

In `edge/src/index.ts`, requests that reach the Worker for a path outside that list go to `env.ASSETS.fetch(request)`;
the rest go to the container as today. Response headers to add at the edge for the HTML: `Content-Security-Policy`
(the same policy as the build-time meta tag, plus `frame-ancestors 'none'`, which a meta tag cannot carry),
`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`. The release workflow stamps `BUILD_SHA` before
`vite build` and `wrangler deploy` so the app's `__BUILD_SHA__` equals the API's `/healthz` `build`; a mismatch
after a release shows the "new version available" banner.

**CI `web` job** (`.github/workflows/release.yml`, alongside `verify`; `staging`/`production` need it):

```yaml
web:
  runs-on: ubuntu-latest
  timeout-minutes: 30
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v4
      with: { node-version: "22", cache: npm }
    - uses: actions/setup-python@v5
      with: { python-version: "3.12" }
    - run: pip install -e .
    - run: npm ci
    - run: npm run -w packages/contracts check
    - run: npm run -w packages/contracts test
    - run: npm run -w apps/web lint
    - run: npm run -w apps/web typecheck
    - run: npm run -w apps/web test
    - run: npm run -w apps/web build
    - run: npm run -w apps/web size
    - run: npx --prefix apps/web playwright install --with-deps chromium
    - run: npm run -w apps/web e2e
      env: { E2E_PYTHON: python, CI: "true" }
    - uses: actions/upload-artifact@v4
      if: failure()
      with: { name: playwright-report, path: apps/web/playwright-report }
```

`E2E_PYTHON=python` points the launcher at the interpreter `setup-python` installed (there is no `.venv` in CI).
The deploy jobs then run `npm run -w apps/web build` before `wrangler deploy` so the assets ship with the image.

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
- **The firm name**: `GET /api/me` has only `firm_id`, which is shown as is (section 4, item 6).
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

- The slice ships without the API changes in section 4; the two fallbacks visible to people are the firm id in the
  context bar and the "100 most recent" note on documents.
- `src/types.ts` is hand-maintained: a Python change to a JSON shape is not caught until a screen misbehaves (the
  states contract and e2e tests would catch the common ones). Item 1 of section 4 removes this class of risk.
- The step-up retry after a provider round trip is a link back, not a replay; forms are not restored.
- The initial JavaScript is 168 kB gzip of the 250 kB budget, most of it React, the router and the query library.
  Each screen's chunk is small (1 to 22 kB gzip; the verify screen carries the QR encoder, the forms carry zod).
- A duplicate client id is a 500 from the API (SQLite constraint) until the API validates it; the app validates the
  format and shows the API's text.
- The e2e run creates data in a temporary home that the next run deletes; nothing touches `state/` or `tenants/`
  in the repository.

## 9. Last local run (2026-10-09, Windows 11, Node 24, Python 3.13 venv; CI uses Node 22 / Python 3.12)

| Check | Result |
| --- | --- |
| `npm ci` (root) | ok |
| `npm run -w apps/web lint` | eslint 0 errors (1 warning: TanStack Table's `useReactTable` is not memoisable by the React Compiler lint, expected), prettier clean |
| `npm run -w apps/web typecheck` | ok |
| `npm run -w apps/web test` | 9 files, 143 tests passed (unit, states contract 12 routes × up to 9 outcomes, axe smoke on 12 screens, flows) |
| `npm run -w apps/web build` | ok; entry 525 kB / 168.6 kB gzip, 21 route and shared chunks |
| `npm run -w apps/web size` | initial JavaScript 168.33 kB gzip of the 250 kB budget; initial CSS 3.11 kB gzip of 40 kB |
| `npm run -w packages/contracts test` / `typecheck` / `check` | 9 tests passed; ok; schema.d.ts matches openapi.json (102 paths) |
| `npm run -w apps/web e2e` | 17 passed, 0 failed (13 chromium-desktop: signin ×4, invite, platform, clients, documents, keyboard, fragments ×4; 4 chromium-mobile: signin ×4), 15.9 s after the servers were up. axe (wcag2a/2aa/21aa/22aa) at 23 call sites, 28 page states across the two projects: 0 serious or critical findings; no minor or moderate findings were reported |

Re-running the suite against servers that are already up (`reuseExistingServer`) replays the data-changing specs on the same seed: the enrolment test finds its accounts enrolled and the lockout test finds them locked (15 minutes), so those four tests fail by design on a second run. `npm run -w apps/web e2e` always starts fresh servers with a fresh seed.
