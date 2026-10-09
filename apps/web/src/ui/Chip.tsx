import type { ReactNode } from "react";

import styles from "./Chip.module.css";

export type ChipTone = "neutral" | "good" | "warn" | "bad" | "info" | "accent";

export function Chip({
  tone = "neutral",
  children,
  title,
}: {
  tone?: ChipTone;
  children: ReactNode;
  title?: string | undefined;
}) {
  return (
    <span className={[styles.chip, styles[tone]].join(" ")} title={title}>
      {children}
    </span>
  );
}

const STATUS_TONES: Record<string, ChipTone> = {
  filed: "good",
  needs_review: "warn",
  open: "warn",
  resolved: "good",
  active: "good",
  provisioning: "warn",
  deleting: "bad",
  explained: "info",
  corrected: "good",
  accepted_risk: "warn",
};

export function StatusChip({ status }: { status: string | null | undefined }) {
  const s = status ?? "unknown";
  return <Chip tone={STATUS_TONES[s] ?? "neutral"}>{s.replaceAll("_", " ")}</Chip>;
}
