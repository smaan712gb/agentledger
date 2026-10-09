import type { Me } from "@agentledger/contracts";
import { HttpResponse, http, type HttpHandler } from "msw";

import * as fx from "./fixtures";

export const ORIGIN = "http://localhost:3000";

/** The happy path for a signed-in person: every route the slice uses answers with fixtures. */
export function happyHandlers(me: Me = fx.firmAdmin): HttpHandler[] {
  return [
    http.get(`${ORIGIN}/healthz`, () => HttpResponse.json(fx.health)),
    http.get(`${ORIGIN}/api/auth/config`, () => HttpResponse.json(fx.localConfig)),
    http.get(`${ORIGIN}/api/me`, () => HttpResponse.json(me)),
    http.get(`${ORIGIN}/api/clients`, () => HttpResponse.json(fx.clients)),
    http.post(`${ORIGIN}/api/clients`, async ({ request }) => {
      const body = (await request.json()) as { id: string };
      return HttpResponse.json({ id: body.id, accounts_created: 42 });
    }),
    http.get(`${ORIGIN}/api/clients/:clientId`, ({ params, request }) => {
      const client = fx.clients.find((c) => c.id === params.clientId);
      if (!client) return HttpResponse.json({ detail: "client not found" }, { status: 404 });
      const year = Number(new URL(request.url).searchParams.get("year") ?? 2026);
      return HttpResponse.json(fx.detailOf(client, year));
    }),
    http.get(`${ORIGIN}/api/clients/:clientId/documents`, ({ params }) => {
      const items = params.clientId === fx.ortiz.id ? fx.ortizDocuments : [];
      return HttpResponse.json({ items, next_cursor: null, total: items.length });
    }),
    http.get(`${ORIGIN}/api/clients/:clientId/returns`, ({ params }) =>
      HttpResponse.json(params.clientId === fx.ortiz.id ? fx.returnList : []),
    ),
    http.post(`${ORIGIN}/api/clients/:clientId/returns`, () => HttpResponse.json({ id: "ret_new" })),
    http.get(`${ORIGIN}/api/returns/:rid`, ({ params }) => {
      if (params.rid === "ret_1") return HttpResponse.json(fx.returnDetail);
      if (params.rid === "ret_new") return HttpResponse.json(fx.freshReturn);
      return HttpResponse.json({ detail: "return not found" }, { status: 404 });
    }),
    http.put(`${ORIGIN}/api/returns/:rid/inputs`, () => HttpResponse.json(fx.returnResult)),
    http.post(`${ORIGIN}/api/returns/:rid/populate`, () => HttpResponse.json(fx.populateResult)),
    http.get(`${ORIGIN}/api/returns/:rid/conflicts`, () => HttpResponse.json(fx.conflicts)),
    http.post(`${ORIGIN}/api/returns/:rid/conflicts/:conflictId`, () => HttpResponse.json({ open: 1 })),
    http.get(`${ORIGIN}/api/returns/:rid/facts`, () => HttpResponse.json(fx.factHistory)),
    http.post(`${ORIGIN}/api/returns/:rid/confirm`, () => HttpResponse.json({ confirmed: 7 })),
    http.post(`${ORIGIN}/api/returns/:rid/compute`, () => HttpResponse.json(fx.returnResult)),
    http.get(`${ORIGIN}/api/returns/:rid/recalculation-preview`, () =>
      HttpResponse.json({ ...fx.returnResult, changes_vs_latest: { total_tax: { latest: "5000", now: "4980" } } }),
    ),
    http.post(`${ORIGIN}/api/returns/:rid/amend`, () => HttpResponse.json({ id: "ret_amend" })),
    http.post(`${ORIGIN}/api/returns/:rid/void`, () => HttpResponse.json({ status: "void" })),
    http.post(`${ORIGIN}/api/returns/:rid/documents/:docId/disposition`, ({ params }) =>
      HttpResponse.json([fx.dispositionFor(String(params.docId))]),
    ),
    // After every specific route: {action} is submit, approve, request-changes, request-signature, release-approve,
    // reconcile or retransmit.
    http.post(`${ORIGIN}/api/returns/:rid/:action`, ({ params }) =>
      HttpResponse.json({ status: fx.statusAfter(String(params.action)), history: fx.returnDetail.history }),
    ),
    http.get(`${ORIGIN}/api/documents/:docId/file`, ({ request }) => {
      const inline = new URL(request.url).searchParams.get("inline") === "1";
      return new HttpResponse(fx.PDF_BYTES, {
        headers: inline
          ? { "Content-Type": "application/pdf", "Content-Disposition": 'inline; filename="w2.pdf"' }
          : { "Content-Type": "application/octet-stream", "Content-Disposition": 'attachment; filename="w2.pdf"' },
      });
    }),
    http.patch(`${ORIGIN}/api/clients/:clientId/facts`, async ({ request }) => {
      const facts = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ ...fx.ortiz.facts, ...facts });
    }),
    http.get(`${ORIGIN}/api/documents/review`, () => HttpResponse.json(fx.review)),
    http.post(`${ORIGIN}/api/documents/:docId/assign`, ({ params }) =>
      HttpResponse.json({ id: params.docId, client_id: "ortiz-auto", status: "filed" }),
    ),
    http.get(`${ORIGIN}/api/documents/:docId/versions`, () => HttpResponse.json(fx.versions)),
    http.post(`${ORIGIN}/api/documents/upload`, () =>
      HttpResponse.json([
        {
          id: "doc_new",
          client_id: "ortiz-auto",
          status: "filed",
          doc_type: "Receipt",
          tax_year: 2026,
          confidence: 0.9,
          vault_path: "blob:x",
          retention_class: "support",
          match: "explicit",
          name: "receipt.txt",
          suggested_client: null,
        },
      ]),
    ),
    http.post(`${ORIGIN}/api/links`, async ({ request }) => {
      const body = (await request.json()) as { path: string };
      return HttpResponse.json({ url: `${body.path}?dl=signed`, expires_in: 120 });
    }),
    http.get(`${ORIGIN}/api/auth/users`, () => HttpResponse.json(fx.users)),
    http.post(`${ORIGIN}/api/auth/invite`, () => HttpResponse.json({ invite_token: "tok_123", expires_in_days: 7 })),
    http.post(`${ORIGIN}/api/auth/users/:userId/disable`, () => HttpResponse.json({ ok: true })),
    http.get(`${ORIGIN}/api/platform/firms`, () => HttpResponse.json(fx.firms)),
    http.post(`${ORIGIN}/api/platform/firms`, () =>
      HttpResponse.json({ firm: fx.firms[0], admin_invite_token: "tok_firm" }),
    ),
    http.get(`${ORIGIN}/api/crm/pipeline`, () => HttpResponse.json(fx.pipeline)),
    http.post(`${ORIGIN}/api/auth/logout`, () => HttpResponse.json({ ok: true })),
    http.post(`${ORIGIN}/api/auth/step-up`, () => HttpResponse.json({ ok: true })),
    http.post(`${ORIGIN}/api/auth/login`, () => HttpResponse.json({ next: "mfa", challenge: "ch_1" })),
    http.post(`${ORIGIN}/api/auth/mfa`, () => HttpResponse.json({ token: "tok_session", user: fx.users[0] })),
    http.post(`${ORIGIN}/api/auth/accept`, () =>
      HttpResponse.json({
        next: "enroll",
        challenge: "ch_2",
        secret: "JBSWY3DPEHPK3PXP",
        otpauth_uri: "otpauth://totp/AgentLedger:x?secret=JBSWY3DPEHPK3PXP",
      }),
    ),
  ];
}

export type Outcome = 200 | "empty" | 401 | 403 | 404 | 409 | 410 | 500 | "network";

const DETAILS: Record<Exclude<Outcome, 200 | "empty" | "network">, string> = {
  401: "sign in required",
  403: "you are not engaged on this client; ask a CPA or your firm administrator",
  404: "client not found",
  409: "the stored evidence failed its integrity check (sha256 mismatch); it was not served",
  410: "deleted under the retention policy on 2026-01-15; the deletion receipt remains",
  500: "Internal Server Error",
};

export const OUTCOME_DETAILS = DETAILS;

/** Overrides a set of GET routes with one outcome; `empty` answers an empty array or an empty detail. */
export function outcomeHandlers(paths: string[], outcome: Outcome): HttpHandler[] {
  return paths.map((path) =>
    http.get(`${ORIGIN}${path}`, () => {
      switch (outcome) {
        case 200:
          return undefined;
        case "empty":
          if (path.endsWith("/documents")) return HttpResponse.json({ items: [], next_cursor: null, total: 0 });
          if (path.endsWith("/returns")) return HttpResponse.json([]);
          if (path === "/api/returns/:rid") return HttpResponse.json(fx.freshReturn);
          return HttpResponse.json(path.includes(":clientId") ? fx.detailOf({ ...fx.lakeside, facts: {} }) : []);
        case "network":
          return HttpResponse.error();
        default:
          return HttpResponse.json(
            { detail: DETAILS[outcome] },
            { status: outcome, headers: { "X-Request-Id": "req-test-1" } },
          );
      }
    }),
  );
}
