/**
 * The fetch layer every screen goes through. It knows four things about the API and nothing about screens:
 *
 * - the session token travels in `Authorization: Bearer` (never in a URL);
 * - a 401 anywhere but the sign-in steps themselves (CREDENTIAL_PATHS) means the session ended: the host clears it
 *   and goes to sign-in;
 * - a 403 whose detail is exactly "step_up_required" asks for a recent sign-in: the host verifies the person
 *   (TOTP dialog, or the identity provider) and the request is retried once;
 * - a 422 carries pydantic field errors; everything else carries `detail` text and an X-Request-Id to quote.
 *
 * The middleware shape (`onRequest`/`onResponse`, in registration order, same names and return conventions as
 * openapi-fetch) is deliberate: once the API publishes response schemas, `createClient<paths>()` from openapi-fetch
 * takes this file's place and the middleware moves over unchanged.
 */

import type { ApiErrorBody, ValidationError } from "./types";

export type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface Middleware {
  /** Return a Request to replace the outgoing one, a Response to short-circuit, or nothing. */
  onRequest?(ctx: {
    request: Request;
    id: string;
  }): Request | Response | undefined | Promise<Request | Response | undefined>;
  /** Return a Response to replace the incoming one, or nothing. */
  onResponse?(ctx: {
    request: Request;
    response: Response;
    id: string;
  }): Response | undefined | Promise<Response | undefined>;
}

export interface RequestInitLite {
  /** Serialised as the JSON body (Content-Type application/json). */
  json?: unknown;
  /** A multipart body; the browser sets the boundary. */
  form?: FormData;
  /** Query parameters; undefined and null values are dropped. */
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

export interface ApiClientOptions {
  /** Origin the paths resolve against. Omitted: the page's own origin (the SPA is served next to the API). */
  baseUrl?: string;
  /** The session token, read per request so a sign-in or sign-out applies at once. */
  getToken: () => string | null;
  /** The session ended (a 401 off the credential paths). Called once per failing request, before the error is thrown. */
  onUnauthorized?: (ctx: { path: string }) => void;
  /**
   * A consequential action needs a recent sign-in (403 "step_up_required"). Resolve true once the person has
   * verified and the request should be retried (exactly once); false to give up, which surfaces the 403.
   */
  onStepUp?: (ctx: { path: string; method: Method }) => Promise<boolean>;
  /** Every completed round trip (any status), for idle tracking. */
  onActivity?: (at: number) => void;
  fetch?: typeof fetch;
}

/** The detail text the API uses for a required step-up (app.py `fresh`). */
export const STEP_UP_REQUIRED = "step_up_required";

/**
 * Routes where a 401 means "wrong credentials or code", not "your session ended": the sign-in steps themselves.
 * Every other route under /api/auth/* (users, invite, grants, events) is session-bound like the rest of the API.
 */
export const CREDENTIAL_PATHS: readonly string[] = [
  "/api/auth/login",
  "/api/auth/mfa",
  "/api/auth/accept",
  "/api/auth/step-up",
  "/api/auth/config",
  "/api/auth/idp/start",
  "/api/auth/logout",
];

export function isCredentialPath(path: string): boolean {
  return CREDENTIAL_PATHS.includes(path.split("?")[0] ?? path);
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | ValidationError[];
  readonly requestId: string | null;
  readonly path: string;
  readonly method: Method;

  constructor(args: {
    status: number;
    detail: string | ValidationError[];
    requestId: string | null;
    path: string;
    method: Method;
  }) {
    super(typeof args.detail === "string" ? args.detail : summariseValidation(args.detail));
    this.name = "ApiError";
    this.status = args.status;
    this.detail = args.detail;
    this.requestId = args.requestId;
    this.path = args.path;
    this.method = args.method;
  }

  /** The request never reached the API (offline, DNS, CORS, aborted): status 0. */
  get isNetworkError(): boolean {
    return this.status === 0;
  }

  get isStepUpRequired(): boolean {
    return this.status === 403 && this.detail === STEP_UP_REQUIRED;
  }

  /** Field name (the last string of pydantic's `loc`, "body" stripped) to message, for 422 responses. */
  fieldErrors(): Record<string, string> {
    if (typeof this.detail === "string") return {};
    const out: Record<string, string> = {};
    for (const e of this.detail) {
      const parts = e.loc.filter((p) => p !== "body");
      const key = parts.length ? String(parts[parts.length - 1]) : "_";
      if (!(key in out)) out[key] = e.msg;
    }
    return out;
  }
}

function summariseValidation(errors: ValidationError[]): string {
  if (!errors.length) return "the request was not valid";
  return errors.map((e) => `${e.loc.filter((p) => p !== "body").join(".") || "request"}: ${e.msg}`).join("; ");
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

export interface ApiClient {
  request<T>(method: Method, path: string, init?: RequestInitLite): Promise<T>;
  /** Register middleware; it runs in registration order on the way out and in reverse on the way back. */
  use(...middleware: Middleware[]): void;
  eject(middleware: Middleware): void;
}

let counter = 0;

export function createApiClient(options: ApiClientOptions): ApiClient {
  const middleware: Middleware[] = [];
  const doFetch: typeof fetch = options.fetch ?? ((input, init) => fetch(input, init));

  function resolve(path: string, query?: RequestInitLite["query"]): string {
    const base = options.baseUrl ?? (typeof location !== "undefined" ? location.origin : "http://localhost");
    const url = new URL(path, base);
    if (query) {
      for (const [k, v] of Object.entries(query)) {
        if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
      }
    }
    return url.toString();
  }

  async function send<T>(method: Method, path: string, init: RequestInitLite, retried: boolean): Promise<T> {
    const headers = new Headers(init.headers);
    const token = options.getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    let body: BodyInit | undefined;
    if (init.form) {
      body = init.form;
    } else if (init.json !== undefined) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(init.json);
    }
    let request = new Request(resolve(path, init.query), {
      method,
      headers,
      ...(body !== undefined ? { body } : {}),
      ...(init.signal ? { signal: init.signal } : {}),
    });
    const id = `req_${++counter}`;

    let response: Response | undefined;
    for (const m of middleware) {
      if (!m.onRequest) continue;
      const out = await m.onRequest({ request, id });
      if (out instanceof Response) {
        response = out;
        break;
      }
      if (out instanceof Request) request = out;
    }
    if (!response) {
      try {
        response = await doFetch(request);
      } catch (err) {
        options.onActivity?.(Date.now());
        throw new ApiError({
          status: 0,
          detail:
            err instanceof Error && err.name === "AbortError"
              ? "the request was cancelled"
              : "could not reach the service",
          requestId: null,
          path,
          method,
        });
      }
    }
    for (const m of [...middleware].reverse()) {
      if (!m.onResponse) continue;
      const out = await m.onResponse({ request, response, id });
      if (out) response = out;
    }
    options.onActivity?.(Date.now());

    const requestId = response.headers.get("X-Request-Id");
    if (response.ok) {
      return (await parseBody(response)) as T;
    }
    const detail = await errorDetail(response);
    const error = new ApiError({ status: response.status, detail, requestId, path, method });
    if (response.status === 401 && !isCredentialPath(path)) {
      options.onUnauthorized?.({ path });
      throw error;
    }
    if (error.isStepUpRequired && !retried && options.onStepUp) {
      const verified = await options.onStepUp({ path, method });
      if (verified) return send<T>(method, path, init, true);
    }
    throw error;
  }

  return {
    request: (method, path, init = {}) => send(method, path, init, false),
    use: (...mw) => {
      middleware.push(...mw);
    },
    eject: (mw) => {
      const i = middleware.indexOf(mw);
      if (i >= 0) middleware.splice(i, 1);
    },
  };
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const type = response.headers.get("content-type") ?? "";
  if (type.includes("json")) return response.json();
  return response.text();
}

async function errorDetail(response: Response): Promise<string | ValidationError[]> {
  const text = await response.text().catch(() => "");
  try {
    const parsed = JSON.parse(text) as Partial<ApiErrorBody>;
    if (typeof parsed.detail === "string") return parsed.detail;
    if (Array.isArray(parsed.detail)) return parsed.detail;
  } catch {
    /* not JSON */
  }
  return text || response.statusText || `HTTP ${response.status}`;
}
