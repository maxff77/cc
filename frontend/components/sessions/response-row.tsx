// Console-density data row (Story 3.2) — DESIGN.md token `data-row`, the
// ONLY console-density element of the system: data-mono ~11.5px, 1px separator
// divider, faint timestamp/index at left, content WRAPS to show the full
// text (long unbroken tokens break with `break-words` + overflow-wrap:anywhere;
// `items-start` keeps the index/glyph pinned to the first line), status glyph
// at right (✓ success / ✕ danger; Datos CC rows carry none — they are data,
// not states, and render their value in success color). Newly captured rows
// wear the `new-highlight` token (success at 12%) + a small "nueva" tag.
import clsx from "clsx";

export interface DataRowProps {
  left: string;
  text: string;
  status?: "ok" | "rejected";
  nueva: boolean;
}

export function DataRow({ left, text, status, nueva }: DataRowProps) {
  // No status ⇒ a Datos CC row: index + success-colored value, never a glyph.
  const isData = status === undefined;

  return (
    <div
      className="flex items-start gap-2.5 border-b border-[var(--separator)] px-3.5 py-2"
      style={
        nueva
          ? {
              background:
                "color-mix(in oklch, var(--success) 12%, transparent)",
            }
          : undefined
      }
    >
      <span className="shrink-0 pt-px font-mono text-[10.5px] tabular-nums text-[var(--faint)]">
        {left}
      </span>
      <span
        className={clsx(
          "min-w-0 flex-1 whitespace-pre-line break-words font-mono leading-[1.45] [overflow-wrap:anywhere]",
          isData ? "text-[12px] text-success" : "text-[11.5px] text-foreground",
        )}
      >
        {text}
      </span>
      {nueva && (
        <span
          className="shrink-0 rounded-[5px] px-[5px] py-px font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-success"
          style={{
            background: "color-mix(in oklch, var(--success) 20%, transparent)",
          }}
        >
          nueva
        </span>
      )}
      {status && (
        <span
          className={clsx(
            "shrink-0 text-[12px] leading-none",
            status === "ok" ? "text-success" : "text-danger",
          )}
        >
          {/* The ✓/✕ glyph carries the state visually; the sr-only word
              carries it as TEXT so screen readers announce the verdict
              (the glyph alone is silent/ambiguous to assistive tech). */}
          <span aria-hidden>{status === "ok" ? "✓" : "✕"}</span>
          <span className="sr-only">
            {status === "ok" ? "Aprobada" : "Rechazada"}
          </span>
        </span>
      )}
    </div>
  );
}
