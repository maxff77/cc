// Flank metric: label-caps (LabelCaps, the system's ONE tracked-caps style —
// ui-polish-spec §1.4/§4.3) over a mono metric value (DESIGN.md typography
// ramp — mono is ONLY for data).
import clsx from "clsx";

import { LabelCaps } from "@/components/ui/label-caps";

export function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "success";
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <LabelCaps>{label}</LabelCaps>
      <span
        className={clsx(
          "font-mono text-lg font-extrabold tabular-nums",
          tone === "success" && "text-success",
        )}
      >
        {value}
      </span>
    </div>
  );
}

// Honest ETA (UX-DR14): "~12 min" style estimate, never a fake-precise
// countdown. Recomputed by the caller on every batch.progress event.
export function formatEta(etaSeconds: number, queued: number): string {
  if (!queued || etaSeconds <= 0) return "—";
  if (etaSeconds < 60) return `~${Math.round(etaSeconds)}s`;

  return `~${Math.round(etaSeconds / 60)} min`;
}
