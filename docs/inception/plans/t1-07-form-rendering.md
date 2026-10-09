# T1-07 plan: form rendering (official PDFs from per-year field maps; page checks against the snapshot)

Read-only study of the engine, the store, the API, the web shell and the IRS PDFs, 2026-10-09. Acceptance Q21
("Form rendering matches calculation", today `todo`: PDF output not built). Status per slice is kept in backlog.md
(T1-07). Nothing in the repo was changed by this study; the PDFs were downloaded into a scratch directory and read with
pypdf under `python -I`.

## Facts the plan rests on

- **Engine output** (`returns/individual.py` `Result.to_dict()`, `returns/sheet.py` `Sheets`): `forms` holds whole-dollar
  strings keyed by engine form key and line; `facts` holds the non-numeric entries a form needs (filing status, the
  taxpayer's and spouse's names, the `dependents` rows with their CTC/ODC flags, checkboxes `6d`, `12a_you`, `12b`,
  `12d_*`, `27c`, `schedule_d_not_required`, Schedule B `interest_payers` / `dividend_payers`, Form 8949 `part_*_box_*`
  rows, Schedule E `k1_<i>`, EIC `children`, the worksheets' `lines`); `notes`, `diagnostics`, `summary`, `carryforwards`,
  `coverage`, `pinned {kb_version, engine}`. Form keys in use: `f1040`, `sch_1`, `sch_1a`, `sch_2`, `sch_3`, `sch_3a`,
  `sch_a`, `sch_b`, `sch_c[i]` (1-based per business), `sch_d`, `f8949`, `sch_e[i]`, `sch_se[owner]`, `sch_8812`,
  `sch_eic`, `f8880`, `f8889[owner]`, `f8606[owner]`, `f1116[passive]`, `f4797`, `f8615`, `f6251`, `f8959`, `f8960`,
  `f2441`, `f8863`, `f8995`, `f8995a`, `f8582` (named only), `f1040x` (`<1040 line>.A/B/C` plus the named 1040-X lines,
  order in `facts.f1040x.lines`), and the worksheets `ws_qdcg`, `ws_sch_d_tax`, `ws_social_security`,
  `ws_capital_loss_carryover`, `ws_ira_deduction`, `ws_roth_contribution`, `ws_unrecaptured_1250` (no IRS fillable form:
  `coverage.FORM_IDS` maps them to None; `coverage.form_id` strips the `[instance]` suffix).
- **Stored versions** (`returns/store.py`): `tax_return_versions` seals `inputs`, `provenance` and `result` with the
  firm key (`Sealer`), keeps `summary` and `diagnostics` in clear, and is append-only (triggers). `package_hash` is the
  sha256 of {inputs, forms, summary, pinned, documents relied on, dispositions}; `approve` pins `approved_version` and
  `approved_hash`; `_version_reproducing` finds the sealed version that reproduces a hash (the 1040-X column-A idiom).
  `HASH_BOUND` statuses reopen on any change; `FROZEN` ones (transmitted, accepted, paper_filed, unknown, rejected,
  void) are never recomputed. `_g_paper_filed` requires the current package to be the approved and signed one.
- **Header facts the model lacks**: `IndividualReturn` has no address, phone, email, digital-asset answer, presidential
  election fund choice, IP PIN or third-party designee (`Person.occupation`, `dob`, `blind`, `ssn`, `can_be_claimed_as_dependent`
  exist); `Business` has no address (Schedule C line E); nothing holds the paid-preparer block (PTIN, firm name, EIN,
  address, phone) — `clients.facts` is free-form and the firm record is id/name/status. The 1040's own lines are complete.
- **API and web**: signed download links (`POST /api/links`: HMAC under the master key, 120 s, a path allow-list regex),
  the inline file route (PDF/PNG/JPEG by extension and byte signature, `Content-Disposition: inline`, `nosniff`,
  `Content-Security-Policy: sandbox; default-src 'none'`, `Cache-Control: private, no-store`), `reviewer_only`, `fresh`
  (step-up), `scope`; the catch-all `POST /api/returns/{rid}/{action}` is declared after the specific return routes.
  The web app downloads through `fetchDocumentBlob` (`screens/DownloadButton.tsx`) and frames files through
  `screens/returns/editor/DocumentViewer.tsx`; `ReturnStatusScreen.tsx` is cards; `packages/contracts` is regenerated
  from `openapi.json` with a CI drift gate (`scripts/export_openapi.py --check`).
- **Evidence storage**: `security/vault.py` `Vault.put(data, owner)` returns `blob:cas/<ab>/<hmac(owner, bytes)>`, sealed
  AES-GCM bound to the locator; `read(loc, sha256=)` authenticates. `evidence.records.integrity` sweeps every blob key
  and reports any object not referenced by `documents` / `document_versions` as `unreferenced`: a new table that stores
  bytes in the vault must join that referenced set. Firm offboarding removes `firms/<firm>/` wholesale.
- **pypdf** 6.19.0 is installed (`pypdf>=5` in pyproject; no pikepdf, pdfplumber, reportlab or pdfium). Measured on the
  2025 Form 1040 PDF: `PdfWriter(clone_from=reader)` + `update_page_form_field_values(auto_regenerate=False)` fills text
  and checkboxes and reads them back exactly (`get_fields()`), 0.16 s, byte-identical on two runs; with `flatten=True`
  the values are burned into the page content (`extract_text()` finds them) but the 229 fields and 128 widgets remain,
  so a real flatten adds `remove_annotations(subtypes="/Widget")` and drops `/AcroForm` (measured: 0 widgets, text kept,
  still deterministic); `PdfWriter.append` of two filled copies of one form collapses same-named fields in the
  document-level AcroForm (only the last copy's value survives in `get_fields()`; each page's widgets keep their own
  `/V`), so read-back is per page, not per document; a raw-content-stream Helvetica page (standard-14 font, nothing
  embedded) merges as a watermark overlay or appends as a statement page and survives text extraction. There is no
  pure-Python rasterizer.
- **IRS PDFs** (34 current finals and 25 2026 drafts downloaded from `irs.gov/pub/irs-pdf/<product>.pdf` and
  `irs.gov/pub/irs-dft/<product>--dft.pdf`): every 2025 final is an XFA + AcroForm hybrid (`/XFA` array of 22 parts, no
  `NeedAppearances`, no JavaScript, no calculation order); AcroForm field names follow `topmostSubform[0].Page1[0].f1_01[0]`
  (text) and `topmostSubform[0].Page1[0].c1_1[0]` (checkbox, export `/1`; a mutually exclusive group such as the filing
  status boxes has distinct exports `/1`..`/5`), grouped under reading-order subforms (`Address_ReadOrder[0]`,
  `Table_Dependents[0].Row5[0].Dependent1[0]`); no `MaxLen`; the SSN fields carry the comb flag without `MaxLen` (so plain
  text). The 2026 drafts carry no XFA, a cover page ("The draft you are looking for begins on the next page"), and
  "DRAFT — DO NOT FILE" inside every page's content. Schedule 3-A's draft has no fields at all and no 2025 final exists.
- **Draft status at IRS.gov/DraftForms on 2026-10-09** (revision 2026 unless noted; posted date): Form 1040 and 1040-SR
  09/17; Schedules 1 06/08, 1-A 09/04, 2 06/04, 3 06/08, 3-A 08/20, A 06/08, B 05/20, C 05/28, D 05/15, E 05/19, SE 05/21,
  8812 05/19, EIC 06/08; Form 1040-X (Rev. December 2026) 09/17; 8880 05/14, 8889 05/07, 8606 07/16, 1116 07/07 (its
  Schedule B, Dec 2026, 06/11), 4797 10/07, 8615 06/30, 8962 05/11, 2210 06/04, 8582 09/18, 8949 05/26, 6251 05/28,
  8959 05/29, 8960 06/01, 2441 05/06, 8863 05/26, 8995 and 8995-A 05/29, 8879 (Dec 2026) 08/07, 8453 06/04. Every form in
  scope is posted as a 2026 draft; most instructions are still the 2025 edition. The page says "Do not file draft forms".
- **Field-name drift, 2025 final → 2026 draft** (leaf names shared / only in the final / only in the draft): Form 1040
  155 / 44 / 52; Schedule 1 72 / 1 / 1; Schedule C 59 / 46 / 50; Schedule D 55 / 0 / 0; 8889 27 / 0 / 0; 8606 27 / 18 / 20
  (zero padding changed, `f1_01` → `f1_1`); 4797 63 / 119 / 125 (a table renumbered); 1040-X 35 / 131 / 159. Names are
  not stable across revisions; a map is only valid for the PDF it was built against.
- **Attachment sequence numbers** (read from the 2025 finals' headers): Schedule 1 01, 1-A 1A, 2 02, 3 03, Form 2210 06,
  Schedule A 07, B 08, C 09, D 12, Form 8949 12A, Schedule E 13, SE 17, Forms 1116 19, 2441 21, 4797 27, 6251 32, 8615 33,
  Schedule EIC 43, 8812 47, Forms 8606 48, 8863 50, 8889 52, 8880 54, 8995 55, 8995-A 55A, 8959 71, 8960 72, 8962 73,
  8582 858. Forms 8453 and 8879 have none (not attachments; T2-03).

## Decisions

1. **pypdf alone.** It fills, reads back, generates appearances, flattens (with the explicit widget removal), merges,
   overlays and writes deterministic bytes; pikepdf (C++ qpdf) and pdfplumber add binaries for nothing this ticket
   needs. XFA is dropped from every output (the AcroForm is what viewers then render; the drafts have none anyway).
2. **The IRS PDFs are vendored** under `forms/us-fed/<year>/<product>-<revision>.pdf`, public-domain government works,
   pinned by sha256 in each map and copied into the image (Dockerfile `COPY forms/`): rendering must be reproducible
   offline and a changed PDF must fail loudly, not be re-downloaded silently.
3. **One map per (form, tax year, PDF revision)**, keyed by engine line keys, with the PDF's sha256, URL, revision and
   download date; a map whose PDF hash does not match refuses to render. Reason: the measured drift.
4. **The renderer never computes.** Its input is an opened stored version (`inputs`, `result`, `provenance`) plus the
   workflow facts; `returns/render/` imports nothing from `individual.py` or `amendment.py` (a test asserts it). A
   filed return renders from the version `_version_reproducing(approved_version, approved_hash)` names.
5. **Rendered PDFs are return artifacts, not documents.** Their bytes go to the vault (`Vault.put(data,
   owner="return:<rid>")`, sealed, content-addressed per return) and a new append-only `return_artifacts` table binds
   each to (return, version, package hash, copy kind, map set, renderer version, sha256, page-check result). A
   `documents` row would make the rendering a taxpayer document: `unaccounted_documents`, population, retention classes
   and the review inbox all treat `documents` as evidence received, which an output is not.
6. **Three copies**, all flattened (field-name collisions on merge; tamper evidence; BL.md §"Flattening and
   Cryptographic Hashing"): *preparer* (full identifiers, worksheets and statements, "DRAFT — not for filing" banner
   unless the version is the approved package), *client* (identifiers masked by default, no worksheets, same banner
   rule, audience `client`), *filing* (unmasked, no banner, only from the approved and signed package and only from a
   map pinned to a final PDF: the IRS says drafts are not filed). The banner is our overlay; the IRS draft watermark in
   the drafts' content stays whatever we do.
7. **Page check** = (a) before flattening, every widget on every page is read back by walking `/Parent` to its full name
   and compared with the formatted snapshot value, and every non-empty widget must correspond to a mapped key; (b)
   after flattening, each page's extracted text must contain every value written to it; (c) every engine key of a
   rendered form is mapped or listed `no_box` with a reason, else diagnostic `render_unmapped_line` (error: a filing or
   client copy is refused; a preparer copy carries the diagnostic in its banner). The result is stored with the artifact.
8. **Visual regression is optional and not pure Python.** `pypdfium2` (a wheel bundling PDFium, no system package) under
   a dev extra `render-visual`, used by an opt-in test that rasterizes the golden package and compares against committed
   PNGs with a tolerance; never in the default CI (font antialiasing differs by platform). Without it the check is the
   owner's one-time eyeballing, recorded as evidence.
9. **Permissions**: any firm CPA-role user renders preparer copies (a working paper, the same scope as reading `result`);
   a reviewer renders client and filing copies (they leave the firm or go to the IRS); no step-up: rendering decides
   nothing, the decisions it depends on (approve, sign, release, paper filing) already require it. Client principals see
   only `audience: client` artifacts of their own returns.
10. **Statements and worksheets are generated pages** (pure pypdf text pages, Helvetica, WinAnsi): continuation statements
    when a list exceeds a form's rows and the instructions call for a statement, worksheets in the preparer copy, and a
    "not on the form" disclosure for `no_box` keys that carry a value. Additional copies of the official form are used
    where the instructions say so (Schedule E over three properties or four entities, Form 8949 pages, Form 4797 Part
    III over four properties) rather than a statement.

## Architecture: `src/agentledger/returns/render/`

| File | Role |
|---|---|
| `maps.py` | Load and validate `forms/us-fed/<year>/<form>.map.yaml` (pydantic); `FieldMap.for_form(key, year)`; the PDF hash check; the per-form set of mapped keys and `no_box` keys |
| `values.py` | Formatting from the snapshot: `amount` (whole dollars, thousands separators, no cents or `$`; `negative: parentheses|minus` per line, default parentheses where the form prints "(loss)", else minus; `zero: blank|"-0-"` per line, default blank, `-0-` where the form says so), `ssn`/`ein`/`itin` (dashes; masking `XXX-XX-1234` and `XX-XXX1234` for the client copy), `date`, `text` (WinAnsi only; anything else blocks with `render_unencodable_text`), `checkbox` (export state), `select_one` (a group's export by value), `pct`/`ratio` (Form 1116 lines 3f/19 four places, 8606 line 10 three places) |
| `sources.py` | Resolves a map entry's `source` against the opened version: `line:<form>.<line>`, `fact:<form>.<key>`, `input:<path>` (`facts.get_path`), `header:<name>` (slice S1), `instance:<n>` for multi-instance forms; lists (`dependents`, `interest_payers`, 8949 rows, Schedule E properties/entities, 4797 rows and columns, 1116 columns) with the form's row count and overflow policy |
| `fill.py` | pypdf: open the pinned PDF, drop the cover pages the map names, delete `/XFA`, set values, generate appearances, run the pre-flatten read-back, then flatten (merge appearances, remove widgets, drop `/AcroForm`) |
| `pages.py` | Generated pages: statements, worksheets, disclosures, the banner overlay, a cover sheet (client copy: firm name, client, year, version, "prepared on" = the version's `created_at`) |
| `assemble.py` | Package order (Form 1040 pages, then forms by attachment sequence with instances in input order, then statements, then worksheets), merge, deterministic metadata (Title, Producer `AgentLedger render <RENDER_VERSION>`, CreationDate/ModDate = the version's `created_at`, no random `/ID`), `compress_identical_objects`, the post-flatten text check |
| `check.py` | `PageCheck` dataclass: per form and page the fields compared, mismatches, unexpected values, unmapped keys, text misses; `ok` only when all are empty |
| `artifacts.py` | `return_artifacts` table (SQLite schema; PG migration `0009_return_artifacts.sql`, RLS through the return's client like `return_carryforwards`, no update/delete triggers), `Artifacts.render(rs, rid, copy, actor, role, version=None)`, `list`, `read` (vault read with sha256), audit `return.rendered`; registers its locators with `evidence.records.integrity` (a `referenced_locators` hook) |
| `__init__.py` | `RENDER_VERSION`, `render(version, copy, header, firm) -> Package` (bytes, pages, check), `COPIES` |
| `scripts/forms_fieldmap.py` | Tooling (section "Data acquisition") |
| `forms/us-fed/<year>/` | `<form>.map.yaml` + `pdf/<product>-<revision>.pdf` |

Map file (YAML), per form: `form` (engine key), `year`, `revision` ("2026 draft posted 2026-09-17" / "2026 final"),
`source` {url, sha256, downloaded, product, cover_pages, pages, attachment_sequence}, `status: draft|final`,
`header` {field name → source} (names, SSNs, address lines, occupation, the form's own checkboxes), `lines` {engine
line → {field, format, negative?, zero?, checkbox?}}, `lists` {list → {rows, overflow: statement|additional_form,
more_checkbox?, columns {column → field pattern per row}}}, `no_box` {engine key → reason}, `notes`. Multi-instance
forms carry `instance_header` (Schedule C: the business's name/EIN/code/method and the owner's name and SSN; 8889, 8606,
SE: the owner). Form 1040-X maps `<line>.A/B/C` to the three columns and the named lines to lines 16-23; the 1040-X's
Part I dependents and Part III explanation come from `facts.f1040x`.

Rendering a version: resolve the forms present in `result.forms` (instances expanded), pick each form's map for the tax
year (refuse when none, or when the map is `draft` and the copy is `filing`), fill, check, assemble, store. A filed or
hash-bound return always renders the pinned version; a `preparing` return renders its latest computed version.

## Data acquisition and map building

- **Which PDFs**: the 2026 drafts for everything (all posted; 2025 finals would be throwaway maps given the drift),
  re-pinned to the 2026 finals as they post (December–January; the tooling diff shows what moved). Form 1040-X uses the
  Rev. December 2026 draft. Schedule 3-A has no fillable PDF yet: its values go on a generated statement until one posts
  (`sch_3a` coverage `limits:` says so). Finals of 2025 are not vendored.
- **Order by go-live value** (one map each, hand-written): f1040 → Schedules 1, 2, 3 → Schedule B, D, 8949 → Schedule C,
  SE → Schedule A → Schedule 8812, EIC → Schedule E → 8889, 8606, 8880 → 1116, 4797, 8615 → 1040-X → 6251, 8959, 8960,
  2441, 8863, 8995, 8995-A, Schedule 1-A → 8962 (after T1-01 S6), 2210 (S8), 8582 (S5) → 3-A when fillable.
- **`scripts/forms_fieldmap.py`** (stdlib + pypdf + yaml, tested through the `_script` loader like
  `scripts/export_openapi.py`): `fetch <product> [--draft]` downloads into `forms/us-fed/<year>/pdf/`, records url,
  sha256, date and detected revision into the map's `source`; `fields <pdf>` lists every widget per page with type,
  export states, flags, rect and the nearest label text (pypdf's `extract_text(visitor_text=...)` gives positions), which
  is what a human needs to place engine keys; `scaffold` writes a map skeleton with every field and its guessed label and
  `source: null`; `preview` fills every field with its own short name and the page number into a labelled proof PDF for
  side-by-side placement; `check <map>` verifies the hash, that every named field exists with the declared type and
  export state, and that lists' row patterns resolve; `diff <old.pdf> <new.pdf>` reports names added/removed and proposes
  carry-forward by label match (the measured drift makes this the recurring cost per revision, about 1–3 h per form).
- **Map validation** (`tests/test_render_maps.py`): for every form with a map, the engine's key set — a static scan of
  `set("<form>", "<line>"` literals in `individual.py` / `amendment.py` plus the dynamic union of keys produced by every
  golden scenario and every hand-worked fixture return — is mapped or `no_box` with a reason; `no_box` entries that name
  a field that exists fail; unknown fields fail; a final-status map's PDF contains no "DRAFT — DO NOT FILE" text; the
  attachment sequence in the map equals the one read from the PDF header.

## Package assembly, storage, API and web

- **Evidence**: `return_artifacts` row per rendering (id, return_id, version, package_hash, copy, audience, locator,
  sha256, size, pages, maps JSON [{form, year, revision, sha256}], render_version, check JSON with counts only (the
  field-level report is sealed like a result), created_at, created_by). The integrity sweep counts these locators as
  referenced; artifacts are never deleted individually (they are part of the return record, kept as long as the sealed
  versions; the firm's offboarding removes them with everything else); a legal hold on the client covers them implicitly.
- **Workflow tie**: `mark_paper_filed` gains a guard: a filing copy whose `package_hash` equals `approved_hash` exists
  (what was mailed is on record). Transmission (T2-01/T2-02) will produce the MeF XML from the same version; the filing
  copy is the human-readable record of the same hash. Form 8879/8453 are T2-03: `assemble.py` leaves a `signature_forms`
  hook (prepended to the client copy when T2-03 supplies them) and nothing else.
- **API** (declared before the catch-all `POST /api/returns/{rid}/{action}`, FastAPI matches in declaration order):
  `POST /api/returns/{rid}/render` body `{copy: preparer|client|filing, version?}` → `RenderResult` (artifact row +
  check summary; 409 with the reasons when refused: draft map for a filing copy, unmapped keys, not approved, missing
  header facts); `GET /api/returns/{rid}/artifacts` (clients see `audience: client` only); `GET
  /api/returns/{rid}/artifacts/{aid}/file?dl=&inline=1` served exactly like `doc_file` (signed `dl` or session, the same
  inline allow-list and sandbox headers, `VaultIntegrityError` → 409 with an audit record); the `make_link` regex gains
  the artifact path. Pydantic models in `api/schemas.py` (`RenderRequest`, `ReturnArtifact`, `RenderResult`), the
  OpenAPI snapshot and `packages/contracts` regenerated. CLI: `agentledger return render <rid> --copy <kind> --out
  <path>` for operators and the owner's eyeballing.
- **Web** (F-10 slice 2 follow-up, then slice 4): a "Return package" card on `/returns/:rid` listing artifacts (copy,
  version, pages, check ok, rendered by/at) with Render (copy chooser; reviewer actions disabled with the API's reason
  as in `actions.tsx`), Download (`fetchDocumentBlob` generalised to a path) and Open (the sandboxed `DocumentViewer`
  generalised to a file path); the Frozen panel offers the filing copy of a filed return. Client portal: the client
  copy appears with the return outcome once its audience is `client`. States contract and axe cover the card; an e2e
  renders a seeded return and asserts the inline response (`inline`, `application/pdf`, `sandbox`) and the `%PDF-`
  signature.

## Tests

- `tests/test_render_maps.py`: completeness per form (above); hash pinning; field existence and types; overflow
  declarations resolve against the PDF's row count.
- `tests/test_render_pages.py`: fill → read-back equality for every mapped line of every golden return
  (`golden/scenarios.yaml` `f1040-2026-*`, `f1040x-2026-wage-correction`) and of one return per hand-worked test module
  (the Rivera household and the per-form fixtures), through the stored version (compute once with `Returns`, render from
  `_version`); post-flatten text containment per page; no unexpected values; multi-instance forms (two Schedules C, both
  spouses' 8889 and 8606) each read back their own values per page; the 1040-X columns.
- `tests/test_render_robustness.py`: five dependents → four rows, the "more than four" box and a statement; fifteen
  Schedule B payers; two hundred 8949 rows → pages; four Schedule E properties → a second Schedule E; five Form 4797 Part
  III properties → a second form; negatives in parentheses and with a minus per the map; a zero on a `-0-` line and a
  blank elsewhere; optional forms absent from `result.forms` are absent from the package; a WinAnsi-unencodable name
  blocks; a draft map refuses a filing copy; a client copy masks every identifier (asserted on extracted text: no full SSN
  anywhere); the banner appears on an unapproved version's preparer copy and not on the filing copy.
- `tests/test_render_artifacts.py`: the artifact is bound to the version and package hash, the locator reads back with
  its sha256, a swapped object is refused (409), the integrity sweep reports no `unreferenced` object after a render,
  RLS on PostgreSQL (another client's session sees nothing), `mark_paper_filed` refused without a filing copy of the
  approved hash, the API permissions (staff: preparer copy only; client: list and download of client artifacts only;
  signed link expiry), audit records.
- `tests/test_render_determinism.py`: the same version rendered twice (and again after a process restart, through the
  CLI) is byte-identical; the metadata carries no clock value; a changed `RENDER_VERSION` or map revision changes the
  bytes and is recorded on the artifact.
- `tests/test_render_tooling.py`: `fields`, `scaffold`, `check` and `diff` against the vendored 1040 PDF and a fixture of
  the 2025 final kept under `tests/fixtures/forms/` (the diff reports the measured 44/52 drift).
- Owner evidence: the owner renders `f1040-2026-self-employed-qbi` (Schedule C, SE, 8995) and `f1040x-2026-wage-correction`
  through the CLI, compares every page with the figures in the scenario by eye once, and the result is recorded in
  `coverage/coverage.yaml` under the forms' `evidence:` ("rendering eyeballed by <owner> on <date>, version <n>").
- Optional: `tests/test_render_visual.py` (skipped without `pypdfium2`), PNG baselines under `tests/fixtures/render/`.

## Slices (each: code + maps + tests + acceptance map + coverage evidence; dependency order)

| # | Slice | Depends | Size |
|---|---|---|---|
| S0 | Foundation: `returns/render/` (maps, values, sources, fill, pages, assemble, check), `RENDER_VERSION`, `scripts/forms_fieldmap.py`, vendored 2026 drafts of the first set with hashes, the Form 1040 map (header, every line, dependents, checkboxes), statement pages, banner, determinism; `forms/` in the Dockerfile and `returns/render` in the mypy files | — | 4–5 d |
| S1 | Header facts: `IndividualReturn.header` (address lines, apt, city/state/ZIP or foreign address, phone, email, digital-asset answer, presidential fund per person, IP PINs, third-party designee), `Business.address`, a firm filing identity (firm name, EIN, address, phone; each reviewer's PTIN) in the platform firm record; `_check_fields` and the input gate; missing filing-copy facts block with stable codes (`render_header_missing`, `render_preparer_identity_missing`); never guessed (a blank digital-asset answer is a blocker, not "No") | S0 | 2 d |
| S2 | Core maps: Schedules 1, 2, 3, A, B, D, C, SE, 8812, EIC, Form 8949, Schedule 1-A; multi-instance Schedule C and SE; the overflow policies of each | S0 | 4–5 d |
| S3 | Storage, API, workflow, CLI: `return_artifacts` (+ PG migration 0009, RLS, sweep registration), `Artifacts`, the three routes and the link regex, schemas and contracts, audit, the `mark_paper_filed` guard, `agentledger return render` | S0, S1 | 3 d |
| S4 | Remaining maps: 8889, 8606, 8880 (per owner), 1116 (columns A–C, Schedule B of 1116 as a statement until mapped), 4797 (Part I rows, Part III columns A–D and overflow), 8615, 6251, 8959, 8960, 2441, 8863, 8995, 8995-A, 1040-X (Rev. Dec 2026 draft) | S2 | 4–5 d |
| S5 | Web: the Return package card, render/download/open, the client portal artifact, states contract, axe, e2e | S3, F-10 slice 2 | 2–3 d |
| S6 | Hardening: robustness and determinism suites complete, a 40-page package under 10 s, the owner's eyeballed golden recorded, optional `pypdfium2` snapshot test, docs (a rendering section in docs/WEB.md and the runbook for re-pinning a form) | S4, S5 | 2 d |
| S7 | Follow the IRS (recurring): re-pin each form to its 2026 final as it posts (`fetch`, `diff`, carry forward, `check`, re-run S2/S4 tests), then 8962 / 2210 / 8582 maps when T1-01 S6 / S8 / S5 land, Schedule 3-A when fillable | finals posted | 0.5 d per form |

## Acceptance (Q21: form rendering matches calculation)

Q21 is `pass` when, for every golden return and every hand-worked fixture return, rendering the stored version produces a
package in which every mapped field reads back equal to the snapshot's formatted value (per-page widgets before
flattening; page text after), no engine key of a rendered form is unmapped (each is mapped or `no_box` with a reason),
no value comes from anything but the stored version's `result` and `inputs` (the renderer imports no engine code), the
artifact is bound to the version and package hash and reads back under its sha256, the same version renders
byte-identical, and the owner has eyeballed one golden rendering with the result recorded in `coverage.yaml`. Until
every in-scope form's map is pinned to a 2026 *final* PDF the state is `partial`, the acceptance map naming the drafts
each map was built against. Q19 carries over: every refusal is an error diagnostic with a stable code
(`render_unmapped_line`, `render_map_missing`, `render_map_hash_mismatch`, `render_draft_not_for_filing`,
`render_unencodable_text`, `render_header_missing`, `render_preparer_identity_missing`, `render_check_failed`), and a
workflow test proves `mark_paper_filed` is refused without a filing copy of the approved hash.

## Flags for the owner

1. **Drafts now, finals later.** Maps are built on the posted 2026 drafts (all in scope are posted; 2025 finals would
   be throwaway given the measured drift) and re-pinned when finals post; until then only preparer and client copies
   render, both carrying the IRS's own "DRAFT — DO NOT FILE" and our banner. Confirm that client review copies may show
   the IRS draft watermark during the season's preparation.
2. **Which forms first**: the order above is a proposal (1040 → Schedules 1/2/3 → B/D/8949 → C/SE → A → 8812/EIC → E →
   HSA/IRA/saver → 1116/4797/8615 → 1040-X → the rest). Say otherwise.
3. **Client copy policy**: mask SSNs/EINs/account numbers to the last four (default on), include worksheets (default
   off), include a cover sheet (default on). §6107(a) requires a complete copy of the return for the taxpayer: confirm
   that a masked copy is the firm's practice, else the client copy is unmasked.
4. **House style**: negatives in parentheses where the form prints "(loss)" and a minus elsewhere; thousands separators,
   no cents, zero lines blank except the "-0-" lines — confirm or name the style every map then follows.
5. **Header facts and preparer identity** (S1) need an owner: who enters the taxpayer's address, the digital-asset
   answer, the presidential fund choice and IP PINs (the organizer, T1-03, is the natural home), and the firm's EIN,
   address, phone and each reviewer's PTIN (also needed by T2-01 MeF). EFIN belongs to T2-02.
6. **Visual snapshots**: accept `pypdfium2` as a dev-only optional dependency, or rely on the one-time eyeballing only.
7. **Schedule 3-A**: no fillable PDF exists; its values print on a statement page until one posts (coverage limit).

## Risks

- **XFA-only forms**: none among the 34 finals (all have AcroForm fields); `fields` refuses a PDF with `/XFA` and no
  AcroForm fields, so such a form would be a known gap, never a silent blank.
- **Field-name drift between revisions** (measured up to 131/159 names on the 1040-X): every map is pinned to a hash;
  `diff` with label matching cuts re-mapping to hours; the first finals in December are the first real exercise.
- **Draft cover pages and watermarks**: the map's `cover_pages` drops the IRS cover; page indices in maps are the
  form's own; the final will have no cover, so `check` verifies page count and header text per revision.
- **Fonts and encoding**: the forms' `/DA` is Helvetica auto-size; pypdf's generated appearances pick a size from the
  field rectangle; long payer names may overflow or shrink — `preview` and the eyeballing catch it; text outside WinAnsi
  blocks rather than renders as boxes. Flattening loses the tagged form-field accessibility of the IRS PDF (acceptable
  for printed copies; the tagged page structure of the IRS content remains).
- **pypdf semantics**: `flatten=True` alone keeps the widgets (measured) — the explicit removal is part of `fill.py` and
  tested; document-level `get_fields()` is unreliable after merging instances — read-back is per page; pypdf warns that
  fontTools is absent when reading the IRS fonts (harmless; `fontTools` could join the dev extras to silence it).
- **Performance and size**: 0.16–0.18 s and ~470 KB per filled 1040; a typical package renders in 2–5 s synchronously
  inside the 30 s request budget; a 200-row Form 8949 package is the S6 performance test; `compress_identical_objects`
  and content-stream compression are measured there, and a workflow activity is the fallback if a package exceeds the
  budget.
- **Determinism**: pypdf preserved the IRS file's Info dictionary and produced identical bytes twice; `assemble.py` sets
  the metadata from the version, never from the clock, and the determinism test runs across processes so an accidental
  timestamp or random `/ID` fails CI.
- **Scope creep**: the organizer's header facts (S1) are the one model change here; Form 8879/8453 (T2-03), MeF (T2-01),
  state forms (T1-05) and business returns (T1-04) reuse `returns/render/` with their own maps and are out of scope.
