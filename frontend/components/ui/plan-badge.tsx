"use client";

// Plan-status badge (always-visible in the client header). Shows days remaining
// plus a status tone: success = active, warning = expiring soon (≤ 3 days) or
// last day, danger = expired. Pure presentational — display only, never gates
// anything (lockout stays owned by middleware + is_plan_expired). Renders only
// for clients: owner/admin carry expires_at = null and get nothing.
//
// Day-scale math on the local clock, mirroring the app-clock exception in
// services/plans.py — seconds of skew cannot move a day-grained deadline.
type PlanTone = "success" | "warning" | "danger";

const DAY_MS = 86_400_000;
const SOON_DAYS = 3;

// Dot color tracks urgency (success healthy → warning soon → danger expired).
const DOT_COLOR: Record<PlanTone, string> = {
  success: "var(--success)",
  warning: "var(--warning)",
  danger: "var(--danger)",
};

function describe(expiresAt: string): {
  days: number | null;
  label: string;
  tone: PlanTone;
} {
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return { days: null, label: "Vencido", tone: "danger" };
  // Under 24h left is the "last day" — ceil() would read this as "1 día", so
  // catch it before the day math (I/O matrix "Last day → Vence hoy").
  if (ms < DAY_MS) return { days: null, label: "Vence hoy", tone: "warning" };
  const days = Math.ceil(ms / DAY_MS);
  const label = days === 1 ? "1 día" : `${days} días`;
  return { days, label, tone: days <= SOON_DAYS ? "warning" : "success" };
}

export function PlanBadge({ expiresAt }: { expiresAt: string | null }) {
  // No badge while /me is loading or for staff (null) — avoids layout shift.
  if (!expiresAt) return null;
  const { days, label, tone } = describe(expiresAt);
  const aria = tone === "danger" ? "Plan vencido" : `Plan: quedan ${label}`;
  // Canvas plan pill: 34px surface-secondary plate, leading 7px tone dot, mono
  // bold days + " días" (or the plain text label for the no-number states).
  return (
    <span
      aria-label={aria}
      className="inline-flex h-[34px] shrink-0 items-center gap-[7px] rounded-[9px] border border-border bg-surface-secondary px-3 text-[12.5px] text-muted"
      role="img"
    >
      <span
        aria-hidden
        className="size-[7px] rounded-full"
        style={{ background: DOT_COLOR[tone] }}
      />
      {days !== null ? (
        <>
          <span className="font-mono font-semibold text-foreground">
            {days}
          </span>{" "}
          {days === 1 ? "día" : "días"}
        </>
      ) : (
        <span className="font-mono font-semibold text-foreground">{label}</span>
      )}
    </span>
  );
}
