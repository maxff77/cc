"use client";

// Admission-queue notice (Story 4.2, AC 2): the WAITING surface — shown in
// place of the progress ring while the batch queues for a send slot. Amber
// INFORMATIONAL, never red (flood-notice idiom: "esperando, no roto"). The
// position IS the metric of the wait — no fake-precise "starts in X min"
// math (UX-DR14); it updates live via `batch.state` events and the snapshot.
import type { LiveBatchState } from "@/lib/ws";

import { LabelCaps } from "@/components/ui/label-caps";

export function WaitingNotice({ live }: { live: LiveBatchState }) {
  if (live.state !== "waiting") return null;

  return (
    <section
      className="flex flex-col items-center gap-2 rounded border border-warning/50 bg-warning/12 px-4 py-6 text-center"
      role="status"
    >
      <LabelCaps>En cola de espera</LabelCaps>
      <span className="font-mono text-[26px] font-extrabold leading-none tracking-[-0.03em] text-warning tabular-nums">
        {live.queuePosition !== null ? `#${live.queuePosition}` : "—"}
      </span>
      <p className="text-xs text-muted">
        Tu lote empezará solo cuando se libere un lugar.
      </p>
    </section>
  );
}
