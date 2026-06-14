"use client";

// Pause/Resume/Stop controls (Story 2.3). Single-tap, server-confirmed
// (UX-DR12/UX-DR5): pressing fires REST and the surface changes ONLY when the
// resulting `batch.state` event lands — zero optimistic jumps. Detener acts
// instantly, no confirmation modal (AC 4 — confirm is reserved for Eliminar).
// Visible set follows the state machine verbatim: sending → Pausar+Detener ·
// paused → Reanudar+Detener · stopping → frozen, disabled · waiting → Detener
// only (Story 4.2) · idle → nothing.
import type { LiveBatchState } from "@/lib/ws";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { SectionCard } from "@/components/ui/section-card";
import { Btn } from "@/components/ui/btn";
import { Notice } from "@/components/ui/notice";

type ControlAction = "pause" | "resume" | "stop";

export function BatchControls({ live }: { live: LiveBatchState }) {
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (action: ControlAction) =>
      api.post<void>(`/api/batches/${live.batchId}/${action}`),
    onMutate: () => setError(null),
    // onSuccess deliberately does NOT touch the store: the resulting
    // `batch.state` event is the single source of truth (AC 1).
    onError: (err) => {
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  if (live.state === "idle" || live.batchId === null) return null;

  // Re-submit guard (2.1 lesson) + everything frozen while 'stopping'.
  const isDisabled = mutation.isPending || live.state === "stopping";

  return (
    <SectionCard
      className="flex flex-col gap-2.5"
      legend="Controles"
      rail={
        live.state === "sending"
          ? "accent"
          : live.state === "paused"
            ? "warning"
            : "none"
      }
    >
      <div className="flex gap-2.5">
        {live.state === "paused" ? (
          // Reanudar — the ONLY solid control (DESIGN.md control-button).
          <Btn
            full
            disabled={isDisabled}
            icon="play"
            variant="success"
            onClick={() => mutation.mutate("resume")}
          >
            Reanudar
          </Btn>
        ) : live.state === "waiting" ? null : ( // waiting: Detener only (4.2)
          <Btn
            full
            disabled={isDisabled}
            icon="pause"
            variant="warning"
            onClick={() => mutation.mutate("pause")}
          >
            Pausar
          </Btn>
        )}
        <Btn
          full
          disabled={isDisabled}
          icon="stop"
          variant="danger"
          onClick={() => mutation.mutate("stop")}
        >
          Detener
        </Btn>
      </div>
      {error && <Notice status="danger">{error}</Notice>}
    </SectionCard>
  );
}
