"use client";

// Watchdog global-pause banner (Story 4.1): DANGER strip — unlike FloodWait
// (amber, "waiting, not broken") this IS a failure: the watchdog latched a
// system-wide send pause (bot stopped replying, or the Telegram session
// died). DESIGN.md: "Danger red — destructive or failed". Everyone sees the
// banner (the pause affects every tenant — no silent stall); ONLY the owner
// gets the "Reanudar envíos" action (AC 3: resuming is an explicit owner
// action, never automatic). The `watchdog.resumed` event clears the banner
// in every tab — no optimistic clear here (UX-DR12).
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@heroui/react";

import { api, ApiError } from "@/lib/api";
import { useLiveBatch } from "@/lib/ws";

// Spanish copy by machine reason (backend core/watchdog.py constants).
const REASON_COPY: Record<string, string> = {
  reply_rate_collapse:
    "El bot dejó de responder y pausamos todos los envíos para proteger la cuenta.",
  session_lost:
    "Se perdió la sesión de Telegram y pausamos todos los envíos para proteger la cuenta.",
};

const FALLBACK_COPY = "Los envíos están pausados por protección de la cuenta.";

interface Me {
  role: string;
}

export function WatchdogNotice() {
  const live = useLiveBatch();
  // Role lookup only while the banner is up (enabled) — idle tabs never pay
  // the /me round-trip for a banner that isn't rendered.
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<Me>("/api/auth/me"),
    enabled: live.watchdog.paused,
  });
  const resume = useMutation({
    mutationFn: () => api.post<void>("/api/watchdog/resume"),
    // onSuccess deliberately does NOT touch the store: the resulting
    // `watchdog.resumed` event is the single source of truth.
  });

  if (!live.watchdog.paused) return null;

  const copy = REASON_COPY[live.watchdog.reason ?? ""] ?? FALLBACK_COPY;
  const isOwner = me.data?.role === "owner";

  return (
    <div
      className="rounded border border-danger/50 bg-danger/10 px-3 py-2 text-xs"
      role="alert"
    >
      <p className="font-semibold text-danger">{copy}</p>
      {isOwner ? (
        <div className="mt-2 flex flex-col gap-1">
          <Button
            className="text-danger"
            isDisabled={resume.isPending}
            size="sm"
            variant="secondary"
            onPress={() => resume.mutate()}
          >
            Reanudar envíos
          </Button>
          {resume.isError && (
            <span className="text-danger">
              {resume.error instanceof ApiError
                ? resume.error.message
                : "No pudimos reanudar. Intenta de nuevo."}
            </span>
          )}
        </div>
      ) : (
        <p className="mt-1 text-muted">
          Solo el owner puede reanudar los envíos.
        </p>
      )}
    </div>
  );
}
