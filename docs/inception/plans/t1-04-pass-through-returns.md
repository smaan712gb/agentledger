# T1-04 plan: Form 1065 and Form 1120-S packages, Schedule K-1 delivery into owners' 1040 cases

Read-only study of the engine, the ledger, the CRM, evidence retention, the platform grants and IRS.gov, 2026-10-09.
Build plan for `returns/pass_through.py`, `returns/partnership.py`, `returns/s_corporation.py` and the K-1 delivery path.
Acceptance Q20. Status per slice is kept in backlog.md (T1-04).

## Precondition owned by the business

`coverage/coverage.yaml` has `owner: null`; `f1065` and `f1120s` are `unsupported`. As with T1-01, slices are built and
proven against IRS worked examples, land as `manual-assisted` with `limits:` and `evidence:`, and move to
`preparation-validated` only on the tax-content owner's sign-off. The owner also names the first entity types (flags, §4).

## Engine and platform facts the plan rests on

- 1040 conventions to reuse unchanged: whole-dollar lines in `Sheets` keyed by engine form keys (`returns/sheet.py`);
  every statutory figure through `ctx.param(rule_id, date(y, 12, 31))` from `rules/**/*.yaml` (`MissingValue` blocks);
  inputs are pydantic facts and documents with provenance; `Optional` = unknown and blocks, never zero; stable diagnostic
  codes (`error` blocks every gate); `Result.carryforwards` persisted per version in `return_carryforwards` (kind, detail)
  and rolled forward by `Returns.roll_forward`; hand-worked tests with expected values from IRS sources, a golden
  `calc` per form, PolicyEngine where it models the item (it models nothing for entities).
- `returns/store.py` hard-codes the 1040: `create` validates `IndividualReturn`, starts kind `return_1040`,
  `_calculate` calls `compute_individual`, `MODEL_FIELDS`/`_check_fields`/`DOC_LISTS` derive from `IndividualReturn`,
  `TAX_FORMS` is the 1040 list, `ENGINE_VERSION = "1040-2026.4"`. `tax_returns.form` already distinguishes forms
  (UNIQUE (client_id, tax_year, form)); the state machine `RETURN_1040` and `filing.SUBMISSION` are form-agnostic
  except `coverage_id("US-FED") == "mef_1040"` and the API's `defs["return_1040"]`.
- `K1` (`returns/model.py`): owner, entity name/EIN/type (partnership, s_corporation, estate_trust), passive,
  ordinary_income, net_rental_income, interest, ordinary/qualified dividends, net ST/LT gain, guaranteed_payments,
  section_179, self_employment_earnings, qbi (None → ordinary + rental − §179), w2_wages, ubia, sstb, S3 foreign
  fields (foreign_tax_paid, foreign_source_income, foreign_country, category never defaulted, accrued), S4 fields
  (net_section_1231_gain box 10/9, unrecaptured_1250_gain box 9c/8c). Consumed at Schedule B (int/div), Schedule D
  lines 5/12, Schedule E Part II line 32 (`passive_k1_loss` warning), Schedule SE, Form 8995/8995-A, Form 4797 Part I
  and the §1250 worksheet, Form 1116, Form 8960 line 4a; `Disposition.k1` links by 1-based index. No field for
  royalties, collectibles, other income, charitable, investment interest, tax-exempt income, nondeductible expenses,
  distributions, AMT items, credits, capital account or liabilities.
- `documents.py` has no `BOXES["K-1"]`: a filed K-1 never populates `k1s`; since "K-1" is in `TAX_FORMS` it blocks review
  through `unaccounted_documents` until a person records "entered by hand". `intake/classify.py` detects "K-1"
  (`schedule k-1`), folder income; `config/retention.yaml` classes K-1 as `property_basis` and
  `evidence/records.DOC_TYPE_GROUPS["K-1"] = "basis"`.
- Ledger: append-only entries/postings with `tax_treatment` tags (meals, entertainment, fines_penalties,
  officer_life_insurance, municipal_interest, depreciation, federal_income_tax, travel, vehicle); `clients.closed_through`
  is the close (`close_period` CPA-only; `reopen_period` with a reason); there is no frozen close snapshot yet (A1-05);
  `balances(conn, client, start, end)` is the trial balance; `verify_chain` gives the chain head hash; `assets` table
  → `calc/federal.Asset` (3/5/7-year half-year MACRS, §179 elected, bonus; no real property classes, no mid-quarter);
  `ledger/m1.py` is a Form 1120 M-1 (permanent items, depreciation book vs tax, §179 phase-out across placed-in-service
  property) over `postings_in` — the pass-through M-1 generalises it. Charts come from `domains/*.yaml` packs (codes,
  names, types; no tax-line attribute).
- CRM/entity: `clients` (kind business/individual, entity_type free text — deadlines.yaml expects `s_corp` /
  `partnership` / `c_corp`, formed_under, tax_id_last4, `facts` JSON incl. `accounting_basis`, `state`, `employees`);
  `parties` are a business's own customers/vendors (not owners); `engagements` (client, type, tax_year, stage); no
  Party/OwnershipInterest table yet (domain-model.md plans `directory/`). `config/deadlines.yaml` dates 03-15 / 09-15
  for `entity_type in ['s_corp','partnership']` (calendar-year only).
- Evidence: `PASS_THROUGH_FORMS = {1065, 1066, 1120-S, 1041}`; `_pass_through` decides by the year's filed return, else
  entity type; a pass-through's records wait for `owners_filed` events (`record_tax_event`, CPA-only, note ≥ 10 chars,
  form 1040 on a business client only as `owners_filed`); `tax_year_events.due_on` carries a fiscal-year due date.
- Security: `client_scope(user)` → `["*"]` for cpa/firm_admin, the engagement grants for staff, one client for a client
  user; PostgreSQL RLS `visible(client_id)` on every client-keyed table including `documents`; `Platform.grant` is
  staff-only; the API checks `scope(user, client_id)` per route; step-up (`fresh`) for approve/sign/release.
- Filing (F-08): `RETURN_1040` (preparing → in_review → approved → awaiting_signature → signed → release_approved →
  transmitted → accepted/rejected; paper_filed; unknown; void), `Filing.plan/transmit_submission/record_ack`, federal
  first with linked states; `Returns.filing_blockers` through `coverage.check_forms(forms + ["mef_1040"])`.
- Golden: `calc/federal.CALCULATORS` registry (`form_1040_line`, `form_1040x_line`) run by `agentledger golden` over
  `golden/scenarios.yaml`. docs/irs (untracked): `EFILE_APPLICATION_PACKET.md` ("Nov 1, 2026: 1065 schemas valid in
  ATS"; the e-file application lists return types 1065 and 1120 incl. 1120-S), `ats-ty2026/` holds 1040 scenarios 1-7,
  12-14 only — no 1065/1120-S scenario is downloaded; scenario 6 (Jeremy Davidson, Schedule E with a partnership K-1)
  is the one that exercises the 1040 side of a K-1.

## IRS.gov status (read 2026-10-09; drafts are not to be relied on, re-read when final)

| Product | Posted | Note |
|---|---|---|
| Form 1065 (2026 draft) | 07/17/2026 | "For calendar year 2026, or tax year beginning …, 2026"; Form 1125-A inside the PDF |
| Instructions for Form 1065 | 2025 edition (01/14/2026) | no 2026 draft; line semantics taken from 2025 until it posts |
| Schedule K-1 (Form 1065) 2026 draft; Partner's Instructions | 05/21/2026; 2025 (11/26/2025) | |
| Schedules K-2 / K-3 (Form 1065) 2026 drafts; their instructions | 06/17/2026; 2025 (11/03/2025) | |
| Schedule D (Form 1065) 2026 draft and its 2026 instructions | 05/20/2026; 09/18/2026 | |
| Schedule M-3 (Form 1065) | Rev. Dec 2021; instructions Nov 2023 | no 2026 draft (`f1065sm3--dft.pdf` is the old draft) |
| Form 1120-S (2026 draft) | 06/26/2026 | Instructions still the 2025 edition (01/16/2026) |
| Schedule K-1 (Form 1120-S) 2026 draft; Shareholder's Instructions 2026 draft | 04/29/2026; 09/18/2026 | |
| Schedules K-2 / K-3 (Form 1120-S) 2026 drafts; instructions 2025 | 06/11/2026; 10/24-28/2025 | |
| Schedule D (Form 1120-S) 2026 draft; Schedule M-3 (1120-S) instructions 2026 draft | 06/01/2026; 09/25/2026 | the M-3 form itself is not re-drafted (`f1120ssm3--dft.pdf` 404) |
| Form 8825 Rev. December 2025 + new Schedule A "Rental Real Estate Other Deductions" + instructions | 09/11, 11/14, 12/12/2025 | current revision page updated 08/04/2026; 04/08/2026 note on reporting other expenses; applies to TY2026 |
| Form 4562 (2026 draft); Form 4562-B Amortization (Dec 2026 draft); instructions 2025 | 06/01/2026; 06/23/2026 | 4562-B is new (also in ATS scenario 12) |
| Form 7203 | Rev. December 2022, no draft | the 1040 side (CR-6) |

Schedules K-2/K-3 domestic filing exception (2025 instructions, both families): (1) no foreign activity, or only
passive-category foreign income with ≤ $300 of creditable foreign taxes shown on payee statements; (2) every direct
partner a U.S. citizen/resident individual, domestic estate/grantor or non-grantor trust with such beneficiaries, an S
corporation, a disregarded single-member LLC, or a compliant domestic partnership; (3) partners notified, at the latest
when the K-1 is furnished, that no K-3 comes unless requested; (4) no request by the one-month date (one month before
the return is filed; a later request → K-3 to that partner within one month). A separate Form 1116 exemption exception
exists. §199A: Schedule K-1 box 20 code Z (1065) / box 17 code V (1120-S) print "STMT" and attach a statement listing QBI
items per trade or business, W-2 wages, UBIA, qualified REIT dividends, PTP income, SSTB status and any aggregation.
2025 instructions: Schedules L and M-1 not required when total receipts and total assets are under the Schedule B
thresholds (1065 Q4: < $250,000 receipts and < $1,000,000 assets, K-1s furnished by the due date, no M-3; 1120-S Q11:
< $250,000 receipts and < $250,000 assets — verify both against the 2026 instructions); M-3 at ≥ $10,000,000 total
assets (1065 also ≥ $35,000,000 receipts or a reportable entity partner — verify); §6698/§6699 for returns required to
be filed in 2026: $255 per owner per month, 12 months (verify; the returns-filed-in-2027 figure is Rev. Proc. 2025-32's).

## 1. Engine design

### 1.1 Files and dispatch

- `returns/entity_model.py`: pydantic inputs (`EntityReturn`, `Owner`, `OwnershipPeriod`, `Books`, `BookTaxFacts`,
  `RentalProperty`, `EntityPriorYear`, `K2K3Facts`, `SCorpFacts`, `PartnershipFacts`), mirroring `model.py` so
  `_check_fields`/`_unknown` work on it unchanged. Separate from `pass_through.py` because the 1040 keeps inputs and
  engine apart and the delivery slice imports the model without the engine.
- `returns/pass_through.py`: the shared core — books population from the close, Schedule L/M-1 bridge, the asset register
  bridge, separately stated items, Form 8825 and the Form 4797 call, allocation to owners, K-1 assembly, the
  `EntityResult` (same `to_dict` shape as `Result`: forms, facts, notes, diagnostics, summary, pinned, carryforwards,
  plus `k1s` keyed by owner ref). `partnership.py` (`compute_partnership`) and `s_corporation.py` (`compute_s_corporation`)
  own page 1, Schedule K, M-2 and the family-specific rules; each is a thin ordered driver like `_Individual.compute`.
- Form keys, prefixed per family so `coverage.form_id` stays a base-key lookup: `f1065`, `f1065_sch_k`, `f1065_k1[<owner>]`,
  `f1065_sch_l`, `f1065_m1`, `f1065_m2`, `f1065_analysis`; `f1120s`, `f1120s_sch_k`, `f1120s_k1[<owner>]`, `f1120s_sch_l`,
  `f1120s_m1`, `f1120s_m2`; shared attachments `f8825`, `f4797`, `f4562`, `f1125a`, `sch_d_1065` / `sch_d_1120s`;
  worksheets `ws_se_earnings`, `ws_k3_exception`, `ws_section_179_entity`, `ws_passive_investment_income` (`FORM_IDS` → None).
  `coverage.FORM_IDS` maps `f1065_*` → `f1065`, `f1120s_*` → `f1120s`; new registry ids `f8825`, `f1125a`, `f4562`,
  `sch_d_1065`, `sch_d_1120s`, channels `mef_1065`, `mef_1120s`.
- `returns/store.py`: a `FORMS` registry `{form: (model, compute, workflow_kind, engine_version, tax_forms, doc_lists,
  mef_channel)}` for "1040", "1040-X", "1065", "1120-S"; `create(form=...)`, `save_inputs`, `_calculate`, `_check_fields`,
  `relied_on`, `_record_retention_facts`, `unaccounted_documents` read it. Engine versions `1065-2026.1`, `1120S-2026.1`
  (the 1040's stays). Reason: one store, one event model, one filing path; the form decides the engine.
- API: `POST /api/clients/{id}/returns` takes `form` (default "1040"); `GET /api/returns/{rid}` uses the return's kind for
  `allowed`/`waiting_on`; the client view exposes `forms.f1065`/`f1120s` and that client's own K-1 only; new
  `POST /api/returns/{rid}/k1s/deliver` (§1.8). `apps/web` return screens gain the entity forms (F-10 slice 2 scope note).

### 1.2 Entity facts (`EntityReturn`)

- Identity: `form` ("1065" | "1120-S"), `name`, `ein`, address, `tax_year` (the year the period begins, as the IRS form
  year), `period_start`/`period_end` (fiscal years and short years allowed in the model; a 52-53-week year, a short
  year with depreciable assets → errors `entity_52_53_week_year`, `short_tax_year_depreciation`), `final_return`,
  `initial_return`, `amended` (1065-X / 1120-S amended are T1-09), `date_business_started`, `business_activity_code`
  (6-digit NAICS, CODED: a non-code blocks), `business_activity`, `product_or_service`, `accounting_method`
  (cash | accrual | other; populated from `clients.facts.accounting_basis` with provenance `source: client`, None blocks),
  `number_of_owners_at_year_end` (derived, must equal the table), Schedule B answers as named booleans/facts
  (B-1 disclosure owners ≥ 50 %, §754 election in effect, like-kind exchanges, foreign accounts, `partnership_representative`
  (required on a 1065, None blocks), BBA election out (Schedule B-2: ≤ 100 eligible partners → rule), §448(c) gross
  receipts item, K-2/K-3 exception assertion, 1120-S: `s_election_effective` (item E, Form 2553 on file), `c_corp_history`,
  `accumulated_e_and_p_at_year_end` (None blocks when `c_corp_history`)).
- `books: Books` — the trial balance and the ledger pin (§1.4), populated, never typed; `book_tax: BookTaxFacts` — what
  the ledger cannot know (§1.4); `owners: list[Owner]` (§1.3); `rentals: list[RentalProperty]` (Form 8825, §1.5);
  `dispositions`, `business_use_recaptures` (the S4 models, reused); `portfolio` (the entity's 1099-INT/1099-DIV/1099-B
  items populated from documents into Schedule K lines 5/6/8/9); `prior_year: EntityPriorYear` (Schedule L beginning
  column, M-2 beginning balances, partner capital beginning, §179 carryover, AAA/OAA/AE&P) with the prior-year 1065/1120-S
  as its document; `k2k3: K2K3Facts` (foreign activity, foreign taxes on payee statements, partner notification date,
  requests received with dates, filing date for the one-month rule); `s_corp: SCorpFacts` (officer compensation detail,
  > 2 % shareholder health premiums included in W-2s, loans from shareholders beginning/end per owner, repayments,
  distributions per owner, §1377(a)(2) election → error); `partnership: PartnershipFacts` (guaranteed payments per
  partner for services/capital, liabilities by class and allocation basis, §704(c) property → error, special
  allocations → error, varying interests → error).

### 1.3 Ownership table

- `Owner`: `ref` (stable id used in form keys and carryforward details), `name`, `tin` (sealed with the inputs; the K-1
  document carries last-4 only), `tin_type` (ssn | ein | itin), address, `kind` (individual | partnership | corporation |
  s_corporation | estate | trust | disregarded_entity | ira | other — the K-1 item I1 / S-corp eligibility),
  `client_id` (a client of this firm when the owner is one; None = outside owner), `periods: list[OwnershipPeriod]`
  (start, end, `units` or `shares`; for a 1065 also `profit_pct`, `loss_pct`, `capital_pct` — item J beginning/ending),
  1065: `partner_type` (general | limited | llc_member_manager | llc_member), `domestic: bool`,
  `se_status` (subject | not_subject | None: stated, never inferred — the LLC-member question; None blocks when ordinary
  income or guaranteed payments exist: `partnership_se_status_unknown`), `retirement_plan` (item I2), liabilities share
  basis; 1120-S: `shares_beginning`/`shares_end` (item H), `loans_from_shareholder_beginning/end` (item I).
- Checks: percentages/shares sum to 100 % / outstanding shares per period (`ownership_table_does_not_sum`); an S
  corporation's owners must be eligible (§1361(b): ≤ 100, individuals/estates/qualifying trusts, no nonresident alien,
  one class of stock) — `s_corporation_ineligible_shareholder`, `s_corporation_shareholder_count` (errors: the
  election question is the CPA's); a foreign partner → `partnership_foreign_partner_withholding` (§1446, error);
  ownership changes during the year: S corporation → per-share-per-day (§1.6); partnership → `partnership_varying_interests`
  (§706(d) interim closing/proration is a scope limit) unless the percentages never change.
- §754 / §743(b): the election is a recorded Schedule B fact; a transfer of an interest or a distribution with the
  election in effect, or a stated §743(b) adjustment, raises `partnership_section_743b_adjustment` (error: the K-1 box
  13 code V / box 20 reporting and the asset-level step-up are out of scope). Decision: record the facts now so the
  return states them, compute nothing.

### 1.4 Book-to-tax bridge from the approved close

- Precondition: `clients.closed_through >= period_end` (`books_not_closed` error names the date). The approved close is
  today's close; A1-05's frozen snapshot replaces the pin when it exists. `Returns.populate_from_books(rid)` (same shape as
  `populate_from_documents`) builds `books` from `ledger.store.balances(period_start, period_end)` plus balance-sheet
  balances at `period_end` and at `period_start − 1 day` (cumulative), with provenance
  `{source: "ledger", chain_head, balances_hash, closed_through, as_of}` on every account amount; a stated account
  value that differs from the ledger is a fact conflict (same machinery, `current_source: ledger`), never a silent
  override. Every gate re-reads the ledger as a dry run (like `_drift`): a reopen or a posting dated in the period after
  population is drift and blocks (`books_changed_since_population`); a hash-bound return reopens on recompute.
- Schedule L mapping: a per-account `tax_line` for the family (`sch_l: "1" | "2a" | … | "22"`) from the domain pack
  (`domains/*.yaml` accounts gain `schedule_l`) with a per-client override table (`account_tax_lines`, new, in the firm
  store); an unmapped account with a non-zero balance blocks (`schedule_l_account_unmapped`); assets = liabilities +
  capital in both columns or `schedule_l_out_of_balance` (error; it means the TB is wrong, never plugged). Beginning
  column = the ledger at `period_start − 1 day` when the books cover it, else `prior_year.schedule_l` from the prior-year
  return document; both present and different → conflict.
- M-1 (`pass_through.m1`, generalising `ledger/m1.py`): line 1 book net income from the TB (revenue − expense);
  permanent items from `tax_treatment` tags (meals/entertainment at `us_fed.business.*_deductible_pct`, fines, officer
  life insurance, municipal interest) → 1065 lines 4b/6a (1120-S 3b/5a) and nondeductible expenses (K line 18c/16c);
  depreciation book (TB tag) vs tax (asset register, `tax_depreciation` + `section_179_allowed`) → 4a/7a (3a/6a);
  guaranteed payments → 1065 line 3; income on Schedule K not on books and deductions on K not on books (§179, charitable
  separately stated) → 2/7 (2/6); line 9 (8) must equal the Analysis of Net Income (K income less deductions) or
  `m1_does_not_reconcile` blocks with the difference named (goal: zero unexplained difference, CR-4). A tagged treatment
  unknown to the bridge is an error, not ignored.
- Facts beyond the ledger (`BookTaxFacts`, each Optional, None blocks only when the books show the item): nondeductible
  expenses not tagged (political, club dues, penalties posted without the tag), tax-exempt income not tagged, officer
  compensation (1120-S line 7: W-2s/payroll report of officers, or a tagged account; `officer_compensation_unknown`
  when wages exist and no officer amount is stated), guaranteed payments per partner for services and for capital
  (must tie to the tagged account `guaranteed_payments` (new treatment) or block `guaranteed_payments_do_not_tie`),
  distributions per owner, cash vs property (must tie to the equity movement in the TB: `distributions_do_not_tie`),
  capital contributions per owner, §179 elections (from the register's `sec179_elected`; the entity-level §179(b)(3)
  business income limit computed in `ws_section_179_entity`, the disallowed amount a carryforward `section_179_carryover`;
  the owner-level limit is the 1040's), bad debts (accrual only; cash-method bad debts block), inventory method and
  §263A applicability for Form 1125-A (small business exemption under the §448(c) test; otherwise `form_1125a_section_263a`
  error), §163(j) (exempt under §448(c) unless a tax shelter; otherwise `form_8990_required` error), charitable
  contributions by category (tagged `charitable` treatment, new), state tax refunds/credits.
- Decision: distributions and guaranteed payments are per-owner facts that must tie to the books rather than a new
  posting dimension — the ledger has no owner dimension today and A1-05 may add one; the tie check keeps the books
  authoritative without blocking this ticket on a ledger change.

### 1.5 Page 1, Schedule K, separately stated items

- Page 1 from the TB by `tax_line` mapping (gross receipts 1a, returns 1b, COGS 2 via `f1125a` lines 1-8 with
  inventories from Schedule L, other income 7/5, deductions 9-21 (1065) / 7-20 (1120-S); interest subject to §163(j) check;
  depreciation line from `f4562` (Part I §179, Part II bonus, Part III MACRS from the register; a depreciable asset with
  no register entry and a stated amount is allowed with provenance to the client's fixed-asset schedule and a warning
  `depreciation_stated_not_computed`); depletion, farm, energy-efficient buildings, employment credits reducing wages,
  ordinary income from other pass-throughs (a K-1 received by the entity) → errors naming the form.
- Form 4797: S4's `_form_4797` is lifted into `returns/form_4797.py` as a function of (dispositions, recaptures, asset
  register, year, activity resolver) returning the same `_F4797` structure, called by `individual.py` and
  `pass_through.py` alike (no behaviour change for the 1040; tests pinned). Part II ordinary → page 1 line 6 (1065) / 4
  (1120-S); Part I §1231 gain → K line 10/9 and the unrecaptured §1250 part → K 9c/8c; §1231(c) lookback is the owner's
  (not applied at the entity: the K-1 carries the gross figure, Form 4797 instructions for partners). §291 (line 26f)
  for an S corporation with C history → `s_corporation_section_291` error.
- Form 8825 (Rev. Dec 2025): one `f8825` sheet, properties in columns A-D (+ additional forms as `f8825[2]`), lines 1-16
  per property (rents 2, expenses 3-15; line 15 other via the new Schedule A itemisation as facts `other_deductions[]`),
  totals 17-21; depreciation per property from the register where the asset exists (real property classes are not in
  `Asset` yet → stated amounts with provenance and the warning above until the register gains 27.5/39-year SL
  mid-month); net to K line 2; `RentalProperty` facts (address, type, fair rental days, personal use → §280A error, a
  self-rental to the business as a fact for the 1040's grouping).
- Separately stated: interest (K 5/4), dividends (6a-b/5a-b; 6c dividend equivalents error), royalties (7/6), capital
  gains through `sch_d_1065`/`sch_d_1120s` from `portfolio` 1099-B items and dispositions (8, 9a-c / 7, 8a-c), §1231
  (10/9), other income with codes (11/10: cancellation of debt, §1256, recoveries → errors except stated
  `other_income_items` with a code), §179 (12/11), charitable by category (13A-G / 12A-G), investment interest (13H/12B),
  §59(e)(2) and other deductions (codes; unsupported codes error), SE earnings (1065 14A-C from `ws_se_earnings`: the
  1065 instructions' worksheet, general partners/member-managers: ordinary business income from trade or business
  activities + guaranteed payments for services − §179 and the owner's share of the same, limited partners: guaranteed
  payments for services only, §1402(a)(13)), credits (15/13: any credit → `entity_credit_unsupported` error, Form 3800
  is out of scope), international (16/14: K-2/K-3 attached only if the exception fails → error `schedules_k2_k3_required`;
  the exception test `ws_k3_exception` records the four criteria, the notification date and the one-month date; a
  late request is a task to furnish a K-3 within one month), AMT items (17/15: post-1986 depreciation adjustment from
  the register's AMT depreciation is not computed → warning `amt_depreciation_adjustment_not_computed`, like the 1040's
  2k/2l; depletion/oil-gas error), tax-exempt income and nondeductible expenses (18/16 A-C), distributions (19A-C / 16D-E),
  investment income/expenses (20A-B / 17A-B), §199A statement items per trade or business (20Z / 17V: QBI = ordinary
  income ± the Reg. §1.199A-3 adjustments the engine knows (§1231 gain excluded, §179 and charitable not subtracted at
  the entity), W-2 wages from the payroll tag/W-3, UBIA from the register (`ubia_unknown` blocks when QBI ≠ 0 and the
  register is empty), SSTB a stated fact, aggregation → error), foreign taxes (21 / K-3: any foreign tax → the K-2/K-3
  path, error in v1), §448(c) gross receipts item (20AG / 17AC) from page 1.

### 1.6 Allocation and Schedule K-1

- Partnership: every K line × the owner's `profit_pct` (losses by `loss_pct`; capital items by `capital_pct` where the
  instructions say so), constant through the year; guaranteed payments are the partner's own (K-1 4a/4b/4c, not shared);
  special allocations and §704(c) block (§1.3). S corporation: per-share-per-day (§1366(a)(1), §1377(a)(1), Reg.
  §1.1377-1): weight = Σ periods (shares held ÷ shares outstanding × days ÷ days in the year), 10 decimal places,
  applied to every K line alike; the §1377(a)(2) election blocks.
- Rounding: the whole-dollar Schedule K line is split into whole dollars per owner (`whole(line × weight)`); the residual
  goes to the owner with the largest weight (ties: first in table), recorded in the K-1 note. Invariant Σ K-1 == K per
  line, exact. Reason: a testable identity beats cents nobody files.
- Partner capital (item L, tax basis method required since 2020): beginning from `prior_year` (or the modified outside
  basis / modified previously taxed capital method as a stated beginning with its method named — a fact, never computed
  here), + contributions, + current-year net income (K-1 lines 1-11 less 12-13 and nondeductible 18C), − distributions
  (19), other increases/decreases stated; ending capital is a carryforward per owner (`partner_tax_capital`, detail =
  owner ref) and the next year's beginning; Σ ending == M-2 line 9 (`m2_capital_does_not_tie` error). Item K liabilities:
  nonrecourse by profit share, recourse by the stated basis (`partnership_liability_allocation_unknown` blocks when
  liabilities exist and no basis is stated); qualified nonrecourse a stated fact. Item J beginning/ending from the table.
  At-risk and outside basis are the 1040's (Form 6198 is a Schedule C/E coverage limit today) — the K-1 carries K, L and
  19 so the owner side can compute them later.
- S corporation M-2 (`f1120s_m2`, columns AAA, OAA, PTEP (shareholders' undistributed taxable income previously taxed),
  AE&P): beginning from `prior_year`; AAA + ordinary and separately stated income (not tax-exempt), − losses and
  deductions, − nondeductible expenses not related to tax-exempt income, − distributions (the ordering rules of
  §1368(c) when AE&P > 0: distributions beyond AAA → dividends from AE&P (K 17c) → `s_corporation_distributions_exceed_aaa_with_aep`
  error in v1); without AE&P an excess distribution reduces basis / is capital gain at the shareholder level — the K-1
  reports 16D in full and the engine warns `s_corporation_distributions_exceed_aaa` with the consequence (CR-6: never
  relabelled as a loan); OAA takes tax-exempt income and related expenses; ending balances are carryforwards (`aaa`,
  `oaa`, `aep`). Shareholder stock/debt basis (Form 7203) is the 1040 side (CR-6); the K-1 carries 16A-E and item I.
- §1374 built-in gains tax: facts `c_corp_history`, `s_election_effective`, `net_unrealized_built_in_gain`; with C
  history inside the recognition period (`us_fed.s_corporation.built_in_gains_recognition_period_years`) and any
  disposition, `s_corporation_built_in_gains_tax` (error, Schedule D Part III not computed). §1375 excess net passive
  income tax: computed test in `ws_passive_investment_income` — applies only when AE&P > 0 at year end; passive
  investment income (interest, dividends, royalties, rents unless the active-rental facts say otherwise, gains from
  stock/securities) ÷ gross receipts > `us_fed.s_corporation.passive_investment_income_pct` → `s_corporation_passive_investment_income_tax`
  (error in v1; the worksheet at `us_fed.corporate.income_tax_rate` is a later slice) plus the third-consecutive-year
  termination warning (§1362(d)(3)). Reasonable compensation: officer compensation 0 with distributions > 0 →
  warning `s_corporation_officer_compensation_zero` naming the facts (CR-6: never a prohibition).

### 1.7 K-1 line map (engine K-1 sheet → the 1040 `K1` model)

| 1065 K-1 | 1120-S K-1 | `K1` field | Status |
|---|---|---|---|
| 1 | 1 | `ordinary_income` | exists |
| 2 | 2 | `net_rental_income` | exists |
| 3 | 3 | `other_rental_income` | new (Schedule E line 28 non-RE rental) |
| 4a/4b/4c | — | `guaranteed_payments_services`, `guaranteed_payments_capital`; `guaranteed_payments` stays the total | split new; total exists |
| 5 | 4 | `interest` | exists |
| 6a / 6b | 5a / 5b | `ordinary_dividends` / `qualified_dividends` | exist |
| 7 | 6 | `royalties` | new (Schedule E Part I royalties) |
| 8 / 9a | 7 / 8a | `net_short_term_gain` / `net_long_term_gain` | exist |
| 9b | 8b | `collectibles_gain` | new (Schedule D line 18) |
| 9c | 8c | `unrecaptured_1250_gain` | exists (S4) |
| 10 | 9 | `net_section_1231_gain` | exists (S4) |
| 11 (codes) | 10 (codes) | `other_income: dict[code, Money]` | new; unknown codes block on the 1040 |
| 12 | 11 | `section_179` | exists |
| 13A-G / 13H / other | 12A-G / 12B / other | `charitable: dict[code, Money]`, `investment_interest`, `other_deductions: dict` | new |
| 14A/B/C | — | `self_employment_earnings` exists; `gross_farming_income`, `gross_nonfarm_income` new | |
| 15 | 13 | `credits: dict[code, Money]` | new; any value blocks the 1040 (Form 3800 unsupported) |
| 16 (K-3 attached) | 14 | `k3_attached: bool`, K-3 fields → existing `foreign_*`, `category` | S3 fields exist |
| 17A-F | 15A-F | `amt_items: dict[code, Money]` | new (warning on Form 6251) |
| 18A/B/C | 16A/B/C | `tax_exempt_interest`, `other_tax_exempt_income`, `nondeductible_expenses` | new (Form 1040 line 2a; basis) |
| 19A-C | 16D | `distributions_cash`, `distributions_property` | new (basis; Form 7203 later) |
| — | 16E | `loan_repayments` | new |
| 20A/B | 17A/B | `investment_income`, `investment_expenses` | new (Form 4952/8960 later) |
| 20Z stmt | 17V stmt | `qbi`, `w2_wages`, `ubia`, `sstb`, plus `qbi_items: list` per trade or business | exist; list new |
| 20AG | 17AC | `gross_receipts_448c` | new, informational |
| 21 | (K-3) | `foreign_tax_paid` | exists (S3) |
| item K / L | item I / H | `liabilities_*`, `capital_*`, `shares_*`, `loans_from_shareholder_*` | new, informational + basis |
| header | header | `entity_name`, `entity_ein`, `entity_type`, `final_k1`, `amended_k1`, `recipient_tin_last4`, `prior_year_unallowed_loss` (S5) | mostly exist |

Every new field defaults to zero only where the K-1 box is additive and a blank box means none (the IRS prints nothing
for zero); `other_income`, `credits`, `other_deductions` codes the 1040 does not model raise errors naming the code.
`documents.BOXES["K-1"]` maps `box1` … `box21` (1065) and `box1` … `box17` (1120-S, decided by `fields.form`) onto
these fields with provenance; `REQUIRED["k1s"] = "entity_ein"`; `owner_of` works because the generated document carries
`recipient_tin_last4` and `recipient_name`. Third-party K-1s (uploaded) take the same path, so the round trip and the
"K-1 from other software" case share one mapping.

### 1.8 The K-1 as a delivered document

- Generation: for each owner of an entity return version, a document of `doc_type "K-1"`, `tax_year`, `fields` = the K-1
  boxes with the engine's keys plus `form`, `entity_name`, `entity_ein`, `recipient_name`, `recipient_tin_last4`,
  `final_k1`, `amended_k1`, and `_source = {entity_return_id, version, package_hash, owner_ref, k1_hash}`; bytes = the
  rendered K-1 (T1-07's PDF once it exists; until then a canonical JSON data sheet with the IRS box captions and the
  §199A/K-3 statements) containing the full TIN, sealed in the vault (`Vault.put(data, owner=doc_id)`); `channel
  "k1-delivery"`, `sender` = the entity client id, `classified_by` = the engine version, `status "filed"`,
  `retention_class` from policy (K-1 → property_basis). `documents.sha256` is unique, so the bytes include the return
  version and owner: redelivery of the same version is idempotent (returns the existing document).
- Delivery rule (`Returns.deliver_k1s(rid, actor, role, *, final=True)`, a human command, step-up like approve):
  1. The entity return is `accepted` or `paper_filed` (final K-1s). Draft K-1s from `approved` or later carry
     `final_k1 = False` and are permitted only if the owner enables drafts (flag); a 1040 holding a draft K-1 item blocks
     with `k1_draft_on_return` until the final one replaces it.
  2. Owner a client of this firm (`Owner.client_id`): the actor's `client_scope` must include both the entity and the owner
     client (CPAs and firm administrators see the firm; staff need both engagement grants) — otherwise 403 and nothing
     is written; recommended (flag): an open tax engagement of the owner client for the year. The document is filed into
     the owner's workspace; the owner's own portal sees it like any filed document. Nothing of the entity return other
     than that owner's K-1 crosses the client boundary.
  3. Owner outside the firm: the document is filed on the *entity* client with `fields.recipient = "external"`, and
     furnished through the portal/download link (`GET /api/documents/{doc_id}/file` with the signed `dl` token, A1-09) or
     the export bundle; the furnishing date is recorded (`k1.furnished`, method, date), evidence for §6031(b)/§6037(b),
     the K-3 one-month rule and §6698/§6699.
  4. Correction: a new entity version delivered again supersedes the earlier K-1 documents of the same (entity return,
     owner): their `status` becomes `superseded` (new status; `populate` reads `filed` only, retention and holds treat
     it as evidence like `filed`, deletion rules untouched), `parent_id` links the new document to the old. The owner's
     1040 then sees an orphaned item and a new item at the next population, and every gate's dry-run drift blocks
     until a person decides (this is Q20). A filed owner return gets a task "amendment candidate: corrected K-1 from
     <entity> v<version>" (T1-09 wording), dedupe per document.
  5. Audit: on the entity client `return.k1_generated` {return_id, version, package_hash, owner_ref, document_id,
     sha256, final} and `return.k1_delivered` {owner client or external, method}; on the owner client
     `document.received` (channel k1-delivery) and a task "Schedule K-1 from <entity> (2026) received: populate the
     return" (dedupe `k1:<document_id>`). `return_document_uses` links the owner's return to the K-1 document as today.
  6. `owners_filed`: when a firm-client owner's return that relies on the K-1 document reaches `accepted`/`paper_filed`,
     the entity client gets a `tax_year_events(kind=owners_filed, form=1040, occurred_on, note=the owner return id and
     its acceptance)` row through a new system path in `records.record_tax_event` (today CPA-only; the evidence is the
     filed return itself). Outside owners stay a CPA's manual `owners_filed` with evidence, as now.
- Reason for a document rather than a direct input copy: the 1040's facts machinery (identity by `source_document`,
  never-overwrite, orphan and drift gates, retention by document) already gives the invalidation semantics Q20 needs;
  copying numbers into `k1s` would bypass all of it.

### 1.9 Filing state machine and e-file

- `Definition(kind="return_entity", …)` reuses `RETURN_1040`'s transitions and guards verbatim (one constructor
  `return_definition(kind, waiting)`), with waiting text "signature on Form 8879-PE (1065) or Form 8879-CORP (1120-S)";
  `signers` facts name the general partner / LLC member-manager or officer; signer authority by form family is T2-03.
  `filing.coverage_id` takes the form: `mef_1065`, `mef_1120s` (the e-file application's 1065 and 1120 families); the
  federal-first submission model, acknowledgements, `unknown` handling and state linkage from F-08 apply unchanged (state
  pass-through, composite and PTE returns are T1-05). T2-01 maps the sheets onto the IRS1065 / IRS1120S schemas; the
  business ATS opens with the 1065 schemas on Nov 1, 2026 (docs/irs packet).
- Due date: 15th day of the 3rd month after `period_end` (§6072(b)), §7503 rollover, 6-month extension (Form 7004, Reg.
  §1.6081-3): computed from the period in `returns/filing.py` and surfaced as a fact; `config/deadlines.yaml` keeps its
  calendar-year rows.

## 2. Rules (each `rules/us-fed/**/*.yaml` with citation; "verify" = confirm from the primary source before the slice lands)

| Rule id | Value / shape | Source to verify | Use |
|---|---|---|---|
| `us_fed.depreciation.section_179_limit`, `…_phaseout_threshold` (exist) | 2026: $2,560,000 / $4,090,000 | Rev. Proc. 2025-32 §4.24 (already cited) | entity §179; add `section_179_suv_limit` (2026 figure, same Rev. Proc.) when vehicles appear |
| `us_fed.penalties.section_6698_per_partner_month`, `section_6699_per_shareholder_month` | $255, max 12 months, returns required to be filed in 2026; 2027 figure from Rev. Proc. 2025-32 | Rev. Proc. 2024-40; Rev. Proc. 2025-32 (verify both) | info diagnostic only (`late_filing_exposure`), never a computed line; Rev. Proc. 84-35 relief note for ≤ 10 partners |
| `us_fed.international.k2_k3_domestic_exception_foreign_tax_limit` | $300 | Instructions for Schedules K-2/K-3 (2025) Domestic Filing Exception; re-read 2026 | `ws_k3_exception` |
| `us_fed.business.gross_receipts_test` (§448(c)) | 2025 $31,000,000; 2026 from Rev. Proc. 2025-32 (verify); `…_manufacturing` $80,000,000 (P.L. 119-21 §70209, verify) | §448(c)(4), Rev. Procs. | §163(j) exemption, §263A/§471(c), cash method of a partnership with a C-corp partner, Schedule B item |
| `us_fed.s_corporation.passive_investment_income_pct` | 0.25 | §1375(a), §1362(d)(3)(A) | §1375 test |
| `us_fed.s_corporation.built_in_gains_recognition_period_years` | 5 | §1374(d)(7) | §1374 scope limit |
| `us_fed.s_corporation.max_shareholders` | 100 | §1361(b)(1)(A) | eligibility error |
| `us_fed.partnership.bba_election_out_max_partners` | 100 | §6221(b)(1)(B) | Schedule B-2 fact check |
| `us_fed.corporate.income_tax_rate` (exists) | 0.21 | §11(b) | §1374/§1375 rate ("highest rate in §11(b)") when implemented |
| `us_fed.business.schedule_l_m1_exemption` (per family) | 1065: receipts < $250,000 and assets < $1,000,000; 1120-S: both < $250,000 | Form 1065 Schedule B Q4 / Form 1120-S Schedule B Q11, 2025 instructions (verify 2026) | whether L/M-1/M-2/item L are required (computed anyway; the flag decides printing) |
| `us_fed.business.schedule_m3_total_assets_threshold` | $10,000,000 (+ 1065 receipts $35,000,000, reportable entity partner as facts) | Schedule M-3 instructions (verify) | `schedule_m3_required` error |
| `us_fed.business.meals_deductible_pct` etc. (exist) | 50 % / 0 % | §274 | M-1 permanent items |
| `us_fed.partnership.se_earnings_limited_partner` | statutory text only | §1402(a)(13) | `ws_se_earnings` citation (no figure) |

Not rules: the per-share-per-day method (§1377), the tax-basis capital method, the K-3 one-month date (dates), the
§6072(b)/§6081 periods — statutory logic with citations in code.

## 3. Validation

- No PolicyEngine for entities (its model is the household). Hand-worked fixtures, expected values never from the engine:
  Form 1065 instructions (Analysis of Net Income, the Worksheet for Figuring Net Earnings From Self-Employment, Schedule
  K-1 item L with the tax basis method, Schedule M-1/M-2 line definitions); Pub. 541 (guaranteed payments under §707(c),
  partner's share of liabilities and basis, distributions); Form 1120-S instructions (per-share-per-day allocation,
  Schedule M-2 AAA/OAA ordering, the Excess Net Passive Income Tax Worksheet and the §1375 25 % test, officer
  compensation line 7); Reg. §1.1377-1(c) examples (mid-year stock transfer); Reg. §1.706-4(e) examples as scope-limit
  tests (varying interests block); Pub. 542 (M-1 permanent items shared with corporations); Pub. 946 Table A-1 (asset
  register depreciation, as in S4); Form 8825 instructions (Rev. Dec 2025, Schedule A other deductions); Form 4797
  examples (S4's, reused); Instructions for Schedules K-2/K-3 domestic filing exception examples. Each slice: ≥ 2
  hand-worked whole-return cases per form plus per-line cases; a workflow test proving `submit_for_review` is refused
  naming each scope limit; exact whole-dollar equality.
- Property tests (`tests/test_returns_pass_through_invariants.py`, hypothesis): Σ K-1 == Schedule K per line; M-1 last
  line == Analysis of Net Income; Schedule L balances both columns; Σ partner ending capital == M-2 ending; per-share-per-day
  weights sum to 1; a transfer on day d gives the buyer (365 − d)/365 of each item.
- Golden: `calc/federal.py` gains `form_1065_line` and `form_1120s_line` (inputs: return, form, line, owner for K-1
  sheets) and `golden/scenarios.yaml` gets `f1065-2026-two-member-llc-services` (guaranteed payments, SE worksheet,
  capital accounts), `f1065-2026-rental-8825` (8825, §1231 through 4797), `f1120s-2026-two-shareholders-transfer`
  (per-share-per-day, AAA, distributions), `f1120s-2026-section-179-limited` (entity-level §179 limit and carryover).
- The K-1 round trip (`tests/test_returns_k1_delivery.py`): entity return computed and filed (mock transmitter, as
  `test_return_workflow.py`) → `deliver_k1s` → the owner's client holds a K-1 document with `_source` → the owner's 1040
  `populate_from_documents` fills `k1s[0]` with provenance (document id, box) → Schedule E line 32, Schedule SE line 2,
  Form 8995 QBI items, Form 4797 line 2 (box 10) and the §1250 worksheet line 5 (box 9c), Form 1116 (box 21 + K-3
  facts) equal the entity's K-1 lines; then the Q20 cases: a recomputed entity version supersedes the K-1 → the owner's
  `approved` 1040 fails `approve_release`/`transmit` on drift naming the K-1, the owner's `accepted` 1040 gets the
  amendment-candidate task; an asset register change (cost or §179) reopens a hash-bound entity return and no K-1 is
  re-delivered without the command; delivery refused before acceptance, by a staff user without the owner grant, and
  for an external owner it lands on the entity client with a furnishing record; redelivery is idempotent; `owners_filed`
  recorded at the owner's acceptance.
- ATS: no 1065/1120-S scenario is in docs/irs; the TY2026 business ATS scenarios (1065 schemas valid Nov 1, 2026) are
  owner material to download once the ETIN exists. Scenario 6 (1040 with a partnership K-1, Schedule E) is the
  1040-side check and is exercised by the round-trip fixture's shape (a passive partnership K-1 on a dependent's return).
- Coverage evidence: `f1065`, `f1120s` move `unsupported → manual-assisted` with `limits:` (every error code above) and
  `evidence:` (test files, golden ids), `f8825`, `f1125a`, `f4562`, `sch_d_1065`, `sch_d_1120s` added; `mef_1065`/`mef_1120s`
  stay `unsupported` until ATS; acceptance-map Q20 row filled with the test names.

## 4. Slices (each = model + population + rules YAML + engine + hand-worked tests + golden + coverage + acceptance map + engine version)

| # | Slice | Depends | Size |
|---|---|---|---|
| S-A | Pass-through core: `entity_model.py`, `pass_through.py` skeleton, `FORMS` dispatch in `store.py` (create/save/calculate/check_fields by form, `return_entity` kind, engine versions), `populate_from_books` from the close with ledger provenance and drift, Schedule L `tax_line` mapping (packs + overrides), M-1 generalised from `ledger/m1.py`, ownership table with checks, entity `TAX_FORMS`/BOXES (1099-INT/DIV/B, prior-year return), API `form` parameter, coverage ids | owner names entity types | 6-8 d |
| S-B | Form 1065: page 1 from the TB, Schedule K ordinary and guaranteed payments, `ws_se_earnings`, Analysis of Net Income, K-1 by fixed percentages (items E-N, J, K, L tax basis), M-2 partner capital, carryforwards per owner, roll-forward into `EntityPriorYear`, §754/§743(b), varying interests and special allocations as errors | S-A | 5-7 d |
| S-C | Form 1120-S: page 1 incl. officer compensation, Schedule K, K-1 per-share-per-day (items E-I), M-2 AAA/OAA/PTEP/AE&P with distribution ordering, eligibility checks, §1375 test and §1374 scope limit, reasonable-compensation warning | S-B (allocation core) | 5-7 d |
| S-D | Attachments and separately stated items: `form_4797.py` extraction (1040 tests pinned), Form 8825 with Schedule A, `sch_d_1065`/`sch_d_1120s` from `portfolio` and dispositions, `f4562` from the register, entity-level §179 limit and carryover, §199A statements, tax-exempt/nondeductible, investment income, `ws_k3_exception`, AMT warning, credits/foreign/other codes as errors | S-B (K lines) | 5-6 d |
| S-E | K-1 delivery into 1040 cases: `K1` model extension (§1.7), `documents.BOXES["K-1"]` + `REQUIRED`, generated K-1 document (fields, JSON bytes, vault), `deliver_k1s` with the authorization rule, supersession (`superseded` status; PG migration for the status check and `account_tax_lines`), audit, tasks, external-owner furnishing record, `owners_filed` system path, round-trip and Q20 tests | S-B; S-C for the 1120-S K-1 | 5-6 d |
| S-F | Balance sheet and reconciliations complete: Schedule L beginning column from the ledger or the prior-year return, full M-1 lines with the exemption flags (Schedule B Q4/Q11 rules), M-3 threshold error, Form 1125-A with inventories and the §448(c)/§263A tests, §163(j) exemption test | S-A (parallel with S-D) | 3-4 d |
| S-G | Scope limits and coverage: every error code under test (`tests/test_returns_pass_through_limits.py`), coverage entries with limits and evidence, acceptance-map Q19/Q20 wording, docs, engine version bumps, hand-off notes for T2-01 (sheet → schema map) and T1-07 (K-1 PDF) | all | 2-3 d |

Dependencies: S-A → S-B → S-C; S-D and S-F after S-B/S-A in parallel; S-E after S-B (and S-C for 1120-S K-1s); S-G last.
A season-aware order: S-A, S-B, S-E (partnership K-1s flow early), then S-C, S-D, S-F, S-G.

### Acceptance wording (Q20)

Q20 "Corrected K-1 or asset invalidates downstream": a Schedule K-1 is a document generated from a specific entity return
version (provenance: entity return id, version, package hash, K-1 hash) and filed into the owner's workspace only through
`deliver_k1s` under the authorization rule; the owner's 1040 takes every K-1 amount from that document with box-level
provenance. A corrected K-1 (a new entity version delivered again) supersedes the earlier document: an owner's return in
preparation or review fails every gate (`submit_for_review`, `approve`, `approve_release`, `transmit`, `mark_paper_filed`)
until the corrected K-1 is populated and each orphaned or differing value is decided by a person; a filed owner return
receives an amendment-candidate task naming the entity version. A changed asset register entry recomputes the entity
return, reopens it when hash-bound, and K-1s are never re-delivered implicitly. Σ K-1 == Schedule K per line on every
version. Tests: `tests/test_returns_k1_delivery.py` (round trip, supersession, gates, authorization, external owner,
idempotency, owners_filed), `tests/test_returns_pass_through_invariants.py`.

### Flags for the owner

1. Which entity types first: recommended order — calendar-year S corporations with ≤ 10 individual shareholders and no
   C history, then LLC/partnerships with fixed percentages and no §704(c)/special allocations; multi-tier owners (a
   partnership or trust as owner), foreign partners, §754 elections and S corporations with AE&P stay blocked in v1.
2. Fiscal-year entities: the model carries the period from day one; figures resolve by the year the period begins; the
   due date is computed from `period_end`. Decide whether the first release accepts non-calendar periods or blocks them
   (`entity_fiscal_year_unsupported`) until a fiscal-year fixture exists.
3. Special allocations, §704(c) and varying interests (§706(d)): blocked in v1 by design; say whether any pilot client
   needs them in the first season.
4. Schedules K-2/K-3: domestic filing exception only, with the partner notification as a recorded fact and date; any
   foreign activity or a K-3 request blocks. The 2026 K-2/K-3 instructions are not posted.
5. Draft K-1 delivery from an `approved` entity return (owners' 1040s prepared before the entity e-files): allow with
   the `final_k1 = False` block on the 1040, or final-only.
6. Automatic `owners_filed` recording from a firm-client owner's accepted return (system path in `record_tax_event`).
7. Chart mapping: each pack's accounts need a `schedule_l` line (owner/accounting reviewer); clients with custom accounts
   map them once (`account_tax_lines`).
8. Asset register: real property classes (27.5/39-year SL mid-month) and mid-quarter are prerequisites for 8825 and
   4562 from the register; until then those depreciation amounts are stated with provenance and a warning.
9. Figures to verify from primary sources as slices land: Rev. Proc. 2025-32 (§448(c) 2026 amount; §6698/§6699 for
   returns filed in 2027; §179 SUV limit), Rev. Proc. 2024-40 ($255), the 2026 Form 1065/1120-S instructions when posted
   (Schedule B thresholds, line maps, K-1 codes), Form 8825 Rev. Dec 2025 Schedule A line map.
10. E-file: add the 1065 and 1120 return types to the e-file application (the packet lists them), download the TY2026
    business ATS scenarios into docs/irs when the ETIN arrives; signature forms 8879-PE / 8879-CORP and signer authority
    (T2-03).
11. The tax-content owner in `coverage/coverage.yaml` is still null; nothing here can be `preparation-validated` without it.

### Risks

- 2026 instructions are not posted: line semantics and K-1 codes come from the 2025 editions and the 2026 draft forms;
  a code or threshold change forces a re-read before the first filing season release.
- No frozen close snapshot (A1-05): the ledger pin is `closed_through` + chain head + balances hash; a reopen after
  population is detected as drift, not prevented; the A1-05 snapshot should replace the pin without changing the model.
- Widening `K1` is additive (Optional/zero defaults) but the new blocking codes (unknown other-income/credit codes) can
  block existing hand-entered 1040 K-1s; those codes block only when a value is present.
- Cross-client writes under RLS: a staff session scoped to the entity cannot insert into the owner's documents — designed
  as a 403, but the API must check scope for both clients before any write and the delivery must be all-or-nothing per owner.
- Rounding and allocation identities are invariants, not IRS rules; the residual-to-largest-owner convention must be
  acceptable to the owner (alternatives: residual to the first owner, or cents on K-1s).
- Season timing: 1065/1120-S due March 15, 2027 and owners' 1040s need K-1s before then — S-A, S-B and S-E must land by
  early February 2027 or the first entities are prepared with manual K-1 entry.
- The entity return consumes payroll (officer compensation, W-2 wages for §199A) that the ledger holds only as tagged
  postings; until A1-level payroll reports populate, these are stated facts with provenance and tie checks.
- The 1040 side's basis (Form 7203), at-risk (Form 6198) and Form 8582 are not built: the K-1 carries the figures they
  need, and the 1040 keeps its existing warnings and limits meanwhile.
