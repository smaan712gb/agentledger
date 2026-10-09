import type { ClientDetail } from "@agentledger/contracts";
import { Link } from "@tanstack/react-router";

import { formatDate, formatMoney } from "../lib/format";
import { useContextBar } from "../shell/ContextBar";
import { Card, Stat } from "../ui/Card";
import { Chip } from "../ui/Chip";
import { Empty, QueryBoundary } from "../ui/states";
import { useClientDetail } from "./clientDetail";
import styles from "./screens.module.css";

export function OverviewScreen() {
  const { query } = useClientDetail();
  return <QueryBoundary query={query}>{(detail) => <Overview detail={detail} />}</QueryBoundary>;
}

function Overview({ detail }: { detail: ClientDetail }) {
  const context = useContextBar(detail);
  const open = detail.findings.filter((f) => f.status === "open");
  const tasks = detail.tasks;
  const ringColour = detail.integrity > 80 ? "var(--good)" : detail.integrity > 50 ? "var(--warn)" : "var(--bad)";
  return (
    <>
      <div className={styles.grid}>
        <Card title="Integrity">
          <div
            className={styles.ring}
            style={{ "--v": detail.integrity, "--ring-colour": ringColour } as React.CSSProperties}
            role="img"
            aria-label={`Integrity score ${detail.integrity} of 100`}
          >
            <span>{detail.integrity}</span>
          </div>
        </Card>
        {detail.kpis.slice(0, 3).map((k) => (
          <Stat
            key={k.title}
            label={k.title}
            value={k.value === null ? "—" : k.unit === "USD" ? formatMoney(k.value) : `${k.value} ${k.unit}`}
            hint={k.missing ? `needs: ${k.missing}` : context.label}
          />
        ))}
      </div>
      <div className={styles.twoCol}>
        <Card title={`Open findings (${open.length})`}>
          {open.length ? (
            <ul className={styles.list}>
              {open.slice(0, 6).map((f) => (
                <li key={f.id} className={styles.listItem}>
                  <Chip
                    tone={
                      f.severity === "critical" || f.severity === "high"
                        ? "bad"
                        : f.severity === "medium"
                          ? "warn"
                          : "info"
                    }
                  >
                    {f.severity}
                  </Chip>
                  <span>
                    <strong>{f.title}</strong>
                    <div className={styles.meta}>
                      {f.owner === "client" ? "client to resolve" : f.owner === "cpa" ? "CPA to resolve" : "both"}
                      {f.citation ? ` · ${f.citation}` : ""}
                    </div>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="No open findings">
              <p>Clean books for {context.label}.</p>
            </Empty>
          )}
        </Card>
        <Card title="Upcoming deadlines">
          {detail.deadlines.length ? (
            <ul className={styles.list}>
              {detail.deadlines.slice(0, 8).map((d) => (
                <li key={`${d.title}-${d.due}`} className={styles.listItem}>
                  <span>{d.title}</span>
                  <span className={styles.spacer} />
                  <Chip tone={d.days < 14 ? "warn" : "neutral"}>{formatDate(d.due)}</Chip>
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="Nothing due in the next 120 days">
              <p>Deadlines come from the profile facts: an incomplete profile hides some.</p>
            </Empty>
          )}
        </Card>
        <Card title={`Open tasks (${tasks.length})`}>
          {tasks.length ? (
            <ul className={styles.list}>
              {tasks.slice(0, 8).map((t) => (
                <li key={t.id} className={styles.listItem}>
                  <span>
                    <strong>{t.title}</strong>
                    <div className={styles.meta}>
                      {t.assignee}
                      {t.due ? ` · due ${formatDate(t.due)}` : ""}
                    </div>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="No open tasks" />
          )}
        </Card>
        <Card title="Records">
          <ul className={styles.list}>
            <li className={styles.listItem}>
              <span>Ledger chain</span>
              <span className={styles.spacer} />
              {detail.chain.ok ? (
                <Chip tone="good">intact ({detail.chain.checked})</Chip>
              ) : (
                <Chip tone="bad">broken at #{detail.chain.broken_at}</Chip>
              )}
            </li>
            <li className={styles.listItem}>
              <span>Documents</span>
              <span className={styles.spacer} />
              <Link to="/clients/$clientId/documents" params={{ clientId: detail.client.id }} search={(prev) => prev}>
                {detail.documents.length} on file
              </Link>
            </li>
            <li className={styles.listItem}>
              <span>Books closed through</span>
              <span className={styles.spacer} />
              <span>{detail.client.closed_through ? formatDate(detail.client.closed_through) : "not closed"}</span>
            </li>
          </ul>
        </Card>
      </div>
    </>
  );
}
