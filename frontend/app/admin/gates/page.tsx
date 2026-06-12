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

// Local response shapes mirror the backend gate schemas (snake_case,
// end-to-end) — same explicit-interface idiom as the users page.
interface GateOut {
  id: number;
  value: string;
  created_at: string;
}

interface GateListResponse {
  items: GateOut[];
  total: number;
}

const GATES_KEY = ["admin-gates"] as const;

function formatCreated(iso: string): string {
  return new Date(iso).toLocaleDateString("es", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export default function AdminGatesPage() {
  const queryClient = useQueryClient();

  const gates = useQuery({
    queryKey: GATES_KEY,
    queryFn: () => api.get<GateListResponse>("/api/admin/gates"),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: GATES_KEY });

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
          <h1 className="text-2xl font-semibold">Catálogo de gates</h1>
          <Link
            className="text-sm text-default-500 underline"
            href="/admin/users"
          >
            Usuarios
          </Link>
        </div>
        <Button variant="secondary" onPress={logout}>
          Cerrar sesión
        </Button>
      </header>

      <CreateGateForm onCreated={invalidate} />

      <section className="mt-8">
        {gates.isLoading && (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        )}

        {gates.isError && (
          <Alert status="danger">
            No pudimos cargar el catálogo. Recarga la página.
          </Alert>
        )}

        {gates.data && (
          <Table>
            <Table.Content aria-label="Catálogo de gates">
              <Table.Header>
                <Table.Column isRowHeader>Gate</Table.Column>
                <Table.Column>Creado</Table.Column>
                <Table.Column>Acciones</Table.Column>
              </Table.Header>
              <Table.Body
                items={gates.data.items}
                renderEmptyState={() => "El catálogo está vacío."}
              >
                {(g) => (
                  <Table.Row id={g.id}>
                    <Table.Cell>
                      <span className="font-mono text-sm">{g.value}</span>
                    </Table.Cell>
                    <Table.Cell>
                      <span className="text-default-500">
                        {formatCreated(g.created_at)}
                      </span>
                    </Table.Cell>
                    <Table.Cell>
                      <div className="flex flex-col gap-2">
                        <EditGateAction gate={g} onChanged={invalidate} />
                        <DeleteGateAction gate={g} onDeleted={invalidate} />
                      </div>
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

// --- Create ----------------------------------------------------------------

function CreateGateForm({ onCreated }: { onCreated: () => void }) {
  const [value, setValue] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.post<GateOut>("/api/admin/gates", { value }),
    onSuccess: () => {
      setValue("");
      onCreated();
    },
    onError: (err) => {
      // Backend sends user-facing Spanish in `message`; route gate_exists to
      // the field, everything else to the banner.
      if (err instanceof ApiError) {
        if (err.code === "gate_exists") setFieldError(err.message);
        else setBanner(err.message);
      } else {
        setBanner("No pudimos conectar. Intenta de nuevo.");
      }
    },
  });

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setFieldError(null);
    setBanner(null);
    mutation.mutate();
  }

  return (
    <section className="mb-6 rounded-lg border border-default/30 p-4">
      <h2 className="mb-3 text-lg font-medium">Crear gate</h2>

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
          className="flex flex-col gap-1 sm:w-48"
          isInvalid={fieldError !== null}
          name="value"
          value={value}
          onChange={(v) => {
            setValue(v);
            if (fieldError) setFieldError(null);
          }}
        >
          <Label>Gate</Label>
          <Input className="font-mono" placeholder=".ej" />
          {fieldError && <FieldError>{fieldError}</FieldError>}
        </TextField>

        <Button
          className="sm:mb-1"
          isDisabled={mutation.isPending}
          type="submit"
          variant="primary"
        >
          {mutation.isPending ? "Creando…" : "Crear gate"}
        </Button>
      </Form>
    </section>
  );
}

// --- Edit (inline per-row) ---------------------------------------------------

function EditGateAction({
  gate,
  onChanged,
}: {
  gate: GateOut;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(gate.value);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.patch<GateOut>(`/api/admin/gates/${gate.id}`, { value }),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      onChanged();
    },
    onError: (err) => {
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  if (!open) {
    return (
      <Button
        size="sm"
        variant="secondary"
        onPress={() => {
          setValue(gate.value);
          setOpen(true);
        }}
      >
        Editar
      </Button>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <TextField
        className="flex flex-col gap-1 sm:w-40"
        name="value"
        value={value}
        onChange={(v) => {
          setValue(v);
          if (error) setError(null);
        }}
      >
        <Label>Gate</Label>
        <Input className="font-mono" />
      </TextField>
      {error && <span className="text-sm text-danger">{error}</span>}
      <div className="flex gap-2">
        <Button
          isDisabled={mutation.isPending}
          size="sm"
          variant="primary"
          onPress={() => mutation.mutate()}
        >
          {mutation.isPending ? "Guardando…" : "Guardar"}
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

// --- Delete (soft-delete; inline confirm, max one layer — UX-DR21) ----------

function DeleteGateAction({
  gate,
  onDeleted,
}: {
  gate: GateOut;
  onDeleted: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.delete<void>(`/api/admin/gates/${gate.id}`),
    onSuccess: () => {
      setConfirming(false);
      setError(null);
      onDeleted();
    },
    onError: (err) => {
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
      <span className="text-sm">
        ¿Eliminar este gate? (<span className="font-mono">{gate.value}</span>)
      </span>
      {error && <span className="text-sm text-danger">{error}</span>}
      <div className="flex gap-2">
        <Button
          isDisabled={mutation.isPending}
          size="sm"
          variant="danger"
          onPress={() => mutation.mutate()}
        >
          {mutation.isPending ? "Eliminando…" : "Eliminar"}
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
