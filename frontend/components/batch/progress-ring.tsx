"use client";

// Progress ring + flank metrics (UX-DR3 / Ranger-X handoff `ProgressRing`),
// re-housed in the "Cliente Redesign" STATUS CARD (Cliente Redesign.dc.html):
// a bordered surface plate that holds the ring + the stat row. Space-optimized
// like the canvas — HORIZONTAL on phone/tablet (ring left · stats grid right)
// and VERTICAL in the lg cockpit column (ring on top · stats row centered). The
// METRICS stay ours (real WS data, UX-DR21): enviadas·en-cola, honest ETA, CC
// nuevas — only the layout adopts the canvas. SVG ring keeps the
// cyan→accent→magenta gradient stroke + a neon glow reserved for the SENDING arc
// (scales with --glow; paused/stopping is calm warning).
import type { LiveBatchState } from "@/lib/ws";

import { useId } from "react";
import clsx from "clsx";

import { formatEta } from "@/components/batch/metric";

const SIZE = 128;
const R = 54;
const C = 2 * Math.PI * R;
const CENTER = SIZE / 2;

// Shared status-card chrome (canvas `statusCardStyle`): flat surface plate.
const CARD = "rounded-[var(--radius)] border border-border bg-surface";

// One flank stat (canvas metric chip): mono value over a caps label. Left-
// aligned beside the ring on phone/tablet, centered under it in the lg column.
function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "success";
}) {
  return (
    <div className="flex flex-col items-start gap-1 lg:items-center">
      <span
        className={clsx(
          "font-mono text-lg font-extrabold leading-none tabular-nums",
          tone === "success" ? "text-success" : "text-foreground",
        )}
      >
        {value}
      </span>
      <span className="text-[10px] font-semibold uppercase leading-tight tracking-[0.1em] text-muted lg:text-center">
        {label}
      </span>
    </div>
  );
}

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
          cx={CENTER}
          cy={CENTER}
          fill="none"
          r={R}
          stroke="var(--surface-tertiary)"
          strokeWidth="9"
        />
        {!idle && (
          <circle
            cx={CENTER}
            cy={CENTER}
            fill="none"
            r={R}
            stroke={stroke}
            strokeDasharray={C}
            strokeDashoffset={offset}
            strokeLinecap="round"
            strokeWidth="9"
            style={{
              transition: "stroke-dashoffset .6s cubic-bezier(.2,.7,.2,1)",
              // Glow is the live-send signal: only the accent (sending) arc
              // carries it. Paused/stopping (warning) reads calm — no neon.
              filter:
                tone === "warning"
                  ? "none"
                  : "drop-shadow(0 0 calc(7px * var(--glow)) var(--accent))",
            }}
          />
        )}
      </svg>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <span
          className={clsx(
            "font-mono text-[26px] font-extrabold leading-none tracking-[-0.03em] tabular-nums",
            idle ? "text-muted" : "text-foreground",
          )}
        >
          {idle ? "—" : `${percent}%`}
        </span>
        {!idle && total !== undefined && (
          <span className="mt-1 font-mono text-[11px] text-muted tabular-nums">
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
    <section
      className={clsx(
        CARD,
        "flex items-center gap-4 p-4 lg:flex-col lg:gap-4 lg:p-5",
      )}
    >
      <Ring
        percent={percent}
        sent={live.sent}
        tone={live.state === "sending" ? "accent" : "warning"}
        total={live.total}
      />
      {/* Four live stats in a 2×2 grid (Cliente Redesign): enviadas·cola, ETA,
          CC nuevas + "Esperando respuesta" (delivered lines without a ✅/❌
          reply yet) — the latter folded in here so it no longer needs its own
          standalone box in the cockpit column. */}
      <div className="grid flex-1 grid-cols-2 gap-x-4 gap-y-3 lg:w-full lg:gap-y-3.5">
        <Stat
          label="Enviadas · En cola"
          value={`${live.sent} · ${live.queued}`}
        />
        <Stat
          label={live.state === "paused" ? "ETA al reanudar" : "ETA"}
          value={formatEta(live.etaSeconds, live.queued)}
        />
        <Stat label="CC nuevas" tone="success" value={String(live.ccNew)} />
        <Stat
          label="Esperando respuesta"
          value={String(live.awaitingReply)}
        />
      </div>
    </section>
  );
}

// Completion moment (P2): the ONE sanctioned success-pulse — shown for a few
// seconds on the active→idle transition before the ring reverts to the idle
// em-dash. Control-room calm: a single gradient ring + the run totals, in the
// same status-card plate. The gradient lives on the RING (a clipped shape,
// never on letters); prefers-reduced-motion drops the pulse (motion-safe).
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
    <section className={clsx(CARD, "flex flex-col items-center gap-3 p-5")}>
      <div className="p-[3px]" style={{ width: SIZE, height: SIZE }}>
        <div className="gradient-moment size-full rounded-full motion-safe:animate-pulse">
          <div className="flex size-full items-center justify-center rounded-full bg-surface">
            <span
              aria-hidden
              className="text-[26px] font-extrabold leading-none tracking-[-0.03em] text-success"
            >
              ✓
            </span>
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
// track + a mono em-dash center — same status-card footprint as the live ring,
// zero layout jump when the lote starts. The invitation sentence sits below.
export function IdleRing() {
  return (
    <section className={clsx(CARD, "flex flex-col items-center gap-3 p-5")}>
      <Ring idle percent={0} />
      <p className="text-center text-sm text-muted">
        Pega tus líneas y elige un gateway.
      </p>
    </section>
  );
}
