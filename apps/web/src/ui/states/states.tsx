/**
 * The required states (spec §3, ADR-0005), each one a component with an accessible name. Screens never improvise
 * these; QueryBoundary picks them from a query's status and the API error, and the states contract test
 * (src/test/states.contract.test.tsx) renders every route against every outcome.
 */

import type { ReactNode } from "react";
import { useEffect, useState } from "react";

import { Button } from "../Button";
import { StatePanel } from "./StatePanel";
import styles from "./StatePanel.module.css";
import { formatTime } from "../../lib/format";

export const STATE_NAMES = {
  loading: "Loading",
  waking: "Waking the service",
  empty: "Nothing here yet",
  error: "Something went wrong",
  forbidden: "You do not have access",
  notFound: "Not found",
  stale: "Showing earlier data",
  offline: "You are offline",
  frozen: "Period closed",
  readOnly: "Read only",
  partial: "Partly done",
  gone: "No longer stored",
  conflict: "This changed underneath you",
  sessionEnded: "Your session ended",
} as const;

// -------------------------------------------------------------------------------------------------- loading

const SHOW_AFTER_MS = 300;
const WAKING_AFTER_MS = 5000;

/**
 * Nothing for 300 ms (a fast answer never flashes a skeleton), then a skeleton marked busy, and after five seconds
 * the honest message: a cold API container can take up to two minutes to start.
 */
export function Loading({ label = "Loading", lines = 3 }: { label?: string; lines?: number }) {
  const [phase, setPhase] = useState<"hidden" | "skeleton" | "waking">("hidden");
  useEffect(() => {
    const a = setTimeout(() => setPhase("skeleton"), SHOW_AFTER_MS);
    const b = setTimeout(() => setPhase("waking"), WAKING_AFTER_MS);
    return () => {
      clearTimeout(a);
      clearTimeout(b);
    };
  }, []);
  if (phase === "hidden") return <div aria-busy="true" aria-label={label} role="status" data-state="loading" />;
  return (
    <section
      role="status"
      aria-busy="true"
      aria-label={phase === "waking" ? STATE_NAMES.waking : label}
      data-state="loading"
      className={styles.panel}
    >
      <div className={styles.skeleton} aria-hidden="true">
        {Array.from({ length: lines }, (_, i) => (
          <div key={i} className={styles.bone} style={{ width: `${90 - i * 15}%` }} />
        ))}
      </div>
      {phase === "waking" ? (
        <p className={styles.meta}>
          Waking the service… a service that has been idle can take up to two minutes to answer.
        </p>
      ) : (
        <span className="visually-hidden">{label}…</span>
      )}
    </section>
  );
}

// -------------------------------------------------------------------------------------------------- empty

export function Empty({
  title = STATE_NAMES.empty,
  children,
  action,
}: {
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <StatePanel title={title} state="empty" actions={action}>
      {children}
    </StatePanel>
  );
}

// -------------------------------------------------------------------------------------------------- failures

export interface ErrorStateProps {
  /** The API's `detail`, shown as is. */
  detail?: string | undefined;
  requestId?: string | null | undefined;
  onRetry?: (() => void) | undefined;
  title?: string;
}

export function ErrorState({ detail, requestId, onRetry, title = STATE_NAMES.error }: ErrorStateProps) {
  return (
    <StatePanel
      title={title}
      tone="bad"
      role="alert"
      state="error"
      actions={onRetry ? <Button onClick={onRetry}>Try again</Button> : undefined}
    >
      {detail ? <p>{detail}</p> : <p>The request could not be completed.</p>}
      {requestId ? (
        <p className={styles.meta}>
          Request id <code>{requestId}</code> — quote it when asking for help.
        </p>
      ) : null}
    </StatePanel>
  );
}

/** The API's own 403 text, verbatim: it is the explanation. */
export function Forbidden({
  detail,
  requestId,
}: {
  detail?: string | undefined;
  requestId?: string | null | undefined;
}) {
  return (
    <StatePanel title={STATE_NAMES.forbidden} tone="warn" role="alert" state="forbidden">
      <p>{detail ?? "This needs a role your account does not have."}</p>
      {requestId ? (
        <p className={styles.meta}>
          Request id <code>{requestId}</code>
        </p>
      ) : null}
    </StatePanel>
  );
}

export function NotFound({ detail, action }: { detail?: string | undefined; action?: ReactNode }) {
  return (
    <StatePanel title={STATE_NAMES.notFound} state="not-found" actions={action}>
      <p>{detail ?? "There is nothing at this address, or it was removed."}</p>
    </StatePanel>
  );
}

/** 409: the API refused because the record moved on (a workflow transition, a used idempotency key). */
export function Conflict({ detail, onRefresh }: { detail?: string | undefined; onRefresh?: (() => void) | undefined }) {
  return (
    <StatePanel
      title={STATE_NAMES.conflict}
      tone="warn"
      role="alert"
      state="conflict"
      actions={onRefresh ? <Button onClick={onRefresh}>Refresh</Button> : undefined}
    >
      <p>{detail ?? "The record changed since it was loaded."}</p>
    </StatePanel>
  );
}

/** 410: deleted under the retention policy; the API's detail carries the receipt. */
export function Gone({ detail }: { detail?: string | undefined }) {
  return (
    <StatePanel title={STATE_NAMES.gone} tone="info" state="gone">
      <p>{detail ?? "This document was deleted under the retention policy; the deletion receipt remains."}</p>
    </StatePanel>
  );
}

/** Shown next to data that is still on screen while a refetch fails or the browser is offline. */
export function Stale({ asOf, retrying = true }: { asOf: number | Date; retrying?: boolean }) {
  const at = asOf instanceof Date ? asOf : new Date(asOf);
  return (
    <div className={styles.inline} role="status" aria-label={STATE_NAMES.stale} data-state="stale">
      <span aria-hidden="true">●</span>
      <span>
        as of {formatTime(at)}
        {retrying ? ", retrying" : ""}
      </span>
    </div>
  );
}

export function OfflineBanner() {
  return (
    <div
      className={[styles.banner, styles.warn].join(" ")}
      role="status"
      aria-label={STATE_NAMES.offline}
      data-state="offline"
    >
      <span>
        You are offline. What you see may be out of date; changes will not be saved until you are back online.
      </span>
    </div>
  );
}

/** A period (or a record) that cannot be changed; the reason is always shown. */
export function Frozen({ reason, compact = true }: { reason: string; compact?: boolean }) {
  return (
    <StatePanel title={STATE_NAMES.frozen} tone="info" state="frozen" compact={compact}>
      <p>{reason}</p>
    </StatePanel>
  );
}

export function ReadOnly({ reason, compact = true }: { reason: string; compact?: boolean }) {
  return (
    <StatePanel title={STATE_NAMES.readOnly} tone="info" state="read-only" compact={compact}>
      <p>{reason}</p>
    </StatePanel>
  );
}

export interface PartialItem {
  key: string;
  label: string;
  outcome: "ok" | "warn" | "failed";
  detail: string;
}

/** Several things were attempted (a multi-file upload); each one's result is listed. */
export function PartialSuccess({ items, onDismiss }: { items: PartialItem[]; onDismiss?: (() => void) | undefined }) {
  const failed = items.filter((i) => i.outcome === "failed").length;
  const warned = items.filter((i) => i.outcome === "warn").length;
  const ok = items.length - failed - warned;
  const allOk = failed === 0 && warned === 0;
  return (
    <StatePanel
      title={allOk ? "Done" : STATE_NAMES.partial}
      tone={failed ? "warn" : "neutral"}
      role={failed ? "alert" : "status"}
      state={allOk ? "success" : "partial-success"}
      actions={onDismiss ? <Button onClick={onDismiss}>Dismiss</Button> : undefined}
    >
      <p>
        {ok} of {items.length} completed{warned ? `, ${warned} need attention` : ""}
        {failed ? `, ${failed} failed` : ""}.
      </p>
      <ul className={styles.list}>
        {items.map((i) => (
          <li key={i.key} className={styles.item}>
            <span aria-hidden="true">{i.outcome === "ok" ? "✓" : i.outcome === "warn" ? "!" : "✗"}</span>
            <span>
              <strong>{i.label}</strong>
              <span className="visually-hidden">
                {i.outcome === "ok" ? " completed" : i.outcome === "warn" ? " needs attention" : " failed"}:
              </span>{" "}
              <span className={styles.meta}>{i.detail}</span>
            </span>
          </li>
        ))}
      </ul>
    </StatePanel>
  );
}
