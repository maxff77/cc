"use client";

// Gift-key catalog (gift-keys feature, admin + owner). Mirrors the plans page
// idiom: a sticky generate form on the left, the keys log on the right. Admins
// only pick DAYS — the tier is the owner-designated default plan (set in
// /admin/plans), so admins can't mint a premium tier. The log shows who minted
// and who claimed each key (the owner's admin-abuse audit view); unclaimed keys
// are revocable.
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

// Local response shapes mirror the backend gift-key schemas (snake_case,
// end-to-end) — the per-page interface idiom shared with the plans/users pages.
interface GiftKeyOut {
  id: number;
  code: string;
  days: number;
  credits: number;
  plan_id: number;
  plan_name: string;
  status: string; // 'active' | 'claimed' | 'revoked'
  created_by_email: string | null;
  claimed_by_email: string | null;
  created_at: string;
  claimed_at: string | null;
}

interface GiftKeyListResponse {
  items: GiftKeyOut[];
}

interface Me {
  role: string;
}

const KEYS_KEY = ["admin-keys"] as const;
const ME_KEY = ["me"] as const;
const KEY_DAYS_MAX = 36500;
const CREDITS_MAX = 2_147_483_647; // int4 ceiling, mirrors the backend bound

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("es", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// What a key grants — days, credits, or both (gift-key-credits feature).
function grantLabel(key: Pick<GiftKeyOut, "days" | "credits">): string {
  const parts: string[] = [];

  if (key.days > 0) parts.push(`${key.days} d`);
  if (key.credits > 0) parts.push(`${key.credits} cr`);

  return parts.join(" · ") || "0 d";
}

const STATUS_TONE: Record<string, "success" | "muted" | "danger"> = {
  active: "success",
  claimed: "muted",
  revoked: "danger",
};

const STATUS_LABEL: Record<string, string> = {
  active: "Activa",
  claimed: "Canjeada",
  revoked: "Revocada",
};

export default function AdminKeysPage() {
  const queryClient = useQueryClient();

  const me = useQuery({
    queryKey: ME_KEY,
    queryFn: () => api.get<Me>("/api/auth/me"),
  });
  const isOwner = me.data?.role === "owner";

  const keys = useQuery({
    queryKey: KEYS_KEY,
    queryFn: () => api.get<GiftKeyListResponse>("/api/admin/keys"),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: KEYS_KEY });

  const items = keys.data?.items ?? [];
  // Declutter: default to only ACTIVE keys; the toggle reveals claimed +
  // (any not-yet-purged) revoked. Pure client-side filter of the existing list
  // — the daily backend purge eventually deletes expired-unclaimed + revoked.
  const [showAll, setShowAll] = useState(false);
  const visible = showAll
    ? items
    : items.filter((key) => key.status === "active");
  const hiddenCount = items.length - visible.length;

  return (
    // Admin + owner reach this page; owner tabs show only for the owner.
    <AdminShell gatesVisible={isOwner} title="Keys">
      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        {/* Left zone: generate form (sticky on desktop). */}
        <div className="flex flex-col gap-5 lg:sticky lg:top-6 lg:self-start">
          <GenerateKeyForm onCreated={invalidate} />
        </div>

        {/* Right zone: the keys log. */}
        <SectionCard legend="KEYS GENERADAS" padding="none">
          {keys.isLoading && <PanelSkeleton rows={5} />}
          {keys.isError && (
            <Notice className="m-3" status="danger">
              No pudimos cargar las keys. Recarga la página.
            </Notice>
          )}
          {keys.data &&
            (items.length === 0 ? (
              <EmptyState
                eyebrow="Keys"
                message="Todavía no hay keys. Genera la primera."
              />
            ) : (
              <>
                <div className="flex items-center justify-between gap-3 border-b border-separator px-3.5 py-2">
                  <span className="text-[11px] text-muted tabular-nums">
                    {showAll
                      ? `${items.length} en total`
                      : hiddenCount > 0
                        ? `${visible.length} activas · ${hiddenCount} ocultas`
                        : `${visible.length} activas`}
                  </span>
                  {(showAll || hiddenCount > 0) && (
                    <Btn
                      size="sm"
                      variant="secondary"
                      onClick={() => setShowAll((v) => !v)}
                    >
                      {showAll ? "Solo activas" : "Mostrar todas"}
                    </Btn>
                  )}
                </div>
                {visible.length === 0 ? (
                  <EmptyState
                    eyebrow="Keys"
                    message="No hay keys activas. Activa “Mostrar todas” para ver canjeadas y revocadas."
                  />
                ) : (
                  <ul className="m-0 list-none p-0">
                    {visible.map((key, i) => (
                  <li
                    key={key.id}
                    className={clsx(
                      "flex flex-wrap items-center gap-3 px-3.5 py-3",
                      i && "border-t border-separator",
                    )}
                  >
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-mono text-sm font-semibold">
                          {key.code}
                        </span>
                        <StatePill tone={STATUS_TONE[key.status] ?? "muted"}>
                          {STATUS_LABEL[key.status] ?? key.status}
                        </StatePill>
                      </div>
                      <span className="font-mono text-[11px] text-muted tabular-nums">
                        {grantLabel(key)} · {key.plan_name} · creó{" "}
                        {key.created_by_email ?? "—"}
                      </span>
                      <span className="font-mono text-[11px] text-[var(--faint)] tabular-nums">
                        {formatDate(key.created_at)}
                        {key.claimed_by_email
                          ? ` · canjeó ${key.claimed_by_email}`
                          : ""}
                      </span>
                    </div>
                    {key.status === "active" && (
                      <RevokeKeyAction keyRow={key} onRevoked={invalidate} />
                    )}
                  </li>
                    ))}
                  </ul>
                )}
              </>
            ))}
        </SectionCard>
      </div>
    </AdminShell>
  );
}

// --- Generate -----------------------------------------------------------------

function GenerateKeyForm({ onCreated }: { onCreated: () => void }) {
  const [days, setDays] = useState("");
  const [credits, setCredits] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [created, setCreated] = useState<GiftKeyOut | null>(null);
  const [copied, setCopied] = useState(false);

  // Empty inputs mean 0 — a key may grant days, credits, or both.
  const daysNum = days.trim() === "" ? 0 : Number(days);
  const creditsNum = credits.trim() === "" ? 0 : Number(credits);

  const mutation = useMutation({
    mutationFn: () =>
      api.post<GiftKeyOut>("/api/admin/keys", {
        days: daysNum,
        credits: creditsNum,
      }),
    onSuccess: (key) => {
      setCreated(key);
      setCopied(false);
      setDays("");
      setCredits("");
      onCreated();
    },
    onError: (err) => {
      // no_default_plan: the owner hasn't flagged a basic tier yet — point
      // there. Everything else (incl. invalid_key_days) → the banner.
      if (err instanceof ApiError && err.code === "no_default_plan") {
        setBanner(
          `${err.message} Marca un plan como “keys” en Planes (solo el owner).`,
        );
      } else if (err instanceof ApiError) {
        setBanner(err.message);
      } else {
        setBanner("No pudimos conectar. Intenta de nuevo.");
      }
    },
  });

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (mutation.isPending) return;
    setBanner(null);
    setFieldError(null);
    // Mirror the backend bounds: days 0..KEY_DAYS_MAX, credits 0..CREDITS_MAX,
    // and a key must grant at least one (days OR credits > 0).
    if (days.trim() !== "" && !/^\d+$/.test(days.trim())) {
      setFieldError("Días: entero ≥ 0.");

      return;
    }
    if (credits.trim() !== "" && !/^\d+$/.test(credits.trim())) {
      setFieldError("Créditos: entero ≥ 0.");

      return;
    }
    if (daysNum > KEY_DAYS_MAX || creditsNum > CREDITS_MAX) {
      setFieldError("Valor demasiado alto.");

      return;
    }
    if (daysNum === 0 && creditsNum === 0) {
      setFieldError("Indica días o créditos (al menos uno).");

      return;
    }
    mutation.mutate();
  }

  async function copyCode() {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.code);
      setCopied(true);
    } catch {
      // Clipboard blocked (insecure context / permissions) — the code is shown
      // for manual copy regardless.
      setCopied(false);
    }
  }

  return (
    <SectionCard legend="GENERAR KEY" legendAs="h2">
      <div className="flex flex-col gap-3">
        {banner && <Notice status="danger">{banner}</Notice>}

        {created && (
          <Notice status="success">
            <div className="flex flex-col gap-2">
              <span>
                Key generada · {grantLabel(created)} · plan {created.plan_name}
              </span>
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded-[var(--radius-sm)] bg-surface-tertiary px-2 py-1 font-mono text-sm">
                  {created.code}
                </code>
                <Btn size="sm" variant="secondary" onClick={copyCode}>
                  {copied ? "Copiado" : "Copiar"}
                </Btn>
              </div>
            </div>
          </Notice>
        )}

        <form className="flex flex-col gap-3" onSubmit={onSubmit}>
          <Field
            error={fieldError}
            label="Días"
            name="days"
            placeholder="30"
            type="number"
            value={days}
            onChange={(v) => {
              setDays(v);
              if (fieldError) setFieldError(null);
            }}
          />
          <Field
            label="Créditos"
            name="credits"
            placeholder="0"
            type="number"
            value={credits}
            onChange={(v) => {
              setCredits(v);
              if (fieldError) setFieldError(null);
            }}
          />
          <p className="text-[11px] text-muted">
            El plan (tier) lo fija el owner en Planes. Indica días, créditos o
            ambos (al menos uno). Días 0 con créditos = key solo de créditos.
          </p>
          <Btn
            full
            disabled={mutation.isPending}
            icon="plus"
            type="submit"
            variant="primary"
          >
            {mutation.isPending ? "Generando…" : "Generar key"}
          </Btn>
        </form>
      </div>
    </SectionCard>
  );
}

// --- Revoke (per-row dialog) --------------------------------------------------

function RevokeKeyAction({
  keyRow,
  onRevoked,
}: {
  keyRow: GiftKeyOut;
  onRevoked: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.post<void>(`/api/admin/keys/${keyRow.id}/revoke`),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      onRevoked();
    },
    onError: (err) => {
      // Already claimed/gone in another tab → refresh to reflect reality.
      if (
        err instanceof ApiError &&
        (err.code === "key_already_claimed" || err.code === "key_not_found")
      ) {
        setError(err.message);
        onRevoked();

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
        size="sm"
        variant="danger"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        Revocar
      </Btn>

      <ConfirmDialog
        confirmLabel={mutation.isPending ? "Revocando…" : "Revocar"}
        confirmVariant="danger"
        heading="¿Revocar esta key?"
        open={open}
        pending={mutation.isPending}
        onConfirm={() => mutation.mutate()}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
      >
        <div className="flex flex-col gap-2">
          {error && <Notice status="danger">{error}</Notice>}
          <p className="text-sm text-muted">
            La key <code className="font-mono">{keyRow.code}</code> dejará de
            poder canjearse. Esto no afecta a quien ya la haya canjeado.
          </p>
        </div>
      </ConfirmDialog>
    </>
  );
}
