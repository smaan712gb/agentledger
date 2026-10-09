import type { Client } from "@agentledger/contracts";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo } from "react";

import { useMe } from "../auth/AuthProvider";
import { can } from "../auth/can";
import { formatDate } from "../lib/format";
import { queries } from "../queries";
import { Button } from "../ui/Button";
import { Card, PageHeader } from "../ui/Card";
import { DataTable } from "../ui/DataTable";
import { Empty, QueryBoundary } from "../ui/states";
import styles from "./screens.module.css";

export function filterClients(clients: Client[], q: string | undefined): Client[] {
  const needle = (q ?? "").trim().toLowerCase();
  if (!needle) return clients;
  return clients.filter((c) =>
    [c.id, c.name, c.domain, c.entity_type ?? ""].some((v) => v.toLowerCase().includes(needle)),
  );
}

export function ClientsScreen() {
  const me = useMe();
  const navigate = useNavigate();
  const { q } = useSearch({ from: "/_app/clients/" });
  const query = useQuery(queries.clients());
  const create = can(me, "clients.create");

  const columns = useMemo<ColumnDef<Client>[]>(
    () => [
      {
        id: "name",
        header: "Client",
        accessorFn: (c) => c.name,
        cell: ({ row }) => (
          <Link to="/clients/$clientId" params={{ clientId: row.original.id }} search={{}}>
            <strong>{row.original.name}</strong>
          </Link>
        ),
      },
      { id: "id", header: "Id", accessorFn: (c) => c.id, cell: ({ getValue }) => <code>{getValue<string>()}</code> },
      { id: "kind", header: "Kind", accessorFn: (c) => c.kind },
      { id: "domain", header: "Industry pack", accessorFn: (c) => c.domain.replaceAll("_", " ") },
      { id: "entity", header: "Entity type", accessorFn: (c) => c.entity_type ?? "—" },
      {
        id: "closed",
        header: "Closed through",
        accessorFn: (c) => c.closed_through ?? "",
        cell: ({ row }) =>
          row.original.closed_through ? formatDate(row.original.closed_through) : <span className="muted">open</span>,
      },
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Clients"
        subtitle={
          me.base_role === "staff" ? "The clients you are engaged on." : "Every client in its own segregated workspace."
        }
        actions={
          <Button
            tone="primary"
            disabledReason={create.allowed ? undefined : create.reason}
            onClick={() => void navigate({ to: "/clients/new" })}
          >
            New client
          </Button>
        }
      />
      <div className={styles.toolbar}>
        <label htmlFor="client-search" className="visually-hidden">
          Search clients
        </label>
        <input
          id="client-search"
          type="search"
          className={styles.search}
          placeholder="Search by name, id or industry"
          value={q ?? ""}
          onChange={(e) => void navigate({ to: "/clients", search: { q: e.target.value || undefined }, replace: true })}
        />
      </div>
      <QueryBoundary
        query={query}
        isEmpty={(clients) => clients.length === 0}
        empty={
          <Empty
            title="No clients yet"
            action={
              <Button
                tone="primary"
                disabledReason={create.allowed ? undefined : create.reason}
                onClick={() => void navigate({ to: "/clients/new" })}
              >
                Add the first client
              </Button>
            }
          >
            <p>
              {me.base_role === "staff"
                ? "You are not engaged on any client yet; ask a CPA or your firm administrator."
                : "Create a client to open its workspace."}
            </p>
          </Empty>
        }
      >
        {(clients) => {
          const rows = filterClients(clients, q);
          return (
            <Card>
              <DataTable
                data={rows}
                columns={columns}
                caption="Clients"
                getRowId={(c) => c.id}
                initialSorting={[{ id: "name", desc: false }]}
                renderEmpty={() => (
                  <Empty title="No clients match">
                    <p>Nothing matches “{q}”.</p>
                  </Empty>
                )}
              />
            </Card>
          );
        }}
      </QueryBoundary>
    </>
  );
}
