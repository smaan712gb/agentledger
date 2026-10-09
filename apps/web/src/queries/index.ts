/**
 * Query definitions: one key scheme for the whole app, so a mutation can invalidate exactly what it changed. Keys
 * carry the context that selects the data (the client and the period), never the session.
 */

import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";

import { api } from "../api";

/** Documents per page of GET /api/clients/{id}/documents (the API allows up to 200). */
export const DOCUMENTS_PAGE_SIZE = 50;

export const queryKeys = {
  clients: ["clients"] as const,
  client: (clientId: string) => ["clients", clientId] as const,
  clientDetail: (clientId: string, year: number) => ["clients", clientId, "detail", { year }] as const,
  /** Under the client's key, so a change to the client (an upload) invalidates every page with the detail. */
  documents: (clientId: string) => ["clients", clientId, "documents"] as const,
  reviewQueue: ["documents", "review"] as const,
  versions: (docId: string) => ["documents", docId, "versions"] as const,
  users: ["auth", "users"] as const,
  firms: ["platform", "firms"] as const,
  pipeline: ["crm", "pipeline"] as const,
  health: ["health"] as const,
};

export const queries = {
  clients: () => queryOptions({ queryKey: queryKeys.clients, queryFn: () => api.clients.list() }),
  clientDetail: (clientId: string, year: number) =>
    queryOptions({
      queryKey: queryKeys.clientDetail(clientId, year),
      queryFn: () => api.clients.detail(clientId, year),
    }),
  /** The client's documents, newest first, a page at a time: `fetchNextPage` follows the API's cursor. */
  documents: (clientId: string) =>
    infiniteQueryOptions({
      queryKey: queryKeys.documents(clientId),
      queryFn: ({ pageParam }) => api.clients.documents(clientId, pageParam, DOCUMENTS_PAGE_SIZE),
      initialPageParam: null as string | null,
      getNextPageParam: (last) => last.next_cursor ?? null,
    }),
  reviewQueue: () => queryOptions({ queryKey: queryKeys.reviewQueue, queryFn: () => api.documents.review() }),
  versions: (docId: string) =>
    queryOptions({ queryKey: queryKeys.versions(docId), queryFn: () => api.documents.versions(docId) }),
  users: () => queryOptions({ queryKey: queryKeys.users, queryFn: () => api.auth.users() }),
  firms: () => queryOptions({ queryKey: queryKeys.firms, queryFn: () => api.platform.firms() }),
  pipeline: () => queryOptions({ queryKey: queryKeys.pipeline, queryFn: () => api.crm.pipeline(), staleTime: 60_000 }),
};
