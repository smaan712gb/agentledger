/** Interaction flows that the states contract does not cover: step-up, uploads with mixed results, the basis chip. */
import { HttpResponse, http } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { STEP_UP_REQUIRED } from "@agentledger/contracts";

import * as fx from "./msw/fixtures";
import { ORIGIN } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

describe("step-up", () => {
  it("a 403 step_up_required opens the code dialog and the action is retried once", async () => {
    let attempts = 0;
    server.use(
      http.post(`${ORIGIN}/api/platform/firms`, () => {
        attempts++;
        if (attempts === 1) return HttpResponse.json({ detail: STEP_UP_REQUIRED }, { status: 403 });
        return HttpResponse.json({
          firm: { ...fx.firms[0], id: "new-firm", name: "New Firm" },
          admin_invite_token: "tok_new",
        });
      }),
    );
    const user = userEvent.setup();
    renderApp("/platform/firms", { me: fx.platformAdmin });
    await screen.findByRole("heading", { level: 1, name: /^Firms$/ });
    await user.type(screen.getByLabelText("Firm name"), "New Firm");
    await user.type(screen.getByLabelText(/^Firm id/), "new-firm");
    await user.type(screen.getByLabelText("Administrator email"), "a@new.example");
    await user.click(screen.getByRole("button", { name: "Create firm" }));

    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    expect(dialog).toHaveTextContent("creating the firm");
    await user.type(screen.getByLabelText("One-time code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    const result = await screen.findByRole("dialog", { name: "Firm created" });
    expect(result).toHaveTextContent("/accept/tok_new");
    expect(attempts).toBe(2);
  });

  it("cancelling the dialog surfaces the refusal and does not retry", async () => {
    let attempts = 0;
    server.use(
      http.post(`${ORIGIN}/api/auth/invite`, () => {
        attempts++;
        return HttpResponse.json({ detail: STEP_UP_REQUIRED }, { status: 403 });
      }),
    );
    const user = userEvent.setup();
    renderApp("/team", { me: fx.firmAdmin });
    await screen.findByText("Lee Park");
    await user.type(screen.getByLabelText("Email"), "new@rivera.example");
    await user.click(screen.getByRole("button", { name: "Create invitation" }));
    await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await screen.findByRole("alert");
    expect(attempts).toBe(1);
  });
});

describe("documents", () => {
  it("a multi-file upload reports each file: filed, needs review, failed", async () => {
    let call = 0;
    server.use(
      http.post(`${ORIGIN}/api/documents/upload`, () => {
        call++;
        if (call === 1) {
          return HttpResponse.json([
            {
              id: "d1",
              client_id: "ortiz-auto",
              status: "filed",
              doc_type: "1099-INT",
              tax_year: 2025,
              confidence: 0.8,
              vault_path: "blob:a",
              retention_class: "tax",
              match: "explicit",
              name: "1099.txt",
              suggested_client: null,
            },
          ]);
        }
        if (call === 2) {
          return HttpResponse.json([
            {
              id: "d2",
              client_id: null,
              status: "needs_review",
              doc_type: "Other",
              tax_year: null,
              confidence: 0,
              vault_path: "blob:b",
              retention_class: null,
              match: "",
              name: "scan.bin",
              suggested_client: "ortiz-auto",
            },
          ]);
        }
        return HttpResponse.json(
          { detail: "the file could not be read" },
          { status: 500, headers: { "X-Request-Id": "req-u3" } },
        );
      }),
    );
    const user = userEvent.setup();
    renderApp("/clients/ortiz-auto/documents", { me: fx.firmAdmin });
    const input = await screen.findByTestId("upload-input");
    await user.upload(input, [new File(["a"], "1099.txt"), new File(["b"], "scan.bin"), new File(["c"], "broken.pdf")]);
    const panel = await screen.findByRole("alert", { name: "Partly done" });
    expect(panel).toHaveTextContent("1 of 3 completed, 1 need attention, 1 failed");
    expect(panel).toHaveTextContent("1099.txt");
    expect(panel).toHaveTextContent("waiting in the inbox");
    expect(panel).toHaveTextContent("the file could not be read");
  });

  it("downloads go through a signed link and a 410 is shown as Gone", async () => {
    server.use(
      http.get(`${ORIGIN}/api/documents/:docId/file`, () =>
        HttpResponse.json(
          { detail: "deleted under the retention policy on 2026-01-15; the deletion receipt remains" },
          { status: 410 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderApp("/clients/ortiz-auto/documents/doc_1", { me: fx.firmAdmin });
    await screen.findByRole("heading", { level: 1, name: /^Ortiz Auto$/ });
    await user.click(await screen.findByRole("button", { name: "Download 1099int.txt" }));
    const gone = await screen.findByRole("status", { name: "No longer stored" });
    expect(gone).toHaveTextContent("deletion receipt");
  });
});

describe("context bar", () => {
  it("shows the recorded basis, the period with its closing date and the firm", async () => {
    renderApp("/clients/ortiz-auto", { me: fx.firmAdmin });
    await waitFor(() => expect(screen.getByTestId("context-basis")).toHaveTextContent("cash"));
    expect(screen.getByTestId("context-period")).toHaveTextContent(/FY2026 · closed through/);
    expect(screen.getByTestId("context-firm")).toHaveTextContent("rivera-cpa");
  });

  it("the basis chip links to the profile when nothing is recorded", async () => {
    renderApp("/clients/lakeside-fuel", { me: fx.firmAdmin });
    await waitFor(() => expect(screen.getByTestId("context-basis")).toHaveTextContent("basis not recorded"));
    const chip = screen.getByTestId("context-basis");
    expect(chip.querySelector("a")?.getAttribute("href")).toContain("/clients/lakeside-fuel/profile");
  });

  it("the period lives in the URL and a frozen year says so", async () => {
    const { router } = renderApp("/clients/ortiz-auto?year=2025", { me: fx.firmAdmin });
    await screen.findByRole("status", { name: "Period closed" });
    expect(router.state.location.search).toMatchObject({ year: 2025 });
    await waitFor(() => expect(screen.getByTestId("context-period")).toHaveTextContent("FY2025"));
  });
});
