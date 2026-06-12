// Rack-plate section card (ui-polish-spec §1.10 / §2.1): the signature
// engraved legend mounted OVER the top border, optional right slot (e.g. a
// CountBadge as a panel LED) and an optional 2px live-state left rail.
// Hand-rolled div on purpose (NOT HeroUI Card): we need the overlapping
// legend and zero elevation.
import clsx from "clsx";

import { LabelCaps } from "@/components/ui/label-caps";

export function SectionCard({
  legend,
  legendRight,
  rail = "none",
  padding = "gutter",
  className,
  children,
}: {
  legend?: string;
  legendRight?: React.ReactNode;
  rail?: "accent" | "warning" | "none";
  padding?: "gutter" | "none";
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className={clsx(
        "relative rounded border border-border bg-surface",
        padding === "gutter" && "p-3",
        rail === "accent" && "border-l-2 border-l-accent",
        rail === "warning" && "border-l-2 border-l-warning",
        className,
      )}
    >
      {legend && (
        <LabelCaps className="absolute -top-2 left-3 bg-background px-1.5">
          {legend}
        </LabelCaps>
      )}
      {legendRight && (
        <span className="absolute -top-2 right-3 bg-background px-1.5">
          {legendRight}
        </span>
      )}
      {children}
    </section>
  );
}
