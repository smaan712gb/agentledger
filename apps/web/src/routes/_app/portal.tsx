import { createFileRoute, redirect } from "@tanstack/react-router";

import { homeFor, isClient } from "../../auth/can";
import { PortalScreen } from "../../screens/PortalScreen";

/** The client portal's landing (slice 4 fills it in); firm and platform accounts are sent to their own home. */
export const Route = createFileRoute("/_app/portal")({
  beforeLoad: ({ context }) => {
    if (!isClient(context.me)) throw redirect({ to: homeFor(context.me) });
  },
  component: PortalScreen,
});
