import { createFileRoute } from "@tanstack/react-router";

import { ReturnStatusScreen } from "../../../../screens/returns/ReturnStatusScreen";

export const Route = createFileRoute("/_app/returns/$rid/")({
  component: ReturnStatusScreen,
});
