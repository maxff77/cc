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
  onSaved,
}: {
  gateId: number;
  open: boolean;
  onClose: () => void;
  // Fired after a cookie is stored — the host closes this modal and resumes a
  // stalled send (cookie-paste-autosave-resume).
  onSaved?: () => void;
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
    <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-5">
      <button
        aria-label="Cerrar"
        className="absolute inset-0 cursor-default backdrop-blur-sm"
        style={{ background: "rgba(6,4,12,.6)" }}
        tabIndex={-1}
        type="button"
        onClick={onClose}
      />
      <div
        ref={cardRef}
        aria-modal="true"
        className="rx-enter relative flex w-full max-h-[90%] flex-col rounded-t-[20px] rounded-b-none p-[18px_16px_22px] shadow-[0_-20px_60px_rgba(0,0,0,0.5)] sm:max-h-[86%] sm:max-w-[430px] sm:rounded-[18px] sm:p-[22px] sm:shadow-[0_30px_70px_rgba(0,0,0,0.5)]"
        role="dialog"
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border-strong)",
        }}
      >
        <button
          aria-label="Cerrar"
          className="rx-focus absolute right-[18px] top-[18px] z-10 inline-flex items-center justify-center transition-colors hover:text-foreground sm:right-[22px] sm:top-[22px]"
          style={{
            width: 32,
            height: 32,
            borderRadius: 9,
            background: "var(--surface-secondary)",
            border: "1px solid var(--border)",
            color: "var(--muted)",
          }}
          type="button"
          onClick={onClose}
        >
          <Icon name="close" size={15} />
        </button>

        <CookieManager gateId={gateId} onSaved={onSaved} />
      </div>
    </div>
  );
}
