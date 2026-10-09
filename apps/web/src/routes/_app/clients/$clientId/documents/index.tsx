import { createFileRoute } from "@tanstack/react-router";

import { DocumentsScreen } from "../../../../../screens/DocumentsScreen";

export const Route = createFileRoute("/_app/clients/$clientId/documents/")({
  component: DocumentsScreen,
});
