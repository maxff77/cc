"use client";

// Gate-cookie vault manager (amazon-gate-cookie-vault, Phase 1): the per-gate
// place a client stores/lists/deletes their own cookies. Rendered by the
// cockpit ONLY for a cookie-mode gate while the surface is idle (see send-form).
//
// Security: the stored value is a SENSITIVE credential. This UI NEVER renders a
// raw value — the backend returns only `masked_value` (e.g. `ab••••yz`), and
// the add input is `type="password"` so the typed secret is shielded too. The
// list/store endpoints are tenant-scoped; this component just paints what the
// vault returns.
import type { CookieOut } from "@/types/api";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import { useAddCookie, useDeleteCookie, useListCookies } from "@/lib/cookies";
import { Btn } from "@/components/ui/btn";
import { EmptyState } from "@/components/ui/empty-state";
import { Field } from "@/components/ui/field";
import { LabelCaps } from "@/components/ui/label-caps";
import { MonoChip } from "@/components/ui/mono-chip";
import { Notice } from "@/components/ui/notice";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";

// Mirrors the backend per-(tenant, gate) cap (proposed 50). UX only — the
// backend's `cookie_limit_reached` (409) stays authoritative; this just dims the
// form and shows the count once the vault is full.
const COOKIE_CAP = 50;

export function CookieManager({ gateId }: { gateId: number }) {
  const list = useListCookies(gateId);
  const add = useAddCookie(gateId);

  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [valueError, setValueError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const cookies = list.data?.items ?? [];
  const count = list.data?.total ?? cookies.length;
  const atCap = count >= COOKIE_CAP;

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    // Enter can re-submit while a POST is in flight (cockpit lesson).
    if (add.isPending) return;
    setValueError(null);
    setBanner(null);

    // Local guard mirrors the backend `invalid_cookie` (empty/whitespace-only)
    // — the backend stays authoritative and re-validates the canonical value.
    if (!value.trim()) {
      setValueError("Pega el valor de la cookie.");

      return;
    }

    add.mutate(
      { value, label: label.trim() || null },
      {
        onSuccess: () => {
          // Clear the secret from the field the moment it lands; the masked row
          // is the only thing that comes back.
          setValue("");
          setLabel("");
        },
        onError: (err) => {
          if (err instanceof ApiError) {
            // invalid_cookie (empty/oversized/unprintable) and
            // cookie_limit_reached anchor to the value field; gate_not_cookie_mode
            // (the gate flipped off in another tab) and anything else go to the
            // banner — all carry the backend's Spanish copy.
            if (
              err.code === "invalid_cookie" ||
              err.code === "cookie_limit_reached"
            ) {
              setValueError(err.message);
            } else {
              setBanner(err.message);
            }
          } else {
            setBanner("No pudimos conectar. Intenta de nuevo.");
          }
        },
      },
    );
  }

  return (
    <SectionCard
      className="flex flex-col gap-3.5"
      legend="Cookies del gate"
      legendAs="h2"
    >
      {banner && <Notice status="danger">{banner}</Notice>}

      <form className="flex flex-col gap-3" onSubmit={onSubmit}>
        {/* type="password" shields the typed secret; the stored value is never
            echoed back (only masked rows below). */}
        <Field
          error={valueError}
          label="Cookie"
          name="cookie-value"
          placeholder="Pega la cookie"
          type="password"
          value={value}
          onChange={(v) => {
            setValue(v);
            if (valueError) setValueError(null);
          }}
        />

        <Field
          label="Etiqueta (opcional)"
          name="cookie-label"
          placeholder="p. ej. cuenta principal"
          value={label}
          onChange={setLabel}
        />

        <Btn
          full
          disabled={add.isPending || atCap}
          type="submit"
          variant="primary"
        >
          {add.isPending ? "Guardando…" : "Guardar cookie"}
        </Btn>

        {atCap && (
          <Notice status="warning">
            Alcanzaste el máximo de {COOKIE_CAP} cookies para este gate. Elimina
            una para agregar otra.
          </Notice>
        )}
      </form>

      <div className="flex items-center justify-between">
        <LabelCaps>Guardadas</LabelCaps>
        <span className="text-[11px] text-muted tabular-nums">
          {count} / {COOKIE_CAP}
        </span>
      </div>

      <div>
        {list.isLoading && <PanelSkeleton rows={3} />}

        {list.isError && (
          <Notice status="danger">
            No pudimos cargar las cookies. Recarga la página.
          </Notice>
        )}

        {!list.isLoading && !list.isError && cookies.length === 0 && (
          <EmptyState message="Todavía no guardaste cookies para este gate." />
        )}

        {cookies.length > 0 && (
          <ul className="m-0 flex list-none flex-col divide-y divide-separator p-0">
            {cookies.map((c) => (
              <CookieRow key={c.id} cookie={c} gateId={gateId} />
            ))}
          </ul>
        )}
      </div>
    </SectionCard>
  );
}

function CookieRow({ cookie, gateId }: { cookie: CookieOut; gateId: number }) {
  const remove = useDeleteCookie(gateId);
  const [error, setError] = useState<string | null>(null);

  function onDelete() {
    if (remove.isPending) return;
    setError(null);
    remove.mutate(cookie.id, {
      onError: (err) => {
        // Deleted in another tab → the desired outcome; the list invalidation on
        // settle removes the ghost row regardless. Surface any other failure.
        if (err instanceof ApiError && err.code === "cookie_not_found") return;
        setError(
          err instanceof ApiError
            ? err.message
            : "No pudimos conectar. Intenta de nuevo.",
        );
      },
    });
  }

  return (
    <li className="flex flex-wrap items-center gap-3 py-2.5">
      <div className="flex min-w-0 flex-[1_1_9rem] flex-col gap-1">
        {cookie.label && (
          <span className="truncate text-sm font-semibold">{cookie.label}</span>
        )}
        {/* Only ever the masked value — the raw credential never reaches here. */}
        <MonoChip className="self-start text-foreground">
          {cookie.masked_value}
        </MonoChip>
        {error && (
          <span className="text-[12px] text-danger" role="alert">
            {error}
          </span>
        )}
      </div>

      <Btn
        disabled={remove.isPending}
        icon="trash"
        size="sm"
        variant="danger"
        onClick={onDelete}
      >
        {remove.isPending ? "Eliminando…" : "Eliminar"}
      </Btn>
    </li>
  );
}
