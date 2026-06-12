"use client";

import { useState } from "react";
import {
  Alert,
  Button,
  Description,
  Form,
  TextField,
  Label,
  Input,
  FieldError,
} from "@heroui/react";

import { api, ApiError } from "@/lib/api";
import { SectionCard } from "@/components/ui/section-card";

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
          // Flag already cleared (change completed in another tab): the
          // session is fully valid, so send the user home instead of
          // stranding them on a dead-end banner.
          window.location.assign("/");
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
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="flex w-full max-w-sm flex-col gap-5">
        <span className="self-center font-mono text-2xl font-extrabold tracking-[-0.03em]">
          CC
        </span>

        <h1 className="text-center text-lg font-bold tracking-[-0.01em]">
          Contraseña nueva
        </h1>

        <p className="text-center text-sm text-muted">
          Elige una contraseña nueva para continuar.
        </p>

        {banner && <Alert status="danger">{banner}</Alert>}

        <SectionCard legend="CONTRASEÑA">
          <Form className="flex flex-col gap-4" onSubmit={onSubmit}>
            <TextField
              isRequired
              className="flex flex-col gap-1"
              isInvalid={currentError !== null}
              name="current_password"
              type="password"
              value={currentPassword}
              onChange={(v) => {
                setCurrentPassword(v);
                if (currentError) setCurrentError(null);
              }}
            >
              <Label>Contraseña temporal</Label>
              <Input placeholder="••••••••" />
              {currentError && <FieldError>{currentError}</FieldError>}
            </TextField>

            <TextField
              isRequired
              className="flex flex-col gap-1"
              isInvalid={fieldError !== null}
              name="new_password"
              type="password"
              value={newPassword}
              onChange={(v) => {
                setNewPassword(v);
                if (fieldError) setFieldError(null);
              }}
            >
              <Label>Contraseña nueva</Label>
              <Input placeholder="••••••••" />
              {/* Always-visible rule (ui-polish-spec §3.2): never reveal the
                8-char minimum only after failing. */}
              <Description>Mínimo 8 caracteres.</Description>
              {fieldError && <FieldError>{fieldError}</FieldError>}
            </TextField>

            <Button
              className="mt-2"
              isDisabled={submitting}
              type="submit"
              variant="primary"
            >
              {submitting ? "Guardando…" : "Guardar"}
            </Button>
          </Form>
        </SectionCard>
      </div>
    </main>
  );
}
