import { isApiError } from "@agentledger/contracts";
import { zodResolver } from "@hookform/resolvers/zod";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api } from "../api";
import { useAuth } from "../auth/AuthProvider";
import { pendingStep, signInFlash } from "../auth/pendingStep";
import styles from "../auth/auth.module.css";
import { Button } from "../ui/Button";
import { FormError, InputField } from "../ui/Field";
import { AuthFrame } from "./AuthFrame";

const schema = z.object({
  email: z.email("Enter the email address of your account"),
  password: z.string().min(1, "Enter your password"),
});

type FormValues = z.infer<typeof schema>;

export function SignInScreen() {
  const auth = useAuth();
  const navigate = useNavigate();
  const search = useSearch({ from: "/sign-in/" });
  const [flash] = useState(() => signInFlash.take());
  const [error, setError] = useState<string | null>(null);
  const [providerBusy, setProviderBusy] = useState(false);
  const idp = auth.config.identity === "workos";

  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { email: "", password: "" } });

  const submit = form.handleSubmit(async (values) => {
    setError(null);
    try {
      const step = await api.auth.login(values);
      pendingStep.set({ step, next: search.next ?? null, email: values.email });
      await navigate({ to: "/sign-in/verify" });
    } catch (err) {
      setError(isApiError(err) ? err.message : "Sign-in failed. Try again.");
    }
  });

  async function provider() {
    setError(null);
    setProviderBusy(true);
    try {
      const { url } = await api.auth.idpStart("login");
      location.assign(url);
    } catch (err) {
      setProviderBusy(false);
      setError(isApiError(err) ? err.message : "The sign-in provider is not reachable.");
    }
  }

  const passwordForm = (
    <form onSubmit={submit} noValidate className={styles.form}>
      <InputField
        label="Email"
        type="email"
        autoComplete="username"
        required
        {...form.register("email")}
        error={form.formState.errors.email?.message}
      />
      <InputField
        label="Password"
        type="password"
        autoComplete="current-password"
        required
        {...form.register("password")}
        error={form.formState.errors.password?.message}
      />
      <div className={styles.actions}>
        <Button type="submit" tone={idp ? "default" : "primary"} busy={form.formState.isSubmitting}>
          Continue
        </Button>
      </div>
    </form>
  );

  return (
    <AuthFrame title="Sign in">
      {flash ? (
        <p className={styles.notice} role="status">
          {flash}
        </p>
      ) : null}
      <FormError message={error} />
      {idp ? (
        <>
          <Button tone="primary" onClick={provider} busy={providerBusy} style={{ width: "100%" }}>
            Sign in
          </Button>
          <p className="small muted" style={{ marginTop: 8 }}>
            Two-step verification or a passkey is required.
          </p>
          <details className={styles.details}>
            <summary>Platform administrator sign-in</summary>
            {passwordForm}
          </details>
        </>
      ) : (
        <>
          {passwordForm}
          <p className="small muted" style={{ marginTop: 12 }}>
            Every account uses two-step verification.
          </p>
        </>
      )}
    </AuthFrame>
  );
}
