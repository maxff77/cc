"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  FieldError,
  Form,
  Input,
  Label,
  Spinner,
  Table,
  TextField,
} from "@heroui/react";

import { api, ApiError } from "@/lib/api";

// Local response shapes mirror the backend admin schemas (snake_case,
// end-to-end). The generated types/api.ts also carries them after
// `npm run generate:api`; we keep these explicit per the login-page idiom.
interface UserOut {
  id: number;
  email: string;
  role: string;
  tenant_id: number;
  expires_at: string | null;
  is_blocked: boolean;
}

interface UserListResponse {
  items: UserOut[];
}

interface Me {
  id: number;
  email: string;
  role: string;
  tenant_id: number;
}

const USERS_KEY = ["admin-users"] as const;
const ME_KEY = ["me"] as const;

// `type="number"` still lets through strings Number.parseInt silently
// mis-reads ("1e2" → 1, "30.5" → 30) or that serialize as null (NaN) — gate on
// plain digits before trusting the value.
function isPositiveInt(value: string): boolean {
  return /^\d+$/.test(value.trim()) && Number(value) > 0;
}

function formatExpiry(iso: string | null): string {
  if (!iso) return "—";

  return new Date(iso).toLocaleDateString("es", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export default function AdminUsersPage() {
  const queryClient = useQueryClient();

  const me = useQuery({
    queryKey: ME_KEY,
    queryFn: () => api.get<Me>("/api/auth/me"),
  });
  const isOwner = me.data?.role === "owner";

  const users = useQuery({
    queryKey: USERS_KEY,
    queryFn: () => api.get<UserListResponse>("/api/admin/users"),
  });

  async function logout() {
    try {
      await api.post("/api/auth/logout");
    } finally {
      // Full navigation so middleware re-reads the cleared cookie.
      window.location.assign("/login");
    }
  }

  return (
    <main className="mx-auto w-full max-w-4xl px-6 py-10">
      <header className="mb-8 flex items-center justify-between">
        <div className="flex items-baseline gap-4">
          <h1 className="text-2xl font-semibold">Gestión de usuarios</h1>
          {isOwner && (
            <Link
              className="text-sm text-default-500 underline"
              href="/admin/gates"
            >
              Gates
            </Link>
          )}
        </div>
        <Button variant="secondary" onPress={logout}>
          Cerrar sesión
        </Button>
      </header>

      <CreateUserForm
        kind="client"
        title="Crear cliente"
        onCreated={() => queryClient.invalidateQueries({ queryKey: USERS_KEY })}
      />

      {isOwner && (
        <CreateUserForm
          kind="admin"
          title="Crear admin"
          onCreated={() =>
            queryClient.invalidateQueries({ queryKey: USERS_KEY })
          }
        />
      )}

      <section className="mt-8">
        {users.isLoading && (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        )}

        {users.isError && (
          <Alert status="danger">
            No pudimos cargar los usuarios. Recarga la página.
          </Alert>
        )}

        {users.data && (
          <Table>
            <Table.Content aria-label="Usuarios">
              <Table.Header>
                <Table.Column isRowHeader>Correo</Table.Column>
                <Table.Column>Rol</Table.Column>
                <Table.Column>Vence</Table.Column>
                <Table.Column>Estado</Table.Column>
                <Table.Column>Acciones</Table.Column>
              </Table.Header>
              <Table.Body
                items={users.data.items}
                renderEmptyState={() => "Todavía no hay clientes."}
              >
                {(u) => (
                  <Table.Row id={u.id}>
                    <Table.Cell>{u.email}</Table.Cell>
                    <Table.Cell>{u.role}</Table.Cell>
                    <Table.Cell>{formatExpiry(u.expires_at)}</Table.Cell>
                    <Table.Cell>
                      {u.role !== "client" ? (
                        "—"
                      ) : u.is_blocked ? (
                        <span className="font-medium text-danger">
                          Bloqueado
                        </span>
                      ) : (
                        <span className="text-default-500">Activo</span>
                      )}
                    </Table.Cell>
                    <Table.Cell>
                      {u.role === "client" ? (
                        <div className="flex flex-col gap-2">
                          {/* Entry point of the cross-tenant support view
                              (Story 3.6, Flow 5) — clients only: the support
                              target is a client's tenant. */}
                          <Link
                            className="text-sm text-default-500 underline"
                            href={`/admin/tenants/${u.tenant_id}`}
                          >
                            Sesiones
                          </Link>
                          <ClientLifecycleActions
                            user={u}
                            onChanged={() =>
                              queryClient.invalidateQueries({
                                queryKey: USERS_KEY,
                              })
                            }
                          />
                        </div>
                      ) : isOwner && u.role === "admin" ? (
                        <DeleteAdminAction
                          email={u.email}
                          userId={u.id}
                          onDeleted={() =>
                            queryClient.invalidateQueries({
                              queryKey: USERS_KEY,
                            })
                          }
                        />
                      ) : (
                        "—"
                      )}
                    </Table.Cell>
                  </Table.Row>
                )}
              </Table.Body>
            </Table.Content>
          </Table>
        )}
      </section>
    </main>
  );
}

// --- Create (client | admin) ---------------------------------------------

function CreateUserForm({
  kind,
  title,
  onCreated,
}: {
  kind: "client" | "admin";
  title: string;
  onCreated: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [planDays, setPlanDays] = useState("30");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = { email, password, role: kind };

      if (kind === "client") payload.plan_days = Number(planDays);

      return api.post<UserOut>("/api/admin/users", payload);
    },
    onSuccess: () => {
      setEmail("");
      setPassword("");
      setPlanDays("30");
      onCreated();
    },
    onError: (err) => {
      // The backend already sends user-facing Spanish in `message`; route it to
      // the relevant field by `code` instead of re-stating the copy here.
      if (err instanceof ApiError) {
        if (err.code === "email_taken") setEmailError(err.message);
        else if (err.code === "invalid_plan_days") setPlanError(err.message);
        else setBanner(err.message);
      } else {
        setBanner("No pudimos conectar. Intenta de nuevo.");
      }
    },
  });

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setEmailError(null);
    setPlanError(null);
    setBanner(null);

    if (kind === "client" && !isPositiveInt(planDays)) {
      setPlanError("Indica un número entero de días.");

      return;
    }
    mutation.mutate();
  }

  return (
    <section className="mb-6 rounded-lg border border-default/30 p-4">
      <h2 className="mb-3 text-lg font-medium">{title}</h2>

      {banner && (
        <Alert className="mb-3" status="danger">
          {banner}
        </Alert>
      )}

      <Form
        className="flex flex-col gap-3 sm:flex-row sm:items-end"
        onSubmit={onSubmit}
      >
        <TextField
          isRequired
          className="flex flex-1 flex-col gap-1"
          isInvalid={emailError !== null}
          name="email"
          type="email"
          value={email}
          onChange={(v) => {
            setEmail(v);
            if (emailError) setEmailError(null);
          }}
        >
          <Label>Correo</Label>
          <Input placeholder="cliente@correo.com" />
          {emailError && <FieldError>{emailError}</FieldError>}
        </TextField>

        <TextField
          isRequired
          className="flex flex-1 flex-col gap-1"
          name="password"
          type="password"
          value={password}
          onChange={setPassword}
        >
          <Label>Contraseña</Label>
          <Input placeholder="••••••••" />
        </TextField>

        {kind === "client" && (
          <TextField
            isRequired
            className="flex flex-col gap-1 sm:w-32"
            isInvalid={planError !== null}
            name="plan_days"
            type="number"
            value={planDays}
            onChange={(v) => {
              setPlanDays(v);
              if (planError) setPlanError(null);
            }}
          >
            <Label>Días del plan</Label>
            <Input placeholder="30" />
            {planError && <FieldError>{planError}</FieldError>}
          </TextField>
        )}

        <Button
          className="sm:mb-1"
          isDisabled={mutation.isPending}
          type="submit"
          variant="primary"
        >
          {mutation.isPending ? "Creando…" : "Crear"}
        </Button>
      </Form>
    </section>
  );
}

// --- Delete admin (owner only) -------------------------------------------

function DeleteAdminAction({
  email,
  userId,
  onDeleted,
}: {
  email: string;
  userId: number;
  onDeleted: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.delete<void>(`/api/admin/users/${userId}`),
    onSuccess: () => {
      setConfirming(false);
      setError(null);
      onDeleted();
    },
    onError: (err) => {
      // Surface failures (e.g. the admin was already removed elsewhere) instead
      // of leaving the button stuck on "Eliminando…" with no feedback.
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos eliminar. Intenta de nuevo.",
      );
    },
  });

  if (!confirming) {
    return (
      <Button size="sm" variant="secondary" onPress={() => setConfirming(true)}>
        Eliminar
      </Button>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <span className="text-sm">¿Eliminar este admin? ({email})</span>
      {error && <span className="text-sm text-danger">{error}</span>}
      <div className="flex gap-2">
        <Button
          isDisabled={mutation.isPending}
          size="sm"
          variant="danger"
          onPress={() => mutation.mutate()}
        >
          {mutation.isPending ? "Eliminando…" : "Sí, eliminar"}
        </Button>
        <Button
          isDisabled={mutation.isPending}
          size="sm"
          variant="secondary"
          onPress={() => setConfirming(false)}
        >
          Cancelar
        </Button>
      </div>
    </div>
  );
}

// --- Client lifecycle: renew + block/unblock (Story 1.5) -----------------

function ClientLifecycleActions({
  user,
  onChanged,
}: {
  user: UserOut;
  onChanged: () => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <RenewAction userId={user.id} onChanged={onChanged} />
      <BlockAction user={user} onChanged={onChanged} />
      <ResetPasswordAction user={user} onChanged={onChanged} />
    </div>
  );
}

function RenewAction({
  userId,
  onChanged,
}: {
  userId: number;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [days, setDays] = useState("");
  const [date, setDate] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => {
      // Exactly one mode: días → plan_days; otherwise the date as end-of-day
      // in the ADMIN'S timezone (not hardcoded Z) — formatExpiry renders in
      // local time, so this keeps the Vence column showing the picked day.
      const payload = days.trim()
        ? { plan_days: Number(days) }
        : { expires_at: new Date(`${date}T23:59:59`).toISOString() };

      return api.post<UserOut>(`/api/admin/users/${userId}/renew`, payload);
    },
    onSuccess: () => {
      setOpen(false);
      setDays("");
      setDate("");
      setError(null);
      onChanged();
    },
    onError: (err) => {
      // Backend sends Spanish in `message` for invalid_renewal / invalid_plan_days.
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos renovar. Intenta de nuevo.",
      );
    },
  });

  function submit() {
    setError(null);
    const hasDays = days.trim() !== "";
    const hasDate = date.trim() !== "";

    if (hasDays === hasDate) {
      setError("Completa solo Días o solo Hasta.");

      return;
    }
    if (hasDays && !isPositiveInt(days)) {
      setError("Indica un número entero de días.");

      return;
    }
    mutation.mutate();
  }

  if (!open) {
    return (
      <Button size="sm" variant="secondary" onPress={() => setOpen(true)}>
        Renovar
      </Button>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <TextField
          className="flex flex-col gap-1 sm:w-24"
          name="plan_days"
          type="number"
          value={days}
          onChange={(v) => {
            setDays(v);
            if (error) setError(null);
          }}
        >
          <Label>Días</Label>
          <Input placeholder="30" />
        </TextField>

        <TextField
          className="flex flex-col gap-1"
          name="expires_at"
          type="date"
          value={date}
          onChange={(v) => {
            setDate(v);
            if (error) setError(null);
          }}
        >
          <Label>Hasta</Label>
          <Input />
        </TextField>
      </div>

      {error && <span className="text-sm text-danger">{error}</span>}

      <div className="flex gap-2">
        <Button
          isDisabled={mutation.isPending}
          size="sm"
          variant="primary"
          onPress={submit}
        >
          {mutation.isPending ? "Renovando…" : "Renovar"}
        </Button>
        <Button
          isDisabled={mutation.isPending}
          size="sm"
          variant="secondary"
          onPress={() => {
            setOpen(false);
            setError(null);
          }}
        >
          Cancelar
        </Button>
      </div>
    </div>
  );
}

function BlockAction({
  user,
  onChanged,
}: {
  user: UserOut;
  onChanged: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const action = user.is_blocked ? "unblock" : "block";

  const mutation = useMutation({
    mutationFn: () =>
      api.post<UserOut>(`/api/admin/users/${user.id}/${action}`),
    onSuccess: () => {
      setConfirming(false);
      setError(null);
      onChanged();
    },
    onError: (err) => {
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos completar la acción. Intenta de nuevo.",
      );
    },
  });

  // Unblock restores access (not destructive) → acts on a single press.
  if (user.is_blocked) {
    return (
      <div className="flex flex-col gap-1">
        <Button
          isDisabled={mutation.isPending}
          size="sm"
          variant="secondary"
          onPress={() => mutation.mutate()}
        >
          {mutation.isPending ? "Desbloqueando…" : "Desbloquear"}
        </Button>
        {error && <span className="text-sm text-danger">{error}</span>}
      </div>
    );
  }

  // Block closes the client's live session → inline confirm (DeleteAdminAction idiom).
  if (!confirming) {
    return (
      <Button size="sm" variant="danger" onPress={() => setConfirming(true)}>
        Bloquear
      </Button>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <span className="text-sm">
        ¿Bloquear a {user.email}? Su sesión se cerrará al instante.
      </span>
      {error && <span className="text-sm text-danger">{error}</span>}
      <div className="flex gap-2">
        <Button
          isDisabled={mutation.isPending}
          size="sm"
          variant="danger"
          onPress={() => mutation.mutate()}
        >
          {mutation.isPending ? "Bloqueando…" : "Sí, bloquear"}
        </Button>
        <Button
          isDisabled={mutation.isPending}
          size="sm"
          variant="secondary"
          onPress={() => setConfirming(false)}
        >
          Cancelar
        </Button>
      </div>
    </div>
  );
}

// --- Reset password (Story 1.6) -------------------------------------------

function ResetPasswordAction({
  user,
  onChanged,
}: {
  user: UserOut;
  onChanged: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  // The EXACTLY-ONCE display (AC1): lives only in local state; "Listo" clears
  // it and it is unrecoverable by design (only a new reset produces a new one).
  const [tempPassword, setTempPassword] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.post<{ temp_password: string }>(
        `/api/admin/users/${user.id}/reset-password`,
      ),
    onSuccess: (res) => {
      setConfirming(false);
      setError(null);
      setTempPassword(res.temp_password);
      // onChanged() (USERS_KEY invalidation) is deferred to "Listo": a
      // refetch-driven remount here could destroy the one-time password
      // before the admin copies it.
    },
    onError: (err) => {
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos completar la acción. Intenta de nuevo.",
      );
    },
  });

  async function copy() {
    if (!tempPassword) return;
    try {
      await navigator.clipboard.writeText(tempPassword);
      setError(null);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard can reject (permissions, non-secure context): never fail
      // silently on a value that won't be shown again.
      setError("No se pudo copiar. Selecciónala y cópiala manualmente.");
    }
  }

  function dismiss() {
    setTempPassword(null);
    setError(null);
    // Drop the response from the mutation cache too — otherwise the plaintext
    // outlives the dismissal in mutation.data.
    mutation.reset();
    onChanged();
  }

  if (tempPassword) {
    return (
      <div className="flex flex-col gap-2">
        <span className="font-mono text-sm">{tempPassword}</span>
        <span className="text-sm text-default-500">
          Cópiala ahora: no volverá a mostrarse.
        </span>
        {error && <span className="text-sm text-danger">{error}</span>}
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" onPress={copy}>
            {copied ? "Copiada" : "Copiar"}
          </Button>
          <Button size="sm" variant="primary" onPress={dismiss}>
            Listo
          </Button>
        </div>
      </div>
    );
  }

  if (!confirming) {
    return (
      <Button size="sm" variant="secondary" onPress={() => setConfirming(true)}>
        Resetear
      </Button>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <span className="text-sm">
        ¿Resetear la contraseña de {user.email}? Su sesión se cerrará al
        instante.
      </span>
      {error && <span className="text-sm text-danger">{error}</span>}
      <div className="flex gap-2">
        <Button
          isDisabled={mutation.isPending}
          size="sm"
          variant="danger"
          onPress={() => mutation.mutate()}
        >
          {mutation.isPending ? "Reseteando…" : "Sí, resetear"}
        </Button>
        <Button
          isDisabled={mutation.isPending}
          size="sm"
          variant="secondary"
          onPress={() => setConfirming(false)}
        >
          Cancelar
        </Button>
      </div>
    </div>
  );
}
