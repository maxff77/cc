"use client";

// Verdict-timeout prompt (amazon-gate-send-rotation Phase 2, patch #6): DANGER
// strip — a cookie-mode line's `.amz` got no `⌿ Status:` verdict in time, the
// worker retried once with a fresh cookie, and still nothing came back, so it
// latched the batch into a `verdict_timeout` pause (a real stall, not a
// "waiting" — mirrors CookiesExhaustedNotice / WatchdogNotice danger styling,
// DESIGN.md "Danger red — destructive or failed"). Unlike the exhausted notice
// there is NO CookieManager: the cookies may be fine, the gate just went quiet —
// the client only needs to retry. It renders ONLY while the surface is `paused`
// with `pauseReason === "verdict_timeout"` (gated by the caller in
// app/app/page.tsx). Reanudar fires the existing batch resume endpoint and the
// resulting `batch.state` event is the single source of truth (UX-DR12 — no
// optimistic clear).
import { useMutation } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { useLiveBatch } from "@/lib/ws";
import { Btn } from "@/components/ui/btn";
import { Notice } from "@/components/ui/notice";

export function VerdictTimeoutNotice() {
  const live = useLiveBatch();

  // Resume the SAME batch: the server re-queues the failed line and continues.
  // onSuccess deliberately does NOT touch the store — the resulting
  // `batch.state` event (which clears `pauseReason`) is authoritative.
  const resume = useMutation({
    mutationFn: () => api.post<void>(`/api/batches/${live.batchId}/resume`),
  });

  return (
    <div
      className="flex flex-col gap-3 rounded border border-danger/50 bg-danger/10 px-3 py-2.5"
      role="alert"
    >
      <p className="text-xs font-semibold text-danger">
        El gateway no respondió a tiempo. Puedes reanudar.
      </p>

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
