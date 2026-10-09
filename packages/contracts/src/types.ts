/**
 * Hand-maintained types for the JSON the API returns today.
 *
 * Every handler in src/agentledger/api/app.py is declared `-> dict[str, Any]` (or `list[dict[str, Any]]`), so the
 * OpenAPI document in ../openapi.json carries no response schemas and `npm run generate` produces `schema.d.ts`
 * with `unknown` bodies. These types were derived by reading app.py, security/platform.py, ledger/store.py,
 * intake/pipeline.py and the no-build UI (src/agentledger/web/app.js). When the API grows pydantic response models
 * and operationIds (docs/WEB.md, "API changes the next slice needs"), `schema.d.ts` replaces this file and
 * `endpoints.ts` switches to openapi-fetch's typed `paths`.
 *
 * Money and dates travel as strings (decimal strings, ISO dates); the app formats them with Intl and never does
 * float arithmetic on them.
 */

// ------------------------------------------------------------------------------------------------ identity

/** The role recorded on the account (platform.py ROLES). */
export type BaseRole = "platform_admin" | "firm_admin" | "cpa" | "staff" | "client";

/** The role the rest of the API speaks in: firm staff share the "cpa" view (app.py `_api_user`). */
export type ApiRole = "cpa" | "client" | "platform_admin";

/** SQLite booleans arrive as 0/1; Python booleans as true/false. Both occur in the same payloads. */
export type Flag = boolean | 0 | 1;

/** GET /api/me (app.py `get_me`): `public_user` + session fields + the two role views. */
export interface Me {
  id: string;
  firm_id: string;
  email: string;
  name: string;
  role: ApiRole;
  base_role: BaseRole;
  client_id: string | null;
  reviewer: boolean;
  /** Present for staff only: the clients they are engaged on. */
  engaged?: string[];
  auth_method: string;
  fresh_at: string;
  session_id: string;
  mfa_enrolled_at: string | null;
  disabled: Flag;
  last_login_at: string | null;
}

/** A firm member as GET /api/auth/users lists them (platform.py `public_user`). */
export interface FirmUser {
  id: string;
  firm_id: string;
  email: string;
  name: string;
  role: BaseRole;
  client_id: string | null;
  mfa_enrolled_at: string | null;
  disabled: Flag;
  last_login_at: string | null;
  reviewer: boolean;
  engaged?: string[];
}

/** The user object returned next to a fresh session token (platform.py `session_user`, before `_api_user`). */
export interface SessionUser extends FirmUser {
  session_id: string;
  auth_method: string;
  fresh_at: string;
}

export type Identity = "local" | "workos";

/** GET /api/auth/config */
export interface AuthConfig {
  identity: Identity;
  password_sign_in: boolean | "platform administrators only";
}

/** POST /api/auth/login: a second factor is always required; enrolment happens on the first sign-in. */
export type LoginStep =
  { next: "mfa"; challenge: string } | { next: "enroll"; challenge: string; secret: string; otpauth_uri: string };

export type EnrolStep = Extract<LoginStep, { next: "enroll" }>;

/** POST /api/auth/mfa */
export interface MfaResult {
  token: string;
  user: SessionUser;
}

/** POST /api/auth/accept returns the enrolment step. */
export type AcceptResult = EnrolStep;

export interface Ok {
  ok: true;
}

/** GET /api/auth/idp/start?purpose=... (sets the agentledger_idp cookie). */
export interface IdpStart {
  url: string;
}

export type IdpPurpose = "login" | "invite" | "step_up" | "link";

/** POST /api/auth/invite */
export interface InviteResult {
  invite_token: string;
  expires_in_days: number;
}

export interface InviteBody {
  email: string;
  role: Exclude<BaseRole, "platform_admin">;
  client_id?: string;
  firm_id?: string;
}

/** GET /api/auth/events */
export interface AuthEvent {
  id: number;
  at: string;
  firm_id: string | null;
  user_id: string | null;
  email: string | null;
  event: string;
  ip: string | null;
  detail: string | null;
}

// ------------------------------------------------------------------------------------------------ platform

export type FirmStatus = "active" | "provisioning" | "deleting" | (string & {});

/** GET /api/platform/firms: a row of the firms table. */
export interface Firm {
  id: string;
  name: string;
  status: FirmStatus;
  created_at: string;
  deleted_at: string | null;
  blob_location?: string | null;
}

export interface CreateFirmBody {
  id: string;
  name: string;
  admin_email: string;
}

/** POST /api/platform/firms */
export interface CreateFirmResult {
  firm: Firm;
  admin_invite_token: string;
}

/** GET /healthz: liveness and the build that is running (the web app compares it with its own). */
export interface Health {
  ok: boolean;
  build: string;
  backend: string;
}

/** POST /api/links: a signed download URL, valid for `expires_in` seconds (120). */
export interface SignedLink {
  url: string;
  expires_in: number;
}

// ------------------------------------------------------------------------------------------------ clients

export type ClientKind = "business" | "individual";

export type AccountingBasis = "cash" | "accrual";

/** A row of the clients table (ledger/store.py `get_client`/`list_clients`), JSON columns parsed. */
export interface Client {
  id: string;
  name: string;
  kind: ClientKind;
  entity_type: string | null;
  formed_under: string;
  tax_id_last4: string | null;
  emails: string[];
  aliases: string[];
  consent_7216_at: string | null;
  /** ISO date; postings on or before it are frozen (ledger/store.py ClosedPeriod). */
  closed_through: string | null;
  domain: string;
  facts: ClientFacts;
  created_at: string;
}

/** Profile facts are free-form; the ones the app reads by name are listed. `accounting_basis` is never defaulted. */
export interface ClientFacts {
  accounting_basis?: AccountingBasis | string;
  state?: string;
  employees?: number | string;
  [key: string]: unknown;
}

export interface CreateClientBody {
  id: string;
  name: string;
  kind?: ClientKind;
  entity_type?: string | null;
  formed_under?: string;
  tax_id_last4?: string | null;
  emails?: string[];
  aliases?: string[];
  consent_7216_at?: string | null;
  domain?: string;
  facts?: ClientFacts;
}

/** POST /api/clients */
export interface CreateClientResult {
  id: string;
  accounts_created: number;
}

export interface Kpi {
  title: string;
  value: string | number | null;
  unit: string;
  missing?: string | null;
}

export type FindingStatus = "open" | "explained" | "corrected" | "accepted_risk" | (string & {});

export interface FindingResolution {
  actor: string;
  role: string;
  action: string;
  note: string;
  at: string;
}

export interface Finding {
  id: string;
  client_id?: string;
  check_id: string;
  severity: "critical" | "high" | "medium" | "low" | "info" | (string & {});
  title: string;
  detail: string;
  citation: string | null;
  owner: "client" | "cpa" | "both" | (string & {});
  status: FindingStatus;
  resolutions: FindingResolution[];
}

export interface Task {
  id: number;
  client_id: string | null;
  client_name?: string | null;
  engagement_id?: number | null;
  title: string;
  detail: string;
  assignee: string;
  status: string;
  source: string;
  due: string | null;
}

export interface Deadline {
  title: string;
  due: string;
  days: number;
  [key: string]: unknown;
}

export interface Opportunity {
  title: string;
  category: "risk" | "planning" | (string & {});
  status: "applies" | "needs_facts" | (string & {});
  summary: string;
  missing_fact?: string | null;
  aggressiveness?: string;
  [key: string]: unknown;
}

export type ChainStatus = { ok: true; checked: number } | { ok: false; broken_at: number | string };

export type DocumentStatus = "filed" | "needs_review" | (string & {});

/** A document as the client detail lists them (app.py `client_detail`, up to 100 newest). */
export interface ClientDocument {
  id: string;
  original_name: string;
  doc_type: string | null;
  tax_year: number | null;
  status: DocumentStatus;
  confidence: number | null;
  vault_path: string | null;
  summary: string | null;
  classified_by: string | null;
  received_at: string;
  channel: string;
}

/** GET /api/clients/{id}?year= */
export interface ClientDetail {
  client: Client;
  year: number;
  pack: { id: string; title: string; description: string; facts: string[] } | null;
  balances: unknown;
  kpis: Kpi[];
  findings: Finding[];
  documents: ClientDocument[];
  tasks: Task[];
  deadlines: Deadline[];
  opportunities: Opportunity[];
  chain: ChainStatus;
  integrity: number;
  /** Business clients only. */
  m1?: unknown;
  ar?: unknown;
  vendors_1099?: unknown;
  deals?: unknown;
}

/** PATCH /api/clients/{id}/facts returns the merged facts. */
export type UpdatedFacts = ClientFacts;

// ------------------------------------------------------------------------------------------------ documents

/** A document waiting in the review queue (GET /api/documents/review). */
export interface ReviewDocument {
  id: string;
  original_name: string;
  doc_type: string | null;
  tax_year: number | null;
  confidence: number | null;
  summary: string | null;
  sender: string | null;
  received_at: string;
  channel: string;
  classified_by: string | null;
}

/** One result per part of an uploaded file (intake/pipeline.py `ingest`; a zip or email yields several). */
export type UploadResult =
  | {
      id: string;
      client_id: string | null;
      status: DocumentStatus;
      doc_type: string;
      tax_year: number | null;
      confidence: number;
      vault_path: string;
      retention_class: string | null;
      match: string;
      name: string;
      suggested_client: string | null;
      duplicate?: undefined;
    }
  | {
      /** The same bytes were stored before: nothing new was written. */
      duplicate: true;
      id: string;
      client_id: string | null;
      status: DocumentStatus;
      vault_path: string | null;
      name: string;
    };

/** POST /api/documents/{id}/assign returns the document row after filing. */
export interface AssignedDocument {
  id: string;
  client_id: string | null;
  status: DocumentStatus;
  [key: string]: unknown;
}

/** GET /api/documents/{id}/versions (the storage locator is withheld). */
export interface DocumentVersion {
  document_id: string;
  version: number;
  sha256: string;
  size: number;
  created_at: string;
  created_by: string;
  reason: string;
}

// ------------------------------------------------------------------------------------------------ CRM

export type EngagementStage = "lead" | "proposal" | "engaged" | "in_progress" | "client_review" | "filed" | "closed";

export interface Engagement {
  id: number;
  client_id: string;
  client_name: string;
  type: string;
  tax_year: number | null;
  owner: string | null;
  due_date: string | null;
  fee: string | number | null;
  stage: EngagementStage;
  open_tasks: number;
}

/** GET /api/crm/pipeline: engagements grouped by stage (every stage is present). */
export type Pipeline = Record<EngagementStage, Engagement[]>;

// ------------------------------------------------------------------------------------------------ errors

/** One entry of a 422 body: pydantic's `errors()`. */
export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
}

/** Every error body FastAPI produces here: `{"detail": "text"}` or `{"detail": [validation errors]}`. */
export interface ApiErrorBody {
  detail: string | ValidationError[];
}
