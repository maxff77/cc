"use client";

// Pendientes panel: the lines still waiting to be sent, draining one-by-one as
// each goes out (instead of the textarea clearing all at once on submit). Fed
// by the WS store — backend is the source of truth: `pending` rebuilds from the
// snapshot on reconnect (survives closing the page), grows on
// `batch.lines_queued` and shrinks on `batch.line_sent` / `batch.line_failed`.
// Neutral/informational (not danger like Fallidas): waiting is the happy path.
// The count uses `queued` (authoritative) — the list itself may be capped.
import type { LiveBatchState } from "@/lib/ws";

export function PendingLines({ live }: { live: LiveBatchState }) {
  // Driven by `queued` (authoritative), NOT the list length: a paste beyond the
  // snapshot cap drains its visible window before the rest of the queue, so the
  // list can empty while lines are still pending — the panel must NOT vanish
  // with work outstanding. It hides only when nothing is queued.
  const count = live.queued;

  if (count === 0) return null;

  // Lines queued beyond what the (capped) list shows — incl. the case where
  // the visible window already drained to empty.
  const overflow = count - live.pending.length;

  return (
    <div
      className="rounded border border-border bg-surface-secondary/50 px-3 py-2 text-xs"
      role="status"
    >
      <p className="font-semibold text-muted">
        {count === 1 ? "1 línea pendiente" : `${count} líneas pendientes`}
      </p>
      {/* Capped height: a big paste scrolls inside the strip instead of
          stretching the cockpit (mirror of Fallidas). Top row = next to send
          (position order). */}
      <ul className="mt-1 flex max-h-40 flex-col gap-1 overflow-y-auto">
        {live.pending.map((line) => (
          <li
            key={line.position}
            className="truncate font-mono text-[11px] leading-[1.4] text-muted"
          >
            {line.text}
          </li>
        ))}
      </ul>
      {overflow > 0 && (
        <p className="mt-1 text-[11px] text-muted">y {overflow} más en cola…</p>
      )}
    </div>
  );
}
