import { isApiError, type CreateClientBody } from "@agentledger/contracts";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api } from "../api";
import { queryKeys } from "../queries";
import { Button } from "../ui/Button";
import { Card, PageHeader } from "../ui/Card";
import { CheckboxField, FormError, InputField, SelectField } from "../ui/Field";
import { useToast } from "../ui/Toast";
import styles from "./screens.module.css";

/** The industry packs shipped in domains/ (the API has no list route yet: docs/WEB.md). */
export const DOMAINS = [
  "general",
  "auto_repair",
  "gas_station",
  "medical_practice",
  "insurance_agency",
  "pe_fund",
  "hedge_fund",
] as const;
export const ENTITY_TYPES = ["llc", "s_corp", "c_corp", "partnership", "sole_prop"] as const;

const schema = z.object({
  id: z
    .string()
    .trim()
    .regex(
      /^[a-z0-9][a-z0-9._-]{1,60}$/,
      "Lowercase letters, digits, dots, dashes; 2 to 61 characters (for example bright-dental)",
    ),
  name: z.string().trim().min(1, "Enter the legal name"),
  kind: z.enum(["business", "individual"]),
  entity_type: z.string().optional(),
  domain: z.string().min(1),
  email: z.union([z.literal(""), z.email("Enter a valid email address")]),
  accounting_basis: z.enum(["", "cash", "accrual"]),
  consent_7216: z.boolean(),
});

type FormValues = z.infer<typeof schema>;

export function toCreateBody(values: FormValues, now: Date = new Date()): CreateClientBody {
  return {
    id: values.id,
    name: values.name,
    kind: values.kind,
    entity_type: values.entity_type?.trim() ? values.entity_type : null,
    domain: values.domain,
    emails: values.email ? [values.email] : [],
    consent_7216_at: values.consent_7216 ? now.toISOString() : null,
    facts: values.accounting_basis ? { accounting_basis: values.accounting_basis } : {},
  };
}

export function NewClientScreen() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { toast } = useToast();
  const [error, setError] = useState<string | null>(null);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      id: "",
      name: "",
      kind: "business",
      entity_type: "",
      domain: "general",
      email: "",
      accounting_basis: "",
      consent_7216: false,
    },
  });

  const submit = form.handleSubmit(async (values) => {
    setError(null);
    try {
      const created = await api.clients.create(toCreateBody(values));
      await qc.invalidateQueries({ queryKey: queryKeys.clients });
      toast(`${values.name} created; ${created.accounts_created} accounts instantiated.`, "success");
      await navigate({ to: "/clients/$clientId", params: { clientId: created.id }, search: {} });
    } catch (err) {
      if (isApiError(err) && err.status === 422) {
        for (const [field, message] of Object.entries(err.fieldErrors())) {
          if (field in values) form.setError(field as keyof FormValues, { message });
        }
        setError("Check the highlighted fields.");
      } else {
        setError(isApiError(err) ? err.message : "The client could not be created.");
      }
    }
  });

  return (
    <>
      <PageHeader
        title="New client"
        subtitle="Creates the workspace, instantiates the chart of accounts and queues onboarding."
      />
      <Card className={styles.form}>
        <FormError message={error} />
        <form onSubmit={submit} noValidate aria-label="New client">
          <div className={styles.formRow}>
            <InputField
              label="Client id"
              hint="Used in links and files; cannot be changed later."
              required
              {...form.register("id")}
              error={form.formState.errors.id?.message}
            />
            <InputField
              label="Legal name"
              required
              {...form.register("name")}
              error={form.formState.errors.name?.message}
            />
          </div>
          <div className={styles.formRow}>
            <SelectField label="Kind" {...form.register("kind")} error={form.formState.errors.kind?.message}>
              <option value="business">Business</option>
              <option value="individual">Individual</option>
            </SelectField>
            <SelectField label="Entity type" {...form.register("entity_type")}>
              <option value="">Not recorded</option>
              {ENTITY_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t.replaceAll("_", " ")}
                </option>
              ))}
            </SelectField>
          </div>
          <div className={styles.formRow}>
            <SelectField label="Industry pack" {...form.register("domain")}>
              {DOMAINS.map((d) => (
                <option key={d} value={d}>
                  {d.replaceAll("_", " ")}
                </option>
              ))}
            </SelectField>
            <SelectField
              label="Accounting basis"
              hint="Recorded as a profile fact; never assumed."
              {...form.register("accounting_basis")}
            >
              <option value="">Not recorded yet</option>
              <option value="cash">Cash</option>
              <option value="accrual">Accrual</option>
            </SelectField>
          </div>
          <InputField
            label="Client email"
            type="email"
            hint="Mail from this address is routed to the client."
            {...form.register("email")}
            error={form.formState.errors.email?.message}
          />
          <CheckboxField label="IRC §7216 consent on file" {...form.register("consent_7216")} />
          <div className={styles.actions}>
            <Button type="submit" tone="primary" busy={form.formState.isSubmitting}>
              Create client
            </Button>
            <Link to="/clients" search={{}}>
              Cancel
            </Link>
          </div>
        </form>
      </Card>
    </>
  );
}
