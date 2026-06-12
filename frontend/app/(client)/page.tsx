"use client";

// Envío surface (Story 2.2). Live state is driven ONLY by the WS store
// (UX-DR12 — no optimistic state beyond the server-confirmed POST seed).
// Desktop ≥lg: 3-col grid 300px 1fr 1fr (UX-DR19) — the two right columns
// stay EMPTY until Story 3.2's data panels; don't fake them.
// Pause/Detener controls, state pill and the FloodWait notice are Story 2.3.
import { useQuery } from "@tanstack/react-query";
import { Alert, Spinner } from "@heroui/react";

import { api } from "@/lib/api";
import { useLiveBatch } from "@/lib/ws";
import { ProgressRing } from "@/components/batch/progress-ring";
import { SendForm, type GateOut } from "@/components/batch/send-form";

interface GateListResponse {
  items: GateOut[];
  total: number;
}

export default function EnvioPage() {
  const live = useLiveBatch();
  const gates = useQuery({
    queryKey: ["gates"],
    queryFn: () => api.get<GateListResponse>("/api/gates"),
  });

  const isLive = live.state === "sending";

  return (
    <div className="lg:grid lg:grid-cols-[300px_1fr_1fr] lg:gap-6">
      {/* Cockpit column — pinned on desktop, single column on mobile. */}
      <div className="flex flex-col gap-5 lg:sticky lg:top-6 lg:self-start">
        {isLive ? (
          <ProgressRing live={live} />
        ) : (
          <p className="py-4 text-center text-muted">
            Pega tus líneas y elige un gate.
          </p>
        )}

        {/* Controls slot — EMPTY until Story 2.3 (no placeholder buttons). */}

        {gates.isLoading && (
          <div className="flex justify-center py-6">
            <Spinner />
          </div>
        )}
        {gates.isError && (
          <Alert status="danger">
            No pudimos cargar el catálogo. Recarga la página.
          </Alert>
        )}
        {gates.data && <SendForm gates={gates.data.items} live={live} />}
      </div>

      {/* Data-panel area — EMPTY this story (Completa/Filtrada is 3.2). */}
      <div aria-hidden className="hidden lg:block" />
      <div aria-hidden className="hidden lg:block" />
    </div>
  );
}
