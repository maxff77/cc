"use client";

import { useEffect } from "react";

import { ContactPanel } from "@/components/contact-panel";
import { AuthLayout } from "@/components/ui/auth-layout";
import { api } from "@/lib/api";

// Hard-lockout surface for an expired client (UX flow 4: never a dead-end —
// always the external renewal channel). No nav, no partial access, no actions
// beyond the two contact buttons. The copy is verbatim from the AC and matches
// the backend `plan_expired` message by design.
const MESSAGE =
  "Tu plan venció. Escríbenos por WhatsApp o Telegram y lo reactivamos.";

export default function ExpiredPage() {
  // /expired sits outside the middleware matcher (a freshly-locked-out client
  // has had their session revoked, so the page must load without one). That
  // also means an ACTIVE user can land here via Back button or stale bookmark
  // after a renewal — probe /me and bounce anyone with a valid session home.
  useEffect(() => {
    api
      .get<{ role: string }>("/api/auth/me")
      .then((me) => {
        window.location.replace(me.role === "client" ? "/" : "/admin/users");
      })
      .catch(() => {
        // 401 (the expected case for a locked-out visitor) or network error →
        // stay on the lockout page.
      });
  }, []);

  return (
    <AuthLayout title="Tu plan venció">
      <ContactPanel message={MESSAGE} />
    </AuthLayout>
  );
}
