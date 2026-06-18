import Link from "next/link";
import clsx from "clsx";

import { Icon, type IconName } from "@/components/ui/icon";

// Navigation-as-button for the public landing: an <a>/<Link> styled like the
// app's <Btn>, so marketing CTAs stay semantically links (a real <button>
// nested in a link is invalid, and these all navigate). Mirrors Btn's variants
// and the AA-safe gradient primary (.btn-fill + glow).
type LinkBtnVariant = "primary" | "secondary" | "ghost";
type LinkBtnSize = "md" | "lg";

const SIZE_CLASS: Record<LinkBtnSize, string> = {
  md: "px-4 py-[9px] text-sm",
  lg: "px-[22px] py-[13px] text-[15px]",
};

const VARIANT_CLASS: Record<LinkBtnVariant, string> = {
  primary: "text-white border-none btn-fill",
  secondary: "bg-surface-secondary text-foreground border-border",
  ghost:
    "bg-transparent text-muted border-transparent hover:text-foreground hover:border-border",
};

interface LinkBtnProps {
  href: string;
  variant?: LinkBtnVariant;
  size?: LinkBtnSize;
  iconRight?: IconName;
  full?: boolean;
  className?: string;
  children: React.ReactNode;
}

export function LinkBtn({
  href,
  variant = "secondary",
  size = "md",
  iconRight,
  full,
  className,
  children,
}: LinkBtnProps) {
  return (
    <Link
      className={clsx(
        "tap-44 rx-focus inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-field)] border font-display font-semibold tracking-[0.02em] no-underline transition-[transform,box-shadow,background,border-color] duration-150 hover:-translate-y-px",
        SIZE_CLASS[size],
        VARIANT_CLASS[variant],
        full && "w-full",
        className,
      )}
      href={href}
      style={
        variant === "primary"
          ? {
              boxShadow:
                "0 6px 22px oklch(64% 0.21 295 / calc(0.35 * var(--glow)))",
            }
          : undefined
      }
    >
      {children}
      {iconRight && <Icon name={iconRight} size={size === "lg" ? 18 : 16} />}
    </Link>
  );
}
