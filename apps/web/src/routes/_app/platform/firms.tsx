import { createFileRoute } from "@tanstack/react-router";

import { requirePlatformAdmin } from "../../../auth/guards";
import { FirmsScreen } from "../../../screens/FirmsScreen";

export const Route = createFileRoute("/_app/platform/firms")({
  beforeLoad: ({ context }) => {
    requirePlatformAdmin(context.me);
  },
  component: FirmsScreen,
});
