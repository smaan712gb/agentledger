import { expect, test } from "@playwright/test";

import { PASSWORD, checkA11y, currentStep, signIn, totpCode, userFor, wrongCode } from "./helpers";

// This spec runs on desktop and on a phone (playwright.config.ts); each project has its own seeded accounts.
test.describe("sign in", () => {
  test("the first sign-in enrols two-step verification and lands in the practice", async ({ page }, testInfo) => {
    const newbie = userFor(testInfo.project.name, "newbie");
    await page.goto("/sign-in");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await checkA11y(page, "sign-in");

    await page.getByLabel("Email").fill(newbie.email);
    await page.getByLabel("Password").fill(PASSWORD());
    await page.getByRole("button", { name: "Continue" }).click();

    await expect(page.getByRole("heading", { name: "Set up two-step verification" })).toBeVisible();
    await expect(page.getByRole("img", { name: /QR code/ })).toBeVisible();
    const code = page.getByLabel("6-digit code");
    await expect(code).toHaveAttribute("autocomplete", "one-time-code");
    await expect(code).toHaveAttribute("inputmode", "numeric");
    await checkA11y(page, "enrolment");

    const secret = ((await page.getByTestId("totp-secret").textContent()) ?? "").replace(/\s+/g, "");
    expect(secret.length).toBeGreaterThanOrEqual(16);
    await code.fill(totpCode(secret, currentStep()));
    await page.getByRole("button", { name: "Verify" }).click();

    await expect(page.getByRole("heading", { level: 1, name: "Clients" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Primary" })).toContainText("Inbox");
    await checkA11y(page, "clients (first sign-in)");
  });

  test("a wrong code is refused with the API's message", async ({ page }, testInfo) => {
    const wrong = userFor(testInfo.project.name, "wrong");
    await page.goto("/sign-in");
    await page.getByLabel("Email").fill(wrong.email);
    await page.getByLabel("Password").fill(PASSWORD());
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("heading", { name: "Two-step verification" })).toBeVisible();
    await page.getByLabel("6-digit code").fill(wrongCode(wrong));
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByRole("alert")).toContainText("that code is not valid");
    await checkA11y(page, "wrong code");
  });

  test("five wrong passwords lock the account for 15 minutes", async ({ page }, testInfo) => {
    const lock = userFor(testInfo.project.name, "lock");
    await page.goto("/sign-in");
    for (let attempt = 1; attempt <= 5; attempt++) {
      await page.getByLabel("Email").fill(lock.email);
      await page.getByLabel("Password").fill(`not the password ${attempt}`);
      await page.getByRole("button", { name: "Continue" }).click();
      await expect(page.getByRole("alert")).toContainText("email or password is incorrect");
    }
    await page.getByLabel("Password").fill(PASSWORD());
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(page.getByRole("alert")).toContainText("too many attempts; try again in 15 minutes");
    await checkA11y(page, "locked out");
  });

  test("signing out ends the session and says so", async ({ page }, testInfo) => {
    await signIn(page, userFor(testInfo.project.name, "sora"));
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByRole("status")).toContainText("Signed out.");
    expect(await page.evaluate(() => sessionStorage.getItem("agentledger.session"))).toBeNull();
    await page.goto("/clients");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    expect(page.url()).toContain("next=%2Fclients");
  });
});
