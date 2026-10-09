import { expect, test } from "@playwright/test";

import { signIn, user } from "./helpers";

test.describe("fragments the API's sign-in callback leaves in the URL", () => {
  test("#signin_error shows the message on the sign-in page and is removed from the address", async ({ page }) => {
    await page.goto("/#signin_error=this%20invitation%20is%20invalid%20or%20has%20expired");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByRole("status")).toContainText("this invitation is invalid or has expired");
    expect(new URL(page.url()).hash).toBe("");
  });

  test("the previous interface's invitation links (/#/accept/<token>) open the accept screen", async ({ page }) => {
    await page.goto("/#/accept/legacy-token-123");
    await expect(page).toHaveURL(/\/accept\/legacy-token-123$/);
    await expect(page.getByRole("heading", { name: "Join AgentLedger" })).toBeVisible();
  });

  test("#step_up=ok and #link=ok confirm the round trip to the provider", async ({ page }) => {
    await signIn(page, user("dana"));
    // The provider's callback redirects to "/" with the fragment: a full navigation, read once at boot.
    await page.goto("/#step_up=ok");
    await expect(page.getByRole("status").filter({ hasText: "Verified. You can retry the action now." })).toBeVisible();
    expect(new URL(page.url()).hash).toBe("");
    await expect(page.getByRole("heading", { level: 1, name: "Clients" })).toBeVisible();

    await page.goto("/#link=ok");
    await expect(page.getByRole("status").filter({ hasText: "sign-in provider is now linked" })).toBeVisible();
    expect(new URL(page.url()).hash).toBe("");
  });

  test("#session=<token> is taken from the URL at once (a bad token sends to sign-in with a notice)", async ({
    page,
  }) => {
    await page.goto("/#session=not-a-real-token");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByRole("status")).toContainText("Your session ended");
    expect(new URL(page.url()).hash).toBe("");
    expect(await page.evaluate(() => sessionStorage.getItem("agentledger.session"))).toBeNull();
  });
});
