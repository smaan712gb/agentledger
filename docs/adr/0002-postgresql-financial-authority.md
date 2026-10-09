# ADR-0002: PostgreSQL is the single financial authority; tenancy by database per firm

Status: Accepted. Supersedes the SQLite store for anything beyond local demos.
Spec references: §1 (one authoritative core), §6 (tenancy, financial integrity), §14 (RLS), Q01–Q05, Q12.

## Decision

1. **One authoritative PostgreSQL ledger.** Beancount and every tax-software format are import or export
   only. Monetary columns are `numeric` with an explicit currency column. Rates, prices and quantities have
   their own precision.
2. **Posting happens through database functions, never direct table writes.** `post_journal(command_id,
   payload)` validates the following inside one transaction:
   - at least two lines
   - an open period, with the row locked
   - valid accounts and dimensions
   - debits equal credits in functional currency, checked by a deferred constraint trigger over the
     whole journal

   It then writes the lines, the command receipt and an outbox event in that same transaction.
   Application and agent roles have `EXECUTE` on the functions and no `INSERT`, `UPDATE` or `DELETE` on
   ledger tables.
3. **Durable command identity.** `command_id` is unique per tenant. Same id with the same payload hash
   returns the original receipt. Same id with a different payload raises a conflict. Receipts are kept
   with the financial records, not in a cache.
4. **Immutability.** Posted lines can't be updated or deleted by any application role; triggers enforce
   this too. Corrections are linked reversals. A closed period needs a controlled reopen workflow.
5. **Tenancy.** Each firm gets its own PostgreSQL database (on Neon: one database per firm inside a project
   per environment). A shared `platform` database holds firms, users, memberships, grants and wrapped keys.
   Inside a firm database:
   - legal entities, engagements and access grants are explicit rows
   - **row-level security** enforces engagement grants, investor restrictions and delegated access as
     defense in depth
   - application roles do not own tables and have no `BYPASSRLS`
6. **Separate security tenant from legal entity** (spec §6). An entity served by two firms appears as two
   engagements in two firm databases, each with its own grant. Data crosses between firms only through an
   authorized export or delivery, never a cross-database write. A firm serving a business does not gain
   the owners' personal returns.
7. **Migrations** use Alembic with an expand-and-contract discipline, applied per firm database by a
   migration runner that records per-database versions.

## Why database per firm, plus RLS

The spec asks for RLS as defense in depth. A separate database per firm adds a second, structural
boundary. Cross-firm leakage would need the wrong connection, not a single missing predicate. It also lets
us export, restore or crypto-shred one firm without touching others. RLS still guards everything *inside*
a firm: staff without an engagement grant, client users, investors and external auditors.

## Migration from the current code

The current `agentledger.db` SQLite schema is the reference model. Ticket F-04 ports it to PostgreSQL, adds the
posting functions, the command and receipt tables and the outbox, and keeps SQLite only as an explicit
`--dev` demo profile until the port is complete. The test suite runs against PostgreSQL in CI.

Status (2026-10-08): the core is in `src/agentledger/pg/migrations/0001_ledger_core.sql`. Writes go only through
SECURITY DEFINER functions; `agentledger_app` holds SELECT and EXECUTE, never INSERT/UPDATE/DELETE. Error codes
AL001–AL007 map to the application's exceptions in `agentledger.pg`. Migrations are plain SQL with recorded
checksums (an edited, already-applied migration is refused) rather than Alembic: the invariants live in SQL, so
the migration tool adds nothing but a dependency.

Tenancy (2026-10-08): `AGENTLEDGER_PG_TENANCY=database` gives each firm its own Neon database (`al_<firm>`) on the
configured branch through the Neon API, sharing the endpoint and roles, so no per-firm credential is stored.
`schema` tenancy (a schema per firm in one database) serves development, tests and self-hosting. Deleting a firm
destroys its data key first, then its store and tenant directory, and records both steps in the platform log.

Credentials (2026-10-08, after the re-audit of 9333fde): owner credentials migrate and provision only. Each store has
its own runtime login role with privileges in that store alone; the API connects as that role and never holds owner
credentials in production (migrations run as a release step). Provisioning is journaled in the platform store and
resumes or cleans up only what it recorded creating.

Platform store (2026-10-09): the platform database of decision 5 (firms, users, sessions, wrapped data keys,
provisioning journal, offboarding records) is PostgreSQL in production: the schema `platform` of the runtime database,
with its own migrations (`pg/migrations/platform/`, applied by `agentledger platform migrate`) and its own runtime role
`rt_<database>_platform`, which has exactly the table privileges the code uses and none outside the schema. Firm
stores and the platform store share one switch (`AGENTLEDGER_DATABASE`); `AGENTLEDGER_PLATFORM_DATABASE` overrides it.
Read-modify-write sections (a firm's status, an account's failure count, an invitation's single use) are transactions
holding a per-key advisory lock, so several API containers share one store safely. SQLite (`state/platform.db`)
remains the local development profile.
