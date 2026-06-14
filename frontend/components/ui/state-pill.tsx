"use client";

// State pill (ui-polish-spec §2.4 / Ranger-X handoff `StatePill`) — the ONLY
// full-round shape of the system, now native (no HeroUI Chip). One meaning per
// tone: accent = live send, cyan = admin role, warning = paused/waiting,
// danger = stopping/destructive/expired, success = active/approved, muted =
// closed/inactive. Alpha tints over semantic tokens + a soft neon glow that
// scales with --glow (muted stays flat). The optional dot pulses for live state.
import clsx from "clsx";

export type PillTone =
  | "accent"
  | "cyan"
  | "warning"
  | "danger"
  | "success"
  | "muted";

const TONE_CLASS: Record<PillTone, string> = {
  accent: "bg-accent/22 text-accent",
  // --cyan/--blue/--magenta are custom Ranger-X tokens (not HeroUI color
  // utilities), so reach them via arbitrary var() instead of bg-cyan/text-cyan.
  cyan: "bg-[color-mix(in_oklch,var(--cyan)_16%,transparent)] text-[var(--cyan)]",
  // text-warning (not -foreground): the --warning-foreground is the near-black
  // contrast color for SOLID warning fills — on a tint it is unreadable
  // (recorded 2-3 decision).
  warning: "bg-warning/18 text-warning",
  danger: "bg-danger/18 text-danger",
  success: "bg-success/18 text-success",
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
    <span
      className={clsx(
        "inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2.5 py-[3px] font-display text-[10px] font-bold uppercase leading-none tracking-[0.12em]",
        dot && "gap-1.5",
        TONE_CLASS[tone],
        className,
      )}
      style={
        tone === "muted"
          ? undefined
          : { boxShadow: "0 0 calc(12px * var(--glow)) currentColor" }
      }
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
    </span>
  );
}
