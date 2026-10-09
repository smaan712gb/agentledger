import { useQuery } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";

import { useMe } from "../auth/AuthProvider";
import { isFirmStaff } from "../auth/can";
import { queries } from "../queries";
import { ContextBar, useContextBar } from "../shell/ContextBar";
import { PageHeader } from "../ui/Card";
import { Frozen, QueryBoundary } from "../ui/states";
import { useClientDetail } from "./clientDetail";
import styles from "./screens.module.css";

/** The entity workspace: header, context bar and section tabs; the sections render in the outlet. */
export function ClientLayout() {
  const me = useMe();
  const { clientId, query } = useClientDetail();
  const clients = useQuery({ ...queries.clients(), enabled: isFirmStaff(me) });
  const context = useContextBar(query.data);
  const client = query.data?.client;

  return (
    <>
      <PageHeader
        title={client?.name ?? clientId}
        subtitle={
          query.data ? (
            <>
              {query.data.pack?.title ?? client?.domain} · {client?.kind}
              {client?.entity_type ? ` · ${client.entity_type}` : ""}
            </>
          ) : null
        }
      />
      <ContextBar detail={query.data} clients={clients.data} />
      {context.frozen.frozen && context.frozen.reason ? <Frozen reason={context.frozen.reason} /> : null}
      <nav className={styles.tabs} aria-label="Client sections">
        <Link
          to="/clients/$clientId"
          params={{ clientId }}
          search={(prev) => prev}
          className={styles.tab}
          activeOptions={{ exact: true, includeSearch: false }}
          activeProps={{ className: [styles.tab, styles.tabActive].join(" "), "aria-current": "page" }}
        >
          Overview
        </Link>
        <Link
          to="/clients/$clientId/documents"
          params={{ clientId }}
          search={(prev) => prev}
          className={styles.tab}
          activeOptions={{ includeSearch: false }}
          activeProps={{ className: [styles.tab, styles.tabActive].join(" "), "aria-current": "page" }}
        >
          Documents{query.data ? ` (${query.data.documents.length})` : ""}
        </Link>
        {isFirmStaff(me) ? (
          <Link
            to="/clients/$clientId/returns"
            params={{ clientId }}
            search={(prev) => prev}
            className={styles.tab}
            activeOptions={{ includeSearch: false }}
            activeProps={{ className: [styles.tab, styles.tabActive].join(" "), "aria-current": "page" }}
          >
            Returns
          </Link>
        ) : null}
        <Link
          to="/clients/$clientId/profile"
          params={{ clientId }}
          search={(prev) => prev}
          className={styles.tab}
          activeOptions={{ includeSearch: false }}
          activeProps={{ className: [styles.tab, styles.tabActive].join(" "), "aria-current": "page" }}
        >
          Profile
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
