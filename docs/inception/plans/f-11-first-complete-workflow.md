# F-11 plan: the first complete workflow (entity setup → exported report)

Read-only survey 2026-10-09 of the tree at de767ee plus the uncommitted F-10 slice 2 (return workspace, fixture router).
Nothing here is built yet. Status goes in backlog.md; the web runbook (docs/WEB.md) gets a section 11 when slice 1 lands.

**The workflow, as one user story.** Rivera CPA takes on *Harbor Coffee Co.* (an S corporation on the accrual basis,
calendar fiscal year, `general` pack). Bea (role `staff`, engaged on Harbor) sets the entity up and posts the opening
trial balance from the prior accountant's file; uploads January's bank statement (PDF plus the bank's CSV), a vendor
bill and a receipt; reviews what was read, with the source beside each field; the system proposes the journals; Tomas
(role `cpa`) approves them (Bea may not approve what she wrote); Bea matches the statement to the books and signs the
reconciliation off; Tomas closes January, which seals a snapshot; both see the dashboard; Tomas exports the trial
balance, P&L and balance sheet from the snapshot and Sam (the client user) downloads them in the portal.

**Honesty rule.** Every one of the nine steps ships with its React screen, its typed contract and its tests in the same
slice; nothing is API-only, and the slice is not done until `books.spec.ts` drives the sub-workflow in the browser.

## Facts and decisions

- **Postings become proposals; approval posts.** Today `POST .../templates/{tid}`, `bank/post`, invoices and vendor
  payments post directly on the caller's say-so, with no persisted intent, no second person and no idempotency key
  except on invoices. Decision: every journal (template, bank line, document rule, opening balances, reversal) is first a
  `journal_proposal`; approving it is the posting, in one command. Reason: spec §12 (AI and rules propose, people
  post), threat T15/T16, and one receipt per financial effect.
- **Maker ≠ checker, any firm role, no step-up for routine postings.** A proposal a person created or edited cannot be
  approved by that person (`segregation`, the same flag `Returns` carries, off for the single-CPA dev firm); rule- and
  agent-made proposals need one human approval; reversal, reconciliation sign-off, close and reopen keep step-up; close
  and reopen stay reviewer-only (`reviewer_only`, PG `assert_reviewer`). Reason: bulk bookkeeping with a one-time code
  per click is unusable; the consequential acts already have the F-05 freshness rule. Owner flag: a reviewer-only or
  amount-threshold rule for approvals (see flags).
- **The Sentinel of the ledger is deterministic.** `ledger/sentinel.py` is the counterpart of `foundry/verify.py`: no
  model call; checks stored on the proposal like `Proposal.checks`; a failed check blocks approval. Reason: ADR-0007
  (AI never posts) and Q16 (an unverified amount is never taken as a number).
- **Proposals are workflow aggregates.** Each proposal is an `Engine` stream (kind `journal_proposal`, hash-chained,
  anchored by F-13 with every other stream) with a projection row in `journal_proposals` for lists, exactly as
  `tax_returns` + `return_1040` streams work. Reason: free history, `allowed` events for the UI, anchoring.
- **Extracted fields get assertions, like facts.** `document_fields` (append-only; source `model | detector | person`;
  `grounded` from `intake.classify.ground_fields`; supersession) replaces the opaque `documents.fields` JSON for anything
  the books use; a value in `_unverified` is shown as *unverified, enter it from the document* and never used until a
  person confirms or corrects it. Reason: F-07's rule ("facts never overwrite", Q16) applies to bank and vendor
  documents as it does to a W-2.
- **Bank data: lines from CSV/OFX/QFX, control totals from the statement PDF.** Statement PDFs are classified and their
  period and opening/closing balances read as fields; transaction lines come from the bank's CSV (parser exists) or
  OFX/QFX (parser exists in `plugins/builtin.ofx`, moved). Reading transaction tables out of statement PDFs is A1-01 /
  CR-1 work. Reason: both line parsers are deterministic and already here; PDF tables are not.
- **The close computes its snapshot in the database.** PG migration 0009 replaces `close_period` with a function that
  computes the trial balance through the date, stores it with its SHA-256 in `period_closes` and emits `period.closed`,
  all in the one transaction that moves `closed_through`; SQLite does the same inside `BEGIN IMMEDIATE`. Reason: Q17
  needs a snapshot that cannot disagree with the ledger at the moment of closing.
- **Reports of a closed period come from the snapshot, never live.** `GET .../reports/{kind}` for a period ending on or
  before `closed_through` renders from `period_closes`; an open period renders live and says so in the file. Reason: Q17.
- **Formats: CSV and XLSX now, PDF with T1-07's renderer.** `openpyxl` is already a dependency; CSV is stdlib; both are
  parsed back in the Q17 test. PDF reports reuse whatever renderer T1-07 chooses (owner flag). "Verified against a
  licensed copy" is T1-08.
- **Fiscal year is a column, basis stays a recorded fact.** `clients.fiscal_year_start_month` (1–12, default 1; PG 0009
  and the SQLite schema) drives period boundaries for close and reports; the accounting basis is already a profile fact
  (`facts.accounting_basis`) that the context bar reads and the new-client form captures, so it stays there. The M-1 and
  returns keep the tax year. Reason: periods are computed everywhere; the basis has one consumer today.
- **Opening balances are a journal with provenance.** One entry dated the day before the conversion date, source
  `opening`, offset to `3100 Retained Earnings` for the P&L balances carried in; every line names the trial-balance
  document and row it came from (`postings.provenance`, new, nullable); an unmapped row or a non-zero difference is
  refused, never plugged. Reason: "never guessed" (the `trial_balance_import` plugin already refuses unmapped rows).
- **Nothing on container disk.** Reports render in memory and are stored in the vault (sealed, content-addressed) as
  evidence of what was delivered; downloads use the existing signed links. Reason: ADR-0001, ADR-0006.
- **Routes live in `api/books.py`.** A new `APIRouter` included by `app.py` (1,872 lines today). Reason: F-03's layout
  move is pending; a router keeps this slice's routes together without moving anything.

## 1. Gap analysis, step by step

Key per step: *Exists* (API · legacy UI `web/app.js` · React `apps/web`) · *Missing* · *Deliverable*. Every new route is
typed in `api/schemas.py` (operation id = handler name), flows into `packages/contracts/openapi.json → schema.d.ts →
types.ts → endpoints.ts`, and every screen implements the required states (loading, empty, stale, forbidden, partial
success, recovery; `Frozen` where the period is closed; `ReadOnly` for the client role).

### 1.1 Entity setup

- Exists: `POST /api/clients` (typed; id, name, kind, entity_type, formed_under, tax_id_last4, emails, aliases,
  consent_7216_at, domain, facts incl. `accounting_basis`); `domains.onboard` instantiates the pack chart (`general`
  plus the industry pack's additions; `Packs.accounts` chain); `GET /api/packs`; `PATCH .../facts`. React: `/clients/new`
  (hard-codes the pack list, captures basis), `/clients/$clientId/profile`, context bar with period/basis/Frozen.
  Legacy: new-client form. Opening balances: only the `trial_balance_import` plugin (`POST /api/plugins/{id}/run`,
  untyped, posts as `plugin:…`/`agent`, no document link).
- Missing: fiscal year; a chart-of-accounts screen (list, add an account; PG `add_account` function exists, no route);
  opening balances with provenance and a screen; the pack list from the API.
- Deliverable:
  - API: `CreateClientRequest.fiscal_year_start_month`; `GET /api/clients/{id}/accounts → list[Account]`,
    `POST /api/clients/{id}/accounts` (`CreateAccountRequest {code, name, type}`; any firm role; `account.created` in the
    audit); `POST /api/clients/{id}/opening-balances/preview` (`{document_id, as_of}` → `OpeningBalancePreview {rows:
    [{row, name, debit, credit, account|null}], difference, unmapped}`: rows parsed from the uploaded trial balance
    (CSV/XLSX through `intake.extract`, or the document's confirmed fields), mapped by name/type as the plugin does);
    `POST /api/clients/{id}/opening-balances` (`OpeningBalancesRequest {as_of, document_id, lines[{account, amount,
    row}]}` → a `journal_proposal` of kind `opening`, approved like any other).
  - Store: `ledger/opening.py` (parse, map, difference, build the proposal); `ledger/periods.py` (`fiscal_year(client,
    fy) → (start, end)`); `store.add_account` audited; migration `0009_bookkeeping.sql` part 1 (the fiscal-year column;
    `postings.provenance`, jsonb on PG and TEXT on SQLite, written by `post_journal` from the line objects).
  - Screens: `/clients/new` reads `api.clients.packs()` and adds "Fiscal year starts" (month select, default January);
    `/clients/$clientId/setup` (`screens/books/SetupScreen.tsx`: chart of accounts table with add-account form, opening
    balances panel: pick the uploaded trial-balance document, mapped rows beside the document (`DocumentViewer`), unmapped
    rows block with "map or add this account", the difference shown and refused, "Propose opening balances").
  - Tests: `tests/test_opening_balances.py` (mapping, refusal of unmapped rows and of a difference, provenance on every
    line, the fiscal-year boundaries); vitest states contract for `/clients/:id/setup`.

### 1.2 Source upload

- Exists: `POST /api/documents/upload` (one file per request; zip and eml exploded; vault first, then classify;
  deterministic detectors for *Bank statement*, *Invoice*, *Receipt*, *Bill*; model classification through the router with
  grounding, `_unverified`; receipts auto-link to an unsupported expense entry of the same amount within 7 days
  (`link_receipt`); 1099s feed `info_returns`); the fixture router (`ai/fixtures.py`, `AGENTLEDGER_AI_FIXTURES`, e2e only)
  answers by document hash; paged `GET /api/clients/{id}/documents`; inline file route (PDF/PNG/JPEG, sandbox CSP);
  retention classes for Receipt/Invoice/Bank statement (`tax_return_support`). CSV bank lines: `bank/preview` (paste only);
  OFX/QFX: `plugins/builtin.ofx` behind `/api/plugins/ofx/run`. React: documents tab with multi-file upload and the
  partial-success panel, inbox. Legacy: drop zone.
- Missing: a document type for a trial balance; the classifier's field vocabulary for statements, bills and receipts
  (period, opening/closing balance, account last-4; vendor, date, total, tax, invoice number, due date, bill-to); bank
  line files (CSV/OFX/QFX) recognised as documents that carry lines; fixtures for these documents; the bank-line
  categoriser gets no fixture answer (its prompt has no `<document>` block).
- Deliverable:
  - Intake: `classify.py` `DocType` gains `Trial balance` and `Bank transactions` (CSV/OFX/QFX by header: `OFXHEADER`,
    `<STMTTRN>`, or date/description/amount columns); detectors for both; `SYSTEM` prompt lists the field keys above;
    `FOLDERS` entries. `ai/fixtures.py`: when a prompt has no `<document>` block, match on the SHA-256 of the whole prompt
    text (so `make-fixtures.mjs` can script the bank-line batch too) — still e2e/dev only.
  - Fixtures (`apps/web/e2e/fixtures/make-fixtures.mjs`, deterministic): `harbor-tb-2025.csv` (prior TB with one row that
    maps to no account), `harbor-chase-2026-01.pdf` (statement text layer: period 2026-01-01..31, opening 10,000.00,
    closing 12,345.67, last-4 4421), `harbor-chase-2026-01.csv` (the lines, incl. a 1,250.00 deposit that equals an open
    invoice, a payroll debit, a recurring software charge, an unknown payee), `harbor-chase-2026-03.pdf` (March: the gap),
    `bill-roastery-2026-01.pdf` (vendor bill to Harbor), `receipt-depot-2026-01.pdf` whose fixture answer says
    `total: 48.90` while the page says 48.60 (the Q16 case); `fixtures.json` entries for each, plus the bank-batch prompt.
  - Tests: `tests/test_intake_books_documents.py` (detectors, grounding puts the 48.90 in `_unverified`, line files keep
    their bytes in the vault and parse later); `tests/test_ai_fixtures.py` gains the prompt-hash case.
  - Screens: no new screen; the upload panel's lines name the new types ("filed as Bank statement 2026").

### 1.3 Extraction review: the source beside the fields

- Exists: for tax documents only, `returns/documents.py` places boxes with provenance and the review screen opens the
  document beside the field (`DocumentViewer`, `ProvenanceChip`, fact history). The document page
  (`/clients/$clientId/documents/$docId`) shows details and versions, downloads, never the fields; `documents.fields` is
  not in any response.
- Missing: field-level review for every other document; assertions (confirm, correct) that never overwrite; a single
  document route; what the document means for the books (its proposals and linked entries).
- Deliverable:
  - Store: `evidence/fields.py`: `document_fields` (document_id, name, value, source, grounded, asserted_by, at,
    supersedes, note; append-only trigger on both backends; RLS `visible(client_id)` via the document), populated by intake
    at filing time from `Classification.fields` and `_unverified` (grounded false), `assert_field(doc, name, value,
    actor)` (a person's value supersedes), `confirm(doc, names, actor)`, `current(doc) → {name: Field}`, `history(doc,
    name)`; backfill of existing `documents.fields` on first read.
  - API: `GET /api/documents/{id} → DocumentDetail` (the row, `fields: list[DocumentField {name, value, source,
    grounded, confirmed_by, superseded}]`, `unverified: list[str]`, `proposals: list[ProposalRef]`, `entries:
    list[EntryRef]`); `POST /api/documents/{id}/fields` (`FieldAssertionRequest {name, value, note?}`), `POST
    /api/documents/{id}/fields/confirm` (`{names?}`), `GET /api/documents/{id}/fields/{name}/history`. Firm staff inside
    the engagement; the client role reads its own documents (`ReadOnly`).
  - Screens: `screens/documents/DocumentWorkspace.tsx` replaces `DocumentScreen.tsx`: the viewer (moved to
    `screens/documents/DocumentViewer.tsx`, re-exported for the returns editor) on the right, the fields on the left with
    chips *read from the page* / *unverified: enter it from the document* / *confirmed by …* / *entered by …*, inline
    edit with the history popover (reusing `FactHistory`'s layout), and the "In the books" panel listing proposals and
    posted entries with links; "Propose a journal" for documents the rules did not handle. The document page no longer
    says "not among the 100 most recent".
  - Tests: `tests/test_document_fields.py` (Q16: an unverified value is never a number for the rules; a person's
    correction supersedes and is grounded by hand; re-classification never overwrites a person's value); states
    contract and axe for the workspace; `returns.flows.test.tsx` still passes with the moved viewer.

### 1.4 Journal proposal

- Exists: pack posting templates (`GET/POST .../templates`, untyped; `domains.render` with KB rules; `verify_pack`
  proves templates balance); bank suggestions (`bankfeed.suggest`: the client's own history, merchant patterns, the
  `classify` model role capped at 0.75 confidence, duplicate flag by (date, description)); invoices and vendor payments
  (`crm/business.py`, idempotent through `run_command` with the `Idempotency-Key` header); automations (events → tasks).
  The foundry's `Proposal` kinds are platform content only (rules, packs, code), never journals. Legacy: template form
  and CSV preview post directly. React: nothing.
- Missing: a persisted proposal with its basis, evidence and checks; deterministic rules from documents (receipt →
  paid expense, vendor bill → payable); a verifier; maker-checker; the inflow-equals-open-invoice rule (Q07); a screen.
- Deliverable:
  - Store: `ledger/proposals.py`: Definition `JOURNAL_PROPOSAL` (`open → posted | rejected | superseded`; `edit` keeps
    `open`, records `edited_by` and the new `proposal_hash`; guards: segregation, hash unchanged since the approver read
    it, Sentinel checks all ok, period open); projection `journal_proposals` (id `jp_<hex>`, client_id, status, kind
    `template | bank_line | document | opening | reversal | manual`, date, memo, lines json with `provenance`, basis json
    `[{kind: history | merchant | ai | rule | document | person, detail}]`, evidence json `[{document_id, field}]`,
    bank_line_id, document_id, template_id, proposal_hash, created_by, edited_by, created_at); `propose(...)`,
    `edit(...)`, `approve(...)` (posts through `store.post(command_id=…)`, same transaction), `reject(...)`,
    `supersede(...)`. `ledger/rules.py`: document rules (Receipt with confirmed `total`, `date`, `vendor` → expense from
    the vendor's history or "account needed"; Bill/Invoice billed to the client → `vendor_bill` template, new in
    `domains/general.yaml`: Dr expense, Cr 2000; direction unknown → a blocker on the proposal, never a guess); bank
    rules (`bankfeed.suggest` refactored to return proposals with basis; an inflow equal to an open invoice total within
    the terms window proposes `match invoice <number>` through `business.record_payment`, never revenue — Q07).
    `ledger/sentinel.py`: `verify(conn, kb, packs, client, proposal) → list[Check]`: ≥ 2 lines in whole cents that
    balance; accounts exist; treatments known; date in an open period (else `needs_reopen`); every amount grounded in a
    confirmed field or the document text (`ai.grounding.numbers_in`); no unverified field used; the document is not
    already behind a posted entry; the bank line is not already matched or posted; no posted bank entry with the same
    (date, amount, payee); an inflow equal to an open invoice is not proposed as revenue; a reversal names an unreversed
    entry.
  - Commands (`ledger/commands.py`, registered with F-08's `register(Spec(...))`): `approve_journal_proposal` (human;
    payload `{proposal_id, proposal_hash}`; handler re-runs the Sentinel and posts), `reject_journal_proposal` (human),
    `supersede_journal_proposal` (system: a re-import replaces its unposted proposals). `workflow.commands.Context` gains
    `app: AppContext | None` so handlers reach `kb` and `packs` (reason: one bag instead of a parameter per domain).
  - API (`api/books.py`): `GET /api/clients/{id}/journal-proposals?status=&start=&end=&kind=&cursor=` →
    `ProposalPage`; `GET /api/journal-proposals/{pid} → ProposalDetail {proposal, checks, allowed, history}`; `POST
    /api/clients/{id}/journal-proposals` (`CreateProposalRequest {template_id?, inputs?, lines?, date, memo?,
    document_id?}`); `PUT /api/journal-proposals/{pid}` (lines, memo, date); `POST /api/journal-proposals/{pid}/approve`
    (`{proposal_hash}` + `Idempotency-Key`) → `ApproveResult {entry_id, replayed}`; `POST .../reject {note}`; `POST
    /api/clients/{id}/journal-proposals/approve` (`{items: [{id, proposal_hash}]}` → `BulkResult` with one line per item:
    posted / refused with the reason). `POST .../templates/{tid}` now answers `{proposal_id}` (the legacy ledger tab's
    toast says "proposed"); `bank/preview` and `bank/post` are removed with the legacy ledger tab's import box (the paths
    are gone from the contract; `/api/journal-proposals` does not collide with the foundry's `/api/proposals`).
  - Screens: `/clients/$clientId/proposals` (`screens/books/ProposalsScreen.tsx`: virtualised `DataTable`, filters by
    status/kind/period, row chips for basis and failed checks, select-and-approve with `PartialSuccess` per item,
    `Frozen` rows for a closed date with "needs a reopen"); `/clients/$clientId/proposals/$pid` (`ProposalScreen.tsx`:
    lines editor, checks list with the Sentinel's wording, the document beside the lines through the shared viewer, the
    history, Approve disabled with the API's reason — `can(me, "books.approve", {proposerId})` mirrors the guard — and
    Reject with a note). `can.ts` gains `books.propose`, `books.approve`, `accounts.manage`.
  - Contracts: `client.ts` gains an `idempotencyKey` request option (header `Idempotency-Key`, a UUID made per logical
    action and kept across the step-up retry and a network retry).
  - Tests: `tests/test_journal_proposals.py` (segregation on both the edit and the approve; a stale hash is refused; a
    failed check blocks; approve is one effect under 100 concurrent retries (extends Q02 to the API path); replay answers
    `replayed=True` with the same entry; a changed payload under the same key is 409; Q04: a proposal dated inside a
    period closed after it was written is refused with the reopen reason and shown as `needs_reopen`; Q07 rule); vitest
    flows for the disabled reason and bulk partial success.

### 1.5 Approved posting

- Exists: `store.post` → PG `post_journal` (balance, open period with the row locked, accounts, hash chain, outbox
  `journal.posted`, audit, `commands` receipt when `p_command_id` is given) — but `store.post` passes `NULL` for the
  command id; SQLite mirrors the checks inside `BEGIN IMMEDIATE`; `reverse_journal`; `entry_documents`; F-08's command
  contract (`workflow/commands.py`: receipt, replay, conflict, advisory lock, human/system split) is used by returns only.
  Legacy ledger tab lists entries; React: nothing.
- Missing: `command_id` through `store.post`; postings reached only through approvals; a ledger screen; reversal as a
  proposal (today `reverse_entry` is `cpa_only`, which every firm role passes).
- Deliverable:
  - Store: `store.post(..., command_id: str | None = None, provenance per line)` → PG `p_command_id`; on SQLite the
    receipt comes from the enclosing `commands.dispatch` (table `commands`) and `post` itself stays idempotent only
    through it (documented). `store.reverse` is reached only through a proposal of kind `reversal`.
  - API: `GET /api/clients/{id}/entries?start=&end=&account=&cursor=&limit=` → `EntryPage` (typed `Entry`, `Posting`
    with `provenance`, `document_id`, `reversed_by`, `command_id`); `POST /api/clients/{id}/entries/{eid}/reverse` creates
    the reversal proposal (`{reason}`) instead of posting.
  - Screens: `/clients/$clientId/ledger` (`screens/books/LedgerScreen.tsx`: paged, filter by account and period, each
    posting's provenance chip opens the document beside the entry, "Propose reversal", chain status from the detail).
  - Tests: `tests/test_pg_ledger.py` gains `test_store_post_passes_the_command_id_and_the_database_replays_it`;
    `tests/test_ledger_entries_api.py` (paging, provenance on the wire, reversal is a proposal).

### 1.6 Statement reconciliation

- Exists: nothing persistent. `bankfeed.suggest` marks a line duplicate when an entry with the same (date,
  description) and a `bank%` source exists; the cash account is the hard-coded `1000`. Legacy: CSV paste. React: nothing.
- Missing: bank accounts, statements with control totals, lines with identity, matching, exclusions, completeness,
  sign-off, findings; the screen (A1-01 later adds feeds, pending and corrected events).
- Deliverable:
  - Store (`ledger/statements.py`, `ledger/matching.py`; migration 0009 part 2): `bank_accounts` (id, client_id, name,
    account_code → `accounts`, kind `bank | card`, institution, last4); `bank_statements` (id, bank_account_id,
    document_id (the PDF, nullable), lines_document_id (the CSV/OFX, nullable), period_start, period_end,
    opening_balance, closing_balance, balances_source `fields | person`, status `imported | reconciled`); `bank_lines`
    (id, statement_id, line_no, date, description, amount, external_id (OFX FITID), line_hash (date, amount, normalised
    description), state `unmatched | matched | posted | excluded`, entry_id, proposal_id, excluded_reason, decided_by,
    at; unique (bank_account_id, external_id|line_hash) so a re-import is idempotent); `reconciliations` (statement_id,
    statement_balance, ledger_balance, difference, unmatched, excluded, signed_by, at). Functions: `import_statement`
    (lines parsed from the vault bytes by `parse_csv` / `parse_ofx`, control totals from the statement document's
    confirmed fields or entered by a person), `candidates(statement)` (exact amount, date within 3 days, same cash
    account, unmatched entry; an inflow equal to an open invoice → `match_invoice`; everything else → a proposal from
    `ledger/rules.py`), `match`, `exclude`, `coverage(bank_account) → gaps` (contiguous periods; a missing month is a
    gap), `ledger_balance_at(account_code, date)`, `reconcile(statement)` (closing balance = ledger balance at
    period_end and no unmatched line; else refused with the list), finding `bank.statement_gap` and `bank.unreconciled`
    (`integrity/checks.py`, so the sweeper and the dashboard see them).
  - Commands: `match_bank_lines` (human; `{items: [{line_id, entry_id | invoice_id | proposal_id}]}`), `exclude_bank_line`
    (human), `reconcile_statement` (human; step-up), `import_statement` (human; idempotent by the lines document's hash).
  - API: `GET/POST /api/clients/{id}/bank-accounts`; `GET /api/bank-accounts/{aid}/statements → {statements, coverage,
    gaps}`; `POST /api/bank-accounts/{aid}/statements` (`ImportStatementRequest {document_id?, lines_document_id,
    period_start?, period_end?, opening_balance?, closing_balance?}` → `StatementImportResult {statement, imported,
    duplicates}`); `GET /api/bank-statements/{sid}/lines?state=&cursor=` paged; `GET /api/bank-statements/{sid}/candidates`;
    `POST /api/bank-statements/{sid}/matches` → `BulkResult`; `POST /api/bank-lines/{lid}/exclude {reason}`; `POST
    /api/bank-statements/{sid}/reconcile → Reconciliation`.
  - Screens: `/clients/$clientId/banking` (`BankAccountsScreen.tsx`: accounts with last statement, coverage bar, gaps
    as warnings, "Add account", "Import statement" picking the uploaded PDF and lines documents);
    `/clients/$clientId/banking/$accountId` (`ReconciliationScreen.tsx`: statement selector; the matching workspace with
    statement lines (virtualised, filter by state) on the left and candidates / ledger entries on the right; per-line
    actions Match, Propose, Exclude (reason); the control strip: statement closing balance vs ledger balance, difference,
    unmatched count; "Sign off" (step-up) disabled with the API's reason until the difference is zero; `Frozen` when the
    period is closed; the statement PDF in the viewer for the control totals).
  - Tests: `tests/test_reconciliation.py` (Q07: a deposit equal to an open invoice is matched as a payment, revenue is
    unchanged, one entry exists for it, a second import of the same CSV changes nothing; Q09: statements for January and
    March leave a February gap that `coverage` names, the gap is a finding and a close warning; exact-amount matching
    never matches across cash accounts; exclusion needs a reason; sign-off refused while a line is unmatched; both
    backends).

### 1.7 Close snapshot

- Exists: `close_period` / `reopen_period` (`POST /api/clients/{id}/periods/{close|reopen}`, untyped; reviewer-only,
  step-up; PG asserts the role, audits, emits `period.closed` / `period.reopened`; the closed-period trigger refuses
  later postings); `clients.closed_through`; React context bar derives `Frozen` / "partly closed"; the integrity check
  `ledger.closed_period`. No screen anywhere (legacy has none).
- Missing: the snapshot; a preview with blockers and warnings; acknowledgements; the list of closes; the screen; the
  frozen state on proposals and reconciliation.
- Deliverable:
  - Store (migration 0009 part 3): `period_closes` (id, client_id, through, snapshot jsonb `{accounts: [{code, name,
    type, balance}], totals, entry_count, chain_head, kb_version, engine: "books-2026.1"}`, snapshot_hash, acknowledgements
    jsonb, closed_by, closed_at, reopened_by, reopened_at, reopen_reason; immutable except the three reopen columns, set
    once); `trial_balance_at(p_client, p_date)` SQL function; `close_period(p_client, p_through, p_actor, p_role,
    p_acknowledgements jsonb)` computes and stores the snapshot, moves `closed_through`, audits with the hash, emits
    `period.closed {through, snapshot_hash, close_id}`; `reopen_period` records on the latest close. `ledger/close.py`:
    `preview(through)` → trial balance, blockers (open proposals dated on or before `through`: "post or reject first";
    an imported statement in the period not reconciled, when the firm requires it — owner flag), warnings to acknowledge
    with a reason (statement gaps, unreconciled statements, documents of the period with unverified fields, bank accounts
    without a statement covering `through`), `close`, `reopen`, `closes`. The SQLite path does the same work in one
    `unit_of_work`.
  - Commands: `close_period` (human; step-up; reviewer), `reopen_period` (human; step-up; reviewer).
  - API: `GET /api/clients/{id}/close/preview?through= → ClosePreview`; `POST /api/clients/{id}/periods/close`
    (`CloseRequest {through, acknowledgements: [{code, reason}]}` → `PeriodClose`); `POST .../periods/reopen`
    (`ReopenRequest {back_to, reason}`); `GET /api/clients/{id}/closes → list[PeriodClose]` (snapshot summarised; the
    full snapshot at `GET /api/closes/{id}`).
  - Screens: `/clients/$clientId/close` (`CloseScreen.tsx`: period picker (fiscal months), the trial balance preview,
    blockers with links to the proposal or statement, warnings each with its acknowledgement field, "Close through …"
    (step-up; disabled with the reason for non-reviewers), the list of closes with their hashes and reopen records,
    "Reopen" with a reason). Downstream: proposals, the reconciliation workspace and the setup screen render `Frozen`
    with the close's reason; the context bar unchanged.
  - Tests: `tests/test_close_snapshot.py` (the snapshot equals `trial_balance_at` at commit; a later posting after a
    reopen leaves the first snapshot unchanged and a second close makes a second row; an edit to `period_closes` is
    refused on both backends; Q04 end to end: a proposal written before the close is refused after it with the reopen
    reason; blockers and acknowledgements recorded in the audit with the hash); `tests/test_pg_ledger.py` for the
    function and its grants.

### 1.8 Dashboard

- Exists: `GET /api/dashboard` (typed; per-client cards: integrity, open findings, open tasks, documents this month;
  CPA extras: foundry proposals, staleness, review queue, tasks, runs, AI status); `GET /api/clients/{id}` (balances,
  pack KPIs, findings, deadlines, opportunities, chain, M-1, AR aging, 1099 readiness, deals). React: `/clients` list,
  `OverviewScreen` (integrity, three KPIs, findings, deadlines, tasks, records incl. "books closed through"),
  `PortalScreen`. Legacy: command center.
- Missing: a firm-level dashboard screen in React (home is `/clients`); close and reconciliation status; proposals
  awaiting approval; freshness; P&L and cash figures; portal reports.
- Deliverable:
  - API: `DashboardCard` gains `closed_through, open_proposals, needs_approval_by_other, unreconciled_accounts,
    statement_gaps, documents_needing_review, latest_close_hash`; `Dashboard.as_of`; `ClientDetail.books` (`{period:
    {start, end, closed}, pl: {revenue, expenses, net_income}, cash: [{bank_account, ledger_balance, last_statement,
    reconciled_through}], open_proposals, latest_close}`), computed for the fiscal period of the `year` parameter.
  - Screens: `/dashboard` (`DashboardScreen.tsx`; `homeFor` for firm staff becomes `/dashboard`; cards per client with the
    figures above and links into the workspaces; "as of …" from `as_of` and TanStack `dataUpdatedAt`, `Stale` on a failed
    refetch, Refresh); `OverviewScreen` gains the Books card (P&L, cash by account, reconciliation and close status,
    links); `PortalScreen` gains "Your reports" (closed periods with downloads) and "Documents waiting for a word"
    (count only; answering is A1-09).
  - Tests: `tests/test_web_contracts.py` key sets for the dashboard and the detail; states contract for `/dashboard`;
    axe.

### 1.9 Exported report

- Exists: `GET /api/clients/{id}/export/{plugin_id}` through signed links: `beancount_export`, `quickbooks_iif_export`,
  `tax_trial_balance_export` (CSV of `store.balances` for the previous calendar year plus M-1 lines; the route passes
  no config, so the year cannot be chosen). `store.balances` is a period movement, not a cumulative balance. Legacy:
  download buttons on Integrations. React: nothing.
- Missing: trial balance, P&L, balance sheet and general ledger as reports; XLSX; snapshot binding; a screen; delivery.
- Deliverable:
  - Store: `ledger/reports.py` (`trial_balance(as_of)` cumulative; `profit_and_loss(start, end)`; `balance_sheet(as_of)`
    with retained earnings rolled from prior fiscal years; `general_ledger(start, end)`; all Decimal, from
    `postings_in`; `from_snapshot(close)` for closed periods); `ledger/render.py` (CSV via stdlib, XLSX via openpyxl;
    every file carries a footer/sheet with entity, basis, period, "closed — snapshot `hash`" or "open period, unaudited",
    generated at, build; `X-AgentLedger-Snapshot` response header); the file is also stored in the vault
    (`reports/<client>/<period>/<kind>.<fmt>`, retention class `workpaper`, new in `config/retention.yaml`, 7 years) with
    `report.exported {kind, format, period, snapshot_hash, sha256}` in the audit.
  - API: `GET /api/clients/{id}/reports → ReportIndex {periods: [{start, end, closed, snapshot_hash}], kinds, formats}`;
    `GET /api/clients/{id}/reports/{kind}?start=&end=&format=csv|xlsx|json` (session header or signed `dl` link; `json`
    serves the screens); the three plugin exports stay and gain `?tax_year=`.
  - Screens: `/clients/$clientId/reports` (`ReportsScreen.tsx`: period picker, closed/open label with the hash, the
    report rendered on screen from `json`, Download CSV / XLSX through `api.links.create`, "Hand-off exports" for the
    IIF / tax TB / Beancount files); the portal lists closed periods' downloads (client role).
  - Tests: `tests/test_reports.py` (Q17 exact: after a close, the CSV and XLSX trial balances parse back to the
    snapshot's figures and hash to the letter; the P&L and balance sheet tie (net income = equity movement); an open
    period's file says so; a posting after a reopen changes the live report and not the snapshot's; downloads through a
    signed link bound to user, firm and path; `report.exported` in the audit; both backends).

## 2. Orchestration, audit trail and evidence

**One request, one unit of work** (the domain commits atomically, receipts where there is a financial effect):
create client and onboarding; add account; upload (vault first, classify, fields; the model call runs inside the request
today, as in F-10 slice 2 — see risks); field assertions; proposal create/edit/approve/reject (approve = the
`approve_journal_proposal` command → `post_journal` with the command id; the F-08 receipt and the ledger's own receipt
both exist, the database one being the backstop under the client lock); statement import, matches, exclusions,
reconciliation sign-off; close and reopen (snapshot in the same transaction); report rendering on demand.

**Durable workflow (F-08 runner): the close package.** `period.closed` on the outbox starts
`close-package-<client>-<close_id>` (`workflow/flows/close_package.py`, steps in `flows/steps.json` `close_package`:
`render-trial-balance`, `render-profit-and-loss`, `render-balance-sheet`, `store-{kind}`, `notify-client`,
`notify-firm`). Every step is a system command (`render_report`, `store_report`, `notify_close_package` in
`ledger/commands.py`): "ensure", not "do" — `store_report` looks the object up by content address before writing
(`Vault.address`), so a crash between the write and the receipt converges on retry; `notify_client` creates the portal
task under a dedupe key and, when SMTP is configured, sends through `crm.send_message` as a two-phase activity (started
marker committed before the send; a lost answer leaves the notification `unknown`, which is reported on the close
record and never resent automatically — email has no lookup; the portal task is the delivery of record). Relay mapping
added in `workflow/runner.py` (`period.closed` → start) and `edge/src/relay.ts` with a `CLOSE` Workflow binding
(`edge/src/workflows/close_package.ts` mirroring the step names); the Cloudflare side is written in the slice and
verified on staging with F-08's own live run. `journal.posted`, `period.reopened` and the new `proposal.*`,
`statement.*` events are information for the relay.

**Audit trail and evidence.** Documents and report files live in the vault (sealed, content-addressed, versions,
retention, holds; reports under their own class). Receipts: `commands` (ledger, PG) / `app_commands` + `commands`
(F-08) for approve, match, reconcile, close, reopen, import. Audit records (hash-chained, anchored hourly by F-13 with
every workflow stream head, which now includes every proposal stream): `account.created`, `document.field_asserted`,
`document.field_confirmed`, `proposal.created | edited | approved | rejected | superseded`, `ledger.posted` (with the
command id), `statement.imported`, `bank.matched | excluded`, `statement.reconciled`, `period.closed` (with
`snapshot_hash`, acknowledgements), `period.reopened`, `report.exported`. The close snapshot's hash therefore sits inside
an anchored chain within the hour; `GET /api/evidence/integrity` is unchanged.

## 3. Acceptance

**Python (both backends, CI):** the test files named per step, plus `tests/test_books_workflow_api.py`: the whole story
over HTTP with the `api` fixture of `tests/test_tenancy.py` (bootstrap admin, one firm, Bea `staff` granted on the client,
Tomas `cpa`, Sam `client`), asserting at the end that every step's audit action exists in order and the chain verifies.
Acceptance-map rows: Q07 → pass for the invoice-plus-deposit case (A1-02 keeps the full AR scenario), Q09 → pass for
uploaded statements (A1-01 keeps feeds), Q16 → the bank/vendor document case added, Q17 → pass, Q04 → strengthened
(proposal awaiting posting), Q02 → the API path under 100 concurrent retries.

**Playwright `apps/web/e2e/books.spec.ts`** (own 300 s timeout; seed adds `iris` (firm_admin) for this spec; it invites
Bea and Tomas through the API like `return-review.spec.ts`, grants Bea the engagement with `POST /api/auth/users/{id}/
grants`, invites Sam as a client user; the API runs with the fixture router; `checkA11y` after every page and every
forced state):

1. Bea creates "Harbor Coffee Co." (business, s_corp, pack `general`, basis accrual, fiscal year January): the context
   bar shows `accrual` and `FY2026`; `/setup` lists the pack's 29 accounts; the API's `accounts_created` is in the
   success toast.
2. Opening balances: upload `harbor-tb-2025.csv`; `/setup` shows the rows beside the document; the unmapped row blocks
   with "map or add this account"; Bea adds `1510 Espresso machines` and maps it; the difference reads 0.00; "Propose".
3. Uploads: statement PDF, lines CSV, vendor bill, receipt, March statement → the partial-success panel names each type
   ("filed as Bank statement 2026", "filed as Bank transactions 2026", "filed as Bill 2026", "filed as Receipt 2026");
   nothing lands in the inbox.
4. Document workspace (receipt): the viewer frame carries the signed `inline=1` URL (`inline`, `application/pdf`,
   `sandbox` asserted on the response as the return spec does); `total` reads "48.90 — unverified: not in the
   document"; its proposal shows the failed check "amount not grounded"; Bea corrects to 48.60; the field reads
   "entered by Bea, grounded"; the proposal's checks are all ok (Q16).
5. Banking: Bea adds account "Chase …4421" on `1000`, imports January (PDF + CSV): the control strip shows opening
   10,000.00 and closing 12,345.67 from the fields; candidates: the 1,250.00 deposit offers "Match invoice INV-1001
   (open)" and no revenue proposal; the software charge offers the merchant-pattern account; the unknown payee reads
   "unknown payee — choose an account" (the batch fixture answers nothing for it); Bea matches and proposes.
6. Proposals: Bea's Approve on the proposal she edited is disabled with "the approver must be a different person from
   the proposer"; Tomas signs in, bulk-approves: the partial-success panel lists each item posted with its entry id,
   and the proposal dated outside January remains open; the ledger lists the entries with provenance chips; the
   invoice payment posted once (Q07: the P&L revenue figure on the Books card is unchanged after the match).
7. Reconciliation: with every line matched or posted the difference reads 0.00; Bea signs off through the forced
   step-up (`page.route` answers the first POST with `403 step_up_required`); the statement reads "reconciled". The March
   statement import makes the coverage bar show "February 2026 missing" (Q09) and the finding appears on the overview.
8. Close: Tomas opens `/close` for January: the trial balance preview; the blocker "1 proposal dated in the period:
   post or reject first" (he rejects it with a note); the warning "no statement covers February 2026" acknowledged with
   a reason; "Close through 2026-01-31" through the forced step-up; the close row shows its hash; the context bar reads
   "partly closed"; Bea's remaining January-dated proposal shows `Frozen` "needs a reopen" and its Approve is disabled
   with the API's reason (Q04); Tomas reopens with a reason (step-up) and closes again: two rows, two hashes.
9. Dashboard: `/dashboard` shows Harbor's card with "closed through Jan 31, 2026", "1 account reconciled", "1 statement
   gap", "0 proposals waiting", and an "as of" time; the overview's Books card shows net income for FY2026.
10. Reports: `/reports` for January reads "closed — snapshot `hash`" equal to the close row's hash; the CSV download's
    bytes are parsed in the test and every account's balance equals the on-screen trial balance and the footer carries
    the hash (Q17 in the browser; exactness is the pytest's job); the XLSX download is named and carries the
    `X-AgentLedger-Snapshot` header; Sam signs in, the portal lists January's reports and downloads the P&L.
11. Forced states: a 409 on approve renders the Conflict state with each reason; a network error on `/dashboard` keeps
    the cards with the `Stale` marker; a 403 on `/close` for Bea renders `Forbidden` with the API's text.

## 4. Slices (dependency order; S ≈ a day of agent work, M two to three, L four to five)

| # | Slice (a usable sub-workflow) | Size | Files | Contract |
|---|---|---|---|---|
| 1 | **Set up the entity and its opening trial balance from a document**: fiscal year, packs from the API, chart of accounts screen, opening balances with provenance, `store.post(command_id, provenance)`, `ledger/commands.py` with `approve_journal_proposal` for the opening kind only | M | `ledger/opening.py`, `ledger/periods.py`, `ledger/commands.py`, `ledger/proposals.py` (minimal), `pg/migrations/0009_bookkeeping.sql` part 1, `db.py` schema, `api/books.py`, `api/schemas.py`, `screens/books/SetupScreen.tsx`, `NewClientScreen.tsx`, `routes/_app/clients/$clientId/setup.tsx` | `Account`, `CreateAccountRequest`, `OpeningBalancePreview`, `OpeningBalancesRequest`, `ProposalDetail` |
| 2 | **Upload and review a document with the source beside its fields**: field assertions, document detail route, the workspace, new document types and fixtures, prompt-hash fixtures | M | `evidence/fields.py`, `intake/classify.py`, `intake/pipeline.py`, `ai/fixtures.py`, `screens/documents/{DocumentWorkspace,DocumentViewer}.tsx`, `e2e/fixtures/make-fixtures.mjs` | `DocumentDetail`, `DocumentField`, `FieldAssertionRequest` |
| 3 | **Propose and post with maker-checker**: the proposal aggregate and projection, document and bank rules, the Sentinel, approve/reject/bulk, templates become proposals, the ledger screen, reversal as a proposal, `Idempotency-Key` in the client | L | `ledger/proposals.py`, `ledger/rules.py`, `ledger/sentinel.py`, `ledger/bankfeed.py` (refactor), `domains/general.yaml` (`vendor_bill`), `workflow/commands.py` (`Context.app`), `screens/books/{ProposalsScreen,ProposalScreen,LedgerScreen}.tsx`, `auth/can.ts`, `packages/contracts/src/client.ts` | `Proposal*`, `BulkResult`, `EntryPage`, `Entry`, `Posting` |
| 4 | **Reconcile a statement**: accounts, statements, lines, candidates, matching, exclusions, coverage gaps, sign-off, findings | L | `ledger/statements.py`, `ledger/matching.py`, `integrity/checks.py`, migration 0009 part 2, `screens/books/{BankAccountsScreen,ReconciliationScreen}.tsx` | `BankAccount`, `Statement*`, `BankLine`, `Candidate`, `Reconciliation` |
| 5 | **Close the period with a sealed snapshot**: `trial_balance_at`, the new `close_period`, `period_closes`, preview with blockers and acknowledgements, the close screen, `Frozen` downstream | M | `ledger/close.py`, migration 0009 part 3, `ledger/store.py`, `screens/books/CloseScreen.tsx` | `ClosePreview`, `CloseRequest`, `ReopenRequest`, `PeriodClose` |
| 6 | **Export the reports**: TB, P&L, balance sheet, general ledger; CSV and XLSX; snapshot binding; vault copy and audit; the reports screen and portal downloads; the close-package flow (runner, relay, Cloudflare mirror) | M | `ledger/reports.py`, `ledger/render.py`, `workflow/flows/close_package.py`, `workflow/flows/steps.json`, `workflow/runner.py`, `edge/src/relay.ts`, `edge/src/workflows/close_package.ts`, `config/retention.yaml`, `screens/books/ReportsScreen.tsx`, `PortalScreen.tsx` | `ReportIndex`, report `json` shapes |
| 7 | **See the whole in one place and prove it**: dashboard route and cards, Books card on the overview, freshness, `books.spec.ts` end to end, states contract and axe for every new route, docs/WEB.md section 11, backlog and acceptance-map updates | M | `screens/DashboardScreen.tsx`, `OverviewScreen.tsx`, `queries/index.ts`, `test/states.contract.test.tsx`, `e2e/books.spec.ts`, `e2e/seed.py` | `DashboardCard`, `ClientDetail.books` |

Slices 1–3 are sequential (each needs the previous one's command and fields); 4 and 5 can run in parallel worktrees
after 3 (5 reads 4's reconciliation status for its warnings last); 6 after 5; 7 last. Each slice ends with the contract
drift gate, both Python backends green, vitest and the spec steps it enables.

### Owner flags

- Chart-of-accounts packs: `general` plus the six industry packs as they are; a firm adds accounts per client in the
  setup screen; pack edits stay Domain Architect proposals. Confirm the numbering convention and whether a firm-wide
  custom pack is wanted (would be a `domains/<firm>/` directory in the firm's tenant).
- Report formats first: CSV and XLSX; PDF through T1-07's renderer once chosen (align with that plan before slice 6).
- Bank file formats first: CSV and OFX/QFX lines plus statement PDFs for control totals; PDF transaction tables and
  feeds are A1-01 / CR-1.
- Approval policy: maker ≠ checker by any firm role is the default; say if postings above an amount, or all postings,
  need a credentialed reviewer, and whether approvals need step-up.
- Close with gaps: a missing statement month is a warning acknowledged with a reason (default) or a blocker.
- Retention class for exported reports (`workpaper`, 7 years proposed).
- Fiscal years other than calendar: keep the column (default) or drop it for v1.

### Risks

- *Extraction quality without a model in CI.* The fixture router answers only scripted hashes; CI therefore proves the
  pipeline, not reading quality. The deterministic detectors and grounding carry the honesty rule (nothing unverified
  posts); real statements and receipts need the local model and will be measured on the demo set before the pilot.
- *PostgreSQL-only invariants vs the SQLite profile.* The balance and closed-period triggers, `trial_balance_at` and the
  immutability of `period_closes` are database facts only on PostgreSQL; SQLite relies on application code inside
  `BEGIN IMMEDIATE`. The pytest suite runs both; Playwright runs on SQLite (the `web` CI job). A PostgreSQL lane for the
  e2e launcher is a follow-up worth doing before the pilot.
- *Volume in the matching workspace.* A card statement can carry thousands of lines: server-side paging and filters,
  indexes `(statement_id, state)` and `(client_id, date, amount)` on entries, candidates computed in SQL by amount and
  date window, bulk matches with per-item results, virtualised tables; no whole-statement payloads to the browser.
- *Model calls inside the upload request.* A slow local model holds the request; the fixture router hides this in CI.
  Moving extraction into a workflow step (`extraction.pending` on the document) is planned with CR-1.
- *Two receipts for one posting* (F-08's and the ledger's). Deliberate: the database one is the backstop; the test for
  concurrent retries covers both.
- *Behaviour changes.* Templates and bank imports stop posting directly; the legacy ledger tab loses its import box and
  gets a "proposed" toast. Any script relying on `bank/post` breaks (none in the repo).
- *Fiscal year touches every "year" computation.* Close and reports use fiscal periods; `client_detail`'s calendar
  `year` and the M-1 stay as they are; the context bar's label will say FY with the start month when it is not January.
- *Step-up fatigue* on sign-off, close and reopen in one sitting (three codes in the spec); the 300 s timeout covers it.

### Out of scope, handed to

Feeds, pending and corrected events (A1-01, Q08); full AR and AP (A1-02, A1-03); the close
checklist, accruals and review notes (A1-05); personal-expense splits (CR-2); upload completeness checks (CR-1); the
portal's uploads and questions (A1-09); PDF rendering (T1-07); exports verified against licensed software (T1-08).
