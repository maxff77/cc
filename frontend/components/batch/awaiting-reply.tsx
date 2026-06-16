"use client";

// "Esperando respuesta" badge: how many DELIVERED lines have no ✅/❌ reply
// yet (session-scoped). Read-only, fed by the WS store — the backend is the
// source of truth: the count climbs on each send (batch.progress) and drops as
// the bot answers (response.captured); a reconnect rebuilds it from the
// snapshot. NOT the Pendientes list (those are pending-to-SEND) — this is
// sent-and-waiting-for-REPLY. Shown whenever a capture session is active so the
// operator always knows how many replies are outstanding, including "0" once
// everything has come back.
import type { LiveBatchState } from "@/lib/ws";

import { LabelCaps } from "@/components/ui/label-caps";

export function AwaitingReply({ live }: { live: LiveBatchState }) {
  // No active session ⇒ nothing has been sent into a session yet: hide (no
  // "0 esperando respuesta" floating over an empty cockpit).
  if (live.sessionId === null) return null;

  return (
    <div
      className="flex items-center justify-between rounded border border-border bg-surface-secondary/50 px-3 py-2"
      role="status"
    >
      <LabelCaps>Esperando respuesta</LabelCaps>
      <span className="font-mono text-lg font-extrabold tabular-nums text-foreground">
        {live.awaitingReply}
      </span>
    </div>
  );
}
