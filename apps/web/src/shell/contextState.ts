/**
 * The context bar's state lives in the URL (typed search params) so a link carries firm, entity, engagement and
 * period with it. These helpers are pure so they can be tested without a router.
 */

import type { ClientFacts } from "@agentledger/contracts";
import { z } from "zod";

import { formatDate } from "../lib/format";

const optionalInt = (min: number, max: number) =>
  z
    .union([z.number(), z.string()])
    .transform((v) => (typeof v === "string" ? Number(v) : v))
    .pipe(z.number().int().min(min).max(max))
    .optional()
    .catch(undefined);

/** `?year=YYYY&engagement=<id>`: anything malformed is dropped rather than failing the route. */
export const clientSearchSchema = z.object({
  year: optionalInt(2000, 2100),
  engagement: optionalInt(1, Number.MAX_SAFE_INTEGER),
});

export type ClientSearch = z.infer<typeof clientSearchSchema>;

export function parseClientSearch(input: unknown): ClientSearch {
  const parsed = clientSearchSchema.safeParse(input ?? {});
  return parsed.success ? parsed.data : {};
}

export function currentYear(now: Date = new Date()): number {
  return now.getFullYear();
}

/** The years offered in the period selector: a few back, one ahead, the selected one always included. */
export function periodChoices(selected: number, now: Date = new Date()): number[] {
  const current = currentYear(now);
  const years = new Set<number>();
  for (let y = current + 1; y >= current - 6; y--) years.add(y);
  years.add(selected);
  return [...years].sort((a, b) => b - a);
}

export function periodLabel(year: number, closedThrough: string | null | undefined): string {
  return closedThrough ? `FY${year} · closed through ${formatDate(closedThrough)}` : `FY${year}`;
}

export interface FrozenState {
  /** The whole period is on or before the closing date: nothing in it can change. */
  frozen: boolean;
  /** The closing date falls inside the period: entries up to it are frozen, later ones are not. */
  partial: boolean;
  reason: string | null;
}

export function frozenState(year: number, closedThrough: string | null | undefined): FrozenState {
  if (!closedThrough) return { frozen: false, partial: false, reason: null };
  const closedYear = Number(closedThrough.slice(0, 4));
  const closedDate = formatDate(closedThrough);
  if (year < closedYear || (year === closedYear && closedThrough.endsWith("-12-31"))) {
    return {
      frozen: true,
      partial: false,
      reason: `The books are closed through ${closedDate}: FY${year} cannot be changed.`,
    };
  }
  if (year === closedYear) {
    return {
      frozen: false,
      partial: true,
      reason: `The books are closed through ${closedDate}: entries dated on or before it are frozen.`,
    };
  }
  return { frozen: false, partial: false, reason: null };
}

export type BasisState = { recorded: true; basis: string } | { recorded: false };

/** The accounting basis comes from the recorded profile fact, never from a default. */
export function basisOf(facts: ClientFacts | null | undefined): BasisState {
  const raw = facts?.accounting_basis;
  if (typeof raw === "string" && raw.trim()) return { recorded: true, basis: raw.trim() };
  return { recorded: false };
}
