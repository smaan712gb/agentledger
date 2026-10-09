import { onlineManager, type UseQueryResult } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { mapError } from "./mapError";
import { Conflict, Empty, ErrorState, Forbidden, Gone, Loading, NotFound, Stale } from "./states";

export interface QueryBoundaryProps<T> {
  query: UseQueryResult<T>;
  children: (data: T) => ReactNode;
  /** When true for the loaded data, the Empty state renders instead of the children. */
  isEmpty?: ((data: T) => boolean) | undefined;
  empty?: ReactNode;
  loadingLabel?: string;
  /** A 404 on a detail screen deserves a way out (a link back). */
  notFoundAction?: ReactNode;
}

/** Observes the browser's connectivity through React Query's onlineManager (one source for the whole app). */
export function useOnline(): boolean {
  const [online, setOnline] = useState(onlineManager.isOnline());
  useEffect(() => onlineManager.subscribe(setOnline), []);
  return online;
}

/**
 * Every data-bearing screen renders through this: one place turns a query's status into the required states, and
 * data already on screen stays visible (marked stale) when a refetch fails or the browser is offline.
 */
export function QueryBoundary<T>({
  query,
  children,
  isEmpty,
  empty,
  loadingLabel,
  notFoundAction,
}: QueryBoundaryProps<T>) {
  const online = useOnline();
  if (query.status === "pending") {
    return <Loading {...(loadingLabel ? { label: loadingLabel } : {})} />;
  }
  if (query.status === "error" && query.data === undefined) {
    const e = mapError(query.error);
    switch (e.kind) {
      case "forbidden":
        return <Forbidden detail={e.detail} requestId={e.requestId} />;
      case "not-found":
        return <NotFound detail={e.detail} action={notFoundAction} />;
      case "conflict":
        return <Conflict detail={e.detail} onRefresh={() => void query.refetch()} />;
      case "gone":
        return <Gone detail={e.detail} />;
      case "offline":
        return (
          <ErrorState
            title="Could not reach the service"
            detail={online ? e.detail : "You are offline."}
            requestId={e.requestId}
            onRetry={() => void query.refetch()}
          />
        );
      case "unauthorized":
        // The session rule in the fetch layer already sent the person to sign in; nothing to render here.
        return <Loading label="Signing in again" />;
      default:
        return <ErrorState detail={e.detail} requestId={e.requestId} onRetry={() => void query.refetch()} />;
    }
  }
  const data = query.data as T;
  const stale = query.isError || !online;
  if (isEmpty?.(data) && !stale) {
    return <>{empty ?? <Empty />}</>;
  }
  return (
    <>
      {stale ? <Stale asOf={query.dataUpdatedAt} retrying={online && query.isError} /> : null}
      {children(data)}
    </>
  );
}
