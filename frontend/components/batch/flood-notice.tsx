"use client";

// FloodWait notice (Story 2.3, AC 6): amber INFORMATIONAL strip — "paused
// and waiting, not broken". NEVER styled as an error (DESIGN.md anti-pattern:
// red is for destructive/failed only). The live countdown is the explicit
// exception to "no precise countdowns" (UX-DR14): an imposed wait with a
// known duration. Self-dismisses at 0 or when the store clears `floodUntil`
// (progress / line_sent / batch.state sending — see lib/ws.ts).
import { useEffect, useState } from "react";

import { useLiveBatch } from "@/lib/ws";

export function FloodNotice() {
  const live = useLiveBatch();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (live.floodUntil === null) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);

    return () => window.clearInterval(id);
  }, [live.floodUntil]);

  if (live.floodUntil === null) return null;
  const seconds = Math.ceil((live.floodUntil - now) / 1000);

  if (seconds <= 0) return null;

  return (
    <div
      className="rounded-md border border-warning/50 bg-warning/12 px-3 py-2 text-xs"
      role="status"
    >
      Telegram pidió esperar{" "}
      <span className="font-mono font-semibold text-warning tabular-nums">
        {seconds}
      </span>{" "}
      s — reanudamos solos.
    </div>
  );
}
