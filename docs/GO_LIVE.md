# AgentLedger go-live plan: TY2026 filing season

Target: production for the TY2026 filing season (IRS MeF production opens in late January 2027).
Scope: everything in `BL.md`, delivered as multi-firm SaaS on a cloud VPS, with interoperability
across all major tax and bookkeeping software.

## Part A: steps only the business can take (calendar-gated)

The IRS controls these steps, so they set the go-live date. Start them now.

| # | Step | Who | Lead time | Unblocks |
|---|---|---|---|---|
| A1 | IRS e-Services accounts (ID.me) for the Responsible Official and a Principal | Owner | days | A2 |
| A2 | IRS e-file application with provider options **Software Developer, Transmitter, ERO**, plus **Online Provider** if taxpayers will self-file | Owner | up to 45 days (suitability check; fingerprints unless credentialed CPA/EA/attorney) | EFIN, ETIN, A3–A5 |
| A3 | Access to MeF schemas and business rules through the e-Services Secure Object Repository (they are not published openly) | Software Developer role | after ETIN | M5 XSD validation |
| A4 | Register the A2A application system (ASID) and obtain an X.509 certificate from an IRS-accepted certificate authority | Transmitter role | 1–3 weeks | M5 live transmission |
| A5 | ATS testing: Pub 1436 test scenarios for Form 1040 (business forms: Pub 4163/4164), and a communications test | Engineering + ETIN | ATS usually opens in November | production approval |
| A6 | Pub 1345 Online Provider security standards (EV TLS certificate, weekly external vulnerability scan, published privacy policy, safeguards policy, incident reporting within 24 h) | Owner + Engineering | 2–4 weeks | Online Provider status |
| A7 | KBA vendor contract for Form 8879 remote signatures (Pub 1345 identity-verification requirements) | Owner | 2–6 weeks | M5 e-signature |
| A8 | Written Information Security Plan (FTC Safeguards Rule 16 CFR 314; IRS Pub 5708), cyber and E&O insurance | Owner | 2 weeks | onboarding firms |
| A9 | State e-file approvals (Fed/State MeF), state by state | Owner | per state | M11 |
| A10 | QuickBooks Online and Xero developer app registration (OAuth client IDs) | Owner | days | M10 live connectors |

## Part B: blueprint traceability

Status key: **done** (built and tested) · **partial** · **todo**.

| BL.md section | Item | Status | Milestone |
|---|---|---|---|
| §1 | Sentinel supervision of postings, transfers and transmissions | done (foundry/verify) | — |
| §1 | Durable, append-only task state; durable pauses (e.g. waiting for 8879) and deterministic resume | todo | M3 |
| §2 | Dual-attribute tagging at ingestion | done | — |
| §2 | Schedule M-1 live | done | — |
| §2 | Schedule M-3; permanent and temporary classification | todo | M6 |
| §2 | Fixed-asset register (book SL vs MACRS, M-1 line 5a/8a) | partial (calculators only) | M6 |
| §2 | Deterministic engines (policyengine-us, tenforty) | done: policyengine-us cross-checks every return | M1 |
| §3 | AcroForm population, flattening, SHA-256 tamper evidence | todo | M4 |
| §3 | MeF XML serialization plus XSD validation (lxml) | todo | M5 (needs A3) |
| §3 | A2A SOAP MTOM/XOP, WS-Security signing, EFIN/ETIN/TCC, ACK/NACK ingestion | todo | M5 (needs A4) |
| §3 | Form 8879 e-signature with KBA (3 attempts, audit manifest) | todo | M5 (needs A7) |
| §4 | Healthcare: X12 835 parsing, compound entries, PHI tokenization (air-gapped) | partial (classifier only) | M7 |
| §4 | Auto repair: three-tier job costing, core-deposit escrow | partial (pack only) | M7 |
| §4 | Gas station: ATG volumetric shrink, excise decomposition, Form 720, lottery clearing | partial (pack only) | M7 |
| §4 | Insurance: commission-statement reconciliation, premium trust accounting, trust-deficit monitor | partial (pack only) | M7 |
| §5 | BOIR (FinCEN 2025 interim final rule: foreign reporting companies only) | partial (rule + calc) | M8 |
| §5 | SOS lifecycle: name check, Articles, operating agreement, SS-4, annual reports, franchise tax | todo | M8 |
| §5 | NASBA CPE: credit math, multi-state renewal rules, certificate OCR | todo | M8 |
| §6 | §7216 redaction (regex + local NER) and consent capture | partial (Ask redaction) | M9 |
| §6 | FIDO2/WebAuthn MFA; AES-256 at rest with customer-managed keys; TLS 1.3; immutable audit log | partial: TOTP MFA, AES-256-GCM per-firm keys, append-only auth log done; WebAuthn and TLS todo | M2, M12 |
| §7 | Beancount + PostgreSQL | partial (SQLite + Beancount export) | M2 |
| §7 | Tesseract/PaddleOCR + local vision models | partial | M7 |
| §7 | Tiered models (local, deterministic, frontier) | done | — |
| SaaS | Multi-firm tenancy, onboarding, billing | partial: database-per-firm isolation, invitations, platform admin done; billing todo | M2 |
| Interop | Trial-balance export to Drake, UltraTax, Lacerte/ProConnect, CCH; QBO/Xero import | partial | M10 |
| Returns | 1040 + schedules TY2026 (OBBBA) | partial: core forms done; 1116, 8615, 8880, 8962, 8606, 8889, 4797, Sch F, 2210 todo | M1 |
| Returns | 1120-S, 1065, 1120 with K-1s | todo | M6 |
| Returns | State income tax returns | todo | M11 |

## Part C: engineering plan

The plan now follows the production specification's waves (F, A1, A2, T1, T2, V, S1, S2). The
dependency-ordered tickets are in [inception/backlog.md](inception/backlog.md). The architecture decisions
are in [adr/](adr/). Acceptance status by Q-test is in [inception/acceptance-map.md](inception/acceptance-map.md),
and supported forms are in [coverage/coverage.yaml](../coverage/coverage.yaml) (also `GET /api/coverage`).

Progress log:
- 2026-10-08: M1 core done. Form 1040 and Schedules 1, 1-A, 2, 3, 3-A, A, B, C, D, E, SE, 8812, 8995/8995-A,
  6251, 8959, 8960, 2441, 8863 follow the 2026 draft forms. 33 cited rules. 26 hand-verified tests,
  5 golden whole-return scenarios, 11 PolicyEngine cross-checks that all agree. The cross-check
  found two gaps that are now fixed: the Treasury tipped-occupation requirement and the SSTB tips
  exclusion.
- 2026-10-08: M2 core done. Each firm gets its own database, vault and AES-256-GCM data key wrapped by a master
  key. Offboarding a firm destroys its data key (documents and sealed returns become unreadable) and, since
  2026-10-08, also removes its operational store and files; until then the books stayed readable after
  offboarding. Sign-in is Argon2id plus TOTP, with enrolment at invitation,
  lockout, idle and absolute session expiry and an append-only sign-in log. Downloads use signed links.
  Regulation decisions belong to platform reviewers. Demo identities exist only with `--dev`.
  Remaining for M2: WebAuthn passkeys, SQLCipher or an encrypted volume for the databases, billing.

Each milestone ends with: full test suite green, golden scenarios green, a commit.


## Part D: honest constraints

- No return is transmitted until the IRS has accepted the ATS results (A5). The engine, forms and XML
  can be finished first.
- Durable execution: Cloudflare Workflows orchestrates, while domain state machines stay in PostgreSQL (ADR-0003).
- FinCEN's March 2025 interim final rule removed BOI reporting for domestic companies. BOIR is built for
  foreign reporting companies and remains driven by rules, so it switches back on if the law changes.
- BL.md cites Rev. Proc. 2008-35 for §7216 consents. The current consent rules are in Treas. Reg.
  §301.7216-3 and Rev. Proc. 2013-14. AgentLedger follows the current authority.

## Deploying (2026-10-09)

The Cloudflare deployment (API container behind a Worker, Neon, R2, WorkOS), its secrets inventory, the owner's
checklist of accounts and credentials, the release and rollback procedures and the staging acceptance criteria are in
[docs/DEPLOY.md](DEPLOY.md). The pipeline exists in `.github/workflows/release.yml` and stays skipped until the
repository variable `DEPLOY_ENABLED` is `true` and the secrets it names exist. No firm may be created on Cloudflare
before the platform store runs on PostgreSQL there (`agentledger platform migrate` as the release step; shipped).

## Operator settings for identity and provisioning (2026-10-08)

- WorkOS environment: MFA set to **Required**; the callback `https://<app>/api/auth/idp/callback` registered and set as
  `WORKOS_REDIRECT_URI`; `AGENTLEDGER_IDENTITY=workos` in the API environment.
- SSO customers: record their identity provider's MFA enforcement (`POST /api/platform/sso-mfa`) after reviewing the
  evidence, and review it periodically; until then their SSO sign-ins are refused for lack of MFA.
- The API environment holds no owner database credentials. Run `agentledger platform provision` with
  `AGENTLEDGER_MIGRATION_URL` as a release or scheduled operations job; firms stay in `provisioning` until it runs.
- The platform store (firms, users, sessions, wrapped data keys, provisioning journal) is PostgreSQL in production: the
  schema `platform` of the runtime database, with its own login role `rt_<database>_platform` whose password derives from
  `AGENTLEDGER_DB_ROLE_KEY` and whose privileges stop at that schema (no DDL, no UPDATE or DELETE of the authentication
  log or offboarding records). Run `agentledger platform migrate` with `AGENTLEDGER_MIGRATION_URL` as a release step
  before the API starts (it needs no master key; it creates the schema and role, and applies new platform migrations);
  an API started against a schema that was never migrated stops with a message naming that command. The API, the
  worker and operations jobs read and write the platform tables as the runtime role only; `platform migrate` and
  `platform provision` run with owner credentials and without `AGENTLEDGER_MASTER_KEY` (the key stays in the API
  container). Each container holds one connection to the direct endpoint per request thread, so bound uvicorn's
  thread pool with the endpoint's connection limit in mind. Set `AGENTLEDGER_PLATFORM_DATABASE` only to put the
  platform store on another backend than the firm stores (tests do).
- Evidence storage: enable R2 on the Cloudflare account (dashboard), create the bucket and an R2 API token scoped to
  it, and set `AGENTLEDGER_BLOBS=s3` with the `AGENTLEDGER_BLOB_*` values. Objects are sealed and content addressed
  before upload. The bucket lock rule on `anchors/` (F-13) is the owner's step in docs/DEPLOY.md, checklist 3b; the
  per-retention-class prefixes for held documents remain F-06's.

## Operator procedures for audit chain anchors and restore drills (F-13, 2026-10-09)

- **What an anchor is.** Each firm's audit trail is a hash chain in its own store. Hourly, the `evidence-anchor` agent
  (the API scheduler; or `agentledger evidence anchor --all` as a job, `--firm <id>` for one firm) writes the chain's
  head to the firm's object store as `anchors/<firm>/<utc date>/<seq>.json`, signed with a key derived from the firm's
  data key, only when the chain moved. Under the bucket lock rule those objects can be neither deleted nor overwritten,
  so a chain that was rewritten or cut short afterwards is caught by comparing it with its anchors
  (ADR-0006, amendment 2026-10-09).
- **The lock is probed, never assumed.** Every run writes a probe object under `anchors/<firm>/probe/` and tries to
  delete it; the anchors count as locked only when the delete is refused. Until the owner has created the rule
  (DEPLOY.md 3b) every run records `evidence.anchor_lock_missing` once and every report shows
  `anchors.lock.status == "missing"`; after the rule exists the next run records `evidence.anchor_lock_confirmed`. A
  report that says `missing` on production is a finding for the owner, not a warning to dismiss. Locally and in CI
  (file blobs, the SeaweedFS server) the gap is reported the same way (`unchecked` / `missing`).
- **Reading the report.** `GET /api/evidence/integrity` (a reviewer, firm-wide) now carries `anchors`: `count`,
  `latest`, `signed`, `signature_failures`, `chain_rewritten`, `chain_truncated`, `workflow_rewritten`,
  `workflow_truncated`, `anchors_missing_from_store`, `anchors_replaced`, `chain` (the chain's own verification),
  `anchor_missing` with `unanchored_since` (unanchored activity older than `AGENTLEDGER_ANCHOR_MAX_AGE_HOURS`, default
  24: the job did not run), and `lock`. Any `chain_*`, `workflow_*`, signature or missing-anchor entry makes the
  report's `ok` false and means the store was changed below the application: stop, preserve the store and the bucket,
  and compare the anchors (the object store is the authority) with the chain before anyone touches either.
  `agentledger evidence anchor` exits 1 on such a mismatch and never writes a new anchor over it.
- **Restore drill** (Q33; run it before go-live and after every change to the database or backup setup, at a quiet
  moment for the firm): `agentledger evidence restore-drill <firm>` with the owner connection
  (`AGENTLEDGER_MIGRATION_URL`) and the master key in the environment. It copies the firm's store (SQLite: the file;
  PostgreSQL: a fresh schema `drill_<token>` in the firm's database, migrated and loaded from the firm's schema),
  verifies the copy's chain against the anchors, lists every parked workflow instance found in the copy and whether it
  would resume, records `evidence.restore_drill` in the firm's audit trail and writes the report to
  `tenants/<firm>/drills/<stamp>-<token>.json`; the copy is removed unless `--keep` (then drop the schema and its
  runtime role by hand when done). Exit 1 means the copy did not verify: treat the backup path as broken until it does.
  On Cloudflare the tenant directory is scratch, so read the report from the command's output.

## Operator procedures for evidence retention and offboarding (re-audit of 952ee96)

- Retention is a CPA's decision, never automatic. For each document a CPA confirms its tax year (or that it has none)
  and class (`POST /api/documents/{id}/retention`); for returns filed outside AgentLedger the CPA records the filing
  with its evidence (`POST /api/clients/{id}/tax-year-events`: the form, the quarter for each 941, the 940, owners'
  returns of a pass-through entity as `owners_filed`, payments, "not required" years). `GET /api/documents/{id}/retention`
  shows the computed date or why there is none.
- A retention run: review `GET /api/evidence/due`, then `POST /api/evidence/purge` with the reviewed `document_ids`,
  a reason and `attested: true` (the attestation in `evidence/records.py: ATTESTATION`: no examination, fraud,
  extension agreement, unfiled foreign information return, listed transaction, pending claim, carryover, basis or
  client request applies; where one does, place a legal hold instead). Check `GET /api/evidence/integrity` afterwards
  (`?verify=true` reads every stored object back and authenticates it).
- Basis records (closing statements, K-1s, brokerage statements, invoices behind capitalized costs) are kept until a CPA
  records the property's disposition (`POST /api/documents/{id}/basis-release`); they are then kept as records of the
  disposition year. A return that will not be filed through AgentLedger (abandoned, or filed with other software) is
  voided (`POST /api/returns/{id}/void`), and a filing made elsewhere is recorded as a tax-year event.
- Firm offboarding runs as an operations job with owner database credentials (`AGENTLEDGER_MIGRATION_URL`), after the
  firm's export is delivered: `delete_firm(reason)` takes the firm out of use, seals its store against new holds and
  destroys it in recorded stages; it is refused while a hold is active. `cancel_offboarding` stops it before the store's
  removal begins (during a run it is a request the run honours at its next stage); the job must run with the object
  store and database server the firm was created with. A firm created before its object store was recorded is
  refused at that stage until an operator checks the deployment's settings and records it with
  `record_blob_location(firm, "s3:<bucket>/<prefix>" or "file", reason)`; a recorded location never changes.
- The R2 credentials are checked live with `AGENTLEDGER_TEST_R2=1 pytest tests/test_live_r2.py` (one sealed object under
  a throwaway `selftest/` prefix, removed afterwards).
