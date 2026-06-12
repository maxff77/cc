// Live mono count badge (ui-polish-spec §2.5) — Filtrada's travels in
// success green; visible at 0 too. Currently unreferenced ON PURPOSE: its
// consumer (response-views, §3.9 data panels) was reverted to the pre-polish
// look because those panels render inside Envío — the §3.9 restyle is
// deferred until story 2-2 lands and re-adopts this primitive.
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
