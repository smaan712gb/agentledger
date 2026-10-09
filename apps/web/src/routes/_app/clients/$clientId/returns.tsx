import { createFileRoute } from "@tanstack/react-router";

import { requireFirmStaff } from "../../../../auth/guards";
import { ReturnsScreen } from "../../../../screens/returns/ReturnsScreen";

/** The client's returns: a list with status, year and form, and the form that creates a Form 1040 for a tax year. */
export const Route = createFileRoute("/_app/clients/$clientId/returns")({
  beforeLoad: ({ context }) => {
    requireFirmStaff(context.me);
  },
  component: ReturnsScreen,
});
