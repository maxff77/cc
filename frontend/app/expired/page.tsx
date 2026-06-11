"use client";

import { Button } from "@heroui/react";

import { siteConfig } from "@/config/site";

// Hard-lockout surface for an expired client (UX flow 4: never a dead-end —
// always the external renewal channel). No nav, no partial access, no actions
// beyond the two contact buttons. The copy is verbatim from the AC and matches
// the backend `plan_expired` message by design.
const MESSAGE =
  "Tu plan venció. Escríbenos por WhatsApp o Telegram y lo reactivamos.";

export default function ExpiredPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="w-full max-w-sm">
        <h1 className="mb-6 text-center text-2xl font-semibold">
          Tu plan venció
        </h1>

        <div className="flex flex-col gap-3 rounded-lg border border-danger/40 bg-danger/10 p-4">
          <p className="text-sm">{MESSAGE}</p>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              onPress={() =>
                window.open(
                  siteConfig.contact.whatsapp,
                  "_blank",
                  "noopener,noreferrer",
                )
              }
            >
              WhatsApp
            </Button>
            <Button
              variant="secondary"
              onPress={() =>
                window.open(
                  siteConfig.contact.telegram,
                  "_blank",
                  "noopener,noreferrer",
                )
              }
            >
              Telegram
            </Button>
          </div>
        </div>
      </div>
    </main>
  );
}
