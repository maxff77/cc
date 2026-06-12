"use client";

// Progress ring + flank metrics (UX-DR3): HeroUI ProgressCircle ~128px,
// accent stroke while sending / warning while paused or stopping (AC 2 —
// "vivo pero no enviando" wears warning; 'stopping' has no DESIGN.md token,
// recorded decision), center % + fraction; flank shows EXACTLY three
// metrics — enviadas · en cola / ETA / CC nuevas. No other stats (UX-DR21).
// While paused the ETA LABEL becomes "ETA al reanudar"; the VALUE keeps the
// last honest estimate (UX-DR14 — never a fake-precise countdown).
import type { LiveBatchState } from "@/lib/ws";

import { ProgressCircle } from "@heroui/react";

import { Metric, formatEta } from "@/components/batch/metric";

export function ProgressRing({ live }: { live: LiveBatchState }) {
  const percent =
    live.total > 0 ? Math.round((live.sent / live.total) * 100) : 0;

  return (
    <section className="flex items-center justify-center gap-8 py-4">
      <div className="relative">
        <ProgressCircle
          aria-label="Progreso del lote"
          color={live.state === "sending" ? "accent" : "warning"}
          value={percent}
        >
          <ProgressCircle.Track className="size-32">
            <ProgressCircle.TrackCircle />
            <ProgressCircle.FillCircle />
          </ProgressCircle.Track>
        </ProgressCircle>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-mono text-[26px] font-extrabold leading-none tabular-nums">
            {percent}%
          </span>
          <span className="mt-1 font-mono text-xs text-muted tabular-nums">
            {live.sent} / {live.total}
          </span>
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <Metric
          label="Enviadas · En cola"
          value={`${live.sent} · ${live.queued}`}
        />
        <Metric
          label={live.state === "paused" ? "ETA al reanudar" : "ETA"}
          value={formatEta(live.etaSeconds, live.queued)}
        />
        <Metric label="CC nuevas" tone="success" value={String(live.ccNew)} />
      </div>
    </section>
  );
}
