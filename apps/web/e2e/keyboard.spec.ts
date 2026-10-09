import { expect, test, type Page } from "@playwright/test";

import { PASSWORD, apiPost, nextCode, suffix, user } from "./helpers";

/** Presses Tab until the locator has focus (bounded), so the test proves the element is in the tab order. */
async function tabTo(
  page: Page,
  name: RegExp | string,
  role: "link" | "button" | "textbox",
  limit = 40,
): Promise<void> {
  const target = page.getByRole(role, { name });
  for (let i = 0; i < limit; i++) {
    if (await target.evaluate((el) => el === document.activeElement).catch(() => false)) return;
    await page.keyboard.press("Tab");
  }
  await expect(target).toBeFocused();
}

test("sign in and open a client without touching the pointer", async ({ page, request }) => {
  const kai = user("kai");
  const clientId = `kbd-${suffix()}`;

  // Set-up through the API (its own sign-in, so the screen sign-in below uses the next one-time step).
  const step = (await (
    await request.post("/api/auth/login", { data: { email: kai.email, password: PASSWORD() } })
  ).json()) as { challenge: string };
  const mfa = (await (
    await request.post("/api/auth/mfa", { data: { challenge: step.challenge, code: await nextCode(kai) } })
  ).json()) as { token: string };
  await apiPost(request, mfa.token, "/api/clients", { id: clientId, name: "Keyboard Client", kind: "business" });

  await page.goto("/sign-in");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Email")).toBeFocused();
  await page.keyboard.type(kai.email);
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Password")).toBeFocused();
  await page.keyboard.type(PASSWORD());
  await page.keyboard.press("Enter");

  await expect(page.getByRole("heading", { name: "Two-step verification" })).toBeVisible();
  await expect(page.getByLabel("6-digit code")).toBeFocused();
  await page.keyboard.type(await nextCode(kai));
  await page.keyboard.press("Enter");

  await expect(page.getByRole("heading", { level: 1, name: "Clients" })).toBeVisible();
  // Signing in is a route change: focus lands on the main region of the new screen.
  await expect(page.locator("#main")).toBeFocused();

  // On a fresh load the skip link is the first stop and sends focus to the main region.
  await page.goto("/clients");
  await expect(page.getByRole("heading", { level: 1, name: "Clients" })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();

  await tabTo(page, "Keyboard Client", "link");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { level: 1, name: "Keyboard Client" })).toBeVisible();
  // After a route change focus is on the main region, so the next Tab starts inside the new screen.
  await expect(page.locator("#main")).toBeFocused();
  await tabTo(page, "Documents", "link");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("status", { name: "No documents yet" })).toBeVisible();
});
