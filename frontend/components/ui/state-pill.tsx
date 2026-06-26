"use client";

// State pill (ui-polish-spec §2.4 / Ranger-X handoff `StatePill`) — the ONLY
// full-round shape of the system, now native (no HeroUI Chip). One meaning per
// tone: accent = live send, cyan = admin role, warning = paused/waiting,
// danger = stopping/destructive/expired, success = active/approved, muted =
// closed/inactive. Alpha tints over semantic tokens; a soft neon glow is
// reserved for the LIVE state (accent + pulse) ONLY — every static pill is flat
// (control-room calm: energy in moments, not on every chip). The dot pulses for
// live state.
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
  // Canvas state pill: 26px tall full-round chip, Saira 600 / 12px, leading 6px
  // dot. The dot pulses (rx-pulse) ONLY while live (dot="pulse"); static pills
  // stay flat (control-room calm — energy in moments, not on every chip).
  return (
    <span
      className={clsx(
        "inline-flex h-[26px] shrink-0 items-center gap-[7px] whitespace-nowrap rounded-full px-[11px] font-display text-[12px] font-semibold leading-none",
        TONE_CLASS[tone],
        className,
      )}
    >
      {dot && (
        <span
          aria-hidden
          className="size-[6px] rounded-full bg-current"
          style={
            dot === "pulse"
              ? { animation: "rx-pulse 1.4s ease infinite" }
              : undefined
          }
        />
      )}
      {children}
    </span>
  );
}
