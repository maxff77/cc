"use client";

import { useState } from "react";
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
        <h1 className="text-2xl font-semibold">Gestión de usuarios</h1>
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
                      {isOwner && u.role === "admin" ? (
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

      if (kind === "client") payload.plan_days = Number.parseInt(planDays, 10);

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
