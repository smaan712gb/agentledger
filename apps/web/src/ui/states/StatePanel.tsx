import type { ReactNode } from "react";

import styles from "./StatePanel.module.css";

export type StateTone = "neutral" | "warn" | "bad" | "info";

export interface StatePanelProps {
  /** The state's accessible name: the heading, and what tests and assistive technology look for. */
  title: string;
  tone?: StateTone;
  children?: ReactNode;
  actions?: ReactNode;
  /** "status" for calm states, "alert" for failures that interrupt the task. */
  role?: "status" | "alert" | "region";
  /** The machine-readable state name, for tests and support ("data-state"). */
  state: string;
  compact?: boolean;
}

/** The shared frame of every required state: a named region with one clear next step. */
export function StatePanel({
  title,
  tone = "neutral",
  children,
  actions,
  role = "status",
  state,
  compact = false,
}: StatePanelProps) {
  return (
    <section
      className={[styles.panel, styles[tone], compact ? styles.compact : ""].join(" ")}
      role={role}
      aria-label={title}
      data-state={state}
    >
      <h2 className={styles.title}>{title}</h2>
      {children ? <div className={styles.body}>{children}</div> : null}
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </section>
  );
}
