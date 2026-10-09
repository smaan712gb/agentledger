import { defineConfig, devices } from "@playwright/test";

// The end-to-end run drives the built app (vite preview) against the real API in multi-firm mode, both started by
// e2e/servers.mjs on an isolated AGENTLEDGER_HOME seeded by e2e/seed.py. Ports: E2E_WEB_PORT (4173) and
// E2E_API_PORT (8740).
const webPort = Number(process.env.E2E_WEB_PORT ?? "4173");
const baseURL = `http://127.0.0.1:${webPort}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts$/,
  // Each spec signs in with its own seeded users (TOTP codes cannot be replayed within a 30 s step), so spec
  // files may run in parallel while the tests inside one file stay serial.
  fullyParallel: false,
  workers: process.env.CI ? 2 : 3,
  retries: process.env.CI ? 1 : 0,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium-desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "chromium-mobile", use: { ...devices["Pixel 7"] }, testMatch: /signin\.spec\.ts$/ },
  ],
  webServer: {
    command: "node e2e/servers.mjs",
    url: `${baseURL}/healthz`,
    timeout: 300_000,
    reuseExistingServer: !process.env.CI,
    stdout: "pipe",
    stderr: "pipe",
  },
});
