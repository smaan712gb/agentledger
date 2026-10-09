/**
 * Return workspace flows the states contract does not cover: the never-zero cues, provenance opening the document
 * beside the field, saving the whole inputs object, conflicts and dispositions through their API, segregation of
 * duties and the workflow's refusal, the step-up on approval, the frozen inputs, and the unknown-transmission form.
 */
import { STEP_UP_REQUIRED } from "@agentledger/contracts";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { REASONS } from "../auth/can";
import * as fx from "./msw/fixtures";
import { ORIGIN } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

const WAGES = /^Wages, tips, other compensation \(box 1\)/;
const review = () => renderApp("/returns/ret_1/review", { me: fx.cpa });

describe("the inputs editor", () => {
  it("tells 'not stated' from 0 and marks a document item's missing required amount", async () => {
    review();
    await screen.findByTestId("inputs-editor");
    const w2a = screen.getByTestId("item-w2s[0]");
    const w2b = screen.getByTestId("item-w2s[1]");
    expect(within(w2a).getByLabelText(/^Qualified tips \(box 12 code TP\)/)).toHaveAccessibleDescription(
      /not stated \(not 0\)/,
    );
    expect(within(w2a).getByLabelText(/^Social security tips \(box 7\)/)).toHaveAccessibleDescription(
      /blank = default 0/,
    );
    const wagesB = within(w2b).getByLabelText(WAGES);
    expect(wagesB).toHaveValue("");
    expect(wagesB).toHaveAccessibleDescription(/missing — never taken as 0/);
    const wagesA = within(w2a).getByLabelText(WAGES);
    expect(wagesA).toHaveValue("61000");
    expect(within(w2a).getByRole("button", { name: /preparer · was 61200\.00 \(doc_w2a\)/ })).toBeInTheDocument();
    expect(w2a).toHaveTextContent("from document doc_w2a");
  });

  it("opens the document beside the field from its provenance chip, with the box label and the fact history", async () => {
    const user = userEvent.setup();
    review();
    await screen.findByTestId("inputs-editor");
    const w2a = screen.getByTestId("item-w2s[0]");
    await user.click(
      within(w2a).getByRole("button", { name: /doc_w2a · Box 2 — Federal income tax withheld · unconfirmed/ }),
    );
    const selected = screen.getByTestId("selected-field");
    expect(selected).toHaveTextContent("Federal income tax withheld (box 2)");
    expect(selected).toHaveTextContent("read from document doc_w2a");
    const history = await within(selected).findByTestId("fact-history");
    await within(history).findByText(/by u_admin/);
    const viewer = await screen.findByTestId("viewer");
    expect(viewer).toHaveTextContent("Box 2 — Federal income tax withheld");
    expect(viewer).toHaveTextContent("the document gave 6400.00");
    const frame = await within(viewer).findByTestId("viewer-frame");
    expect(frame.getAttribute("src")).toContain("/api/documents/doc_w2a/file?dl=signed&inline=1");
  });

  it("refuses an amount that is not a number and saves the whole inputs object with its identities", async () => {
    let body: unknown = null;
    server.use(
      http.put(`${ORIGIN}/api/returns/:rid/inputs`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(fx.returnResult);
      }),
    );
    const user = userEvent.setup();
    review();
    await screen.findByTestId("inputs-editor");
    const w2a = screen.getByTestId("item-w2s[0]");
    const wages = within(w2a).getByLabelText(WAGES);
    await user.clear(wages);
    await user.type(wages, "sixty");
    await user.tab();
    await screen.findByText("Enter the amount as a plain number, like 1234.50.");
    expect(screen.getByRole("button", { name: "Save and recompute" })).toHaveAttribute("aria-disabled", "true");
    await user.clear(wages);
    await user.type(wages, "61,200.00");
    await user.tab();
    await waitFor(() => expect(wages).toHaveValue("61200.00"));
    await user.click(screen.getByRole("button", { name: "Save and recompute" }));
    await waitFor(() => expect(body).not.toBeNull());
    const sent = body as { w2s: { source_document: string; wages: string }[]; taxpayer: { ssn: string } };
    expect(sent.w2s[0]).toMatchObject({ source_document: "doc_w2a", wages: "61200.00" });
    expect(sent.taxpayer.ssn).toBe("400-00-0009");
    await screen.findByText(/Saved and recomputed/);
  });
});

describe("conflicts and dispositions", () => {
  it("takes the document's value through the conflicts API", async () => {
    let body: unknown = null;
    server.use(
      http.post(`${ORIGIN}/api/returns/:rid/conflicts/:conflictId`, async ({ request, params }) => {
        body = { id: params.conflictId, ...((await request.json()) as object) };
        return HttpResponse.json({ open: 1 });
      }),
    );
    const user = userEvent.setup();
    review();
    const conflict = await screen.findByTestId("conflict-1");
    expect(conflict).toHaveTextContent("The return holds");
    expect(conflict).toHaveTextContent("61000");
    expect(conflict).toHaveTextContent("61200.00");
    expect(conflict).toHaveTextContent("Box 1 — Wages, tips, other compensation");
    await user.click(within(conflict).getByRole("button", { name: "Take the document's value" }));
    await waitFor(() => expect(body).toEqual({ id: "1", choice: "document" }));
    await screen.findByText("The document's value was taken.");
  });

  it("a missing amount can only be kept once entered; taking is refused with the API's words", async () => {
    review();
    const conflict = await screen.findByTestId("conflict-2");
    expect(conflict).toHaveTextContent("never taken as zero");
    expect(within(conflict).getByRole("button", { name: "Take the document's value" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(within(conflict).getByRole("button", { name: "Keep: I entered it" })).toBeInTheDocument();
    const w2b = screen.getByTestId("item-w2s[1]");
    expect(within(w2b).getByText(/open conflict: the document says nothing/)).toBeInTheDocument();
  });

  it("records a disposition for a filed document the return does not use", async () => {
    let body: unknown = null;
    server.use(
      http.post(`${ORIGIN}/api/returns/:rid/documents/:docId/disposition`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json([fx.dispositionFor("doc_nec")]);
      }),
    );
    const user = userEvent.setup();
    review();
    const row = await screen.findByTestId("year-doc-doc_nec");
    expect(row).toHaveTextContent("not on the return");
    expect(screen.getByTestId("year-doc-doc_w2a")).toHaveTextContent("on the return");
    await user.selectOptions(within(row).getByLabelText("Account for it as"), "entered_by_hand");
    await user.type(within(row).getByLabelText(/^Reason/), "entered on Schedule C line 1 from the 1099-NEC");
    await user.click(within(row).getByRole("button", { name: "Record" }));
    await waitFor(() =>
      expect(body).toEqual({ disposition: "entered_by_hand", note: "entered on Schedule C line 1 from the 1099-NEC" }),
    );
    await waitFor(() => expect(screen.getByTestId("year-doc-doc_nec")).toHaveTextContent("entered by hand"));
  });
});

describe("review authority", () => {
  it("the submitter cannot approve: the button stays, disabled with the API's reason", async () => {
    server.use(http.get(`${ORIGIN}/api/returns/:rid`, () => HttpResponse.json(fx.returnWith("in_review", fx.cpa.id))));
    const user = userEvent.setup();
    renderApp("/returns/ret_1", { me: fx.cpa });
    const approve = await screen.findByRole("button", { name: "Approve" });
    expect(approve).toHaveAttribute("aria-disabled", "true");
    await user.hover(approve);
    await screen.findAllByText(REASONS.segregation);
  });

  it("firm staff without reviewer authority see why they cannot approve", async () => {
    server.use(http.get(`${ORIGIN}/api/returns/:rid`, () => HttpResponse.json(fx.returnWith("in_review", "u_x"))));
    renderApp("/returns/ret_1", { me: fx.firmAdmin });
    expect(await screen.findByRole("button", { name: "Approve" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: "Request changes" })).toHaveAttribute("aria-disabled", "true");
  });

  it("the workflow's refusal (409) is rendered as the Conflict state, one line per reason", async () => {
    server.use(
      http.get(`${ORIGIN}/api/returns/:rid`, () => HttpResponse.json(fx.returnWith("in_review", "u_admin"))),
      http.post(`${ORIGIN}/api/returns/:rid/approve`, () =>
        HttpResponse.json(
          {
            detail:
              "the return changed after it was submitted for review; 2 fact conflict(s) between documents and the return must be resolved",
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderApp("/returns/ret_1", { me: fx.cpa });
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    const panel = await screen.findByRole("alert", { name: "This changed underneath you" });
    expect(within(panel).getAllByRole("listitem")).toHaveLength(2);
    expect(panel).toHaveTextContent("the return changed after it was submitted for review");
  });

  it("approving asks for a recent sign-in once and retries", async () => {
    let attempts = 0;
    server.use(
      http.get(`${ORIGIN}/api/returns/:rid`, () => HttpResponse.json(fx.returnWith("in_review", "u_admin"))),
      http.post(`${ORIGIN}/api/returns/:rid/approve`, () => {
        attempts++;
        if (attempts === 1) return HttpResponse.json({ detail: STEP_UP_REQUIRED }, { status: 403 });
        return HttpResponse.json({ status: "approved", history: [] });
      }),
    );
    const user = userEvent.setup();
    renderApp("/returns/ret_1", { me: fx.cpa });
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    expect(dialog).toHaveTextContent("approving the return");
    await user.type(screen.getByLabelText("One-time code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByText(/The return is now approved/);
    expect(attempts).toBe(2);
  });
});

describe("frozen inputs", () => {
  it("a hash-bound status locks the editor until unlocked, saying that saving reopens the return", async () => {
    server.use(http.get(`${ORIGIN}/api/returns/:rid`, () => HttpResponse.json(fx.returnWith("awaiting_signature"))));
    const user = userEvent.setup();
    review();
    const frozen = await screen.findByRole("status", { name: "Period closed" });
    expect(frozen).toHaveTextContent("bound to the hash the reviewer approved");
    const w2a = await screen.findByTestId("item-w2s[0]");
    const wages = within(w2a).getByLabelText(WAGES);
    expect(wages).toBeDisabled();
    await user.click(within(frozen).getByRole("button", { name: "Unlock to edit" }));
    await user.click(await screen.findByRole("button", { name: /^Unlock: I understand/ }));
    await waitFor(() => expect(wages).toBeEnabled());
  });

  it("a filed return is never edited: the editor is frozen with the amendment as the way, and no unlock", async () => {
    server.use(http.get(`${ORIGIN}/api/returns/:rid`, () => HttpResponse.json(fx.returnWith("accepted"))));
    review();
    const frozen = await screen.findByRole("status", { name: "Period closed" });
    expect(frozen).toHaveTextContent("never edited or recomputed");
    expect(frozen).toHaveTextContent("amendment");
    expect(screen.queryByRole("button", { name: "Unlock to edit" })).toBeNull();
    const w2a = await screen.findByTestId("item-w2s[0]");
    expect(within(w2a).getByLabelText(WAGES)).toBeDisabled();
    expect(screen.getByRole("button", { name: "Populate from documents" })).toHaveAttribute("aria-disabled", "true");
  });

  it("an accepted return's status page offers the amendment and the what-if preview", async () => {
    server.use(http.get(`${ORIGIN}/api/returns/:rid`, () => HttpResponse.json(fx.returnWith("accepted"))));
    const user = userEvent.setup();
    renderApp("/returns/ret_1", { me: fx.cpa });
    await screen.findByRole("button", { name: "Start an amendment" });
    await user.click(screen.getByRole("button", { name: "What-if under today's rules" }));
    const dialog = await screen.findByRole("dialog", { name: "What-if under today's rules" });
    await within(dialog).findByText(/^total tax$/i);
    expect(dialog).toHaveTextContent("$4,980.00");
  });

  it("an unknown transmission offers the reconciliation form and nothing else", async () => {
    let body: unknown = null;
    server.use(
      http.get(`${ORIGIN}/api/returns/:rid`, () => HttpResponse.json(fx.returnWith("unknown"))),
      http.post(`${ORIGIN}/api/returns/:rid/reconcile`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ status: "signed", history: [] });
      }),
    );
    const user = userEvent.setup();
    renderApp("/returns/ret_1", { me: fx.cpa });
    expect(await screen.findByRole("status", { name: "Period closed" })).toHaveTextContent("outcome is not known");
    expect(screen.queryByRole("button", { name: "Submit for review" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Reconcile the transmission" }));
    const dialog = await screen.findByRole("dialog", { name: "Reconcile the transmission" });
    await user.selectOptions(within(dialog).getByLabelText("Did the transmitter receive it?"), "no");
    await user.type(within(dialog).getByLabelText("Evidence"), "transmitter support ticket 4411: nothing received");
    await user.click(within(dialog).getByRole("button", { name: "Reconcile the transmission" }));
    await waitFor(() =>
      expect(body).toEqual({ submitted: false, evidence: "transmitter support ticket 4411: nothing received" }),
    );
  });
});

describe("status page and the returns list", () => {
  it("lists the blocking checklist with stable codes and links into the review", async () => {
    renderApp("/returns/ret_1", { me: fx.cpa });
    const list = await screen.findByRole("list", { name: "Blocking checklist" });
    expect(list).toHaveTextContent("missing_amount");
    expect(list).toHaveTextContent("unconfirmed");
    await waitFor(() => expect(list).toHaveTextContent("fact_conflict"));
    await waitFor(() => expect(list).toHaveTextContent("unaccounted_document"));
    expect(within(list).getAllByRole("link", { name: "Open the inputs" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("list", { name: "Workflow history" })).toHaveTextContent("started");
  });

  it("the client's returns tab lists the return and creates a Form 1040 for a tax year", async () => {
    let body: unknown = null;
    server.use(
      http.post(`${ORIGIN}/api/clients/:clientId/returns`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ id: "ret_new" });
      }),
    );
    const user = userEvent.setup();
    const { router } = renderApp("/clients/ortiz-auto/returns", { me: fx.firmAdmin });
    await screen.findByRole("link", { name: /Form 1040 · 2026/ });
    const year = screen.getByLabelText("Tax year");
    await user.clear(year);
    await user.type(year, "2025");
    await user.type(screen.getByLabelText("Taxpayer first name"), "Jordan");
    await user.type(screen.getByLabelText(/^Taxpayer SSN/), "400-00-0009");
    await user.click(screen.getByRole("button", { name: "Create return" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/returns/ret_new"));
    expect(body).toEqual({
      tax_year: 2025,
      inputs: { filing_status: "single", taxpayer: { first_name: "Jordan", ssn: "400-00-0009" } },
    });
    await screen.findByRole("status", { name: "Not computed yet" });
  });
});
