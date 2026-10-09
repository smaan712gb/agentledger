/**
 * Everything the status page and the review screen read about one return, through one set of queries: the return,
 * its open conflicts, the client's documents (for the "not on the return" check), and the dispositions this session
 * learnt from POST answers (the API has no GET for them: docs/WEB.md section 4). Both screens share the cache, so
 * moving between them costs no request.
 */

import type { Disposition } from "@agentledger/contracts";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";

import { useMe } from "../../auth/AuthProvider";
import { queries, queryKeys } from "../../queries";
import { flattenPages } from "../DocumentsScreen";
import { blockers, unaccountedDocuments } from "./returnState";

/** Its own key root, so invalidating the return's data (`["returns", rid]`) never wipes what the session learnt. */
export const dispositionsKey = (rid: string) => ["return-dispositions", rid] as const;

export function useReturnWorkspace(rid: string) {
  const me = useMe();
  const qc = useQueryClient();
  const detail = useQuery(queries.return(rid));
  // A client's view has no working papers, and the conflicts route is CPA-only.
  const conflicts = useQuery({ ...queries.conflicts(rid), enabled: detail.data?.inputs !== undefined });
  const clientId = detail.data?.return.client_id ?? "";
  const documents = useInfiniteQuery({ ...queries.documents(clientId), enabled: clientId !== "" });
  const dispositions = useQuery({
    queryKey: dispositionsKey(rid),
    queryFn: () => Promise.resolve<Disposition[]>([]),
    staleTime: Infinity,
    gcTime: Infinity,
  });

  const documentList = documents.data ? flattenPages(documents.data).documents : [];
  const known = dispositions.data ?? [];
  const unaccounted = detail.data ? unaccountedDocuments(detail.data, documentList, known) : [];
  const checklist = detail.data ? blockers(detail.data, conflicts.data ?? [], unaccounted) : [];

  return {
    me,
    rid,
    clientId,
    detail,
    conflicts,
    documents: documentList,
    moreDocuments: documents.hasNextPage,
    dispositions: known,
    setDispositions: (list: Disposition[]) => qc.setQueryData(dispositionsKey(rid), list),
    unaccounted,
    checklist,
    /** After anything that changes the return: its detail, conflicts and history, and the client's list of returns. */
    invalidate: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: queryKeys.return(rid) }),
        clientId ? qc.invalidateQueries({ queryKey: queryKeys.returns(clientId) }) : Promise.resolve(),
      ]),
  };
}

export type ReturnWorkspace = ReturnType<typeof useReturnWorkspace>;
