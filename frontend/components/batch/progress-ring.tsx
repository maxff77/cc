"use client";

// Progress ring + flank metrics (UX-DR3 / Ranger-X handoff `ProgressRing`):
// a native SVG ring with a cyan→accent→magenta gradient stroke + neon glow
// (the glow scales with --glow), accent while sending / solid warning while
// paused or stopping (AC 2 — "vivo pero no enviando" wears warning; 'stopping'
// has no DESIGN token, recorded decision). Center % + fraction; flank shows
// EXACTLY three metrics — enviadas · en cola / ETA / CC nuevas (UX-DR21).
import type { LiveBatchState } from "@/lib/ws";

import { useId } from "react";
import clsx from "clsx";

import { Metric, formatEta } from "@/components/batch/metric";

const SIZE = 144;
const R = 58;
const C = 2 * Math.PI * R;

// The bare ring SVG, shared by every ring state. `idle` paints only the muted
// track + an em-dash; otherwise the gradient (or warning) arc fills to percent.
function Ring({
  percent,
  sent,
  total,
  idle,
  tone = "accent",
}: {
  percent: number;
  sent?: number;
  total?: number;
  idle?: boolean;
  tone?: "accent" | "warning";
}) {
  const gid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const offset = C - (percent / 100) * C;
  const stroke =
    tone === "warning" ? "var(--warning)" : `url(#ring-grad-${gid})`;

  return (
    <div className="relative shrink-0" style={{ width: SIZE, height: SIZE }}>
      <svg
        height={SIZE}
        style={{ transform: "rotate(-90deg)" }}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        width={SIZE}
      >
        <defs>
          <linearGradient id={`ring-grad-${gid}`} x1="0" x2="1" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--cyan)" />
            <stop offset="55%" stopColor="var(--accent)" />
            <stop offset="100%" stopColor="var(--magenta)" />
          </linearGradient>
        </defs>
        <circle
          cx="72"
          cy="72"
          fill="none"
          r={R}
          stroke="var(--surface-tertiary)"
          strokeWidth="9"
        />
        {!idle && (
          <circle
            cx="72"
            cy="72"
            fill="none"
            r={R}
            stroke={stroke}
            strokeDasharray={C}
            strokeDashoffset={offset}
            strokeLinecap="round"
            strokeWidth="9"
            style={{
              transition: "stroke-dashoffset .6s cubic-bezier(.2,.7,.2,1)",
              filter: "drop-shadow(0 0 calc(7px * var(--glow)) var(--accent))",
            }}
          />
        )}
      </svg>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <span
          className={clsx(
            "font-mono text-[28px] font-extrabold leading-none tracking-[-0.03em] tabular-nums",
            idle ? "text-muted" : "text-foreground",
          )}
        >
          {idle ? "—" : `${percent}%`}
        </span>
        {!idle && total !== undefined && (
          <span className="mt-1.5 font-mono text-xs text-muted tabular-nums">
            {sent} / {total}
          </span>
        )}
      </div>
    </div>
  );
}

export function ProgressRing({ live }: { live: LiveBatchState }) {
  const percent =
    live.total > 0 ? Math.round((live.sent / live.total) * 100) : 0;

  return (
    <section className="flex items-center justify-between gap-4 py-2">
      <Ring
        percent={percent}
        sent={live.sent}
        tone={live.state === "sending" ? "accent" : "warning"}
        total={live.total}
      />
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
// em-dash. Control-room calm: a single gradient ring + the run totals. The
// gradient lives on the RING (a clipped shape, never on letters);
// prefers-reduced-motion drops the pulse (motion-safe).
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
    <section className="flex flex-col items-center gap-3 py-2">
      <div className="relative">
        <div className="p-[3px]" style={{ width: SIZE, height: SIZE }}>
          <div className="gradient-moment size-full rounded-full motion-safe:animate-pulse">
            <div className="flex size-full items-center justify-center rounded-full bg-surface">
              <span
                aria-hidden
                className="text-[28px] font-extrabold leading-none tracking-[-0.03em] text-success"
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

// Idle placeholder (ui-polish-spec §4.2): the ring renders at 0 with the muted
// track + a mono em-dash center — same footprint as the live ring, zero layout
// jump when the lote starts. The invitation sentence sits below.
export function IdleRing() {
  return (
    <section className="flex flex-col items-center gap-3 py-2">
      <Ring idle percent={0} />
      <p className="text-center text-sm text-muted">
        Pega tus líneas y elige un gate.
      </p>
    </section>
  );
}
