import { expect, test } from "@playwright/test";

import { PASSWORD, checkA11y, currentStep, signIn, suffix, totpCode, user } from "./helpers";

test("a firm administrator invites a CPA, who accepts the link, enrols and signs in", async ({ page, browser }) => {
  const maya = user("maya");
  const email = `cpa-${suffix()}@rivera.example`;
  await signIn(page, maya);

  await test.step("create the invitation", async () => {
    await page.getByRole("link", { name: "Team" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Team & access" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Firm accounts" })).toContainText("Maya Rivera");
    await checkA11y(page, "team");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Role").selectOption("cpa");
    await page.getByRole("button", { name: "Create invitation" }).click();
  });

  const link = await test.step("the link is shown once", async () => {
    const dialog = page.getByRole("dialog", { name: "Invitation created" });
    await expect(dialog).toContainText(email);
    const url = (await page.getByTestId("invite-link").textContent()) ?? "";
    expect(url).toMatch(/\/accept\/[\w-]+$/);
    await checkA11y(page, "invitation link");
    await page.getByRole("button", { name: "Done" }).click();
    await expect(dialog).toBeHidden();
    return url;
  });

  await test.step("the invitee accepts, enrols and lands in the practice", async () => {
    const context = await browser.newContext();
    const invitee = await context.newPage();
    await invitee.goto(new URL(link).pathname);
    await expect(invitee.getByRole("heading", { name: "Join AgentLedger" })).toBeVisible();
    await checkA11y(invitee, "accept invitation");
    await invitee.getByLabel("Full name").fill("Casey Invited");
    await invitee.getByLabel("Password").fill(PASSWORD());
    await invitee.getByRole("button", { name: "Continue" }).click();
    await expect(invitee.getByRole("heading", { name: "Set up two-step verification" })).toBeVisible();
    const secret = ((await invitee.getByTestId("totp-secret").textContent()) ?? "").replace(/\s+/g, "");
    await invitee.getByLabel("6-digit code").fill(totpCode(secret, currentStep()));
    await invitee.getByRole("button", { name: "Verify" }).click();
    await expect(invitee.getByRole("heading", { level: 1, name: "Clients" })).toBeVisible();
    await expect(invitee.getByText("Casey Invited")).toBeVisible();
    await context.close();
  });

  await test.step("the new account is listed with two-step on", async () => {
    await page.reload();
    const row = page.getByRole("row", { name: new RegExp(email) });
    await expect(row).toContainText("Casey Invited");
    await expect(row).toContainText("on");
  });

  await test.step("the same link cannot be used twice", async () => {
    const context = await browser.newContext();
    const again = await context.newPage();
    await again.goto(new URL(link).pathname);
    await again.getByLabel("Full name").fill("Someone Else");
    await again.getByLabel("Password").fill(PASSWORD());
    await again.getByRole("button", { name: "Continue" }).click();
    await expect(again.getByRole("alert")).toContainText("invalid or has expired");
    await context.close();
  });
});
