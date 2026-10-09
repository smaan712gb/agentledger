import type { ReactNode } from "react";

import styles from "../auth/auth.module.css";

/** The frame of the sign-in, verification and invitation pages: no navigation, one card, one task. */
export function AuthFrame({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main id="main" className={styles.page}>
      <div className={styles.card}>
        <div className={styles.brand}>
          <div className={styles.mark} aria-hidden="true">
            V
          </div>
          <div>
            AgentLedger
            <small>Autonomous accounting &amp; tax</small>
          </div>
        </div>
        <h1 className={styles.title}>{title}</h1>
        {children}
      </div>
    </main>
  );
}
