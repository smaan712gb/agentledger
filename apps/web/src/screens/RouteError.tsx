import { Link, type ErrorComponentProps } from "@tanstack/react-router";

import { Conflict, ErrorState, Forbidden, Gone, Loading, NotFound, mapError } from "../ui/states";

/** Errors thrown by guards and loaders become the same states the screens use. */
export function RouteError({ error, reset }: ErrorComponentProps) {
  const e = mapError(error);
  switch (e.kind) {
    case "forbidden":
      return <Forbidden detail={e.detail} requestId={e.requestId} />;
    case "not-found":
      return <NotFound detail={e.detail} action={<Link to="/">Go to the start</Link>} />;
    case "conflict":
      return <Conflict detail={e.detail} onRefresh={reset} />;
    case "gone":
      return <Gone detail={e.detail} />;
    case "unauthorized":
      return <Loading label="Signing in again" />;
    default:
      return <ErrorState detail={e.detail} requestId={e.requestId} onRetry={reset} />;
  }
}

export function RouteNotFound() {
  return <NotFound action={<Link to="/">Go to the start</Link>} />;
}
