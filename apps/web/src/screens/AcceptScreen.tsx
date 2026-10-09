import { isApiError } from "@agentledger/contracts";
import { zodResolver } from "@hookform/resolvers/zod";
import { useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api } from "../api";
import { useAuth } from "../auth/AuthProvider";
import { pendingStep } from "../auth/pendingStep";
import styles from "../auth/auth.module.css";
import { Button } from "../ui/Button";
import { FormError, InputField } from "../ui/Field";
import { AuthFrame } from "./AuthFrame";

const schema = z.object({
  name: z.string().trim().min(1, "Enter your name"),
  password: z.string().min(12, "Use at least 12 characters"),
});

type FormValues = z.infer<typeof schema>;

/** Accepting an invitation: with a provider, continue there; locally, choose a name and a password, then enrol. */
export function AcceptScreen() {
  const auth = useAuth();
  const navigate = useNavigate();
  const { token } = useParams({ from: "/accept/$token" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { name: "", password: "" } });

  if (auth.config.identity === "workos") {
    const go = async () => {
      setBusy(true);
      setError(null);
      try {
        const { url } = await api.auth.idpStart("invite", token);
        location.assign(url);
      } catch (err) {
        setBusy(false);
        setError(isApiError(err) ? err.message : "The sign-in provider is not reachable.");
      }
    };
    return (
      <AuthFrame title="Join AgentLedger">
        <p className="muted">
          Continue with the email address this invitation was sent to. Two-step verification or a passkey is required.
        </p>
        <FormError message={error} />
        <Button tone="primary" onClick={go} busy={busy} style={{ width: "100%" }}>
          Continue
        </Button>
      </AuthFrame>
    );
  }

  const submit = form.handleSubmit(async (values) => {
    setError(null);
    try {
      const step = await api.auth.accept({ token, name: values.name, password: values.password });
      pendingStep.set({ step, next: null, email: null });
      await navigate({ to: "/sign-in/verify" });
    } catch (err) {
      setError(isApiError(err) ? err.message : "The invitation could not be accepted.");
    }
  });

  return (
    <AuthFrame title="Join AgentLedger">
      <p className="muted">Choose your name and a password of at least 12 characters.</p>
      <FormError message={error} />
      <form onSubmit={submit} noValidate className={styles.form} aria-label="Accept invitation">
        <InputField
          label="Full name"
          autoComplete="name"
          required
          {...form.register("name")}
          error={form.formState.errors.name?.message}
        />
        <InputField
          label="Password"
          type="password"
          autoComplete="new-password"
          minLength={12}
          required
          {...form.register("password")}
          error={form.formState.errors.password?.message}
        />
        <div className={styles.actions}>
          <Button type="submit" tone="primary" busy={form.formState.isSubmitting}>
            Continue
          </Button>
        </div>
      </form>
    </AuthFrame>
  );
}
