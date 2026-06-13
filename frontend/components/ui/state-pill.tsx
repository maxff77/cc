"use client";

// State pill (ui-polish-spec §2.4) — the ONLY full-round shape of the system.
// One meaning per tone (§1.5): accent = live send, warning = paused/waiting,
// danger = stopping/destructive, muted = closed/inactive. Alpha tints over
// semantic tokens, never hardcoded dark values.
import { Chip } from "@heroui/react";
import clsx from "clsx";

export type PillTone = "accent" | "warning" | "danger" | "muted";

const TONE_CLASS: Record<PillTone, string> = {
  accent: "bg-accent/22 text-accent",
  // text-warning (not -foreground): the app is fixed dark-mode and
  // --warning-foreground is the near-black contrast color for SOLID warning
  // fills — on a tint it was unreadable (recorded 2-3 decision).
  warning: "bg-warning/18 text-warning",
  danger: "bg-danger/18 text-danger",
  muted: "bg-surface-tertiary text-muted",
};

export function StatePill({
  tone,
  dot,
  children,
  className,
}: {
  tone: PillTone;
  dot?: "pulse" | "static";
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Chip
      className={clsx(
        "shrink-0 rounded-full text-[10px] font-bold uppercase tracking-[0.1em]",
        dot && "gap-1.5",
        TONE_CLASS[tone],
        className,
      )}
    >
      {dot && (
        <span
          aria-hidden
          className={clsx(
            "size-1.5 rounded-full",
            // The LIVE/sending dot (accent + pulse) wears the brand gradient;
            // every other state keeps its semantic tone via bg-current.
            tone === "accent" && dot === "pulse"
              ? "gradient-moment"
              : "bg-current",
            dot === "pulse" && "motion-safe:animate-pulse",
          )}
        />
      )}
      {children}
    </Chip>
  );
}
