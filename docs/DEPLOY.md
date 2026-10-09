# Deploying AgentLedger on Cloudflare: operator runbook

Phase 0 of the Cloudflare deployment (ADR-0001, backlog F-02): the pipeline that builds the API image, deploys it
behind a Worker, proves the deployment with a smoke test, and promotes it from staging to production. This document
is the runbook for whoever operates it. It assumes `wrangler.jsonc`, `Dockerfile`, `edge/`, `scripts/smoke.py`,
`.github/workflows/release.yml` and `.github/workflows/operations.yml` as they are in the repository.

Read the phase gates (last section) before creating any firm on Cloudflare.

## 1. Architecture

```text
browser / API client
   │  https://api.<zone>              (custom domain; no workers.dev hostname)
   ▼
static assets  apps/web/dist          the web app (docs/WEB.md), served before the Worker for every path outside
   │                                   `run_worker_first` (/api/*, /healthz, /legacy, /legacy/*, /static/*,
   │                                   /openapi.json, /internal/*), single-page-application fallback, headers from
   │                                   apps/web/public/_headers (frame-ancestors, nosniff, referrer, immutable /assets)
   ▼
Worker  agentledger-edge-<env>        edge/src/index.ts
   │  picks instance api-<hash(client ip) mod API_INSTANCES>
   │  sets X-Request-Id, X-Forwarded-For, X-Forwarded-Proto: https, X-Forwarded-Host; echoes X-Request-Id, adds HSTS
   │  404 for /internal/*; strips X-AgentLedger-Workflow; runs the outbox relay after a response marked
   │  X-AgentLedger-Outbox and from the cron (every minute)
   ├── Workflow  FILING (FilingSubmission)   edge/src/workflows/submission.ts: one instance per attempt at a submission;
   │                                          each step is one command on POST /internal/commands (ADR-0003, F-08)
   ├── relay                                 edge/src/relay.ts: GET /internal/outbox -> create instances / sendEvent -> mark delivered
   ▼
Durable Object  ApiContainer          one per instance name; starts and owns the container, copies every
   │                                   AGENTLEDGER_*/WORKOS_*/ANTHROPIC_* var and secret into its environment
   ▼
Container  (Dockerfile)               uvicorn agentledger.api.app on :8080, reachable only from its Durable Object
   ├── PostgreSQL on Neon             direct endpoint on 5432 (AGENTLEDGER_RUNTIME_DATABASE_URL), per-store runtime
   │                                   roles derived from AGENTLEDGER_DB_ROLE_KEY. Hyperdrive bindings are not
   │                                   reachable from containers, so the container connects to Neon itself.
   ├── R2                             S3 API (AGENTLEDGER_BLOBS=s3): sealed, content-addressed evidence objects
   └── WorkOS AuthKit                 hosted sign-in for firm users (AGENTLEDGER_IDENTITY=workos)

GitHub Actions
   release.yml      verify + web (web.yml: contract drift gate, lint, types, unit, build, e2e; uploads web-dist)
                    -> staging (migrate, provision, download web-dist, deploy, smoke) -> production (approval, same, 15-minute soak)
   ci.yml           pull requests: the verify checks and the web job; nothing deployed
   operations.yml   every 15 minutes: `agentledger platform provision` for staging and production
```

What never reaches the Worker or the container: owner database credentials (`AGENTLEDGER_MIGRATION_URL`,
`DATABASE_URL`, `DATABASE_URL_UNPOOLED`) and the Neon API key. Migrations and firm provisioning run in GitHub Actions
with those; the API runs with the per-store runtime role only (ADR-0002).

The image is one build for every environment. `BUILD_SHA` in the repository says `dev`; the release writes the
commit SHA into it before `wrangler deploy`, and `GET /healthz` reports it as `build`, which is how the smoke test
knows the rollout has reached the new image. The web app is built in the same release with the same `BUILD_SHA`
(its `<meta name="agentledger-build">` and `__BUILD_SHA__`), and `wrangler deploy` uploads `apps/web/dist` as the
Worker's static assets next to the image; the smoke test checks `GET /` names that build too. Container disk is scratch: `/app/state` and `/app/tenants` exist for
the process and hold nothing of record.

Instance sizes and counts (`wrangler.jsonc`): staging `basic`, one instance, sleeps after 30 minutes idle;
production `standard-1`, up to three instances, sleeps after 4 hours idle, rollouts in steps of 10% then 100% with a
five-minute active grace period. `API_INSTANCES` (a var) is how many named instances the Worker spreads clients over;
`max_instances` is the platform ceiling. Both environments run with `API_INSTANCES=1` until the phase gates below are
passed.

## 2. Settings and secrets inventory

"Read where" is the code that reads the value. "Kind" says whether it is a secret. "Lives in" says where the operator
sets it: a Worker secret (`npx wrangler secret put`), a var in `wrangler.jsonc`, a GitHub environment secret or
variable, or the Neon console. Every value differs between staging and production unless noted.

| Name | Read where | Kind | Lives in | Notes |
|---|---|---|---|---|
| `AGENTLEDGER_MASTER_KEY` | `security/crypto.py` (wraps every firm's data key) | secret | Worker secret | Generate per environment with `agentledger platform new-master-key`. Losing it loses every firm's data. Not needed by the GitHub jobs. Phase 2 moves it to AWS KMS. |
| `AGENTLEDGER_DB_ROLE_KEY` | `pg/__init__.py` (derives each store's runtime password) | secret | Worker secret **and** GitHub environment secret (`staging`, `production`, `*-operations`) | The same value in both places for one environment. |
| `AGENTLEDGER_RUNTIME_DATABASE_URL` | `pg/__init__.py` (host, database and options of runtime connections; its credentials are replaced by the runtime role's) | secret | Worker secret **and** GitHub environment secret | The Neon **direct** (unpooled, port 5432) endpoint of the environment's database. |
| `AGENTLEDGER_MIGRATION_URL` | `pg/__init__.py` (owner credentials for migrations and provisioning) | secret | GitHub environment secret only | **Never a Worker secret.** The Neon owner role's direct URL. |
| `DATABASE_URL`, `DATABASE_URL_UNPOOLED` | `pg/__init__.py` fallbacks for development | secret | nowhere on Cloudflare or GitHub deploy jobs | Development and the test suite only. Never a Worker secret. |
| `NEON_API_KEY`, `NEON_PROJECT_ID` | `pg/provision.py` (a database per firm, production tenancy) | secret | GitHub environment secret only | **Never a Worker secret.** Staging (schema tenancy) does not use them but may hold them. |
| `NEON_BRANCH_ID` | `pg/provision.py` | setting | GitHub environment variable (optional) | The branch that holds firm databases; defaults to the project's default branch. |
| `AGENTLEDGER_BLOB_ACCESS_KEY_ID`, `AGENTLEDGER_BLOB_SECRET_ACCESS_KEY` | `evidence/blobs.py` | secret | Worker secret | An R2 API token scoped to the environment's bucket. |
| `AGENTLEDGER_BLOB_ENDPOINT`, `AGENTLEDGER_BLOB_BUCKET`, `AGENTLEDGER_BLOB_PREFIX`, `AGENTLEDGER_BLOBS=s3` | `evidence/blobs.py` | setting | `wrangler.jsonc` vars | Endpoint `https://<account id>.r2.cloudflarestorage.com`; one bucket per environment; prefix empty. |
| `WORKOS_API_KEY`, `WORKOS_CLIENT_ID` | `security/workos.py` | secret | Worker secret | From the WorkOS environment (staging or production). |
| `WORKOS_REDIRECT_URI` | `api/app.py` | setting | `wrangler.jsonc` vars | `https://<api host>/api/auth/idp/callback`, registered in the same WorkOS environment. |
| `AGENTLEDGER_IDENTITY=workos`, `AGENTLEDGER_DATABASE=postgres`, `AGENTLEDGER_PG_TENANCY`, `AGENTLEDGER_AGENTS=0`, `ENVIRONMENT`, `API_INSTANCES` | `api/app.py`, `db.py`, `pg/provision.py`, `edge/src/index.ts` | setting | `wrangler.jsonc` vars | Tenancy is `schema` on staging and `database` on production; the GitHub jobs default to the same (`vars.AGENTLEDGER_PG_TENANCY` overrides). |
| `AGENTLEDGER_SMOKE_TOKEN` | `api/app.py` (`_smoke_user`) | secret | Worker secret **and** GitHub environment secret as `STAGING_SMOKE_TOKEN` / `PROD_SMOKE_TOKEN` | 32+ characters, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Grants exactly `GET /api/coverage` and `POST /api/returns/individual`. |
| `AGENTLEDGER_WORKFLOW_TOKEN` | `api/internal.py` (the workflow runtime's bearer for `/internal/*`) | secret | Worker secret | 32+ characters, generated like the smoke token. Presented by edge/src/workflows and edge/src/relay.ts through the `API` Durable Object; `/internal/*` is 404 from the internet. Grants the system commands only (never approve, release, reconcile). |
| `ANTHROPIC_API_KEY` | `ai/router.py` | secret | Worker secret (optional) | Frontier tier; absent means local and deterministic tiers only. |
| `AGENTLEDGER_WEBHOOK_SECRET`, `AGENTLEDGER_SMTP_*` | `api/app.py`, `crm` | secret | Worker secret (optional) | Inbound webhooks and outbound mail stay disabled without them. |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | wrangler | secret | GitHub environment secret (`staging`, `production`) | Token template "Edit Cloudflare Workers" plus Zone > Workers Routes > Write on the API's zone (custom domains). May be one token for both environments. |
| `DEPLOY_ENABLED` | `release.yml`, `operations.yml` | setting | GitHub **repository** variable | `true` switches the deploy and operations jobs on. Unset or anything else: they are skipped and main stays green. |
| `STAGING_URL`, `PROD_URL` | `release.yml` | setting | GitHub environment variables | `https://<api host>` of each environment, no trailing slash. |
| `PROD_LAST_GOOD_SHA` | `release.yml` | setting | GitHub repository variable, written by the workflow | The last commit whose production soak passed; the rollback target for the image. |

## 3. Owner checklist (one-time, per environment unless noted)

1. **Cloudflare account on the Workers Paid plan** (Containers and Durable Objects need it). Note the account id.
2. **DNS zone** for the API hostnames on this account, and the two hostnames chosen: replace
   `staging-api.agentledger.example` and `api.agentledger.example` in `wrangler.jsonc` (`routes`, `WORKOS_REDIRECT_URI`)
   and in the GitHub variables `STAGING_URL`/`PROD_URL`. `custom_domain: true` creates the DNS records on deploy.
3. **R2**: enable R2, create one bucket per environment (`agentledger-staging-evidence`,
   `agentledger-production-evidence`, or rename them in `wrangler.jsonc`), and one R2 API token per environment scoped
   to its bucket (Object Read & Write). Put the account's endpoint in `AGENTLEDGER_BLOB_ENDPOINT`.
4. **Neon**: a project for production (the existing project's branch serves staging), with per environment: the owner
   role's direct URL (`AGENTLEDGER_MIGRATION_URL`), the direct URL the runtime connects to
   (`AGENTLEDGER_RUNTIME_DATABASE_URL`, same host and database), a Neon API key and the project id (production, database
   tenancy), and a fresh `AGENTLEDGER_DB_ROLE_KEY` (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).
5. **WorkOS**: a staging and a production environment with MFA set to Required, the callback
   `https://<api host>/api/auth/idp/callback` registered, and their API key and client id.
6. **Master keys**: `agentledger platform new-master-key` once per environment; store each in the team's secrets
   manager before setting it as a Worker secret. Phase 2 replaces this with AWS KMS.
7. **Smoke tokens**: one random 32+ character token per environment.
8. **Cloudflare API token** for GitHub: template "Edit Cloudflare Workers", plus Zone > Workers Routes > Write for the
   API's zone. See the open flags about Containers permissions.
9. **GitHub environments**: `staging`, `production` (with required reviewers: this is the production approval gate),
   `staging-operations` and `production-operations` (no reviewers; see section 5).

## 4. Worker secrets

Run from a machine with `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID` in the environment (or `npx wrangler login`),
once per environment, before the first deploy. On a Worker that does not exist yet wrangler offers to create a draft
Worker to hold the secrets; accept. `wrangler.jsonc` lists these under `secrets.required`, so a deploy fails with the
names of any that are missing.

```sh
for ENV in staging production; do
  for NAME in AGENTLEDGER_MASTER_KEY AGENTLEDGER_DB_ROLE_KEY AGENTLEDGER_RUNTIME_DATABASE_URL \
              AGENTLEDGER_BLOB_ACCESS_KEY_ID AGENTLEDGER_BLOB_SECRET_ACCESS_KEY \
              WORKOS_API_KEY WORKOS_CLIENT_ID AGENTLEDGER_SMOKE_TOKEN AGENTLEDGER_WORKFLOW_TOKEN; do
    npx wrangler secret put "$NAME" --env "$ENV"        # prompts for the value; nothing is echoed
  done
done
# optional, per environment:
npx wrangler secret put ANTHROPIC_API_KEY --env staging
npx wrangler secret put AGENTLEDGER_WEBHOOK_SECRET --env staging
```

`npx wrangler secret list --env staging` shows the names. A changed secret takes effect on the next container start
(`sleepAfter` idle, a rollout, or `npx wrangler deploy --env <env>`); the Durable Object copies the environment into
the container when it starts it.

## 5. GitHub environments, secrets and variables

Repository variable: `DEPLOY_ENABLED=true` (only after everything above and below exists).

| Environment | Secrets | Variables | Protection |
|---|---|---|---|
| `staging` | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `AGENTLEDGER_MIGRATION_URL`, `AGENTLEDGER_RUNTIME_DATABASE_URL`, `AGENTLEDGER_DB_ROLE_KEY`, `NEON_API_KEY`, `NEON_PROJECT_ID`, `STAGING_SMOKE_TOKEN` | `STAGING_URL`; optional `AGENTLEDGER_PG_TENANCY` (default `schema`), `NEON_BRANCH_ID` | none; `main` only |
| `production` | the same names with production values, and `PROD_SMOKE_TOKEN` | `PROD_URL`; optional `AGENTLEDGER_PG_TENANCY` (default `database`), `NEON_BRANCH_ID` | **required reviewers**; `main` only |
| `staging-operations` | `AGENTLEDGER_MIGRATION_URL`, `AGENTLEDGER_RUNTIME_DATABASE_URL`, `AGENTLEDGER_DB_ROLE_KEY`, `NEON_API_KEY`, `NEON_PROJECT_ID` (staging values) | optional `AGENTLEDGER_PG_TENANCY`, `NEON_BRANCH_ID` | none |
| `production-operations` | the same names with production values | the same | none |

Why the `*-operations` environments: a required-reviewer rule applies to every job that targets the environment, and
`operations.yml` runs every 15 minutes. It holds only database and Neon secrets, never the Cloudflare token.

`PROD_LAST_GOOD_SHA` is written by the production job with `gh variable set` through `GITHUB_TOKEN`
(`permissions: actions: write`). If the organization forbids that, create the variable by hand once and give the job a
fine-grained token with Variables: write as `GH_TOKEN`.

## 6. Deploy procedure

Every merge to `main` runs `release.yml`:

1. `verify` and `minimum-versions`: lint, types, the whole suite on SQLite and PostgreSQL, golden scenarios.
2. `staging` (only with `DEPLOY_ENABLED=true` and `wrangler.jsonc` present): `npm ci`, `pip install . httpx`,
   write `$GITHUB_SHA` to `BUILD_SHA`, `agentledger platform migrate` (owner credentials; forward only),
   `agentledger platform provision`, `npx wrangler deploy --env staging` (builds and pushes the image, activates the
   Worker, starts the rollout), then `python scripts/smoke.py "$STAGING_URL"` with `EXPECTED_BUILD=$GITHUB_SHA`.
3. `production` (after the staging smoke passes and a reviewer approves): the same steps with production secrets,
   `npx wrangler deploy --env production`, then a 15-minute soak running the smoke test every 5 minutes. On success it
   records `PROD_LAST_GOOD_SHA`; on failure it prints the rollback instructions and fails.

`wrangler deploy` is used deliberately. `wrangler versions upload` / `versions deploy` do not roll out container
images; gradual rollout of the image is configured in `wrangler.jsonc` (`rollout_step_percentage`,
`rollout_active_grace_period`) and performed by the platform after the deploy returns. The smoke test's health poll
waits (up to `SMOKE_WAIT_SECONDS`, default 600) for `/healthz` to report the new build three polls in a row.

**First deploy of an environment**: set the Worker secrets (section 4), then run the pipeline, or by hand from a
checkout of `main`:

```sh
npm ci && pip install . httpx
echo "$(git rev-parse HEAD)" > BUILD_SHA
AGENTLEDGER_DATABASE=postgres AGENTLEDGER_PG_TENANCY=schema AGENTLEDGER_MIGRATION_URL=... \
  AGENTLEDGER_RUNTIME_DATABASE_URL=... AGENTLEDGER_DB_ROLE_KEY=... agentledger platform migrate
npx wrangler deploy --env staging
EXPECTED_BUILD=$(git rev-parse HEAD) SMOKE_TOKEN=... python scripts/smoke.py https://staging-api.<zone>
```

**The smoke test** (`scripts/smoke.py <base url>`): `/healthz` reports the expected build (3 consecutive polls);
`GET /api/auth/config` is 200 and `GET /api/users` is 404 (no demo identity picker); `POST /api/auth/login` with a
random account is 401 with a generic message; with identity `workos`, `GET /api/auth/idp/start` returns a
`https://api.workos.com/` URL; `GET /api/coverage` lists `f1040`; `POST /api/returns/individual` computes the sample
return with Form 1040 line 16 = 5023. It creates, changes and deletes nothing. Against a local server:

```sh
AGENTLEDGER_SMOKE_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))") agentledger serve --dev
SMOKE_TOKEN=<the same value> SMOKE_DEV=1 SMOKE_WAIT_SECONDS=30 python scripts/smoke.py http://127.0.0.1:8740
```

**Firms created between releases** stay in `provisioning` (their invitations do not work) until
`operations.yml` runs `agentledger platform provision`, every 15 minutes or on demand from the Actions tab.

**Building the image locally**:

```sh
docker build -t agentledger-api:local .                       # add --build-arg WITH_ORACLE=1 for policyengine-us
docker run --rm agentledger-api:local sh -c 'ls -a /app; id -u'   # the runtime directories only; 1000
docker run --rm -e AGENTLEDGER_DEV_AUTH=1 -e AGENTLEDGER_AGENTS=0 -p 18080:8080 agentledger-api:local
curl http://localhost:18080/healthz                            # {"ok":true,"build":"dev","backend":"sqlite"}
```

## 7. Rollback

- **Worker code** (`edge/src/index.ts`, bindings, vars): `npx wrangler rollback --env production` and pick the previous
  deployment. This does not change the container image.
- **API image**: redeploy the last good commit. From a checkout of `PROD_LAST_GOOD_SHA`:
  `echo <sha> > BUILD_SHA && npx wrangler deploy --env production`, or push a branch at that commit and run
  `release.yml` for it (`gh workflow run release.yml --ref <branch>`; staging is redeployed first, which is fine).
  Immediate instead of gradual: add `--containers-rollout=immediate`.
- **Migrations roll forward only.** A release whose migration must be undone gets a new migration that reverses it,
  after the image is rolled back to a build that runs with the current schema. `agentledger platform migrate` refuses
  an edited, already-applied migration (ADR-0002), so never rewrite one.
- **Secrets**: `npx wrangler secret put NAME --env production` with the previous value, then a deploy (or wait for the
  next container start).

## 8. Acceptance criteria for staging

The staging environment is accepted when all of these hold on a release from `main`:

- `release.yml`'s `staging` job is green end to end: migrate, provision, deploy, smoke, with no manual step.
- `GET https://<staging host>/healthz` returns `{"ok": true, "build": "<the commit SHA>", "backend": "postgres"}`.
- `scripts/smoke.py` passes all six checks, including the WorkOS sign-in URL (identity `workos`).
- `GET /api/users` is 404 and `POST /api/auth/login` with an unknown account is 401 with the generic message.
- The smoke token is refused (403 or 404) on every route but `GET /api/coverage` and `POST /api/returns/individual`
  (`tests/test_health_and_smoke.py` sweeps the whole API).
- A platform administrator can sign in (break-glass password + TOTP), create a firm, and after the next
  `operations.yml` run the firm is `active`; the firm administrator accepts the invitation through WorkOS, uploads a
  document and sees it sealed in the staging bucket (`GET /api/evidence/integrity?verify=true` is clean).
- A second deploy of a trivial change reaches `build == new SHA` within `SMOKE_WAIT_SECONDS` and the cold-start time
  (first request after `sleepAfter`) is measured and recorded here.
- Logs: Workers observability shows one line per request with method, path, status, duration and request id; the
  container log shows uvicorn's access lines with the client's real address (not the Durable Object's).

## 9. Phase gates

**Phase 1 (platform store on PostgreSQL) must be merged before any firm is created on Cloudflare.** Until then the
platform store (firms, users, sessions, invitations, wrapped keys) is SQLite inside the container, on scratch disk:
every container restart or rollout loses it, GitHub's `provision` job sees a different store than the API, and two
instances would see two different sets of users. With Phase 1 merged, `agentledger platform migrate` and
`agentledger platform provision` run against the same PostgreSQL platform store the API uses, and `API_INSTANCES` can
be raised.

**Phase 2** (before real taxpayer data in production):

- Master key in **AWS KMS**: one key per environment, unwrapped at container start by a decrypt-only IAM user whose
  credentials are the Worker secret; the raw master key leaves Cloudflare's secret store. CloudTrail is the evidence.
- **Logpush** for Workers trace events and container logs to the retention store the security plan names.
- **Digest-pinned images**: deploy by digest from the Cloudflare registry (an image reference in `wrangler.jsonc`)
  instead of rebuilding from `./Dockerfile` on every deploy, so a rollback redeploys bytes that already ran.
- **`uv.lock`** committed and installed with `uv pip sync` in the Dockerfile, so an image build is reproducible and
  dependabot's pins mean something; today the dependency layer resolves `pyproject.toml` ranges at build time.
- R2 bucket lock rules per retention prefix (F-13) and the cold-start and p95 measurements ADR-0001 asks for.

## 10. Open flags (unverified until the first real deploy)

- `exports` and `containers` repeated inside `env.staging` / `env.production`: the Cloudflare documentation shows
  `exports` as overridable per environment and `durable_objects`/`vars` as non-inheritable; whether `containers`
  inherits is not documented, so it is repeated. The dry run below accepted the shape without a warning; only the
  first real deploy confirms the API accepts it.
- The API token permission for Containers is unconfirmed: the Workers documentation names "Workers Editor" for
  `wrangler deploy` and Zone > Workers Routes > Write for custom domains; if the first deploy fails on the image push
  or the container application, add the Containers (Cloudchamber) permission to the token.
- Cold-start latency (image pull plus uvicorn boot behind `startAndWaitForPorts`, 120-second ceiling) is unmeasured;
  the first staging deploy should record it in section 8.
- `secrets.required` makes a deploy fail until every secret exists; the first `wrangler secret put` on a new
  environment creates a draft Worker (interactive prompt), which is why section 4 runs from an operator's machine.
- `gh variable set` through `GITHUB_TOKEN` needs `actions: write`; some organizations restrict it (section 5).
- The `workflows` binding (`FILING`) and `triggers.crons` are repeated per environment like the other non-inheritable
  bindings; the dry run lists the binding. Whether Workflows is enabled on the account's plan, the instance retention
  (7 days requested per instance) and the per-step timeout behaviour against a cold container are first exercised by
  the real deploy. Until `AGENTLEDGER_WORKFLOW_TOKEN` is set in the container, every call from the Workflow or the relay
  is refused (403) and nothing is filed by the platform; a CPA can still transmit directly.

### Dry run record (2026-10-09, wrangler 4.149.0, no credentials)

`npx wrangler deploy --dry-run --outdir <tmp> --env staging` and `--env production` both exit 0 with no warnings.
Wrangler builds the image from `./Dockerfile` locally even in a dry run (so `wrangler deploy` needs Docker on the
runner; GitHub's `ubuntu-latest` has it), bundles the Worker (55 KiB), and lists the `API` Durable Object binding,
the eleven vars, the `FILING` Workflow binding (2026-10-09, F-08) and the container application
`agentledger-edge-<env>-apicontainer-<env>` for each environment. It
makes no API call, so `secrets.required`, the custom-domain routes and the account's plan are first exercised by the
real deploy. Also checked offline: `npx tsc --noEmit -p edge` is clean; the image is 434 MB, holds only
`BUILD_SHA config coverage domains evals golden playbooks rules src state tenants` and runs as uid 1000;
`scripts/smoke.py` passes all six checks against that image started with `AGENTLEDGER_DEV_AUTH=1` (hosted sign-in
skipped, identity `local`); `tests/test_health_and_smoke.py` passes on SQLite and PostgreSQL. Update this record after
the first real deploy with the cold-start time and anything the live API rejected.

2026-10-09, after the `assets` block (F-10 follow-up): `--dry-run --env staging` exits 0, reads the 39 files of
`apps/web/dist` (65 KiB uploaded, gzip 17 KiB) and lists `env.ASSETS (Assets)` next to the Durable Object and the
Workflow; the block is repeated per environment, so whether `assets` inherits into env blocks is not relied on. The
smoke check now has seven steps (`web app` is new); against a `--dev` API without the assets in front it passes only
with `SMOKE_DEV=1`. Whether `run_worker_first` and the `_headers` rules behave as documented is first seen on the real
deploy: `GET /` must be the app, `GET /legacy` the previous interface, `GET /api/auth/config` the API.
