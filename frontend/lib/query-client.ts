// The app-wide TanStack Query client (the architecture-mandated REST data layer,
// introduced in Story 1.3 — the first real list/query surface).
//
// `makeQueryClient` builds a fresh client; `getQueryClient` returns a
// browser-side singleton so the cache survives navigations within a session.
// Cache keys follow the array convention: ['admin-users'], ['me'].
import { QueryClient } from "@tanstack/react-query";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: 1,
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

export function getQueryClient(): QueryClient {
  if (typeof window === "undefined") {
    // Server: always a fresh client (no cross-request cache sharing).
    return makeQueryClient();
  }
  // Browser: reuse one client for the tab's lifetime.
  if (!browserQueryClient) browserQueryClient = makeQueryClient();

  return browserQueryClient;
}
