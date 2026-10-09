"""Request and response models of the HTTP API (pydantic v2). `scripts/export_openapi.py` writes what they declare to
packages/contracts/openapi.json, from which the web app's TypeScript types are generated (docs/WEB.md).

Conventions:

- Money and dates travel as strings (the handlers' `jsonable()`), never as JSON numbers that lose precision.
- A model that is a table row, or carries one (`Row`), lets unknown keys through: a column the models do not know yet
  is passed on, not filtered out. A model the handler assembles key by key (`Shape`) declares every key it has.
- A key a handler sets only in some cases (staff engagements on `Me`, the business-only parts of a client detail,
  the CPA-only parts of the dashboard) is optional here, and that route serializes with
  `response_model_exclude_unset=True`, so the JSON keeps today's key set exactly: absent, not null.
- Request models validate what the handlers used to read with `body[...]`; a missing or malformed field is a 422 with
  the field named, where it used to be a 500 or a confusing 401/403.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

BaseRole = Literal["platform_admin", "firm_admin", "cpa", "staff", "client"]
"""The role recorded on an account (security/platform.py ROLES)."""

ApiRole = Literal["cpa", "client", "platform_admin"]
"""The role the rest of the API speaks in: firm staff share the CPA view (app.py `_api_user`)."""

CLIENT_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
"""The clients table's rule on PostgreSQL (pg/migrations/0001_ledger_core.sql); SQLite stores get the same check here."""


class Shape(BaseModel):
    """A payload the handler assembles key by key: every key it carries is declared."""

    model_config = ConfigDict(extra="ignore")


class Row(BaseModel):
    """A payload that is a table row, or carries one: columns the models do not declare pass through."""

    model_config = ConfigDict(extra="allow")


# ------------------------------------------------------------------------------ authentication

class AuthConfig(Shape):
    identity: str = Field(description="AGENTLEDGER_IDENTITY: 'local' (password + one-time code) or 'workos'")
    password_sign_in: bool | str = Field(description="true, or 'platform administrators only' when firms sign in through the provider")


class LoginRequest(BaseModel):
    email: str
    password: str


class MfaStep(Shape):
    """A second factor is always required: the account is enrolled, enter the current code."""

    next: Literal["mfa"]
    challenge: str


class EnrolStep(Shape):
    """The account's first sign-in (or an accepted invitation): enrol the authenticator with this secret."""

    next: Literal["enroll"]
    challenge: str
    secret: str
    otpauth_uri: str


class MfaRequest(BaseModel):
    challenge: str
    code: str


class StepUpRequest(BaseModel):
    code: str


class AcceptRequest(BaseModel):
    token: str
    name: str
    password: str


class FirmUser(Row):
    """A firm member as GET /api/auth/users lists them (platform.py `public_user`)."""

    id: str
    firm_id: str
    email: str
    name: str
    role: BaseRole
    client_id: str | None
    mfa_enrolled_at: str | None
    disabled: bool
    last_login_at: str | None
    reviewer: bool
    engaged: list[str] | None = Field(default=None, description="staff only: the clients they are engaged on")


class SessionUser(FirmUser):
    """The person a fresh session belongs to (platform.py `session_user`)."""

    session_id: str
    auth_method: str | None = None
    fresh_at: str | None = None


class MfaResult(Shape):
    token: str
    user: SessionUser | None = None


class Ok(Shape):
    ok: bool = True


class IdpStart(Shape):
    url: str = Field(description="the identity provider's hosted sign-in page; the agentledger_idp cookie binds the browser")


class InviteRequest(BaseModel):
    email: str
    role: Literal["firm_admin", "cpa", "staff", "client"]
    client_id: str | None = Field(default=None, description="required for a client user")
    firm_id: str | None = Field(default=None, description="platform administrators may name the firm; members invite into their own")


class InviteResult(Shape):
    invite_token: str
    expires_in_days: int


class DisableRequest(BaseModel):
    disabled: bool = True


class AuthEvent(Row):
    id: int
    at: str
    firm_id: str | None = None
    user_id: str | None = None
    email: str | None = None
    event: str
    ip: str | None = None
    detail: str | None = None


class FirmRef(Shape):
    """The firm a session belongs to, as GET /api/me names it."""

    id: str
    name: str
    status: str


class Me(Row):
    """GET /api/me: the public account, the session, the two role views and the firm."""

    id: str
    firm_id: str | None = None
    email: str | None = None
    name: str
    role: ApiRole
    base_role: BaseRole
    client_id: str | None = None
    reviewer: bool = False
    engaged: list[str] | None = Field(default=None, description="staff only: the clients they are engaged on")
    auth_method: str | None = None
    fresh_at: str | None = None
    session_id: str | None = None
    mfa_enrolled_at: str | None = None
    disabled: bool = False
    last_login_at: str | None = None
    firm: FirmRef | None = Field(default=None, description="null for platform administrators")


# ------------------------------------------------------------------------------ platform

class Firm(Row):
    id: str
    name: str
    status: str = Field(description="active, provisioning, or deleting")
    created_at: str
    deleted_at: str | None = None


class CreateFirmRequest(BaseModel):
    id: str
    name: str
    admin_email: str


class CreateFirmResult(Shape):
    firm: Firm
    admin_invite_token: str


class Health(Shape):
    ok: bool
    build: str = Field(description="AGENTLEDGER_BUILD or the BUILD_SHA file; 'dev' in a checkout")
    backend: str


class LinkRequest(BaseModel):
    path: str = Field(description="/api/documents/{id}/file or /api/clients/{id}/export/{plugin}")


class LinkResult(Shape):
    url: str = Field(description="the path with a signed `dl` token; a session header is not needed")
    expires_in: int


# ------------------------------------------------------------------------------ clients

class ClientFacts(Row):
    """Profile facts are free-form; the keys the app reads by name are declared. PATCH /api/clients/{id}/facts merges
    the keys sent; a key sent as null is removed."""

    accounting_basis: str | None = None
    state: str | None = None
    employees: int | str | None = None


class Client(Row):
    """A row of the clients table (ledger/store.py), JSON columns parsed."""

    id: str
    name: str
    kind: Literal["business", "individual"]
    entity_type: str | None
    formed_under: str
    tax_id_last4: str | None
    emails: list[str]
    aliases: list[str]
    consent_7216_at: str | None
    closed_through: str | None = Field(description="ISO date; postings on or before it are frozen")
    domain: str
    facts: ClientFacts
    created_at: str


class CreateClientRequest(BaseModel):
    id: str = Field(pattern=CLIENT_ID_PATTERN, description="lowercase letters, digits, '-' or '_'; up to 64 characters")
    name: str
    kind: Literal["business", "individual"] = "business"
    entity_type: str | None = None
    formed_under: str = "domestic"
    tax_id_last4: str | None = None
    emails: list[str] = []
    aliases: list[str] = []
    consent_7216_at: str | None = None
    domain: str = "general"
    facts: ClientFacts = Field(default_factory=ClientFacts)


class CreateClientResult(Shape):
    id: str
    accounts_created: int


class PackSummary(Shape):
    id: str
    title: str
    description: str
    facts: list[str]


class Pack(Shape):
    """An industry pack (domains/*.yaml) a client can be onboarded on."""

    id: str
    title: str
    description: str
    status: str
    facts: list[str]


class Balance(Row):
    code: str
    name: str
    type: str
    balance: str


class Kpi(Row):
    id: str | None = None
    title: str
    value: str | None = None
    unit: str = ""
    missing: str | None = Field(default=None, description="the fact or balance the KPI needs and does not have")


class FindingResolution(Row):
    id: int | None = None
    finding_id: str | None = None
    actor: str
    role: str
    action: str
    note: str
    at: str


class Finding(Row):
    id: str
    client_id: str
    check_id: str
    severity: Literal["info", "low", "medium", "high", "critical"]
    title: str
    detail: str
    evidence: Any = None
    citation: str | None = None
    owner: Literal["client", "cpa", "both"]
    tax_year: int | None = None
    first_seen: str
    status: str
    resolutions: list[FindingResolution] = []


class Task(Row):
    id: int
    client_id: str | None = None
    client_name: str | None = None
    engagement_id: int | None = None
    title: str
    detail: str = ""
    assignee: str
    due: str | None = None
    status: str
    source: str
    created_at: str | None = None
    done_at: str | None = None
    done_note: str | None = None


class Deadline(Row):
    id: str | None = None
    title: str
    due: str
    days: int
    form: str | None = None
    citation: str | None = None
    client_id: str | None = None


class Opportunity(Row):
    playbook_id: str
    title: str
    category: str
    status: str = Field(description="applies, or needs_facts")
    missing_fact: str | None = None
    summary: str
    aggressiveness: str | None = None
    freshness: Any = None


class ChainStatus(Row):
    ok: bool
    checked: int
    head: str | None = None
    broken_at: int | str | None = None


class ClientDocument(Row):
    """A document as the client detail and GET /api/clients/{id}/documents list them."""

    id: str
    original_name: str
    doc_type: str | None = None
    tax_year: int | None = None
    status: str = Field(description="filed, or needs_review")
    confidence: float | None = None
    vault_path: str | None = None
    summary: str | None = None
    classified_by: str | None = None
    received_at: str
    channel: str


class DocumentPage(Shape):
    """One page of a client's documents, newest first; `next_cursor` is null on the last page."""

    items: list[ClientDocument]
    next_cursor: str | None = None
    total: int = Field(description="the client's documents not deleted under retention, all pages")


class ClientDetail(Row):
    client: Client
    year: int
    pack: PackSummary | None = None
    balances: list[Balance]
    kpis: list[Kpi]
    findings: list[Finding]
    documents: list[ClientDocument] = Field(description="the 100 most recent; the full list is paged at /documents")
    tasks: list[Task]
    deadlines: list[Deadline]
    opportunities: list[Opportunity]
    chain: ChainStatus
    integrity: int
    m1: dict[str, Any] | None = Field(default=None, description="business clients only")
    ar: dict[str, Any] | None = Field(default=None, description="business clients only")
    vendors_1099: list[dict[str, Any]] | None = Field(default=None, description="business clients only")
    deals: dict[str, Any] | None = Field(default=None, description="business clients only")


class DashboardCard(Shape):
    id: str
    name: str
    kind: str
    domain: str
    integrity: int
    open_findings: int
    open_tasks: int
    docs_this_month: int


class Dashboard(Row):
    """GET /api/dashboard: the cards every role gets, the firm's work for CPAs, the client's tasks for a client."""

    clients: list[DashboardCard]
    today: str
    tasks: list[Task] | None = None
    pending: list[dict[str, Any]] | None = None
    adopted: list[dict[str, Any]] | None = None
    staleness: list[dict[str, Any]] | None = None
    review_queue: int | None = None
    runs: list[dict[str, Any]] | None = None
    ai: dict[str, Any] | None = None
    kb: dict[str, Any] | None = None
    ai_usage: list[dict[str, Any]] | None = None


# ------------------------------------------------------------------------------ documents

class IngestedDocument(Shape):
    """One part of an upload that was stored (intake/pipeline.py `ingest`; a zip or an email yields several)."""

    id: str
    client_id: str | None = None
    status: str = Field(description="filed, or needs_review (waiting in the inbox)")
    doc_type: str | None = None
    tax_year: int | None = None
    confidence: float
    vault_path: str | None = None
    retention_class: str | None = None
    match: str = Field(description="how the client was matched")
    name: str
    suggested_client: str | None = None


class DuplicateDocument(Shape):
    """The same bytes were stored before: nothing new was written."""

    duplicate: Literal[True]
    id: str
    client_id: str | None = None
    status: str
    vault_path: str | None = None
    name: str


class ReviewDocument(Row):
    """A document waiting in the review queue (GET /api/documents/review)."""

    id: str
    original_name: str
    doc_type: str | None = None
    tax_year: int | None = None
    confidence: float | None = None
    summary: str | None = None
    sender: str | None = None
    received_at: str
    channel: str
    classified_by: str | None = None


class AssignRequest(BaseModel):
    client_id: str
    move_reason: str | None = Field(default=None, description="required (10+ characters) to move a document already filed elsewhere")


class AssignedDocument(Row):
    """The document row after filing."""

    id: str
    client_id: str | None = None
    status: str


class DocumentVersion(Row):
    """A stored version of a document (the storage locator is withheld)."""

    document_id: str
    version: int
    sha256: str
    size: int
    created_at: str
    created_by: str
    reason: str


# ------------------------------------------------------------------------------ CRM

class Engagement(Row):
    id: int
    client_id: str
    client_name: str
    type: str
    tax_year: int | None = None
    owner: str | None = None
    due_date: str | None = None
    fee: str | None = None
    stage: str
    open_tasks: int
    created_at: str | None = None
