// Live mono count badge (ui-polish-spec §2.5) — extracted from
// response-views (Filtrada's travels in success green). Visible at 0 too.
import clsx from "clsx";

export function CountBadge({
  value,
  tone,
}: {
  value: number;
  tone?: "success";
}) {
  return (
    <span
      className={clsx(
        "rounded bg-surface-secondary px-1.5 font-mono text-[11px] leading-5 tabular-nums",
        tone === "success" && "text-success",
      )}
    >
      {value}
    </span>
  );
}
