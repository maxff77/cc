// Empty state (ui-polish-spec §2.7): an invitation inside the panel it
// belongs to — eyebrow in label-caps + one plain sentence + an optional REAL
// action (never a dead button). Hand-rolled 10-liner on purpose: avoids
// verifying HeroUI's composite EmptyState API (lesson 3.3).
import clsx from "clsx";

import { LabelCaps } from "@/components/ui/label-caps";

export function EmptyState({
  eyebrow,
  message,
  action,
  className,
}: {
  eyebrow?: string;
  message: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "flex flex-col items-center gap-2 px-3 py-10 text-center",
        className,
      )}
    >
      {eyebrow && <LabelCaps>{eyebrow}</LabelCaps>}
      <p className="text-sm text-muted">{message}</p>
      {action}
    </div>
  );
}
