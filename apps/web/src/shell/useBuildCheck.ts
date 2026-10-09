import { useEffect, useState } from "react";

import { api } from "../api";

export const BUILD_CHECK_INTERVAL_MS = 5 * 60_000;

/**
 * Compares the build this bundle was compiled from (__BUILD_SHA__) with the API's GET /healthz `build` every few
 * minutes and when the tab becomes visible again. A difference means a release landed: the person is offered a
 * reload rather than being reloaded. "dev" on either side never counts.
 */
export function useBuildCheck(intervalMs: number = BUILD_CHECK_INTERVAL_MS): {
  newVersion: boolean;
  serverBuild: string | null;
} {
  const [serverBuild, setServerBuild] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = () => {
      api
        .health()
        .then((h) => {
          if (!cancelled) setServerBuild(h.build);
        })
        .catch(() => {
          /* a failed liveness check is not news */
        });
    };
    const timer = setInterval(check, intervalMs);
    const onVisible = () => {
      if (document.visibilityState === "visible") check();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [intervalMs]);

  return { newVersion: isNewVersion(__BUILD_SHA__, serverBuild), serverBuild };
}

export function isNewVersion(ours: string, theirs: string | null): boolean {
  if (!theirs || ours === "dev" || theirs === "dev") return false;
  return ours !== theirs;
}
