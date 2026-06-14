"use client";

// Historial (Story 3.3): the tenant's sessions grouped by gate (newest first
// inside each group; groups ordered by their most recent session), with
// inline rename and a confirm-modal delete. The API delivers a FLAT
// newest-first list — the grouping is pure presentation (recorded decision).
// Continuar (Story 3.4) reopens a closed session as the active capture
// session — dedup is server-side, the WS `session.active` rebinds Envío.
// Export `↓ .txt` (Story 3.5) lives in the dual views (Envío + detail), NOT
// here: the spine's row actions are Renombrar/Continuar/Eliminar only.
import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";

import { api, ApiError } from "@/lib/api";
import { clearSession } from "@/lib/ws";
import { Btn } from "@/components/ui/btn";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Field } from "@/components/ui/field";
import { MonoChip } from "@/components/ui/mono-chip";
import { Notice } from "@/components/ui/notice";
import { PageHeader } from "@/components/ui/page-header";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";
import { StatePill } from "@/components/ui/state-pill";

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

  let content: React.ReactNode;

  if (sessions.isLoading) {
    content = <PanelSkeleton rows={6} />;
  } else if (sessions.isError || !sessions.data) {
    content = (
      <Notice status="danger">
        No pudimos cargar el historial. Recarga la página.
      </Notice>
    );
  } else if (sessions.data.items.length === 0) {
    // Empty state (AC 7) — copy verbatim, never a dead-end (UX-DR16). A REAL
    // link, not a Button+router.push: navigation keeps anchor semantics
    // (middle-click, copy-link, announced as link) and matches the detail
    // page's NotFound escape action.
    content = (
      <EmptyState
        action={
          <Link className="text-accent underline" href="/">
            Ir a Envío
          </Link>
        }
        eyebrow="Historial"
        message="Todavía no tienes sesiones. Tu primer lote crea una."
      />
    );
  } else {
    // Group per gate → SectionCard: the engraved legend IS the group header,
    // jerarquically above its rows (ui-polish-spec §3.7).
    content = groupByGate(sessions.data.items).map((group) => (
      <SectionCard
        key={group.gateValue}
        legend={group.gateName}
        legendRight={<MonoChip>{group.gateValue}</MonoChip>}
        padding="none"
      >
        <ul className="flex flex-col">
          {group.sessions.map((session, i) => (
            <SessionRow key={session.id} separated={i > 0} session={session} />
          ))}
        </ul>
      </SectionCard>
    ));
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <PageHeader title="Historial" />
      {content}
    </div>
  );
}

// One session row (UX-DR11): Link (heading + mono sub-line) → badge →
// actions. The actions live OUTSIDE the Link — tapping Renombrar/Eliminar
// never navigates. Content editing does not exist anywhere (AC 5 / FR19).
function SessionRow({
  session,
  separated,
}: {
  session: SessionOut;
  separated: boolean;
}) {
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
    // flex-wrap: under sm the actions wrap to their own line instead of
    // crushing the title (ui-polish-spec §3.7). First-row has no top border;
    // every following row carries the separator (handoff HistorialScreen).
    <li
      className={clsx(
        "flex flex-wrap items-center gap-x-3 gap-y-2 px-3.5 py-3",
        separated && "border-t border-separator",
      )}
    >
      {editing ? (
        <Field
          className="min-w-0 flex-1"
          error={renameError}
          name="session-name"
          value={name}
          onChange={(v) => {
            setName(v);
            if (renameError) setRenameError(null);
          }}
        />
      ) : (
        <Link className="min-w-0 flex-1" href={`/sessions/${session.id}`}>
          <span className="block truncate text-sm font-medium">
            {session.name ?? fallbackName(session.created_at)}
          </span>
          {/* The gate is the group legend and the internal id is debug data —
              the sub-line is the creation date, and only when a custom name
              isn't already showing it. */}
          {session.name !== null && (
            <span className="mt-0.5 block truncate font-mono text-[11px] text-muted">
              {fallbackName(session.created_at)}
            </span>
          )}
        </Link>
      )}

      <StatePill
        dot={session.is_active ? "pulse" : undefined}
        tone={session.is_active ? "accent" : "muted"}
      >
        {session.is_active ? "En curso" : "Cerrada"}
      </StatePill>

      <div className="flex shrink-0 gap-2">
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
            {/* Only on "Cerrada" rows (AC 1: "a closed session") — the
                  active session already captures; no no-op button. NOT
                  destructive ⇒ secondary, no confirm (UX-DR triad). */}
            {!session.is_active && (
              <Btn
                disabled={continuar.isPending}
                icon="play"
                size="sm"
                variant="secondary"
                onClick={() => continuar.mutate()}
              >
                {continuar.isPending ? "Continuando…" : "Continuar"}
              </Btn>
            )}
            <Btn
              size="sm"
              variant="secondary"
              onClick={() => {
                setName(session.name ?? "");
                setRenameError(null);
                setEditing(true);
              }}
            >
              Renombrar
            </Btn>
            <Btn
              icon="trash"
              size="sm"
              variant="danger"
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

      {continueError && (
        <Notice className="w-full" status="danger">
          {continueError}
        </Notice>
      )}

      {/* Confirm modal (AC 5) — a REAL modal per the AC (unlike the inline
          confirm of admin/gates), max ONE level (UX-DR10). session_in_use
          (AC 6) shows INSIDE the dialog without closing it. */}
      <ConfirmDialog
        confirmLabel={remove.isPending ? "Eliminando…" : "Eliminar"}
        confirmVariant="danger"
        heading="¿Eliminar esta sesión? No se puede deshacer."
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
