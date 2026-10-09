/**
 * The engine's never-zero rule, mirrored from src/agentledger/returns/facts.py so the editor can say it where it
 * applies: a document item without its required amount (a W-2 without wages) is not a $0 item, an amount another
 * amount implies (Medicare wages when Medicare tax was withheld) must be positive, and a required code is never
 * defaulted. The engine blocks review on every one of these; the editor shows the cue next to the field. The API is
 * the authority: these tables are kept in step with facts.py by hand.
 */

import { getPath, isRecord, itemIdentity } from "./paths";

/** facts.REQUIRED: the amount without which a document item means nothing. */
export const REQUIRED: Record<string, string> = {
  w2s: "wages",
  interest: "interest",
  dividends: "ordinary",
  retirement: "gross_distribution",
  social_security: "net_benefits",
  unemployment: "amount",
  hsa_distributions: "gross_distribution",
  hsa_contributions: "total_contributions",
  ira_accounts: "fmv",
  marketplace_coverage: "annual_premium",
};

/** facts.IMPLIED: [amount, the amount that implies it]. */
export const IMPLIED: Record<string, [string, string][]> = {
  w2s: [
    ["medicare_wages", "medicare_tax"],
    ["ss_wages", "ss_tax"],
  ],
  marketplace_coverage: [["annual_slcsp", "annual_aptc"]],
};

/** facts.CODED: codes that are required and never defaulted. */
export const CODED: Record<string, string> = {
  retirement: "distribution_code",
  hsa_distributions: "distribution_code",
};

const CODE_SETS: Record<string, [string, number]> = {
  retirement: ["123456789ABCDEFGHJKLMNPQRSTUWY", 2],
  hsa_distributions: ["123456", 1],
};

/** returns/store.py TAX_FORMS: the filed documents that feed an individual return. */
export const TAX_FORMS: readonly string[] = [
  "W-2",
  "1099-NEC",
  "1099-MISC",
  "1099-K",
  "1099-INT",
  "1099-DIV",
  "1099-B",
  "1099-R",
  "1098",
  "1095",
  "K-1",
  "SSA-1099",
  "1099-G",
  "1098-E",
  "1098-T",
  "1099-SA",
  "5498",
  "5498-SA",
  "1095-A",
  "Prior-year return",
];

export const PRIOR_YEAR_RETURN = "Prior-year return";

export type NeverZeroReason = { kind: "required" } | { kind: "implied"; by: string } | { kind: "code" };

/** Why a blank here is "missing", never 0: the field's rule in the engine, or null when a blank is a plain default. */
export function neverZero(list: string | null, field: string): NeverZeroReason | null {
  if (!list) return null;
  if (REQUIRED[list] === field) return { kind: "required" };
  const implied = (IMPLIED[list] ?? []).find(([f]) => f === field);
  if (implied) return { kind: "implied", by: implied[1] };
  if (CODED[list] === field) return { kind: "code" };
  return null;
}

export function isEmptyValue(v: unknown): boolean {
  return v === null || v === undefined || (typeof v === "string" && v.trim() === "");
}

export function isPositive(v: unknown): boolean {
  if (typeof v === "number") return v > 0;
  if (typeof v !== "string") return false;
  const n = Number(v.replace(/,/g, ""));
  return Number.isFinite(n) && n > 0;
}

export function validCode(v: unknown, list: string): boolean {
  const rule = CODE_SETS[list];
  if (!rule) return !isEmptyValue(v);
  const code = (typeof v === "string" ? v : typeof v === "number" ? String(v) : "").trim();
  const [chars, width] = rule;
  if (code.length < 1 || code.length > width) return false;
  for (const ch of code) if (!chars.includes(ch)) return false;
  return true;
}

export interface MissingAmount {
  path: string;
  anchor: string;
  list: string;
  field: string;
  why: string;
}

/** facts.missing_required over the inputs as they are on screen (without the unreadable boxes only populate knows). */
export function missingAmounts(inputs: Record<string, unknown>): MissingAmount[] {
  const out: MissingAmount[] = [];
  const add = (list: string, index: number, item: unknown, field: string, why: string) => {
    const anchor = `missing:${list}[${itemIdentity(item) ?? `#${index}`}].${field}`;
    if (out.some((m) => m.anchor === anchor)) return;
    out.push({ path: `${list}[${index}].${field}`, anchor, list, field, why });
  };
  for (const [list, items] of Object.entries(inputs)) {
    if (!Array.isArray(items)) continue;
    items.forEach((item: unknown, index) => {
      if (!isRecord(item)) return;
      const required = REQUIRED[list];
      if (required && isEmptyValue(item[required])) add(list, index, item, required, "required");
      for (const [field, by] of IMPLIED[list] ?? []) {
        if (!isPositive(item[field]) && isPositive(item[by])) add(list, index, item, field, `implied by ${by}`);
      }
      const code = CODED[list];
      if (code && !validCode(item[code], list)) add(list, index, item, code, "a valid code is required");
    });
  }
  return out;
}

/** Whether the value at `path` answers the never-zero rule (facts.satisfied). */
export function satisfied(inputs: Record<string, unknown>, list: string, field: string, path: string): boolean {
  const value = getPath(inputs, path);
  if (isEmptyValue(value)) return false;
  if (CODED[list] === field) return validCode(value, list);
  if ((IMPLIED[list] ?? []).some(([f]) => f === field)) return isPositive(value);
  return true;
}
