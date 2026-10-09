import { expect, test } from "@playwright/test";

import { checkA11y, nextCode, signIn, suffix, user } from "./helpers";

test("the platform administrator signs in, creates a firm through a step-up and gets the administrator's invite link", async ({
  page,
}) => {
  const ops = user("ops");
  const firmId = `lake-tax-${suffix()}`;
  await signIn(page, ops);

  await test.step("lands on the firms console", async () => {
    await expect(page.getByRole("heading", { level: 1, name: "Firms" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Firms on this platform" })).toContainText("Rivera CPA");
    await checkA11y(page, "firms");
  });

  await test.step("creating a firm needs a recent sign-in: the step-up dialog, then the action is retried", async () => {
    // The API asks for a step-up once the sign-in is five minutes old; the test makes the first attempt ask at once.
    let forced = false;
    await page.route("**/api/platform/firms", async (route) => {
      if (route.request().method() === "POST" && !forced) {
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
    await page.getByLabel("Firm name").fill("Lake Tax");
    await page.getByLabel(/^Firm id/).fill(firmId);
    await page.getByLabel("Administrator email").fill(`lee-${firmId}@lake.example`);
    await page.getByRole("button", { name: "Create firm" }).click();

    const stepUp = page.getByRole("dialog", { name: "Confirm it's you" });
    await expect(stepUp).toContainText("creating the firm");
    await checkA11y(page, "step-up dialog");
    await stepUp.getByLabel("One-time code").fill(await nextCode(ops));
    await stepUp.getByRole("button", { name: "Verify" }).click();

    const created = page.getByRole("dialog", { name: "Firm created" });
    await expect(created).toContainText("Lake Tax");
    expect(await page.getByTestId("invite-link").textContent()).toMatch(/\/accept\/[\w-]+$/);
    await checkA11y(page, "firm created");
    await created.getByRole("button", { name: "Done" }).click();
    await expect(page.getByRole("row", { name: new RegExp(firmId) })).toContainText("active");
    await page.unroute("**/api/platform/firms");
  });

  await test.step("platform administrators are kept out of client data, with the API's words", async () => {
    await page.goto("/clients/ortiz-auto");
    const forbidden = page.getByRole("alert", { name: "You do not have access" });
    await expect(forbidden).toContainText("platform administrators manage firms");
    await checkA11y(page, "forbidden");
  });
});
