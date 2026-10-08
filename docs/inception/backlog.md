# Implementation backlog (dependency-ordered)

Spec §20 waves, sliced per the §22 rule. Each ticket is one complete user workflow, or one foundation
capability with its tests, never "API now, screen later". Every ticket lists component ids (spec §5),
acceptance tests and dependencies. Done tickets are marked ✅.

Owner decisions recorded 2026-10-08:
- **Platform:** Cloudflare (ADR-0001).
- **Pilot cohort:** small CPA firms, bookkeeping/CFO firms, small businesses and individual filers.
- **First tax coverage:** federal 1040 core, 1120-S/1065 with K-1s, first states (to be named), 1099
  information returns.
- **AI:** hybrid with consent (ADR-0007).

## Wave F: inception and financial foundation

| Id | Ticket | Components | Proves | Depends on |
|---|---|---|---|---|
| F-01 ✅ | Inception package: ADRs, domain model, threat model, coverage registry, provider register, acceptance map, backlog | C03, C28 | — | — |
| F-02 | **Cloudflare spike.** Container cold start and p95, Workflows limits against the return/close flows, Hyperdrive transaction semantics, R2 bucket locks per prefix. Written results go back into ADR-0001 and ADR-0003. | C34 | — | Cloudflare account |
| F-03 | Repository restructure toward the spec layout (`apps/api`, `apps/web`, `workers/*`, `domains/*`, `packages/contracts`) without behavior change; CI runs lint, types and tests on PostgreSQL | C34 | all existing tests | — |
| F-04 🟡 | **PostgreSQL financial core.** *Done:* `src/agentledger/pg` (checksummed SQL migrations, `post_journal`/`reverse_journal`, deferred balance trigger, immutability and closed-period triggers, command receipts, outbox, audit chain, app role without table writes, RLS by session scope), proven on Neon dev and in CI on postgres:17. *Remaining:* port the ledger, CRM, returns and workflow modules off SQLite; database-per-firm provisioning through the Neon API. Originally: Alembic migrations; `post_journal()` with a deferred balance trigger; command ids with receipts; outbox; immutability grants; database per firm with an RLS skeleton | C07, C34 | Q01–Q05 | F-03 |
| F-05 | **Identity through WorkOS AuthKit** (staging environment): login, MFA and passkeys at the IdP; memberships and engagement grants in the domain; step-up for consequential actions; current TOTP stack kept as the self-host profile | C01 | Q12, Q13 | F-04 |
| F-06 | **Evidence vault on R2** (MinIO locally): client-side encryption, content addressing, document versions, retention classes, legal holds, deletion receipts | C04 | Q37 | F-04 |
| F-07 | **Facts never overwrite.** FactAssertion with supersession; conflict exceptions on re-population; a missing value is never zero | C04, C16 | Q16 | F-06 |
| F-08 | **Workflows adapter.** Domain commands as idempotent steps; a local runner for tests; `unknown`-outcome handling; return filing state machine with release approval and separate federal/state submissions | C26, C18 | Q03, Q23, Q24 | F-04, F-02 |
| F-09 | **Inference gateway.** Data-class policy, consent checks, AI Gateway routing, per-tenant and per-role budgets; Model Scout benchmarks hosted routes for cost per correct result | C26, C28 | Q34 | F-05 |
| F-10 | **React app shell** (Vite + TS): workspaces, context bar (firm/entity/engagement/period/basis), all required states, generated API client, Playwright plus axe in CI | C24 | Q38 (shell) | F-05 |
| F-11 | **First complete workflow (spec §22):** entity setup → source upload → extraction review (source beside fields) → journal proposal → approved posting → statement reconciliation → close snapshot → dashboard → exported report | C02, C05, C07, C08, C15, C25 | Q07, Q09, Q16, Q17 | F-04…F-10 |
| F-12 | Rule-change governance in production: statutory rules need tax-content owner sign-off; auto-adopt only in dev | C20 | — | F-05 |
| F-13 | Audit chain anchoring to R2 with a bucket lock; restore drill with active workflows | C28, C34 | Q33 | F-06, F-08 |

## Wave A1: accounting and practice operations

| Id | Ticket | Proves |
|---|---|---|
| A1-01 | Bank and card feeds through an aggregator; statement imports; completeness warnings; matching workspace | Q08, Q09 |
| A1-02 | AR end to end: invoices, deposits, partial payments, refunds, aging, collections (Stripe acceptance) | Q07 |
| A1-03 | AP end to end: bills, approvals, duplicate-invoice controls, payment batches, verified bank-detail changes | Q14, Q25 |
| A1-04 | Processor settlements, gross to net (Stripe, Square, Shopify) | Q10 |
| A1-05 | Close workspace: checklist, control-account ties, accruals, review notes, lock and reopen, frozen snapshot | Q04, Q17 |
| A1-06 | Practice: engagements, recurring jobs, requests portal, deadlines with holidays and time zones, staffing | Q40 |
| A1-07 | Practice economics: time, WIP, billing, retainers | — |
| A1-08 | Migration: QuickBooks and Xero imports, mappings, tie-outs, cutover watermark | Q32 |
| A1-09 | Client portal: uploads, questions, approvals, status, delegated access | Q12, Q38 |
| A1-10 | Offboarding: grant revocation, export bundle, retention and deletion | Q13, Q37 |

## Wave T1: unified tax preparation (owner priority)

| Id | Ticket | Proves |
|---|---|---|
| T1-01 | 1040 gaps to `preparation-validated`: 1116, 8615, 8880, 8962, 8606, 8889, 4797, 8582, 2210, capital loss carryover, 1040-X | Q18, Q19 |
| T1-02 | Independent validation set: returns prepared by an outside preparer, compared line by line (not self-derived) | Q18 |
| T1-03 | Tax organizer with prior-year roll-forward; book-to-tax bridge from approved close | — |
| T1-04 | **1120-S and 1065 packages with K-1s**; K-1 flow into owners' 1040 cases through authorized delivery | Q20 |
| T1-05 | **First states** (owner to name them): resident, nonresident, apportionment as applicable | Q19, Q24 |
| T1-06 | **1099 information returns:** vendor 1099 readiness → NEC/MISC preparation → IRIS filing adapter | — |
| T1-07 | Form rendering: official PDFs from a per-year field map, attachments, page checks against the calculation snapshot | Q21 |
| T1-08 | Export handoff: validated trial-balance and workpaper exports per target software, each verified against a licensed copy | — |
| T1-09 | Recalculation candidates on rule changes; amended-return cases | Q39, Q20 |

## Wave T2: supported production filings

| Id | Ticket | Proves |
|---|---|---|
| T2-01 | MeF XML builder from the per-year element map; XSD validation (schemas from e-Services SOR) | — |
| T2-02 | A2A transmission adapter (SOAP MTOM, WS-Security), acknowledgment polling, `unknown`-state recovery | Q23 |
| T2-03 | Form 8879 / 8878 with a KBA provider; failure path to wet signature; signer authority by form family | Q22 |
| T2-04 | ATS scenario harness (Pub 1436 / 4163 / 4164), with results recorded as coverage evidence | — |
| T2-05 | Federal and state submission states; extensions; payments tracked separately; notices (W09) | Q24 |

## Wave R: Tax Monitoring and Resolution (ADR-0011; notice handling first)

| Id | Ticket | Proves |
|---|---|---|
| R-01 | **Notice intake and case:** CP/LT and state notices classified; taxpayer, period, issue and amounts extracted and verified; response deadline computed (CDP 30-day windows included); ResolutionCase with evidence checklist | Q16, Q40 |
| R-02 | **Response package:** draft response from case evidence, practitioner approval bound to the package hash, submission and delivery evidence, outcome monitoring | Q15, Q22 |
| R-03 | **Authorizations:** 8821 and 2848 records with scope, periods, CAF and revocation; every access and contact checks the covering authorization; forms rendered and e-signed where allowed | Q12, Q13 |
| R-04 | **Transcripts:** parser for account, wage-and-income and record-of-account transcripts from uploads; versioned snapshots; change detection; practice-wide exception queue | fixtures from real layouts |
| R-05 | **IRS-to-records reconciliation:** agency account vs filed returns, payments and books; discrepancy classification with an evidence-backed explanation | independent worked cases |
| R-06 | Authorized transcript retrieval through IRS-permitted channels only (provider register) | — |
| R-07 | Collection Financial Standards as effective-dated rules kept current by RegWatch | — |
| R-08 | Collection information statements (433-A, 433-A (OIC), 433-B, 433-F) from verified records, with missing-fact requests | Q16 |
| R-09 | Resolution options: guaranteed and streamlined IA, OIC eligibility plus RCP (lump sum and periodic), CNC; comparison for practitioner review; 9465 and 656 | independent worked cases |
| R-10 | Penalty relief: first-time abatement eligibility, reasonable-cause drafts, Form 843 | — |
| R-11 | Post-resolution compliance: payment and filing obligations tracked, reminders under the approved policy | — |
| R-12 | *(after specialist validation)* CSED with suspension and extension events and incomplete-history warnings; bankruptcy dischargeability; appeals and CDP; innocent spouse | specialist-reviewed cases |

Waves V (vertical packs), S1 (fund accounting) and S2 (PE and advanced) follow the spec §20 table. Their
tickets are written when their wave starts, after representative datasets and specialist reviewers are
lined up (spec §20, "Required ownership and resourcing").
