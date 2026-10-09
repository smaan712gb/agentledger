/**
 * The contract's types. Every route the API declares request and response models for (src/agentledger/api/schemas.py)
 * is typed from the generated `schema.d.ts`: openapi-typescript over openapi.json, which scripts/export_openapi.py
 * writes from the application itself. A Python change to a JSON shape changes these types, and CI's drift gate
 * fails until openapi.json and schema.d.ts are regenerated and committed (docs/WEB.md).
 *
 * Hand-written types remain only for what the API does not declare: the identity-provider purposes (a plain string
 * query parameter), the engagement stages (CRM rows carry the stage as text) and the error body FastAPI produces.
 *
 * Money and dates travel as strings (decimal strings, ISO dates); the app formats them with Intl and never does
 * float arithmetic on them.
 */

import type { components } from "./schema";

type Schemas = components["schemas"];

// ------------------------------------------------------------------------------------------------ identity

/** GET /api/me (app.py `get_me`): the public account, the session, the two role views and the firm. */
export type Me = Schemas["Me"];

/** The role recorded on the account (platform.py ROLES). */
export type BaseRole = Me["base_role"];

/** The role the rest of the API speaks in: firm staff share the "cpa" view (app.py `_api_user`). */
export type ApiRole = Me["role"];

/** The firm a session belongs to; null for platform administrators. */
export type FirmRef = Schemas["FirmRef"];

/** A firm member as GET /api/auth/users lists them (platform.py `public_user`). */
export type FirmUser = Schemas["FirmUser"];

/** The user object returned next to a fresh session token (platform.py `session_user`). */
export type SessionUser = Schemas["SessionUser"];

/** GET /api/auth/config */
export type AuthConfig = Schemas["AuthConfig"];

export type Identity = "local" | "workos";

export type LoginBody = Schemas["LoginRequest"];
export type MfaBody = Schemas["MfaRequest"];
export type StepUpBody = Schemas["StepUpRequest"];
export type AcceptBody = Schemas["AcceptRequest"];

/** POST /api/auth/login: a second factor is always required; enrolment happens on the first sign-in. */
export type MfaStep = Schemas["MfaStep"];
export type EnrolStep = Schemas["EnrolStep"];
export type LoginStep = MfaStep | EnrolStep;

/** POST /api/auth/mfa */
export type MfaResult = Schemas["MfaResult"];

/** POST /api/auth/accept returns the enrolment step. */
export type AcceptResult = EnrolStep;

export type Ok = Schemas["Ok"];

/** GET /api/auth/idp/start?purpose=... (sets the agentledger_idp cookie). */
export type IdpStart = Schemas["IdpStart"];

/** The `purpose` query parameter of GET /api/auth/idp/start (security/platform.py `idp_begin`). */
export type IdpPurpose = "login" | "invite" | "step_up" | "link";

/** POST /api/auth/invite */
export type InviteBody = Schemas["InviteRequest"];
export type InviteResult = Schemas["InviteResult"];

/** GET /api/auth/events */
export type AuthEvent = Schemas["AuthEvent"];

// ------------------------------------------------------------------------------------------------ platform

/** GET /api/platform/firms: a row of the firms table. */
export type Firm = Schemas["Firm"];

export type FirmStatus = "active" | "provisioning" | "deleting" | (string & {});

export type CreateFirmBody = Schemas["CreateFirmRequest"];

/** POST /api/platform/firms */
export type CreateFirmResult = Schemas["CreateFirmResult"];

/** GET /healthz: liveness and the build that is running (the web app compares it with its own). */
export type Health = Schemas["Health"];

/** POST /api/links: a signed download URL, valid for `expires_in` seconds (120). */
export type SignedLink = Schemas["LinkResult"];

// ------------------------------------------------------------------------------------------------ clients

/** A row of the clients table (ledger/store.py `get_client`/`list_clients`), JSON columns parsed. */
export type Client = Schemas["Client"];

export type ClientKind = Client["kind"];

export type AccountingBasis = "cash" | "accrual";

/** Profile facts are free-form; the ones the app reads by name are declared. `accounting_basis` is never defaulted. */
export type ClientFacts = Schemas["ClientFacts"];

export type CreateClientBody = Schemas["CreateClientRequest"];

/** POST /api/clients */
export type CreateClientResult = Schemas["CreateClientResult"];

/** GET /api/packs: an industry pack a client can be onboarded on. */
export type Pack = Schemas["Pack"];

export type Kpi = Schemas["Kpi"];
export type Balance = Schemas["Balance"];
export type Finding = Schemas["Finding"];
export type FindingResolution = Schemas["FindingResolution"];
export type FindingStatus = Finding["status"];
export type Task = Schemas["Task"];
export type Deadline = Schemas["Deadline"];
export type Opportunity = Schemas["Opportunity"];
export type ChainStatus = Schemas["ChainStatus"];

export type DocumentStatus = "filed" | "needs_review" | (string & {});

/** A document as the client detail and GET /api/clients/{id}/documents list them. */
export type ClientDocument = Schemas["ClientDocument"];

/** GET /api/clients/{id}/documents?cursor=&limit=: one page, newest first; `next_cursor` is null on the last page. */
export type DocumentPage = Schemas["DocumentPage"];

/** GET /api/clients/{id}?year= */
export type ClientDetail = Schemas["ClientDetail"];

/** GET /api/dashboard */
export type Dashboard = Schemas["Dashboard"];

/** PATCH /api/clients/{id}/facts returns the merged facts. */
export type UpdatedFacts = ClientFacts;

// ------------------------------------------------------------------------------------------------ documents

/** A document waiting in the review queue (GET /api/documents/review). */
export type ReviewDocument = Schemas["ReviewDocument"];

/** One result per part of an uploaded file (intake/pipeline.py `ingest`; a zip or email yields several). */
export type IngestedDocument = Schemas["IngestedDocument"];
/** The same bytes were stored before: nothing new was written. Narrow with `"duplicate" in result`. */
export type DuplicateDocument = Schemas["DuplicateDocument"];
export type UploadResult = IngestedDocument | DuplicateDocument;

export type AssignBody = Schemas["AssignRequest"];

/** POST /api/documents/{id}/assign returns the document row after filing. */
export type AssignedDocument = Schemas["AssignedDocument"];

/** GET /api/documents/{id}/versions (the storage locator is withheld). */
export type DocumentVersion = Schemas["DocumentVersion"];

// ------------------------------------------------------------------------------------------------ CRM

export type EngagementStage = "lead" | "proposal" | "engaged" | "in_progress" | "client_review" | "filed" | "closed";

export type Engagement = Schemas["Engagement"];

/** GET /api/crm/pipeline: engagements grouped by stage (every stage is present). */
export type Pipeline = Record<string, Engagement[]>;

// ------------------------------------------------------------------------------------------------ returns
//
// The return routes (app.py, "tax returns") still take and answer untyped dicts, so these shapes are written by hand
// from src/agentledger/returns/{store,facts,filing}.py and workflow/engine.py. docs/WEB.md section 4 lists the models
// the API should declare so that these move into schema.d.ts like everything above. Money is a decimal string.

/** The workflow statuses of RETURN_1040 (returns/store.py). */
export type ReturnStatus =
  | "preparing"
  | "in_review"
  | "approved"
  | "awaiting_signature"
  | "signed"
  | "release_approved"
  | "transmitted"
  | "accepted"
  | "rejected"
  | "paper_filed"
  | "unknown"
  | "void"
  | (string & {});

/** The workflow events (RETURN_1040 transitions); `allowed` lists the ones the status permits. */
export type ReturnEvent =
  | "submit_for_review"
  | "request_changes"
  | "approve"
  | "request_signature"
  | "signed"
  | "approve_release"
  | "transmit"
  | "ack_accepted"
  | "ack_rejected"
  | "correct"
  | "mark_paper_filed"
  | "reopen"
  | "void"
  | "outcome_unknown"
  | "reconciled_submitted"
  | "reconciled_not_submitted"
  | (string & {});

/** A row of tax_returns. */
export interface ReturnRow {
  id: string;
  client_id: string;
  tax_year: number;
  form: string;
  created_at: string;
  created_by: string;
  /** The filed return this one amends (a Form 1040-X), else null. */
  amends: string | null;
}

/** Result.summary (returns/individual.py): empty until the return is computed. */
export type ReturnSummaryFigures = Partial<
  Record<"agi" | "taxable_income" | "total_tax" | "payments" | "refund" | "amount_owed", string>
>;

/** GET /api/clients/{id}/returns: the row, its status, latest version and summary. */
export interface ReturnListItem extends ReturnRow {
  status: ReturnStatus;
  version: number;
  summary: ReturnSummaryFigures;
}

/**
 * The stored inputs: an IndividualReturn as JSON (return-schema.json is its schema). Keys the model defaults may be
 * absent; a list item from a document carries `source_document`, its identity (returns/facts.py IDENTITY).
 */
export type ReturnInputs = Record<string, unknown>;

export interface CreateReturnBody {
  tax_year: number;
  inputs?: ReturnInputs;
}

export interface CreateReturnResult {
  id: string;
}

/** One event of the return's hash-chained stream (workflow/engine.py State.history). */
export interface WorkflowEvent {
  seq: number;
  event: ReturnEvent;
  actor: string;
  at: string;
  /** The status after this event. */
  status: ReturnStatus;
  note: string;
}

export type ProvenanceSource = "document" | "preparer" | "resolution" | "return" | (string & {});

export interface ProvenanceDocument {
  document_id: string;
  box: string;
  value: string;
}

/** Where a value came from (returns/facts.py, returns/documents.py), keyed by the positional path (`w2s[0].wages`). */
export interface Provenance {
  /** Absent for a preparer's entry; "document" when a document gave the value. */
  source?: ProvenanceSource;
  document_id?: string;
  box?: string | null;
  value?: string;
  confirmed?: boolean;
  confirmed_by?: string;
  /** A summed amount (a 1098 total) names every document behind it. */
  documents?: ProvenanceDocument[];
  /** A preparer's edit over a document value keeps what the document said. */
  edited_by?: string;
  previous_document?: string | null;
  previous_value?: string | null;
  previous_source?: string;
  /** A conflict resolved by taking the document's value. */
  resolved_by?: string;
  /** A prior-year roll-forward names the return and version it came from. */
  return_id?: string;
  version?: number;
}

export type DiagnosticSeverity = "error" | "warning" | "info" | (string & {});

/** returns/sheet.py Diagnostic; an "error" blocks review and filing. */
export interface ReturnDiagnostic {
  severity: DiagnosticSeverity;
  code: string;
  message: string;
  form: string | null;
  line: string | null;
}

export interface CoverageBlocker {
  form: string;
  status: string;
  need: string;
  limits?: string[];
  jurisdiction?: string;
  flag?: string;
}

export interface ReturnCoverage {
  forms: Record<string, string>;
  lowest: string;
  flags: unknown[];
  below_preparation: CoverageBlocker[];
  filing_blockers: CoverageBlocker[];
}

/** Result.to_dict() plus the store's `coverage` and `pinned` (returns/individual.py, returns/store.py). */
export interface ReturnResult {
  tax_year: number;
  filing_status: string;
  /** Every computed form line, as decimal strings: forms.f1040["11a"]. */
  forms: Record<string, Record<string, string>>;
  summary: ReturnSummaryFigures;
  diagnostics: ReturnDiagnostic[];
  coverage?: ReturnCoverage;
  pinned?: { kb_version: string; engine: string };
  carryforwards?: Record<string, string>;
  facts?: Record<string, unknown>;
  notes?: unknown;
  sources?: unknown[];
}

export interface CrosscheckDiscrepancy {
  item: string;
  agentledger: string;
  policyengine: string;
}

/** The independent cross-check of a computation (returns/oracle.py), stored with the version when asked for. */
export interface Crosscheck {
  status: "agree" | "differ" | "unavailable" | (string & {});
  compared?: number;
  unmodelled?: string[];
  discrepancies?: CrosscheckDiscrepancy[];
}

export type SubmissionStatus =
  | "queued"
  | "transmitted"
  | "accepted"
  | "rejected"
  | "unknown"
  | "cancelled"
  | "superseded"
  | (string & {});

/** One electronic submission of a return to one jurisdiction (returns/filing.py `_public`). */
export interface FilingSubmission {
  id: string;
  return_id: string;
  jurisdiction: string;
  kind: "original" | "retransmission";
  status: SubmissionStatus;
  attempt: number;
  supersedes: string | null;
  linked_to: string | null;
  package_hash: string;
  planned_submission_id: string | null;
  provider_submission_id: string | null;
  rejection_codes: string[];
  created_at: string;
  updated_at: string;
}

export interface FilingRelease {
  approved_by: string | null;
  at: string | null;
  hash: string | null;
  jurisdictions: string[];
}

/** `filing` on GET /api/returns/{rid} (Filing.summary): the release, every submission, and whether all are accepted. */
export interface FilingSummary {
  status: ReturnStatus;
  release: FilingRelease | null;
  submissions: FilingSubmission[];
  complete: boolean;
}

/** GET /api/returns/{rid}. Firm staff get the working papers (inputs, provenance, result); a client gets `forms`. */
export interface ReturnDetail {
  return: ReturnRow;
  version: number;
  status: ReturnStatus;
  history: WorkflowEvent[];
  summary: ReturnSummaryFigures;
  allowed: ReturnEvent[];
  /** What the world must supply in this status (RETURN_1040.waiting), else null. */
  waiting_on: string | null;
  crosscheck: Crosscheck | null;
  filing: FilingSummary;
  inputs?: ReturnInputs;
  provenance?: Record<string, Provenance>;
  result?: ReturnResult | null;
  forms?: { f1040: Record<string, string> };
}

/** An issue populate reports (returns/documents.py, returns/facts.py): never guessed, a person decides. */
export interface PopulateIssue {
  document_id: string | null;
  code: string;
  message: string;
  blocking?: boolean | string;
  path?: string;
  anchor?: string;
}

/** POST /api/returns/{rid}/populate */
export interface PopulateResult {
  documents: string[];
  issues: PopulateIssue[];
  fields: number;
  conflicts: number;
  superseded: number;
}

export type ConflictChoice = "keep" | "document";

/**
 * An open fact conflict (returns/facts.py): a document disagrees with what the return holds. Anchors starting with
 * `missing:` (a required amount nobody entered) and `orphan:` (an item whose document left the return) are questions
 * only "keep" can answer, after the person acted.
 */
export interface FactConflict {
  id: number;
  return_id: string;
  path: string;
  anchor: string;
  document_id: string;
  box: string | null;
  current_value: unknown;
  proposed_value: unknown;
  current_source: string;
  raised_by: string;
  raised_at: string;
  resolution: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  note: string | null;
}

export interface ResolveConflictBody {
  choice: ConflictChoice;
  note?: string;
}

export interface ResolveConflictResult {
  open: number;
}

/** GET /api/returns/{rid}/facts?path=<anchor>: every value the field has had (append-only). */
export interface FactAssertion {
  id: number;
  return_id: string;
  path: string;
  value: unknown;
  source: string;
  source_ref: string | null;
  asserted_by: string;
  asserted_at: string;
  supersedes: number | null;
}

export type DispositionKind = "entered_by_hand" | "not_applicable";

export interface DispositionBody {
  disposition: DispositionKind;
  /** At least ten characters: the reason is part of the approved package. */
  note: string;
}

/** POST /api/returns/{rid}/documents/{id}/disposition answers with every disposition of the return. */
export interface Disposition {
  id: number;
  return_id: string;
  document_id: string;
  disposition: DispositionKind;
  note: string;
  actor: string;
  at: string;
}

export interface ConfirmResult {
  confirmed: number;
}

/** GET /api/returns/{rid}/recalculation-preview: today's rules against the stored inputs; nothing is stored. */
export interface RecalculationPreview extends ReturnResult {
  changes_vs_latest: Record<string, { latest: string | null | undefined; now: string }>;
}

/** POST /api/returns/{rid}/{action} */
export type ReturnAction =
  | "submit"
  | "approve"
  | "request-changes"
  | "request-signature"
  | "release-approve"
  | "reconcile"
  | "retransmit";

export interface ReturnActionBody {
  /** submit: required when the cross-check disagrees. */
  explanation?: string;
  /** request-changes */
  note?: string;
  /** release-approve: "US-FED" alone by default. */
  jurisdictions?: string[];
  /** reconcile: one of this return's submission ids; without it, the return's federal transmission. */
  submission?: string;
  submitted?: boolean;
  /** reconcile: the provider's id; retransmit: the rejected submission to replace. */
  submission_id?: string;
  evidence?: string;
}

export interface ReturnActionResult {
  status: ReturnStatus;
  history: WorkflowEvent[];
  filing?: FilingSummary;
  submission?: FilingSubmission & { submission_id: string; return_status: ReturnStatus };
}

export interface VoidResult {
  status: ReturnStatus;
}

export interface AmendResult {
  id: string;
}

// ------------------------------------------------------------------------------------------------ errors

/** One entry of a 422 body: pydantic's `errors()`. */
export type ValidationError = Schemas["ValidationError"];

/** Every error body FastAPI produces here: `{"detail": "text"}` or `{"detail": [validation errors]}`. */
export interface ApiErrorBody {
  detail: string | ValidationError[];
}
