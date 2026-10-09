// Shared by generate.mjs and check.mjs: the TypeScript text openapi-typescript produces for openapi.json.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";

export const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const HEADER = [
  "/**",
  " * This file was generated from openapi.json by `npm run generate` (openapi-typescript). Do not edit it.",
  " * openapi.json is written by `python scripts/export_openapi.py` from the API's request and response models",
  " * (src/agentledger/api/schemas.py); src/types.ts names the schemas the app uses (docs/WEB.md).",
  " */",
  "",
].join("\n");

export async function render() {
  const snapshot = JSON.parse(readFileSync(resolve(root, "openapi.json"), "utf8"));
  // defaultNonNullable off: a field the API defaults (a request field the app may omit, a response field a handler sets
  // only in some cases and serializes with exclude_unset) stays optional in TypeScript, as it is on the wire.
  const ast = await openapiTS(snapshot, { alphabetize: true, emptyObjectsUnknown: true, defaultNonNullable: false });
  return HEADER + astToString(ast).replace(/\r\n/g, "\n");
}
