import { setupServer } from "msw/node";

import { happyHandlers } from "./handlers";

export const server = setupServer(...happyHandlers());
