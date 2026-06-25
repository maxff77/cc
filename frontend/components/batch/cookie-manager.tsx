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
import { Icon } from "@/components/ui/icon";
import { LabelCaps } from "@/components/ui/label-caps";
import { MonoChip } from "@/components/ui/mono-chip";
import { Notice } from "@/components/ui/notice";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";

// Mirrors the backend per-(tenant, gate) cap (proposed 50). UX only — the
// backend's `cookie_limit_reached` (409) stays authoritative; this just dims the
// form and shows the count once the vault is full.
const COOKIE_CAP = 50;

export function CookieManager({
  gateId,
  onSaved,
}: {
  gateId: number;
  // Called after a successful store (201 fresh or 200 idempotent). The host uses
  // it to close the modal / resume a stalled send (cookie-paste-autosave-resume).
  onSaved?: () => void;
}) {
  const list = useListCookies(gateId);
  const add = useAddCookie(gateId);

  const [value, setValue] = useState("");
  const [valueError, setValueError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  const cookies = list.data?.items ?? [];
  const count = list.data?.total ?? cookies.length;
  const atCap = count >= COOKIE_CAP;

  // "Pegar" shortcut — fill the field from the clipboard (mirrors send-form's
  // líneas paste). Best-effort: an absent/denied Clipboard API is a no-op, the
  // native paste still works.
  async function pasteCookie() {
    try {
      const clip = await navigator.clipboard?.readText();

      if (clip) {
        setValue(clip);
        saveValue(clip, true);
      }
    } catch {
      /* clipboard unavailable / denied — manual paste still works */
    }
  }

  // `fromPaste` marks the single-cookie paste flow (native paste or "Pegar"):
  // only that path fires `onSaved` (close the modal / resume a stalled send). A
  // typed value + "Guardar cookie" (fromPaste=false) saves but leaves the modal
  // open, so a manual multi-cookie add still works (frozen Intent).
  function saveValue(raw: string, fromPaste = false) {
    // Enter / paste can re-fire while a POST is in flight (cockpit lesson).
    if (add.isPending) return;
    setValueError(null);
    setBanner(null);
    setOkMsg(null);

    // Canonicalize like the backend (value.strip()) so every save path stores
    // the same value and the empty-guard matches what the server validates.
    const value = raw.trim();

    if (!value) {
      setValueError("Pega el valor de la cookie.");

      return;
    }

    add.mutate(
      { value },
      {
        onSuccess: () => {
          // Clear the field only if it still holds what we just saved — a second
          // paste landing while this POST was in flight must not be wiped.
          setValue((cur) => (cur === raw ? "" : cur));
          // Confirm the store (also covers the idempotent re-POST, which the
          // backend dedups to the same row).
          setOkMsg("Cookie guardada correctamente.");
          // Single-cookie paste flow only: let the host close the modal / resume
          // a stalled send. Fires on 200 (idempotent) and 201 (fresh) alike.
          if (fromPaste) onSaved?.();
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

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    saveValue(value);
  }

  // A paste IS the action — clients paste one cookie at a time, so save it
  // straight away instead of making them also click "Guardar". Typing never
  // triggers this; an empty clipboard falls through to the native paste.
  function onPaste(e: React.ClipboardEvent<HTMLInputElement>) {
    const pasted = e.clipboardData.getData("text").trim();

    if (!pasted) return;
    e.preventDefault();
    setValue(pasted);
    saveValue(pasted, true);
  }

  return (
    <SectionCard
      className="flex flex-col gap-3.5"
      legend="Cookies del gateway"
      legendAs="h2"
    >
      {okMsg && <Notice status="success">{okMsg}</Notice>}
      {banner && <Notice status="danger">{banner}</Notice>}

      <form className="flex flex-col gap-3" onSubmit={onSubmit}>
        {/* The stored value is a SENSITIVE credential, but the client is typing
            THEIR OWN cookie into THEIR OWN session — show it as plain text
            (owner request) so a paste is verifiable. The saved rows below stay
            masked (only `masked_value` ever crosses the wire). */}
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <LabelCaps>Cookie</LabelCaps>
            <button
              className="rx-focus inline-flex items-center gap-1.5 text-[11.5px] font-semibold text-accent transition-colors hover:text-foreground"
              type="button"
              onClick={pasteCookie}
            >
              <Icon name="copy" size={13} />
              Pegar
            </button>
          </div>
          {/* Visible plain text (owner request), but the value is still a
              credential: autoComplete off + spellCheck off keep the browser's
              form history / autofill and remote spellcheck services from
              retaining or shipping it — the protections type="password" gave
              implicitly. */}
          <Field
            autoComplete="off"
            error={valueError}
            name="cookie-value"
            placeholder="Pega la cookie"
            spellCheck={false}
            value={value}
            onChange={(v) => {
              setValue(v);
              if (valueError) setValueError(null);
            }}
            onPaste={onPaste}
          />
        </div>

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
            Alcanzaste el máximo de {COOKIE_CAP} cookies para este gateway. Elimina
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
          <EmptyState message="Todavía no guardaste cookies para este gateway." />
        )}

        {cookies.length > 0 && (
          <ul className="m-0 flex list-none flex-col divide-y divide-separator p-0">
            {cookies.map((c) => (
              <CookieRow
                key={c.id}
                cookie={c}
                gateId={gateId}
                onDeleting={() => setOkMsg(null)}
              />
            ))}
          </ul>
        )}
      </div>
    </SectionCard>
  );
}

function CookieRow({
  cookie,
  gateId,
  onDeleting,
}: {
  cookie: CookieOut;
  gateId: number;
  onDeleting?: () => void;
}) {
  const remove = useDeleteCookie(gateId);
  const [error, setError] = useState<string | null>(null);

  function onDelete() {
    if (remove.isPending) return;
    // Drop a stale "guardada correctamente" — deleting contradicts it.
    onDeleting?.();
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
