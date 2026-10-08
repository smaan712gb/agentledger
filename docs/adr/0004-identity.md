# ADR-0004: External OIDC identity; authorization stays in the domain

Status: Accepted. IdP: WorkOS AuthKit (revised 2026-10-08; Zitadel dropped on cost and agent fit). Self-host profile: Better Auth.
Spec references: §4 (identity), §5 C01, §12 (approval binding), §14 (RBAC + attributes), Q12–Q15.

## Decision

- **Authentication goes to an OIDC provider**, not to code we maintain:
  - password, MFA, passkeys/WebAuthn, recovery and breach detection
  - enterprise federation (SAML or OIDC) for firms on Entra ID or Google Workspace
- **Platform staff** reach admin and support consoles through Cloudflare Access, with device posture checks
  and time-limited support grants.
- **AgentLedger authorizes every request itself:**
  - memberships (user in firm, with job role)
  - engagement grants (user or role on an entity and service, with periods and data classes)
  - client-user grants
  - investor restrictions
  - delegated bookkeeper access
  - external auditor read-only access
  - service identities for workers

  The server derives scope from identity and grants and never trusts a caller-supplied tenant or entity id
  (spec §15).
- **Approvals** bind tenant, entity, actor, action type, destination, amount, input version, payload hash,
  expiry and policy version, and are re-checked just before execution.
- **The current Argon2id + TOTP implementation** (`src/agentledger/security/platform.py`) remains as the
  **self-hosted profile** and as the test double for the OIDC contract. Sessions, the auth event log and
  invitations keep their semantics. Sign-in itself moves to the IdP.
- **Step-up authentication** (a fresh MFA within N minutes) is required for releasing returns, approving
  payments, changing vendor bank details, granting access and exporting firm data.

## Why WorkOS AuthKit

- **Cost.** Free up to 1M MAU, including MFA, passkeys and organizations. Enterprise SSO costs $125 per
  connection per month at low volume, passed through to the firms that ask for it.
- **B2B model.** Organizations map to firms, and SCIM directory sync covers larger firms.
- **Agentic access.** It works as the authorization server for AgentLedger's remote MCP server on Cloudflare
  (`workers-oauth-provider`). A user's own AI agent (Claude, ChatGPT and others) connects with OAuth and gets
  exactly that user's grants, no more.
- **Self-host profile.** Better Auth: an open-source TypeScript library that runs in Workers on our own
  PostgreSQL, with organization, two-factor, passkey and SSO plugins. It replaces the hand-written TOTP
  stack there.

Status (2026-10-08): implemented in `security/workos.py` and `Platform.idp_begin/idp_complete`. WorkOS proves the
person; AgentLedger keeps membership, role and authority. A WorkOS identity creates an account only through an
invitation whose verified email matches, or is linked from an already signed-in local session; never by matching
email alone. Every sign-in must be multi-factor: a passkey, a TOTP factor enrolled at WorkOS (checked through the
auth-factors API), or SSO from an organization whose identity provider is recorded as enforcing MFA (WorkOS's MFA
requirement does not apply to SSO users). Impersonated sessions are refused, and the callback must return to the
browser that started the sign-in. Freshness is the verified access token's `auth_time` (RS256, WorkOS JWKS), never the
moment our session was created. Consequential actions, including granting access, require a sign-in or step-up
within five minutes: WorkOS re-authentication with `max_age=0` (whose `auth_time` must be fresh), or the local TOTP
code. With `AGENTLEDGER_IDENTITY=workos` the password stack remains only for platform administrators (break-glass).
