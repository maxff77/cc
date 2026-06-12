// Console-density data row (Story 3.2) — DESIGN.md token `data-row`, the
// ONLY console-density element of the system: data-mono 11px, 1px separator
// divider, muted timestamp/index at left, content ellipsized to one line,
// status glyph at right (✅ success / ❌ danger; Filtrada rows carry none —
// they are data, not states). Newly captured rows wear the `new-highlight`
// token (success at 12%) + success text + a small "nueva" tag.
import clsx from "clsx";

export interface DataRowProps {
  left: string;
  text: string;
  status?: "ok" | "rejected";
  nueva: boolean;
}

export function DataRow({ left, text, status, nueva }: DataRowProps) {
  return (
    <div
      className={clsx(
        // transition-colors: the `nueva` highlight fades out smoothly when
        // the store drops the flag (CSS only, respects reduced motion).
        "flex items-center gap-2 border-b border-separator px-3 py-1 font-mono text-[11px] leading-[1.4] transition-colors duration-700 motion-reduce:transition-none",
        nueva && "bg-success/12 text-success",
      )}
    >
      {/* Fixed-width right-aligned gutter: timestamps/indexes align like a
          true tape. */}
      <span className="w-14 shrink-0 text-right text-muted tabular-nums">
        {left}
      </span>
      <span className="min-w-0 flex-1 truncate">{text}</span>
      {nueva && (
        <span className="shrink-0 rounded bg-success/20 px-1 text-[9px] font-medium uppercase tracking-[0.1em] text-success">
          nueva
        </span>
      )}
      {status && (
        <span
          className={clsx(
            "shrink-0",
            status === "ok" ? "text-success" : "text-danger",
          )}
        >
          {status === "ok" ? "✅" : "❌"}
        </span>
      )}
    </div>
  );
}
