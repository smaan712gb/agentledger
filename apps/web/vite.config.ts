import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

/** The repository's BUILD_SHA ("dev" in git; the release stamps the commit). GET /healthz reports the API's copy. */
function buildSha(): string {
  try {
    return readFileSync(fileURLToPath(new URL("../../BUILD_SHA", import.meta.url)), "utf8").trim() || "dev";
  } catch {
    return "dev";
  }
}

// Where the API runs during development and the end-to-end run. The deployed app calls /api relatively from the
// same origin (Workers static assets in front of the container), so nothing here reaches production.
const apiTarget = `http://127.0.0.1:${process.env.API_PORT ?? "8740"}`;
const proxy = {
  "/api": apiTarget,
  "/healthz": apiTarget,
  "/static": apiTarget,
  "/legacy": apiTarget,
  "/openapi.json": apiTarget,
};

// Production builds carry a Content-Security-Policy meta tag. Scripts come only from this origin; styles from this
// origin and inline (Radix's scroll lock injects a <style> element); images include data: for the enrolment QR code.
// frame-ancestors cannot be set from a meta tag: the edge adds it as a header (docs/WEB.md).
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

function cspMeta(): Plugin {
  return {
    name: "agentledger:csp-meta",
    apply: "build",
    transformIndexHtml(html) {
      return {
        html,
        tags: [
          { tag: "meta", attrs: { "http-equiv": "Content-Security-Policy", content: CSP }, injectTo: "head-prepend" },
        ],
      };
    },
  };
}

export default defineConfig({
  plugins: [tanstackRouter({ target: "react", autoCodeSplitting: true }), react(), cspMeta()],
  define: { __BUILD_SHA__: JSON.stringify(buildSha()) },
  build: {
    target: "es2022",
    manifest: true,
    sourcemap: false,
  },
  server: { port: 5173, strictPort: true, proxy },
  preview: { port: Number(process.env.WEB_PORT ?? "4173"), strictPort: true, proxy },
});
