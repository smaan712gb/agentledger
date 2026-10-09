# IRS Assurance Testing System (ATS) scenarios: fixtures, harness and readiness

Backlog T2-04, first half: the IRS's TY2026 Form 1040 MeF ATS scenarios (Publication 1436; the MeF guides are Publications
4164 and 4163) turned into independent fixtures for the 1040 engine, with the engine's outcome on each recorded and pinned
by a test. The second half, transmitting the scenarios to ATS and receiving the acknowledgments, is T2-02 (with T2-01 for
the MeF XML and T2-03 for the signatures).

The names, SSNs, EINs and addresses in the scenarios and in the fixtures are IRS test identities, invented by the IRS for
ATS. They are not real people. IRS publications are U.S. government works, so the fixtures derived from them are
committed; the PDFs themselves stay in the owner's untracked `docs/irs/ats-ty2026` download.

## 1. What the harness does

| Piece | What it is |
|---|---|
| `docs/irs/ats-ty2026/*.pdf` | The owner's download (untracked): ten TY2026 scenario PDFs (1-7, 12-14) and a README |
| `scripts/ats_fixtures.py` | Reads each PDF with pypdf and writes `tests/fixtures/ats/scenario-NN.json`; `--record`, `--check`, `--dump N`, `--table` |
| `tests/fixtures/ats/scenario-NN.json` | One fixture per scenario (committed) |
| `src/agentledger/returns/ats.py` | Runs a fixture through `compute_individual` with the project's rules and compares, line by line |
| `tests/test_ats_scenarios.py` | Pins each scenario's recorded outcome; runs from the JSON alone (no PDFs, no store) |

A fixture holds:

- `return`: the facts the scenario states, in the engine's `IndividualReturn` shape where the model has a field (W-2 boxes,
  1099-R boxes, Schedule A and Schedule C entries, the cover page's dates of birth and elections), with `provenance` naming
  the page and box or line of every value;
- `unmodelled`: every stated fact the model cannot hold, each with its effect ("changes the return", "none: the engine
  already assumes it", or filing data that belongs to the MeF return rather than the computation);
- `assumptions`: values the model requires that the scenario does not state (a filing status nobody checked, a day of
  birth where only the year is printed, a sale date on a Schedule D total), with the evidence and, where they could matter,
  `alternatives` that the harness re-runs to prove they decide no scored line;
- `expected`: every amount typed on a numbered line of a form, keyed by the scenario form and line and by the engine's key
  (`engine`), which differs where the engine keeps the value elsewhere (Schedule D columns are part of the engine's line
  key, `1a` column d is `1ad`; Schedule 8812 line 4 is a fact; Schedule 1-A line 16a is reported by the engine as line 17);
- `ambiguities`: whatever the text does not settle, including the scenario's own arithmetic where it fails;
- `gap_causes`: the reviewed explanation of each difference (worked by hand, below);
- `source`: file name, SHA-256, page count, pages used, extraction date and pypdf version;
- `recorded`: the outcome when last recorded (`--record`), which the test compares with today's.

`returns/ats.py` gives each expected line one status:

| Status | Meaning |
|---|---|
| matched | the engine computes the printed amount (the count notes zero lines the engine leaves out, zero by `Result.line`) |
| differs | the engine computes the line and gets another amount; both are recorded, with the reviewed cause |
| blocked | the engine cannot produce the line: the form is not modelled (with its coverage status), the line is one the engine never computes, or the return raises blocking diagnostics (their codes are recorded) |
| echo | the printed amount is an input the fixture feeds the engine; compared only to prove the mapping, never counted as a match (`input_unshown` when the engine takes it but shows no such form, as Schedule A under a larger standard deduction) |
| ambiguous | the scenario's own figures contradict this line; reported, not scored |

Nothing is corrected in either direction. The ATS scenarios exist to test transmission, and their figures are not
necessarily tax-correct (several are not, section 5): a difference is a finding for the tax-content owner, not proof of an
engine error. The test fails when a recorded match regresses, when a recorded gap closes or changes (re-record the fixture
deliberately), when an input no longer maps onto the engine, when a line was never recorded, or when an assumption's
alternative starts to change a scored line.

## 2. Regenerating the fixtures

When the owner downloads new or revised scenarios (the IRS marks them as drafts and revises them; re-download before testing):

1. Put the PDFs in `docs/irs/ats-ty2026/` (keep the folder untracked; never commit the PDFs). The scenario number is taken
   from `scenario-N` in the file name.
2. `python -I scripts/ats_fixtures.py` re-extracts every PDF found and rewrites the fixtures, keeping each one's recorded
   outcome and, when nothing else changed, its extraction date.
3. Review `git diff tests/fixtures/ats` (a new SHA-256 means a new PDF; look at changed facts and lines first).
   `python -I scripts/ats_fixtures.py --dump N` prints what the reader sees on every page of scenario N.
4. A new scenario needs a `scenario_N` function in the script (the facts it reads from the cover, the documents and the
   tables) and an entry in `SCENARIOS`. A page whose layout the reader does not know is classified `unknown` and read as
   nothing; add its title to `PAGE_TITLES`.
5. `python -I scripts/ats_fixtures.py --record` runs the engine and records each outcome. Explain every new difference in
   `GAP_CAUSES` (the test requires a cause for each differing line), then record again.
6. `pytest -q tests/test_ats_scenarios.py`; `python -I scripts/ats_fixtures.py --check` confirms the PDFs reproduce the
   committed fixtures (the test does the same where the PDFs are present and skips where they are not, as in CI).
7. `python -I scripts/ats_fixtures.py --table` prints the table of section 4 for this document.

After an engine or rule change, `--record` works without the PDFs: it re-records the committed fixtures' outcomes. Do it
only after reading the test's report of what changed.

## 3. How the PDFs are read

pypdf parses the content streams; the script tracks the text and graphics matrices (including form XObjects and the text
state that q/Q save and restore) to place every glyph, and decodes the fonts itself:

- Scenarios 1-7 embed Arial subsets whose ToUnicode maps each glyph id to itself, so ordinary extraction yields control
  characters. These subsets keep Arial's glyph order, where a printable character's glyph id is its code minus 29; the
  script detects the identity map and repairs it (6,259 glyphs in scenarios 1-7). The text then reads correctly: names,
  SSNs and amounts cross-check between the cover, the forms and the W-2s.
- Typed entries are told from the forms' printed text by font (the IRS forms print in Helvetica Neue, Franklin Gothic and
  similar; the scenarios type in Arial, Helvetica LT Bold or Myriad), and check marks are their own words: the printed option
  just right of a check mark is the one chosen.
- An amount belongs to the rightmost printed line label to its left on its baseline (the box label at the right margin, or
  the label of a sub-column such as 25a), never a number inside a sentence or a reference such as "go to line 1b"; a line
  whose text runs over several printed lines takes its amounts on the last of them (Schedule D lines 1a and 8a). Several
  amounts on one line take their columns from the printed column letters ("(d)", "(e)").
- Boxes of information returns (W-2, W-2G, 1099-R) are read by the box whose number is nearest above and in the same column.
- Tables (the Form 1040 dependents, Schedule EIC, Form 2441's providers and persons, Schedule E Part II, Form 8283, Form
  8888, Form 8862) are read explicitly by the scenario that needs them; the landscape and table pages of Forms 3800, 4136,
  7207 and 7220 are not read as lines (they are unmodelled forms; their pages are listed in the fixture).
- The forms' own arithmetic is checked (sums such as Form 1040 lines 9, 11a, 14, 22, 24a, 32a, 33; cross-references such as
  Schedule 3-A line 1a = Form 1040 line 32a). A failure is recorded as an ambiguity, and a line that only failing identities
  support (or that repeats a line printing another amount, or is figured from such a line) is marked ambiguous, not scored.

What the PDFs state clearly: every typed amount on the numbered lines of the complete returns (scenarios 12-14), the W-2,
W-2G and 1099-R boxes, the cover pages' facts, check marks. What they do not: scenarios 1-7 are input documents, not
completed returns (Form 1040's computed lines are blank; only a handful of computed lines are printed, such as Schedule 1
line 26 = 0 in scenario 2), and none of them checks a filing status; scenario 14 prints years of birth, not dates; a
glyph in scenario 6's W-2G box 4 that no font names; two unnumbered entries (the count of Schedules A on Forms 1062 and
3800), recorded as not read.

## 4. Results by scenario

Engine as of commit 736a3c9 (ENGINE_VERSION 1040-2026.4) and the rules in `rules/`, recorded 2026-10-09.

| Scenario | Taxpayer | Forms in the scenario | Matched | Differ | Blocked | Inputs echoed (not shown) | Ambiguous | Blocked lines by reason | Engine errors on the return | Unmodelled facts |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Sadie Long | Form 1040, Form W-2(2), Schedule 2, Schedule 3, Schedule H, Form 5695 | 0 | 0 | 6 | 0 | 0 | `form_not_modelled` (5), `line_not_computed` (1) | none | 5 |
| 2 | James and June Brown | Form 1040, Form W-2 (2), Schedule 1, Schedule A, Schedule C, Form 8283 | 1 | 0 | 0 | 6 (6) | 0 | none | none | 8 |
| 3 | Lynette Heather | Form 1040, Form 1099-R, Schedule 1, Schedule 2, Schedule D, Schedule E, Schedule F, Schedule SE, Form 4835 | 6 (3 zero lines the engine leaves out) | 0 | 19 | 4 | 0 | `form_not_modelled` (19) | none | 7 |
| 4 | Dani Lozano | Form 1040, Form W-2, Schedule 1, Schedule 3, Schedule 3-A, Form 2441, Form 8862, Form 8863, Schedule EIC, Schedule 8812 | 1 (1 zero line the engine leaves out) | 0 | 0 | 0 | 0 | none | none | 7 |
| 5 | Sam Wheat | Form 1040, Form W-2, Form 8888 | 0 | 0 | 0 | 0 | 0 | none | none | 2 |
| 6 | Jeremy Davidson | Form 1040, Form W-2, Form W-2G, Schedule 1, Schedule E | 1 | 0 | 0 | 0 | 0 | none | `form_8615_living_parent_unknown`, `form_8615_support_unknown` | 2 |
| 7 | Teresa Morehouse | Form 4868 | 0 | 0 | 2 | 0 | 0 | `form_not_modelled` (2) | none | 7 |
| 12 | Sam Gardenia | Form 1040, Schedules 1, 2, 3, C, SE, Form 3800 and its Schedule A, Forms 4562-B, 7205, 7207, 7220, W-2, four binary attachments | 37 | 13 | 19 | 10 | 0 | `form_not_modelled` (18), `line_not_computed` (1) | none | 14 |
| 13 | William and Nancy Birch | Form 1040, Schedules 1, 1-A, 2, 3, F, SE, Form 4136 and two Schedules A, Form 1062 and its Schedule A, W-2 | 22 | 40 | 47 | 3 | 2 | `form_not_modelled` (43), `line_not_computed` (4) | none | 8 |
| 14 | John Rosen | Form 1040, Schedules 3-A, EIC, 8812, W-2 | 32 | 16 | 0 | 0 | 3 | none | none | 1 |

Totals: 100 lines matched (4 of them zero lines the engine leaves out), 69 differ, 93 blocked (162 gaps recorded), 23
inputs echoed and 6 not shown, 5 ambiguous.

Per scenario:

- **1** (input documents): two W-2s; Schedule H wages (4,100) and a $200 Form 5695 carryforward; Form 1062 assumed attached,
  its amount (200) printed on Form 1040 line 24b. Schedule H, Form 5695 and Form 1062 are not modelled; filing status Single
  by elimination; no date of birth.
- **2** (input documents): joint return (assumed from the header; nothing checked), a 16-year-old son, two W-2s, Schedule A
  entries totalling less than the 2026 standard deduction (the engine takes the standard deduction, so Schedule A is not
  shown), a Schedule C with expenses and no gross receipts (a 2,555 loss), a $300 overpayment applied from 2025, Form 8283's
  730 of clothing. Schedule 1 line 26 = 0 matches.
- **3** (input documents): a 1099-R, a $4,089 taxable refund, Schedule D totals (lines 1a and 8a), Schedule F and Form 4835
  (not supported), the farm optional method on Schedule SE. Form 1040 line 1z, Schedule D lines 18-19 and Schedule SE's zero
  lines match.
- **4** (input documents): a blind full-time student with two children, a W-2, child care (2,300 for two persons), $890 of
  education expenses (American opportunity credit), Form 8862 after a disallowance (not modelled), $1,325 of moving
  expenses (deductible only for the Armed Forces; not stated). Head of household assumed; Single re-run, no scored line
  changes (only Form 2441 line 23 = 0 is printed).
- **5** (input documents): one W-2 and a Form 8888 split (1,000 to savings, the rest to checking). No computed line is
  printed: nothing to compare until T2-01 serializes the return and the refund allocation.
- **6** (input documents): an 18-year-old dependent with a W-2 (overtime TT 200, tipped occupation code 102, boxes 3 and 5
  blank), a $2,200 W-2G (Schedule 1 line 8b) and a gambling partnership on Schedule E (a 1,000 nonpassive loss and 2,200 of
  nonpassive income). The engine blocks: his unearned income (3,400) is over the $2,700 Form 8615 threshold, and the scenario
  does not say whether his earned income was over half his support or whether a parent was living.
- **7**: Form 4868 alone (extension with a $4,000 electronic payment; Part II lines 4 and 5 printed, 6 and 7 blank). There is
  no Form 1040; Form 4868 is T2-05.
- **12** (complete return): matches on wages, Schedule C (gross receipts through net profit 8,661), Schedule SE (1,224),
  the self-employment deduction, AGI 108,885, the standard deduction and withholding. Differs: the scenario takes no QBI
  deduction (the engine figures 1,610), figures the tax by the rate schedule, and has a 10,000 general business credit
  (Form 3800, not modelled).
- **13** (complete return): matches on wages, the overtime deduction (5,000), car loan interest (5,000), the senior deduction
  (6,000 for the spouse), Schedule 1-A's total (16,000), the standard deduction with one spouse 65 or older (33,850) and
  withholding. Differs: Schedule F (not supported) carries a 10,000 farm profit and the self-employment tax on it, so AGI and
  everything after it differ; Form 4136 and Form 1062 are not modelled.
- **14** (complete return, single with two children): matches on wages, AGI, the standard deduction, taxable income, the
  child tax credit's computation through Schedule 8812 line 12, line 18a-20, withholding and Schedule 3-A's lines for a
  citizen. Differs: the tax (Tax Table) and the earned income credit (2026 amounts), with the credits and refund after them.

## 5. What the scenarios say

Findings from the comparison, each worked by hand from the scenario's printed figures (also in each fixture's `gap_causes`):

1. **The scenarios figure the tax by the rate schedule, not the Tax Table.** Scenario 14's tax on 18,900 is 2,020 (1,240 +
   12% of 6,500); the 2026 instructions' Tax Table, which the engine follows below $100,000, gives 2,023 (the row's midpoint,
   18,925). Scenario 12's 15,125 on 92,785 and scenario 13's 9,886 on 86,516 are also exact rate-schedule amounts (the table
   gives 15,123 and 9,887). The engine is right by the instructions; whether ATS expects the printed amount is a question for
   the e-Help Desk (section 6).
2. **Scenario 14's earned income credit uses the TY2025 amounts.** 4,693 is exactly what 2025's two-child figures give
   (maximum 7,152, phase-out from 23,350 at 21.06%, at the EIC Table midpoint 35,025); the 2026 amounts of Rev. Proc. 2025-32
   §4.06 (7,316, from 23,890) give 4,971, the engine's figure.
3. **Scenario 12 takes no qualified business income deduction** (line 13b blank) for a non-SSTB Schedule C profit of 8,661
   under the threshold; the engine figures 1,610.
4. **Scenario 13's tax ignores the 0% rate on capital gain distributions**: line 7a prints 500 with "Schedule D not
   required", yet line 16 taxes all of taxable income at ordinary rates.
5. **The scenarios' own arithmetic fails in places** (recorded as ambiguities, the lines not scored): scenario 14's Schedule
   3-A line 1a prints 5,141 where Form 1040 line 32a, which it repeats, prints 7,073; scenario 13 counts the 1,007 fuel credit
   twice (Schedule 3 line 15 on Form 1040 line 31, and again on line 32a), its Schedule SE line 12 (268) omits line 10's
   social security part (1,145), and its Form 1062 treats 56,516 of farmland gain as part of taxable income that no income
   line carries.
6. **Facts the engine requires that ATS does not state**: scenario 6 blocks on Form 8615's support and living-parent facts
   (correctly: the scenario's dependent has unearned income over $2,700). No scenario checks a filing status in 1-7.
7. **Forms the scenarios need that the engine does not model**: Schedule F and Form 4835 (3, 13), Schedule H (1), Form 5695
   (1), Form 1062 (1, 13), Form 3800 with Forms 7207, 7220 and Schedule A (12), Form 4136 (13), Form 8862 (4), Form 8888 and
   direct deposit (5), W-2G as a document (6), Form 4868 (7). Deductions printed on Schedule C (Form 7205's 5,000, Form
   4562-B's 667) enter as stated amounts.
8. **None of the ten scenarios exercises Forms 8962, 2210 or 8582** (no marketplace coverage, no estimated tax penalty, no
   passive activity), nor 1116, 8606, 8889, 4797, 8615's computation or 1040-X: the remaining T1-01 slices get no evidence
   from this ATS set and keep relying on the IRS instructions' examples and the independent preparer's set (T1-02).

## 6. The ATS calendar and what the firm must do

From `docs/irs/ats-ty2026/README.md` and `docs/irs/EFILE_APPLICATION_PACKET.md` (both prepared 2026-10-08):

| When | What |
|---|---|
| Now | Create the e-Services accounts (ID.me identity proofing, Secure Access PIN) for each Principal and Responsible Official and submit the e-file application: Software Developer, Transmitter and Online Provider; Electronic Return Originator only if Ignite9 prepares returns itself |
| October 13, 2026 | The TY2026 Form 1040 ATS opens; testing starts once the ETIN arrives |
| About 45 days after submitting | The IRS's decision (suitability check, fingerprints unless credentialed): EFIN and ETIN issued |
| After the ETIN | Request the TY2026 1040 schemas and business rules from the e-Services Secure Object Repository (T2-01); call the e-Help Desk (866-255-0654) to open ATS, obtain the TY2026 Software ID and confirm which scenarios AgentLedger must pass |
| If Transmitter | Register the A2A application system (ASID), enroll an IRS-accepted X.509 certificate, schedule the communications test (T2-02) |
| November 1, 2026 | 1065 schemas valid in ATS (business returns, later) |
| Late January 2027 | MeF production opens: transmit only after ATS acceptance |

The README's order still holds: scenarios 5 and 14 first (the full pipeline: engine, XML, schema validation, transmission,
acknowledgment), then 4, 2 and 6 (small input additions: Form 8862, Form 8283, the IP PIN, the W-2G), then 7 (Form 4868, a
separate submission), then 1, 3, 13 and 12 or a declaration that they are out of scope for TY2026. Scenarios 8-9 (1040-SS)
and NR 1-3 (1040-NR) were not downloaded and are out of scope.

Questions for the e-Help Desk when ATS opens: whether ATS compares the transmitted amounts with the scenario's printed ones
(findings 1-2: if it does, T2-01 needs a test mode that serializes the fixture's printed amounts, never an engine bent to
them); which scenarios are required for the forms AgentLedger declares; whether the drafts will be revised before testing.

## 7. Coverage evidence

`coverage/coverage.yaml` is maintained by its owner; nothing there was changed by this ticket. Proposed evidence once the
tax-content owner reviews the findings: on `f1040`, `sch_1`, `sch_1a`, `sch_2`, `sch_3`, `sch_3a`, `sch_8812`, `sch_c`,
`sch_se` and `sch_d`, "tests/test_ats_scenarios.py (IRS ATS TY2026 scenarios 1-7 and 12-14 as independent fixtures:
100 lines matched, 162 gaps recorded with causes; docs/ATS.md)". The ATS figures are independent of the engine but not
authoritative tax computations (section 5), so they support, and do not replace, the owner's sign-off and T1-02.
`filing-approved` for `mef_1040` needs the ATS acceptance itself (T2-02).
