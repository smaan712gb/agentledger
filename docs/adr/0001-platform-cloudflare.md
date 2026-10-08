# ADR-0001: Run on Cloudflare, with managed PostgreSQL, an OIDC identity provider and an external KMS

Status: Accepted (owner chose Cloudflare, 2026-10-08). Vendor selection for the three non-Cloudflare
components is proposed below and needs owner confirmation before contracts are signed.
Spec references: §4 (default technology decisions), §14 (data protection), §18 (operations).

## Context

The spec's baseline is one cloud with managed PostgreSQL, versioned object storage with KMS and retention
locks, containers, durable workflows, managed OIDC identity, and infrastructure as code. The owner chose
Cloudflare. Its documentation (checked 2026-10-08) shows:

- **Containers.** Docker workloads with custom instance types up to 4 vCPU, 12 GiB memory and 20 GB disk.
- **R2 bucket locks.** Retention by prefix, for a duration, until a date, or indefinitely.
- **Workflows.** Durable steps with retries, `sleep`, and `waitForEvent` for days or weeks.
- **Hyperdrive.** Connects to any PostgreSQL 9–18, including private databases through Workers VPC and
  Tunnel, and is supported from Python Workers since 2026-09-16.
- **Workers AI and AI Gateway.** In-platform open models, plus logging, budgets and keys for external
  models.
- **Secrets Store.** Open beta. It is not a key-management service.
- **No managed PostgreSQL.**
- **No customer identity product.** Access is for staff.

## Decision

| Concern | Choice |
|---|---|
| Edge, TLS 1.3, WAF, DDoS, bot management | Cloudflare |
| Staff and support access to admin consoles | Cloudflare Access (Zero Trust), with time-limited support grants |
| Web application | React + TypeScript, served as Workers static assets (ADR-0005) |
| Edge API gateway | Worker: session validation, rate limits, request ids, routing to the API container |
| Domain API and calculation engines | Python (FastAPI) in Cloudflare Containers. Stateless, with no data on local disk. |
| Background workers (OCR, extraction, connectors, MeF) | Containers, one image per worker class, with separate identities |
| Durable orchestration | Cloudflare Workflows (ADR-0003) |
| Outbox and event delivery | Cloudflare Queues, fed from the PostgreSQL outbox table |
| Financial authority | **Managed PostgreSQL, proposed: Neon.** SOC 2 Type II, HIPAA BAA available, branching for test copies. Alternative: AWS RDS for PostgreSQL for multi-AZ high availability. Reached through Hyperdrive and Workers VPC only, with no public endpoint. |
| Evidence vault | R2. Objects are encrypted client-side with the firm's data key before upload. Content-addressed keys. Bucket-lock rules per retention class (ADR-0006). |
| Customer identity | **OIDC provider, proposed: Zitadel Cloud** (open source, so it can be self-hosted later). MFA, passkeys, and SAML or OIDC federation for firms on Entra ID or Google (ADR-0004). |
| Key management | **External KMS, proposed: AWS KMS.** Used only to wrap the platform master key, with HSM-backed keys, rotation and CloudTrail evidence. The master key is never stored in plaintext at rest. |
| Tier-1 AI on taxpayer data | Workers AI open models (in-platform, no training on inputs), under the data-processing policy (ADR-0007) |
| Tier-3 AI | Claude through AI Gateway, only with consent and under the policy in ADR-0007 |
| Infrastructure as code | Terraform (Cloudflare provider, Neon provider, AWS KMS). Wrangler for Worker and Container builds. |
| Environments | `dev` (local Docker Compose), `staging`, `prod`: separate Cloudflare accounts or zones, Neon projects and KMS keys |

## Consequences

- One edge platform with no servers to patch. Containers and Workflows scale per tenant load.
- Three external vendors to contract and assess (Neon, Zitadel, AWS KMS). Each goes on the provider-access
  register with its status.
- Container instances are ephemeral. Nothing authoritative lives on container disk; PostgreSQL and R2 hold
  all state.
- A **spike** (wave F, ticket F-02) must confirm, before the first firm is onboarded:
  - Workflow limits (steps, payload size, retention) against the return and close workflows.
  - Container cold-start latency against the p95 targets in spec §18.
  - Hyperdrive transaction semantics for posting functions (transaction mode, no cached writes).
- Local development keeps working without Cloudflare: Docker Compose with PostgreSQL, MinIO and a local
  workflow runner behind the same interfaces.
