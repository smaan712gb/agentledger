// Writes the synthetic documents the return-review spec uploads and fixtures.json, the mapping the API's extraction
// fixture router reads (src/agentledger/ai/fixtures.py, enabled by e2e/servers.mjs with AGENTLEDGER_AI_FIXTURES):
//
//   node e2e/fixtures/make-fixtures.mjs      (in apps/web; deterministic, so the committed files only change on purpose)
//
// Each document is a one-page PDF with a real text layer (Helvetica, uncompressed), so the API's intake reads it with
// pypdf, its deterministic detector sees a 2026 Form W-2, every amount the fixture answers is grounded in the text, and
// the browser renders it inline through GET /api/documents/{id}/file?inline=1. The mapping is keyed by the SHA-256 of
// each file's bytes; the fixture router also matches the text it extracts from them. Nothing here is a real form.
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/** A minimal valid PDF whose page draws the lines as text (parentheses and backslashes escaped). */
export function minimalPdf(lines) {
  const escape = (s) => s.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
  const content = `BT /F1 11 Tf 14 TL 56 760 Td ${lines.map((l) => `(${escape(l)}) Tj T*`).join(" ")} ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${Buffer.byteLength(content, "latin1")} >>\nstream\n${content}\nendstream`,
  ];
  let out = "%PDF-1.4\n";
  const offsets = [];
  objects.forEach((obj, i) => {
    offsets.push(Buffer.byteLength(out, "latin1"));
    out += `${i + 1} 0 obj\n${obj}\nendobj\n`;
  });
  const xref = Buffer.byteLength(out, "latin1");
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const o of offsets) out += `${String(o).padStart(10, "0")} 00000 n \n`;
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}

/** The two W-2s of one employee (a single filer) for 2026: two employers, so the return carries two W-2 items. */
export const W2S = [
  {
    file: "w2-brightline-2026.pdf",
    employer: "Brightline LLC",
    ein: "82-1234567",
    boxes: { box1: "61200.00", box2: "6400.00", box3: "61200.00", box4: "3794.40", box5: "61200.00", box6: "887.40" },
  },
  {
    file: "w2-harbor-2026.pdf",
    employer: "Harbor Coffee Co.",
    ein: "47-7654321",
    boxes: { box1: "8400.00", box2: "600.00", box3: "8400.00", box4: "520.80", box5: "8400.00", box6: "121.80" },
  },
];

const EMPLOYEE = { name: "Jordan Lee", last4: "0009" };
const LABELS = {
  box1: "Box 1 Wages, tips, other compensation",
  box2: "Box 2 Federal income tax withheld",
  box3: "Box 3 Social security wages",
  box4: "Box 4 Social security tax withheld",
  box5: "Box 5 Medicare wages and tips",
  box6: "Box 6 Medicare tax withheld",
};

/** "61200.00" -> "61,200.00", as a printed form shows it (intake grounds the amount either way). */
function printed(amount) {
  const [whole, cents] = amount.split(".");
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${cents}`;
}

export function documentLines(w2) {
  return [
    "Form W-2 Wage and Tax Statement 2026",
    "Copy B. To be filed with the employee federal tax return.",
    `Employer: ${w2.employer}   EIN ${w2.ein}`,
    `Employee: ${EMPLOYEE.name}   SSN XXX-XX-${EMPLOYEE.last4}`,
    ...Object.entries(w2.boxes).map(([box, amount]) => `${LABELS[box]} ${printed(amount)}`),
    "Synthetic document for AgentLedger end-to-end tests. Not a real form.",
  ];
}

export function response(w2) {
  return {
    doc_type: "W-2",
    tax_year: 2026,
    party_names: [EMPLOYEE.name, w2.employer],
    tin_last4: [EMPLOYEE.last4],
    fields: {
      employer_name: w2.employer,
      employer_ein: w2.ein,
      recipient_name: EMPLOYEE.name,
      recipient_tin_last4: EMPLOYEE.last4,
      ...w2.boxes,
    },
    summary: `2026 Form W-2 from ${w2.employer} for ${EMPLOYEE.name}`,
    confidence: 0.97,
  };
}

const mapping = {};
for (const w2 of W2S) {
  const bytes = minimalPdf(documentLines(w2));
  fs.writeFileSync(path.join(here, w2.file), bytes);
  mapping[createHash("sha256").update(bytes).digest("hex")] = { file: w2.file, response: response(w2) };
}
fs.writeFileSync(path.join(here, "fixtures.json"), `${JSON.stringify(mapping, null, 2)}\n`);
console.log(`wrote ${W2S.length} documents and fixtures.json in ${here}`);
