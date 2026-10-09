/**
 * The hand-off between /sign-in (password accepted, or invitation accepted) and /sign-in/verify (one-time code).
 * The challenge and an enrolment's secret are sensitive and short-lived (5 and 15 minutes at the API), so they
 * stay in memory: a reload lands on /sign-in again, which is the API's behaviour too.
 */

import type { LoginStep } from "@agentledger/contracts";

export interface PendingStep {
  step: LoginStep;
  /** Where to go once signed in. */
  next: string | null;
  /** Shown on the verify screen: the account being verified. */
  email: string | null;
}

let pending: PendingStep | null = null;

export const pendingStep = {
  set(value: PendingStep): void {
    pending = value;
  },
  peek(): PendingStep | null {
    return pending;
  },
  take(): PendingStep | null {
    const v = pending;
    pending = null;
    return v;
  },
  clear(): void {
    pending = null;
  },
};

/** One-line notices for the sign-in page ("Your session ended", a provider error); read once. */
let flash: string | null = null;

export const signInFlash = {
  set(message: string): void {
    flash = message;
  },
  take(): string | null {
    const m = flash;
    flash = null;
    return m;
  },
};
