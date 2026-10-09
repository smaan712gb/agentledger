// The end-to-end servers, started by Playwright (playwright.config.ts `webServer`) or by hand:
//
//   node e2e/servers.mjs
//
// 1. A fresh AGENTLEDGER_HOME in the OS temp directory with copies of the repository's rules/, config/, golden/,
//    domains/, playbooks/, evals/ and pyproject.toml (what tests/conftest.py's `home` fixture copies).
// 2. e2e/seed.py creates the platform administrator, a firm and its accounts (TOTP enrolled) and writes
//    <home>/e2e-seed.json for the specs.
// 3. The API in multi-firm mode (never --dev) on E2E_API_PORT (8740), with the same environment pins the Python
//    test suite uses: SQLite stores, local identity, file blobs, no models, no agents, nothing from a developer's .env.
// 4. `vite build` (skip with E2E_SKIP_BUILD=1) and `vite preview` on E2E_WEB_PORT (4173), proxying /api, /healthz
//    and /static to the API, as vite.config.ts says.
//
// Cross-platform: no shell, quoted paths, the project's virtualenv Python by default (E2E_PYTHON overrides).
import { spawn, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(webDir, "..", "..");
const apiPort = Number(process.env.E2E_API_PORT ?? "8740");
const webPort = Number(process.env.E2E_WEB_PORT ?? "4173");
const home = process.env.E2E_HOME ?? path.join(os.tmpdir(), "agentledger-web-e2e");
const seedFile = path.join(home, "e2e-seed.json");

function defaultPython() {
  if (process.env.E2E_PYTHON) return process.env.E2E_PYTHON;
  const venv =
    process.platform === "win32"
      ? path.join(repoRoot, ".venv", "Scripts", "python.exe")
      : path.join(repoRoot, ".venv", "bin", "python");
  if (fs.existsSync(venv)) return venv;
  return process.platform === "win32" ? "python" : "python3";
}

function viteBin() {
  for (const candidate of [path.join(webDir, "node_modules", "vite"), path.join(repoRoot, "node_modules", "vite")]) {
    const bin = path.join(candidate, "bin", "vite.js");
    if (fs.existsSync(bin)) return bin;
  }
  throw new Error("vite is not installed; run `npm ci` at the repository root");
}

function log(prefix, chunk) {
  for (const line of String(chunk).split(/\r?\n/)) {
    if (line.trim()) process.stdout.write(`[${prefix}] ${line}\n`);
  }
}

async function waitFor(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (err) {
      lastError = err instanceof Error ? err.message : String(err);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`${url} did not answer within ${timeoutMs} ms (${lastError})`);
}

// ------------------------------------------------------------------------------------------------ 1. home
try {
  fs.rmSync(home, { recursive: true, force: true, maxRetries: 10, retryDelay: 500 });
} catch (err) {
  console.error(`[e2e] cannot clear ${home}: ${err instanceof Error ? err.message : String(err)}`);
  console.error(
    "[e2e] is an API from a previous run still running? Stop it (it holds the SQLite files open) and retry.",
  );
  process.exit(1);
}
fs.mkdirSync(home, { recursive: true });
for (const dir of ["rules", "config", "golden", "domains", "playbooks", "evals"]) {
  fs.cpSync(path.join(repoRoot, dir), path.join(home, dir), { recursive: true });
}
fs.copyFileSync(path.join(repoRoot, "pyproject.toml"), path.join(home, "pyproject.toml"));

// ------------------------------------------------------------------------------------------------ 2. environment
const env = {
  ...process.env,
  AGENTLEDGER_HOME: home,
  AGENTLEDGER_MASTER_KEY: crypto.randomBytes(32).toString("base64"),
  AGENTLEDGER_DATABASE: "sqlite",
  AGENTLEDGER_PLATFORM_DATABASE: "sqlite",
  AGENTLEDGER_ALLOW_SQLITE: "1",
  AGENTLEDGER_IDENTITY: "local",
  AGENTLEDGER_BLOBS: "file",
  AGENTLEDGER_OLLAMA_URL: "http://127.0.0.1:9",
  AGENTLEDGER_AGENTS: "0",
  // Document extraction answers come from e2e/fixtures (src/agentledger/ai/fixtures.py), never from a model; the
  // fixture router is refused unless this run is marked as an end-to-end run (or dev mode, which never runs here).
  AGENTLEDGER_E2E: "1",
  AGENTLEDGER_AI_FIXTURES: path.join(webDir, "e2e", "fixtures"),
  PYTHONPATH: path.join(repoRoot, "src"),
  PYTHONUTF8: "1",
  PYTHONIOENCODING: "utf-8",
};
// Nothing from a developer's environment reaches the run: no dev auth, no real identity provider, bucket or database.
for (const name of [
  "AGENTLEDGER_DEV_AUTH",
  "AGENTLEDGER_BUILD",
  "AGENTLEDGER_SMOKE_TOKEN",
  "ANTHROPIC_API_KEY",
  "AGENTLEDGER_WEBHOOK_SECRET",
]) {
  delete env[name];
}
for (const name of [
  "AGENTLEDGER_BLOB_ENDPOINT",
  "AGENTLEDGER_BLOB_BUCKET",
  "AGENTLEDGER_BLOB_ACCESS_KEY_ID",
  "AGENTLEDGER_BLOB_SECRET_ACCESS_KEY",
  "AGENTLEDGER_BLOB_PREFIX",
  "WORKOS_REDIRECT_URI",
  "WORKOS_API_KEY",
  "WORKOS_CLIENT_ID",
  "DATABASE_URL",
  "DATABASE_URL_UNPOOLED",
  "AGENTLEDGER_MIGRATION_URL",
  "AGENTLEDGER_RUNTIME_DATABASE_URL",
]) {
  env[name] = "";
}

const python = defaultPython();
console.log(`[e2e] home ${home}`);
console.log(`[e2e] python ${python}`);

// ------------------------------------------------------------------------------------------------ 3. seed, API
const seed = spawnSync(python, [path.join(webDir, "e2e", "seed.py"), seedFile], {
  env,
  cwd: repoRoot,
  stdio: "inherit",
});
if (seed.status !== 0) {
  console.error(`[e2e] seed failed (${seed.status ?? seed.signal})`);
  process.exit(seed.status ?? 1);
}

const children = [];
function stopAll() {
  for (const child of children) {
    if (child.exitCode !== null) continue;
    if (process.platform === "win32")
      spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    else child.kill("SIGTERM");
  }
}
process.on("exit", stopAll);
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => {
    stopAll();
    process.exit(0);
  });
}

const api = spawn(
  python,
  ["-m", "agentledger.cli", "serve", "--host", "127.0.0.1", "--port", String(apiPort), "--no-agents"],
  {
    env,
    cwd: repoRoot,
    stdio: ["ignore", "pipe", "pipe"],
  },
);
children.push(api);
api.stdout.on("data", (c) => log("api", c));
api.stderr.on("data", (c) => log("api", c));
api.on("exit", (code) => {
  console.error(`[e2e] the API exited (${code})`);
  stopAll();
  process.exit(1);
});
await waitFor(`http://127.0.0.1:${apiPort}/healthz`, 120_000);
console.log(`[e2e] API ready on ${apiPort}`);

// ------------------------------------------------------------------------------------------------ 4. web
const vite = viteBin();
const webEnv = { ...process.env, API_PORT: String(apiPort), WEB_PORT: String(webPort) };
if (process.env.E2E_SKIP_BUILD !== "1") {
  const build = spawnSync(process.execPath, [vite, "build"], { cwd: webDir, env: webEnv, stdio: "inherit" });
  if (build.status !== 0) {
    console.error(`[e2e] vite build failed (${build.status ?? build.signal})`);
    stopAll();
    process.exit(build.status ?? 1);
  }
}
const web = spawn(
  process.execPath,
  [vite, "preview", "--host", "127.0.0.1", "--port", String(webPort), "--strictPort"],
  {
    cwd: webDir,
    env: webEnv,
    stdio: ["ignore", "pipe", "pipe"],
  },
);
children.push(web);
web.stdout.on("data", (c) => log("web", c));
web.stderr.on("data", (c) => log("web", c));
web.on("exit", (code) => {
  console.error(`[e2e] vite preview exited (${code})`);
  stopAll();
  process.exit(1);
});
await waitFor(`http://127.0.0.1:${webPort}/healthz`, 60_000);
console.log(`[e2e] web ready on http://127.0.0.1:${webPort} (seed: ${seedFile})`);
