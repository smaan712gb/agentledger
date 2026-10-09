// `npm run generate`: src/schema.d.ts from openapi.json with openapi-typescript's programmatic API, so that `check`
// (scripts/check.mjs) can regenerate the exact same text and compare. LF line endings, whatever the platform.
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, root } from "./render.mjs";

const target = resolve(root, "src", "schema.d.ts");
writeFileSync(target, await render(), { encoding: "utf8" });
console.log(`wrote ${target}`);
