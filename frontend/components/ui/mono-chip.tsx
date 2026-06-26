// Mono data chip (ui-polish-spec §2.6) — for gate values and other short
// machine identifiers (sub-lines of Historial/Detalle, "Gate activo" in §4).
import clsx from "clsx";

export function MonoChip({
  children,
  className,
  dot = false,
}: {
  children: React.ReactNode;
  className?: string;
  // Optional 6px accent dot (e.g. the "Gateway activo" live chip).
  dot?: boolean;
}) {
  return (
    <span
      className={clsx(
        "inline-flex max-w-full items-center gap-2 rounded border border-border bg-surface-secondary px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-foreground",
        className,
      )}
    >
      {dot && (
        <span className="size-1.5 shrink-0 rounded-full bg-accent" aria-hidden />
      )}
      <span className="overflow-hidden text-ellipsis">{children}</span>
    </span>
  );
}
