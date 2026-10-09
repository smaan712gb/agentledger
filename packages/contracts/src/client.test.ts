import { describe, expect, it, vi } from "vitest";

import { ApiError, STEP_UP_REQUIRED, createApiClient } from "./client";
import { endpoints } from "./endpoints";

type Answer = { status: number; body?: unknown; headers?: Record<string, string> } | Error;

/** A scripted fetch: answers in order, records every request. */
function scriptedFetch(answers: Answer[]) {
  const calls: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    calls.push(request);
    const next = answers.shift();
    if (!next) throw new Error("no scripted answer left");
    if (next instanceof Error) throw next;
    const headers = { "content-type": "application/json", ...(next.headers ?? {}) };
    return new Response(next.body === undefined ? null : JSON.stringify(next.body), { status: next.status, headers });
  };
  return { fetchImpl, calls };
}

describe("createApiClient", () => {
  it("attaches the session token and resolves relative paths against the base url", async () => {
    const { fetchImpl, calls } = scriptedFetch([{ status: 200, body: { ok: true } }]);
    const api = createApiClient({ baseUrl: "http://api.test", getToken: () => "tok", fetch: fetchImpl });
    await api.request("GET", "/api/me", { query: { year: 2026, skip: undefined } });
    expect(calls[0]?.url).toBe("http://api.test/api/me?year=2026");
    expect(calls[0]?.headers.get("authorization")).toBe("Bearer tok");
  });

  it("a 401 ends the session, except on the sign-in steps where it is a wrong password or code", async () => {
    const onUnauthorized = vi.fn();
    const { fetchImpl } = scriptedFetch([
      { status: 401, body: { detail: "sign in required" } },
      { status: 401, body: { detail: "email or password is incorrect" } },
      { status: 401, body: { detail: "that code is not valid" } },
      { status: 401, body: { detail: "sign in required" } },
    ]);
    const api = createApiClient({
      baseUrl: "http://api.test",
      getToken: () => "tok",
      fetch: fetchImpl,
      onUnauthorized,
    });
    await expect(api.request("GET", "/api/clients")).rejects.toMatchObject({ status: 401, detail: "sign in required" });
    expect(onUnauthorized).toHaveBeenCalledWith({ path: "/api/clients" });
    await expect(api.request("POST", "/api/auth/login", { json: {} })).rejects.toMatchObject({ status: 401 });
    await expect(api.request("POST", "/api/auth/step-up", { json: {} })).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    // /api/auth/users is session-bound like every other route: a 401 there is the session ending.
    await expect(api.request("GET", "/api/auth/users")).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(2);
  });

  it("a 403 step_up_required asks the host to verify and retries exactly once", async () => {
    const onStepUp = vi.fn().mockResolvedValue(true);
    const { fetchImpl, calls } = scriptedFetch([
      { status: 403, body: { detail: STEP_UP_REQUIRED } },
      { status: 200, body: { firm: { id: "x" } } },
    ]);
    const api = createApiClient({ baseUrl: "http://api.test", getToken: () => "tok", fetch: fetchImpl, onStepUp });
    const out = await api.request<{ firm: { id: string } }>("POST", "/api/platform/firms", { json: { id: "x" } });
    expect(out.firm.id).toBe("x");
    expect(onStepUp).toHaveBeenCalledWith({ path: "/api/platform/firms", method: "POST" });
    expect(calls).toHaveLength(2);
    expect(await calls[1]?.text()).toBe(JSON.stringify({ id: "x" }));
  });

  it("surfaces the 403 when the person does not verify, and never loops", async () => {
    const onStepUp = vi.fn().mockResolvedValue(false);
    const { fetchImpl, calls } = scriptedFetch([{ status: 403, body: { detail: STEP_UP_REQUIRED } }]);
    const api = createApiClient({ baseUrl: "http://api.test", getToken: () => null, fetch: fetchImpl, onStepUp });
    const err = await api.request("POST", "/api/platform/firms").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).isStepUpRequired).toBe(true);
    expect(calls).toHaveLength(1);
  });

  it("a plain 403 is not a step-up", async () => {
    const onStepUp = vi.fn();
    const { fetchImpl } = scriptedFetch([{ status: 403, body: { detail: "CPA access required" } }]);
    const api = createApiClient({ baseUrl: "http://api.test", getToken: () => null, fetch: fetchImpl, onStepUp });
    await expect(api.request("GET", "/api/documents/review")).rejects.toMatchObject({
      status: 403,
      detail: "CPA access required",
    });
    expect(onStepUp).not.toHaveBeenCalled();
  });

  it("turns 422 bodies into field errors and keeps the request id", async () => {
    const { fetchImpl } = scriptedFetch([
      {
        status: 422,
        body: {
          detail: [
            { loc: ["body", "tax_year"], msg: "Input should be a valid integer", type: "int_parsing" },
            { loc: ["body", "name"], msg: "Field required", type: "missing" },
          ],
        },
        headers: { "X-Request-Id": "req-9" },
      },
    ]);
    const api = createApiClient({ baseUrl: "http://api.test", getToken: () => null, fetch: fetchImpl });
    const err = (await api.request("PUT", "/api/returns/r1/inputs", { json: {} }).catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(422);
    expect(err.requestId).toBe("req-9");
    expect(err.fieldErrors()).toEqual({ tax_year: "Input should be a valid integer", name: "Field required" });
    expect(err.message).toContain("tax_year: Input should be a valid integer");
  });

  it("network failures become status 0", async () => {
    const { fetchImpl } = scriptedFetch([new TypeError("Failed to fetch")]);
    const api = createApiClient({ baseUrl: "http://api.test", getToken: () => null, fetch: fetchImpl });
    const err = (await api.request("GET", "/healthz").catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(0);
    expect(err.isNetworkError).toBe(true);
  });

  it("runs middleware out in order and back in reverse, with openapi-fetch's shape", async () => {
    const trail: string[] = [];
    const { fetchImpl } = scriptedFetch([{ status: 200, body: {} }]);
    const api = createApiClient({ baseUrl: "http://api.test", getToken: () => null, fetch: fetchImpl });
    api.use(
      { onRequest: () => void trail.push("a:req"), onResponse: () => void trail.push("a:res") },
      { onRequest: () => void trail.push("b:req"), onResponse: () => void trail.push("b:res") },
    );
    await api.request("GET", "/healthz");
    expect(trail).toEqual(["a:req", "b:req", "b:res", "a:res"]);
  });

  it("endpoints build the API's paths and bodies", async () => {
    const { fetchImpl, calls } = scriptedFetch([
      { status: 200, body: { url: "/api/documents/d1/file?dl=x", expires_in: 120 } },
      { status: 200, body: {} },
    ]);
    const api = endpoints(createApiClient({ baseUrl: "http://api.test", getToken: () => null, fetch: fetchImpl }));
    await api.links.create(api.documents.filePath("d1"));
    expect(calls[0]?.url).toBe("http://api.test/api/links");
    expect(await calls[0]?.text()).toBe(JSON.stringify({ path: "/api/documents/d1/file" }));
    await api.clients.detail("ortiz auto", 2025);
    expect(calls[1]?.url).toBe("http://api.test/api/clients/ortiz%20auto?year=2025");
  });
});
