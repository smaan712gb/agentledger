import { createFileRoute } from "@tanstack/react-router";

import { OverviewScreen } from "../../../../screens/OverviewScreen";

export const Route = createFileRoute("/_app/clients/$clientId/")({
  component: OverviewScreen,
});
