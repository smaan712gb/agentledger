import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type APIRequestContext } from "@playwright/test";

import {
  PASSWORD,
  apiPost,
  checkA11y,
  currentStep,
  nextCode,
  signIn,
  suffix,
  totpCode,
  user,
  type SeedUser,
} from "./helpers";

const fixtures = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "fixtures");

// Several sign-ins and a step-up: every one-time code needs its own 30-second step, so this spec waits for the clock.
test.setTimeout(300_000);

/** Signs in through the API (spending one one-time step) and returns the session token. */
async function apiSignIn(request: APIRequestContext, u: SeedUser): Promise<string> {
  const login = await request.post("/api/auth/login", { data: { email: u.email, password: PASSWORD() } });
  const step = (await login.json()) as { challenge: string };
  const mfa = await request.post("/api/auth/mfa", { data: { challenge: step.challenge, code: await nextCode(u) } });
  expect(mfa.ok(), await mfa.text()).toBeTruthy();
  return ((await mfa.json()) as { token: string }).token;
}

/**
 * The invite flow (invite.spec drives it through the screens): the firm administrator invites a CPA, who accepts the
 * link and enrols. The account comes back ready to sign in, with the step its enrolment code used.
 */
async function inviteCpa(
  request: APIRequestContext,
  adminToken: string,
  email: string,
  name: string,
): Promise<SeedUser> {
  const invite = await apiPost<{ invite_token: string }>(request, adminToken, "/api/auth/invite", {
    email,
    role: "cpa",
  });
  const accept = await request.post("/api/auth/accept", {
    data: { token: invite.invite_token, name, password: PASSWORD() },
  });
  expect(accept.ok(), await accept.text()).toBeTruthy();
  const enrol = (await accept.json()) as { challenge: string; secret: string };
  const step = currentStep();
  const mfa = await request.post("/api/auth/mfa", {
    data: { challenge: enrol.challenge, code: totpCode(enrol.secret, step) },
  });
  expect(mfa.ok(), await mfa.text()).toBeTruthy();
  return { email, name, role: "cpa", secret: enrol.secret, last_step: step };
}

test("two CPAs prepare, review and approve a Form 1040 populated from two W-2s", async ({ page, request }) => {
  const admin = user("ravi");
  const tag = suffix();
  const clientId = `rr-${tag}`;
  const adminToken = await apiSignIn(request, admin);
  const preparer = await inviteCpa(request, adminToken, `prep-${tag}@rivera.example`, "Priya Preparer");
  const reviewer = await inviteCpa(request, adminToken, `rev-${tag}@rivera.example`, "Tomas Reviewer");
  await apiPost(request, adminToken, "/api/clients", { id: clientId, name: "Jordan Lee", kind: "individual" });
  await signIn(page, preparer);

  await test.step("upload two W-2s; intake reads them from the extraction fixtures and files them", async () => {
    await page.goto(`/clients/${clientId}/documents`);
    await page
      .getByTestId("upload-input")
      .setInputFiles([path.join(fixtures, "w2-brightline-2026.pdf"), path.join(fixtures, "w2-harbor-2026.pdf")]);
    const outcome = page.locator("[data-state='success']");
    await expect(outcome).toContainText("2 of 2 completed");
    await expect(outcome).toContainText("filed as W-2 2026");
  });

  let rid = "";
  await test.step("create the return without the SSN: the checklist names the engine's blocking code", async () => {
    await page.getByRole("link", { name: "Returns" }).click();
    await expect(page.getByRole("status", { name: "No returns yet" })).toBeVisible();
    await checkA11y(page, "returns (empty)");
    await page.getByLabel("Tax year").fill("2026");
    await page.getByLabel("Taxpayer first name").fill("Jordan");
    await page.getByLabel("Taxpayer last name").fill("Lee");
    await page.getByLabel("Taxpayer date of birth").fill("1990-01-01");
    await page.getByRole("button", { name: "Create return" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Form 1040 · 2026" })).toBeVisible();
    rid = /\/returns\/(ret_[a-f0-9]+)/.exec(page.url())?.[1] ?? "";
    expect(rid).toMatch(/^ret_/);
    await expect(page.getByTestId("context-entity")).toContainText("Jordan Lee");
    await expect(page.getByRole("status", { name: "Not computed yet" })).toBeVisible();
    await checkA11y(page, "return status (new)");

    await page.getByRole("button", { name: "Populate from documents" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Populated from 2 document(s)" })).toBeVisible();
    const checklist = page.getByRole("list", { name: "Blocking checklist" });
    await expect(checklist).toContainText("taxpayer_ssn_missing");
    await expect(checklist).toContainText("unconfirmed");
    await checkA11y(page, "return status (blockers)");
  });

  await test.step("review: provenance chips, the document beside the field, 'not stated' versus 0", async () => {
    await page.getByRole("link", { name: "Review" }).click();
    await expect(page.getByTestId("inputs-editor")).toBeVisible();
    const w2a = page.getByTestId(/^item-w2s\[\d+\]$/).filter({ hasText: "Brightline LLC" });
    await expect(w2a).toContainText("Brightline LLC");
    await expect(w2a.getByLabel("Wages, tips, other compensation (box 1)")).toHaveValue("61200.00");
    await expect(
      page
        .getByTestId(/^item-w2s\[\d+\]$/)
        .filter({ hasText: "Harbor Coffee Co." })
        .getByLabel("Wages, tips, other compensation (box 1)"),
    ).toHaveValue("8400.00");
    await expect(w2a.getByLabel("Qualified tips (box 12 code TP)")).toHaveAccessibleDescription(/not stated \(not 0\)/);
    await expect(w2a.getByLabel("Social security tips (box 7)")).toHaveAccessibleDescription(/blank = default 0/);
    await page.getByLabel("Social Security number").fill("400-00-0009");

    await w2a.getByRole("button", { name: /Box 1 — Wages, tips, other compensation · unconfirmed/ }).click();
    const viewer = page.getByTestId("document-viewer");
    await expect(viewer).toContainText("w2-brightline-2026.pdf");
    await expect(viewer).toContainText("Box 1 — Wages, tips, other compensation");
    await expect(viewer).toContainText("the document gave 61200.00");
    const frame = viewer.getByTestId("viewer-frame");
    await expect(frame).toBeVisible();
    const src = await frame.getAttribute("src");
    expect(src).toMatch(/\/api\/documents\/doc_[a-f0-9]+\/file\?dl=.+&inline=1$/);
    // The signed inline link really serves the PDF inline, sandboxed, with its real media type.
    const inline = await request.get(src ?? "");
    expect(inline.status()).toBe(200);
    expect(inline.headers()["content-type"]).toContain("application/pdf");
    expect(inline.headers()["content-disposition"]).toMatch(/^inline/);
    expect(inline.headers()["content-security-policy"]).toContain("sandbox");
    await expect(page.getByTestId("fact-history")).toContainText("document");
    await checkA11y(page, "review (document open)");
  });

  await test.step("edit over the document's value, save; populating again raises the conflict; take the document's value", async () => {
    const w2a = page.getByTestId(/^item-w2s\[\d+\]$/).filter({ hasText: "Brightline LLC" });
    const wages = w2a.getByLabel("Wages, tips, other compensation (box 1)");
    await wages.fill("61000");
    await wages.blur();
    await expect(page.getByTestId("dirty-state")).toHaveText("Unsaved changes.");
    await page.getByRole("button", { name: "Save and recompute" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Saved and recomputed" })).toBeVisible();
    await expect(w2a.getByRole("button", { name: /preparer · was 61200\.00/ })).toBeVisible();

    await page.getByRole("button", { name: "Populate from documents" }).click();
    await expect(page.getByRole("status").filter({ hasText: "1 open conflict(s)" })).toBeVisible();
    const conflicts = page.getByRole("list", { name: "Open conflicts" });
    await expect(conflicts).toContainText("The return holds");
    await expect(conflicts).toContainText("61000");
    await expect(conflicts).toContainText("The document says");
    await expect(conflicts).toContainText("61200.00");
    await expect(w2a).toContainText("open conflict: the document says 61200.00");
    await checkA11y(page, "review (conflict)");
    await conflicts.getByRole("button", { name: "Take the document's value" }).click();
    await expect(page.getByRole("status").filter({ hasText: "The document's value was taken." })).toBeVisible();
    await expect(w2a.getByLabel("Wages, tips, other compensation (box 1)")).toHaveValue("61200.00");
    await expect(w2a.getByRole("button", { name: /resolved · doc_/ })).toBeVisible();
    await expect(page.getByRole("status", { name: "No open conflicts" })).toBeVisible();
  });

  await test.step("confirm, compute and submit; the submitter cannot approve", async () => {
    await page.getByRole("button", { name: /Confirm document amounts/ }).click();
    await expect(page.getByRole("status").filter({ hasText: "document amount(s) confirmed" })).toBeVisible();
    await page.getByRole("button", { name: "Compute", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "Computed." })).toBeVisible();
    await page.getByRole("link", { name: "Status" }).click();
    await expect(page.getByText(/Nothing blocks the next step/)).toBeVisible();
    await expect(page.getByText("Adjusted gross income")).toBeVisible();
    await page.getByRole("button", { name: "Submit for review" }).click();
    const dialog = page.getByRole("dialog", { name: "Submit for review" });
    await checkA11y(page, "submit dialog");
    await dialog.getByRole("button", { name: "Submit for review" }).click();
    await expect(page.getByText(/Submitted for review · version/)).toBeVisible();
    await expect(page.getByText(/waiting on reviewer/)).toBeVisible();
    const approve = page.getByRole("button", { name: "Approve" });
    await expect(approve).toHaveAttribute("aria-disabled", "true");
    await approve.hover();
    await expect(page.getByRole("tooltip")).toContainText("the reviewer must be a different person from the preparer");
    await checkA11y(page, "return status (in review)");
  });

  await test.step("the reviewer approves through a forced step-up and requests the signature; the inputs are frozen", async () => {
    await page.getByRole("button", { name: "Sign out" }).click();
    await signIn(page, reviewer, `/returns/${rid}`);
    await expect(page.getByRole("heading", { level: 1, name: "Form 1040 · 2026" })).toBeVisible();
    // A real sign-in is fresh for five minutes; the first attempt is made to ask for the step-up at once.
    let forced = false;
    await page.route(`**/api/returns/${rid}/approve`, async (route) => {
      if (!forced) {
        forced = true;
        await route.fulfill({
          status: 403,
          contentType: "application/json",
          body: JSON.stringify({ detail: "step_up_required" }),
        });
        return;
      }
      await route.continue();
    });
    await page.getByRole("button", { name: "Approve" }).click();
    const stepUp = page.getByRole("dialog", { name: "Confirm it's you" });
    await expect(stepUp).toContainText("approving the return");
    await checkA11y(page, "step-up (approve)");
    await stepUp.getByLabel("One-time code").fill(await nextCode(reviewer));
    await stepUp.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText(/Approved by the reviewer · version/)).toBeVisible();
    await page.unroute(`**/api/returns/${rid}/approve`);

    await page.getByRole("button", { name: "Request signature" }).click();
    await expect(page.getByText(/Awaiting the taxpayer's signature/)).toBeVisible();
    await expect(page.getByText(/waiting on taxpayer signature on Form 8879/)).toBeVisible();
    await expect(page.getByRole("list", { name: "Workflow history" })).toContainText("request signature");
    await checkA11y(page, "return status (awaiting signature)");

    await page.getByRole("link", { name: "Review" }).click();
    const frozen = page.getByRole("status", { name: "Period closed" });
    await expect(frozen).toContainText("bound to the hash the reviewer approved");
    await expect(frozen.getByRole("button", { name: "Unlock to edit" })).toBeVisible();
    await expect(
      page
        .getByTestId(/^item-w2s\[\d+\]$/)
        .filter({ hasText: "Brightline LLC" })
        .getByLabel("Wages, tips, other compensation (box 1)"),
    ).toBeDisabled();
    await expect(page.getByRole("button", { name: "Save and recompute" })).toHaveAttribute("aria-disabled", "true");
    await checkA11y(page, "review (frozen)");
  });

  await test.step("forced states: the workflow's refusal and the unknown-transmission recovery form", async () => {
    await page.route(`**/api/returns/${rid}/compute`, (route) =>
      route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({ detail: "forced for the test: first reason; second reason" }),
      }),
    );
    await page.getByRole("button", { name: "Compute", exact: true }).click();
    const refused = page.getByRole("alert", { name: "This changed underneath you" });
    await expect(refused.getByRole("listitem")).toHaveCount(2);
    await expect(refused).toContainText("second reason");
    await checkA11y(page, "review (409)");
    await page.unroute(`**/api/returns/${rid}/compute`);

    await page.route(`**/api/returns/${rid}`, async (route) => {
      const response = await route.fetch();
      const json = (await response.json()) as Record<string, unknown>;
      await route.fulfill({
        response,
        json: {
          ...json,
          status: "unknown",
          allowed: ["reconciled_not_submitted", "reconciled_submitted"],
          waiting_on: "reconciliation with the transmitter",
        },
      });
    });
    await page.goto(`/returns/${rid}`);
    await expect(page.getByRole("status", { name: "Period closed" })).toContainText("outcome is not known");
    await expect(page.getByRole("button", { name: "Submit for review" })).toBeHidden();
    await page.getByRole("button", { name: "Reconcile the transmission" }).click();
    const dialog = page.getByRole("dialog", { name: "Reconcile the transmission" });
    await expect(dialog.getByLabel("Did the transmitter receive it?")).toBeVisible();
    await expect(dialog.getByLabel("Evidence")).toBeVisible();
    await checkA11y(page, "reconcile form");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await page.unroute(`**/api/returns/${rid}`);
  });
});
