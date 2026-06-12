// Rack-plate section card (ui-polish-spec §1.10 / §2.1): the signature
// engraved legend mounted OVER the top border, optional right slot (e.g. a
// CountBadge as a panel LED) and an optional 2px live-state left rail.
// Hand-rolled div on purpose (NOT HeroUI Card): we need the overlapping
// legend and zero elevation.
import clsx from "clsx";

import { LabelCaps } from "@/components/ui/label-caps";

// Vertical split mask for anything mounted over the top border: page
// background above the line, card surface below. A flat bg-background would
// paint a darker rectangle onto the bg-surface body (12% vs 21% in dark
// mode) — the engraving must interrupt the border, not stain the card. The
// fixed heights (h-4 / h-5) put the border exactly at the 50% hard stop.
const LEGEND_MASK =
  "bg-[linear-gradient(to_bottom,var(--background)_50%,var(--surface)_50%)]";

export function SectionCard({
  legend,
  legendAs,
  legendRight,
  rail = "none",
  padding = "gutter",
  className,
  children,
}: {
  legend?: string;
  // Forwarded to LabelCaps: pass "h2" when the legend replaces a real
  // heading (form/section titles) so the document outline keeps a heading
  // level under the page h1 for screen-reader navigation.
  legendAs?: "span" | "h2";
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
        <LabelCaps
          as={legendAs}
          className={clsx(
            "absolute -top-2 left-3 flex h-4 items-center px-1.5",
            LEGEND_MASK,
          )}
        >
          {legend}
        </LabelCaps>
      )}
      {legendRight && (
        <span
          className={clsx(
            "absolute -top-2.5 right-3 flex h-5 items-center px-1.5",
            LEGEND_MASK,
          )}
        >
          {legendRight}
        </span>
      )}
      {children}
    </section>
  );
}
