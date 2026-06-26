"use client";

// Gate-cookie vault manager (amazon-gate-cookie-vault, Phase 1): the per-gate
// place a client stores/lists/deletes their own cookies. Rendered inside the
// cookie modal (see cookie-modal / send-form) for a cookie-mode gate.
//
// Security: the stored value is a SENSITIVE credential. This UI NEVER renders a
// raw value — the backend returns only `masked_value` (e.g. `ab••••yz`). The
// list/store endpoints are tenant-scoped; this component just paints what the
// vault returns.
import type { CookieOut } from "@/types/api";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import { useAddCookie, useDeleteCookie, useListCookies } from "@/lib/cookies";
import { Icon } from "@/components/ui/icon";

// Mirrors the backend per-(tenant, gate) cap (proposed 50). UX only — the
// backend's `cookie_limit_reached` (409) stays authoritative; this just dims the
// form and shows the count once the vault is full.
const COOKIE_CAP = 50;

function bannerStyle(kind: "ok" | "err"): React.CSSProperties {
  const tone = kind === "ok" ? "var(--success)" : "var(--danger)";

  return {
    padding: "9px 12px",
    borderRadius: 10,
    marginBottom: 12,
    fontSize: "12.5px",
    fontWeight: 600,
    background: `color-mix(in oklch, ${tone} 14%, transparent)`,
    border: `1px solid color-mix(in oklch, ${tone} 30%, transparent)`,
    color: tone,
  };
}

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
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        flex: "1 1 auto",
        minHeight: 0,
      }}
    >
      {/* header: cookie tile + title (the modal owns the close button) */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: 12,
          marginBottom: 16,
          paddingRight: 40,
          flexShrink: 0,
        }}
      >
        <div
          style={{
            width: 42,
            height: 42,
            borderRadius: 12,
            background: "var(--accent-soft)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <svg
            fill="none"
            height="22"
            stroke="var(--accent)"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.7"
            viewBox="0 0 24 24"
            width="22"
          >
            <path d="M12 2a10 10 0 1 0 10 10 4 4 0 0 1-5-5 4 4 0 0 1-5-5z" />
            <circle cx="9" cy="13.5" r=".7" />
            <circle cx="13.5" cy="16" r=".7" />
            <circle cx="15.5" cy="10.5" r=".7" />
          </svg>
        </div>
        <div style={{ flex: 1, minWidth: 0, paddingTop: 1 }}>
          <h2
            className="font-display"
            style={{
              margin: 0,
              fontSize: 17,
              fontWeight: 700,
              color: "var(--foreground)",
            }}
          >
            Cookies del gateway
          </h2>
        </div>
      </div>

      {okMsg && <div style={bannerStyle("ok")}>{okMsg}</div>}
      {banner && <div style={bannerStyle("err")}>{banner}</div>}

      <form style={{ flexShrink: 0 }} onSubmit={onSubmit}>
        {/* The stored value is a SENSITIVE credential, but the client is typing
            THEIR OWN cookie into THEIR OWN session — show it as plain text
            (owner request) so a paste is verifiable. The saved rows below stay
            masked (only `masked_value` ever crosses the wire). */}
        <span
          className="font-display"
          style={{
            display: "block",
            marginBottom: 7,
            fontSize: "10.5px",
            letterSpacing: ".1em",
            textTransform: "uppercase",
            color: "var(--faint)",
          }}
        >
          Cookie
        </span>

        {/* paste row: input + Pegar side by side */}
        <div style={{ display: "flex", gap: 8, alignItems: "stretch" }}>
          {/* Visible plain text (owner request), but the value is still a
              credential: autoComplete off + spellCheck off keep the browser's
              form history / autofill and remote spellcheck services from
              retaining or shipping it. */}
          <input
            autoComplete="off"
            className="font-mono rx-focus"
            name="cookie-value"
            placeholder="Pega la cookie"
            spellCheck={false}
            style={{
              flex: 1,
              minWidth: 0,
              height: 44,
              padding: "0 13px",
              borderRadius: "var(--radius-field)",
              background: "var(--field-background)",
              border: `1px solid ${
                valueError ? "var(--danger)" : "var(--field-border)"
              }`,
              color: "var(--foreground)",
              fontSize: "12.5px",
            }}
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              if (valueError) setValueError(null);
            }}
            onPaste={onPaste}
          />
          <button
            className="font-display rx-focus"
            style={{
              flexShrink: 0,
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              height: 44,
              padding: "0 15px",
              borderRadius: "var(--radius-field)",
              background: "var(--surface-secondary)",
              border: "1px solid var(--border-strong)",
              color: "var(--foreground)",
              fontSize: "12.5px",
              fontWeight: 600,
              cursor: "pointer",
            }}
            type="button"
            onClick={pasteCookie}
          >
            <Icon name="copy" size={14} />
            Pegar
          </button>
        </div>

        {valueError && (
          <span
            role="alert"
            style={{
              display: "block",
              marginTop: 6,
              fontSize: "12px",
              color: "var(--danger)",
            }}
          >
            {valueError}
          </span>
        )}

        <button
          className="font-display"
          disabled={add.isPending || atCap}
          style={{
            marginTop: 12,
            width: "100%",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            height: 46,
            borderRadius: 12,
            background: "var(--brand-gradient)",
            color: "#fff",
            fontSize: 14,
            fontWeight: 700,
            border: "none",
            cursor: add.isPending || atCap ? "not-allowed" : "pointer",
            opacity: add.isPending || atCap ? 0.55 : 1,
            boxShadow: "0 6px 22px oklch(64% 0.21 295 / 0.32)",
          }}
          type="submit"
        >
          {add.isPending ? "Guardando…" : "Guardar cookie"}
        </button>

        {atCap && (
          <div style={{ ...bannerStyle("err"), marginTop: 10, marginBottom: 0 }}>
            Alcanzaste el máximo de {COOKIE_CAP} cookies para este gateway.
            Elimina una para agregar otra.
          </div>
        )}
      </form>

      {/* counter */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginTop: 18,
          paddingBottom: 10,
          borderBottom: "1px solid var(--separator)",
          flexShrink: 0,
        }}
      >
        <span
          className="font-display"
          style={{
            fontSize: "10.5px",
            letterSpacing: ".12em",
            textTransform: "uppercase",
            color: "var(--faint)",
          }}
        >
          Guardadas
        </span>
        <span
          className="font-mono"
          style={{ fontSize: 12, color: "var(--muted)" }}
        >
          {count} / {COOKIE_CAP}
        </span>
      </div>

      {/* saved list */}
      <div
        className="rx-scroll"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 7,
          marginTop: 12,
          overflowY: "auto",
          flex: "1 1 auto",
          minHeight: 0,
        }}
      >
        {list.isLoading &&
          [0, 1, 2].map((i) => (
            <div
              key={i}
              style={{
                height: 38,
                borderRadius: 10,
                background: "var(--surface-secondary)",
                border: "1px solid var(--border)",
                opacity: 0.5,
              }}
            />
          ))}

        {list.isError && (
          <div style={bannerStyle("err")}>
            No pudimos cargar las cookies. Recarga la página.
          </div>
        )}

        {!list.isLoading && !list.isError && cookies.length === 0 && (
          <div
            style={{
              padding: "28px 12px",
              textAlign: "center",
              fontSize: "12.5px",
              color: "var(--muted)",
            }}
          >
            Todavía no guardaste cookies para este gateway.
          </div>
        )}

        {cookies.map((c) => (
          <CookieRow
            key={c.id}
            cookie={c}
            gateId={gateId}
            onDeleting={() => setOkMsg(null)}
          />
        ))}
      </div>
    </div>
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
    <div style={{ flexShrink: 0 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "9px 11px",
          borderRadius: 10,
          background: "var(--surface-secondary)",
          border: "1px solid var(--border)",
        }}
      >
        {/* Only ever the masked value — the raw credential never reaches here. */}
        <span
          className="font-mono"
          style={{
            flex: 1,
            minWidth: 0,
            fontSize: 12,
            color: "var(--foreground)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {cookie.masked_value}
        </span>
        <button
          aria-label="Eliminar cookie"
          className="rx-focus"
          disabled={remove.isPending}
          style={{
            flexShrink: 0,
            width: 26,
            height: 26,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            borderRadius: 7,
            background: "none",
            border: "none",
            color: "var(--faint)",
            cursor: remove.isPending ? "not-allowed" : "pointer",
            opacity: remove.isPending ? 0.5 : 1,
          }}
          type="button"
          onClick={onDelete}
        >
          <Icon name="trash" size={14} />
        </button>
      </div>
      {error && (
        <span
          role="alert"
          style={{
            display: "block",
            marginTop: 4,
            paddingLeft: 11,
            fontSize: "12px",
            color: "var(--danger)",
          }}
        >
          {error}
        </span>
      )}
    </div>
  );
}
