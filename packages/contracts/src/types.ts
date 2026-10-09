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

// ------------------------------------------------------------------------------------------------ errors

/** One entry of a 422 body: pydantic's `errors()`. */
export type ValidationError = Schemas["ValidationError"];

/** Every error body FastAPI produces here: `{"detail": "text"}` or `{"detail": [validation errors]}`. */
export interface ApiErrorBody {
  detail: string | ValidationError[];
}
