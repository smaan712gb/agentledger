import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";

import type { RouterContext } from "../app/router";
import { RouteError, RouteNotFound } from "../screens/RouteError";

export const Route = createRootRouteWithContext<RouterContext>()({
  component: () => <Outlet />,
  errorComponent: RouteError,
  notFoundComponent: RouteNotFound,
});
