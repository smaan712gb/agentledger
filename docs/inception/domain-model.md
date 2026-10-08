# Domain model

Spec §6 lists the core records. This maps each one to a module, a storage location (platform database or
firm database) and today's state. Every firm-database record carries `entity_id` where it applies, plus
`created_at` and `recorded_at`. Mutable consequential records are versioned. Facts, ownership, elections,
rules and prices carry both effective time and recorded time.

| Area | Records | Module | Where | Today |
|---|---|---|---|---|
| Identity | Tenant(firm), User, Membership, Role, ServiceIdentity, AccessGrant, Consent, CredentialReference | `security/` | platform DB; grants and consents in firm DB | Firm, User, roles, invites, sessions built. Grants, service identities, consent records todo. |
| Directory | Party, Person, LegalEntity, Household, Relationship, OwnershipInterest, Engagement, ContactChannel | `directory/` (new) | firm DB | Only `clients` exists today; split into Party, LegalEntity and Engagement |
| Accounting | Book, Account, Dimension, FiscalPeriod, Journal, JournalLine, PostingBatch, BalanceSnapshot | `ledger/` | firm DB, posting functions | Accounts, entries and postings built (single book). Books, dimensions, periods, snapshots todo. |
| Subledgers | BankAccount, BankTransaction, Statement, Reconciliation, Customer, Invoice, Vendor, Bill, Settlement, Asset, InventoryMovement, Project | `subledgers/` (new) | firm DB | Bank feed suggestions, parties, invoices built. Statements, reconciliation, bills, assets todo. |
| Evidence | SourceObject, DocumentVersion, ExtractedField, FactAssertion, EvidenceLink, ReviewDecision | `evidence/` (from `intake/`) | firm DB plus R2 | Documents, fields and entry links built. Versions, fact assertions with supersession and conflicts todo. |
| Tax | TaxCase, TaxYear, Jurisdiction, Election, Carryforward, BasisSchedule, BookTaxAdjustment, CalculationRun, FormInstance, FilingPackage, Submission, Acknowledgment | `returns/`, `filing/` (new) | firm DB | Return (TaxCase plus versions with a pinned CalculationRun), M-1 built. Elections, carryforwards, packages, submissions and acks todo. |
| Practice | Opportunity, Proposal, Job, Task, Request, Deadline, ReviewNote, TimeEntry, WIPItem, PracticeInvoice | `crm/` | firm DB | Engagements, tasks, messages, deadlines built. Time, WIP and practice billing todo. |
| Automation | WorkflowRun, AgentRun, ActionProposal, Approval, SkillVersion, PluginInstallation, ToolInvocation, ProviderReceipt | `workflow/`, `foundry/`, `plugins/` | firm DB (tenant work); platform DB (platform agents) | Workflow events, agent runs, foundry proposals, plugins built. Generic ActionProposal/Approval binding todo. |
| Investments | Instrument … WaterfallRun | `funds/` (wave S) | firm DB | Not started |
| Operations | AuditEvent, OutboxEvent, SyncCursor, ImportBatch, CoverageRecord, ReleaseManifest, RetentionPolicy, Incident | `ops/` | both | Audit (hash-chained) and coverage registry built. Outbox, import batches, retention todo. |

## Invariants that move into the database (ADR-0002)

1. A posted journal has ≥ 2 lines, an open period, valid accounts and dimensions, and balances in
   functional currency. Enforced by `post_journal()` and a deferred constraint trigger.
2. Posted lines, audit events, workflow events and return versions are immutable to application roles.
3. One financial effect per `command_id`. Payload-hash conflicts are rejected.
4. Posting, its command receipt and its outbox event commit together.
5. Facts never overwrite: a new assertion supersedes an old one, and conflicting assertions open an
   exception.
6. Control accounts tie to their subledgers before a period can close.
