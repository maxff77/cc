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
  ListBox,
  Select,
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
  name: string;
  category_id: number;
  category_name: string;
  created_at: string;
}

interface GateListResponse {
  items: GateOut[];
  total: number;
}

interface CategoryOut {
  id: number;
  name: string;
  created_at: string;
}

interface CategoryListResponse {
  items: CategoryOut[];
  total: number;
}

const GATES_KEY = ["admin-gates"] as const;
const CATEGORIES_KEY = ["admin-gate-categories"] as const;
const GATE_VALUE_MAX = 20;
const GATE_NAME_MAX = 80;
const CATEGORY_NAME_MAX = 80;

// Mirror of backend `_validate_category_name`: required, ≤80, spaces allowed.
function validateCategoryName(raw: string): string | null {
  const name = raw.trim();

  if (!name) return "Ingresá un nombre.";
  if (name.length > CATEGORY_NAME_MAX)
    return `Máximo ${CATEGORY_NAME_MAX} caracteres.`;

  return null;
}

// Client-side mirror of the backend `_validate_gate_value` policy, so the
// owner gets an inline message instead of a raw 422 round-trip.
function validateGateValue(raw: string): string | null {
  const value = raw.trim();

  if (!value) return "Ingresá un gate.";
  if (/\s/.test(value)) return "El gate no puede contener espacios.";
  if (value.length > GATE_VALUE_MAX)
    return `Máximo ${GATE_VALUE_MAX} caracteres.`;

  return null;
}

// Mirror of `_validate_gate_name`: required, ≤80 chars, spaces allowed.
function validateGateName(raw: string): string | null {
  const name = raw.trim();

  if (!name) return "Ingresá un nombre.";
  if (name.length > GATE_NAME_MAX) return `Máximo ${GATE_NAME_MAX} caracteres.`;

  return null;
}

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

  const categories = useQuery({
    queryKey: CATEGORIES_KEY,
    queryFn: () => api.get<CategoryListResponse>("/api/admin/gate-categories"),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: GATES_KEY });

  const invalidateCategories = () => {
    queryClient.invalidateQueries({ queryKey: CATEGORIES_KEY });
    // Renames change every gate row's category_name too.
    queryClient.invalidateQueries({ queryKey: GATES_KEY });
  };

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

      <CategoriesBlock
        categories={categories.data?.items ?? []}
        isError={categories.isError}
        isLoading={categories.isLoading}
        onChanged={invalidateCategories}
      />

      <CreateGateForm
        categories={categories.data?.items ?? []}
        onCreated={invalidate}
      />

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
                <Table.Column isRowHeader>Nombre</Table.Column>
                <Table.Column>Gate</Table.Column>
                <Table.Column>Categoría</Table.Column>
                <Table.Column>Creado</Table.Column>
                <Table.Column>Acciones</Table.Column>
              </Table.Header>
              <Table.Body
                items={gates.data.items}
                renderEmptyState={() => "El catálogo está vacío."}
              >
                {(g) => (
                  <Table.Row id={g.id}>
                    <Table.Cell>{g.name}</Table.Cell>
                    <Table.Cell>
                      <span className="font-mono text-sm">{g.value}</span>
                    </Table.Cell>
                    <Table.Cell>{g.category_name}</Table.Cell>
                    <Table.Cell>
                      <span className="text-default-500">
                        {formatCreated(g.created_at)}
                      </span>
                    </Table.Cell>
                    <Table.Cell>
                      <div className="flex flex-col gap-2">
                        <EditGateAction
                          categories={categories.data?.items ?? []}
                          gate={g}
                          onChanged={invalidate}
                        />
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

// --- Category Select (shared by gate create/edit forms) ---------------------

function CategorySelect({
  categories,
  value,
  onChange,
  label = "Categoría",
}: {
  categories: CategoryOut[];
  value: number | null;
  onChange: (id: number | null) => void;
  label?: string;
}) {
  return (
    <Select
      className="sm:w-56"
      placeholder="Elegí una categoría"
      selectedKey={value === null ? null : String(value)}
      onSelectionChange={(key) => onChange(key == null ? null : Number(key))}
    >
      <Label>{label}</Label>
      <Select.Trigger>
        <Select.Value />
        <Select.Indicator />
      </Select.Trigger>
      <Select.Popover>
        <ListBox>
          {categories.map((c) => (
            <ListBox.Item key={c.id} id={String(c.id)} textValue={c.name}>
              {c.name}
            </ListBox.Item>
          ))}
        </ListBox>
      </Select.Popover>
    </Select>
  );
}

// --- Categorías management block (Story 2.2, owner addition) ----------------

function CategoriesBlock({
  categories,
  isLoading,
  isError,
  onChanged,
}: {
  categories: CategoryOut[];
  isLoading: boolean;
  isError: boolean;
  onChanged: () => void;
}) {
  const [name, setName] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.post<CategoryOut>("/api/admin/gate-categories", { name }),
    onSuccess: () => {
      setName("");
      onChanged();
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.code === "category_exists") setFieldError(err.message);
        else setBanner(err.message);
      } else {
        setBanner("No pudimos conectar. Intenta de nuevo.");
      }
    },
  });

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (mutation.isPending) return;
    setFieldError(null);
    setBanner(null);
    const invalid = validateCategoryName(name);

    if (invalid) {
      setFieldError(invalid);

      return;
    }
    mutation.mutate();
  }

  return (
    <section className="mb-6 rounded-lg border border-default/30 p-4">
      <h2 className="mb-3 text-lg font-medium">Categorías</h2>

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
          className="flex flex-col gap-1 sm:w-64"
          isInvalid={fieldError !== null}
          name="category-name"
          value={name}
          onChange={(v) => {
            setName(v);
            if (fieldError) setFieldError(null);
          }}
        >
          <Label>Nombre</Label>
          <Input placeholder="Visa" />
          {fieldError && <FieldError>{fieldError}</FieldError>}
        </TextField>

        <Button
          className="sm:mb-1"
          isDisabled={mutation.isPending}
          type="submit"
          variant="primary"
        >
          {mutation.isPending ? "Creando…" : "Crear categoría"}
        </Button>
      </Form>

      <div className="mt-4">
        {isLoading && (
          <div className="flex justify-center py-4">
            <Spinner />
          </div>
        )}
        {isError && (
          <Alert status="danger">
            No pudimos cargar las categorías. Recarga la página.
          </Alert>
        )}
        {!isLoading && !isError && categories.length === 0 && (
          <p className="text-sm text-default-500">Todavía no hay categorías.</p>
        )}
        <ul className="flex flex-col divide-y divide-default/20">
          {categories.map((c) => (
            <CategoryRow key={c.id} category={c} onChanged={onChanged} />
          ))}
        </ul>
      </div>
    </section>
  );
}

function CategoryRow({
  category,
  onChanged,
}: {
  category: CategoryOut;
  onChanged: () => void;
}) {
  // Max ONE inline layer open at a time (UX-DR21): renaming closes the
  // delete confirm and vice versa.
  const [mode, setMode] = useState<"view" | "edit" | "confirm">("view");
  const [name, setName] = useState(category.name);
  const [error, setError] = useState<string | null>(null);

  const rename = useMutation({
    mutationFn: () =>
      api.patch<CategoryOut>(`/api/admin/gate-categories/${category.id}`, {
        name,
      }),
    onSuccess: () => {
      setMode("view");
      setError(null);
      onChanged();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === "category_not_found") {
        onChanged();

        return;
      }
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  const remove = useMutation({
    mutationFn: () =>
      api.delete<void>(`/api/admin/gate-categories/${category.id}`),
    onSuccess: () => {
      setMode("view");
      setError(null);
      onChanged();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === "category_not_found") {
        onChanged();

        return;
      }
      // category_in_use renders inline, verbatim from the server.
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  function saveRename() {
    if (rename.isPending) return;
    const invalid = validateCategoryName(name);

    if (invalid) {
      setError(invalid);

      return;
    }
    setError(null);
    rename.mutate();
  }

  return (
    <li className="flex flex-col gap-2 py-2">
      <div className="flex items-center justify-between gap-3">
        {mode === "edit" ? (
          <TextField
            className="flex-1"
            name="rename"
            value={name}
            onChange={(v) => {
              setName(v);
              if (error) setError(null);
            }}
          >
            <Input aria-label="Nombre de la categoría" />
          </TextField>
        ) : (
          <span>{category.name}</span>
        )}

        <div className="flex gap-2">
          {mode === "edit" ? (
            <>
              <Button
                isDisabled={rename.isPending}
                size="sm"
                variant="primary"
                onPress={saveRename}
              >
                {rename.isPending ? "Guardando…" : "Guardar"}
              </Button>
              <Button
                isDisabled={rename.isPending}
                size="sm"
                variant="secondary"
                onPress={() => {
                  setMode("view");
                  setError(null);
                }}
              >
                Cancelar
              </Button>
            </>
          ) : mode === "confirm" ? (
            <>
              <Button
                isDisabled={remove.isPending}
                size="sm"
                variant="danger"
                onPress={() => remove.mutate()}
              >
                {remove.isPending ? "Eliminando…" : "Eliminar"}
              </Button>
              <Button
                isDisabled={remove.isPending}
                size="sm"
                variant="secondary"
                onPress={() => {
                  setMode("view");
                  setError(null);
                }}
              >
                Cancelar
              </Button>
            </>
          ) : (
            <>
              <Button
                size="sm"
                variant="secondary"
                onPress={() => {
                  setName(category.name);
                  setError(null);
                  setMode("edit");
                }}
              >
                Renombrar
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onPress={() => {
                  setError(null);
                  setMode("confirm");
                }}
              >
                Eliminar
              </Button>
            </>
          )}
        </div>
      </div>
      {mode === "confirm" && (
        <span className="text-sm">
          ¿Eliminar la categoría “{category.name}”?
        </span>
      )}
      {error && <span className="text-sm text-danger">{error}</span>}
    </li>
  );
}

// --- Create ----------------------------------------------------------------

function CreateGateForm({
  categories,
  onCreated,
}: {
  categories: CategoryOut[];
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [categoryId, setCategoryId] = useState<number | null>(null);
  const [nameError, setNameError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [categoryError, setCategoryError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.post<GateOut>("/api/admin/gates", {
        value,
        name,
        category_id: categoryId,
      }),
    onSuccess: () => {
      setName("");
      setValue("");
      onCreated();
    },
    onError: (err) => {
      // Backend sends user-facing Spanish in `message`; route gate_exists to
      // the value field, everything else to the banner.
      if (err instanceof ApiError) {
        if (err.code === "gate_exists") setFieldError(err.message);
        else if (err.code === "category_not_found")
          setCategoryError(err.message);
        else setBanner(err.message);
      } else {
        setBanner("No pudimos conectar. Intenta de nuevo.");
      }
    },
  });

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    // Enter can re-submit the Form while a POST is in flight (isDisabled only
    // blocks the button) — would double-create and show a spurious duplicate.
    if (mutation.isPending) return;
    setNameError(null);
    setFieldError(null);
    setCategoryError(null);
    setBanner(null);
    const invalidName = validateGateName(name);
    const invalidValue = validateGateValue(value);
    const invalidCategory = categoryId === null ? "Elegí una categoría." : null;

    if (invalidName) setNameError(invalidName);
    if (invalidValue) setFieldError(invalidValue);
    if (invalidCategory) setCategoryError(invalidCategory);
    if (invalidName || invalidValue || invalidCategory) return;
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
          className="flex flex-col gap-1 sm:w-56"
          isInvalid={nameError !== null}
          name="name"
          value={name}
          onChange={(v) => {
            setName(v);
            if (nameError) setNameError(null);
          }}
        >
          <Label>Nombre</Label>
          <Input placeholder="Visa Oro" />
          {nameError && <FieldError>{nameError}</FieldError>}
        </TextField>

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

        <div className="flex flex-col gap-1">
          <CategorySelect
            categories={categories}
            value={categoryId}
            onChange={(id) => {
              setCategoryId(id);
              if (categoryError) setCategoryError(null);
            }}
          />
          {categoryError && (
            <span className="text-sm text-danger">{categoryError}</span>
          )}
        </div>

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
  categories,
  onChanged,
}: {
  gate: GateOut;
  categories: CategoryOut[];
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(gate.name);
  const [value, setValue] = useState(gate.value);
  const [categoryId, setCategoryId] = useState<number | null>(gate.category_id);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.patch<GateOut>(`/api/admin/gates/${gate.id}`, {
        value,
        name,
        category_id: categoryId,
      }),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      onChanged();
    },
    onError: (err) => {
      // Retired/deleted in another tab: the row no longer exists server-side —
      // refresh the list so the ghost row (and this editor) goes away.
      if (err instanceof ApiError && err.code === "gate_not_found") {
        onChanged();

        return;
      }
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  function save() {
    if (mutation.isPending) return;
    const invalid =
      validateGateName(name) ??
      validateGateValue(value) ??
      (categoryId === null ? "Elegí una categoría." : null);

    if (invalid) {
      setError(invalid);

      return;
    }
    setError(null);
    mutation.mutate();
  }

  if (!open) {
    return (
      <Button
        size="sm"
        variant="secondary"
        onPress={() => {
          setName(gate.name);
          setValue(gate.value);
          setCategoryId(gate.category_id);
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
        className="flex flex-col gap-1 sm:w-48"
        name="name"
        value={name}
        onChange={(v) => {
          setName(v);
          if (error) setError(null);
        }}
      >
        <Label>Nombre</Label>
        <Input />
      </TextField>
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
      <CategorySelect
        categories={categories}
        value={categoryId}
        onChange={(id) => {
          setCategoryId(id);
          if (error) setError(null);
        }}
      />
      {error && <span className="text-sm text-danger">{error}</span>}
      <div className="flex gap-2">
        <Button
          isDisabled={mutation.isPending}
          size="sm"
          variant="primary"
          onPress={save}
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
      // Already retired in another tab → the desired outcome; just refresh.
      if (err instanceof ApiError && err.code === "gate_not_found") {
        onDeleted();

        return;
      }
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
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
