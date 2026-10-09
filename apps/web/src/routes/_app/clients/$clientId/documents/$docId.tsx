import { createFileRoute } from "@tanstack/react-router";

import { DocumentScreen } from "../../../../../screens/DocumentScreen";

export const Route = createFileRoute("/_app/clients/$clientId/documents/$docId")({
  component: DocumentScreen,
});
