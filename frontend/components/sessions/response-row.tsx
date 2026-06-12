// Console-density data row (Story 3.2) — DESIGN.md token `data-row`, the
// ONLY console-density element of the system: data-mono 11px, 1px separator
// divider, muted timestamp/index at left, content WRAPS to show the full
// text (long unbroken tokens break with `break-words`; `items-start` keeps
// the index/glyph pinned to the first line), status glyph at right (✅
// success / ❌ danger; Filtrada rows carry none — they are data, not
// states). Newly captured rows wear the `new-highlight` token (success at
// 12%) + success text + a small "nueva" tag.
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
        "flex items-start gap-2 border-b border-separator px-3 py-1 font-mono text-[11px] leading-[1.4]",
        nueva && "bg-success/12 text-success",
      )}
    >
      <span className="shrink-0 text-muted tabular-nums">{left}</span>
      <span className="min-w-0 flex-1 break-words">{text}</span>
      {nueva && (
        <span className="shrink-0 rounded-md bg-success/20 px-1 text-[9px] font-medium uppercase tracking-[0.08em] text-success">
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
