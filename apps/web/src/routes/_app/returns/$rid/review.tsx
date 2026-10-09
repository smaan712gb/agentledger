import { createFileRoute } from "@tanstack/react-router";

import { ReturnReviewScreen } from "../../../../screens/returns/ReturnReviewScreen";

/** The inputs editor beside the document, with conflicts, fact history and dispositions. Loaded only when opened. */
export const Route = createFileRoute("/_app/returns/$rid/review")({
  component: ReturnReviewScreen,
});
