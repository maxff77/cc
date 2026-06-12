// Mono data chip (ui-polish-spec §2.6) — for gate values and other short
// machine identifiers (sub-lines of Historial/Detalle, "Gate activo" in §4).
import clsx from "clsx";

export function MonoChip({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        "rounded border border-border bg-surface-secondary px-1.5 py-0.5 font-mono text-[11px] tabular-nums",
        className,
      )}
    >
      {children}
    </span>
  );
}
