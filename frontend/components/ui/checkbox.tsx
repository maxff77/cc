"use client";

// Checkbox (Ranger-X handoff lib.jsx `Checkbox`) — a brand-gradient filled box
// when checked, token border when not. Controlled (checked + onChange(bool)).
import clsx from "clsx";

import { Icon } from "@/components/ui/icon";

export function Checkbox({
  checked,
  onChange,
  children,
  className,
}: {
  checked: boolean;
  onChange?: (checked: boolean) => void;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <label
      className={clsx(
        "inline-flex cursor-pointer items-center gap-2.5 text-sm text-muted",
        className,
      )}
    >
      <button
        aria-checked={checked}
        aria-label={typeof children === "string" ? children : "checkbox"}
        className={clsx(
          "rx-focus inline-flex size-[18px] shrink-0 items-center justify-center rounded-[5px] border-[1.5px] text-white transition-[background] duration-150",
          checked
            ? "border-transparent brand-fill"
            : "border-[var(--border-strong)]",
        )}
        role="checkbox"
        type="button"
        onClick={() => onChange?.(!checked)}
      >
        {checked && <Icon name="check" size={13} />}
      </button>
      {children}
    </label>
  );
}
