"use client";

// Active-session strip for the Envío cockpit: shows WHICH capture session is
// live (name + gate + "En curso"), renames it inline, and starts a fresh one
// on the same gate. It fills the gap left by the implicit session lifecycle
// (`resolve_for_batch` only ever reuses the active same-gate session): without
// "Nueva sesión" a client working one gate can never reset dedup nor produce a
// closed row that Historial would offer to "Continuar".
//
// Live state comes from the WS store (UX-DR12). Rename is a REST-confirmed
// local seed (`renameActiveSession` — the PATCH emits no WS event); "Nueva
// sesión" needs no seed — the backend's `session.active` emit rebinds the
// store (panels clear) exactly like Continuar.
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  AlertDialog,
  Button,
  FieldError,
  Input,
  TextField,
} from "@heroui/react";

import { api, ApiError } from "@/lib/api";
import { renameActiveSession, useLiveBatch } from "@/lib/ws";
import { MonoChip } from "@/components/ui/mono-chip";
import { SectionCard } from "@/components/ui/section-card";
import { StatePill } from "@/components/ui/state-pill";

const NAME_MAX = 200;

// Mirror of the backend RenameSessionRequest validator (duplicated from
// sessions/page.tsx — App Router pages can't export helpers; accepted
// precedent). Unicode "Other" + line/paragraph separators + any space
// separator except U+0020.
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
  // sesión"); rename stays allowed during a batch (backend is unguarded —
  // legacy parity, same as Historial).
  const isLive = live.state !== "idle";

  // Historial caches the list + each detail — a rename/new here must reach
  // them so they don't show stale names/badges.
  const invalidateHistorial = () => {
    queryClient.invalidateQueries({ queryKey: ["sessions"] });
    queryClient.invalidateQueries({ queryKey: ["session"] });
  };

  const rename = useMutation({
    mutationFn: () =>
      api.patch<{ id: number; name: string | null }>(
        `/api/sessions/${sessionId}`,
        // Send the trimmed value that was validated (the backend trims too,
        // but the wire value should match what `validateSessionName` checked).
        { name: name.trim() },
      ),
    onSuccess: (updated) => {
      setEditing(false);
      setRenameError(null);
      // REST-confirmed local seed: the PATCH has no WS event, so update this
      // tab's strip now (the backend trimmed/validated `updated.name`).
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
      // The store rebinds via the server's `session.active` emit (panels
      // clear); just refresh Historial so the now-closed session appears with
      // its "Continuar" button and the fresh one shows "En curso".
      invalidateHistorial();
    },
    onError: (err) => {
      // batch_live / session_conflict / session_not_found carry Spanish copy.
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

  // No active session ⇒ no strip (the empty-state copy "tu primer lote crea
  // una" lives in the form/Historial). Never a dead disabled card.
  if (sessionId === null) return null;

  return (
    <SectionCard legend="Sesión activa" padding="gutter" rail="accent">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {editing ? (
            <TextField
              className="min-w-0 flex-1"
              isInvalid={renameError !== null}
              name="active-session-name"
              value={name}
              onChange={(v) => {
                setName(v);
                if (renameError) setRenameError(null);
              }}
            >
              <Input aria-label="Nombre de la sesión" maxLength={NAME_MAX} />
              {renameError && <FieldError>{renameError}</FieldError>}
            </TextField>
          ) : (
            <span className="min-w-0 flex-1 truncate text-sm font-medium">
              {live.sessionName ?? "Sesión sin nombre"}
            </span>
          )}
          <StatePill tone="accent">En curso</StatePill>
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
                  setEditing(false);
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
                  setName(live.sessionName ?? "");
                  setRenameError(null);
                  setEditing(true);
                }}
              >
                Renombrar
              </Button>
              {/* Disabled mid-batch: the backend 409s "Nueva sesión" while a
                  batch lives — a disabled button is honest, not a failed POST. */}
              <Button
                isDisabled={isLive || startNew.isPending}
                size="sm"
                variant="secondary"
                onPress={() => {
                  setNewError(null);
                  setConfirmNew(true);
                }}
              >
                Nueva sesión
              </Button>
            </>
          )}
        </div>

        {newError && <Alert status="danger">{newError}</Alert>}
      </div>

      <AlertDialog
        isOpen={confirmNew}
        onOpenChange={(open) => {
          setConfirmNew(open);
          if (!open) setNewError(null);
        }}
      >
        <AlertDialog.Backdrop>
          <AlertDialog.Container>
            <AlertDialog.Dialog>
              <AlertDialog.Header>
                <AlertDialog.Heading>
                  ¿Iniciar una sesión nueva? La actual se cerrará — podrás
                  retomarla desde Historial.
                </AlertDialog.Heading>
              </AlertDialog.Header>
              {newError && (
                <AlertDialog.Body>
                  <Alert status="danger">{newError}</Alert>
                </AlertDialog.Body>
              )}
              <AlertDialog.Footer>
                <Button
                  isDisabled={startNew.isPending}
                  size="sm"
                  variant="secondary"
                  onPress={() => {
                    setConfirmNew(false);
                    setNewError(null);
                  }}
                >
                  Cancelar
                </Button>
                <Button
                  isDisabled={startNew.isPending}
                  size="sm"
                  variant="primary"
                  onPress={() => startNew.mutate()}
                >
                  {startNew.isPending ? "Creando…" : "Nueva sesión"}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      </AlertDialog>
    </SectionCard>
  );
}
