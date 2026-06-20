"use client";

// Gift-key claim in a modal (Cliente Redesign). The cockpit no longer carries an
// always-present "Canjear key" section — a key-icon button in the nav (and the
// mobile bottom nav) opens this instead, so the cockpit column reads más aireado.
// Reuses the ConfirmDialog backdrop/card look and mounts the shared <ClaimKey>
// form; on a successful claim we refresh /me so the plan badge updates, and keep
// the modal open so its success Notice stays visible (user closes with the X).
// ponytail: lighter than ConfirmDialog's full focus-trap — Escape + backdrop
// close + initial input focus is enough for a single-field modal.
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { ClaimKey } from "@/components/keys/claim-key";
import { Icon } from "@/components/ui/icon";

export function KeyModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    // Land focus in the key field so typing starts at once.
    cardRef.current?.querySelector<HTMLElement>("input")?.focus();

    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <button
        aria-label="Cerrar"
        className="absolute inset-0 cursor-default"
        tabIndex={-1}
        type="button"
        onClick={onClose}
      />
      <div
        ref={cardRef}
        aria-modal="true"
        className="rx-enter glow-soft relative w-full max-w-sm rounded-[var(--radius)] border border-[var(--border-strong)] bg-surface p-6"
        role="dialog"
      >
        <button
          aria-label="Cerrar"
          className="rx-focus absolute right-3.5 top-3.5 inline-flex size-7 items-center justify-center rounded-[var(--radius-sm)] border border-border bg-surface-secondary text-muted transition-colors hover:text-foreground"
          type="button"
          onClick={onClose}
        >
          <Icon name="close" size={15} />
        </button>

        <div className="mb-5 flex flex-col items-center gap-3.5 text-center">
          <div className="flex size-[52px] items-center justify-center rounded-[14px] bg-[var(--accent-soft)]">
            <Icon className="text-accent" name="key" size={26} />
          </div>
          <div className="flex flex-col gap-1">
            <h2 className="font-display text-lg font-bold text-foreground">
              Canjear key
            </h2>
            <p className="text-[13px] leading-relaxed text-muted">
              Pega tu key de regalo para sumar días a tu plan.
            </p>
          </div>
        </div>

        <ClaimKey
          onClaimed={() =>
            queryClient.invalidateQueries({ queryKey: ["me"] })
          }
        />
      </div>
    </div>
  );
}
