import { expect, test } from "@playwright/test";

import { checkA11y, signIn, suffix, user } from "./helpers";

test("create a client, read the context bar, record the accounting basis, and see the forced states", async ({
  page,
}) => {
  const noor = user("noor");
  const clientId = `ortiz-${suffix()}`;
  const year = new Date().getFullYear();
  await signIn(page, noor);

  await test.step("the clients list", async () => {
    await expect(page.getByRole("heading", { level: 1, name: "Clients" })).toBeVisible();
    await checkA11y(page, "clients");
    await page.getByRole("button", { name: "New client" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "New client" })).toBeVisible();
    await checkA11y(page, "new client");
  });

  await test.step("create the client", async () => {
    await page.getByLabel("Client id").fill(clientId);
    await page.getByLabel("Legal name").fill("Ortiz Auto");
    await page.getByLabel("Kind").selectOption("business");
    await page.getByLabel("Entity type").selectOption("s_corp");
    await page.getByLabel("Industry pack").selectOption("auto_repair");
    await page.getByRole("button", { name: "Create client" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Ortiz Auto" })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "created" })).toBeVisible();
  });

  await test.step("the context bar: firm, entity, period, basis", async () => {
    const context = page.getByRole("navigation", { name: "Context" });
    await expect(context.getByTestId("context-firm")).toHaveText("rivera-cpa");
    await expect(context.getByTestId("context-period")).toHaveText(`FY${year}`);
    await expect(context.getByTestId("context-basis")).toContainText("basis not recorded");
    await expect(context.getByLabel("Engagement")).toHaveValue("");
    await checkA11y(page, "client overview");

    await context.getByLabel("Period").selectOption(String(year - 1));
    await expect(page).toHaveURL(new RegExp(`year=${year - 1}`));
    await expect(context.getByTestId("context-period")).toHaveText(`FY${year - 1}`);
    await context.getByLabel("Period").selectOption(String(year));
  });

  await test.step("record the basis through the profile", async () => {
    await page.getByTestId("context-basis").getByRole("link").click();
    await expect(page.getByRole("heading", { level: 1, name: "Profile facts" })).toBeVisible();
    await checkA11y(page, "profile");
    await page.getByLabel("Accounting basis").selectOption("cash");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Ortiz Auto" })).toBeVisible();
    await expect(page.getByTestId("context-basis")).toContainText("cash");
  });

  await test.step("the client appears in the list and the search filters it", async () => {
    await page.getByRole("link", { name: "Clients" }).click();
    await expect(page.getByRole("table", { name: "Clients" })).toContainText("Ortiz Auto");
    await page.getByLabel("Search clients").fill("no-such-client");
    await expect(page.getByRole("status", { name: "No clients match" })).toBeVisible();
    await page.getByLabel("Search clients").fill("");
  });

  await test.step("forced states on the list: error with request id, forbidden, unreachable", async () => {
    await page.route("**/api/clients", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        headers: { "X-Request-Id": "req-e2e-500" },
        body: JSON.stringify({ detail: "Internal Server Error" }),
      }),
    );
    await page.goto("/clients");
    const error = page.getByRole("alert", { name: "Something went wrong" });
    await expect(error).toContainText("req-e2e-500");
    await expect(error.getByRole("button", { name: "Try again" })).toBeVisible();
    await checkA11y(page, "error state");

    await page.unroute("**/api/clients");
    await page.route("**/api/clients", (route) =>
      route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ detail: "CPA access required" }),
      }),
    );
    await page.goto("/clients");
    await expect(page.getByRole("alert", { name: "You do not have access" })).toContainText("CPA access required");
    await checkA11y(page, "forbidden state");

    await page.unroute("**/api/clients");
    await page.route("**/api/clients", (route) => route.abort("connectionrefused"));
    await page.goto("/clients");
    await expect(page.getByRole("alert", { name: "Could not reach the service" })).toBeVisible();
    await checkA11y(page, "unreachable state");
    await page.unroute("**/api/clients");
  });
});
