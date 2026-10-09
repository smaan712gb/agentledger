import { createFileRoute } from "@tanstack/react-router";

import { requireFirmStaff } from "../../../auth/guards";
import { ReturnLayout } from "../../../screens/returns/ReturnLayout";

/**
 * One return's workspace (status, review). Firm staff only: the working papers are theirs (app.py `cpa_only`); the
 * engagement check happens at the API, whose 403 is rendered, because the client is known only once the return loads.
 */
export const Route = createFileRoute("/_app/returns/$rid")({
  beforeLoad: ({ context }) => {
    requireFirmStaff(context.me);
  },
  component: ReturnLayout,
});
