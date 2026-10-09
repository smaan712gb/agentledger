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
  /** Under the client's key: creating a return invalidates the client; a return's own data lives under "returns". */
  returns: (clientId: string) => ["clients", clientId, "returns"] as const,
  return: (rid: string) => ["returns", rid] as const,
  conflicts: (rid: string) => ["returns", rid, "conflicts"] as const,
  factHistory: (rid: string, anchor: string) => ["returns", rid, "facts", anchor] as const,
  recalculation: (rid: string) => ["returns", rid, "recalculation-preview"] as const,
  /** The dispositions this session learnt from POST answers (the API has no GET for them yet: docs/WEB.md §4). */
  dispositions: (rid: string) => ["returns", rid, "dispositions"] as const,
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
  returns: (clientId: string) =>
    queryOptions({ queryKey: queryKeys.returns(clientId), queryFn: () => api.returns.list(clientId) }),
  return: (rid: string) => queryOptions({ queryKey: queryKeys.return(rid), queryFn: () => api.returns.get(rid) }),
  conflicts: (rid: string) =>
    queryOptions({ queryKey: queryKeys.conflicts(rid), queryFn: () => api.returns.conflicts(rid) }),
  factHistory: (rid: string, anchor: string) =>
    queryOptions({
      queryKey: queryKeys.factHistory(rid, anchor),
      queryFn: () => api.returns.factHistory(rid, anchor),
    }),
  recalculation: (rid: string) =>
    queryOptions({
      queryKey: queryKeys.recalculation(rid),
      queryFn: () => api.returns.recalculationPreview(rid),
      staleTime: 0,
    }),
  users: () => queryOptions({ queryKey: queryKeys.users, queryFn: () => api.auth.users() }),
  firms: () => queryOptions({ queryKey: queryKeys.firms, queryFn: () => api.platform.firms() }),
  pipeline: () => queryOptions({ queryKey: queryKeys.pipeline, queryFn: () => api.crm.pipeline(), staleTime: 60_000 }),
};
