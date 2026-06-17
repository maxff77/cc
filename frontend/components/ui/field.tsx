"use client";

// Text field (Ranger-X handoff lib.jsx `Field`) — replaces HeroUI TextField/
// Input. Caps label, optional leading icon, focus ring on --focus, optional
// right slot (e.g. a password reveal toggle) and an inline error line. Controlled
// (value + onChange(string)); standard input attrs (name, type, autoComplete,
// required) pass through for native form submits.
import type { HTMLInputTypeAttribute } from "react";

import { useState } from "react";
import clsx from "clsx";

import { Icon, type IconName } from "@/components/ui/icon";
import { LabelCaps } from "@/components/ui/label-caps";

export interface FieldProps {
  label?: string;
  icon?: IconName;
  type?: HTMLInputTypeAttribute;
  value: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  rightSlot?: React.ReactNode;
  mono?: boolean;
  error?: string | null;
  name?: string;
  autoComplete?: string;
  required?: boolean;
  disabled?: boolean;
  className?: string;
  inputClassName?: string;
}

export function Field({
  label,
  icon,
  type = "text",
  value,
  onChange,
  placeholder,
  rightSlot,
  mono,
  error,
  name,
  autoComplete,
  required,
  disabled,
  className,
  inputClassName,
}: FieldProps) {
  const [focus, setFocus] = useState(false);
  const invalid = error != null && error !== "";

  return (
    <label className={clsx("block", className)}>
      {label && (
        <div className="mb-1.5">
          <LabelCaps>{label}</LabelCaps>
        </div>
      )}
      <div
        className={clsx(
          "flex items-center gap-2.5 rounded-[var(--radius-field)] border bg-[var(--field-background)] px-3 py-2.5 transition-[border-color,box-shadow] duration-150",
          invalid
            ? "border-danger"
            : focus
              ? "border-[var(--focus)] shadow-[0_0_0_3px_var(--accent-soft)]"
              : "border-[var(--field-border)]",
        )}
      >
        {icon && (
          <Icon
            className={focus ? "text-accent" : "text-muted"}
            name={icon}
            size={17}
          />
        )}
        <input
          autoComplete={autoComplete}
          className={clsx(
            // text-base on phones (<640) keeps inputs ≥16px so iOS Safari does
            // not zoom the viewport on focus; design size returns at sm+.
            "min-w-0 flex-1 border-none bg-transparent text-base text-[var(--field-foreground)] outline-none placeholder:text-[var(--field-placeholder)] sm:text-sm",
            mono && "font-mono",
            inputClassName,
          )}
          disabled={disabled}
          name={name}
          placeholder={placeholder}
          required={required}
          type={type}
          value={value}
          onBlur={() => setFocus(false)}
          onChange={(e) => onChange?.(e.target.value)}
          onFocus={() => setFocus(true)}
        />
        {rightSlot}
      </div>
      {invalid && (
        <p className="mt-1.5 px-0.5 text-[12px] text-danger">{error}</p>
      )}
    </label>
  );
}
