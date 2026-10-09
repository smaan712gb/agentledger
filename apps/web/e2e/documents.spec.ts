import { randomBytes } from "node:crypto";

import { expect, test } from "@playwright/test";

import { apiPost, checkA11y, sessionToken, signIn, suffix, user } from "./helpers";

test("a multi-file upload reports each file; one lands in the inbox; the filed one downloads through a signed link", async ({
  page,
  request,
}) => {
  const lee = user("lee");
  const clientId = `docs-${suffix()}`;
  // Unique bytes per run: the API stores each document once by content hash and reports a repeat as "already stored".
  const FORM_TEXT = `Form 1099-INT 2025 Payer: First Bank Recipient: Jordan Lee SSN 123-45-6789 Interest income 1,234.56 Account ${clientId}`;
  await signIn(page, lee);
  const token = await sessionToken(page);
  await apiPost(request, token, "/api/clients", { id: clientId, name: "Docs Client", kind: "individual" });

  await test.step("empty documents tab", async () => {
    await page.goto(`/clients/${clientId}/documents`);
    await expect(page.getByRole("heading", { level: 1, name: "Docs Client" })).toBeVisible();
    await expect(page.getByRole("status", { name: "No documents yet" })).toBeVisible();
    await checkA11y(page, "documents (empty)");
  });

  await test.step("upload a recognisable form and an unreadable binary", async () => {
    await page.getByTestId("upload-input").setInputFiles([
      { name: "1099int.txt", mimeType: "text/plain", buffer: Buffer.from(FORM_TEXT) },
      { name: "scan.bin", mimeType: "application/octet-stream", buffer: randomBytes(96) },
    ]);
    const outcome = page.locator("[data-state='partial-success']");
    await expect(outcome).toBeVisible();
    await expect(outcome).toContainText("1 of 2 completed, 1 need attention");
    await expect(outcome).toContainText("1099int.txt");
    await expect(outcome).toContainText("filed as 1099-INT");
    await expect(outcome).toContainText("scan.bin");
    await expect(outcome).toContainText("waiting in the inbox");
    await checkA11y(page, "partial success");
    await expect(page.getByRole("table", { name: /Documents of Docs Client/ })).toContainText("1099int.txt");
  });

  await test.step("download through a signed link (attachment)", async () => {
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download 1099int.txt" }).click();
    const file = await download;
    expect(file.suggestedFilename()).toBe("1099int.txt");
    const chunks: Buffer[] = [];
    const stream = (await file.createReadStream()) as AsyncIterable<Buffer>;
    for await (const chunk of stream) chunks.push(chunk);
    expect(Buffer.concat(chunks).toString("utf8")).toBe(FORM_TEXT);
  });

  await test.step("the document page lists its stored version", async () => {
    await page.getByRole("link", { name: "1099int.txt" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "1099int.txt" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Stored versions" })).toContainText("received");
    await checkA11y(page, "document");
  });

  await test.step("the inbox holds the unreadable file; filing it to the client clears it", async () => {
    await page.getByRole("link", { name: "Inbox" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Intake inbox" })).toBeVisible();
    const row = page.getByRole("row", { name: /scan\.bin/ });
    await expect(row).toBeVisible();
    await checkA11y(page, "inbox");
    await row.getByRole("combobox").selectOption(clientId);
    await expect(page.getByRole("status").filter({ hasText: "filed to Docs Client" })).toBeVisible();
    await expect(row).toBeHidden();
  });
});
