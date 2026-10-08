# Threat model

Spec §14 lists the required threat cases. For each: the asset at risk, the control (deterministic first),
the test that proves it (Q-ids from spec §19), and today's state.

Assets: taxpayer identifiers and return information (§7216), bank details, ledger integrity, filing and
payment authority, firm and investor confidentiality, credentials and keys.

| # | Threat | Controls | Proof | State today |
|---|---|---|---|---|
| T1 | Cross-tenant access (firm A reads firm B) | Database per firm; connection chosen from the authenticated identity, never from request input; RLS inside the firm for engagement grants; signed download links bound to user, firm and path | Q12 | Built for firms (`tests/test_tenancy.py`: API, exports, links, deletion). RLS for engagement grants: todo (PostgreSQL port). |
| T2 | Cross-client or cross-investor access inside a firm | Client users scoped to one client; engagement grants per staff member; investor restrictions | Q12, Q13 | Client scoping built; staff engagement grants and investors todo |
| T3 | Malicious uploaded PDF or email | Malware scan before extraction; parsing in isolated, egress-blocked workers; previews rendered in a sandbox | Q11 | Parsing is in-process today. Isolation todo. |
| T4 | Prompt injection in documents | Document text is data, never instructions (intake prompt says so); models have no tools that mutate state; every mutation is a typed proposal checked deterministically; answers verified number by number | Q11 | Partially built: grounding checks, no model write access. Adversarial test suite todo. |
| T5 | Poisoned vendor bank instructions | Bank detail changes only through a verified-channel workflow with separate approval; invoice text can never change a payment destination | Q14 | Not built (no payments yet) |
| T6 | Stolen OAuth token or session | Hashed session tokens, idle and absolute expiry, revocation on disable; short-lived provider tokens through a credential broker, never in prompts | Q13, Q35 | Sessions built; credential broker todo |
| T7 | Rogue plugin or skill | Signed packages, declared permissions, sandboxed runtime, canary rollout, revocation | Q36 | Manifests and permission declarations exist; signing and sandbox todo |
| T8 | Model provider leakage | Inference gateway with data-class policy (ADR-0007); consent checks; zero-retention verification; minimum-necessary payloads | (policy tests) | Redaction and consent check in Ask; gateway policy todo |
| T9 | Report or file URL exposure | No bearer tokens in URLs; signed links expire in 120 s and are bound to user, firm and path | Q12 | Built |
| T10 | Support impersonation | Support access only through time-limited, logged grants under Cloudflare Access; platform admins cannot read client data (403) | Q12 | Platform/firm separation built; support grants todo |
| T11 | Credential replay (TOTP or password) | One-time-code replay protection, lockout, Argon2id; IdP-side controls after ADR-0004 | (security tests) | Built (`tests/test_security.py`) |
| T12 | Backup compromise | Backups contain only firm-key ciphertext for documents and returns; KMS-wrapped master key; restore drills | Q33 | Envelope encryption built; backups and drills todo |
| T13 | Tampering by a database administrator | Hash-chained ledger, audit and workflow events; daily chain heads anchored in R2 with a bucket lock | (audit verify) | Chains built; external anchoring todo |
| T14 | Duplicate or blind retransmission of a return or payment | Command ids, exactly-once activities, an `unknown` state reconciled before any retry | Q02, Q23, Q25 | Exactly-once transmission built; `unknown` state todo |
| T15 | AI approving its own proposals or self-granting access | Approvals need a human principal different from the proposer; agents have service identities without grant rights | Q15 | Foundry policy built; formal service identities todo |
| T16 | Stale approval executed after change | Approval bound to payload hash and version, re-checked at execution | Q15 | Built for returns (`approved_hash`, `reopen`) |
| T17 | Unsupported tax situation silently approximated | Coverage registry enforced; blocking diagnostics | Q19 | Built |
| T18 | Insider exports a firm's data after offboarding | Grants re-checked before every action, including queued jobs; offboarding stops workflows | Q13 | Firm deletion built; per-user queued-job re-check todo |
