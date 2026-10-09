import { createFileRoute, redirect } from "@tanstack/react-router";

import { homeFor } from "../auth/can";
import { requireSignedIn } from "../auth/guards";

/** "/" is a dispatcher: each role has its own landing (platform console, practice, client portal). */
export const Route = createFileRoute("/")({
  beforeLoad: ({ context, location }) => {
    const me = requireSignedIn(context.auth, location.href);
    throw redirect({ to: homeFor(me) });
  },
  component: () => null,
});
