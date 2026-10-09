import { createFileRoute } from "@tanstack/react-router";

import { VerifyScreen } from "../../screens/VerifyScreen";

export const Route = createFileRoute("/sign-in/verify")({
  component: VerifyScreen,
});
