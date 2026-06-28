"use client";

// Pricing-plan catalog (feat/plan-catalog, owner-only). Mirrors the gates page
// idiom: a sticky create form in the left zone + the full catalog (active +
// retired) on the right, each row carrying an inline edit dialog and a
// soft-suggesting delete. Plans are NEVER hard-required to delete — a plan in
// use returns 409 plan_in_use and the dialog suggests deactivating instead.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";

import { api, ApiError } from "@/lib/api";
import { AdminShell } from "@/components/ui/admin-shell";
import { Btn } from "@/components/ui/btn";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Field } from "@/components/ui/field";
import { Notice } from "@/components/ui/notice";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";
import { StatePill } from "@/components/ui/state-pill";

// Local response shapes mirror the backend plan schemas (snake_case,
// end-to-end) — same explicit-interface idiom as the gates/users pages. Decimal
// fields (price_usd) ride as number|string per the JSON. Antispam is no longer a
// plan field (antispam-per-user feature) — it's a global default + per-user
// override, managed from the Usuarios page.
interface PlanOut {
  id: number;
  name: string;
  price_usd: number | string;
  duration_days: number;
  max_lines_per_batch: number;
  // Credits granted on assign/renew (credits feature). 0 ⇒ time-only plan.
  credits: number;
  is_active: boolean;
  // The gift-key default ("basic") tier — at most one plan true. Gift keys
  // grant THIS plan to a plan-less claimer (gift-keys feature).
  is_default: boolean;
  created_at: string;
}

interface PlanListResponse {
  items: PlanOut[];
  total: number;
}

const PLANS_KEY = ["admin-plans"] as const;
const PLAN_NAME_MAX = 80;

// Field bounds mirror the backend `_validate_plan_fields`: duration ≥ 1,
// max_lines ≥ 1, price ≥ 0. Validated client-side so the owner gets an inline
// message instead of a raw 400 round-trip; backend authoritative.
function validatePlanName(raw: string): string | null {
  const name = raw.trim();

  if (!name) return "Ingresá un nombre.";
  if (name.length > PLAN_NAME_MAX) return `Máximo ${PLAN_NAME_MAX} caracteres.`;

  return null;
}

// Digits-only integer ≥ min (the isPositiveInt idiom from the users page).
function validateIntAtLeast(raw: string, min: number): boolean {
  return /^\d+$/.test(raw.trim()) && Number(raw) >= min;
}

// Non-negative decimal (price): digits, optional fraction.
function validatePrice(raw: string): boolean {
  const v = raw.trim();

  return /^\d+(\.\d+)?$/.test(v) && Number(v) >= 0;
}

function formatCreated(iso: string): string {
  return new Date(iso).toLocaleDateString("es", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatPrice(value: number | string): string {
  const n = Number(value);

  return Number.isFinite(n) ? `$${n.toFixed(2)}` : String(value);
}

// One draft of the plan form fields (create + edit share the shape).
interface PlanDraft {
  name: string;
  price_usd: string;
  duration_days: string;
  max_lines_per_batch: string;
  credits: string;
  is_active: boolean;
}

const EMPTY_DRAFT: PlanDraft = {
  name: "",
  price_usd: "",
  duration_days: "",
  max_lines_per_batch: "",
  credits: "0",
  is_active: true,
};

// Per-field validation shared by create + edit. Returns the first error keyed by
// field, or null when the whole draft is valid.
function validateDraft(
  draft: PlanDraft,
): Partial<Record<keyof PlanDraft, string>> {
  const errors: Partial<Record<keyof PlanDraft, string>> = {};
  const nameError = validatePlanName(draft.name);

  if (nameError) errors.name = nameError;
  if (!validatePrice(draft.price_usd))
    errors.price_usd = "Indica un precio válido (≥ 0).";
  if (!validateIntAtLeast(draft.duration_days, 1))
    errors.duration_days = "Indica un número entero de días (≥ 1).";
  if (!validateIntAtLeast(draft.max_lines_per_batch, 1))
    errors.max_lines_per_batch = "Indica el máximo de líneas (≥ 1).";
  if (!validateIntAtLeast(draft.credits, 0))
    errors.credits = "Indica los créditos (entero ≥ 0).";

  return errors;
}

// Build the JSON payload from a draft (numbers, not strings).
function draftToPayload(draft: PlanDraft) {
  return {
    name: draft.name.trim(),
    price_usd: Number(draft.price_usd),
    duration_days: Number(draft.duration_days),
    max_lines_per_batch: Number(draft.max_lines_per_batch),
    credits: Number(draft.credits),
    is_active: draft.is_active,
  };
}

export default function AdminPlansPage() {
  const queryClient = useQueryClient();

  const plans = useQuery({
    queryKey: PLANS_KEY,
    queryFn: () => api.get<PlanListResponse>("/api/admin/plans"),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: PLANS_KEY });

  const items = plans.data?.items ?? [];

  return (
    // Only the owner reaches this page (backend guard + middleware) → owner-only
    // nav visible.
    <AdminShell gatesVisible title="Planes">
      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        {/* Left zone: create form (sticky on desktop). */}
        <div className="flex flex-col gap-5 lg:sticky lg:top-6 lg:self-start">
          <CreatePlanForm onCreated={invalidate} />
        </div>

        {/* Right zone: the catalog. */}
        <SectionCard legend="CATÁLOGO" padding="none">
          {plans.isLoading && <PanelSkeleton rows={5} />}
          {plans.isError && (
            <Notice className="m-3" status="danger">
              No pudimos cargar los planes. Recarga la página.
            </Notice>
          )}
          {plans.data &&
            (items.length === 0 ? (
              <EmptyState
                eyebrow="Planes"
                message="Todavía no hay planes. Crea el primero."
              />
            ) : (
              <ul className="m-0 list-none p-0">
                {items.map((plan, i) => (
                  <li
                    key={plan.id}
                    className={clsx(
                      "flex flex-wrap items-center gap-3 px-3.5 py-3",
                      i && "border-t border-separator",
                    )}
                  >
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-semibold">
                          {plan.name}
                        </span>
                        {plan.is_active ? (
                          <StatePill tone="success">Activo</StatePill>
                        ) : (
                          <StatePill tone="muted">Inactivo</StatePill>
                        )}
                        {plan.is_default && (
                          <StatePill tone="cyan">Keys</StatePill>
                        )}
                      </div>
                      <span className="font-mono text-[11px] text-muted tabular-nums">
                        {formatPrice(plan.price_usd)} · {plan.duration_days} d ·
                        máx {plan.max_lines_per_batch} líneas · {plan.credits}{" "}
                        créd.
                      </span>
                      <span className="font-mono text-[11px] text-[var(--faint)] tabular-nums">
                        {formatCreated(plan.created_at)}
                      </span>
                    </div>
                    <SetDefaultAction plan={plan} onChanged={invalidate} />
                    <EditPlanAction plan={plan} onChanged={invalidate} />
                    <DeletePlanAction plan={plan} onDeleted={invalidate} />
                  </li>
                ))}
              </ul>
            ))}
        </SectionCard>
      </div>
    </AdminShell>
  );
}

// --- Shared form body (create + edit dialogs) ------------------------------

function PlanFields({
  draft,
  errors,
  onChange,
}: {
  draft: PlanDraft;
  errors: Partial<Record<keyof PlanDraft, string>>;
  onChange: <K extends keyof PlanDraft>(key: K, value: PlanDraft[K]) => void;
}) {
  return (
    <>
      <Field
        required
        error={errors.name}
        label="Nombre"
        name="name"
        placeholder="Plan mensual"
        value={draft.name}
        onChange={(v) => onChange("name", v)}
      />

      <div className="flex gap-2">
        <Field
          required
          className="flex-1"
          error={errors.price_usd}
          label="Precio (USD)"
          name="price_usd"
          placeholder="10.00"
          type="number"
          value={draft.price_usd}
          onChange={(v) => onChange("price_usd", v)}
        />
        <Field
          required
          className="flex-1"
          error={errors.duration_days}
          label="Días"
          name="duration_days"
          placeholder="30"
          type="number"
          value={draft.duration_days}
          onChange={(v) => onChange("duration_days", v)}
        />
      </div>

      <Field
        required
        error={errors.max_lines_per_batch}
        label="Máx. líneas"
        name="max_lines_per_batch"
        placeholder="500"
        type="number"
        value={draft.max_lines_per_batch}
        onChange={(v) => onChange("max_lines_per_batch", v)}
      />

      <Field
        required
        error={errors.credits}
        label="Créditos incluidos (0 = ninguno)"
        name="credits"
        placeholder="0"
        type="number"
        value={draft.credits}
        onChange={(v) => onChange("credits", v)}
      />

      <label className="flex cursor-pointer items-center gap-2 text-sm text-foreground">
        <input
          checked={draft.is_active}
          className="rx-focus h-4 w-4 accent-[var(--accent)]"
          name="is_active"
          type="checkbox"
          onChange={(e) => onChange("is_active", e.target.checked)}
        />
        Activo (visible para asignar/renovar)
      </label>
    </>
  );
}

// --- Create ----------------------------------------------------------------

function CreatePlanForm({ onCreated }: { onCreated: () => void }) {
  const [draft, setDraft] = useState<PlanDraft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<
    Partial<Record<keyof PlanDraft, string>>
  >({});
  const [banner, setBanner] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.post<PlanOut>("/api/admin/plans", draftToPayload(draft)),
    onSuccess: () => {
      setDraft(EMPTY_DRAFT);
      setErrors({});
      onCreated();
    },
    onError: (err) => {
      // Backend sends user-facing Spanish in `message`; route plan_name_taken
      // to the name field, invalid_plan (field-specific copy) to the banner,
      // everything else to the banner.
      if (err instanceof ApiError) {
        if (err.code === "plan_name_taken") setErrors({ name: err.message });
        else setBanner(err.message);
      } else {
        setBanner("No pudimos conectar. Intenta de nuevo.");
      }
    },
  });

  function onChange<K extends keyof PlanDraft>(key: K, value: PlanDraft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
    if (errors[key]) setErrors((e) => ({ ...e, [key]: undefined }));
  }

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    // Enter can re-submit while a POST is in flight (gates-page lesson).
    if (mutation.isPending) return;
    setBanner(null);
    const next = validateDraft(draft);

    if (Object.keys(next).length > 0) {
      setErrors(next);

      return;
    }
    setErrors({});
    mutation.mutate();
  }

  return (
    // legendAs="h2": the legend replaces a real heading under the page h1.
    <SectionCard legend="CREAR PLAN" legendAs="h2">
      <div className="flex flex-col gap-3">
        {banner && <Notice status="danger">{banner}</Notice>}

        <form className="flex flex-col gap-3" onSubmit={onSubmit}>
          <PlanFields draft={draft} errors={errors} onChange={onChange} />

          <Btn
            full
            disabled={mutation.isPending}
            icon="plus"
            type="submit"
            variant="primary"
          >
            {mutation.isPending ? "Creando…" : "Crear plan"}
          </Btn>
        </form>
      </div>
    </SectionCard>
  );
}

// --- Edit (per-row dialog) --------------------------------------------------

function EditPlanAction({
  plan,
  onChanged,
}: {
  plan: PlanOut;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<PlanDraft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<
    Partial<Record<keyof PlanDraft, string>>
  >({});
  const [banner, setBanner] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.patch<PlanOut>(`/api/admin/plans/${plan.id}`, draftToPayload(draft)),
    onSuccess: () => {
      setOpen(false);
      setBanner(null);
      setErrors({});
      onChanged();
    },
    onError: (err) => {
      // Deleted in another tab → drop the ghost row.
      if (err instanceof ApiError && err.code === "plan_not_found") {
        setOpen(false);
        onChanged();

        return;
      }
      if (err instanceof ApiError) {
        if (err.code === "plan_name_taken") setErrors({ name: err.message });
        else setBanner(err.message);
      } else {
        setBanner("No pudimos conectar. Intenta de nuevo.");
      }
    },
  });

  function openDialog() {
    // Re-sync the draft from the current row each open (a prior cancel or an
    // external refresh may have moved it). Decimals → trimmed display strings.
    setDraft({
      name: plan.name,
      price_usd: String(Number(plan.price_usd)),
      duration_days: String(plan.duration_days),
      max_lines_per_batch: String(plan.max_lines_per_batch),
      credits: String(plan.credits),
      is_active: plan.is_active,
    });
    setErrors({});
    setBanner(null);
    setOpen(true);
  }

  function onChange<K extends keyof PlanDraft>(key: K, value: PlanDraft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
    if (errors[key]) setErrors((e) => ({ ...e, [key]: undefined }));
  }

  function save() {
    if (mutation.isPending) return;
    setBanner(null);
    const next = validateDraft(draft);

    if (Object.keys(next).length > 0) {
      setErrors(next);

      return;
    }
    setErrors({});
    mutation.mutate();
  }

  return (
    <>
      <Btn size="sm" variant="secondary" onClick={openDialog}>
        Editar
      </Btn>

      <ConfirmDialog
        confirmLabel={mutation.isPending ? "Guardando…" : "Guardar"}
        confirmVariant="primary"
        heading="Editar plan"
        open={open}
        pending={mutation.isPending}
        onConfirm={save}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) {
            setErrors({});
            setBanner(null);
          }
        }}
      >
        <div className="flex flex-col gap-3">
          {banner && <Notice status="danger">{banner}</Notice>}
          <PlanFields draft={draft} errors={errors} onChange={onChange} />
        </div>
      </ConfirmDialog>
    </>
  );
}

// --- Set as gift-key default ("basic" tier) ----------------------------------
//
// Owner-only (this whole page is owner-gated). Flagging one plan clears the
// prior default server-side; gift keys then grant THIS plan to a plan-less
// claimer. A plan already flagged shows the "Keys" badge instead of the button.

function SetDefaultAction({
  plan,
  onChanged,
}: {
  plan: PlanOut;
  onChanged: () => void;
}) {
  const mutation = useMutation({
    mutationFn: () => api.post<PlanOut>(`/api/admin/plans/${plan.id}/default`),
    onSuccess: onChanged,
    onError: (err) => {
      // Gone in another tab → just refresh; no other error path on this
      // owner-only action.
      if (err instanceof ApiError && err.code === "plan_not_found") onChanged();
    },
  });

  if (plan.is_default) return null; // the "Keys" badge already marks it

  return (
    <Btn
      disabled={mutation.isPending}
      size="sm"
      variant="secondary"
      onClick={() => mutation.mutate()}
    >
      {mutation.isPending ? "…" : "Usar para keys"}
    </Btn>
  );
}

// --- Delete (with plan_in_use suggestion) ------------------------------------

function DeletePlanAction({
  plan,
  onDeleted,
}: {
  plan: PlanOut;
  onDeleted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.delete<void>(`/api/admin/plans/${plan.id}`),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      onDeleted();
    },
    onError: (err) => {
      // Already gone in another tab → the desired outcome; just refresh.
      if (err instanceof ApiError && err.code === "plan_not_found") {
        setOpen(false);
        onDeleted();

        return;
      }
      // plan_in_use: the plan is referenced by ≥1 client — the backend's
      // Spanish copy explains it; we add the "deactivate instead" nudge and
      // keep the dialog open.
      if (err instanceof ApiError && err.code === "plan_in_use") {
        setError(
          `${err.message} En su lugar, edítalo y desmárcalo como activo para retirarlo.`,
        );

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
        heading={`¿Eliminar el plan “${plan.name}”?`}
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
