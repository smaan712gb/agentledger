import { isApiError, type BaseRole, type FirmUser } from "@agentledger/contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";

import { api } from "../api";
import { useMe } from "../auth/AuthProvider";
import { can } from "../auth/can";
import { copyText } from "../lib/clipboard";
import { formatDateTime } from "../lib/format";
import { queries, queryKeys } from "../queries";
import { Button } from "../ui/Button";
import { Card, PageHeader } from "../ui/Card";
import { Chip } from "../ui/Chip";
import { Dialog } from "../ui/Dialog";
import { FormError, InputField, SelectField } from "../ui/Field";
import { Empty, QueryBoundary } from "../ui/states";
import { useToast } from "../ui/Toast";
import styles from "./screens.module.css";

const ROLE_LABELS: Record<Exclude<BaseRole, "platform_admin">, string> = {
  firm_admin: "Firm administrator",
  cpa: "CPA",
  staff: "Staff",
  client: "Client",
};

/** The invitation link the API's token belongs in (the accept route of this app). */
export function inviteLink(origin: string, token: string): string {
  return `${origin}/accept/${token}`;
}

export function TeamScreen() {
  const me = useMe();
  const qc = useQueryClient();
  const { toast } = useToast();
  const users = useQuery(queries.users());
  const clients = useQuery(queries.clients());
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Exclude<BaseRole, "platform_admin">>(me.base_role === "cpa" ? "client" : "cpa");
  const [clientId, setClientId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [link, setLink] = useState<{ email: string; url: string } | null>(null);
  const disableAllowed = can(me, "users.disable");

  const invite = useMutation({
    mutationFn: () => api.auth.invite({ email, role, ...(role === "client" ? { client_id: clientId } : {}) }),
    onSuccess: (r) => {
      setLink({ email, url: inviteLink(location.origin, r.invite_token) });
      setEmail("");
      setClientId("");
      toast("Invitation created. The link is shown once.", "success");
      void qc.invalidateQueries({ queryKey: queryKeys.users });
    },
    onError: (err) => setError(isApiError(err) ? err.message : "The invitation could not be created."),
  });

  const toggle = useMutation({
    mutationFn: (u: FirmUser) => api.auth.setDisabled(u.id, !u.disabled),
    onSuccess: (_r, u) => {
      toast(`${u.name} ${u.disabled ? "enabled" : "disabled"}.`, "success");
      void qc.invalidateQueries({ queryKey: queryKeys.users });
    },
    onError: (err) => toast(isApiError(err) ? err.message : "The account could not be changed.", "error"),
  });

  function submit(e: SyntheticEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    if (role === "client" && !clientId) {
      setError("Choose the client this person belongs to.");
      return;
    }
    invite.mutate();
  }

  const roleDecision = can(me, "users.invite", { role });

  return (
    <>
      <PageHeader
        title="Team & access"
        subtitle="Everyone who can sign in to this firm. Every account uses two-step verification."
      />
      <QueryBoundary query={users} isEmpty={(u) => u.length === 0} empty={<Empty title="No accounts yet" />}>
        {(list) => (
          <Card>
            <table>
              <caption className="visually-hidden">Firm accounts</caption>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Email</th>
                  <th scope="col">Role</th>
                  <th scope="col">Two-step</th>
                  <th scope="col">Last sign-in</th>
                  <th scope="col">Status</th>
                  <th scope="col">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.map((u) => {
                  const self = u.id === me.id;
                  const reason = !disableAllowed.allowed
                    ? `${disableAllowed.reason}${disableAllowed.hint ? ` — ${disableAllowed.hint}` : ""}`
                    : self
                      ? "disabling your own account would sign you out"
                      : undefined;
                  return (
                    <tr key={u.id}>
                      <td>{u.name}</td>
                      <td>{u.email}</td>
                      <td>
                        {u.role.replaceAll("_", " ")}
                        {u.client_id ? ` · ${u.client_id}` : ""}
                        {u.reviewer && u.role === "firm_admin" ? <Chip tone="accent">reviewer</Chip> : null}
                      </td>
                      <td>{u.mfa_enrolled_at ? <Chip tone="good">on</Chip> : <Chip tone="warn">pending</Chip>}</td>
                      <td>{u.last_login_at ? formatDateTime(u.last_login_at) : "—"}</td>
                      <td>{u.disabled ? <Chip tone="bad">disabled</Chip> : <Chip tone="good">active</Chip>}</td>
                      <td>
                        <Button
                          size="sm"
                          tone={u.disabled ? "default" : "danger"}
                          disabledReason={reason}
                          busy={toggle.isPending && toggle.variables.id === u.id}
                          onClick={() => toggle.mutate(u)}
                        >
                          {u.disabled ? "Enable" : "Disable"}
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
        )}
      </QueryBoundary>

      <Card title="Invite someone" className={styles.form}>
        <FormError message={error} />
        <form onSubmit={submit} aria-label="Invite someone">
          <div className={styles.formRow}>
            <InputField
              label="Email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="off"
            />
            <SelectField
              label="Role"
              value={role}
              onChange={(e) => setRole(e.target.value as Exclude<BaseRole, "platform_admin">)}
              hint={roleDecision.allowed ? undefined : roleDecision.reason}
            >
              {(Object.keys(ROLE_LABELS) as Exclude<BaseRole, "platform_admin">[]).map((r) => {
                const d = can(me, "users.invite", { role: r });
                return (
                  <option key={r} value={r} disabled={!d.allowed} title={d.reason}>
                    {ROLE_LABELS[r]}
                    {d.allowed ? "" : ` (${d.reason})`}
                  </option>
                );
              })}
            </SelectField>
          </div>
          {role === "client" ? (
            <SelectField label="Client" value={clientId} onChange={(e) => setClientId(e.target.value)} required>
              <option value="">choose client…</option>
              {(clients.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </SelectField>
          ) : null}
          <div className={styles.actions}>
            <Button
              type="submit"
              tone="primary"
              busy={invite.isPending}
              disabledReason={roleDecision.allowed ? undefined : roleDecision.reason}
            >
              Create invitation
            </Button>
            <span className={styles.meta}>
              Valid 7 days, single use. Granting access needs a recent sign-in: you may be asked for your code.
            </span>
          </div>
        </form>
      </Card>

      <Dialog
        open={link !== null}
        onOpenChange={(open) => !open && setLink(null)}
        title="Invitation created"
        description={`Send this link to ${link?.email ?? ""}. It is shown once, works once, and expires in 7 days.`}
      >
        <div className={styles.linkBox} data-testid="invite-link">
          {link?.url}
        </div>
        <div className={styles.actions}>
          <Button
            tone="primary"
            onClick={() => {
              if (link)
                void copyText(link.url).then((ok) =>
                  toast(ok ? "Link copied." : "Copy failed; select the link and copy it.", ok ? "success" : "error"),
                );
            }}
          >
            Copy link
          </Button>
          <Button onClick={() => setLink(null)}>Done</Button>
        </div>
      </Dialog>
    </>
  );
}
