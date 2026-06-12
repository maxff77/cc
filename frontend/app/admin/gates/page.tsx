"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  AlertDialog,
  Button,
  FieldError,
  Form,
  Input,
  Label,
  ListBox,
  Select,
  Table,
  TextField,
} from "@heroui/react";

import { api, ApiError } from "@/lib/api";
import { AdminShell } from "@/components/ui/admin-shell";
import { EmptyState } from "@/components/ui/empty-state";
import { MonoChip } from "@/components/ui/mono-chip";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";

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

  return (
    // Only the owner reaches this page (backend guard) → Gates nav visible.
    <AdminShell gatesVisible title="Gates">
      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        {/* Left zone: categories + create form (sticky on desktop). */}
        <div className="flex flex-col gap-5 lg:sticky lg:top-6 lg:self-start">
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
        </div>

        {/* Right zone: the catalog table. */}
        <SectionCard legend="CATÁLOGO" padding="none">
          {gates.isLoading && <PanelSkeleton rows={5} />}

          {gates.isError && (
            <Alert className="m-3" status="danger">
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
                  renderEmptyState={() => (
                    <EmptyState message="El catálogo está vacío." />
                  )}
                >
                  {(g) => (
                    <Table.Row id={g.id}>
                      <Table.Cell>{g.name}</Table.Cell>
                      <Table.Cell>
                        <MonoChip>{g.value}</MonoChip>
                      </Table.Cell>
                      <Table.Cell>{g.category_name}</Table.Cell>
                      <Table.Cell>
                        <span className="font-mono text-[11px] text-muted tabular-nums">
                          {formatCreated(g.created_at)}
                        </span>
                      </Table.Cell>
                      <Table.Cell>
                        <div className="flex gap-2">
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
        </SectionCard>
      </div>
    </AdminShell>
  );
}

// --- Category Select (shared by gate create/edit forms) ---------------------

function CategorySelect({
  categories,
  value,
  onChange,
  label = "Categoría",
  isInvalid,
  errorMessage,
  className,
}: {
  categories: CategoryOut[];
  value: number | null;
  onChange: (id: number | null) => void;
  label?: string;
  isInvalid?: boolean;
  errorMessage?: string | null;
  className?: string;
}) {
  return (
    <Select
      className={className ?? "w-full"}
      isInvalid={isInvalid}
      placeholder="Elegí una categoría"
      selectedKey={value === null ? null : String(value)}
      onSelectionChange={(key) => onChange(key == null ? null : Number(key))}
    >
      <Label>{label}</Label>
      <Select.Trigger>
        <Select.Value />
        <Select.Indicator />
      </Select.Trigger>
      {errorMessage && <FieldError>{errorMessage}</FieldError>}
      <Select.Popover>
        <ListBox>
          {categories.length === 0 ? (
            // Empty catalog hint — disabled, not selectable, zero behavior.
            <ListBox.Item isDisabled id="__none" textValue="Sin categorías">
              Primero crea una categoría.
            </ListBox.Item>
          ) : (
            categories.map((c) => (
              <ListBox.Item key={c.id} id={String(c.id)} textValue={c.name}>
                {c.name}
              </ListBox.Item>
            ))
          )}
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
    // legendAs="h2": replaces the old "Categorías" h2 heading.
    <SectionCard legend="CATEGORÍAS" legendAs="h2">
      <div className="flex flex-col gap-3">
        {banner && <Alert status="danger">{banner}</Alert>}

        <Form className="flex flex-col gap-3" onSubmit={onSubmit}>
          <TextField
            isRequired
            className="flex w-full flex-col gap-1"
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
            className="w-full"
            isDisabled={mutation.isPending}
            type="submit"
            variant="primary"
          >
            {mutation.isPending ? "Creando…" : "Crear categoría"}
          </Button>
        </Form>

        <div>
          {isLoading && <PanelSkeleton rows={3} />}
          {isError && (
            <Alert status="danger">
              No pudimos cargar las categorías. Recarga la página.
            </Alert>
          )}
          {!isLoading && !isError && categories.length === 0 && (
            <EmptyState message="Todavía no hay categorías." />
          )}
          <ul className="flex flex-col divide-y divide-separator">
            {categories.map((c) => (
              <CategoryRow key={c.id} category={c} onChanged={onChanged} />
            ))}
          </ul>
        </div>
      </div>
    </SectionCard>
  );
}

function CategoryRow({
  category,
  onChanged,
}: {
  category: CategoryOut;
  onChanged: () => void;
}) {
  // Max ONE layer open at a time (UX-DR21): the inline rename stays (a single
  // field fits the 320px column); the delete confirm lives in an AlertDialog.
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [name, setName] = useState(category.name);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const rename = useMutation({
    mutationFn: () =>
      api.patch<CategoryOut>(`/api/admin/gate-categories/${category.id}`, {
        name,
      }),
    onSuccess: () => {
      setMode("view");
      setRenameError(null);
      onChanged();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === "category_not_found") {
        onChanged();

        return;
      }
      setRenameError(
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
      setConfirmOpen(false);
      setDeleteError(null);
      onChanged();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === "category_not_found") {
        setConfirmOpen(false);
        onChanged();

        return;
      }
      // category_in_use renders inside the dialog, verbatim from the server,
      // without closing it.
      setDeleteError(
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
      setRenameError(invalid);

      return;
    }
    setRenameError(null);
    rename.mutate();
  }

  return (
    <li className="flex flex-col gap-2 py-2">
      <div className="flex items-center justify-between gap-3">
        {mode === "edit" ? (
          <TextField
            className="flex-1"
            isInvalid={renameError !== null}
            name="rename"
            value={name}
            onChange={(v) => {
              setName(v);
              if (renameError) setRenameError(null);
            }}
          >
            <Input aria-label="Nombre de la categoría" />
            {renameError && <FieldError>{renameError}</FieldError>}
          </TextField>
        ) : (
          <span className="text-sm">{category.name}</span>
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
                  setRenameError(null);
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
                  setRenameError(null);
                  setMode("edit");
                }}
              >
                Renombrar
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onPress={() => {
                  setDeleteError(null);
                  setConfirmOpen(true);
                }}
              >
                Eliminar
              </Button>
            </>
          )}
        </div>
      </div>

      <AlertDialog
        isOpen={confirmOpen}
        onOpenChange={(open) => {
          setConfirmOpen(open);
          if (!open) setDeleteError(null);
        }}
      >
        <AlertDialog.Backdrop>
          <AlertDialog.Container>
            <AlertDialog.Dialog>
              <AlertDialog.Header>
                <AlertDialog.Heading>
                  ¿Eliminar la categoría “{category.name}”?
                </AlertDialog.Heading>
              </AlertDialog.Header>
              {deleteError && (
                <AlertDialog.Body>
                  <Alert status="danger">{deleteError}</Alert>
                </AlertDialog.Body>
              )}
              <AlertDialog.Footer>
                <Button
                  isDisabled={remove.isPending}
                  size="sm"
                  variant="secondary"
                  onPress={() => {
                    setConfirmOpen(false);
                    setDeleteError(null);
                  }}
                >
                  Cancelar
                </Button>
                <Button
                  isDisabled={remove.isPending}
                  size="sm"
                  variant="danger"
                  onPress={() => remove.mutate()}
                >
                  {remove.isPending ? "Eliminando…" : "Eliminar"}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      </AlertDialog>
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
    // legendAs="h2": replaces the old "Crear gate" h2 heading.
    <SectionCard legend="CREAR GATE" legendAs="h2">
      {banner && (
        <Alert className="mb-3" status="danger">
          {banner}
        </Alert>
      )}

      <Form className="flex flex-col gap-3" onSubmit={onSubmit}>
        <TextField
          isRequired
          className="flex w-full flex-col gap-1"
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
          className="flex w-full flex-col gap-1"
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

        <CategorySelect
          categories={categories}
          errorMessage={categoryError}
          isInvalid={categoryError !== null}
          value={categoryId}
          onChange={(id) => {
            setCategoryId(id);
            if (categoryError) setCategoryError(null);
          }}
        />

        <Button
          className="w-full"
          isDisabled={mutation.isPending}
          type="submit"
          variant="primary"
        >
          {mutation.isPending ? "Creando…" : "Crear gate"}
        </Button>
      </Form>
    </SectionCard>
  );
}

// --- Edit (per-row dialog) ---------------------------------------------------

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
        setOpen(false);
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

  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        onPress={() => {
          setName(gate.name);
          setValue(gate.value);
          setCategoryId(gate.category_id);
          setError(null);
          setOpen(true);
        }}
      >
        Editar
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
                <AlertDialog.Heading>Editar gate</AlertDialog.Heading>
              </AlertDialog.Header>
              <AlertDialog.Body>
                <div className="flex flex-col gap-3">
                  <TextField
                    className="flex w-full flex-col gap-1"
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
                    className="flex w-full flex-col gap-1"
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
                  onPress={save}
                >
                  {mutation.isPending ? "Guardando…" : "Guardar"}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      </AlertDialog>
    </>
  );
}

// --- Delete (soft-delete; confirm dialog) ------------------------------------

function DeleteGateAction({
  gate,
  onDeleted,
}: {
  gate: GateOut;
  onDeleted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.delete<void>(`/api/admin/gates/${gate.id}`),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      onDeleted();
    },
    onError: (err) => {
      // Already retired in another tab → the desired outcome; just refresh.
      if (err instanceof ApiError && err.code === "gate_not_found") {
        setOpen(false);
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
                  ¿Eliminar este gate? (
                  <span className="font-mono">{gate.value}</span>)
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
                  {mutation.isPending ? "Eliminando…" : "Eliminar"}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      </AlertDialog>
    </>
  );
}
