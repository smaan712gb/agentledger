import { isApiError, type ClientDetail, type ClientFacts } from "@agentledger/contracts";
import { useNavigate } from "@tanstack/react-router";
import { useState, type SyntheticEvent } from "react";

import { api } from "../api";
import { useMe } from "../auth/AuthProvider";
import { can } from "../auth/can";
import { Button } from "../ui/Button";
import { Card, PageHeader } from "../ui/Card";
import { FormError, InputField, SelectField } from "../ui/Field";
import { QueryBoundary } from "../ui/states";
import { useToast } from "../ui/Toast";
import { useClientDetail, useInvalidateClient } from "./clientDetail";
import styles from "./screens.module.css";

export function ProfileScreen() {
  const { query } = useClientDetail();
  return <QueryBoundary query={query}>{(detail) => <Profile detail={detail} />}</QueryBoundary>;
}

/** Only the facts the person changed are sent; the API merges them into the recorded profile. */
export function changedFacts(before: ClientFacts, after: Record<string, string>): ClientFacts {
  const out: ClientFacts = {};
  for (const [key, raw] of Object.entries(after)) {
    const value = raw.trim();
    const previous = before[key];
    if (value === "" && (previous === undefined || previous === null || previous === "")) continue;
    if (value === "") continue; // the API merges; a blank cannot remove a recorded fact yet (docs/WEB.md)
    const typed: unknown =
      value === "true" ? true : value === "false" ? false : /^-?\d+(\.\d+)?$/.test(value) ? Number(value) : value;
    if (previous !== typed) out[key] = typed;
  }
  return out;
}

function Profile({ detail }: { detail: ClientDetail }) {
  const me = useMe();
  const { toast } = useToast();
  const navigate = useNavigate();
  const invalidate = useInvalidateClient();
  const facts = detail.client.facts;
  const allowed = can(me, "facts.update", { clientId: detail.client.id });
  const [basis, setBasis] = useState(typeof facts.accounting_basis === "string" ? facts.accounting_basis : "");
  const [state, setState] = useState(typeof facts.state === "string" ? facts.state : "");
  const [employees, setEmployees] = useState(facts.employees === undefined ? "" : String(facts.employees));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const others = Object.entries(facts).filter(([k]) => !["accounting_basis", "state", "employees"].includes(k));

  async function submit(e: SyntheticEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    const changes = changedFacts(facts, { accounting_basis: basis, state, employees });
    if (!Object.keys(changes).length) {
      toast("Nothing changed.");
      return;
    }
    setBusy(true);
    try {
      await api.clients.updateFacts(detail.client.id, changes);
      await invalidate(detail.client.id);
      toast("Profile saved. Opportunities and deadlines are recalculated.", "success");
      await navigate({ to: "/clients/$clientId", params: { clientId: detail.client.id }, search: (prev) => prev });
    } catch (err) {
      setError(isApiError(err) ? err.message : "The profile could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Profile facts" subtitle="Used by playbooks, deadlines and KPIs. Recorded, never assumed." />
      <Card className={styles.form}>
        <FormError message={error} />
        <form onSubmit={submit} aria-label="Profile facts">
          <SelectField
            label="Accounting basis"
            value={basis}
            onChange={(e) => setBasis(e.target.value)}
            hint="Shown in the context bar; the books are kept on this basis."
            data-testid="fact-accounting-basis"
          >
            <option value="">Not recorded</option>
            <option value="cash">Cash</option>
            <option value="accrual">Accrual</option>
          </SelectField>
          <div className={styles.formRow}>
            <InputField
              label="State"
              value={state}
              onChange={(e) => setState(e.target.value)}
              maxLength={2}
              hint="Two-letter code"
            />
            <InputField
              label="Employees"
              type="number"
              inputMode="numeric"
              min={0}
              value={employees}
              onChange={(e) => setEmployees(e.target.value)}
            />
          </div>
          {others.length ? (
            <dl className={styles.dl}>
              {others.map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt>{k.replaceAll("_", " ")}</dt>
                  <dd>{typeof v === "string" ? v : JSON.stringify(v)}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          <div className={styles.actions} style={{ marginTop: 16 }}>
            <Button
              type="submit"
              tone="primary"
              busy={busy}
              disabledReason={allowed.allowed ? undefined : allowed.reason}
            >
              Save
            </Button>
          </div>
        </form>
      </Card>
    </>
  );
}
