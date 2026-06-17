"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";

import { api, ApiError } from "@/lib/api";
import { AdminShell } from "@/components/ui/admin-shell";
import { Btn } from "@/components/ui/btn";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Field } from "@/components/ui/field";
import { MonoChip } from "@/components/ui/mono-chip";
import { Notice } from "@/components/ui/notice";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";
import { Select } from "@/components/ui/select";

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
// owner gets an inline message instead of a raw 422 round-trip. Inner ASCII
// spaces are allowed (e.g. "/xx x"); other whitespace (tabs, NBSP, unicode
// separators) and invisible/control chars are rejected — same net effect as
// the backend `not ch.isprintable()` check.
function validateGateValue(raw: string): string | null {
  // Trim ends and collapse internal space-runs to mirror the backend, which
  // stores a single inner space (a stored double space would desync apply_gate).
  const value = raw.trim().replace(/ {2,}/g, " ");

  if (!value) return "Ingresá un gate.";
  // `[^\S ]` = any whitespace except the plain ASCII space (tabs, NBSP, unicode
  // separators). The second class adds the control/format/zero-width chars JS
  // `\s` misses (soft hyphen, bidi overrides, isolates, BOM…) so this stays a
  // superset-reject of the backend `not isprintable()` check — no silent 422s.
  if (
    /[^\S ]|[\u0000-\u001f\u007f-\u009f\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\ufff9-\ufffb]/.test(
      value,
    )
  )
    return "El gate solo admite un espacio simple, sin tabulaciones ni caracteres invisibles.";
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

  const categoryItems = categories.data?.items ?? [];

  // Group the catalog into one SectionCard per category (gate rows in a ul),
  // following the Ranger-X GatesScreen pattern. Order follows the category
  // list; categories with no gates still render (with an inline empty state).
  const grouped = useMemo(() => {
    const byCategory = new Map<number, GateOut[]>();

    for (const g of gates.data?.items ?? []) {
      const bucket = byCategory.get(g.category_id) ?? [];

      bucket.push(g);
      byCategory.set(g.category_id, bucket);
    }

    return categoryItems.map((c) => ({
      category: c,
      gates: byCategory.get(c.id) ?? [],
    }));
  }, [gates.data, categoryItems]);

  return (
    // Only the owner reaches this page (backend guard) → Gates nav visible.
    <AdminShell gatesVisible title="Gates">
      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        {/* Left zone: categories + create form (sticky on desktop). */}
        <div className="flex flex-col gap-5 lg:sticky lg:top-6 lg:self-start">
          <CategoriesBlock
            categories={categoryItems}
            isError={categories.isError}
            isLoading={categories.isLoading}
            onChanged={invalidateCategories}
          />

          <CreateGateForm categories={categoryItems} onCreated={invalidate} />
        </div>

        {/* Right zone: the catalog, grouped by category. */}
        <div className="flex flex-col gap-6">
          {gates.isLoading && (
            <SectionCard legend="CATÁLOGO" padding="none">
              <PanelSkeleton rows={5} />
            </SectionCard>
          )}

          {gates.isError && (
            <Notice status="danger">
              No pudimos cargar el catálogo. Recarga la página.
            </Notice>
          )}

          {gates.data && grouped.length === 0 && (
            <SectionCard legend="CATÁLOGO" padding="none">
              <EmptyState message="El catálogo está vacío." />
            </SectionCard>
          )}

          {gates.data &&
            grouped.map(({ category, gates: rows }) => (
              <SectionCard
                key={category.id}
                legend={category.name}
                padding="none"
              >
                {rows.length === 0 ? (
                  <EmptyState message="Sin gates en esta categoría." />
                ) : (
                  <ul className="m-0 list-none p-0">
                    {rows.map((g, i) => (
                      <li
                        key={g.id}
                        className={clsx(
                          "flex items-center gap-3 px-3.5 py-3",
                          // Top border separates rows (none on the first),
                          // per the Ranger-X GatesScreen li styling.
                          i && "border-t border-separator",
                        )}
                      >
                        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                          <span className="truncate text-sm font-semibold">
                            {g.name}
                          </span>
                          <span className="font-mono text-[11px] text-muted tabular-nums">
                            {formatCreated(g.created_at)}
                          </span>
                        </div>
                        <MonoChip>{g.value}</MonoChip>
                        <EditGateAction
                          categories={categoryItems}
                          gate={g}
                          onChanged={invalidate}
                        />
                        <DeleteGateAction gate={g} onDeleted={invalidate} />
                      </li>
                    ))}
                  </ul>
                )}
              </SectionCard>
            ))}
        </div>
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
  errorMessage,
  className,
}: {
  categories: CategoryOut[];
  value: number | null;
  onChange: (id: number | null) => void;
  label?: string;
  errorMessage?: string | null;
  className?: string;
}) {
  // Empty catalog: a single disabled hint option with zero behavior.
  const options =
    categories.length === 0
      ? [{ id: "__none", label: "Primero crea una categoría." }]
      : categories.map((c) => ({ id: String(c.id), label: c.name }));

  return (
    <Select
      className={className ?? "w-full"}
      disabled={categories.length === 0}
      error={errorMessage}
      label={label}
      options={options}
      placeholder="Elegí una categoría"
      value={value === null ? null : String(value)}
      onChange={(id) => onChange(id === "__none" ? null : Number(id))}
    />
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
        {banner && <Notice status="danger">{banner}</Notice>}

        <form className="flex flex-col gap-3" onSubmit={onSubmit}>
          <Field
            required
            error={fieldError}
            label="Nombre"
            name="category-name"
            placeholder="Visa"
            value={name}
            onChange={(v) => {
              setName(v);
              if (fieldError) setFieldError(null);
            }}
          />

          <Btn
            full
            disabled={mutation.isPending}
            type="submit"
            variant="primary"
          >
            {mutation.isPending ? "Creando…" : "Crear categoría"}
          </Btn>
        </form>

        <div>
          {isLoading && <PanelSkeleton rows={3} />}
          {isError && (
            <Notice status="danger">
              No pudimos cargar las categorías. Recarga la página.
            </Notice>
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
  // field fits the 320px column); the delete confirm lives in a ConfirmDialog.
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
          <Field
            className="flex-1"
            error={renameError}
            name="rename"
            value={name}
            onChange={(v) => {
              setName(v);
              if (renameError) setRenameError(null);
            }}
          />
        ) : (
          <span className="text-sm">{category.name}</span>
        )}

        <div className="flex gap-2">
          {mode === "edit" ? (
            <>
              <Btn
                disabled={rename.isPending}
                size="sm"
                variant="primary"
                onClick={saveRename}
              >
                {rename.isPending ? "Guardando…" : "Guardar"}
              </Btn>
              <Btn
                disabled={rename.isPending}
                size="sm"
                variant="secondary"
                onClick={() => {
                  setMode("view");
                  setRenameError(null);
                }}
              >
                Cancelar
              </Btn>
            </>
          ) : (
            <>
              <Btn
                size="sm"
                variant="secondary"
                onClick={() => {
                  setName(category.name);
                  setRenameError(null);
                  setMode("edit");
                }}
              >
                Renombrar
              </Btn>
              <Btn
                size="sm"
                variant="secondary"
                onClick={() => {
                  setDeleteError(null);
                  setConfirmOpen(true);
                }}
              >
                Eliminar
              </Btn>
            </>
          )}
        </div>
      </div>

      <ConfirmDialog
        confirmLabel={remove.isPending ? "Eliminando…" : "Eliminar"}
        confirmVariant="danger"
        heading={`¿Eliminar la categoría “${category.name}”?`}
        open={confirmOpen}
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
        onOpenChange={(open) => {
          setConfirmOpen(open);
          if (!open) setDeleteError(null);
        }}
      >
        {deleteError && <Notice status="danger">{deleteError}</Notice>}
      </ConfirmDialog>
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
    // Enter can re-submit the Form while a POST is in flight (disabled only
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
      <div className="flex flex-col gap-3">
        {banner && <Notice status="danger">{banner}</Notice>}

        <form className="flex flex-col gap-3" onSubmit={onSubmit}>
          <Field
            required
            error={nameError}
            label="Nombre"
            name="name"
            placeholder="Visa Oro"
            value={name}
            onChange={(v) => {
              setName(v);
              if (nameError) setNameError(null);
            }}
          />

          <Field
            mono
            required
            error={fieldError}
            label="Gate"
            name="value"
            placeholder=".ej"
            value={value}
            onChange={(v) => {
              setValue(v);
              if (fieldError) setFieldError(null);
            }}
          />

          <CategorySelect
            categories={categories}
            errorMessage={categoryError}
            value={categoryId}
            onChange={(id) => {
              setCategoryId(id);
              if (categoryError) setCategoryError(null);
            }}
          />

          <Btn
            full
            disabled={mutation.isPending}
            type="submit"
            variant="primary"
          >
            {mutation.isPending ? "Creando…" : "Crear gate"}
          </Btn>
        </form>
      </div>
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
      <Btn
        size="sm"
        variant="secondary"
        onClick={() => {
          setName(gate.name);
          setValue(gate.value);
          setCategoryId(gate.category_id);
          setError(null);
          setOpen(true);
        }}
      >
        Renombrar
      </Btn>

      <ConfirmDialog
        confirmLabel={mutation.isPending ? "Guardando…" : "Guardar"}
        confirmVariant="primary"
        heading="Editar gate"
        open={open}
        pending={mutation.isPending}
        onConfirm={save}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
      >
        <div className="flex flex-col gap-3">
          <Field
            label="Nombre"
            name="name"
            value={name}
            onChange={(v) => {
              setName(v);
              if (error) setError(null);
            }}
          />

          <Field
            mono
            label="Gate"
            name="value"
            value={value}
            onChange={(v) => {
              setValue(v);
              if (error) setError(null);
            }}
          />

          <CategorySelect
            categories={categories}
            value={categoryId}
            onChange={(id) => {
              setCategoryId(id);
              if (error) setError(null);
            }}
          />

          {error && <Notice status="danger">{error}</Notice>}
        </div>
      </ConfirmDialog>
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
      <Btn
        icon="trash"
        size="sm"
        variant="danger"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        Eliminar
      </Btn>

      <ConfirmDialog
        confirmLabel={mutation.isPending ? "Eliminando…" : "Eliminar"}
        confirmVariant="danger"
        heading={`¿Eliminar este gate? (${gate.value})`}
        open={open}
        pending={mutation.isPending}
        onConfirm={() => mutation.mutate()}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
      >
        {error && <Notice status="danger">{error}</Notice>}
      </ConfirmDialog>
    </>
  );
}
