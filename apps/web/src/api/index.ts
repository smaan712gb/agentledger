/** The one API client of the app, wired to the session, the step-up dialog and the idle tracker. */

import { createApiClient, endpoints } from "@agentledger/contracts";

import { activity, sessionEnded, stepUpBridge } from "../auth/bridges";
import { session } from "../auth/session";

export const client = createApiClient({
  getToken: () => session.get(),
  onUnauthorized: ({ path }) => {
    sessionEnded.emit({ path });
  },
  onStepUp: (request) => stepUpBridge.request(request),
  onActivity: (at) => {
    activity.touch(at);
  },
});

export const api = endpoints(client);
