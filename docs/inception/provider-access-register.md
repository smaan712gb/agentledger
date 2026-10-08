# Provider-access register

Spec §13 and §22: record what we actually have access to. A provider is **production** only after its
contract, credentials, sandbox tests and recovery procedure exist. A mocked adapter or a successful login
does not count.

Status values: `planned` · `access-requested` · `sandbox-tested` · `pilot` · `production` · `degraded` · `retired`.
As of 2026-10-08 nothing is in production.

| Area | Provider (proposed) | Purpose | Needs from the owner | Status |
|---|---|---|---|---|
| Edge, compute, storage, workflows | Cloudflare (Workers, Containers, R2, Queues, Workflows, Access, AI Gateway, Workers AI) | Platform (ADR-0001) | Account, enterprise or business plan decision, DPA | planned (MCP connector attached; account not yet configured for AgentLedger) |
| Financial database | Neon (alternative: PlanetScale Postgres via Cloudflare) | PostgreSQL authority (ADR-0002) | Paid plan with compliance terms (and a BAA for healthcare clients) before any real client data | sandbox-tested: project AgentLedger (aws-us-east-1, PostgreSQL 17), `dev` branch linked, TLS + channel binding verified, free plan |
| Customer identity | WorkOS AuthKit (self-host: Better Auth) | OIDC, MFA, passkeys, SSO (ADR-0004) | Contract | planned |
| Key management | AWS KMS | Wrap the master key (ADR-0001) | AWS account | planned |
| IRS e-file | IRS e-Services: EFIN, ETIN, MeF A2A, ATS | Native 1040 / 1120-S / 1065 filing | Responsible Official, e-file application, Pub 1345 standards (GO_LIVE A1–A6) | planned |
| IRS information returns | IRS IRIS (or FIRE) TCC | 1099 filing | Separate TCC application | planned |
| State e-file | Each state's Fed/State MeF program | State returns | Per-state approval after states are named | planned |
| Signature with KBA | to choose (identity bureau with IRS Pub 1345-compliant KBA) | Form 8879 / 8878 | Contract | planned |
| E-signature (engagement letters, consents) | to choose | Engagement letters, §7216 consents | Contract | planned |
| Bank and card feeds | Plaid, MX or Finicity | Transactions and statements | Contract, production approval | planned |
| Payments acceptance | Stripe | Invoices, practice billing | Account | planned |
| Payroll data | Gusto / Check / ADP APIs | Payroll journals (spec C14) | Partner access | planned |
| Accounting import | QuickBooks Online, Xero (OAuth apps) | Migration and coexistence | Developer app registration (GO_LIVE A10) | planned |
| Tax-software interoperability | Drake, UltraTax, Lacerte/ProConnect, CCH, TaxWise | Export of trial balances and workpapers | Verify each import format with a licensed copy; no partner API assumed | planned |
| Email and calendar | Google Workspace, Microsoft 365 (OAuth, minimum scopes) | Intake, requests, reminders | App verification | planned |
| OCR and extraction | Workers AI vision models, Tesseract, or a benchmarked vendor | Document fields | Benchmark on permitted samples | planned |
| Open-model inference | Workers AI (in-platform), NVIDIA API catalog, Hugging Face Inference Providers | Tier-1 tasks (ADR-0009) | API keys stored in AI Gateway; per-provider data terms verified | sandbox-tested (catalogs read live; no inference calls yet) |
| Frontier inference | Anthropic (through AI Gateway) | Tier-3 reasoning under consent | Zero-retention configuration verified for our account | planned |
| Reference tax engine | PolicyEngine US (open source) | Independent cross-check only | None | sandbox-tested (11 cross-check scenarios agree) |
| Regulatory sources | Federal Register API, IRS newsroom, IRS forms (irs.gov) | RegWatch | None | sandbox-tested |
| Malware scanning | ClamAV in a worker, or a scanning API | Intake | None or contract | planned |
| Observability | Grafana Cloud or Cloudflare Logpush + OpenTelemetry | Traces, metrics, logs | Account | planned |
| Billing | Stripe Billing | SaaS subscriptions and metering | Account | planned |
| IRS transcripts for representatives | IRS e-Services Transcript Delivery System (authorized representatives with 2848/8821 on file, CAF number) | Resolution early warning and CSED (ADR-0011) | Firm's e-Services access; confirm which automated access the IRS permits | planned (file upload works without it) |
| Legislation feeds | Open States API (all states), GovInfo / api.data.gov (federal public laws) | legislation-watch (ADR-0010) | Free API keys | planned |
