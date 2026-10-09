/**
 * The states contract: every route of this slice, rendered against every outcome the API can produce, must show
 * the matching required state by its accessible name. A screen that improvises its own error handling fails here.
 */
import type { Me } from "@agentledger/contracts";
import { onlineManager } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { STATE_NAMES } from "../ui/states";
import * as fx from "./msw/fixtures";
import { OUTCOME_DETAILS, outcomeHandlers, type Outcome } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

interface RouteCase {
  path: string;
  me: Me | null;
  /** The GET routes whose outcome decides the screen's state. */
  primary: string[];
  /** The accessible name of the Empty state for the "empty" outcome, when the screen has one. */
  emptyTitle?: string;
  /** What proves the happy path rendered. */
  happy: RegExp;
}

const ROUTES: RouteCase[] = [
  { path: "/clients", me: fx.firmAdmin, primary: ["/api/clients"], emptyTitle: "No clients yet", happy: /^Clients$/ },
  { path: "/clients/new", me: fx.firmAdmin, primary: [], happy: /^New client$/ },
  { path: "/clients/ortiz-auto", me: fx.firmAdmin, primary: ["/api/clients/:clientId"], happy: /^Ortiz Auto$/ },
  {
    path: "/clients/ortiz-auto/documents",
    me: fx.firmAdmin,
    primary: ["/api/clients/:clientId", "/api/clients/:clientId/documents"],
    emptyTitle: "No documents yet",
    happy: /^Ortiz Auto$/,
  },
  {
    path: "/clients/ortiz-auto/documents/doc_1",
    me: fx.firmAdmin,
    primary: ["/api/clients/:clientId"],
    happy: /^Ortiz Auto$/,
  },
  { path: "/clients/ortiz-auto/profile", me: fx.firmAdmin, primary: ["/api/clients/:clientId"], happy: /^Ortiz Auto$/ },
  {
    path: "/inbox",
    me: fx.firmAdmin,
    primary: ["/api/documents/review"],
    emptyTitle: "Inbox zero",
    happy: /^Intake inbox$/,
  },
  {
    path: "/team",
    me: fx.firmAdmin,
    primary: ["/api/auth/users"],
    emptyTitle: "No accounts yet",
    happy: /^Team & access$/,
  },
  {
    path: "/platform/firms",
    me: fx.platformAdmin,
    primary: ["/api/platform/firms"],
    emptyTitle: "No firms yet",
    happy: /^Firms$/,
  },
  { path: "/portal", me: fx.clientUser, primary: ["/api/clients/:clientId"], happy: /^Welcome, Sam$/ },
  { path: "/sign-in", me: null, primary: [], happy: /^Sign in$/ },
  { path: "/accept/tok_1", me: null, primary: [], happy: /^Join AgentLedger$/ },
];

const OUTCOMES: Outcome[] = [200, "empty", 401, 403, 404, 409, 410, 500, "network"];

afterEach(() => {
  onlineManager.setOnline(true);
});

describe.each(ROUTES)("$path", (route) => {
  const outcomes = route.primary.length ? OUTCOMES : ([200] as Outcome[]);

  it.each(outcomes)("renders the required state for %s", async (outcome) => {
    if (outcome !== 200) server.use(...outcomeHandlers(route.primary, outcome));
    renderApp(route.path, { me: route.me });

    switch (outcome) {
      case 200:
        await screen.findByRole("heading", { level: 1, name: route.happy });
        expect(screen.queryByRole("alert")).toBeNull();
        break;
      case "empty":
        if (route.emptyTitle) {
          await screen.findByRole("status", { name: route.emptyTitle });
        } else {
          await screen.findByRole("heading", { level: 1 });
          expect(screen.queryByRole("alert")).toBeNull();
        }
        break;
      case 401:
        await screen.findByRole("heading", { level: 1, name: /^Sign in$/ });
        await screen.findByText(/Your session ended/);
        break;
      case 403: {
        const panel = await screen.findByRole("alert", { name: STATE_NAMES.forbidden });
        expect(panel).toHaveTextContent(OUTCOME_DETAILS[403]);
        break;
      }
      case 404:
        await screen.findByRole("status", { name: STATE_NAMES.notFound });
        break;
      case 409: {
        const panel = await screen.findByRole("alert", { name: STATE_NAMES.conflict });
        expect(panel).toHaveTextContent("integrity check");
        break;
      }
      case 410: {
        const panel = await screen.findByRole("status", { name: STATE_NAMES.gone });
        expect(panel).toHaveTextContent("deletion receipt");
        break;
      }
      case 500: {
        const panel = await screen.findByRole("alert", { name: STATE_NAMES.error });
        expect(panel).toHaveTextContent("req-test-1");
        expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
        break;
      }
      case "network":
        await screen.findByRole("alert", { name: "Could not reach the service" });
        break;
    }
  });
});

describe("stale and offline", () => {
  it("keeps the data on screen with a stale marker and shows the offline banner when the browser goes offline", async () => {
    renderApp("/clients", { me: fx.firmAdmin });
    await screen.findByRole("heading", { level: 1, name: /^Clients$/ });
    await screen.findByText("Ortiz Auto");
    onlineManager.setOnline(false);
    await screen.findByRole("status", { name: STATE_NAMES.offline });
    await screen.findByRole("status", { name: STATE_NAMES.stale });
    expect(screen.getByText("Ortiz Auto")).toBeInTheDocument();
    onlineManager.setOnline(true);
    await waitFor(() => expect(screen.queryByRole("status", { name: STATE_NAMES.offline })).toBeNull());
  });
});

describe("guards before any client-scoped query", () => {
  it("a platform administrator opening a client gets the API's 403 text without a request", async () => {
    let requested = false;
    server.use(...outcomeHandlers(["/api/clients/:clientId"], 200));
    server.events.on("request:start", ({ request }) => {
      if (request.url.includes("/api/clients/")) requested = true;
    });
    renderApp("/clients/ortiz-auto", { me: fx.platformAdmin });
    const panel = await screen.findByRole("alert", { name: STATE_NAMES.forbidden });
    expect(panel).toHaveTextContent("platform administrators manage firms");
    expect(requested).toBe(false);
  });

  it("staff not engaged on a client see why", async () => {
    renderApp("/clients/lakeside-fuel", { me: fx.staff });
    const panel = await screen.findByRole("alert", { name: STATE_NAMES.forbidden });
    expect(panel).toHaveTextContent("not engaged on this client");
  });

  it("a signed-out person is sent to sign in with the way back", async () => {
    const { router } = renderApp("/inbox", { me: null });
    await screen.findByRole("heading", { level: 1, name: /^Sign in$/ });
    expect(router.state.location.search).toMatchObject({ next: "/inbox" });
  });
});
