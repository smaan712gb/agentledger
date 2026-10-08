# ADR-0004: External OIDC identity; authorization stays in the domain

Status: Accepted. IdP vendor proposed: Zitadel. Owner to confirm.
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
- **The current Argon2id + TOTP implementation** (`src/veritas/security/platform.py`) remains as the
  **self-hosted profile** and as the test double for the OIDC contract. Sessions, the auth event log and
  invitations keep their semantics. Sign-in itself moves to the IdP.
- **Step-up authentication** (a fresh MFA within N minutes) is required for releasing returns, approving
  payments, changing vendor bank details, granting access and exporting firm data.
