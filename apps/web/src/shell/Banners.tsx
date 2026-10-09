import type { PendingAction } from "../auth/session";
import { Button } from "../ui/Button";
import { OfflineBanner, useOnline } from "../ui/states";
import styles from "./AppShell.module.css";
import { useBuildCheck } from "./useBuildCheck";

export function Offline() {
  const online = useOnline();
  return online ? null : <OfflineBanner />;
}

export function NewVersion() {
  const { newVersion } = useBuildCheck();
  if (!newVersion) return null;
  return (
    <div className={styles.banner} role="status" aria-label="A new version is available" data-state="new-version">
      <span>A new version of AgentLedger is available.</span>
      <Button size="sm" onClick={() => location.reload()}>
        Reload
      </Button>
    </div>
  );
}

/** After a provider step-up the action that needed it has to be repeated; say so, with a way back to it. */
export function PendingActionNotice({ action, onDismiss }: { action: PendingAction; onDismiss: () => void }) {
  return (
    <div className={styles.banner} role="status" aria-label="Verified: retry the action" data-state="retry">
      <span>Your sign-in is verified. Retry {action.label}.</span>
      <span className={styles.bannerActions}>
        <a href={action.href} className={styles.bannerLink}>
          Retry
        </a>
        <Button size="sm" tone="ghost" onClick={onDismiss} aria-label="Dismiss">
          ×
        </Button>
      </span>
    </div>
  );
}
