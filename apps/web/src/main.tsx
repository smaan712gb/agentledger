import "./styles/tokens.css";
import "./styles/base.css";

import type { AuthConfig, Me } from "@agentledger/contracts";
import { isApiError } from "@agentledger/contracts";
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { api } from "./api";
import { App } from "./app/App";
import { bootNotices } from "./app/bootState";
import { createQueryClient } from "./app/queryClient";
import { createAppRouter, type AppRouter } from "./app/router";
import { createAuthStore, type AuthStore } from "./auth/AuthProvider";
import { signInFlash } from "./auth/pendingStep";
import { consumeFragment, session } from "./auth/session";
import { Button } from "./ui/Button";
import { ErrorState, Loading } from "./ui/states";

/**
 * Boot: read the URL fragment the sign-in provider may have left (and strip it), then ask the API how sign-in works
 * here and who the stored session belongs to. Only then does the router mount, so every guard sees a settled
 * answer. A cold API container can take a while: the loading state says so, and failures offer a retry.
 */
function readFragment(): void {
  const fragment = consumeFragment();
  if (!fragment) return;
  switch (fragment.kind) {
    case "session":
      session.set(fragment.token);
      break;
    case "legacy_accept":
      history.replaceState(null, "", `/accept/${fragment.token}`);
      break;
    case "signin_error":
      signInFlash.set(fragment.message);
      break;
    case "step_up":
      // The interrupted action stays in sessionStorage; the shell shows it as "retry" once the person is back in.
      bootNotices.set({ toast: "Verified. You can retry the action now." });
      break;
    case "link":
      bootNotices.set({ toast: "Your sign-in provider is now linked to this account." });
      break;
  }
}

async function loadAuth(): Promise<AuthStore> {
  const config: AuthConfig = await api.auth.config();
  let me: Me | null = null;
  if (session.get()) {
    try {
      me = await api.me();
    } catch (err) {
      if (isApiError(err) && err.status === 401) {
        session.clear();
        signInFlash.set("Your session ended. Sign in again.");
      } else {
        throw err;
      }
    }
  }
  return createAuthStore({ me, config });
}

type BootResult =
  | { attempt: number; phase: "failed"; error: unknown }
  | { attempt: number; phase: "ready"; store: AuthStore; router: AppRouter };

function Boot() {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<BootResult | null>(null);
  // Loading whenever the latest attempt has no result yet; a retry bumps `attempt` and the effect runs again.
  const state = result?.attempt === attempt ? result : ({ phase: "loading" } as const);

  useEffect(() => {
    let cancelled = false;
    loadAuth()
      .then((store) => {
        if (cancelled) return;
        const router = createAppRouter({ queryClient: createQueryClient(), auth: store });
        setResult({ attempt, phase: "ready", store, router });
      })
      .catch((error: unknown) => {
        if (!cancelled) setResult({ attempt, phase: "failed", error });
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  if (state.phase === "loading") {
    return (
      <div style={{ maxWidth: 480, margin: "15vh auto", padding: 16 }}>
        <Loading label="Starting AgentLedger" />
      </div>
    );
  }
  if (state.phase === "failed") {
    const detail = isApiError(state.error) ? state.error.message : "The service did not answer.";
    return (
      <div style={{ maxWidth: 480, margin: "15vh auto", padding: 16 }}>
        <ErrorState title="Could not reach the service" detail={detail} onRetry={() => setAttempt((n) => n + 1)} />
        <p className="small muted">
          If this keeps happening, the previous interface is at <a href="/legacy">/legacy</a>.
        </p>
        <Button tone="ghost" onClick={() => setAttempt((n) => n + 1)}>
          Retry now
        </Button>
      </div>
    );
  }
  return <App store={state.store} router={state.router} />;
}

readFragment();
const container = document.getElementById("root");
if (!container) throw new Error("#root is missing from index.html");
createRoot(container).render(
  <StrictMode>
    <Boot />
  </StrictMode>,
);
