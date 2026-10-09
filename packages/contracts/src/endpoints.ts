/**
 * Typed functions for the routes the web app uses, one per route, named after the FastAPI handlers in
 * src/agentledger/api/app.py. Paths and bodies are the API's; nothing here decides anything.
 */

import type { ApiClient } from "./client";
import type {
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
  DocumentVersion,
  Firm,
  FirmUser,
  Health,
  IdpPurpose,
  IdpStart,
  InviteBody,
  InviteResult,
  LoginStep,
  Me,
  MfaResult,
  Ok,
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
      login: (body: { email: string; password: string }) =>
        api.request<LoginStep>("POST", "/api/auth/login", { json: body }),
      mfa: (body: { challenge: string; code: string }) =>
        api.request<MfaResult>("POST", "/api/auth/mfa", { json: body }),
      accept: (body: { token: string; name: string; password: string }) =>
        api.request<AcceptResult>("POST", "/api/auth/accept", { json: body }),
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
      createFirm: (body: CreateFirmBody) =>
        api.request<CreateFirmResult>("POST", "/api/platform/firms", { json: body }),
    },

    clients: {
      list: () => api.request<Client[]>("GET", "/api/clients"),
      create: (body: CreateClientBody) => api.request<CreateClientResult>("POST", "/api/clients", { json: body }),
      detail: (clientId: string, year?: number) =>
        api.request<ClientDetail>("GET", `/api/clients/${enc(clientId)}`, { query: { year } }),
      updateFacts: (clientId: string, facts: ClientFacts) =>
        api.request<UpdatedFacts>("PATCH", `/api/clients/${enc(clientId)}/facts`, { json: facts }),
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
