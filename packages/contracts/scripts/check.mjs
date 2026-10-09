// `npm run check`: the committed src/schema.d.ts must be what `npm run generate` produces from openapi.json.
// Exit 1 with a hint when it is not (the snapshot changed without regenerating, or the other way round).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, root } from "./render.mjs";

const schemaPath = resolve(root, "src", "schema.d.ts");
const generated = await render();
const committed = readFileSync(schemaPath, "utf8").replace(/\r\n/g, "\n");

if (generated.trim() !== committed.trim()) {
  console.error(
    "packages/contracts/src/schema.d.ts is out of date with openapi.json: run `npm run -w packages/contracts generate`.",
  );
  process.exit(1);
}
const paths = Object.keys(JSON.parse(readFileSync(resolve(root, "openapi.json"), "utf8")).paths ?? {}).length;
console.log(`schema.d.ts matches openapi.json (${paths} paths).`);
