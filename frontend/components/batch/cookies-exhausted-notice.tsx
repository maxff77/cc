"use client";

// Cookies-exhausted prompt (amazon-gate-send-rotation Phase 2): DANGER strip —
// the send worker ran out of active cookies for the live cookie-mode gate and
// latched the batch into a `cookies_exhausted` pause (a real stall, not a
// "waiting" — mirrors WatchdogNotice's danger styling, DESIGN.md "Danger red —
// destructive or failed"). The client must add at least one cookie and resume;
// the worker then continues from the failed line. It renders ONLY while the
// surface is `paused` with `pauseReason === "cookies_exhausted"` (gated by the
// caller in app/app/page.tsx). The inlined CookieManager lets the client top up
// the vault without leaving the cockpit; Reanudar fires the existing batch
// resume endpoint and the resulting `batch.state` event is the single source of
// truth (UX-DR12 — no optimistic clear).
import { useMemo } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { useLiveBatch } from "@/lib/ws";
import { CookieManager } from "@/components/batch/cookie-manager";
import { Btn } from "@/components/ui/btn";
import { Notice } from "@/components/ui/notice";

// The cockpit catalog shape (mirror of app/app/page.tsx's GateListResponse).
// Only `id` + `display_value` are read here — to resolve the live gate id.
interface CatalogGate {
  id: number;
  display_value: string;
}

interface GateListResponse {
  items: CatalogGate[];
  total: number;
}

export function CookiesExhaustedNotice() {
  const live = useLiveBatch();

  // Shared ["gates"] query — dedupes with the cockpit's own catalog fetch, so
  // this round-trip is free. The selector is locked while a lote is live, so
  // the live gate is resolved by matching the batch's "Comando visible"
  // (display_value) against the catalog — the same idiom send-form uses for
  // `liveGateId` (the gate `id` itself never travels on the WS frame).
  const gates = useQuery({
    queryKey: ["gates"],
    queryFn: () => api.get<GateListResponse>("/api/gates"),
  });

  const gateId = useMemo(() => {
    const items = gates.data?.items ?? [];

    return (
      items.find((g) => g.display_value === live.gateDisplayValue)?.id ?? null
    );
  }, [gates.data, live.gateDisplayValue]);

  // Resume the SAME batch (not the watchdog): the server re-queues the failed
  // line and continues. onSuccess deliberately does NOT touch the store — the
  // resulting `batch.state` event (which clears `pauseReason`) is authoritative.
  const resume = useMutation({
    mutationFn: () => api.post<void>(`/api/batches/${live.batchId}/resume`),
  });

  return (
    <div
      className="flex flex-col gap-3 rounded border border-danger/50 bg-danger/10 px-3 py-2.5"
      role="alert"
    >
      <p className="text-xs font-semibold text-danger">
        Se agotaron las cookies. Agrega más para continuar.
      </p>

      {/* Inline vault: top up the cookies for the live gate without leaving the
          cockpit. Mounts only once the live gate id is resolved from the
          catalog (a transient catalog load shows nothing here). */}
      {gateId !== null && <CookieManager gateId={gateId} />}

      <div className="flex flex-col gap-1">
        <Btn
          disabled={resume.isPending || live.batchId === null}
          icon="play"
          size="sm"
          variant="success"
          onClick={() => resume.mutate()}
        >
          {resume.isPending ? "Reanudando…" : "Reanudar"}
        </Btn>
        {resume.isError && (
          <Notice status="danger">
            {resume.error instanceof ApiError
              ? resume.error.message
              : "No pudimos reanudar. Intenta de nuevo."}
          </Notice>
        )}
      </div>
    </div>
  );
}
