// `npm run return-schema`: src/return-schema.json, the JSON schema of the return inputs (IndividualReturn,
// src/agentledger/returns/model.py) as pydantic emits it, in the model's own field order (the editor shows fields in
// that order, so keys are not sorted; pydantic's output is deterministic), LF line endings. The web app's inputs editor
// is generated from it (apps/web/src/screens/returns/editor), so a field added to the model appears in the editor once
// this file is regenerated. `--check` exits 1 when the committed file differs from what the model now produces (the
// drift gate CI runs next to scripts/export_openapi.py). The model is not in openapi.json because
// PUT /api/returns/{rid}/inputs still takes an untyped body (docs/WEB.md, section 4).
//
// Python: E2E_PYTHON or AGENTLEDGER_PYTHON when set, else the repository's .venv, else `python` on the PATH.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repo = resolve(root, "..", "..");
const target = resolve(root, "src", "return-schema.json");
const check = process.argv.includes("--check");

function python() {
  if (process.env.E2E_PYTHON) return process.env.E2E_PYTHON;
  if (process.env.AGENTLEDGER_PYTHON) return process.env.AGENTLEDGER_PYTHON;
  const venv =
    process.platform === "win32"
      ? resolve(repo, ".venv", "Scripts", "python.exe")
      : resolve(repo, ".venv", "bin", "python");
  return existsSync(venv) ? venv : "python";
}

// Pydantic's rendering of Decimal fields changed between releases (2.13 adds a `pattern` to the string branch of
// `anyOf: [number, string]`, 2.14 does not), and CI installs the newest release allowed while a developer's venv may be
// older: the pattern is dropped before writing so the file depends on the model alone. The editor reads only `type`.
const code = [
  "import json, sys",
  `sys.path.insert(0, ${JSON.stringify(resolve(repo, "src"))})`,
  "from agentledger.returns.model import IndividualReturn",
  "def strip(o):",
  "    if isinstance(o, dict):",
  "        branches = o.get('anyOf') or []",
  "        if any(isinstance(b, dict) and b.get('type') == 'number' for b in branches):",
  "            for b in branches:",
  "                if isinstance(b, dict) and b.get('type') == 'string':",
  "                    b.pop('pattern', None)",
  "        for v in o.values():",
  "            strip(v)",
  "    elif isinstance(o, list):",
  "        for v in o:",
  "            strip(v)",
  "schema = IndividualReturn.model_json_schema()",
  "strip(schema)",
  "print(json.dumps(schema, indent=2))",
].join("\n");
const run = spawnSync(python(), ["-I", "-c", code], { encoding: "utf8", env: { ...process.env, PYTHONUTF8: "1" } });
if (run.status !== 0) {
  console.error(run.stderr || `python exited with ${run.status ?? run.signal}`);
  process.exit(1);
}
const generated = `${run.stdout.replace(/\r\n/g, "\n").trim()}\n`;

if (check) {
  const committed = existsSync(target) ? readFileSync(target, "utf8").replace(/\r\n/g, "\n") : "";
  if (committed !== generated) {
    const a = committed.split("\n");
    const b = generated.split("\n");
    const at = a.findIndex((line, i) => line !== b[i]);
    const line = at === -1 ? Math.min(a.length, b.length) : at;
    console.error(
      "packages/contracts/src/return-schema.json is out of date with IndividualReturn: run `npm run -w packages/contracts return-schema`.",
    );
    console.error(`first difference at line ${line + 1}:\n  committed: ${a[line] ?? "<end>"}\n  generated: ${b[line] ?? "<end>"}`);
    process.exit(1);
  }
  console.log("return-schema.json matches IndividualReturn.model_json_schema().");
} else {
  writeFileSync(target, generated, { encoding: "utf8" });
  console.log(`wrote ${target}`);
}
