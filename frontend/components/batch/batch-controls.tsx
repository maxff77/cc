"use client";

// Pause/Resume/Stop controls (Story 2.3). Single-tap, server-confirmed
// (UX-DR12/UX-DR5): pressing fires REST and the surface changes ONLY when
// the resulting `batch.state` event lands — zero optimistic jumps. Detener
// acts instantly, no confirmation modal (AC 4 — confirm is reserved for
// Eliminar, Epic 3). Visible set follows the state machine verbatim:
// sending → Pausar+Detener · paused → Reanudar+Detener · stopping → the
// frozen pair, disabled · waiting → Detener only (Story 4.2: nothing to
// pause yet — Detener leaves the admission queue; mirrors the backend's
// 409 batch_waiting) · idle → nothing.
import type { LiveBatchState } from "@/lib/ws";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button } from "@heroui/react";

import { api, ApiError } from "@/lib/api";
import { SectionCard } from "@/components/ui/section-card";

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
      // 409/404 carry the server's Spanish message; the next
      // batch.state/snapshot reconciles the surface on its own.
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
    // Rack instrument (ui-polish-spec §4.1/§4.4): SectionCard with a live
    // state rail; controls use HeroUI variants + text-color only — the
    // solid success fill on Reanudar is the system's ONE recorded exception.
    <SectionCard
      className="flex flex-col gap-2"
      legend="CONTROLES"
      rail={
        live.state === "sending"
          ? "accent"
          : live.state === "paused"
            ? "warning"
            : "none"
      }
    >
      <div className="flex gap-3">
        {live.state === "paused" ? (
          // Reanudar — the ONLY solid control (DESIGN.md control-button).
          <Button
            className="flex-1 bg-success text-success-foreground"
            isDisabled={isDisabled}
            variant="primary"
            onPress={() => mutation.mutate("resume")}
          >
            Reanudar
          </Button>
        ) : live.state === "waiting" ? null : ( // waiting: Detener only (4.2)
          <Button
            className="flex-1 text-warning"
            isDisabled={isDisabled}
            variant="secondary"
            onPress={() => mutation.mutate("pause")}
          >
            Pausar
          </Button>
        )}
        <Button
          className="flex-1 text-danger"
          isDisabled={isDisabled}
          variant="secondary"
          onPress={() => mutation.mutate("stop")}
        >
          Detener
        </Button>
      </div>
      {error && <Alert status="danger">{error}</Alert>}
    </SectionCard>
  );
}
