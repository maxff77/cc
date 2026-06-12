"use client";

// Historial (Story 3.3): the tenant's sessions grouped by gate (newest first
// inside each group; groups ordered by their most recent session), with
// inline rename and a confirm-modal delete. The API delivers a FLAT
// newest-first list — the grouping is pure presentation (recorded decision).
// Continuar (Story 3.4) reopens a closed session as the active capture
// session — dedup is server-side, the WS `session.active` rebinds Envío.
// Export `↓ .txt` is Story 3.5 — no dead buttons.
import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  AlertDialog,
  Button,
  Input,
  Spinner,
  TextField,
} from "@heroui/react";
import clsx from "clsx";

import { api, ApiError } from "@/lib/api";
import { clearSession } from "@/lib/ws";

// Local response shapes mirror the backend session schemas (snake_case,
// end-to-end) — same explicit-interface idiom as admin/gates (the 2-1
// "generated types in one epic-wide pass" deferral still stands).
interface SessionOut {
  id: number;
  name: string | null;
  gate_value: string;
  gate_name: string;
  is_active: boolean;
  created_at: string;
}

interface SessionListResponse {
  items: SessionOut[];
  total: number;
}

const SESSIONS_KEY = ["sessions"] as const;
const NAME_MAX = 200;

// Mirror of Python's `not ch.isprintable()` (backend RenameSessionRequest):
// Unicode "Other" (control, format, surrogate, private-use, unassigned) plus
// line/paragraph separators and any space separator except U+0020 (e.g. NBSP).
// RegExp constructor, not a literal: tsconfig targets es5 and rejects the
// `u` flag in literals; every runtime Next serves supports property escapes.
const NON_PRINTABLE_RE = new RegExp("[\\p{C}\\p{Zl}\\p{Zp}]|[^\\P{Zs} ]", "u");

// Mirror of the backend RenameSessionRequest validator: trimmed, non-empty,
// no control/invisible chars, ≤200 — the AC 4 cap (CaptureSession.name
// String(200)). All three rules, so a pasted tab/zero-width char gets an
// actionable inline error instead of a generic 422 round-trip.
function validateSessionName(raw: string): string | null {
  const name = raw.trim();

  if (!name) return "Ingresa un nombre.";
  if (NON_PRINTABLE_RE.test(name))
    return "El nombre no puede contener caracteres invisibles.";
  if (name.length > NAME_MAX) return `Máximo ${NAME_MAX} caracteres.`;

  return null;
}

// Deterministic created_at fallback for unnamed sessions (mirror of legacy
// `nombre_bonito`): local "YYYY-MM-DD HH:MM", padStart idiom, no locale.
// (Duplicated in sessions/[id]/page.tsx — App Router pages cannot export
// helpers.)
function fallbackName(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");

  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    ` ${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

// Right badge (UX-DR11): "En curso" accent-tint / "Cerrada" muted. Derives
// from `is_active` (recorded 3.1 decision) — at most ONE "En curso" per
// tenant (partial unique index); the active session stays "En curso" between
// batches (capture stays armed — legacy parity).
function SessionBadge({ isActive }: { isActive: boolean }) {
  return (
    <span
      className={clsx(
        "shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.12em]",
        isActive
          ? "bg-accent/22 text-accent"
          : "bg-surface-tertiary text-muted",
      )}
    >
      {isActive ? "En curso" : "Cerrada"}
    </span>
  );
}

interface GateGroup {
  gateValue: string;
  gateName: string;
  sessions: SessionOut[];
}

// Group the flat newest-first list by gate_value, preserving first-appearance
// order ⇒ groups sort by their most recent session.
function groupByGate(items: SessionOut[]): GateGroup[] {
  const groups: GateGroup[] = [];
  const indexByGate = new Map<string, number>();

  for (const session of items) {
    const at = indexByGate.get(session.gate_value);

    if (at === undefined) {
      indexByGate.set(session.gate_value, groups.length);
      groups.push({
        gateValue: session.gate_value,
        gateName: session.gate_name,
        sessions: [session],
      });
    } else {
      groups[at].sessions.push(session);
    }
  }

  return groups;
}

export default function SessionsPage() {
  const sessions = useQuery({
    queryKey: SESSIONS_KEY,
    queryFn: () => api.get<SessionListResponse>("/api/sessions"),
  });

  if (sessions.isLoading) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  if (sessions.isError || !sessions.data) {
    return (
      <Alert status="danger">
        No pudimos cargar el historial. Recarga la página.
      </Alert>
    );
  }

  // Empty state (AC 7) — copy verbatim, never a dead-end (UX-DR16).
  if (sessions.data.items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
        <p className="text-muted">
          Todavía no tienes sesiones. Tu primer lote crea una.
        </p>
        <Link className="text-accent underline" href="/">
          Ir a Envío
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {groupByGate(sessions.data.items).map((group) => (
        <section key={group.gateValue} className="flex flex-col gap-2">
          <header className="flex items-baseline gap-2">
            <span className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted">
              {group.gateName}
            </span>
            <span className="font-mono text-[11px] text-muted">
              {group.gateValue}
            </span>
          </header>
          <ul className="flex flex-col divide-y divide-separator rounded-md border border-border bg-surface">
            {group.sessions.map((session) => (
              <SessionRow key={session.id} session={session} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

// One session row (UX-DR11): Link (heading + mono sub-line) → badge →
// actions. The actions live OUTSIDE the Link — tapping Renombrar/Eliminar
// never navigates. Content editing does not exist anywhere (AC 5 / FR19).
function SessionRow({ session }: { session: SessionOut }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [continueError, setContinueError] = useState<string | null>(null);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: SESSIONS_KEY });
    // The detail page caches under ["session", "<id>"] — a rename/delete
    // here must reach it too ("shows immediately").
    queryClient.invalidateQueries({
      queryKey: ["session", String(session.id)],
    });
  };

  const rename = useMutation({
    mutationFn: () =>
      api.patch<SessionOut>(`/api/sessions/${session.id}`, { name }),
    onSuccess: () => {
      setEditing(false);
      setRenameError(null);
      invalidate();
    },
    onError: (err) => {
      // Deleted in another tab: the row no longer exists — refresh the list
      // so the ghost row (and this editor) goes away (DeleteGateAction idiom).
      if (err instanceof ApiError && err.code === "session_not_found") {
        invalidate();

        return;
      }
      setRenameError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  // Continuar (Story 3.4, AC 1): reactivate as the active capture session.
  // NO local seed — the `session.active` event arrives on this tab's own
  // socket and the WS store is who rebinds Envío (recorded decision).
  // (Mutation duplicated in sessions/[id]/page.tsx — App Router pages cannot
  // export helpers; same accepted precedent as fallbackName/SessionBadge.)
  const continuar = useMutation({
    mutationFn: () =>
      api.post<SessionOut>(`/api/sessions/${session.id}/continue`),
    onSuccess: () => {
      setContinueError(null);
      invalidate();
      // The session that WAS active changed badge too — its cached detail
      // would go stale; the prefix invalidation covers both details.
      queryClient.invalidateQueries({ queryKey: ["session"] });
    },
    onError: (err) => {
      // Deleted in another tab: refresh so the ghost row goes away.
      if (err instanceof ApiError && err.code === "session_not_found") {
        setContinueError(null);
        invalidate();

        return;
      }
      // batch_live carries the AC 3 copy verbatim — rendered as-is.
      setContinueError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  const remove = useMutation({
    mutationFn: () => api.delete<void>(`/api/sessions/${session.id}`),
    onSuccess: () => {
      setConfirmOpen(false);
      setDeleteError(null);
      invalidate();
      // Same-tab honesty: if Envío's panels held this session, clear them —
      // the rows no longer exist server-side (REST-confirmed local seed).
      clearSession(session.id);
    },
    onError: (err) => {
      // Already deleted in another tab → the desired outcome.
      if (err instanceof ApiError && err.code === "session_not_found") {
        setConfirmOpen(false);
        setDeleteError(null);
        invalidate();
        clearSession(session.id);

        return;
      }
      // session_in_use carries the AC 6 copy verbatim — shown INSIDE the
      // modal without closing it (the operator decides: stop the lote or
      // cancel).
      setDeleteError(
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

  return (
    <li className="flex flex-col gap-2 px-3 py-2">
      <div className="flex items-center gap-3">
        {editing ? (
          <TextField
            className="min-w-0 flex-1"
            name="session-name"
            value={name}
            onChange={(v) => {
              setName(v);
              if (renameError) setRenameError(null);
            }}
          >
            <Input aria-label="Nombre de la sesión" maxLength={NAME_MAX} />
          </TextField>
        ) : (
          <Link className="min-w-0 flex-1" href={`/sessions/${session.id}`}>
            <span className="block truncate text-sm font-medium">
              {session.name ?? fallbackName(session.created_at)}
            </span>
            <span className="block truncate font-mono text-[11px] text-muted">
              {session.gate_value} · {session.id}
            </span>
          </Link>
        )}

        <SessionBadge isActive={session.is_active} />

        <div className="flex shrink-0 gap-2">
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
              {/* Only on "Cerrada" rows (AC 1: "a closed session") — the
                  active session already captures; no no-op button. NOT
                  destructive ⇒ secondary, no confirm (UX-DR triad). */}
              {!session.is_active && (
                <Button
                  isDisabled={continuar.isPending}
                  size="sm"
                  variant="secondary"
                  onPress={() => continuar.mutate()}
                >
                  {continuar.isPending ? "Continuando…" : "Continuar"}
                </Button>
              )}
              <Button
                size="sm"
                variant="secondary"
                onPress={() => {
                  setName(session.name ?? "");
                  setRenameError(null);
                  setEditing(true);
                }}
              >
                Renombrar
              </Button>
              <Button
                size="sm"
                variant="danger"
                onPress={() => {
                  setDeleteError(null);
                  setConfirmOpen(true);
                }}
              >
                Eliminar
              </Button>
            </>
          )}
        </div>
      </div>

      {renameError && (
        <span className="text-sm text-danger">{renameError}</span>
      )}

      {continueError && (
        <span className="text-sm text-danger">{continueError}</span>
      )}

      {/* Confirm modal (AC 5) — a REAL modal per the AC (unlike the inline
          confirm of admin/gates), max ONE level (UX-DR10). */}
      <AlertDialog
        isOpen={confirmOpen}
        onOpenChange={(open) => {
          setConfirmOpen(open);
          if (!open) setDeleteError(null);
        }}
      >
        <AlertDialog.Backdrop>
          <AlertDialog.Container>
            <AlertDialog.Dialog>
              <AlertDialog.Header>
                <AlertDialog.Heading>
                  ¿Eliminar esta sesión? No se puede deshacer.
                </AlertDialog.Heading>
              </AlertDialog.Header>
              {deleteError && (
                <AlertDialog.Body>
                  <span className="text-sm text-danger">{deleteError}</span>
                </AlertDialog.Body>
              )}
              <AlertDialog.Footer>
                <Button
                  isDisabled={remove.isPending}
                  size="sm"
                  variant="secondary"
                  onPress={() => {
                    setConfirmOpen(false);
                    setDeleteError(null);
                  }}
                >
                  Cancelar
                </Button>
                <Button
                  isDisabled={remove.isPending}
                  size="sm"
                  variant="danger"
                  onPress={() => remove.mutate()}
                >
                  {remove.isPending ? "Eliminando…" : "Eliminar"}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      </AlertDialog>
    </li>
  );
}
