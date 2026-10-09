import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useSearch } from "@tanstack/react-router";

import { queries, queryKeys } from "../queries";
import { currentYear } from "../shell/contextState";

/** The client detail for the entity and period in the URL; every screen under /clients/$clientId shares this query. */
export function useClientDetail() {
  const { clientId } = useParams({ from: "/_app/clients/$clientId" });
  const search = useSearch({ from: "/_app/clients/$clientId" });
  const year = search.year ?? currentYear();
  return { clientId, year, query: useQuery(queries.clientDetail(clientId, year)) };
}

/** After a change to a client, every period's detail is stale. */
export function useInvalidateClient() {
  const qc = useQueryClient();
  return (clientId: string) =>
    Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.client(clientId) }),
      qc.invalidateQueries({ queryKey: queryKeys.clients, exact: true }),
    ]);
}
