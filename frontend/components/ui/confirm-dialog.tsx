"use client";

// Native confirm modal (replaces HeroUI AlertDialog) — a blurred backdrop + a
// neon-bordered card with a heading, an optional body slot (e.g. an error
// Notice) and Cancel / Confirm buttons. Closes on backdrop click or Escape;
// Confirm's tone is caller-chosen (primary by default, danger for destructive
// actions like Eliminar). Controlled via `open` + `onOpenChange`.
import { useEffect } from "react";

import { Btn, type BtnVariant } from "@/components/ui/btn";

export function ConfirmDialog({
  open,
  onOpenChange,
  heading,
  confirmLabel,
  cancelLabel = "Cancelar",
  confirmVariant = "primary",
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
  // Single-action dialogs (e.g. the one-time-password "Listo" view) drop the
  // cancel button so there's no second, ambiguous close affordance.
  hideCancel?: boolean;
  pending?: boolean;
  onConfirm: () => void;
  children?: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onOpenChange(false);
    }
    document.addEventListener("keydown", onKey);

    return () => document.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

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
        aria-modal="true"
        className="glow-soft relative w-full max-w-md rounded-[var(--radius)] border border-border bg-surface p-5"
        role="alertdialog"
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
