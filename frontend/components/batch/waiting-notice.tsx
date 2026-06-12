"use client";

// Admission-queue notice (Story 4.2, AC 2): the WAITING surface — shown in
// place of the progress ring while the batch queues for a send slot. Amber
// INFORMATIONAL, never red (flood-notice idiom: "esperando, no roto"). The
// position IS the metric of the wait — no fake-precise "starts in X min"
// math (UX-DR14); it updates live via `batch.state` events and the snapshot.
import type { LiveBatchState } from "@/lib/ws";

export function WaitingNotice({ live }: { live: LiveBatchState }) {
  if (live.state !== "waiting") return null;

  return (
    <section
      className="flex flex-col items-center gap-2 rounded-md border border-warning/50 bg-warning/12 px-4 py-6 text-center"
      role="status"
    >
      <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted">
        En cola de espera
      </span>
      <span className="font-mono text-[26px] font-extrabold leading-none text-warning tabular-nums">
        {live.queuePosition !== null ? `#${live.queuePosition}` : "—"}
      </span>
      <p className="text-xs text-muted">
        Tu lote empezará solo cuando se libere un lugar.
      </p>
    </section>
  );
}
