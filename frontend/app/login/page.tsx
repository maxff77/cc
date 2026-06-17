"use client";

import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { siteConfig } from "@/config/site";
import { ContactPanel } from "@/components/contact-panel";
import { Logo, Mark } from "@/components/ui/logo";
import { RxBackdrop } from "@/components/ui/rx-backdrop";
import { CornerTicks } from "@/components/ui/auth-layout";
import { LabelCaps } from "@/components/ui/label-caps";
import { Field } from "@/components/ui/field";
import { Btn } from "@/components/ui/btn";
import { Icon } from "@/components/ui/icon";
import { Notice } from "@/components/ui/notice";

interface LoginResponse {
  id: number;
  email: string;
  role: string;
  tenant_id: number;
  home_path: string;
}

// Code → Spanish copy. AC2/AC4 strings are verbatim from the UX spec; we map on
// the client so the UI is stable even if a backend message changes.
const COPY: Record<string, string> = {
  invalid_credentials: "Correo o contraseña incorrectos.",
  account_blocked:
    "Tu cuenta está bloqueada. Escríbenos por Telegram para reactivarla.",
  too_many_attempts: "Demasiados intentos. Espera unos minutos.",
};

type Notice = { kind: "blocked" } | { kind: "banner"; message: string } | null;

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [credentialError, setCredentialError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setCredentialError(null);
    setNotice(null);
    setSubmitting(true);
    try {
      const res = await api.post<LoginResponse>("/api/auth/login", {
        email,
        password,
      });

      // Full navigation so the new session cookie is picked up by middleware.
      window.location.assign(res.home_path);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "invalid_credentials") {
          // AC2: inline field-level error; email stays filled; no redirect.
          setCredentialError(COPY.invalid_credentials);
        } else if (err.code === "plan_expired") {
          // lib/api.ts already routed to /expired (the hard-lockout page);
          // suppress the generic banner while that navigation lands.
        } else if (err.code === "account_blocked") {
          setNotice({ kind: "blocked" });
        } else if (err.code === "too_many_attempts") {
          setNotice({ kind: "banner", message: COPY.too_many_attempts });
        } else {
          setNotice({ kind: "banner", message: err.message });
        }
      } else {
        setNotice({
          kind: "banner",
          message: "No pudimos conectar. Intenta de nuevo.",
        });
      }
    } finally {
      // Always re-enable the form: window.location.assign() doesn't block, and
      // bfcache can restore this page (Back button) with its last rendered
      // state — a missed reset would leave the button stuck on "Entrando…".
      setSubmitting(false);
    }
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center px-5 py-10">
      <RxBackdrop />
      <div className="rx-enter relative z-[1] flex w-full max-w-[460px] flex-col items-center gap-6">
        {/* Brand + tagline */}
        <div className="flex flex-col items-center gap-3">
          <Logo priority maxWidth={340} />
          <div className="flex items-center gap-2.5">
            <span className="h-px w-5 bg-accent/60" />
            <LabelCaps className="tracking-[0.22em]">
              Seguridad · Control · Rendimiento
            </LabelCaps>
            <span className="h-px w-5 bg-[var(--magenta)] opacity-60" />
          </div>
        </div>

        {/* Branded card */}
        <div
          className="glow-soft relative w-full rounded-[18px] border border-border bg-surface px-7 pb-7 pt-[30px]"
          style={{ backgroundImage: "var(--brand-gradient-soft)" }}
        >
          <CornerTicks />

          <div className="mb-6 flex items-center justify-center gap-3">
            <span className="h-px flex-1 bg-[linear-gradient(90deg,transparent,var(--accent))]" />
            <h1 className="whitespace-nowrap font-display text-[19px] font-extrabold uppercase tracking-[0.18em] text-foreground">
              Iniciar sesión
            </h1>
            <span className="h-px flex-1 bg-[linear-gradient(90deg,var(--magenta),transparent)]" />
          </div>

          {notice?.kind === "banner" && (
            <Notice className="mb-4" status="danger">
              {notice.message}
            </Notice>
          )}
          {notice?.kind === "blocked" && (
            <ContactPanel className="mb-4" message={COPY.account_blocked} />
          )}

          <form className="flex flex-col gap-4" onSubmit={onSubmit}>
            <Field
              required
              autoComplete="email"
              icon="user"
              label="Correo"
              name="email"
              placeholder="tu@correo.com"
              type="email"
              value={email}
              onChange={setEmail}
            />
            <Field
              required
              autoComplete="current-password"
              error={credentialError}
              icon="lock"
              label="Contraseña"
              name="password"
              placeholder="••••••••"
              rightSlot={
                <button
                  aria-label={
                    showPassword ? "Ocultar contraseña" : "Mostrar contraseña"
                  }
                  className="rx-focus flex p-0 text-muted hover:text-foreground"
                  type="button"
                  onClick={() => setShowPassword((s) => !s)}
                >
                  <Icon name={showPassword ? "eyeOff" : "eye"} size={18} />
                </button>
              }
              type={showPassword ? "text" : "password"}
              value={password}
              onChange={(v) => {
                setPassword(v);
                if (credentialError) setCredentialError(null);
              }}
            />

            <Btn
              full
              className="mt-1 uppercase tracking-[0.14em]"
              disabled={submitting}
              iconRight="arrow"
              size="lg"
              type="submit"
              variant="primary"
            >
              {submitting ? "Entrando…" : "Iniciar sesión"}
            </Btn>

            <p className="m-0 flex items-center justify-center gap-2">
              <LabelCaps>Soporte Telegram</LabelCaps>
              <a
                className="rounded-[var(--radius-sm)] border border-[var(--field-border)] px-2 py-0.5 font-mono text-[12px] text-accent no-underline transition-colors hover:border-accent"
                href={siteConfig.contact.telegram}
                rel="noopener noreferrer"
                target="_blank"
              >
                @{siteConfig.contact.handle}
              </a>
            </p>
          </form>
        </div>

        <div className="flex flex-col items-center gap-2">
          <Mark size={30} />
          <span className="font-mono text-[11px] tracking-[0.1em] text-[var(--faint)]">
            RANGER-X CHECK © 2026
          </span>
        </div>
      </div>
    </main>
  );
}
