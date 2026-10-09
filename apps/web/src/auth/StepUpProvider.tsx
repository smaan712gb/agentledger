import { isApiError } from "@agentledger/contracts";
import { useEffect, useState, type ReactNode, type SyntheticEvent } from "react";

import { api } from "../api";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { InputField } from "../ui/Field";
import { useAuth } from "./AuthProvider";
import { stepUpBridge, type StepUpRequest } from "./bridges";
import { pendingAction } from "./session";
import styles from "./auth.module.css";

interface Pending {
  request: StepUpRequest;
  resolve: (verified: boolean) => void;
}

/** A readable name for the action that needed a step-up, for the "retry" notice after a provider round trip. */
export function describeAction(request: StepUpRequest): string {
  const p = request.path;
  if (p.startsWith("/api/platform/firms")) return "creating the firm";
  if (p.startsWith("/api/auth/invite")) return "the invitation";
  if (p.includes("/grants")) return "the engagement change";
  if (p.includes("/reviewer")) return "the reviewer change";
  if (p.includes("/periods/")) return "closing or reopening the period";
  if (p.includes("/assign")) return "moving the document";
  return `${request.method} ${p}`;
}

/**
 * Answers the fetch layer's step-up requests. Local accounts enter a one-time code here (POST /api/auth/step-up) and
 * the original request is retried once. Accounts that sign in through the identity provider re-authenticate there:
 * the action is remembered, the browser leaves for the provider, and on return (#step_up=ok) the person is asked
 * to repeat it.
 */
export function StepUpProvider({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const [pending, setPending] = useState<Pending | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const identity = auth.config.identity;
  const providerAccount = identity === "workos" && auth.me?.auth_method.startsWith("workos:") === true;

  useEffect(
    () =>
      stepUpBridge.setHandler(async (request) => {
        if (providerAccount) {
          pendingAction.set({ label: describeAction(request), href: location.pathname + location.search });
          try {
            const { url } = await api.auth.idpStart("step_up");
            location.assign(url);
          } catch {
            pendingAction.take();
          }
          return false;
        }
        return new Promise<boolean>((resolve) => {
          setCode("");
          setError(null);
          setPending({ request, resolve });
        });
      }),
    [providerAccount],
  );

  function finish(verified: boolean) {
    pending?.resolve(verified);
    setPending(null);
    setBusy(false);
  }

  async function submit(e: SyntheticEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      await api.auth.stepUp(code.trim());
      void auth.refresh();
      finish(true);
    } catch (err) {
      setBusy(false);
      setError(isApiError(err) ? err.message : "verification failed");
    }
  }

  return (
    <>
      {children}
      <Dialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) finish(false);
        }}
        title="Confirm it's you"
        description={`This action (${pending ? describeAction(pending.request) : ""}) needs a recent sign-in. Enter the 6-digit code from your authenticator app.`}
      >
        <form onSubmit={submit} className={styles.form} data-testid="step-up-form">
          <InputField
            label="One-time code"
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="\d{6}"
            maxLength={6}
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            error={error ?? undefined}
            className={styles.code}
          />
          <div className={styles.actions}>
            <Button type="button" onClick={() => finish(false)}>
              Cancel
            </Button>
            <Button type="submit" tone="primary" busy={busy}>
              Verify
            </Button>
          </div>
        </form>
      </Dialog>
    </>
  );
}
