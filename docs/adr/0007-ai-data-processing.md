# ADR-0007: Hybrid AI data-processing profile

Status: Accepted (owner chose hybrid with consent, 2026-10-08). Spec references: §12, §14 (§7216, inference
gateway).

## Decision

Every model call goes through one inference gateway in the domain API. It evaluates:

- purpose
- data classification
- tenant consent
- provider and region
- retention and training terms
- budget

| Data class | Allowed models |
|---|---|
| Public (rules, published guidance) | Any configured model |
| Firm operational, no tax return information | Workers AI open models. Claude through AI Gateway within the firm's budget. |
| Tax return information (§7216) | Workers AI open models by default. Claude only when a §7216 consent covering that purpose is on file for the taxpayer, the provider's zero-retention configuration is verified for our account, and only the minimum necessary fields are sent. |
| PHI (healthcare clients) | Only after a BAA covers that provider. Otherwise in-platform models, or deterministic code only. |

- Redaction is defense in depth, not the legal basis (spec §14).
- Every call records purpose, data class, consent id, model, token counts and cost, without the raw prompt
  when the data is sensitive.
- AI never posts, approves, files or pays. It produces typed proposals that deterministic checks and
  authorized people act on (spec §12, autonomy levels).
- When models are unavailable, the system shows a visible pause and the manual workflow stays usable. It
  never silently substitutes an unreviewed model (Q34).
