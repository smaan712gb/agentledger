# ADR-0005: React + TypeScript web application

Status: Accepted. Spec references: §3 (workspaces, screen inventory, required states), §4.

## Decision

- **Stack.** React 19 and TypeScript built with Vite, served as Cloudflare Workers static assets.
  - Routing and data fetching with TanStack Router and TanStack Query.
  - Grids with TanStack Table plus virtualization: keyboard navigation, saved views, bulk actions with
    per-item results.
  - An accessible component layer on Radix primitives.
  - An authenticated single-page app needs no server rendering, so we use Vite instead of Next.js. This is
    a minor deviation from the spec's "React/Next.js" and avoids a Node server tier.
- **Contracts.** The API is the contract. OpenAPI generated from FastAPI produces typed clients
  (`packages/contracts`). Money travels as decimal strings with currency.
- **Context.** Three workspaces (business, practice, client/investor portal) share one shell. The active
  firm, entity, engagement, period and accounting basis are always visible.
- **Required states.** Every screen implements loading, empty, stale (with freshness), permission-denied,
  partial-success and recovery states. Drafts autosave. Edits carry a version for conflict resolution.
- **The current no-build UI** (`src/agentledger/web`) is retired screen by screen. The backlog slices each
  ticket as one complete user workflow, never "API now, screen later".
- **Testing.** Playwright end-to-end tests per workflow (W01–W10) and axe accessibility checks run in CI
  (Q38).
