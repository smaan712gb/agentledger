/**
 * Display formatting. Amounts arrive from the API as decimal strings and are handed to Intl as strings, so no float
 * arithmetic (or float parsing) happens in the app. Dates are ISO dates (YYYY-MM-DD) or SQLite UTC timestamps
 * (YYYY-MM-DD HH:MM:SS, no zone marker).
 */

const moneyFormatters = new Map<string, Intl.NumberFormat>();

function moneyFormatter(currency: string): Intl.NumberFormat {
  let f = moneyFormatters.get(currency);
  if (!f) {
    f = new Intl.NumberFormat(undefined, { style: "currency", currency });
    moneyFormatters.set(currency, f);
  }
  return f;
}

const DECIMAL = /^-?\d+(\.\d+)?$/;

/** "1234.50" -> "$1,234.50". Anything that is not a plain decimal string is shown as is (never guessed). */
export function formatMoney(value: string | number | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined || value === "") return "—";
  const text = typeof value === "number" ? String(value) : value.trim();
  if (!DECIMAL.test(text)) return text;
  // Intl.NumberFormat accepts decimal strings (ES2023) and formats them exactly.
  return moneyFormatter(currency).format(text as unknown as number);
}

const percentFormatter = new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 0 });

export function formatPercent(fraction: number | null | undefined): string {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return "—";
  return percentFormatter.format(fraction);
}

const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
const dateTimeFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const timeFormatter = new Intl.DateTimeFormat(undefined, { timeStyle: "short" });

/** Parses an ISO date as a calendar date (no time zone shift) or a timestamp (UTC when it carries no zone). */
export function parseApiDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (date) return new Date(Number(date[1]), Number(date[2]) - 1, Number(date[3]));
  const normalised = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value.replace(" ", "T")}Z`;
  const d = new Date(normalised);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDate(value: string | null | undefined): string {
  const d = parseApiDate(value);
  if (d) return dateFormatter.format(d);
  return value?.trim() ? value : "—";
}

export function formatDateTime(value: string | null | undefined): string {
  const d = parseApiDate(value);
  if (d) return dateTimeFormatter.format(d);
  return value?.trim() ? value : "—";
}

export function formatTime(date: Date): string {
  return timeFormatter.format(date);
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} kB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/** "needs_review" -> "needs review" */
export function humanise(value: string | null | undefined): string {
  return (value ?? "").replaceAll("_", " ");
}
