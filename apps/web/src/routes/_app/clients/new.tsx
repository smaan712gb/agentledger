import { createFileRoute } from "@tanstack/react-router";

import { requireFirmStaff } from "../../../auth/guards";
import { NewClientScreen } from "../../../screens/NewClientScreen";

export const Route = createFileRoute("/_app/clients/new")({
  beforeLoad: ({ context }) => {
    requireFirmStaff(context.me);
  },
  component: NewClientScreen,
});
