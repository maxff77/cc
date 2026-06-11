"use client";

import { useState } from "react";
import {
  Alert,
  Button,
  Form,
  TextField,
  Label,
  Input,
  FieldError,
} from "@heroui/react";

import { api, ApiError } from "@/lib/api";
import { ContactPanel } from "@/components/contact-panel";

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
    "Tu cuenta está bloqueada. Escríbenos por WhatsApp o Telegram para reactivarla.",
  too_many_attempts: "Demasiados intentos. Espera unos minutos.",
};

type Notice = { kind: "blocked" } | { kind: "banner"; message: string } | null;

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="w-full max-w-sm">
        <h1 className="mb-6 text-center text-2xl font-semibold">
          Iniciar sesión
        </h1>

        {notice?.kind === "banner" && (
          <Alert className="mb-4" status="danger">
            {notice.message}
          </Alert>
        )}

        {notice?.kind === "blocked" && (
          <ContactPanel className="mb-4" message={COPY.account_blocked} />
        )}

        <Form className="flex flex-col gap-4" onSubmit={onSubmit}>
          <TextField
            isRequired
            className="flex flex-col gap-1"
            name="email"
            type="email"
            value={email}
            onChange={setEmail}
          >
            <Label>Correo</Label>
            <Input placeholder="tu@correo.com" />
          </TextField>

          <TextField
            isRequired
            className="flex flex-col gap-1"
            isInvalid={credentialError !== null}
            name="password"
            type="password"
            value={password}
            onChange={(v) => {
              setPassword(v);
              if (credentialError) setCredentialError(null);
            }}
          >
            <Label>Contraseña</Label>
            <Input placeholder="••••••••" />
            {credentialError && <FieldError>{credentialError}</FieldError>}
          </TextField>

          <Button
            className="mt-2"
            isDisabled={submitting}
            type="submit"
            variant="primary"
          >
            {submitting ? "Entrando…" : "Entrar"}
          </Button>
        </Form>
      </div>
    </main>
  );
}
