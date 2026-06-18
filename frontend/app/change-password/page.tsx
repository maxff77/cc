"use client";

import { useState } from "react";

import { api, ApiError } from "@/lib/api";
import { AuthLayout } from "@/components/ui/auth-layout";
import { Field } from "@/components/ui/field";
import { Btn } from "@/components/ui/btn";
import { Notice } from "@/components/ui/notice";

interface ChangePasswordResponse {
  home_path: string;
}

// Forced-password-change single screen (UX-DR16, Story 1.6). Reachable only
// with a valid session (middleware bounces no-cookie visitors to /login); a
// non-flagged user can render it but its POST will 403 — harmless edge.
export default function ChangePasswordPage() {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [currentError, setCurrentError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setCurrentError(null);
    setFieldError(null);
    setBanner(null);

    if (currentPassword.length === 0) {
      setCurrentError("Ingresa la contraseña temporal.");

      return;
    }

    if (newPassword.length < 8) {
      setFieldError("La contraseña debe tener al menos 8 caracteres.");

      return;
    }

    setSubmitting(true);
    try {
      const res = await api.post<ChangePasswordResponse>(
        "/api/auth/change-password",
        { current_password: currentPassword, new_password: newPassword },
      );

      // Full navigation so middleware re-evaluates the cleared flag.
      window.location.assign(res.home_path);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "password_reuse") {
          setFieldError(err.message);
        } else if (err.code === "invalid_credentials") {
          setCurrentError("La contraseña temporal no es correcta.");
        } else if (err.code === "not_authenticated") {
          window.location.assign("/login");
        } else if (err.code === "forbidden") {
          // Flag already cleared (change completed in another tab): the session
          // is fully valid, so send the user home instead of stranding them.
          window.location.assign("/app");
        } else {
          // Pydantic 422s carry no {code,message} body, so err.message can be
          // empty — never render a blank banner.
          setBanner(err.message || "La contraseña no es válida.");
        }
      } else {
        setBanner("No pudimos conectar. Intenta de nuevo.");
      }
    } finally {
      // Always re-enable: window.location.assign() doesn't block, and bfcache
      // can restore this page with its last rendered state (login lesson).
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      subtitle="Elige una contraseña nueva para continuar."
      title="Contraseña nueva"
    >
      {banner && (
        <Notice className="mb-4" status="danger">
          {banner}
        </Notice>
      )}
      <form className="flex flex-col gap-4" onSubmit={onSubmit}>
        <Field
          required
          autoComplete="current-password"
          error={currentError}
          icon="lock"
          label="Contraseña temporal"
          name="current_password"
          placeholder="••••••••"
          type="password"
          value={currentPassword}
          onChange={(v) => {
            setCurrentPassword(v);
            if (currentError) setCurrentError(null);
          }}
        />
        <div>
          <Field
            required
            autoComplete="new-password"
            error={fieldError}
            icon="lock"
            label="Contraseña nueva"
            name="new_password"
            placeholder="••••••••"
            type="password"
            value={newPassword}
            onChange={(v) => {
              setNewPassword(v);
              if (fieldError) setFieldError(null);
            }}
          />
          {/* Always-visible rule (ui-polish-spec §3.2): never reveal the 8-char
              minimum only after failing. */}
          {!fieldError && (
            <p className="mt-1.5 px-0.5 text-[12px] text-muted">
              Mínimo 8 caracteres.
            </p>
          )}
        </div>

        <Btn
          full
          className="mt-1"
          disabled={submitting}
          icon="check"
          type="submit"
          variant="primary"
        >
          {submitting ? "Guardando…" : "Guardar"}
        </Btn>
      </form>
    </AuthLayout>
  );
}
