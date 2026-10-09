import type { ReturnDetail, ReturnRow } from "@agentledger/contracts";
import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useParams } from "@tanstack/react-router";
import { useId } from "react";

import { useMe } from "../../auth/AuthProvider";
import { queries } from "../../queries";
import { firmLabel } from "../../shell/ContextBar";
import cb from "../../shell/ContextBar.module.css";
import { PageHeader } from "../../ui/Card";
import { StatusChip } from "../../ui/Chip";
import { QueryBoundary } from "../../ui/states";
import styles from "../screens.module.css";
import { describeStatus } from "./returnState";

export function returnTitle(r: ReturnRow): string {
  return `Form ${r.form} · ${r.tax_year}`;
}

/** The return workspace: header with the status, the context strip, Status | Review tabs; the screens render in the outlet. */
export function ReturnLayout() {
  const { rid } = useParams({ from: "/_app/returns/$rid" });
  const query = useQuery(queries.return(rid));
  const detail = query.data;
  return (
    <>
      <PageHeader
        title={detail ? returnTitle(detail.return) : "Return"}
        subtitle={
          detail ? (
            <>
              <StatusChip status={detail.status} /> {describeStatus(detail.status)} · version {detail.version}
              {detail.waiting_on ? ` · waiting on ${detail.waiting_on}` : ""}
            </>
          ) : null
        }
      />
      <ReturnContextBar detail={detail} />
      <nav className={styles.tabs} aria-label="Return sections">
        <Link
          to="/returns/$rid"
          params={{ rid }}
          className={styles.tab}
          activeOptions={{ exact: true }}
          activeProps={{ className: [styles.tab, styles.tabActive].join(" "), "aria-current": "page" }}
        >
          Status
        </Link>
        <Link
          to="/returns/$rid/review"
          params={{ rid }}
          className={styles.tab}
          activeProps={{ className: [styles.tab, styles.tabActive].join(" "), "aria-current": "page" }}
        >
          Review
        </Link>
      </nav>
      <QueryBoundary
        query={query}
        notFoundAction={
          <Link to="/clients" search={{}}>
            Back to clients
          </Link>
        }
      >
        {() => <Outlet />}
      </QueryBoundary>
    </>
  );
}

/** Firm · entity · period · form, read from the return itself (the context bar's state for a return is the return). */
export function ReturnContextBar({ detail }: { detail: ReturnDetail | undefined }) {
  const me = useMe();
  const clients = useQuery({ ...queries.clients(), enabled: me.base_role !== "client" });
  const ids = { firm: useId(), entity: useId(), period: useId(), form: useId() };
  const clientId = detail?.return.client_id;
  const clientName = clientId ? (clients.data?.find((c) => c.id === clientId)?.name ?? clientId) : "…";
  return (
    <nav className={cb.bar} aria-label="Context">
      <div className={cb.item}>
        <span className={cb.label} id={ids.firm}>
          Firm
        </span>
        <span className={cb.value} aria-labelledby={ids.firm} data-testid="context-firm">
          {firmLabel(me)}
        </span>
      </div>
      <div className={cb.item}>
        <span className={cb.label} id={ids.entity}>
          Entity
        </span>
        <span className={cb.value} aria-labelledby={ids.entity} data-testid="context-entity">
          {detail ? (
            <Link
              to="/clients/$clientId"
              params={{ clientId: detail.return.client_id }}
              search={{ year: detail.return.tax_year }}
            >
              {clientName}
            </Link>
          ) : (
            clientName
          )}
        </span>
      </div>
      <div className={cb.item}>
        <span className={cb.label} id={ids.period}>
          Period
        </span>
        <span className={cb.value} aria-labelledby={ids.period} data-testid="context-period">
          {detail ? `FY${detail.return.tax_year}` : "…"}
        </span>
      </div>
      <div className={cb.item}>
        <span className={cb.label} id={ids.form}>
          Form
        </span>
        <span className={cb.value} aria-labelledby={ids.form}>
          {detail ? (
            <>
              {detail.return.form}
              {detail.return.amends ? (
                <>
                  {" · amends "}
                  <Link to="/returns/$rid" params={{ rid: detail.return.amends }}>
                    {detail.return.amends}
                  </Link>
                </>
              ) : null}
            </>
          ) : (
            "…"
          )}
        </span>
      </div>
    </nav>
  );
}
