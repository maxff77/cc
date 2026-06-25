"use client";

// Cookie-vault in a modal (cookie-vault-modal spec). The cockpit no longer
// renders the CookieManager inline under the send form — a "Cookies (N)" button
// (see send-form) opens this instead, so the column reads más aireado on mobile
// and the vault is reachable DURING a send, not only while idle.
//
// Reuses KeyModal's lightweight backdrop idiom (backdrop click + Escape close,
// initial input focus) rather than a focus-trapping primitive — one short form
// is enough. The body is the unchanged <CookieManager>: its SectionCard chrome
// reads fine as the dialog panel; a max-h + scroll keeps a full (50-cookie) list
// usable on a phone.
import { useEffect, useRef } from "react";

import { CookieManager } from "@/components/batch/cookie-manager";
import { Icon } from "@/components/ui/icon";

export function CookieModal({
  gateId,
  open,
  onClose,
}: {
  gateId: number;
  open: boolean;
  onClose: () => void;
}) {
  const cardRef = useRef<HTMLDivElement>(null);

  // Focus the cookie field once on open. Keyed on `open` ONLY — `onClose` is a
  // fresh closure on every parent render and the cockpit re-renders on each WS
  // frame during a live send, so depending on it here would re-run focus() every
  // few seconds and yank the caret out of whatever field the user is typing in.
  useEffect(() => {
    if (open) cardRef.current?.querySelector<HTMLElement>("input")?.focus();
  }, [open]);

  // Escape closes. Re-subscribing when the `onClose` identity changes is cheap
  // and has no visible effect (unlike re-focusing).
  useEffect(() => {
    if (!open) return;

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);

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
        className="rx-enter relative w-full max-w-md"
        role="dialog"
      >
        <button
          aria-label="Cerrar"
          className="rx-focus absolute right-3 top-3 z-10 inline-flex size-7 items-center justify-center rounded-[var(--radius-sm)] border border-border bg-surface-secondary text-muted transition-colors hover:text-foreground"
          type="button"
          onClick={onClose}
        >
          <Icon name="close" size={15} />
        </button>

        <div className="max-h-[85vh] overflow-y-auto">
          <CookieManager gateId={gateId} />
        </div>
      </div>
    </div>
  );
}
