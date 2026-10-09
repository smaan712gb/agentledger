import { createFileRoute } from "@tanstack/react-router";

import { requireClientAccess } from "../../../auth/guards";
import { ClientLayout } from "../../../screens/ClientLayout";
import { clientSearchSchema } from "../../../shell/contextState";

/** One entity's workspace. The context bar's state (period, engagement) lives in this route's search params. */
export const Route = createFileRoute("/_app/clients/$clientId")({
  validateSearch: (search) => clientSearchSchema.parse(search),
  beforeLoad: ({ context, params }) => {
    requireClientAccess(context.me, params.clientId);
  },
  component: ClientLayout,
});
