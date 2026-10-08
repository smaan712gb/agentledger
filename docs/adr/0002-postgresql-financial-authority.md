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
