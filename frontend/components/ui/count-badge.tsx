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
        "inline-flex items-center rounded-full px-[7px] py-px font-mono text-[11px] tabular-nums",
        tone === "success"
          ? "bg-[color-mix(in_oklch,var(--success)_22%,transparent)] text-success"
          : "bg-surface-tertiary text-muted",
      )}
    >
      {value}
    </span>
  );
}
