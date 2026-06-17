"use client";

// Native select (Ranger-X handoff lib.jsx `Select`) — replaces HeroUI Select/
// ListBox. A button trigger + absolutely-positioned popover list; closes on
// outside-click or Escape. Options are { id, label, mono? }; controlled by
// value (the selected id, or null) + onChange(id). Inline error line below.
import { useEffect, useRef, useState } from "react";
import clsx from "clsx";

import { Icon } from "@/components/ui/icon";
import { LabelCaps } from "@/components/ui/label-caps";

export interface SelectOption {
  id: string;
  label: string;
  mono?: string;
}

export interface SelectProps {
  label?: string;
  value: string | null;
  placeholder?: string;
  options: SelectOption[];
  onChange?: (id: string) => void;
  disabled?: boolean;
  error?: string | null;
  className?: string;
}

export function Select({
  label,
  value,
  placeholder,
  options,
  onChange,
  disabled,
  error,
  className,
}: SelectProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const invalid = error != null && error !== "";

  useEffect(() => {
    if (!open) return;

    function onPointer(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);

    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const selected = options.find((o) => o.id === value) ?? null;

  return (
    <div ref={ref} className={clsx("relative block", className)}>
      {label && (
        <div className="mb-1.5">
          <LabelCaps>{label}</LabelCaps>
        </div>
      )}
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        className={clsx(
          "tap-44 rx-focus flex w-full items-center justify-between gap-2.5 rounded-[var(--radius-field)] border bg-[var(--field-background)] px-3 py-2.5 text-left text-sm transition-[border-color,box-shadow] duration-150 disabled:cursor-not-allowed disabled:opacity-55",
          selected
            ? "text-[var(--field-foreground)]"
            : "text-[var(--field-placeholder)]",
          invalid
            ? "border-danger"
            : open
              ? "border-[var(--focus)] shadow-[0_0_0_3px_var(--accent-soft)]"
              : "border-[var(--field-border)]",
        )}
        disabled={disabled}
        type="button"
        onClick={() => !disabled && setOpen((o) => !o)}
      >
        <span className="overflow-hidden text-ellipsis whitespace-nowrap">
          {selected ? selected.label : placeholder}
        </span>
        <Icon
          className={clsx(
            "text-muted transition-transform duration-150",
            open && "rotate-180",
          )}
          name="chevron"
          size={16}
        />
      </button>
      {open && (
        <div
          className="rx-scroll glow-soft absolute left-0 right-0 top-full z-40 mt-1.5 max-h-60 overflow-y-auto rounded-[var(--radius-field)] border border-border bg-surface p-1.5"
          role="listbox"
        >
          {options.map((o) => {
            const active = o.id === value;

            return (
              <button
                key={o.id}
                aria-selected={active}
                className={clsx(
                  "tap-44 flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-2.5 text-left text-sm",
                  active
                    ? "bg-[var(--accent-soft)] text-accent"
                    : "text-foreground hover:bg-surface-secondary",
                )}
                role="option"
                type="button"
                onClick={() => {
                  onChange?.(o.id);
                  setOpen(false);
                }}
              >
                {o.label}
                {o.mono && (
                  <span className="font-mono text-[12px] text-muted">
                    {o.mono}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
      {invalid && (
        <p className="mt-1.5 px-0.5 text-[12px] text-danger">{error}</p>
      )}
    </div>
  );
}
