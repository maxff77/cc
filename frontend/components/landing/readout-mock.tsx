// Product imagery for the hero — a stylized, NON-interactive mock of the live
// cockpit readout (sending state + ✅/❌ rows). It's a tool brand, so the
// product itself is the hero image; this is decorative (aria-hidden) and uses
// only abstract placeholder tokens, never real captured data.
import clsx from "clsx";

const ROWS: { token: string; ok: boolean }[] = [
  { token: "TX-0461", ok: true },
  { token: "TX-0462", ok: true },
  { token: "TX-0463", ok: false },
  { token: "TX-0464", ok: true },
  { token: "TX-0465", ok: false },
];

export function ReadoutMock({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={clsx(
        "glow-accent w-full max-w-[440px] rounded-[18px] border border-border bg-surface p-5",
        className,
      )}
      style={{ backgroundImage: "var(--brand-gradient-soft)" }}
    >
      {/* status header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2.5 w-2.5">
            <span
              className="absolute inline-flex h-full w-full rounded-full opacity-70"
              style={{
                background: "var(--success)",
                animation: "rx-pulse 1.6s ease-in-out infinite",
              }}
            />
            <span
              className="relative inline-flex h-2.5 w-2.5 rounded-full"
              style={{ background: "var(--success)" }}
            />
          </span>
          <span className="font-display text-[13px] font-semibold uppercase tracking-[0.14em] text-foreground">
            Enviando
          </span>
        </div>
        <span className="font-mono text-[13px] tabular-nums text-muted">
          128<span className="text-faint"> / 300</span>
        </span>
      </div>

      {/* progress bar */}
      <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-surface-secondary">
        <div
          className="brand-fill h-full rounded-full"
          style={{ width: "42%" }}
        />
      </div>

      {/* captured rows */}
      <div className="mt-4 flex flex-col gap-1.5">
        {ROWS.map((r) => (
          <div
            key={r.token}
            className="flex items-center justify-between rounded-[var(--radius-sm)] border border-border bg-surface-secondary/60 px-3 py-2"
          >
            <span className="font-mono text-[12px] text-muted">{r.token}</span>
            {r.ok ? (
              <span className="inline-flex items-center gap-1.5 font-mono text-[12px] font-semibold text-success">
                <svg fill="none" height="13" viewBox="0 0 24 24" width="13">
                  <path
                    d="M5 12l5 5L20 6"
                    stroke="currentColor"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2.6"
                  />
                </svg>
                OK
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 font-mono text-[12px] font-semibold text-danger">
                <svg fill="none" height="13" viewBox="0 0 24 24" width="13">
                  <path
                    d="M6 6l12 12M18 6L6 18"
                    stroke="currentColor"
                    strokeLinecap="round"
                    strokeWidth="2.6"
                  />
                </svg>
                NO
              </span>
            )}
          </div>
        ))}
      </div>

      {/* footer meta */}
      <div className="mt-4 flex items-center justify-between border-t border-border pt-3">
        <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-faint">
          Completa
        </span>
        <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-faint">
          Filtrada · 86 únicas
        </span>
      </div>
    </div>
  );
}
