import { isApiError } from "@agentledger/contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type SyntheticEvent } from "react";

import { api } from "../api";
import { useMe } from "../auth/AuthProvider";
import { can } from "../auth/can";
import { copyText } from "../lib/clipboard";
import { formatDate } from "../lib/format";
import { queries, queryKeys } from "../queries";
import { Button } from "../ui/Button";
import { Card, PageHeader } from "../ui/Card";
import { StatusChip } from "../ui/Chip";
import { Dialog } from "../ui/Dialog";
import { FormError, InputField } from "../ui/Field";
import { Empty, QueryBoundary } from "../ui/states";
import { useToast } from "../ui/Toast";
import { inviteLink } from "./TeamScreen";
import styles from "./screens.module.css";

export function FirmsScreen() {
  const me = useMe();
  const qc = useQueryClient();
  const { toast } = useToast();
  const firms = useQuery(queries.firms());
  const [name, setName] = useState("");
  const [id, setId] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [link, setLink] = useState<{ firm: string; url: string } | null>(null);
  const allowed = can(me, "firms.create");

  const create = useMutation({
    mutationFn: () => api.platform.createFirm({ id: id.trim(), name: name.trim(), admin_email: adminEmail.trim() }),
    onSuccess: (r) => {
      setLink({ firm: r.firm.name, url: inviteLink(location.origin, r.admin_invite_token) });
      setName("");
      setId("");
      setAdminEmail("");
      toast(`Firm ${r.firm.name} created (${r.firm.status}).`, "success");
      void qc.invalidateQueries({ queryKey: queryKeys.firms });
    },
    onError: (err) => setError(isApiError(err) ? err.message : "The firm could not be created."),
  });

  function submit(e: SyntheticEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    create.mutate();
  }

  return (
    <>
      <PageHeader title="Firms" subtitle="Each firm has its own database, document vault and encryption key." />
      <QueryBoundary query={firms} isEmpty={(f) => f.length === 0} empty={<Empty title="No firms yet" />}>
        {(list) => (
          <Card>
            <table>
              <caption className="visually-hidden">Firms on this platform</caption>
              <thead>
                <tr>
                  <th scope="col">Firm</th>
                  <th scope="col">Id</th>
                  <th scope="col">Status</th>
                  <th scope="col">Since</th>
                </tr>
              </thead>
              <tbody>
                {list.map((f) => (
                  <tr key={f.id}>
                    <td>{f.name}</td>
                    <td>
                      <code>{f.id}</code>
                    </td>
                    <td>
                      <StatusChip status={f.status} />
                    </td>
                    <td>{formatDate(f.created_at.slice(0, 10))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </QueryBoundary>

      <Card title="Onboard a firm" className={styles.form}>
        <FormError message={error} />
        <form onSubmit={submit} aria-label="Onboard a firm">
          <div className={styles.formRow}>
            <InputField label="Firm name" required value={name} onChange={(e) => setName(e.target.value)} />
            <InputField
              label="Firm id"
              required
              pattern="[a-z0-9][a-z0-9-]{1,40}"
              hint="2 to 41 lowercase letters, digits or hyphens"
              value={id}
              onChange={(e) => setId(e.target.value)}
            />
          </div>
          <InputField
            label="Administrator email"
            type="email"
            required
            value={adminEmail}
            onChange={(e) => setAdminEmail(e.target.value)}
            autoComplete="off"
          />
          <div className={styles.actions}>
            <Button
              type="submit"
              tone="primary"
              busy={create.isPending}
              disabledReason={allowed.allowed ? undefined : allowed.reason}
            >
              Create firm
            </Button>
            <span className={styles.meta}>Creating a firm needs a recent sign-in: you may be asked for your code.</span>
          </div>
        </form>
      </Card>

      <Dialog
        open={link !== null}
        onOpenChange={(open) => !open && setLink(null)}
        title="Firm created"
        description={`Send the administrator of ${link?.firm ?? ""} this link. It is shown once and works once.`}
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
