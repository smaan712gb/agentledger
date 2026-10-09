/** Axe on every screen of the slice with happy-path data. Colour contrast needs layout, so jsdom cannot judge it. */
import type { Me } from "@agentledger/contracts";
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { axe } from "vitest-axe";

import * as fx from "./msw/fixtures";
import { renderApp } from "./render";

const SCREENS: { path: string; me: Me | null; heading: RegExp }[] = [
  { path: "/sign-in", me: null, heading: /^Sign in$/ },
  { path: "/accept/tok_1", me: null, heading: /^Join AgentLedger$/ },
  { path: "/clients", me: fx.firmAdmin, heading: /^Clients$/ },
  { path: "/clients/new", me: fx.firmAdmin, heading: /^New client$/ },
  { path: "/clients/ortiz-auto", me: fx.firmAdmin, heading: /^Ortiz Auto$/ },
  { path: "/clients/ortiz-auto/documents", me: fx.firmAdmin, heading: /^Ortiz Auto$/ },
  { path: "/clients/ortiz-auto/documents/doc_1", me: fx.firmAdmin, heading: /^Ortiz Auto$/ },
  { path: "/clients/ortiz-auto/profile", me: fx.firmAdmin, heading: /^Ortiz Auto$/ },
  { path: "/clients/ortiz-auto/returns", me: fx.firmAdmin, heading: /^Ortiz Auto$/ },
  { path: "/returns/ret_1", me: fx.cpa, heading: /^Form 1040 · 2026$/ },
  { path: "/returns/ret_1/review", me: fx.cpa, heading: /^Form 1040 · 2026$/ },
  { path: "/inbox", me: fx.firmAdmin, heading: /^Intake inbox$/ },
  { path: "/team", me: fx.cpa, heading: /^Team & access$/ },
  { path: "/platform/firms", me: fx.platformAdmin, heading: /^Firms$/ },
  { path: "/portal", me: fx.clientUser, heading: /^Welcome, Sam$/ },
];

describe.each(SCREENS)("$path", ({ path, me, heading }) => {
  it("has no axe violations", async () => {
    const { container } = renderApp(path, { me });
    await screen.findByRole("heading", { level: 1, name: heading });
    if (path.includes("ortiz-auto")) await screen.findByTestId("context-basis");
    if (path === "/team") await screen.findByText("Lee Park");
    if (path === "/clients/ortiz-auto/returns") await screen.findByRole("link", { name: /Form 1040 · 2026/ });
    if (path === "/returns/ret_1") await screen.findByRole("list", { name: "Blocking checklist" });
    if (path === "/returns/ret_1/review") {
      await screen.findByTestId("inputs-editor");
      await screen.findByRole("list", { name: "Open conflicts" });
    }
    const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results).toHaveNoViolations();
  });
});
