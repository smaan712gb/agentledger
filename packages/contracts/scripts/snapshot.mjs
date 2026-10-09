// Fetch /openapi.json from a running API and write it as the committed snapshot: keys sorted, two-space indent, LF.
//
//   node scripts/snapshot.mjs [http://127.0.0.1:8740]
//
// The API-side replacement (scripts/export_openapi.py, which needs no running server) is listed in docs/WEB.md.
import { writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const base = process.argv[2] ?? "http://127.0.0.1:8740";
const target = resolve(dirname(fileURLToPath(import.meta.url)), "..", "openapi.json");

function sortKeys(value) {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((k) => [k, sortKeys(value[k])]),
    );
  }
  return value;
}

const response = await fetch(new URL("/openapi.json", base));
if (!response.ok) {
  console.error(`GET ${base}/openapi.json -> ${response.status}`);
  process.exit(1);
}
const document = sortKeys(await response.json());
writeFileSync(target, JSON.stringify(document, null, 2) + "\n", { encoding: "utf8" });
console.log(
  `wrote ${target}: ${Object.keys(document.paths ?? {}).length} paths, ${document.info?.title} ${document.info?.version}`,
);
