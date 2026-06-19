"use client";

// Native confirm modal (replaces HeroUI AlertDialog) — a blurred backdrop + a
// neon-bordered card with a heading, an optional body slot (e.g. an error
// Notice) and Cancel / Confirm buttons. Closes on backdrop click or Escape;
// Confirm's tone is caller-chosen (primary by default, danger for destructive
// actions like Eliminar). Controlled via `open` + `onOpenChange`. Doubles as a
// multi-field edit FORM (gates editor) — pass role="dialog" there so SRs don't
// hear an alert; the default stays "alertdialog" for plain confirms.
import { useEffect, useLayoutEffect, useRef } from "react";

import { Btn, type BtnVariant } from "@/components/ui/btn";

// Tab-cycle / initial-focus target set. [tabindex="-1"] (the backdrop) is
// excluded; disabled controls are skipped so a pending Confirm can't trap focus.
const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

function getFocusable(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

export function ConfirmDialog({
  open,
  onOpenChange,
  heading,
  confirmLabel,
  cancelLabel = "Cancelar",
  confirmVariant = "primary",
  role = "alertdialog",
  hideCancel,
  pending,
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  heading: string;
  confirmLabel: string;
  cancelLabel?: string;
  confirmVariant?: BtnVariant;
  // "dialog" for form-bearing dialogs (the gates editor), "alertdialog" for a
  // plain destructive/affirm confirm. Drives the card's ARIA role only.
  role?: "dialog" | "alertdialog";
  // Single-action dialogs (e.g. the one-time-password "Listo" view) drop the
  // cancel button so there's no second, ambiguous close affordance.
  hideCancel?: boolean;
  pending?: boolean;
  onConfirm: () => void;
  children?: React.ReactNode;
}) {
  const cardRef = useRef<HTMLDivElement>(null);
  // The control that had focus when we opened — restored on close so keyboard/
  // SR users land back on the trigger row, not the top of the document.
  const restoreRef = useRef<HTMLElement | null>(null);

  // Capture the outgoing focus on open; restore it on close/unmount.
  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;

    return () => restoreRef.current?.focus?.();
  }, [open]);

  // Escape to close + a focus trap so Tab/Shift+Tab cycle within the card only
  // (aria-modal alone does NOT keep focus in — it'd escape behind the backdrop).
  useEffect(() => {
    if (!open) return;

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onOpenChange(false);

        return;
      }
      if (e.key !== "Tab") return;
      const card = cardRef.current;

      if (!card) return;
      const f = getFocusable(card);

      if (!f.length) return;
      const first = f[0];
      const last = f[f.length - 1];
      const active = document.activeElement;

      if (e.shiftKey) {
        if (active === first || !card.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else if (active === last || !card.contains(active)) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);

    return () => document.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  // On open, focus the first form field (edit dialogs) so typing starts at once;
  // with no field, fall back to Confirm (last focusable) for plain confirms.
  useLayoutEffect(() => {
    if (!open) return;
    const card = cardRef.current;

    if (!card) return;
    const field = card.querySelector<HTMLElement>("input, select, textarea");

    if (field) {
      field.focus();

      return;
    }
    const f = getFocusable(card);

    (f[f.length - 1] ?? card).focus();
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      {/* Full-bleed backdrop button — a native control so click-outside-to-close
          stays keyboard/touch accessible without a static onClick handler. */}
      <button
        aria-label="Cerrar"
        className="absolute inset-0 cursor-default"
        tabIndex={-1}
        type="button"
        onClick={() => onOpenChange(false)}
      />
      <div
        ref={cardRef}
        aria-modal="true"
        className="glow-soft relative w-full max-w-md rounded-[var(--radius)] border border-border bg-surface p-5"
        role={role}
        // -1 so the card itself can take focus as a last resort (no focusables).
        tabIndex={-1}
      >
        <h2 className="font-display text-base font-bold leading-snug text-foreground">
          {heading}
        </h2>
        {children && <div className="mt-3">{children}</div>}
        <div className="mt-5 flex justify-end gap-2">
          {!hideCancel && (
            <Btn
              disabled={pending}
              size="sm"
              variant="secondary"
              onClick={() => onOpenChange(false)}
            >
              {cancelLabel}
            </Btn>
          )}
          <Btn
            disabled={pending}
            size="sm"
            variant={confirmVariant}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Btn>
        </div>
      </div>
    </div>
  );
}
