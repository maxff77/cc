"use client";

import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { Logo, Mark } from "@/components/ui/logo";
import { VersionPill } from "@/components/ui/version-badge";
import { RxBackdrop } from "@/components/ui/rx-backdrop";
import { LabelCaps } from "@/components/ui/label-caps";
import { Field } from "@/components/ui/field";
import { Btn } from "@/components/ui/btn";
import { Icon } from "@/components/ui/icon";
import { Notice } from "@/components/ui/notice";

interface RegisterResponse {
  id: number;
  email: string;
  role: string;
  tenant_id: number;
  home_path: string;
}

// Mirror the backend bounds (auth.py _PASSWORD_MIN / _PASSWORD_MAX). Guarding
// client-side keeps an out-of-range password from reaching the API as an opaque
// 422 (the {detail} body the fetch wrapper can only render as a generic "error
// inesperado").
const PASSWORD_MIN = 8;
const PASSWORD_MAX = 128;

// Code → Spanish copy, mapped client-side so the UI is stable if a backend
// message changes (same pattern as the login page).
const COPY: Record<string, string> = {
  email_taken: "Ya existe una cuenta con ese correo.",
  too_many_attempts: "Demasiados intentos. Espera unos minutos.",
};

export default function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPasswordError(null);
    setBanner(null);

    if (password.length < PASSWORD_MIN) {
      setPasswordError(
        `La contraseña debe tener al menos ${PASSWORD_MIN} caracteres.`,
      );

      return;
    }

    if (password.length > PASSWORD_MAX) {
      setPasswordError(
        `La contraseña no puede superar ${PASSWORD_MAX} caracteres.`,
      );

      return;
    }

    setSubmitting(true);
    try {
      const res = await api.post<RegisterResponse>("/api/auth/register", {
        email,
        password,
      });

      // Full navigation so the new session cookie is picked up by middleware,
      // which routes the no-plan session on to /expired (the contact surface).
      window.location.assign(res.home_path);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "email_taken") {
          setBanner(COPY.email_taken);
        } else if (err.code === "too_many_attempts") {
          setBanner(COPY.too_many_attempts);
        } else {
          setBanner(err.message);
        }
      } else {
        setBanner("No pudimos conectar. Intenta de nuevo.");
      }
    } finally {
      // Always re-enable: window.location.assign() doesn't block and bfcache
      // can restore this page (Back) with its last rendered state.
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
          <LabelCaps className="tracking-[0.22em]">
            Seguridad · Control · Rendimiento
          </LabelCaps>
        </div>

        {/* Branded card */}
        <div
          className="glow-soft relative w-full rounded-[18px] border border-border bg-surface px-7 pb-7 pt-7"
          style={{ backgroundImage: "var(--brand-gradient-soft)" }}
        >
          <h1 className="mb-6 text-center font-display text-[19px] font-extrabold uppercase tracking-[0.18em] text-foreground">
            Crear cuenta
          </h1>

          {banner && (
            <Notice className="mb-4" status="danger">
              {banner}
            </Notice>
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
              autoComplete="new-password"
              error={passwordError}
              icon="lock"
              label="Contraseña"
              name="password"
              placeholder="Mínimo 8 caracteres"
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
                if (passwordError) setPasswordError(null);
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
              {submitting ? "Creando…" : "Crear cuenta"}
            </Btn>

            <p className="m-0 flex items-center justify-center gap-2">
              <LabelCaps>¿Ya tienes cuenta?</LabelCaps>
              <a
                className="rounded-[var(--radius-sm)] border border-[var(--field-border)] px-2 py-0.5 font-mono text-[12px] text-accent no-underline transition-colors hover:border-accent"
                href="/login"
              >
                Iniciar sesión
              </a>
            </p>
          </form>
        </div>

        <div className="flex flex-col items-center gap-2">
          <Mark size={30} />
          <span className="font-mono text-[11px] tracking-[0.1em] text-muted">
            RANGER-X CHECK © 2026
          </span>
          <VersionPill />
        </div>
      </div>
    </main>
  );
}
