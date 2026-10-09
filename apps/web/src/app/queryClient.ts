import { isApiError } from "@agentledger/contracts";
import { QueryClient } from "@tanstack/react-query";

/** A 4xx is an answer, not a hiccup: only network failures and 5xx are retried, and only once. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) =>
          failureCount < 1 && (!isApiError(error) || error.status === 0 || error.status >= 500),
        retryDelay: 1500,
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
      },
      mutations: { retry: false },
    },
  });
}
