import { createFileRoute } from "@tanstack/react-router";

import { requireFirmStaff } from "../../auth/guards";
import { TeamScreen } from "../../screens/TeamScreen";

export const Route = createFileRoute("/_app/team")({
  beforeLoad: ({ context }) => {
    requireFirmStaff(context.me);
  },
  component: TeamScreen,
});
