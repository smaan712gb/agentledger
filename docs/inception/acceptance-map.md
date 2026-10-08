# Acceptance tests Q01–Q40: where each one stands

Spec §19. A Q-test passes only when an automated test proves it with independently derived expectations.
States: **pass** (a test exists and proves it) · **partial** · **todo**. Wave = the release gate it belongs to.

| Q | Scenario (short) | Wave | State | Where |
|---|---|---|---|---|
| Q01 | Unbalanced journal rejected on every route | F | pass (PostgreSQL core) | deferred balance trigger refuses even the table owner; the app role cannot write tables (`tests/test_pg_ledger.py`). With `AGENTLEDGER_DATABASE=postgres` the API and every module post through `post_journal`; the whole suite runs on PostgreSQL in CI |
| Q02 | Same command retried 100× concurrently → one effect | F | partial | database receipts checked under the client lock; 4 concurrent connections post once (`tests/test_pg_ledger.py`); a 100× stress run todo |
| Q03 | Worker crash after commit, before reply | F | pass (transmission) | two-phase activities: `test_crash_*` in `tests/test_audit_findings.py`; ledger outbox todo |
| Q04 | Period closes while a draft awaits posting | F | pass | closed period enforced by a database trigger, CPA-only audited reopen (`tests/test_pg_ledger.py`); app-level check in `test_closed_period_*` |
| Q05 | Reversal / correction reconciles | F | partial | reversal-only corrections, immutable rows, hash chain detects tampering (`tests/test_pg_ledger.py`); report tie-out test todo |
| Q06 | Foreign-currency settlement | A2 | todo | |
| Q07 | Invoice plus bank payment recognized once | A1 | todo | |
| Q08 | Duplicate, pending and corrected feed events | A1 | todo | |
| Q09 | Missing bank-feed date range | A1 | todo | |
| Q10 | Processor deposit with fees, refunds and reserves | A1 | todo | |
| Q11 | Malicious document tries to exfiltrate | F | partial | grounding and no-write models; adversarial suite todo |
| Q12 | Cross-client and cross-investor access | F | partial | firms (`tests/test_tenancy.py`), related-record ownership and reviewer authority (`tests/test_audit_findings.py`); search, background jobs, investors todo |
| Q13 | Access revoked mid-workflow | F | partial | disable revokes sessions; queued-task re-check todo |
| Q14 | Vendor bank change embedded in invoice | A1 | todo | |
| Q15 | Approved action modified before execution | F | pass (returns) | approval and signature bound to the full package hash; recompute and edits reopen (`test_rule_change_after_signature_voids_it`) |
| Q16 | OCR ambiguity needs review | A1 | partial | grounding drops unsupported numbers; date, decimal and entity cases todo |
| Q17 | Close snapshot equals exports | A1 | todo | |
| Q18 | Tax package regression, exact | T1 | partial | 26 hand-worked tests, 5 golden returns, 11 PolicyEngine cross-checks; needs an independent preparer's set |
| Q19 | Unsupported form/state/election blocked | T1 | pass | blocking diagnostics plus coverage gate (`tests/test_return_workflow.py`) |
| Q20 | Corrected K-1 or asset invalidates downstream | T1 | todo | |
| Q21 | Form rendering matches calculation | T1 | todo | PDF output not built |
| Q22 | Signature failure or stale signed package | T2 | partial | hash-bound signature guard; KBA failure path todo |
| Q23 | Filing response lost after acceptance | T2 | partial | `unknown` state with provider lookup or human reconciliation (`test_crash_*`); real transmitter adapter todo |
| Q24 | Federal accepted, state rejected | T2 | todo | |
| Q25 | Payment timeout or return | A1 | todo | |
| Q26 | Healthcare remittance with adjustments | V | todo | |
| Q27 | Fuel and lottery mixed retail | V | todo | |
| Q28 | Pooled trust cash masks a client deficit | V | todo | |
| Q29–Q31 | Fund trades and NAV, fees, PE waterfall | S | todo | |
| Q32 | Source migration ties out | A1 | todo | |
| Q33 | Backup restore with active workflows | F | todo | |
| Q34 | Model unavailable or budget exhausted | F | partial | router falls back and pauses; visible pause UI todo |
| Q35 | Connector schema, rate-limit or auth change | A1 | todo | |
| Q36 | Skill or plugin update governance | F | todo | |
| Q37 | Retention expiry with legal hold | F | todo | |
| Q38 | Browser, mobile and keyboard end-to-end | A1 | todo | needs the React UI (ADR-0005) |
| Q39 | Rule change after filing | T1 | partial | runs pin the knowledge-base version and rule trace; recalculation candidates todo |
| Q40 | Deadline relief, holidays and time zones | A1 | partial | §7503 weekend rollover built; holidays, relief and time zones todo |
