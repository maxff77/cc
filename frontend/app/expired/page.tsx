"use client";

import { useEffect } from "react";

import { Btn } from "@/components/ui/btn";
import { ContactPanel } from "@/components/contact-panel";
import { AuthLayout } from "@/components/ui/auth-layout";
import { ApiError, api } from "@/lib/api";

// Hard-lockout surface for any client WITHOUT an active plan (UX flow 4: never a
// dead-end — always the external activation channel). No nav, no partial access,
// no actions beyond the contact buttons and the manual login fallback. The copy
// is intentionally neutral so it fits BOTH a freshly self-registered account
// (never had a plan) and a client whose plan expired — both reach here via the
// same repeatable 403 plan_expired gate.
const MESSAGE =
  "Tu cuenta no tiene un plan activo. Escríbenos por Telegram para activarlo.";

// How often the lockout page re-checks whether the plan was renewed. Light load:
// only a handful of clients are ever locked out at once and /me is a cheap call.
const POLL_MS = 10_000;

export default function ExpiredPage() {
  // /expired sits outside the middleware matcher, and an expired client KEEPS a
  // valid (but gated) session cookie, so this page can poll /me to detect a
  // renewal and recover automatically. While the plan is expired /me answers
  // 403 plan_expired (a repeatable, non-revoking 403); the instant an admin
  // renews it flips to 200 and we send the client back into the app — no manual
  // re-login. A 401 means the session itself died (SESSION_TTL elapsed, or a
  // block revoked it) → route to /login so they can re-auth. This also bounces
  // an ACTIVE user who lands here via Back button or a stale bookmark.
  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      try {
        const me = await api.get<{ role: string }>("/api/auth/me");

        if (cancelled) return;
        window.location.replace(me.role === "client" ? "/" : "/admin/users");
      } catch (err) {
        if (cancelled || !(err instanceof ApiError)) return;
        // 401 → session gone, log in again. 403 password_change_required → the
        // plan is active again but a forced password change is now due, so route
        // there (the expiry gate runs BEFORE the flag gate, so this code only
        // surfaces once the plan is renewed). 403 plan_expired / anything else →
        // still locked, keep waiting for the next poll.
        if (err.status === 401) {
          window.location.replace("/login");
        } else if (err.status === 403 && err.code === "password_change_required") {
          window.location.replace("/change-password");
        }
      }
    };

    void check();
    const id = window.setInterval(() => void check(), POLL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  return (
    <AuthLayout title="Activa tu plan">
      <ContactPanel message={MESSAGE} />
      <Btn
        className="mt-4"
        full
        variant="ghost"
        onClick={() => window.location.replace("/login")}
      >
        Iniciar sesión
      </Btn>
    </AuthLayout>
  );
}
