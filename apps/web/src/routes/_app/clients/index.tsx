import { createFileRoute, redirect } from "@tanstack/react-router";
import { z } from "zod";

import { isClient } from "../../../auth/can";
import { requireFirmStaff } from "../../../auth/guards";
import { ClientsScreen } from "../../../screens/ClientsScreen";

const searchSchema = z.object({
  /** The search box, kept in the URL so a filtered list can be shared. */
  q: z.string().optional().catch(undefined),
});

export const Route = createFileRoute("/_app/clients/")({
  validateSearch: (search) => searchSchema.parse(search),
  beforeLoad: ({ context }) => {
    if (isClient(context.me)) throw redirect({ to: "/portal" });
    requireFirmStaff(context.me);
  },
  component: ClientsScreen,
});
