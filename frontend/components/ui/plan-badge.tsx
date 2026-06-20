"use client";

// Plan-status badge (always-visible in the client header). Shows days remaining
// plus a status tone: success = active, warning = expiring soon (≤ 3 days) or
// last day, danger = expired. Pure presentational — display only, never gates
// anything (lockout stays owned by middleware + is_plan_expired). Renders only
// for clients: owner/admin carry expires_at = null and get nothing.
//
// Day-scale math on the local clock, mirroring the app-clock exception in
// services/plans.py — seconds of skew cannot move a day-grained deadline.
import { StatePill, type PillTone } from "@/components/ui/state-pill";

const DAY_MS = 86_400_000;
const SOON_DAYS = 3;

function describe(expiresAt: string): { label: string; tone: PillTone } {
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return { label: "Vencido", tone: "danger" };
  // Under 24h left is the "last day" — ceil() would read this as "1 día", so
  // catch it before the day math (I/O matrix "Last day → Vence hoy").
  if (ms < DAY_MS) return { label: "Vence hoy", tone: "warning" };
  const days = Math.ceil(ms / DAY_MS);
  const label = days === 1 ? "1 día" : `${days} días`;
  return { label, tone: days <= SOON_DAYS ? "warning" : "success" };
}

export function PlanBadge({ expiresAt }: { expiresAt: string | null }) {
  // No badge while /me is loading or for staff (null) — avoids layout shift.
  if (!expiresAt) return null;
  const { label, tone } = describe(expiresAt);
  const aria = tone === "danger" ? "Plan vencido" : `Plan: quedan ${label}`;
  // role="img" + aria-label so screen readers announce the full "Plan: …"
  // phrase instead of the bare "12 días"; StatePill stays reused as-is.
  return (
    <span aria-label={aria} className="inline-flex" role="img">
      <StatePill dot="static" tone={tone}>
        {label}
      </StatePill>
    </span>
  );
}
