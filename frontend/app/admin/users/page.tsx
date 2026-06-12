"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  AlertDialog,
  Button,
  FieldError,
  Form,
  Input,
  Label,
  Table,
  TextField,
} from "@heroui/react";

import { api, ApiError } from "@/lib/api";
import { AdminShell } from "@/components/ui/admin-shell";
import { EmptyState } from "@/components/ui/empty-state";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";

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

  return (
    <AdminShell gatesVisible={isOwner} title="Usuarios">
      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        {/* Left zone: creation forms (sticky on desktop). */}
        <div className="flex flex-col gap-5 lg:sticky lg:top-6 lg:self-start">
          <CreateUserForm
            kind="client"
            title="Crear cliente"
            onCreated={() =>
              queryClient.invalidateQueries({ queryKey: USERS_KEY })
            }
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

          {/* Owner knob: admission-control cap (Story 4.2). */}
          {isOwner && <AdmissionControlCard />}
        </div>

        {/* Right zone: the users table. */}
        <SectionCard legend="USUARIOS" padding="none">
          {users.isLoading && <PanelSkeleton rows={5} />}

          {users.isError && (
            <Alert className="m-3" status="danger">
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
                  renderEmptyState={() => (
                    <EmptyState
                      eyebrow="Usuarios"
                      message="Todavía no hay clientes."
                    />
                  )}
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
                          <span className="text-muted">Activo</span>
                        )}
                      </Table.Cell>
                      <Table.Cell>
                        {u.role === "client" ? (
                          <div className="flex flex-col gap-2">
                            {/* Entry point of the cross-tenant support view
                                (Story 3.6, Flow 5) — clients only: the support
                                target is a client's tenant. */}
                            <Link
                              className="text-sm text-muted underline hover:text-foreground"
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
        </SectionCard>
      </div>
    </AdminShell>
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
    // legendAs="h2": the legend replaces the old "Crear cliente"/"Crear
    // admin" h2 headings — keep the document outline under the page h1.
    <SectionCard legend={title} legendAs="h2">
      {banner && (
        <Alert className="mb-3" status="danger">
          {banner}
        </Alert>
      )}

      <Form className="flex flex-col gap-3" onSubmit={onSubmit}>
        <TextField
          isRequired
          className="flex w-full flex-col gap-1"
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
          className="flex w-full flex-col gap-1"
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
            className="flex w-full flex-col gap-1"
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
          className="w-full"
          isDisabled={mutation.isPending}
          type="submit"
          variant="primary"
        >
          {mutation.isPending ? "Creando…" : "Crear"}
        </Button>
      </Form>
    </SectionCard>
  );
}

// --- Admission control (Story 4.2, owner only) -----------------------------

interface AdmissionOut {
  max_active_senders: number;
}

const ADMISSION_KEY = ["admin-admission"] as const;
const ADMISSION_CAP_MAX = 1000;

// Digits-only gate (the isPositiveInt idiom) that ALSO admits 0 — 0 disables
// admission control entirely (backend bounds: 0..1000).
function isValidCap(value: string): boolean {
  return /^\d+$/.test(value.trim()) && Number(value) <= ADMISSION_CAP_MAX;
}

function AdmissionControlCard() {
  const queryClient = useQueryClient();
  // null = untouched → render the server value; editing overrides it.
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const admission = useQuery({
    queryKey: ADMISSION_KEY,
    queryFn: () => api.get<AdmissionOut>("/api/admin/admission"),
  });

  const mutation = useMutation({
    mutationFn: (cap: number) =>
      api.put<AdmissionOut>("/api/admin/admission", {
        max_active_senders: cap,
      }),
    onSuccess: (data) => {
      setDraft(null);
      setBanner(null);
      queryClient.setQueryData(ADMISSION_KEY, data);
    },
    onError: (err) => {
      // invalid_admission_cap (and anything else) carries the server's
      // Spanish message — render it verbatim ({code, message} contract).
      setBanner(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  const value = draft ?? String(admission.data?.max_active_senders ?? "");

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (mutation.isPending) return;
    setError(null);
    setBanner(null);

    if (!isValidCap(value)) {
      setError(`Indica un número entero entre 0 y ${ADMISSION_CAP_MAX}.`);

      return;
    }
    mutation.mutate(Number(value));
  }

  return (
    <section className="mb-6 rounded-lg border border-default/30 p-4">
      <h2 className="mb-1 text-lg font-medium">Control de admisión</h2>
      <p className="mb-3 text-sm text-default-500">
        Máximo de envíos activos a la vez; los lotes que excedan el límite
        esperan en cola. 0 desactiva el límite: todos los lotes entran de
        inmediato (degradación adaptativa pura).
      </p>

      {banner && (
        <Alert className="mb-3" status="danger">
          {banner}
        </Alert>
      )}

      {admission.isError ? (
        <Alert status="danger">
          No pudimos cargar el límite. Recarga la página.
        </Alert>
      ) : (
        <Form
          className="flex flex-col gap-3 sm:flex-row sm:items-end"
          onSubmit={onSubmit}
        >
          <TextField
            isRequired
            className="flex flex-col gap-1 sm:w-40"
            isDisabled={admission.isLoading}
            isInvalid={error !== null}
            name="max_active_senders"
            type="number"
            value={value}
            onChange={(v) => {
              setDraft(v);
              if (error) setError(null);
            }}
          >
            <Label>Envíos activos máx.</Label>
            <Input placeholder="0" />
            {error && <FieldError>{error}</FieldError>}
          </TextField>

          <Button
            className="sm:mb-1"
            isDisabled={mutation.isPending || admission.isLoading}
            type="submit"
            variant="primary"
          >
            {mutation.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </Form>
      )}
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
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.delete<void>(`/api/admin/users/${userId}`),
    onSuccess: () => {
      setOpen(false);
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

  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        onPress={() => {
          setError(null);
          setOpen(true);
        }}
      >
        Eliminar
      </Button>

      <AlertDialog
        isOpen={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
      >
        <AlertDialog.Backdrop>
          <AlertDialog.Container>
            <AlertDialog.Dialog>
              <AlertDialog.Header>
                <AlertDialog.Heading>
                  ¿Eliminar este admin? ({email})
                </AlertDialog.Heading>
              </AlertDialog.Header>
              {error && (
                <AlertDialog.Body>
                  <Alert status="danger">{error}</Alert>
                </AlertDialog.Body>
              )}
              <AlertDialog.Footer>
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
                <Button
                  isDisabled={mutation.isPending}
                  size="sm"
                  variant="danger"
                  onPress={() => mutation.mutate()}
                >
                  {mutation.isPending ? "Eliminando…" : "Sí, eliminar"}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      </AlertDialog>
    </>
  );
}

// --- Client lifecycle: renew + block/unblock (Story 1.5) -----------------
// Horizontal button row, constant row height — anything that used to expand
// inline now lives in an AlertDialog (ui-polish-spec §3.5).

function ClientLifecycleActions({
  user,
  onChanged,
}: {
  user: UserOut;
  onChanged: () => void;
}) {
  return (
    <div className="flex gap-2">
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

  return (
    <>
      <Button size="sm" variant="secondary" onPress={() => setOpen(true)}>
        Renovar
      </Button>

      <AlertDialog
        isOpen={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
      >
        <AlertDialog.Backdrop>
          <AlertDialog.Container>
            <AlertDialog.Dialog>
              <AlertDialog.Header>
                <AlertDialog.Heading>Renovar plan</AlertDialog.Heading>
              </AlertDialog.Header>
              <AlertDialog.Body>
                <div className="flex flex-col gap-3">
                  <div className="flex gap-2">
                    <TextField
                      className="flex w-24 flex-col gap-1"
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

                  {error && <Alert status="danger">{error}</Alert>}
                </div>
              </AlertDialog.Body>
              <AlertDialog.Footer>
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
                <Button
                  isDisabled={mutation.isPending}
                  size="sm"
                  variant="primary"
                  onPress={submit}
                >
                  {mutation.isPending ? "Renovando…" : "Renovar"}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      </AlertDialog>
    </>
  );
}

function BlockAction({
  user,
  onChanged,
}: {
  user: UserOut;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const action = user.is_blocked ? "unblock" : "block";

  const mutation = useMutation({
    mutationFn: () =>
      api.post<UserOut>(`/api/admin/users/${user.id}/${action}`),
    onSuccess: () => {
      setOpen(false);
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

  // Unblock restores access (not destructive) → acts on a single press. Its
  // error renders as a compact Alert under the button — the documented
  // exception for single-press action errors (ui-polish-spec §3.5).
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
        {error && (
          <Alert className="mt-1" status="danger">
            {error}
          </Alert>
        )}
      </div>
    );
  }

  // Block closes the client's live session → confirm dialog.
  return (
    <>
      <Button
        size="sm"
        variant="danger"
        onPress={() => {
          setError(null);
          setOpen(true);
        }}
      >
        Bloquear
      </Button>

      <AlertDialog
        isOpen={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
      >
        <AlertDialog.Backdrop>
          <AlertDialog.Container>
            <AlertDialog.Dialog>
              <AlertDialog.Header>
                <AlertDialog.Heading>
                  ¿Bloquear a {user.email}? Su sesión se cerrará al instante.
                </AlertDialog.Heading>
              </AlertDialog.Header>
              {error && (
                <AlertDialog.Body>
                  <Alert status="danger">{error}</Alert>
                </AlertDialog.Body>
              )}
              <AlertDialog.Footer>
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
                <Button
                  isDisabled={mutation.isPending}
                  size="sm"
                  variant="danger"
                  onPress={() => mutation.mutate()}
                >
                  {mutation.isPending ? "Bloqueando…" : "Sí, bloquear"}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      </AlertDialog>
    </>
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
  const [open, setOpen] = useState(false);
  // The EXACTLY-ONCE display (AC1): lives only in local state; "Listo" (or
  // closing the dialog by ANY means) clears it and it is unrecoverable by
  // design (only a new reset produces a new one).
  const [tempPassword, setTempPassword] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.post<{ temp_password: string }>(
        `/api/admin/users/${user.id}/reset-password`,
      ),
    onSuccess: (res) => {
      setError(null);
      setTempPassword(res.temp_password);
      // The SAME dialog mutates into the temp-password view; onChanged()
      // (USERS_KEY invalidation) is deferred to dismiss: a refetch-driven
      // remount here could destroy the one-time password before the admin
      // copies it.
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

  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        onPress={() => {
          setError(null);
          setOpen(true);
        }}
      >
        Resetear
      </Button>

      <AlertDialog
        isOpen={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) {
            // Today only the footer buttons close this dialog (the Backdrop
            // below pins isDismissable={false} + isKeyboardDismissDisabled —
            // HeroUI 3.1's defaults, made explicit so a library upgrade or a
            // stray prop can't silently open an ESC/backdrop path that
            // destroys the one-time password by accident). This branch is
            // the safety net: if any close route ever bypasses "Listo", the
            // exactly-once password is still destroyed, never recoverable.
            if (tempPassword) dismiss();
            else setError(null);
          }
        }}
      >
        <AlertDialog.Backdrop isKeyboardDismissDisabled isDismissable={false}>
          <AlertDialog.Container>
            <AlertDialog.Dialog>
              {tempPassword ? (
                <>
                  <AlertDialog.Header>
                    <AlertDialog.Heading>
                      Contraseña temporal
                    </AlertDialog.Heading>
                  </AlertDialog.Header>
                  <AlertDialog.Body>
                    <div className="flex flex-col gap-2">
                      <span className="font-mono text-sm">{tempPassword}</span>
                      <span className="text-sm text-muted">
                        Cópiala ahora: no volverá a mostrarse.
                      </span>
                      {error && <Alert status="danger">{error}</Alert>}
                    </div>
                  </AlertDialog.Body>
                  <AlertDialog.Footer>
                    <Button size="sm" variant="secondary" onPress={copy}>
                      {copied ? "Copiada" : "Copiar"}
                    </Button>
                    <Button
                      size="sm"
                      variant="primary"
                      onPress={() => {
                        setOpen(false);
                        dismiss();
                      }}
                    >
                      Listo
                    </Button>
                  </AlertDialog.Footer>
                </>
              ) : (
                <>
                  <AlertDialog.Header>
                    <AlertDialog.Heading>
                      ¿Resetear la contraseña de {user.email}? Su sesión se
                      cerrará al instante.
                    </AlertDialog.Heading>
                  </AlertDialog.Header>
                  {error && (
                    <AlertDialog.Body>
                      <Alert status="danger">{error}</Alert>
                    </AlertDialog.Body>
                  )}
                  <AlertDialog.Footer>
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
                    <Button
                      isDisabled={mutation.isPending}
                      size="sm"
                      variant="danger"
                      onPress={() => mutation.mutate()}
                    >
                      {mutation.isPending ? "Reseteando…" : "Sí, resetear"}
                    </Button>
                  </AlertDialog.Footer>
                </>
              )}
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      </AlertDialog>
    </>
  );
}
