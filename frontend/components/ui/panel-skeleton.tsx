// Loading skeleton with a shape faithful to data panels and tables
// (ui-polish-spec §1.9 / §2.8): row-height bars, never a floating centered
// spinner. Native pulse bars over surface-tertiary (motion-safe).
import clsx from "clsx";

export function PanelSkeleton({
  rows = 5,
  className,
}: {
  rows?: number;
  className?: string;
}) {
  return (
    <div className={clsx("flex flex-col gap-2 p-3", className)}>
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="h-4 rounded bg-surface-tertiary motion-safe:animate-pulse"
        />
      ))}
    </div>
  );
}
