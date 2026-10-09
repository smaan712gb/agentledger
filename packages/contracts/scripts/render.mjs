// Shared by generate.mjs and check.mjs: the TypeScript text openapi-typescript produces for openapi.json.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";

export const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const HEADER = [
  "/**",
  " * This file was generated from openapi.json by `npm run generate` (openapi-typescript). Do not edit it.",
  " * The API publishes no response schemas yet, so every body is `unknown`; src/types.ts carries the hand-maintained",
  " * shapes until it does (docs/WEB.md).",
  " */",
  "",
].join("\n");

export async function render() {
  const snapshot = JSON.parse(readFileSync(resolve(root, "openapi.json"), "utf8"));
  const ast = await openapiTS(snapshot, { alphabetize: true, emptyObjectsUnknown: true });
  return HEADER + astToString(ast).replace(/\r\n/g, "\n");
}
