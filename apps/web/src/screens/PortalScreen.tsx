import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { useMe } from "../auth/AuthProvider";
import { formatDate } from "../lib/format";
import { queries } from "../queries";
import { currentYear } from "../shell/contextState";
import { Card, PageHeader, Stat } from "../ui/Card";
import { Empty, QueryBoundary, StatePanel } from "../ui/states";
import styles from "./screens.module.css";

/** The client portal's landing: the person's own business, what is needed from them, and the way to upload. */
export function PortalScreen() {
  const me = useMe();
  const clientId = me.client_id;
  const year = currentYear();
  const query = useQuery({ ...queries.clientDetail(clientId ?? "", year), enabled: Boolean(clientId) });

  if (!clientId) {
    return (
      <StatePanel title="Your account is not linked to a business yet" tone="info" state="not-yet">
        <p>Ask your CPA to link your account to your business; until then there is nothing to show here.</p>
      </StatePanel>
    );
  }

  return (
    <>
      <PageHeader
        title={`Welcome, ${me.name.split(" ")[0] ?? me.name}`}
        subtitle="Your books are being kept for you."
      />
      <QueryBoundary query={query}>
        {(detail) => {
          const requests = detail.tasks.filter((t) => t.assignee === "client");
          return (
            <>
              <div className={styles.grid}>
                <Stat label="Business" value={detail.client.name} hint={detail.client.kind} />
                <Stat label="Integrity" value={detail.integrity} hint="shared with your CPA" />
                <Stat label="Documents on file" value={detail.documents.length} hint={`FY${detail.year}`} />
              </div>
              <Card
                title="What we need from you"
                actions={
                  <Link to="/clients/$clientId/documents" params={{ clientId }} search={{}}>
                    Upload documents
                  </Link>
                }
              >
                {requests.length ? (
                  <ul className={styles.list}>
                    {requests.map((t) => (
                      <li key={t.id} className={styles.listItem}>
                        <span>
                          <strong>{t.title}</strong>
                          {t.due ? <div className={styles.meta}>due {formatDate(t.due)}</div> : null}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty title="Nothing right now">
                    <p>Forward receipts and tax forms, or upload them; your CPA sees exactly what is missing.</p>
                  </Empty>
                )}
              </Card>
              <p className={styles.hint}>
                The full portal (returns, messages, approvals) arrives in a later release;{" "}
                <Link to="/clients/$clientId" params={{ clientId }} search={{}}>
                  open your workspace
                </Link>
                .
              </p>
            </>
          );
        }}
      </QueryBoundary>
    </>
  );
}
