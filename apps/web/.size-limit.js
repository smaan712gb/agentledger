// The initial payload budget (250 kB gzip of JavaScript): the entry chunk plus everything it imports statically,
// read from Vite's manifest so lazily loaded route chunks do not count and vendor chunks are not forgotten.
import { readFileSync } from "node:fs";

const manifest = JSON.parse(readFileSync(new URL("./dist/.vite/manifest.json", import.meta.url), "utf8"));
const entryKey = Object.keys(manifest).find((k) => manifest[k].isEntry);
if (!entryKey) throw new Error("dist/.vite/manifest.json has no entry chunk; run `vite build` first");

const seen = new Set();
const js = [];
const css = new Set();
function walk(key) {
  const chunk = manifest[key];
  if (!chunk || seen.has(key)) return;
  seen.add(key);
  js.push(`dist/${chunk.file}`);
  for (const file of chunk.css ?? []) css.add(`dist/${file}`);
  for (const dep of chunk.imports ?? []) walk(dep);
}
walk(entryKey);

export default [
  { name: "initial JavaScript (entry + static imports, gzip)", path: js, limit: "250 kB", gzip: true },
  { name: "initial CSS (gzip)", path: [...css], limit: "40 kB", gzip: true },
];
