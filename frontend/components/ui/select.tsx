"use client";

// Native select (Ranger-X handoff lib.jsx `Select`) — replaces HeroUI Select/
// ListBox. A button trigger + absolutely-positioned popover list; closes on
// outside-click or Escape. Options are { id, label, mono? }; controlled by
// value (the selected id, or null) + onChange(id). Inline error line below.
// Full WAI-ARIA listbox keyboard model: arrow/Home/End nav, type-ahead, Enter/
// Space select, real focus moved onto the highlighted option, focus returned to
// the trigger on close.
import { useEffect, useId, useRef, useState } from "react";
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
  // Highlighted option index while open (-1 = none yet).
  const [highlight, setHighlight] = useState(-1);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  // Type-ahead buffer + its reset timer (no Date.now() — a setTimeout clears it).
  const typeahead = useRef("");
  const typeaheadTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const invalid = error != null && error !== "";
  const baseId = useId();

  const selectedIndex = options.findIndex((o) => o.id === value);
  const selected = selectedIndex >= 0 ? options[selectedIndex] : null;

  // Close + (optionally) hand focus back to the trigger. Outside-click closes
  // without yanking focus; keyboard exits restore it so the tab order is sane.
  function close(returnFocus: boolean) {
    setOpen(false);
    setHighlight(-1);
    if (returnFocus) triggerRef.current?.focus();
  }

  function openMenu(to: number) {
    if (disabled) return;
    setOpen(true);
    setHighlight(to);
  }

  function selectAt(i: number) {
    const opt = options[i];

    if (!opt) return;
    onChange?.(opt.id);
    close(true);
  }

  // Outside-click closes (no focus steal). Escape handled per-element below.
  useEffect(() => {
    if (!open) return;

    function onPointer(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        close(false);
      }
    }
    document.addEventListener("mousedown", onPointer);

    return () => document.removeEventListener("mousedown", onPointer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Move real DOM focus onto the highlighted option whenever it changes.
  useEffect(() => {
    if (open && highlight >= 0) optionRefs.current[highlight]?.focus();
  }, [open, highlight]);

  // Drop the type-ahead timer on unmount.
  useEffect(
    () => () => {
      if (typeaheadTimer.current) clearTimeout(typeaheadTimer.current);
    },
    [],
  );

  // Jump highlight to the next option whose label starts with the buffer.
  function typeJump(char: string) {
    typeahead.current += char.toLowerCase();
    if (typeaheadTimer.current) clearTimeout(typeaheadTimer.current);
    typeaheadTimer.current = setTimeout(() => {
      typeahead.current = "";
    }, 500);

    const buf = typeahead.current;
    // Search after the current highlight so repeated keys cycle matches.
    const from = highlight < 0 ? 0 : highlight;

    for (let step = 1; step <= options.length; step++) {
      const i = (from + step) % options.length;

      if (options[i].label.toLowerCase().startsWith(buf)) {
        setHighlight(i);

        return;
      }
    }
    // Single-char buffer: also accept a match at the current index.
    if (options[from]?.label.toLowerCase().startsWith(buf)) setHighlight(from);
  }

  function onTriggerKey(e: React.KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      openMenu(selectedIndex >= 0 ? selectedIndex : 0);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      openMenu(options.length - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openMenu(selectedIndex >= 0 ? selectedIndex : 0);
    }
  }

  function onListKey(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => (h + 1) % options.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => (h <= 0 ? options.length - 1 : h - 1));
    } else if (e.key === "Home") {
      e.preventDefault();
      setHighlight(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setHighlight(options.length - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (highlight >= 0) selectAt(highlight);
    } else if (e.key === "Escape") {
      e.preventDefault();
      close(true);
    } else if (e.key === "Tab") {
      // Let focus proceed naturally; just dismiss the popover.
      close(false);
    } else if (e.key.length === 1) {
      // Printable char → type-ahead (Space is reserved for select, above).
      typeJump(e.key);
    }
  }

  return (
    <div ref={ref} className={clsx("relative block", className)}>
      {label && (
        <div className="mb-1.5">
          <LabelCaps>{label}</LabelCaps>
        </div>
      )}
      <button
        ref={triggerRef}
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
        onClick={() => !disabled && (open ? close(false) : openMenu(selectedIndex >= 0 ? selectedIndex : 0))}
        onKeyDown={onTriggerKey}
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
          aria-activedescendant={
            highlight >= 0 ? `${baseId}-opt-${options[highlight].id}` : undefined
          }
          className="rx-scroll glow-soft absolute left-0 right-0 top-full z-40 mt-1.5 max-h-60 overflow-y-auto rounded-[var(--radius-field)] border border-border bg-surface p-1.5"
          role="listbox"
          tabIndex={-1}
          onKeyDown={onListKey}
        >
          {options.map((o, i) => {
            const active = o.id === value;
            const highlighted = i === highlight;

            return (
              <button
                key={o.id}
                ref={(el) => {
                  optionRefs.current[i] = el;
                }}
                aria-selected={active}
                className={clsx(
                  "tap-44 flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-2.5 py-2.5 text-left text-sm",
                  active
                    ? "bg-[var(--accent-soft)] text-accent"
                    : highlighted
                      ? "bg-surface-secondary text-foreground"
                      : "text-foreground hover:bg-surface-secondary",
                )}
                id={`${baseId}-opt-${o.id}`}
                role="option"
                tabIndex={-1}
                type="button"
                onClick={() => selectAt(i)}
                onMouseEnter={() => setHighlight(i)}
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
