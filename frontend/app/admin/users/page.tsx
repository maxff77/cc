"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";

import { api, ApiError } from "@/lib/api";
import { AdminShell } from "@/components/ui/admin-shell";
import { EmptyState } from "@/components/ui/empty-state";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";
import { Btn } from "@/components/ui/btn";
import { Field } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { Notice } from "@/components/ui/notice";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { StatePill } from "@/components/ui/state-pill";
import { LabelCaps } from "@/components/ui/label-caps";

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
  contact: string | null;
  // The tenant's credit balance (credits feature). Shown in the table; the
  // owner recharges it via the per-row action.
  credit_balance: number;
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

// Plan catalog row (feat/plan-catalog) — mirrors the backend PlanOut; only the
// fields the selector needs are read. Decimals ride as number|string.
interface PlanOut {
  id: number;
  name: string;
  price_usd: number | string;
  duration_days: number;
  antispam_seconds: number | string;
  max_lines_per_batch: number;
  is_active: boolean;
  created_at: string;
}

interface PlanListResponse {
  items: PlanOut[];
  total: number;
}

const USERS_KEY = ["admin-users"] as const;
const ME_KEY = ["me"] as const;
const PLANS_KEY = ["admin-plans"] as const;

// Active-plan options for the create/renew selectors. Uses the
// admin-accessible /admin/plans/active read (full plan CRUD at /admin/plans is
// owner-only) so a non-owner admin can still pick a tier when creating/renewing
// a client. The endpoint already returns active-only; the filter is a
// belt-and-braces guard.
function useActivePlans() {
  const query = useQuery({
    queryKey: PLANS_KEY,
    queryFn: () => api.get<PlanListResponse>("/api/admin/plans/active"),
  });
  const active = (query.data?.items ?? []).filter((p) => p.is_active);

  return { ...query, active };
}

// Compact "Name · 30 d · $10.00" label so the owner picks a tier by its terms.
function planLabel(plan: PlanOut): string {
  const price = Number(plan.price_usd);
  const priceText = Number.isFinite(price) ? `$${price.toFixed(2)}` : "";

  return `${plan.name} · ${plan.duration_days} d · ${priceText}`;
}

// Shared plan selector for the create-client form and the renew dialog. Empty
// catalog renders a single disabled hint option with zero behavior (mirrors the
// gates page CategorySelect idiom).
function PlanSelect({
  plans,
  value,
  onChange,
  label = "Plan",
  error,
  disabled,
}: {
  plans: PlanOut[];
  value: number | null;
  onChange: (id: number | null) => void;
  label?: string;
  error?: string | null;
  disabled?: boolean;
}) {
  const empty = plans.length === 0;
  const options = empty
    ? [{ id: "__none", label: "Primero crea un plan." }]
    : plans.map((p) => ({ id: String(p.id), label: planLabel(p) }));

  return (
    <Select
      className="w-full"
      disabled={disabled || empty}
      error={error}
      label={label}
      options={options}
      placeholder="Elegí un plan"
      value={value === null ? null : String(value)}
      onChange={(id) => onChange(id === "__none" ? null : Number(id))}
    />
  );
}

// Role pill tone: owner→accent, admin→cyan, client→muted (per Ranger-X handoff).
const ROLE_TONE: Record<string, "accent" | "cyan" | "muted"> = {
  owner: "accent",
  admin: "cyan",
  client: "muted",
};

function formatExpiry(iso: string | null): string {
  if (!iso) return "—";

  return new Date(iso).toLocaleDateString("es", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// The backend stores the handle canonical (sin '@'); we re-add '@' for display
// and link straight to the Telegram chat so the operator can write for renewal.
function ContactLink({ contact }: { contact: string | null }) {
  if (!contact) return <span className="text-[var(--faint)]">—</span>;

  return (
    <a
      className="text-sm text-accent underline hover:text-foreground"
      href={`https://t.me/${contact}`}
      rel="noopener noreferrer"
      target="_blank"
    >
      @{contact}
    </a>
  );
}

// Tokenized table header cell (LabelCaps-style: caps, 0.14em tracking, muted).
function Th({
  children,
  align = "left",
}: {
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      className={clsx(
        "px-3.5 pb-3 pt-4 text-[10px] font-bold uppercase tracking-[0.14em] text-muted",
        align === "right" ? "text-right" : "text-left",
      )}
    >
      {children}
    </th>
  );
}

// One labelled field inside a user card (the mobile reflow of a table cell).
function CardField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <LabelCaps>{label}</LabelCaps>
      <div className="text-sm">{children}</div>
    </div>
  );
}

// Phone/tablet card — the table row reflowed to a single column. Reuses the
// exact action components and helpers the table uses, so behaviour is identical
// across breakpoints (no second information architecture).
function UserCard({
  user: u,
  isOwner,
  onChanged,
}: {
  user: UserOut;
  isOwner: boolean;
  onChanged: () => void;
}) {
  const isClient = u.role === "client";

  return (
    <li className="flex flex-col gap-3.5 rounded-[var(--radius-field)] border border-border bg-surface-secondary p-3.5">
      <div className="flex items-start justify-between gap-2.5">
        <span className="min-w-0 break-all font-mono text-[0.82rem] font-semibold leading-snug text-foreground">
          {u.email}
        </span>
        <StatePill tone={ROLE_TONE[u.role] ?? "muted"}>{u.role}</StatePill>
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-3">
        <CardField label="Contacto">
          <ContactLink contact={u.contact} />
        </CardField>
        <CardField label="Vence">
          <span className="font-mono text-[0.72rem] tabular-nums text-muted">
            {formatExpiry(u.expires_at)}
          </span>
        </CardField>
        {isClient && (
          <>
            <CardField label="Créditos">
              <span className="font-mono text-[0.82rem] tabular-nums text-foreground">
                {u.credit_balance}
              </span>
            </CardField>
            <CardField label="Estado">
              {u.is_blocked ? (
                <StatePill tone="danger">Bloqueado</StatePill>
              ) : (
                <StatePill tone="success">Activo</StatePill>
              )}
            </CardField>
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-1.5 border-t border-separator pt-3">
        {isClient ? (
          <>
            <Link
              className="tap-44 rx-focus inline-flex shrink-0 items-center rounded-[var(--radius-field)] border border-border px-3 py-1.5 font-display text-[13px] font-semibold tracking-[0.02em] text-muted transition-colors hover:text-foreground"
              href={`/admin/tenants/${u.tenant_id}`}
            >
              Sesiones
            </Link>
            <ClientLifecycleActions
              isOwner={isOwner}
              user={u}
              onChanged={onChanged}
            />
          </>
        ) : isOwner && u.role === "admin" ? (
          <DeleteAdminAction
            email={u.email}
            userId={u.id}
            onDeleted={onChanged}
          />
        ) : (
          <LabelCaps>Sin acciones</LabelCaps>
        )}
      </div>
    </li>
  );
}

export default function AdminUsersPage() {
  const queryClient = useQueryClient();
  // Segmented create switch: which form to show. The "admin" tab is owner-only;
  // when the operator is a plain admin only the "client" branch ever renders.
  const [tab, setTab] = useState<"client" | "admin">("client");

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
          {/* Segmented create switch (Crear cliente | Crear admin). The admin
              option only exists for owners — admins can create clients only. */}
          {isOwner && (
            <div className="flex gap-1.5 rounded-[var(--radius-field)] border border-border bg-surface-secondary p-1">
              {(
                [
                  ["client", "Crear cliente"],
                  ["admin", "Crear admin"],
                ] as const
              ).map(([id, lbl]) => (
                <button
                  key={id}
                  className={clsx(
                    "rx-focus flex-1 rounded-[var(--radius-sm)] px-2.5 py-2 font-display text-[13px] font-semibold tracking-[0.02em] transition-colors",
                    tab === id
                      ? "btn-fill text-white"
                      : "text-muted hover:text-foreground",
                  )}
                  type="button"
                  onClick={() => setTab(id)}
                >
                  {lbl}
                </button>
              ))}
            </div>
          )}

          {/* One form, keyed by kind so switching tabs resets its draft state.
              For non-owners the switch is hidden and only the client form shows. */}
          <CreateUserForm
            key={isOwner ? tab : "client"}
            kind={isOwner ? tab : "client"}
            title={
              (isOwner ? tab : "client") === "admin"
                ? "Crear admin"
                : "Crear cliente"
            }
            onCreated={() =>
              queryClient.invalidateQueries({ queryKey: USERS_KEY })
            }
          />

          {/* Owner knob: admission-control cap (Story 4.2). */}
          {isOwner && <AdmissionControlCard />}

          {/* Owner knob: constant send interval (configurable pacing). */}
          {isOwner && <SendIntervalCard />}
        </div>

        {/* Right zone: the users table. */}
        <SectionCard legend="USUARIOS" padding="none">
          {users.isLoading && <PanelSkeleton rows={5} />}
          {users.isError && (
            <Notice className="m-3" status="danger">
              No pudimos cargar los usuarios. Recarga la página.
            </Notice>
          )}
          {users.data &&
            (users.data.items.length === 0 ? (
              <EmptyState
                eyebrow="Usuarios"
                message="Todavía no hay clientes."
              />
            ) : (
              <>
                {/* Desktop: the dense 7-column table. */}
                <div className="hidden rx-scroll overflow-x-auto lg:block">
                  <table className="w-full min-w-[640px] border-collapse">
                  <thead>
                    <tr>
                      <Th>Correo</Th>
                      <Th>Rol</Th>
                      <Th>Contacto</Th>
                      <Th>Vence</Th>
                      <Th align="right">Créditos</Th>
                      <Th>Estado</Th>
                      <Th align="right">Acciones</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.data.items.map((u, i) => (
                      <tr
                        key={u.id}
                        className={clsx(i > 0 && "border-t border-separator")}
                      >
                        <td className="break-all px-3.5 py-3.5 font-mono text-[0.8rem] font-semibold text-foreground">
                          {u.email}
                        </td>
                        <td className="px-3.5 py-3.5">
                          <StatePill tone={ROLE_TONE[u.role] ?? "muted"}>
                            {u.role}
                          </StatePill>
                        </td>
                        <td className="px-3.5 py-3.5">
                          <ContactLink contact={u.contact} />
                        </td>
                        <td className="px-3.5 py-3.5 font-mono text-[0.72rem] tabular-nums text-muted">
                          {formatExpiry(u.expires_at)}
                        </td>
                        <td className="px-3.5 py-3.5 text-right font-mono text-[0.78rem] tabular-nums text-foreground">
                          {u.role === "client" ? (
                            u.credit_balance
                          ) : (
                            <span className="text-[var(--faint)]">—</span>
                          )}
                        </td>
                        <td className="px-3.5 py-3.5">
                          {u.role === "client" ? (
                            u.is_blocked ? (
                              <StatePill tone="danger">Bloqueado</StatePill>
                            ) : (
                              <StatePill tone="success">Activo</StatePill>
                            )
                          ) : (
                            <span className="text-[var(--faint)]">—</span>
                          )}
                        </td>
                        <td className="px-3.5 py-3.5">
                          <div className="flex flex-wrap items-center justify-end gap-1.5">
                            {u.role === "client" ? (
                              <>
                                <Link
                                  className="rx-focus inline-flex shrink-0 items-center rounded-[var(--radius-field)] px-3 py-1.5 font-display text-[13px] font-semibold tracking-[0.02em] text-muted transition-colors hover:text-foreground"
                                  href={`/admin/tenants/${u.tenant_id}`}
                                >
                                  Sesiones
                                </Link>
                                <ClientLifecycleActions
                                  isOwner={isOwner}
                                  user={u}
                                  onChanged={() =>
                                    queryClient.invalidateQueries({
                                      queryKey: USERS_KEY,
                                    })
                                  }
                                />
                              </>
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
                              <LabelCaps>Sin acciones</LabelCaps>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  </table>
                </div>
                {/* Phone/tablet: one card per user — every field reflowed to a
                    single column, with touch-sized actions. Same data, no
                    horizontal scroll. */}
                <ul className="flex flex-col gap-3 p-3 lg:hidden">
                  {users.data.items.map((u) => (
                    <UserCard
                      key={u.id}
                      isOwner={isOwner}
                      user={u}
                      onChanged={() =>
                        queryClient.invalidateQueries({ queryKey: USERS_KEY })
                      }
                    />
                  ))}
                </ul>
              </>
            ))}
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
  // Plan is now a CATALOG pick (feat/plan-catalog): the selector sends plan_id;
  // the backend derives expires_at from the plan's duration_days. The legacy
  // plan_days free-input is gone (the page no longer offers it).
  const [planId, setPlanId] = useState<number | null>(null);
  const [contact, setContact] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [contactError, setContactError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  // Active plans for the client selector (admins carry no plan). Shared query.
  const plans = useActivePlans();

  const mutation = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = { email, password, role: kind };

      if (kind === "client") payload.plan_id = planId;
      // Optional — only send when filled; the backend normalizes (strips '@').
      if (contact.trim()) payload.contact = contact.trim();

      return api.post<UserOut>("/api/admin/users", payload);
    },
    onSuccess: () => {
      setEmail("");
      setPassword("");
      setPlanId(null);
      setContact("");
      onCreated();
    },
    onError: (err) => {
      // The backend already sends user-facing Spanish in `message`; route it to
      // the relevant field by `code` instead of re-stating the copy here.
      if (err instanceof ApiError) {
        if (err.code === "email_taken") setEmailError(err.message);
        // invalid_plan (unknown/inactive plan) lands on the plan selector.
        else if (err.code === "invalid_plan") setPlanError(err.message);
        else if (err.code === "invalid_contact") setContactError(err.message);
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
    setContactError(null);
    setBanner(null);

    if (kind === "client" && planId === null) {
      setPlanError("Elegí un plan.");

      return;
    }
    mutation.mutate();
  }

  return (
    // legendAs="h2": the legend replaces the old "Crear cliente"/"Crear
    // admin" h2 headings — keep the document outline under the page h1.
    <SectionCard legend={title} legendAs="h2">
      {banner && (
        <Notice className="mb-3" status="danger">
          {banner}
        </Notice>
      )}

      <form className="flex flex-col gap-3" onSubmit={onSubmit}>
        <Field
          required
          error={emailError}
          label="Correo"
          name="email"
          placeholder="cliente@correo.com"
          type="email"
          value={email}
          onChange={(v) => {
            setEmail(v);
            if (emailError) setEmailError(null);
          }}
        />

        <Field
          required
          label="Contraseña"
          name="password"
          placeholder="••••••••"
          type="password"
          value={password}
          onChange={setPassword}
        />

        {kind === "client" && (
          <PlanSelect
            error={planError}
            plans={plans.active}
            value={planId}
            onChange={(id) => {
              setPlanId(id);
              if (planError) setPlanError(null);
            }}
          />
        )}

        {/* Client-only: contact is for renewal outreach; admins carry none. */}
        {kind === "client" && (
          <Field
            error={contactError}
            label="Telegram (opcional)"
            name="contact"
            placeholder="@usuario"
            value={contact}
            onChange={(v) => {
              setContact(v);
              if (contactError) setContactError(null);
            }}
          />
        )}

        <Btn
          full
          disabled={mutation.isPending}
          icon="plus"
          type="submit"
          variant="primary"
        >
          {mutation.isPending ? "Creando…" : "Crear"}
        </Btn>
      </form>
    </SectionCard>
  );
}

// --- Admission control (Story 4.2, owner only) -----------------------------

interface AdmissionOut {
  max_active_senders: number;
}

const ADMISSION_KEY = ["admin-admission"] as const;
const ADMISSION_CAP_MAX = 1000;

// Digits-only gate that ALSO admits 0 — 0 disables
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
    <SectionCard legend="Control de admisión" legendAs="h2">
      <p className="mb-3 text-sm leading-relaxed text-muted">
        Máximo de envíos activos a la vez; los lotes que excedan el límite
        esperan en cola. 0 desactiva el límite: todos los lotes entran de
        inmediato (degradación adaptativa pura).
      </p>

      {banner && (
        <Notice className="mb-3" status="danger">
          {banner}
        </Notice>
      )}

      {admission.isError ? (
        <Notice status="danger">
          No pudimos cargar el límite. Recarga la página.
        </Notice>
      ) : (
        <form
          className="flex flex-col gap-3 sm:flex-row sm:items-end"
          onSubmit={onSubmit}
        >
          <Field
            required
            className="sm:w-40"
            disabled={admission.isLoading}
            error={error}
            label="Envíos activos máx."
            name="max_active_senders"
            placeholder="0"
            type="number"
            value={value}
            onChange={(v) => {
              setDraft(v);
              if (error) setError(null);
            }}
          />

          <Btn
            className="sm:mb-1"
            disabled={mutation.isPending || admission.isLoading}
            type="submit"
            variant="primary"
          >
            {mutation.isPending ? "Guardando…" : "Guardar"}
          </Btn>
        </form>
      )}
    </SectionCard>
  );
}

// --- Send interval (configurable pacing, owner only) -----------------------

interface IntervalOut {
  interval_seconds: number;
}

const INTERVAL_KEY = ["admin-interval"] as const;
// Anti-ban floor removed on owner request (testing): lower bound is now 0.
const INTERVAL_MIN = 0;
const INTERVAL_MAX = 30;

// Decimal-aware gate (0.5s steps allowed); backend re-enforces 0..30.
function isValidInterval(value: string): boolean {
  const v = value.trim();
  const n = Number(v);

  return /^\d+(\.\d+)?$/.test(v) && n >= INTERVAL_MIN && n <= INTERVAL_MAX;
}

function SendIntervalCard() {
  const queryClient = useQueryClient();
  // null = untouched → render the server value; editing overrides it.
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const interval = useQuery({
    queryKey: INTERVAL_KEY,
    queryFn: () => api.get<IntervalOut>("/api/admin/interval"),
  });

  const mutation = useMutation({
    mutationFn: (seconds: number) =>
      api.put<IntervalOut>("/api/admin/interval", {
        interval_seconds: seconds,
      }),
    onSuccess: (data) => {
      setDraft(null);
      setBanner(null);
      queryClient.setQueryData(INTERVAL_KEY, data);
    },
    onError: (err) => {
      // invalid_send_interval (and anything else) carries the server's
      // Spanish message — render it verbatim ({code, message} contract).
      setBanner(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  const value = draft ?? String(interval.data?.interval_seconds ?? "");

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (mutation.isPending) return;
    setError(null);
    setBanner(null);

    if (!isValidInterval(value)) {
      setError(
        `Indica un intervalo entre ${INTERVAL_MIN} y ${INTERVAL_MAX} segundos.`,
      );

      return;
    }
    mutation.mutate(Number(value));
  }

  return (
    <SectionCard legend="Intervalo de envío" legendAs="h2">
      <p className="mb-3 text-sm leading-relaxed text-muted">
        Segundos entre cada mensaje en la cuenta compartida. Bajarlo acelera el
        envío pero AUMENTA el riesgo de baneo de Telegram. El piso de seguridad
        fue retirado: puedes bajarlo hasta 0s — úsalo con cuidado. Aplica en
        vivo, sin reinicio.
      </p>

      {banner && (
        <Notice className="mb-3" status="danger">
          {banner}
        </Notice>
      )}

      {interval.isError ? (
        <Notice status="danger">
          No pudimos cargar el intervalo. Recarga la página.
        </Notice>
      ) : (
        <form
          className="flex flex-col gap-3 sm:flex-row sm:items-end"
          onSubmit={onSubmit}
        >
          <Field
            required
            className="sm:w-40"
            disabled={interval.isLoading}
            error={error}
            label="Segundos por envío"
            name="interval_seconds"
            placeholder="4"
            type="number"
            value={value}
            onChange={(v) => {
              setDraft(v);
              if (error) setError(null);
            }}
          />

          <Btn
            className="sm:mb-1"
            disabled={mutation.isPending || interval.isLoading}
            type="submit"
            variant="primary"
          >
            {mutation.isPending ? "Guardando…" : "Guardar"}
          </Btn>
        </form>
      )}
    </SectionCard>
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
        confirmLabel={mutation.isPending ? "Eliminando…" : "Sí, eliminar"}
        confirmVariant="danger"
        heading={`¿Eliminar este admin? (${email})`}
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

// --- Client lifecycle: renew + block/unblock (Story 1.5) -----------------
// Horizontal button row, constant row height — anything that used to expand
// inline now lives in a ConfirmDialog (ui-polish-spec §3.5).

function ClientLifecycleActions({
  user,
  isOwner,
  onChanged,
}: {
  user: UserOut;
  isOwner: boolean;
  onChanged: () => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <RenewAction userId={user.id} onChanged={onChanged} />
      {/* Recharge is owner-only (backend require_owner) — hidden for admins. */}
      {isOwner && <RechargeCreditsAction user={user} onChanged={onChanged} />}
      <EditContactAction user={user} onChanged={onChanged} />
      <BlockAction user={user} onChanged={onChanged} />
      <ResetPasswordAction user={user} onChanged={onChanged} />
    </div>
  );
}

// --- Recharge credits (credits feature, owner only) -----------------------
// Absolute set: the dialog pre-fills the current balance; the owner types the
// new total. Mirrors the EditContact dialog shape.

function RechargeCreditsAction({
  user,
  onChanged,
}: {
  user: UserOut;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(String(user.credit_balance));
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.post<UserOut>(`/api/admin/users/${user.id}/credits`, {
        credit_balance: Number(value),
      }),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      onChanged();
    },
    onError: (err) => {
      // invalid_credits (and anything else) carries the server's Spanish copy.
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos guardar. Intenta de nuevo.",
      );
    },
  });

  function submit() {
    if (mutation.isPending) return;
    if (!/^\d+$/.test(value.trim())) {
      setError("Indica un número entero ≥ 0.");

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
          setValue(String(user.credit_balance));
          setError(null);
          setOpen(true);
        }}
      >
        Créditos
      </Btn>

      <ConfirmDialog
        confirmLabel={mutation.isPending ? "Guardando…" : "Guardar"}
        confirmVariant="primary"
        heading="Recargar créditos"
        open={open}
        pending={mutation.isPending}
        onConfirm={submit}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
      >
        <div className="flex flex-col gap-3">
          <Field
            label="Saldo de créditos"
            name="credit_balance"
            placeholder="0"
            type="number"
            value={value}
            onChange={(v) => {
              setValue(v);
              if (error) setError(null);
            }}
          />

          {error && <Notice status="danger">{error}</Notice>}
        </div>
      </ConfirmDialog>
    </>
  );
}

// --- Edit Telegram contact (spec-client-telegram-contact) -----------------
// Same dialog shape as RenewAction: a small form in a ConfirmDialog, constant
// row height. Empty input clears the contact (persists NULL).

function EditContactAction({
  user,
  onChanged,
}: {
  user: UserOut;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [contact, setContact] = useState(user.contact ?? "");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.post<UserOut>(`/api/admin/users/${user.id}/contact`, {
        contact: contact.trim(),
      }),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      onChanged();
    },
    onError: (err) => {
      // invalid_contact (and anything else) carries the server's Spanish copy.
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos guardar. Intenta de nuevo.",
      );
    },
  });

  return (
    <>
      <Btn
        size="sm"
        variant="secondary"
        onClick={() => {
          // Re-sync the draft to the current value each open (a prior cancel or
          // an external refresh may have moved it).
          setContact(user.contact ?? "");
          setError(null);
          setOpen(true);
        }}
      >
        Contacto
      </Btn>

      <ConfirmDialog
        confirmLabel={mutation.isPending ? "Guardando…" : "Guardar"}
        confirmVariant="primary"
        heading="Contacto de Telegram"
        open={open}
        pending={mutation.isPending}
        onConfirm={() => mutation.mutate()}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
      >
        <div className="flex flex-col gap-3">
          <Field
            label="Usuario (vacío para quitar)"
            name="contact"
            placeholder="@usuario"
            value={contact}
            onChange={(v) => {
              setContact(v);
              if (error) setError(null);
            }}
          />

          {error && <Notice status="danger">{error}</Notice>}
        </div>
      </ConfirmDialog>
    </>
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
  // Renew is now a catalog pick XOR a manual end-date (feat/plan-catalog). The
  // legacy free "Días" input is gone: picking a plan sends plan_id and the
  // backend extends expires_at from the plan's duration_days
  // (max(now, current) + duration). The date stays as a manual override.
  const [planId, setPlanId] = useState<number | null>(null);
  const [date, setDate] = useState("");
  const [error, setError] = useState<string | null>(null);

  const plans = useActivePlans();

  const mutation = useMutation({
    mutationFn: () => {
      // Exactly one mode: plan → plan_id; otherwise the date as end-of-day in
      // the ADMIN'S timezone (not hardcoded Z) — formatExpiry renders in local
      // time, so this keeps the Vence column showing the picked day.
      const payload =
        planId !== null
          ? { plan_id: planId }
          : { expires_at: new Date(`${date}T23:59:59`).toISOString() };

      return api.post<UserOut>(`/api/admin/users/${userId}/renew`, payload);
    },
    onSuccess: () => {
      setOpen(false);
      setPlanId(null);
      setDate("");
      setError(null);
      onChanged();
    },
    onError: (err) => {
      // Backend sends Spanish in `message` for invalid_renewal / invalid_plan.
      setError(
        err instanceof ApiError
          ? err.message
          : "No pudimos renovar. Intenta de nuevo.",
      );
    },
  });

  function submit() {
    setError(null);
    const hasPlan = planId !== null;
    const hasDate = date.trim() !== "";

    if (hasPlan === hasDate) {
      setError("Elegí solo un plan o solo una fecha.");

      return;
    }
    mutation.mutate();
  }

  return (
    <>
      <Btn
        icon="refresh"
        size="sm"
        variant="secondary"
        onClick={() => setOpen(true)}
      >
        Renovar
      </Btn>

      <ConfirmDialog
        confirmLabel={mutation.isPending ? "Renovando…" : "Renovar"}
        confirmVariant="primary"
        heading="Renovar plan"
        open={open}
        pending={mutation.isPending}
        onConfirm={submit}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
      >
        <div className="flex flex-col gap-3">
          <PlanSelect
            plans={plans.active}
            value={planId}
            onChange={(id) => {
              setPlanId(id);
              if (error) setError(null);
            }}
          />

          <Field
            label="Hasta (fecha manual)"
            name="expires_at"
            type="date"
            value={date}
            onChange={(v) => {
              setDate(v);
              if (error) setError(null);
            }}
          />

          {error && <Notice status="danger">{error}</Notice>}
        </div>
      </ConfirmDialog>
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
  // error renders as a compact Notice under the button — the documented
  // exception for single-press action errors (ui-polish-spec §3.5).
  if (user.is_blocked) {
    return (
      <div className="flex flex-col gap-1">
        <Btn
          disabled={mutation.isPending}
          size="sm"
          variant="secondary"
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? "Desbloqueando…" : "Desbloquear"}
        </Btn>
        {error && (
          <Notice className="mt-1" status="danger">
            {error}
          </Notice>
        )}
      </div>
    );
  }

  // Block closes the client's live session → confirm dialog.
  return (
    <>
      <Btn
        size="sm"
        variant="danger"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        Bloquear
      </Btn>

      <ConfirmDialog
        confirmLabel={mutation.isPending ? "Bloqueando…" : "Sí, bloquear"}
        confirmVariant="danger"
        heading={`¿Bloquear a ${user.email}? Su sesión se cerrará al instante.`}
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
      <Btn
        size="sm"
        variant="secondary"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        Resetear
      </Btn>

      {tempPassword ? (
        // The temp-password view. Closing by ANY route runs dismiss(), which
        // destroys the exactly-once password — never recoverable (AC1). The
        // backdrop/Escape paths are wired straight to dismiss via onOpenChange.
        <ConfirmDialog
          hideCancel
          confirmLabel="Listo"
          confirmVariant="primary"
          heading="Contraseña temporal"
          open={open}
          onConfirm={() => {
            setOpen(false);
            dismiss();
          }}
          onOpenChange={(o) => {
            // No close route bypasses dismiss(): backdrop/Escape also destroy
            // the one-time password, by design.
            if (!o) {
              setOpen(false);
              dismiss();
            }
          }}
        >
          {/* Single-action view: "Listo" is the only footer button (hideCancel)
              so there's no ambiguous second close. Copy lives in the body. */}
          <div className="flex flex-col gap-2">
            <span className="font-mono text-sm text-foreground">
              {tempPassword}
            </span>
            <span className="text-sm text-muted">
              Cópiala ahora: no volverá a mostrarse.
            </span>
            <button
              className="rx-focus self-start rounded-[var(--radius-field)] border border-border bg-surface-secondary px-3 py-1.5 font-display text-[13px] font-semibold tracking-[0.02em] text-foreground transition-colors hover:bg-surface-tertiary"
              type="button"
              onClick={copy}
            >
              {copied ? "Copiada" : "Copiar"}
            </button>
            {error && <Notice status="danger">{error}</Notice>}
          </div>
        </ConfirmDialog>
      ) : (
        <ConfirmDialog
          confirmLabel={mutation.isPending ? "Reseteando…" : "Sí, resetear"}
          confirmVariant="danger"
          heading={`¿Resetear la contraseña de ${user.email}? Su sesión se cerrará al instante.`}
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
      )}
    </>
  );
}
