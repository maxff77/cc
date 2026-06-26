"use client";

// Textarea (Ranger-X handoff lib.jsx `Area`) — replaces HeroUI TextArea. Mono
// by default (it holds pasted data lines), focus ring on --focus, vertical
// resize, neon scrollbar. Controlled (value + onChange(string)) with an inline
// error line.
import { useState } from "react";
import clsx from "clsx";

import { LabelCaps } from "@/components/ui/label-caps";

export interface AreaProps {
  label?: string;
  value: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  rows?: number;
  error?: string | null;
  name?: string;
  mono?: boolean;
  className?: string;
  textareaClassName?: string;
}

export function Area({
  label,
  value,
  onChange,
  placeholder,
  rows = 6,
  error,
  name,
  mono = true,
  className,
  textareaClassName,
}: AreaProps) {
  const [focus, setFocus] = useState(false);
  const invalid = error != null && error !== "";

  return (
    <label className={clsx("block", className)}>
      {label && (
        <div className="mb-1.5">
          <LabelCaps>{label}</LabelCaps>
        </div>
      )}
      <textarea
        className={clsx(
          // text-base on phones (<640) keeps the paste box ≥16px so iOS Safari
          // does not zoom on focus; the dense 13px mono returns at sm+.
          "rx-scroll min-h-[92px] w-full resize-y rounded-[var(--radius-field)] border bg-[var(--field-background)] px-3 py-[11px] text-base leading-[1.55] text-[var(--field-foreground)] outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-[var(--field-placeholder)] sm:text-[12.5px]",
          mono && "font-mono",
          invalid
            ? "border-danger"
            : focus
              ? "border-[var(--focus)] shadow-[0_0_0_3px_var(--accent-soft)]"
              : "border-[var(--field-border)]",
          textareaClassName,
        )}
        name={name}
        placeholder={placeholder}
        rows={rows}
        value={value}
        onBlur={() => setFocus(false)}
        onChange={(e) => onChange?.(e.target.value)}
        onFocus={() => setFocus(true)}
      />
      {invalid && (
        <p className="mt-1.5 px-0.5 text-[12px] text-danger">{error}</p>
      )}
    </label>
  );
}
