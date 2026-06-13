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
    // justify-between gap-4 (ui-polish-spec §4.3): justify-center gap-8
    // overflowed the 300px cockpit column.
    <section className="flex items-center justify-between gap-4 py-4">
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
          <span className="font-mono text-[26px] font-extrabold leading-none tracking-[-0.03em] tabular-nums">
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

// Completion moment (P2): the ONE sanctioned success-pulse — shown for a few
// seconds on the active→idle transition before the ring reverts to the idle
// em-dash, so a successful finish has a peak-end payoff instead of a silent
// return to "—". Control-room calm: a single gradient ring + the run totals,
// no confetti. The gradient is the brand .gradient-moment (a clipped ring, not
// a fill on letters); prefers-reduced-motion drops the pulse (motion-safe).
// Same footprint as the live/idle ring → zero layout jump when it appears or
// reverts. Totals are snapshotted by the caller at the transition (the store
// resets `sent`/`total` to 0 the instant it goes idle).
export interface RunSummary {
  sent: number;
  ccCaptured: number;
  durationSeconds: number | null;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const rem = Math.round(seconds % 60);

  return rem > 0 ? `${mins} min ${rem}s` : `${mins} min`;
}

export function CompletionRing({ summary }: { summary: RunSummary }) {
  return (
    <section className="flex flex-col items-center gap-3 py-4">
      <div className="relative">
        {/* The gradient ring is the success pulse: a .gradient-moment disc
            masked to a ring by an inset surface circle — the brand fill clipped
            to a shape (never background-clip on text). motion-safe gates the
            pulse so reduced-motion gets a calm STATIC gradient ring; the
            appear/revert is an instant conditional swap (no animation), the
            sanctioned reduced-motion fallback. */}
        <div className="size-32 rounded-full p-[3px]">
          <div className="gradient-moment size-full rounded-full motion-safe:animate-pulse">
            <div className="flex size-full items-center justify-center rounded-full bg-surface">
              {/* Solid success glyph — the gradient lives on the RING (a fill on
                  a shape), never clipped to the letters (hard ban). */}
              <span
                aria-hidden
                className="text-[26px] font-extrabold leading-none tracking-[-0.03em] text-success"
              >
                ✓
              </span>
            </div>
          </div>
        </div>
      </div>
      <div className="flex flex-col items-center gap-1.5">
        <p className="text-center text-sm font-semibold" role="status">
          Lote completo
        </p>
        <p className="text-center font-mono text-xs text-muted tabular-nums">
          {summary.sent} enviadas · {summary.ccCaptured} CC
          {summary.durationSeconds !== null
            ? ` · ${formatDuration(summary.durationSeconds)}`
            : ""}
        </p>
      </div>
    </section>
  );
}

// Idle placeholder (ui-polish-spec §4.2): the ring renders at 0 with the
// default muted track and a mono em-dash center — same footprint as the live
// ring, zero layout jump when the lote starts. The invitation sentence
// (verbatim copy) sits below.
export function IdleRing() {
  return (
    <section className="flex flex-col items-center gap-3 py-4">
      <div className="relative">
        <ProgressCircle aria-label="Sin lote activo" value={0}>
          <ProgressCircle.Track className="size-32">
            <ProgressCircle.TrackCircle />
            <ProgressCircle.FillCircle />
          </ProgressCircle.Track>
        </ProgressCircle>
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <span className="font-mono text-[26px] font-extrabold leading-none tracking-[-0.03em] text-muted tabular-nums">
            —
          </span>
        </div>
      </div>
      <p className="text-center text-sm text-muted">
        Pega tus líneas y elige un gate.
      </p>
    </section>
  );
}
