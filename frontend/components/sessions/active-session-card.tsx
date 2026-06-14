"use client";

// Active-session strip for the Envío cockpit: shows WHICH capture session is
// live (name + gate + "En curso"), renames it inline, and starts a fresh one on
// the same gate. It fills the gap left by the implicit session lifecycle
// (`resolve_for_batch` only ever reuses the active same-gate session): without
// "Nueva sesión" a client working one gate can never reset dedup nor produce a
// closed row that Historial would offer to "Continuar".
//
// Live state comes from the WS store (UX-DR12). Rename is a REST-confirmed local
// seed (`renameActiveSession` — the PATCH emits no WS event); "Nueva sesión"
// needs no seed — the backend's `session.active` emit rebinds the store.
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { renameActiveSession, useLiveBatch } from "@/lib/ws";
import { MonoChip } from "@/components/ui/mono-chip";
import { SectionCard } from "@/components/ui/section-card";
import { StatePill } from "@/components/ui/state-pill";
import { Field } from "@/components/ui/field";
import { Btn } from "@/components/ui/btn";
import { Notice } from "@/components/ui/notice";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";

const NAME_MAX = 200;

// Mirror of the backend RenameSessionRequest validator. Unicode "Other" +
// line/paragraph separators + any space separator except U+0020.
const NON_PRINTABLE_RE = new RegExp("[\\p{C}\\p{Zl}\\p{Zp}]|[^\\P{Zs} ]", "u");

function validateSessionName(raw: string): string | null {
  const name = raw.trim();

  if (!name) return "Ingresa un nombre.";
  if (NON_PRINTABLE_RE.test(name))
    return "El nombre no puede contener caracteres invisibles.";
  if (name.length > NAME_MAX) return `Máximo ${NAME_MAX} caracteres.`;

  return null;
}

export function ActiveSessionCard() {
  const live = useLiveBatch();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [confirmNew, setConfirmNew] = useState(false);
  const [newError, setNewError] = useState<string | null>(null);

  const sessionId = live.sessionId;
  // A live batch can't reshuffle the active session (backend 409s "Nueva
  // sesión"); rename stays allowed during a batch (legacy parity).
  const isLive = live.state !== "idle";

  const invalidateHistorial = () => {
    queryClient.invalidateQueries({ queryKey: ["sessions"] });
    queryClient.invalidateQueries({ queryKey: ["session"] });
  };

  const rename = useMutation({
    mutationFn: () =>
      api.patch<{ id: number; name: string | null }>(
        `/api/sessions/${sessionId}`,
        { name: name.trim() },
      ),
    onSuccess: (updated) => {
      setEditing(false);
      setRenameError(null);
      if (sessionId !== null) {
        renameActiveSession(sessionId, updated.name ?? name.trim());
      }
      invalidateHistorial();
    },
    onError: (err) => {
      setRenameError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  const startNew = useMutation({
    mutationFn: () => api.post<{ id: number }>("/api/sessions/new"),
    onSuccess: () => {
      setConfirmNew(false);
      setNewError(null);
      invalidateHistorial();
    },
    onError: (err) => {
      setNewError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  function saveRename() {
    if (rename.isPending) return;
    const invalid = validateSessionName(name);

    if (invalid) {
      setRenameError(invalid);

      return;
    }
    setRenameError(null);
    rename.mutate();
  }

  // No active session ⇒ no strip. Never a dead disabled card.
  if (sessionId === null) return null;

  return (
    <SectionCard legend="Sesión activa" padding="gutter" rail="accent">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {editing ? (
            <Field
              className="min-w-0 flex-1"
              error={renameError}
              value={name}
              onChange={(v) => {
                setName(v);
                if (renameError) setRenameError(null);
              }}
            />
          ) : (
            <span className="min-w-0 flex-1 truncate text-sm font-medium">
              {live.sessionName ?? "Sesión sin nombre"}
            </span>
          )}
          <StatePill dot="pulse" tone="accent">
            En curso
          </StatePill>
        </div>

        {!editing && live.sessionGateName !== null && (
          <div className="flex items-center gap-2 text-xs text-muted">
            <span className="truncate">{live.sessionGateName}</span>
            {live.sessionGateValue !== null && (
              <MonoChip>{live.sessionGateValue}</MonoChip>
            )}
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          {editing ? (
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
                  setEditing(false);
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
                  setName(live.sessionName ?? "");
                  setRenameError(null);
                  setEditing(true);
                }}
              >
                Renombrar
              </Btn>
              {/* Disabled mid-batch: the backend 409s "Nueva sesión" while a
                  batch lives — a disabled button is honest, not a failed POST. */}
              <Btn
                disabled={isLive || startNew.isPending}
                size="sm"
                variant="secondary"
                onClick={() => {
                  setNewError(null);
                  setConfirmNew(true);
                }}
              >
                Nueva sesión
              </Btn>
            </>
          )}
        </div>

        {newError && !confirmNew && <Notice status="danger">{newError}</Notice>}
      </div>

      <ConfirmDialog
        confirmLabel={startNew.isPending ? "Creando…" : "Nueva sesión"}
        heading="¿Iniciar una sesión nueva? La actual se cerrará — podrás retomarla desde Historial."
        open={confirmNew}
        pending={startNew.isPending}
        onConfirm={() => startNew.mutate()}
        onOpenChange={(open) => {
          setConfirmNew(open);
          if (!open) setNewError(null);
        }}
      >
        {newError && <Notice status="danger">{newError}</Notice>}
      </ConfirmDialog>
    </SectionCard>
  );
}
