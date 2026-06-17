"use client";

// Native button (Ranger-X handoff lib.jsx `Btn`) — replaces HeroUI <Button>.
// Saira display type, token-driven variants, field radius. Primary wears the
// brand gradient + neon glow (the one gradient-on-a-surface moment); the glow
// scales with --glow. It's a real <button>, so callers use onClick (not the
// HeroUI onPress) and standard button attributes pass through.
import type { ButtonHTMLAttributes } from "react";

import clsx from "clsx";

import { Icon, type IconName } from "@/components/ui/icon";

export type BtnVariant =
  | "primary"
  | "secondary"
  | "ghost"
  | "danger"
  | "success"
  | "warning";
export type BtnSize = "sm" | "md" | "lg";

const SIZE_CLASS: Record<BtnSize, string> = {
  sm: "px-3 py-1.5 text-[13px]",
  md: "px-4 py-[9px] text-sm",
  lg: "px-[22px] py-[13px] text-[15px]",
};

// Variant surfaces. color-mix borders mirror the handoff's danger/warning
// outline buttons; primary's gradient + glow ride inline style below.
const VARIANT_CLASS: Record<BtnVariant, string> = {
  primary: "text-white border-none brand-fill",
  secondary: "bg-surface-secondary text-foreground border-border",
  ghost: "bg-transparent text-muted border-transparent hover:text-foreground",
  danger:
    "bg-transparent text-danger border-[color-mix(in_oklch,var(--danger)_40%,transparent)]",
  success: "bg-success text-success-foreground border-none",
  warning:
    "bg-transparent text-warning border-[color-mix(in_oklch,var(--warning)_40%,transparent)]",
};

export interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BtnVariant;
  size?: BtnSize;
  icon?: IconName;
  iconRight?: IconName;
  full?: boolean;
}

export function Btn({
  variant = "secondary",
  size = "md",
  icon,
  iconRight,
  full,
  className,
  style,
  children,
  type = "button",
  ...rest
}: BtnProps) {
  const glyph = size === "sm" ? 15 : 17;

  return (
    <button
      className={clsx(
        "tap-44 rx-focus inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-field)] border font-display font-semibold tracking-[0.02em] transition-[transform,box-shadow,background,border-color] duration-150 hover:enabled:-translate-y-px disabled:cursor-not-allowed disabled:opacity-55",
        SIZE_CLASS[size],
        VARIANT_CLASS[variant],
        full && "w-full",
        className,
      )}
      style={{
        ...(variant === "primary"
          ? {
              boxShadow:
                "0 6px 22px oklch(64% 0.21 295 / calc(0.35 * var(--glow)))",
            }
          : null),
        ...style,
      }}
      type={type}
      {...rest}
    >
      {icon && <Icon name={icon} size={glyph} />}
      {children}
      {iconRight && <Icon name={iconRight} size={glyph} />}
    </button>
  );
}
