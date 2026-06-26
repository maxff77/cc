"use client";

// Pause/Resume/Stop controls (Story 2.3). Single-tap, server-confirmed
// (UX-DR12/UX-DR5): pressing fires REST and the surface changes ONLY when the
// resulting `batch.state` event lands — zero optimistic jumps. Detener acts
// instantly, no confirmation modal (AC 4 — confirm is reserved for Eliminar).
// Visible set follows the state machine verbatim: sending → Pausar+Detener ·
// paused → Reanudar+Detener · stopping → frozen, disabled · waiting → Detener
// only (Story 4.2) · idle → nothing.
import type { CSSProperties } from "react";
import type { LiveBatchState } from "@/lib/ws";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { Notice } from "@/components/ui/notice";

type ControlAction = "pause" | "resume" | "stop";

const baseBtn: CSSProperties = {
  flex: 1,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  gap: "7px",
  height: "40px",
  borderRadius: "10px",
  border: "1px solid",
  cursor: "pointer",
  fontSize: "13px",
  fontWeight: 600,
  fontFamily: "'Saira',sans-serif",
};

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
  const disabledStyle: CSSProperties = isDisabled
    ? { opacity: 0.5, cursor: "not-allowed" }
    : {};

  const isPaused = live.state === "paused";
  // Pause/Resume tint: success-tinted while paused (▶ Reanudar), warning-tinted
  // while running (⏸ Pausar) — canvas pauseBtnStyle.
  const pauseStyle: CSSProperties = {
    ...baseBtn,
    background: isPaused
      ? "color-mix(in oklch, var(--success) 16%, transparent)"
      : "color-mix(in oklch, var(--warning) 16%, transparent)",
    borderColor: isPaused
      ? "color-mix(in oklch, var(--success) 40%, transparent)"
      : "color-mix(in oklch, var(--warning) 40%, transparent)",
    color: isPaused ? "var(--success)" : "var(--warning)",
    ...disabledStyle,
  };

  const stopStyle: CSSProperties = {
    ...baseBtn,
    background: "var(--surface-secondary)",
    borderColor: "var(--border)",
    color: "var(--muted)",
    ...disabledStyle,
  };

  return (
    <div className="flex flex-col gap-2.5">
      <div style={{ display: "flex", gap: "8px" }}>
        {/* waiting (4.2): Detener only — no pause/resume button. */}
        {live.state !== "waiting" && (
          <button
            disabled={isDisabled}
            style={pauseStyle}
            type="button"
            onClick={() => mutation.mutate(isPaused ? "resume" : "pause")}
          >
            {isPaused ? "▶ Reanudar" : "⏸ Pausar"}
          </button>
        )}
        <button
          disabled={isDisabled}
          style={stopStyle}
          type="button"
          onClick={() => mutation.mutate("stop")}
        >
          <svg fill="currentColor" height="14" viewBox="0 0 24 24" width="14">
            <rect height="12" rx="2" width="12" x="6" y="6" />
          </svg>
          Detener
        </button>
      </div>
      {error && <Notice status="danger">{error}</Notice>}
    </div>
  );
}
