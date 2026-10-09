/**
 * Typed functions for the routes the web app uses, one per route, named after the FastAPI handlers in
 * src/agentledger/api/app.py. Paths and bodies are the API's; nothing here decides anything.
 */

import type { ApiClient } from "./client";
import type {
  AcceptBody,
  AcceptResult,
  AssignedDocument,
  AuthConfig,
  AuthEvent,
  Client,
  ClientDetail,
  ClientFacts,
  CreateClientBody,
  CreateClientResult,
  CreateFirmBody,
  CreateFirmResult,
  DocumentPage,
  DocumentVersion,
  Firm,
  FirmUser,
  Health,
  IdpPurpose,
  IdpStart,
  InviteBody,
  InviteResult,
  LoginBody,
  LoginStep,
  Me,
  MfaBody,
  MfaResult,
  Ok,
  Pack,
  Pipeline,
  ReviewDocument,
  SignedLink,
  Task,
  UpdatedFacts,
  UploadResult,
} from "./types";

export function endpoints(api: ApiClient) {
  const enc = encodeURIComponent;
  return {
    health: () => api.request<Health>("GET", "/healthz"),
    me: () => api.request<Me>("GET", "/api/me"),

    auth: {
      config: () => api.request<AuthConfig>("GET", "/api/auth/config"),
      login: (body: LoginBody) => api.request<LoginStep>("POST", "/api/auth/login", { json: body }),
      mfa: (body: MfaBody) => api.request<MfaResult>("POST", "/api/auth/mfa", { json: body }),
      accept: (body: AcceptBody) => api.request<AcceptResult>("POST", "/api/auth/accept", { json: body }),
      logout: () => api.request<Ok>("POST", "/api/auth/logout"),
      stepUp: (code: string) => api.request<Ok>("POST", "/api/auth/step-up", { json: { code } }),
      idpStart: (purpose: IdpPurpose, invite?: string) =>
        api.request<IdpStart>("GET", "/api/auth/idp/start", { query: { purpose, ...(invite ? { invite } : {}) } }),
      users: () => api.request<FirmUser[]>("GET", "/api/auth/users"),
      invite: (body: InviteBody) => api.request<InviteResult>("POST", "/api/auth/invite", { json: body }),
      setDisabled: (userId: string, disabled: boolean) =>
        api.request<Ok>("POST", `/api/auth/users/${enc(userId)}/disable`, { json: { disabled } }),
      events: () => api.request<AuthEvent[]>("GET", "/api/auth/events"),
    },

    platform: {
      firms: () => api.request<Firm[]>("GET", "/api/platform/firms"),
      firm: (firmId: string) => api.request<Firm>("GET", `/api/platform/firms/${enc(firmId)}`),
      createFirm: (body: CreateFirmBody) =>
        api.request<CreateFirmResult>("POST", "/api/platform/firms", { json: body }),
    },

    clients: {
      list: () => api.request<Client[]>("GET", "/api/clients"),
      create: (body: CreateClientBody) => api.request<CreateClientResult>("POST", "/api/clients", { json: body }),
      detail: (clientId: string, year?: number) =>
        api.request<ClientDetail>("GET", `/api/clients/${enc(clientId)}`, { query: { year } }),
      /** One page of the client's documents, newest first; pass the previous page's `next_cursor` for the next. */
      documents: (clientId: string, cursor?: string | null, limit?: number) =>
        api.request<DocumentPage>("GET", `/api/clients/${enc(clientId)}/documents`, { query: { cursor, limit } }),
      /** Merges the facts sent; a fact sent as null is removed. */
      updateFacts: (clientId: string, facts: ClientFacts) =>
        api.request<UpdatedFacts>("PATCH", `/api/clients/${enc(clientId)}/facts`, { json: facts }),
      packs: () => api.request<Pack[]>("GET", "/api/packs"),
    },

    documents: {
      /** One request per file; the API answers with one result per part (a zip or an email has several). */
      upload: (file: File, clientId?: string) => {
        const form = new FormData();
        form.append("file", file, file.name);
        if (clientId) form.append("client_id", clientId);
        return api.request<UploadResult[]>("POST", "/api/documents/upload", { form });
      },
      review: () => api.request<ReviewDocument[]>("GET", "/api/documents/review"),
      assign: (docId: string, clientId: string, moveReason?: string) =>
        api.request<AssignedDocument>("POST", `/api/documents/${enc(docId)}/assign`, {
          json: { client_id: clientId, ...(moveReason ? { move_reason: moveReason } : {}) },
        }),
      versions: (docId: string) => api.request<DocumentVersion[]>("GET", `/api/documents/${enc(docId)}/versions`),
      /** The path a signed download link is minted for. */
      filePath: (docId: string) => `/api/documents/${enc(docId)}/file`,
      /**
       * The same signed link, asking for the bytes inline: a PDF, PNG or JPEG comes back with its real media type in
       * a sandbox (Content-Security-Policy: sandbox; nosniff); every other type stays an attachment.
       */
      inlineUrl: (signedUrl: string) => `${signedUrl}&inline=1`,
    },

    links: {
      /** A signed URL (120 s) for a file or export path; the response carries Content-Disposition: attachment. */
      create: (path: string) => api.request<SignedLink>("POST", "/api/links", { json: { path } }),
    },

    crm: {
      pipeline: () => api.request<Pipeline>("GET", "/api/crm/pipeline"),
      tasks: (clientId?: string) => api.request<Task[]>("GET", "/api/tasks", { query: { client_id: clientId } }),
    },
  };
}

export type Endpoints = ReturnType<typeof endpoints>;
