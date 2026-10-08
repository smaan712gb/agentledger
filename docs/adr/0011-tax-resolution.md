# ADR-0011: Tax Monitoring and Resolution module (compliance + resolution on one record)

Status: Accepted (owner direction 2026-10-08, revised the same day with the owner's analysis). Spec references:
§5 C18–C20, §10 (service packs), §14 (permissions), W09 (notice and deadline management).

## Product thesis

Professional tax suites are built for **compliance**: staying out of trouble. Resolution platforms are built for
**resolution**: getting out of trouble. AgentLedger does both on **one client record**. The advantage is not
more agents; it is the **connected workflow**. Notices, transcripts and collection activity are reconciled
against the client's own filed returns, payments and accounting records, with evidence.

New buyers: enrolled agents, tax-resolution practices and tax attorneys. For CPA firms, it is a year-round
monitoring service.

## Who does what

| Responsibility | Owner |
|---|---|
| Gathering documents, explaining changes, drafting correspondence, coordinating work | Agents |
| Calculations, eligibility rules, dates, permissions, financial reconciliation | Versioned deterministic software |
| Choosing a consequential resolution strategy, representing, approving submissions | The authorized practitioner, or the client where applicable |

## Permissions are modelled exactly

- **Form 8821** (tax information authorization) permits access to specified information. It does **not**
  permit representation.
- **Form 2848** (power of attorney) permits representation for the listed matters, periods and
  representative.
- An `Authorization` record holds: form, taxpayer, representative (CAF), covered tax matters and periods,
  signature dates, recording status and revocation.
- Every transcript retrieval, IRS contact and submission checks the authorization that covers it. Transcript
  Delivery System access also needs the practitioner's own e-Services eligibility.

## Build order

1. **Notice intake and case management.**
   - IRS and state notices at intake, with taxpayer, period, notice type, issue and response deadline
     extracted and *verified*.
   - A resolution case, evidence gathering and a response checklist.
   - Reviewed response drafts; practitioner approval; submission and delivery evidence; outcome monitoring.
2. **Authorized transcript monitoring.**
   - Transcripts retrieved through legitimate, permitted access (file upload from day one).
   - Every snapshot versioned; meaningful changes detected; practice-wide exception queues.
   - **IRS-to-records reconciliation:** the agency's account compared with filed returns, payments and
     accounting records, with the discrepancy classified (timing, misapplied payment, unreported income,
     unexplained) and explained with evidence.
3. **Resolution calculations and packages.**
   - Supported installment agreements, offers in compromise (eligibility conditions *and*
     reasonable-collection-potential analysis), currently-not-collectible, penalty relief (first-time
     abatement eligibility, reasonable-cause drafts, Form 843).
   - Collection information statements (433 series) assembled from verified records, with requests for
     missing personal facts and inconsistency flags.
   - IRS Collection Financial Standards as effective-dated rules kept current by RegWatch.
   - Forms 9465 and 656; a comparison for practitioner review. A recommendation never implies the IRS will
     accept it.
4. **Advanced controversy, after specialist validation.**
   - **Collection statute expiration (CSED):** computed from assessment history plus every suspension and
     extension event, with an explicit incomplete-history warning. It is not "tax year plus ten years".
   - Bankruptcy dischargeability.
   - Appeals and CDP hearings.
   - Innocent-spouse relief.

After resolution, **ongoing compliance** tracks the payment and filing obligations the agreement requires, and
sends reminders under an approved communication policy.

## Domain records (added to the firm database)

| Record | Holds |
|---|---|
| `Authorization` | 8821 or 2848, scope, periods, CAF, status, revocation |
| `TranscriptSnapshot` | Source file, retrieval channel, as-of date, parsed transactions, version links |
| `Assessment` | Tax period, type, amount, 23C date, related codes |
| `Notice` | Agency, type (CP/LT…), period, amounts, issue, verified response deadline, source document |
| `ResolutionCase` | Issue, strategy options, chosen strategy and approver, status |
| `CollectionEvent` | Levy, lien, IA, OIC, CNC, bankruptcy and CDP events, with dates for CSED tolling |

Notices, transcripts and cases reuse the existing CRM engagements, document vault, workflow engine,
permissions and client portal. There is no separate resolution CRM.

## Claims we make, and claims we don't

- We describe **observed transcript changes and potential issues**. We do not promise that every audit can be
  predicted or detected months ahead.
- We assume **no API from IRS Solutions or any other vendor**. Any partnership needs commercial and technical
  confirmation first, and is recorded on the provider-access register.
