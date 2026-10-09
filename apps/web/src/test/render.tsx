import type { AuthConfig, Me } from "@agentledger/contracts";
import { render } from "@testing-library/react";
import { createMemoryHistory } from "@tanstack/react-router";
import { HttpResponse, http } from "msw";

import { App } from "../app/App";
import { createQueryClient } from "../app/queryClient";
import { createAppRouter } from "../app/router";
import { createAuthStore } from "../auth/AuthProvider";
import { session } from "../auth/session";
import { localConfig } from "./msw/fixtures";
import { ORIGIN } from "./msw/handlers";
import { server } from "./msw/server";

export interface RenderAppOptions {
  me?: Me | null;
  config?: AuthConfig;
}

/** Mounts the real app (providers, router, screens) at a path with a memory history, as a signed-in person or not. */
export function renderApp(path: string, { me = null, config = localConfig }: RenderAppOptions = {}) {
  if (me) {
    session.set("test-token");
    // GET /api/me answers with the person this render is for (a refresh must not turn them into someone else).
    server.use(http.get(`${ORIGIN}/api/me`, () => HttpResponse.json(me)));
  } else {
    session.clear();
  }
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({
    queries: { ...queryClient.getDefaultOptions().queries, retry: false, retryDelay: 0 },
  });
  const store = createAuthStore({ me, config });
  const router = createAppRouter({ queryClient, auth: store }, createMemoryHistory({ initialEntries: [path] }));
  const utils = render(<App store={store} router={router} />);
  return { ...utils, router, queryClient, store };
}
