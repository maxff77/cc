// Gate-cookie vault data layer (amazon-gate-cookie-vault, Phase 1).
//
// Thin lib/api wrappers + TanStack hooks for the per-gate cookie vault. The
// stored value is a SENSITIVE credential — every endpoint here returns ONLY the
// masked shape (`CookieOut`), never the raw value (the backend masks; the value
// never crosses the wire). Same fetch wrapper as everything else: `api.*`
// carries `credentials: include` and parses the `{code, message}` contract, so
// callers branch on `ApiError.code` (invalid_cookie / cookie_limit_reached /
// gate_not_cookie_mode / cookie_not_found).
import type { CookieListResponse, CookieOut } from "@/types/api";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

// Cache key per gate — the list is gate-scoped, so add/delete invalidate only
// the affected gate's list. Array convention matches lib/query-client.ts.
export const cookiesKey = (gateId: number) => ["cookies", gateId] as const;

// List a gate's cookies (newest first, masked). The query is enabled only for a
// real gate id — `enabled: gateId > 0` keeps the hook safe to call before a
// gate is picked.
//
// `refetchOnMount: "always"` overrides the global 30s staleTime: the engine
// HARD-DELETES cookies during a send (dead-verdict rotation purge), and the
// CookieManager only mounts while idle or inside the cookies-exhausted notice —
// so each time it (re)appears the vault must refetch to drop already-purged
// cookies, not serve a stale cached list.
export function useListCookies(gateId: number | null) {
  return useQuery({
    queryKey: cookiesKey(gateId ?? 0),
    queryFn: () =>
      api.get<CookieListResponse>(
        `/api/cookies?gate_id=${encodeURIComponent(String(gateId))}`,
      ),
    enabled: gateId != null && gateId > 0,
    refetchOnMount: "always",
  });
}

// Store a cookie for a gate. Returns the masked row (200 on an idempotent
// re-store of the same canonical value, 201 on a fresh insert — both deserialize
// to `CookieOut`). Invalidates the gate's list on success.
export function useAddCookie(gateId: number) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: { value: string; label?: string | null }) =>
      api.post<CookieOut>("/api/cookies", {
        gate_id: gateId,
        value: payload.value,
        label: payload.label ?? null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cookiesKey(gateId) });
    },
  });
}

// Hard-delete a cookie the tenant owns. Invalidates the gate's list on success.
export function useDeleteCookie(gateId: number) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (cookieId: number) =>
      api.delete<void>(`/api/cookies/${cookieId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cookiesKey(gateId) });
    },
  });
}
