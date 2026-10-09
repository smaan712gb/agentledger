import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import AxeBuilder from "@axe-core/playwright";
import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { Secret, TOTP } from "otpauth";

export interface SeedUser {
  email: string;
  name: string;
  role: string;
  secret: string | null;
  last_step: number | null;
}

export interface Seed {
  password: string;
  firm: string;
  firm_name: string;
  users: Record<string, SeedUser>;
}

/** What e2e/seed.py wrote (e2e/servers.mjs decides the path the same way). */
export function loadSeed(): Seed {
  const home = process.env.E2E_HOME ?? path.join(os.tmpdir(), "agentledger-web-e2e");
  return JSON.parse(fs.readFileSync(path.join(home, "e2e-seed.json"), "utf8")) as Seed;
}

export function user(key: string): SeedUser {
  const u = loadSeed().users[key];
  if (!u) throw new Error(`no seeded account "${key}"`);
  return u;
}

/** The account for a spec that runs in several Playwright projects: `<key>` on desktop, `<key>_mobile` on mobile. */
export function userFor(projectName: string, key: string): SeedUser {
  return user(projectName.includes("mobile") ? `${key}_mobile` : key);
}

export const PASSWORD = (): string => loadSeed().password;

const STEP_MS = 30_000;

/** RFC 6238: SHA-1, 30 s, 6 digits, the same as src/agentledger/security/totp.py. */
export function totpCode(secret: string, step: number): string {
  const totp = new TOTP({ algorithm: "SHA1", digits: 6, period: 30, secret: Secret.fromBase32(secret) });
  return totp.generate({ timestamp: step * STEP_MS });
}

export function currentStep(): number {
  return Math.floor(Date.now() / STEP_MS);
}

// The API remembers the last step each account used and refuses it again; it accepts one step of drift either
// side. Each worker process tracks the steps it spent so a second sign-in (or a step-up) right after the first gets
// the next step, waiting for the clock only when it must.
const spent = new Map<string, number>();

export async function nextCode(u: SeedUser): Promise<string> {
  if (!u.secret) throw new Error(`${u.email} is not enrolled`);
  const used = Math.max(spent.get(u.email) ?? -1, u.last_step ?? -1);
  let step = Math.max(currentStep(), used + 1);
  if (step > currentStep() + 1) {
    await new Promise((resolve) => setTimeout(resolve, (step - 1) * STEP_MS - Date.now() + 250));
    step = Math.max(step, currentStep());
  }
  spent.set(u.email, step);
  return totpCode(u.secret, step);
}

/** A code that is certainly wrong for this account right now. */
export function wrongCode(u: SeedUser): string {
  const now = currentStep();
  const secret = u.secret;
  const valid = new Set(secret ? [now - 1, now, now + 1].map((s) => totpCode(secret, s)) : []);
  for (const candidate of ["000000", "111111", "123456", "999999"]) {
    if (!valid.has(candidate)) return candidate;
  }
  return "000001";
}

/** Signs in through the real screens: email + password, then the one-time code. */
export async function signIn(page: Page, u: SeedUser, next?: string): Promise<void> {
  await page.goto(next ? `/sign-in?next=${encodeURIComponent(next)}` : "/sign-in");
  await page.getByLabel("Email").fill(u.email);
  await page.getByLabel("Password").fill(PASSWORD());
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: /Two-step verification/ })).toBeVisible();
  await page.getByLabel("6-digit code").fill(await nextCode(u));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
}

/** The session token of the signed-in page, for API calls that set up data. */
export async function sessionToken(page: Page): Promise<string> {
  const token = await page.evaluate(() => sessionStorage.getItem("agentledger.session"));
  if (!token) throw new Error("the page has no session");
  return token;
}

export async function apiPost<T>(
  request: APIRequestContext,
  token: string,
  pathName: string,
  data: unknown,
): Promise<T> {
  const response = await request.post(pathName, { data, headers: { Authorization: `Bearer ${token}` } });
  expect(response.ok(), `${pathName} -> ${response.status()} ${await response.text()}`).toBeTruthy();
  return (await response.json()) as T;
}

export function suffix(): string {
  return `${Date.now().toString(36)}${Math.floor(Math.random() * 1000).toString(36)}`;
}

const TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];

/**
 * Runs axe on the page as it is. Serious and critical findings fail the test; minor and moderate ones are printed
 * so the run's report lists them.
 */
export async function checkA11y(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const describe = (v: (typeof results.violations)[number]) =>
    `${v.id} (${v.impact ?? "n/a"}): ${v.help} @ ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`;
  const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  const other = results.violations.filter((v) => v.impact !== "serious" && v.impact !== "critical");
  for (const v of other) console.log(`[axe:${label}] ${describe(v)}`);
  expect(blocking.map(describe), `axe serious/critical findings on ${label}`).toEqual([]);
}
