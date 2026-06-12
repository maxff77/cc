"use client";

// Failed-lines panel (Story 2.5, AC 4): compact inline DANGER strip listing
// the lines the retry cap gave up on, each with a Spanish notice mapped from
// its machine `code`. Red is correct here — DESIGN.md: "Danger red —
// destructive or failed" (unlike FloodWait, which stays amber/informational).
// The batch keeps going: this panel is informative, never blocking. It lives
// with the live batch only — a reconnect rebuilds it from the snapshot and
// the idle reset clears it (post-batch persistence is Epic 3's history).
import type { LiveBatchState } from "@/lib/ws";

// Spanish copy by machine code (snake_case of the backend exception class).
// Extensible without touching the backend; anything unknown uses the fallback.
const FAIL_COPY: Record<string, string> = {
  rpc_error: "Telegram rechazó esta línea (3 intentos).",
};

const FALLBACK_COPY = "No se pudo enviar esta línea (3 intentos).";

export function FailedLines({ live }: { live: LiveBatchState }) {
  if (live.failedLines.length === 0) return null;

  const count = live.failedLines.length;

  return (
    <div
      className="rounded-md border border-danger/50 bg-danger/10 px-3 py-2 text-xs"
      role="status"
    >
      <p className="font-semibold text-danger">
        {count === 1 ? "1 línea falló" : `${count} líneas fallaron`} — el lote
        continúa.
      </p>
      <ul className="mt-1 flex flex-col gap-1">
        {live.failedLines.map((line) => (
          <li key={line.position}>
            <span className="font-mono">{line.text}</span>{" "}
            <span className="text-danger">
              {FAIL_COPY[line.code] ?? FALLBACK_COPY}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
