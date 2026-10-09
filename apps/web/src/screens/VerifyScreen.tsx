import { isApiError } from "@agentledger/contracts";
import { Link, Navigate, useRouter } from "@tanstack/react-router";
import QRCode from "qrcode";
import { useEffect, useRef, useState, type SyntheticEvent } from "react";

import { api } from "../api";
import { useAuth } from "../auth/AuthProvider";
import { homeFor } from "../auth/can";
import { pendingStep } from "../auth/pendingStep";
import { returnTo } from "../auth/session";
import styles from "../auth/auth.module.css";
import { safeNext } from "../lib/safeNext";
import { Button } from "../ui/Button";
import { FormError, InputField } from "../ui/Field";
import { AuthFrame } from "./AuthFrame";

/** The manual key in groups of four, as authenticator apps show it. */
export function groupSecret(secret: string): string {
  return secret.replace(/(.{4})/g, "$1 ").trim();
}

/** Step two of every sign-in: the one-time code. A first sign-in (or an accepted invitation) enrols the authenticator here. */
export function VerifyScreen() {
  const auth = useAuth();
  const router = useRouter();
  const [pending] = useState(() => pendingStep.peek());
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [qr, setQr] = useState<string | null>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const enrol = pending?.step.next === "enroll" ? pending.step : null;

  // One task on this page: the code. Focus moves there once (not an autofocus attribute, which fires on every load).
  useEffect(() => {
    codeRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!enrol) return;
    let cancelled = false;
    // An SVG data URL needs no canvas, so it renders everywhere (and the CSP allows img-src data:).
    QRCode.toString(enrol.otpauth_uri, { type: "svg", margin: 1, errorCorrectionLevel: "M" })
      .then((svg) => {
        if (!cancelled) setQr(`data:image/svg+xml;utf8,${encodeURIComponent(svg)}`);
      })
      .catch(() => {
        /* the manual key is always shown */
      });
    return () => {
      cancelled = true;
    };
  }, [enrol]);

  if (!pending) return <Navigate to="/sign-in" search={{}} />;

  async function submit(e: SyntheticEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.auth.mfa({ challenge: pending.step.challenge, code: code.trim() });
      const me = await auth.signIn(result.token);
      pendingStep.clear();
      const next = safeNext(pending.next) ?? safeNext(returnTo.take());
      if (next) await router.navigate({ href: next });
      else await router.navigate({ to: homeFor(me) });
    } catch (err) {
      setBusy(false);
      const message = isApiError(err) ? err.message : "Verification failed.";
      setError(message);
      if (message.includes("expired")) setExpired(true);
    }
  }

  return (
    <AuthFrame title={enrol ? "Set up two-step verification" : "Two-step verification"}>
      {pending.email ? <p className="muted small">Signing in as {pending.email}</p> : null}
      {enrol ? (
        <>
          <p>Add AgentLedger to your authenticator app, then enter the 6-digit code it shows.</p>
          <div className={styles.qr}>
            {qr ? <img src={qr} alt="QR code for your authenticator app" /> : null}
            <a href={enrol.otpauth_uri}>Open in authenticator app</a>
          </div>
          <p className="small muted">Or enter this key manually:</p>
          <p>
            <code className={styles.secret} data-testid="totp-secret" aria-label="Manual key">
              {groupSecret(enrol.secret)}
            </code>
          </p>
        </>
      ) : (
        <p>Enter the 6-digit code from your authenticator app.</p>
      )}
      <FormError message={error} />
      <form onSubmit={submit} className={styles.form} aria-label="One-time code">
        <InputField
          label="6-digit code"
          name="code"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="\d{6}"
          maxLength={6}
          required
          ref={codeRef}
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className={styles.code}
        />
        <div className={styles.actions}>
          {expired ? (
            <Link to="/sign-in" search={{}}>
              Start again
            </Link>
          ) : null}
          <Button type="submit" tone="primary" busy={busy}>
            Verify
          </Button>
        </div>
      </form>
    </AuthFrame>
  );
}
