import { createFileRoute } from "@tanstack/react-router";

import { requireFirmStaff } from "../../auth/guards";
import { InboxScreen } from "../../screens/InboxScreen";

export const Route = createFileRoute("/_app/inbox")({
  beforeLoad: ({ context }) => {
    requireFirmStaff(context.me);
  },
  component: InboxScreen,
});
