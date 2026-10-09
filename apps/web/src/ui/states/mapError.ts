import { isApiError } from "@agentledger/contracts";

/** The route guard's refusal, phrased with the API's own text so the screen reads the same as the response would. */
export class RouteForbidden extends Error {
  constructor(public readonly reason: string) {
    super(reason);
    this.name = "RouteForbidden";
  }
}

export type ErrorKind = "unauthorized" | "forbidden" | "not-found" | "conflict" | "gone" | "offline" | "error";

export interface MappedError {
  kind: ErrorKind;
  detail: string | undefined;
  requestId: string | null;
  status: number | null;
}

/** One place that decides which state an error becomes. */
export function mapError(error: unknown): MappedError {
  if (error instanceof RouteForbidden) return { kind: "forbidden", detail: error.reason, requestId: null, status: 403 };
  if (isApiError(error)) {
    const detail = typeof error.detail === "string" ? error.detail : error.message;
    const base = { detail, requestId: error.requestId, status: error.status };
    switch (error.status) {
      case 0:
        return { ...base, kind: "offline" };
      case 401:
        return { ...base, kind: "unauthorized" };
      case 403:
        return { ...base, kind: "forbidden" };
      case 404:
        return { ...base, kind: "not-found" };
      case 409:
        return { ...base, kind: "conflict" };
      case 410:
        return { ...base, kind: "gone" };
      default:
        return { ...base, kind: "error" };
    }
  }
  const detail = error instanceof Error ? error.message : undefined;
  return { kind: "error", detail, requestId: null, status: null };
}
