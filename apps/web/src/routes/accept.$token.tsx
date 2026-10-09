import { createFileRoute } from "@tanstack/react-router";

import { AcceptScreen } from "../screens/AcceptScreen";

/** An invitation link: /accept/<token> (the previous interface's /#/accept/<token> is redirected here at boot). */
export const Route = createFileRoute("/accept/$token")({
  component: AcceptScreen,
});
