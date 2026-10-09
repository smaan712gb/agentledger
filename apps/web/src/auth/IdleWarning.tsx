import { useEffect, useState } from "react";

import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { useAuth } from "./AuthProvider";
import { activity } from "./bridges";
import styles from "./auth.module.css";

/** The API ends a session after 30 idle minutes; the warning comes at 25 (both in milliseconds here, for tests). */
export const IDLE_WARN_MS = 25 * 60_000;
export const IDLE_END_MS = 30 * 60_000;

export function IdleWarning({
  onSignOut,
  warnAfter = IDLE_WARN_MS,
  endAfter = IDLE_END_MS,
  tickMs = 15_000,
}: {
  onSignOut: () => void;
  warnAfter?: number;
  endAfter?: number;
  tickMs?: number;
}) {
  const auth = useAuth();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const signedIn = auth.me !== null;

  useEffect(() => {
    if (!signedIn) return;
    const tick = () => {
      const idle = Date.now() - activity.last();
      if (idle >= endAfter) {
        // The API has ended the session by now; asking confirms it and runs the sign-out path.
        void auth.refresh();
        setOpen(false);
      } else if (idle >= warnAfter) {
        setOpen(true);
      }
    };
    const timer = setInterval(tick, tickMs);
    const unsubscribe = activity.subscribe(() => setOpen(false));
    return () => {
      clearInterval(timer);
      unsubscribe();
    };
  }, [signedIn, auth, warnAfter, endAfter, tickMs]);

  async function stay() {
    setBusy(true);
    await auth.refresh();
    setBusy(false);
    setOpen(false);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title="Still there?"
      description="You have been inactive for a while. Your session ends after 30 minutes without activity."
    >
      <div className={styles.actions}>
        <Button onClick={onSignOut}>Sign out</Button>
        <Button tone="primary" busy={busy} onClick={stay}>
          Stay signed in
        </Button>
      </div>
    </Dialog>
  );
}
