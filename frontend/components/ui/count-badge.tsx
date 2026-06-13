// Live mono count badge (ui-polish-spec §2.5) — Filtrada's travels in
// success green; visible at 0 too. THE single count-badge source: the
// response-views data panels and mobile Tabs consume this primitive (the
// inline duplicate that lived there was consolidated away).
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
