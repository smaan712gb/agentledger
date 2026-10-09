import { createFileRoute } from "@tanstack/react-router";

import { ProfileScreen } from "../../../../screens/ProfileScreen";

export const Route = createFileRoute("/_app/clients/$clientId/profile")({
  component: ProfileScreen,
});
