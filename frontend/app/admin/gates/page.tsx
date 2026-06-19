"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";

import { api, ApiError } from "@/lib/api";
import { AdminShell } from "@/components/ui/admin-shell";
import { Btn } from "@/components/ui/btn";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Field } from "@/components/ui/field";
import { LabelCaps } from "@/components/ui/label-caps";
import { MonoChip } from "@/components/ui/mono-chip";
import { Notice } from "@/components/ui/notice";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";
import { Select } from "@/components/ui/select";

// Local response shapes mirror the backend gate schemas (snake_case,
// end-to-end) — same explicit-interface idiom as the users page.
interface GateOut {
  id: number;
  // The REAL command — owner-only, shown ONLY on this admin catalog page.
  value: string;
  name: string;
  // The client-visible "Comando visible" (what every client surface shows).
  display_value: string;
  // Credits charged per captured ✅ (credits feature). 0 ⇒ free gate.
  credit_cost: number;
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
  // Special-mode feature: gates here capture in "special mode" (status from the
  // `Approveds! ✅: N` count + Approveds!/Deads! stripping). Owner-only config.
  special_mode: boolean;
  // Cookie-mode (Amazon gate, Phase 1): clients store per-account cookies for
  // gates in this category; the cockpit shows the cookie manager. Owner-only.
  cookie_mode: boolean;
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
const GATE_DISPLAY_VALUE_MAX = 80;
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

// Mirror of the backend gate credit-cost bound (credits feature): a
// non-negative integer (0 = free gate).
function validateCreditCost(raw: string): string | null {
  const t = raw.trim();
  const n = Number(t);

  if (t === "") return "Ingresá un costo (0 si es gratis).";
  if (!Number.isInteger(n) || n < 0) return "Debe ser un entero ≥ 0.";

  return null;
}

// Mirror of `_validate_gate_name`: required, ≤80 chars, spaces allowed.
function validateGateName(raw: string): string | null {
  const name = raw.trim();

  if (!name) return "Ingresá un nombre.";
  if (name.length > GATE_NAME_MAX) return `Máximo ${GATE_NAME_MAX} caracteres.`;

  return null;
}

// Mirror of `_validate_gate_display_value` ("Comando visible"): required, ≤80,
// plain ASCII spaces allowed; tabs/NBSP/unicode-separators and invisible/
// control chars rejected (same net effect as the backend `not isprintable()`).
function validateDisplayValue(raw: string): string | null {
  const display = raw.trim();

  if (!display) return "Ingresá el comando visible.";
  if (
    /[^\S ]|[\u0000-\u001f\u007f-\u009f\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\ufff9-\ufffb]/.test(
      display,
    )
  )
    return "El comando visible no puede contener tabulaciones ni caracteres invisibles.";
  if (display.length > GATE_DISPLAY_VALUE_MAX)
    return `Máximo ${GATE_DISPLAY_VALUE_MAX} caracteres.`;

  return null;
}

function formatCreated(iso: string): string {
  return new Date(iso).toLocaleDateString("es", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// "State is the product": every mutation confirms. An ephemeral success line
// that auto-clears after 2.5s — appearance only, no choreography (motion-safe
// by construction). Timer is cleared on unmount so a quick close never leaks.
function useFlash(): [string | null, (msg: string) => void] {
  const [msg, setMsg] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  function flash(next: string) {
    setMsg(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setMsg(null), 2500);
  }

  return [msg, flash];
}

// The two highest-stakes fields on the page share these hints. The real/visible
// split is load-bearing: a wrong "comando real" mis-attributes replies across
// tenants, so both inputs carry a one-line explainer wherever they appear.
const HINT_REAL = "El comando que se envía de verdad al bot.";
const HINT_VISIBLE = "Lo que ven los clientes; nunca el comando real.";

export default function AdminGatesPage() {
  const queryClient = useQueryClient();
  // Catalog-column affirmation: the per-row edit/delete actions live deep in
  // the list, so they bubble their success up here where it's visible.
  const [catalogMsg, flashCatalog] = useFlash();

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
        {/* Left zone: categories + create form (sticky on desktop). On mobile
            it drops below the catalog (order-2) — the owner came to see the
            catalog, not to scroll past every management control first. */}
        <div className="order-2 flex flex-col gap-5 lg:order-1 lg:sticky lg:top-6 lg:self-start">
          <CategoriesBlock
            categories={categoryItems}
            isError={categories.isError}
            isLoading={categories.isLoading}
            onChanged={invalidateCategories}
          />

          <CreateGateForm categories={categoryItems} onCreated={invalidate} />
        </div>

        {/* Right zone: the catalog, grouped by category (first on mobile). */}
        <div className="order-1 flex flex-col gap-6 lg:order-2">
          {catalogMsg && <Notice status="success">{catalogMsg}</Notice>}

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
              <EmptyState
                action={
                  <Btn
                    size="sm"
                    variant="primary"
                    onClick={() => {
                      const el = document.getElementById("create-gate-form");

                      el?.scrollIntoView({ behavior: "smooth", block: "center" });
                      el?.querySelector<HTMLInputElement>("input")?.focus();
                    }}
                  >
                    Crea tu primer gate
                  </Btn>
                }
                message="El catálogo está vacío."
              />
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
                          // flex-wrap: the name keeps line 1 while meta+actions
                          // drop to line 2 on narrow widths (full-width single
                          // column on mobile) — no horizontal overflow at 320px.
                          "flex flex-wrap items-center gap-3 px-3.5 py-3",
                          // Top border separates rows (none on the first),
                          // per the Ranger-X GatesScreen li styling.
                          i && "border-t border-separator",
                        )}
                      >
                        <div className="flex min-w-0 flex-[1_1_11rem] flex-col gap-0.5">
                          <span className="truncate text-sm font-semibold">
                            {g.name}
                          </span>
                          <span className="font-mono text-[11px] text-muted tabular-nums">
                            {formatCreated(g.created_at)}
                            {g.credit_cost > 0 &&
                              ` · ${g.credit_cost} créd./✅`}
                          </span>
                        </div>
                        <div className="flex shrink-0 flex-col items-end gap-1">
                          {/* LabelCaps for the One-Tracking-Rule (0.1em); the
                              chips carry text-foreground so the command data
                              isn't dimmer than the created-at date above. */}
                          <span className="flex items-center gap-1.5">
                            <LabelCaps>Visible</LabelCaps>
                            <MonoChip className="text-foreground">
                              {g.display_value}
                            </MonoChip>
                          </span>
                          <span className="flex items-center gap-1.5">
                            <LabelCaps>Real</LabelCaps>
                            <MonoChip className="text-foreground">
                              {g.value}
                            </MonoChip>
                          </span>
                        </div>
                        <EditGateAction
                          categories={categoryItems}
                          gate={g}
                          notify={flashCatalog}
                          onChanged={invalidate}
                        />
                        <DeleteGateAction
                          gate={g}
                          notify={flashCatalog}
                          onDeleted={invalidate}
                        />
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
  const [special, setSpecial] = useState(false);
  const [cookieMode, setCookieMode] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [okMsg, flashOk] = useFlash();

  const mutation = useMutation({
    mutationFn: () =>
      api.post<CategoryOut>("/api/admin/gate-categories", {
        name,
        special_mode: special,
        cookie_mode: cookieMode,
      }),
    onSuccess: () => {
      setName("");
      setSpecial(false);
      setCookieMode(false);
      flashOk("Categoría creada");
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
        {okMsg && <Notice status="success">{okMsg}</Notice>}

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

          <Checkbox checked={special} onChange={setSpecial}>
            Modo especial (validez por «Approveds! ✅: N», oculta créditos)
          </Checkbox>

          <Checkbox checked={cookieMode} onChange={setCookieMode}>
            Modo cookies (gate Amazon: el cliente guarda sus cookies)
          </Checkbox>

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
              <CategoryRow
                key={c.id}
                category={c}
                notify={flashOk}
                onChanged={onChanged}
              />
            ))}
          </ul>
        </div>
      </div>
    </SectionCard>
  );
}

function CategoryRow({
  category,
  notify,
  onChanged,
}: {
  category: CategoryOut;
  notify: (msg: string) => void;
  onChanged: () => void;
}) {
  // Max ONE layer open at a time (UX-DR21): the inline rename stays (a single
  // field fits the 320px column); the delete confirm lives in a ConfirmDialog.
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [name, setName] = useState(category.name);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // Optimistic special-mode: reflect the click immediately, then let the
  // refetch (source of truth) reconcile on settle. Null = no override.
  const [optimisticSpecial, setOptimisticSpecial] = useState<boolean | null>(
    null,
  );
  const [optimisticCookie, setOptimisticCookie] = useState<boolean | null>(
    null,
  );

  const rename = useMutation({
    mutationFn: () =>
      api.patch<CategoryOut>(`/api/admin/gate-categories/${category.id}`, {
        name,
      }),
    onSuccess: () => {
      setMode("view");
      setRenameError(null);
      notify("Categoría renombrada");
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

  // Special-mode toggle (special-mode feature): an immediate PATCH that keeps
  // the current name. Refetch on settle either way — the category list is the
  // source of truth, so a failed/raced toggle self-corrects on the refresh.
  const toggleSpecial = useMutation({
    mutationFn: (next: boolean) =>
      api.patch<CategoryOut>(`/api/admin/gate-categories/${category.id}`, {
        name: category.name,
        special_mode: next,
      }),
    onMutate: (next) => setOptimisticSpecial(next),
    // Clear the override on settle either way — the refetched list is truth, so
    // a failed/raced toggle self-corrects back to the server value.
    onSettled: () => {
      setOptimisticSpecial(null);
      onChanged();
    },
  });

  // Cookie-mode toggle (Amazon gate, Phase 1): mirrors toggleSpecial. Sends only
  // {name, cookie_mode} so the backend's None-leaves-untouched rule preserves
  // special_mode. Refetch on settle reconciles a failed/raced toggle.
  const toggleCookie = useMutation({
    mutationFn: (next: boolean) =>
      api.patch<CategoryOut>(`/api/admin/gate-categories/${category.id}`, {
        name: category.name,
        cookie_mode: next,
      }),
    onMutate: (next) => setOptimisticCookie(next),
    onSettled: () => {
      setOptimisticCookie(null);
      onChanged();
    },
  });

  const remove = useMutation({
    mutationFn: () =>
      api.delete<void>(`/api/admin/gate-categories/${category.id}`),
    onSuccess: () => {
      setConfirmOpen(false);
      setDeleteError(null);
      notify("Categoría eliminada");
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

      {mode === "view" && (
        <Checkbox
          checked={optimisticSpecial ?? category.special_mode}
          className={clsx(
            "text-[13px]",
            toggleSpecial.isPending && "opacity-60",
          )}
          onChange={(next) => {
            if (!toggleSpecial.isPending) toggleSpecial.mutate(next);
          }}
        >
          Modo especial
        </Checkbox>
      )}

      {mode === "view" && (
        <Checkbox
          checked={optimisticCookie ?? category.cookie_mode}
          className={clsx(
            "text-[13px]",
            toggleCookie.isPending && "opacity-60",
          )}
          onChange={(next) => {
            if (!toggleCookie.isPending) toggleCookie.mutate(next);
          }}
        >
          Modo cookies
        </Checkbox>
      )}

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
  const [displayValue, setDisplayValue] = useState("");
  const [creditCost, setCreditCost] = useState("0");
  const [categoryId, setCategoryId] = useState<number | null>(null);
  const [nameError, setNameError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [displayError, setDisplayError] = useState<string | null>(null);
  const [creditError, setCreditError] = useState<string | null>(null);
  const [categoryError, setCategoryError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [okMsg, flashOk] = useFlash();

  const mutation = useMutation({
    mutationFn: () =>
      api.post<GateOut>("/api/admin/gates", {
        value,
        name,
        display_value: displayValue,
        credit_cost: Number(creditCost),
        category_id: categoryId,
      }),
    onSuccess: () => {
      setName("");
      setValue("");
      setDisplayValue("");
      setCreditCost("0");
      flashOk("Gate creado");
      onCreated();
    },
    onError: (err) => {
      // Backend sends user-facing Spanish in `message`; route gate_exists to
      // the value field, everything else to the banner.
      if (err instanceof ApiError) {
        if (err.code === "gate_exists") setFieldError(err.message);
        else if (err.code === "invalid_gate") setCreditError(err.message);
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
    setDisplayError(null);
    setCreditError(null);
    setCategoryError(null);
    setBanner(null);
    const invalidName = validateGateName(name);
    const invalidValue = validateGateValue(value);
    const invalidDisplay = validateDisplayValue(displayValue);
    const invalidCredit = validateCreditCost(creditCost);
    const invalidCategory = categoryId === null ? "Elegí una categoría." : null;

    if (invalidName) setNameError(invalidName);
    if (invalidValue) setFieldError(invalidValue);
    if (invalidDisplay) setDisplayError(invalidDisplay);
    if (invalidCredit) setCreditError(invalidCredit);
    if (invalidCategory) setCategoryError(invalidCategory);
    if (
      invalidName ||
      invalidValue ||
      invalidDisplay ||
      invalidCredit ||
      invalidCategory
    )
      return;
    mutation.mutate();
  }

  return (
    // id is the scroll target for the empty-catalog "Crea tu primer gate" CTA.
    <div id="create-gate-form">
      {/* legendAs="h2": replaces the old "Crear gate" h2 heading. */}
      <SectionCard legend="CREAR GATE" legendAs="h2">
        <div className="flex flex-col gap-3">
          {banner && <Notice status="danger">{banner}</Notice>}
          {okMsg && <Notice status="success">{okMsg}</Notice>}

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
              label="Gate (comando real)"
              name="value"
              placeholder=".ej"
              value={value}
              onChange={(v) => {
                setValue(v);
                if (fieldError) setFieldError(null);
              }}
            />
            <p className="-mt-1 px-0.5 text-[11px] text-muted">{HINT_REAL}</p>

            <Field
              mono
              required
              error={displayError}
              label="Comando visible"
              name="display_value"
              placeholder="Lo que ve el cliente"
              value={displayValue}
              onChange={(v) => {
                setDisplayValue(v);
                if (displayError) setDisplayError(null);
              }}
            />
            <p className="-mt-1 px-0.5 text-[11px] text-muted">{HINT_VISIBLE}</p>

            <Field
              required
              error={creditError}
              inputMode="numeric"
              label="Costo en créditos (0 = gratis)"
              name="credit_cost"
              placeholder="0"
              type="number"
              value={creditCost}
              onChange={(v) => {
                setCreditCost(v);
                if (creditError) setCreditError(null);
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
    </div>
  );
}

// --- Edit (per-row dialog) ---------------------------------------------------

function EditGateAction({
  gate,
  categories,
  notify,
  onChanged,
}: {
  gate: GateOut;
  categories: CategoryOut[];
  notify: (msg: string) => void;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(gate.name);
  const [value, setValue] = useState(gate.value);
  const [displayValue, setDisplayValue] = useState(gate.display_value);
  const [creditCost, setCreditCost] = useState(String(gate.credit_cost));
  const [categoryId, setCategoryId] = useState<number | null>(gate.category_id);
  // Per-field errors mirror CreateGateForm: every invalid field lights at once
  // and anchors to its own input, instead of a single one-at-a-time Notice.
  const [nameError, setNameError] = useState<string | null>(null);
  const [valueError, setValueError] = useState<string | null>(null);
  const [displayError, setDisplayError] = useState<string | null>(null);
  const [creditError, setCreditError] = useState<string | null>(null);
  const [categoryError, setCategoryError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  function clearErrors() {
    setNameError(null);
    setValueError(null);
    setDisplayError(null);
    setCreditError(null);
    setCategoryError(null);
    setBanner(null);
  }

  const mutation = useMutation({
    mutationFn: () =>
      api.patch<GateOut>(`/api/admin/gates/${gate.id}`, {
        value,
        name,
        display_value: displayValue,
        credit_cost: Number(creditCost),
        category_id: categoryId,
      }),
    onSuccess: () => {
      setOpen(false);
      clearErrors();
      notify("Gate actualizado");
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
      // Same code→field routing as create: known codes anchor to their field,
      // everything else falls to the banner.
      if (err instanceof ApiError) {
        if (err.code === "gate_exists") setValueError(err.message);
        else if (err.code === "invalid_gate") setCreditError(err.message);
        else if (err.code === "category_not_found")
          setCategoryError(err.message);
        else setBanner(err.message);
      } else {
        setBanner("No pudimos conectar. Intenta de nuevo.");
      }
    },
  });

  function save() {
    if (mutation.isPending) return;
    clearErrors();
    const invalidName = validateGateName(name);
    const invalidValue = validateGateValue(value);
    const invalidDisplay = validateDisplayValue(displayValue);
    const invalidCredit = validateCreditCost(creditCost);
    const invalidCategory = categoryId === null ? "Elegí una categoría." : null;

    if (invalidName) setNameError(invalidName);
    if (invalidValue) setValueError(invalidValue);
    if (invalidDisplay) setDisplayError(invalidDisplay);
    if (invalidCredit) setCreditError(invalidCredit);
    if (invalidCategory) setCategoryError(invalidCategory);
    if (
      invalidName ||
      invalidValue ||
      invalidDisplay ||
      invalidCredit ||
      invalidCategory
    )
      return;
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
          setDisplayValue(gate.display_value);
          setCreditCost(String(gate.credit_cost));
          setCategoryId(gate.category_id);
          clearErrors();
          setOpen(true);
        }}
      >
        Editar
      </Btn>

      {/* role="dialog": this is a multi-field FORM, not a confirmation — the
          default alertdialog role would mis-announce it to screen readers. */}
      <ConfirmDialog
        confirmLabel={mutation.isPending ? "Guardando…" : "Guardar"}
        confirmVariant="primary"
        heading="Editar gate"
        open={open}
        pending={mutation.isPending}
        role="dialog"
        onConfirm={save}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) clearErrors();
        }}
      >
        <div className="flex flex-col gap-3">
          {banner && <Notice status="danger">{banner}</Notice>}
          {/* Editing the real command re-attributes inbound replies — the one
              expensive mistake on this page gets an explicit caution. */}
          <Notice status="warning">
            Cambiar el comando real re-atribuye las respuestas que lleguen.
          </Notice>

          <Field
            error={nameError}
            label="Nombre"
            name="name"
            value={name}
            onChange={(v) => {
              setName(v);
              if (nameError) setNameError(null);
            }}
          />

          <Field
            mono
            error={valueError}
            label="Gate (comando real)"
            name="value"
            value={value}
            onChange={(v) => {
              setValue(v);
              if (valueError) setValueError(null);
            }}
          />
          <p className="-mt-1 px-0.5 text-[11px] text-muted">{HINT_REAL}</p>

          <Field
            mono
            error={displayError}
            label="Comando visible"
            name="display_value"
            value={displayValue}
            onChange={(v) => {
              setDisplayValue(v);
              if (displayError) setDisplayError(null);
            }}
          />
          <p className="-mt-1 px-0.5 text-[11px] text-muted">{HINT_VISIBLE}</p>

          <Field
            error={creditError}
            inputMode="numeric"
            label="Costo en créditos (0 = gratis)"
            name="credit_cost"
            type="number"
            value={creditCost}
            onChange={(v) => {
              setCreditCost(v);
              if (creditError) setCreditError(null);
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
        </div>
      </ConfirmDialog>
    </>
  );
}

// --- Delete (soft-delete; confirm dialog) ------------------------------------

function DeleteGateAction({
  gate,
  notify,
  onDeleted,
}: {
  gate: GateOut;
  notify: (msg: string) => void;
  onDeleted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.delete<void>(`/api/admin/gates/${gate.id}`),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      notify("Gate eliminado");
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
