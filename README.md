# AgentLedger

**Open-source accounting, ledger and tax platform where AI agents do the work and deterministic code proves every number.**

A workforce of AI agents watches the law, the documents arriving from clients, and the AI and
open-source landscape. They propose changes, a deterministic layer proves those changes are
grounded and safe, and verified routine updates adopt themselves. Everything else is decided by a
CPA in plain language, so the software stays compliant without anyone having to write code.

Open source first. Local open-source models do most of the AI work. The Claude API is used only
for advanced reasoning, under a daily call budget, and never with raw taxpayer identifiers.

```
              official sources          client documents            AI / OSS landscape
     (Federal Register, IRS, FinCEN)  (email, uploads, connectors)  (Ollama, HF, GitHub, PyPI)
                    │                          │                            │
                    ▼                          ▼                            ▼
   ┌──────────────────────────── Agent Foundry: observe → propose ────────────────────────────┐
   │ RegWatch · Staleness Hunter · Intake · Integrity Sweeper · Automations · Model Scout ·   │
   │ Repo Scout · Dependency Watch · Researcher · Engineer · Architect · Connectors           │
   └─────────────────────────────────────────────┬────────────────────────────────────────────┘
                                                 ▼
             Sentinel: deterministic verification (no model involved)
   verbatim-quote grounding · every number traced to evidence · type and bounds checks ·
   timeline placement · golden regression · protected paths · test suite · risk tier
                                                 │
                   ┌─────────────────────────────┴──────────────────────────────┐
                   ▼                                                            ▼
     auto-adopt (verified + within policy)                     a CPA decides in plain language
                   └──────────────► provenance · changelog · undo · audit ◄─────┘
                                                 │
     regulation-as-code KB ─► deterministic calculators ─► hash-chained ledger ─► live M-1
     domain packs · CPA second brain · two-sided CRM · Ask (grounded answers) · MCP · plugins
```

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"      # or: pip install -e ".[dev]"
ollama pull qwen2.5:7b && ollama pull qwen3.5:4b   # local open-source models (optional but recommended)
agentledger demo                               # seed a 4-client, multi-industry demo firm
agentledger serve                              # http://127.0.0.1:8740  (agents run in the background)
```

Optional settings:

- `ANTHROPIC_API_KEY` enables the frontier tier, capped by `config/models.yaml → frontier.daily_call_budget`.
- `AGENTLEDGER_IMAP_*` enables mailbox intake.
- `AGENTLEDGER_SMTP_*` lets drafted emails be sent.
- `AGENTLEDGER_WEBHOOK_SECRET` enables inbound webhooks.
- `AGENTLEDGER_TEAM_WEBHOOK_URL` enables posts to Slack or Teams.

To try the app, use the identity picker at the bottom-left of the screen. You can switch between
the CPA and three client owners and see the same facts from each side.

Useful CLI commands:

```bash
agentledger agents list | run <id> | due | daemon        # the workforce
agentledger agents design playbook "QSBS planning for C-corp founders"   # AI drafts it; you approve
agentledger regdoc https://www.irs.gov/pub/irs-drop/rp-26-xx.pdf --title "Rev. Proc. 2026-xx"
agentledger ingest ./scans --client ortiz-auto           # any format, any folder
agentledger proposals list | show | approve | reject | rollback
agentledger golden                                       # regression scenarios
agentledger stale                                        # what is due, overdue, sunsetting
agentledger mcp                                          # expose AgentLedger to any MCP client
python -m pytest -q
```

## What is in the box

| Area | What it does | Where |
|---|---|---|
| Regulation as code | Every statutory number is stored as an effective-dated value with its citation. Code never hardcodes law. | `rules/`, `src/agentledger/kb` |
| Deterministic engines | Calculators record which rule values they used. The LLM never does tax math. | `src/agentledger/calc` |
| Ledger | Double-entry and append-only (database triggers block edits), hash-chained per client, with a tax treatment on every posting. Corrections are reversals. Exports to Beancount. | `src/agentledger/ledger` |
| Book-to-tax | Schedule M-1 is computed live from the ledger and the rules, including §179 phase-out, bonus depreciation by acquisition date, and MACRS. | `ledger/m1.py` |
| RegWatch | Watches the Federal Register, the IRS newsroom, and any web page you declare. Drafts evidence-carrying rule changes. Escalates once, to Claude, only when a local draft fails verification or a final/proposed rule looks dismissed too quickly. | `regwatch/`, `foundry/agents/regwatch.py` |
| Sentinel | Quotes must appear verbatim in the source, and every number must appear in those quotes. Also checks official-domain sources, types and bounds, timeline conflicts, and golden regression, then assigns a risk tier. | `foundry/verify.py` |
| Staleness Hunter | Knows when each indexed parameter is due (wage base, inflation Rev. Proc., mileage). Alerts before the due date and hunts official sources once it is late. Watches sunsets. | `foundry/agents/staleness.py` |
| Intake | Takes email (IMAP), a maildrop folder, uploads, webhooks, and MCP. Handles PDF, images, DOCX, XLSX, CSV, EML and ZIP. Identifies the form type deterministically first, then uses the local model. Matches each document to one client or sends it to review; it never guesses a client. Files to `vault/<client>/<year>/<category>/`, links receipts to entries, and feeds 1099s to the integrity checks. | `src/agentledger/intake` |
| Ask | Builds an evidence pack (rules, books, M-1, findings, documents, information returns, playbooks, precedents). The answer must cite it, and attribution is verified number by number. Clients never see an unverified answer. Every answer goes into the shared audit trail. | `src/agentledger/ask` |
| Integrity | Covers ledger tampering, closed-period and backdated entries, 1099 income vs. books (CP2000 risk), missing receipts, entertainment booked as meals, personal expenses, and round-number estimates, plus domain rules. Findings can be explained, corrected or (CPA only) accepted as a risk; they are never deleted. | `src/agentledger/integrity` |
| Domain packs | Industry is data: chart of accounts, balanced posting templates (which can pull statutory rates from the rules), integrity rules and KPIs. Ships general, auto repair, gas station / C-store, medical practice, insurance agency, PE fund and hedge fund packs. The Architect drafts new packs, which are verified mechanically. | `domains/`, `src/agentledger/domains` |
| CPA second brain | Expert playbooks with machine-checkable applicability conditions, a substance (honesty) requirement, and links to live rules (a playbook is flagged stale when its rules change). Firm precedents. A proactive opportunity scan per client. | `playbooks/`, `src/agentledger/brain` |
| Two-sided CRM | The firm side has engagements, tasks and client requests, messages, and a deadline calendar with §7503 weekend rollover. The business side has customers, vendors, deals, invoices that post automatically, AR aging, and vendor 1099 / W-9 readiness checked against the live threshold. | `src/agentledger/crm` |
| Automations | Rules of the form "when this event happens and this condition holds, do this", fed by the audit trail. You can describe one in plain English and the AI drafts it. Email is drafted, never sent automatically. | `config/automations.yaml` |
| Integrations | Least-privilege plugins with manifests. Bank CSV, OFX, Stripe, synced folders, any MCP server, universal HMAC webhooks, QuickBooks/Xero trial-balance migration, QuickBooks IIF export, tax-software trial-balance export, Beancount. A catalog lists more connectors the Engineer can build on request. | `src/agentledger/plugins`, `config/connectors_catalog.yaml` |
| Model Scout | Per-role champion models chosen by benchmark on our own evals. Discovers new open models (Ollama library, Hugging Face) and new Claude models (Models API). Promotions are reversible. | `foundry/agents/scouts.py`, `evals/`, `config/models.yaml` |
| Repo Scout and Dependency Watch | Live inventory of the open-source stack: health, releases, licence changes, newly discovered candidates. Upgrade proposals. | `config/oss_inventory.yaml` |
| Researcher and Engineer | The Researcher writes briefs and proposes regression tests (auto-added only if the engine already reproduces the authority's number). The Engineer drives an open-source coding agent (Aider on a local model) or Claude Code in an isolated git worktree. Gates: protected paths, diff size, new tests, full suite, golden. | `foundry/agents/builders.py` |
| MCP server | Exposes Ask, rules, calculators, M-1, findings, deadlines, opportunities and intake to any MCP client, scoped by role and client. | `src/agentledger/mcp_server.py` |

## Guardrails (non-negotiable)

- **The AI writes words; deterministic code establishes facts.** Numbers come from calculators and
  the ledger. Drafted rule values must appear verbatim in quotes found in the official source.
  Answer numbers must be cited to evidence that contains them.
- **Nothing is guessed.** A missing rule value raises an error. An unmatched document goes to
  review. An unverified answer is not shown to a client.
- **The AI cannot change its own guardrails.** Paths listed in `config/foundry.yaml →
  protected_paths` always need human approval.
- **Supply-chain changes are always human.** That covers new repositories and dependency upgrades.
  Model weights are data and can auto-promote, but only after winning on our evals, and they are
  reversible.
- **Every change can be undone and is recorded.** Proposals store undo data, and the audit trail
  is hash-chained and shared between CPA and client.
- **Privacy.** Tier-1 models run on-premises. Frontier calls require a §7216 consent on file,
  redact direct identifiers, and are capped per day.

## How AgentLedger fixes the pain points of current US tools

| Pain point (QuickBooks, Xero, FreshBooks, Wave, Sage, Drake, UltraTax, Lacerte, ProConnect, …) | AgentLedger |
|---|---|
| Books and tax live in different products, so trial balances get re-keyed into tax software | One ledger with tax attributes on every posting. M-1 is computed live. A tax-software trial-balance export is there for coexistence. |
| Payroll and tax tables lag behind law changes, and you wait for the vendor's release | Regulation as code, maintained by agents with citations. Routine indexed updates adopt themselves once verified. |
| Bank rules are brittle, so you recategorize the same vendor every month | Categorization learns from your own confirmed history first, then known merchants, then a local model. You confirm before anything posts. |
| Accountant and client edit the same books, closed periods change, nobody knows who changed what | Append-only, hash-chained ledger. Corrections are reversals. Backdated and closed-period entries are flagged. One shared audit trail. |
| Industry gaps: job costing, fund accounting, fuel inventory, insurance trust, medical remittances | Domain packs, plus an Architect that drafts a new industry pack from a sentence. |
| Receipt and document chaos across email, phones and portals | Any channel, any format. Auto-classified, filed by client, year and category, linked to entries. Nothing is guessed into a client. |
| "AI" features that are opaque or invent things | Every answer is cited and verified number by number. Unverified answers are flagged to CPAs and held back from clients. |
| Price hikes, per-user pricing, lock-in | Open source and self-hosted. SQLite or Beancount export. Local models by default. |
| Chasing clients for missing documents and facts | Automations create client requests from findings, deadlines and the facts a playbook needs. Messages are drafted for you. |
| Integrations are paid add-ons or brittle point-to-point syncs | Least-privilege plugins, universal webhooks, MCP in both directions. New connectors are built by the AI Engineer on request. |
| Missed deadlines | A deadline calendar computed from client facts, with weekend rollover and daily task creation. |

## Honest status

An external audit on 2026-10-08 found nine gaps between these docs and the code; each is now fixed and covered by a
regression test (see `docs/inception/audit-2026-10-08.md`). Capabilities earn their status through demonstrated behavior.


**Working, tested, and exercised live against real sources and local models:**

- RegWatch on the live Federal Register and IRS newsroom
- intake (including email, ZIP and receipt linking)
- Ask with attribution checks
- the Model Scout's benchmark-driven promotion and rollback
- all of the deterministic core

- the TY2026 Form 1040 engine (`src/agentledger/returns`). It covers 20 forms and schedules, including OBBBA
  Schedule 1-A and Schedule 3-A, and every return is cross-checked against PolicyEngine US
  (`pip install -e .[oracle]`). Try it with `agentledger return samples/return_hoh_2026.yaml`

**Not built yet** (see `docs/GO_LIVE.md` for the full plan):

- IRS MeF e-file transmission and AcroForm generation (requires an EFIN/ETIN and ATS certification)
- state income tax engines (policyengine-us is tracked by the Repo Scout but not integrated)
- multi-entity consolidation
- bank reconciliation screens
- payroll processing
- passkeys (WebAuthn) and SSO. Multi-firm sign-in with password and TOTP two-step verification is built.
  Use `agentledger serve --dev` for the demo identities in `config/users.yaml`
- the "buildable" connectors in the catalog

**Facts to know about the shipped content:**

- The rule seed values were checked on 2026-10-08. One example is FinCEN's final rule, effective
  2026-08-14, which exempts domestic companies from BOI reporting. That supersedes the CTA section
  of `BL.md`.
- Playbooks ship as `seed_unreviewed`. A CPA should review them before relying on them in client
  work.
