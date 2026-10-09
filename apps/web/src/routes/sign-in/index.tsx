import { createFileRoute, redirect } from "@tanstack/react-router";
import { z } from "zod";

import { homeFor } from "../../auth/can";
import { safeNext } from "../../lib/safeNext";
import { SignInScreen } from "../../screens/SignInScreen";

const searchSchema = z.object({
  /** Where to go after signing in: a same-origin path only. */
  next: z.string().optional().catch(undefined),
});

export const Route = createFileRoute("/sign-in/")({
  validateSearch: (search) => searchSchema.parse(search),
  beforeLoad: ({ context, search }) => {
    if (context.auth.me) {
      const next = safeNext(search.next);
      if (next) throw redirect({ href: next });
      throw redirect({ to: homeFor(context.auth.me) });
    }
  },
  component: SignInScreen,
});
