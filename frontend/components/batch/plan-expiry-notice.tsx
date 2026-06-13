"use client";

// Plan-expiry PRE-warning (P2): amber INFORMATIONAL strip — "tu plan está por
// vencer", same idiom as WaitingNotice/FloodNotice ("esperando, no roto", never
// red). NON-BLOCKING by design: today expiry is enforced only at the edge
// (middleware → full-page /expired), so a client gets yanked mid-batch with no
// warning. This strip gives the days-before heads-up so renewal isn't a
// surprise. Self-hides outside the warning window (> THRESHOLD days, or already
// expired — the middleware lockout owns that case).
//
// TODO(backend): /api/auth/me (MeResponse) does NOT yet expose the client's
// plan expiry. The admin UserOut carries `expires_at`, but the client never
// learns its own. Surface `expires_at` (ISO string | null) on MeResponse and
// pass it here as `expiresAt`. Until then this renders nothing (dormant): with
// no prop the component is a no-op, so it ships safely ahead of the backend.
import { LabelCaps } from "@/components/ui/label-caps";

// Show the strip only inside this many days of expiry (inclusive). Small
// window so it reads as "act soon", not constant nagging.
const WARN_WITHIN_DAYS = 5;

const MS_PER_DAY = 24 * 60 * 60 * 1000;

// Whole days from now until `iso`, rounded UP: an expiry 30 hours out is "2
// días" (the client still has parts of two calendar days of use), and the last
// partial day before expiry reads "1 día", never "0".
function daysUntil(iso: string): number {
  return Math.ceil((new Date(iso).getTime() - Date.now()) / MS_PER_DAY);
}

export function PlanExpiryNotice({
  expiresAt,
}: {
  // ISO datetime | null — null = no plan expiry (staff) or field absent.
  expiresAt?: string | null;
}) {
  if (!expiresAt) return null;

  const days = daysUntil(expiresAt);

  // Already expired (≤ 0) → middleware's /expired lockout owns it; nothing to
  // pre-warn. Outside the window → silent.
  if (days <= 0 || days > WARN_WITHIN_DAYS) return null;

  return (
    <div
      className="rounded border border-warning/50 bg-warning/12 px-3 py-2 text-xs"
      role="status"
    >
      <LabelCaps className="leading-4">Tu plan está por vencer</LabelCaps>
      <p className="mt-1 text-muted">
        Vence en{" "}
        <span className="font-mono font-semibold text-warning tabular-nums">
          {days}
        </span>{" "}
        {days === 1 ? "día" : "días"} — renueva para no interrumpir tus envíos.
      </p>
    </div>
  );
}
