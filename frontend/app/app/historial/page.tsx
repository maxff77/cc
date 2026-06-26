"use client";

// Historial (PR-2) — the client's deferred history of APPROVED (✅) captured
// responses, grouped by the batch's gate snapshot. Fully independent of the
// cockpit "Limpiar" cutoff (that is a non-destructive view cut; this reads the
// persisted `responses` rows directly). Read-only list + three DESTRUCTIVE
// deletes (one response / one gate / all), each confirmed and invalidating the
// query on success.
//
// 🔒 The page only ever sees `name` (gate_name) + `display_value`
// (client-visible) — never `gate_value` (owner-only). The null group surfaces
// as "Sin gate" and has no per-gate delete (there is no gate name to target).
//
// Layout follows the cockpit master column (frontend/app/app/page.tsx): on lg
// the shell pins the viewport (main is `overflow-y-hidden`), so this flowing
// page owns its OWN scroll container (`lg:h-full lg:overflow-y-auto rx-scroll`);
// below lg it flows with the page under the mobile bottom nav.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { Icon } from "@/components/ui/icon";
import { Notice } from "@/components/ui/notice";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";

// --- Inline response types (the contract; types/api.ts is hand-curated and
// must NOT be regenerated for this). `name` is the gate_name (null = "Sin
// gate"); `display_value` is the client-visible label. `gate_value` is NEVER
// part of this shape. ---
interface HistoryItem {
  id: number;
  text: string;
  captured_at: string;
  cc: string[];
}

interface HistoryGate {
  name: string | null;
  display_value: string;
  count: number;
  items: HistoryItem[];
}

interface HistoryResponse {
  gates: HistoryGate[];
}

const HISTORY_KEY = ["history"] as const;

function formatTime(iso: string): string {
  const date = new Date(iso);

  return [date.getHours(), date.getMinutes(), date.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

// One approved-✅ response — the DataRow visual idiom (console density, muted
// timestamp at left, wrapping text) extended with extracted-cc chips and a
// trailing trash button. A single response deletes on click with a light
// confirm (quick — it's the client's own data).
function HistoryRow({
  item,
  onDelete,
  pending,
}: {
  item: HistoryItem;
  onDelete: () => void;
  pending: boolean;
}) {
  return (
    <div
      className="flex items-start gap-[11px] border-b border-separator px-4 py-[11px] last:border-b-0"
    >
      <span
        className="shrink-0 pt-0.5 font-mono text-[10.5px] tabular-nums"
        style={{ color: "var(--faint)" }}
      >
        {formatTime(item.captured_at)}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-[7px]">
        <span className="break-words font-mono text-[11.5px] leading-[1.5] text-foreground [overflow-wrap:anywhere]">
          {item.text}
        </span>
        {item.cc.length > 0 && (
          <div className="flex flex-wrap gap-[5px]">
            {item.cc.map((cc, i) => (
              <span
                key={`${cc}-${i}`}
                className="rounded-md px-2 py-0.5 font-mono text-[11px] text-success"
                style={{
                  background:
                    "color-mix(in oklch, var(--success) 15%, transparent)",
                }}
              >
                {cc}
              </span>
            ))}
          </div>
        )}
      </div>
      <button
        aria-label="Eliminar esta respuesta"
        className="rx-focus flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-[7px] transition-colors hover:text-danger disabled:opacity-40"
        disabled={pending}
        style={{ color: "var(--faint)" }}
        type="button"
        onClick={onDelete}
      >
        <Icon name="trash" size={14} />
      </button>
    </div>
  );
}

export default function HistorialPage() {
  const queryClient = useQueryClient();
  const history = useQuery({
    queryKey: HISTORY_KEY,
    queryFn: () => api.get<HistoryResponse>("/api/history"),
  });

  // Surfaced error from any delete (shared strip at the top of the list).
  const [actionError, setActionError] = useState<string | null>(null);
  // Pending row id (a single-response delete fires on click — disable just it).
  const [pendingId, setPendingId] = useState<number | null>(null);
  // Confirm state for the two heavier deletes: a gate (by name) or all.
  const [confirm, setConfirm] = useState<
    { kind: "gate"; name: string; label: string } | { kind: "all" } | null
  >(null);

  function refresh() {
    queryClient.invalidateQueries({ queryKey: HISTORY_KEY });
  }

  function toMessage(err: unknown): string {
    return err instanceof ApiError
      ? err.message
      : "No pudimos conectar. Intenta de nuevo.";
  }

  const deleteResponse = useMutation({
    mutationFn: (id: number) =>
      api.delete<{ deleted: number }>(`/api/history/response/${id}`),
    onMutate: (id) => {
      setActionError(null);
      setPendingId(id);
    },
    onError: (err) => setActionError(toMessage(err)),
    onSuccess: () => refresh(),
    onSettled: () => setPendingId(null),
  });

  const deleteGate = useMutation({
    mutationFn: (name: string) =>
      api.delete<{ deleted: number }>(
        `/api/history/gate?name=${encodeURIComponent(name)}`,
      ),
    onMutate: () => setActionError(null),
    onError: (err) => setActionError(toMessage(err)),
    onSuccess: () => {
      refresh();
      setConfirm(null);
    },
  });

  const deleteAll = useMutation({
    mutationFn: () => api.delete<{ deleted: number }>("/api/history"),
    onMutate: () => setActionError(null),
    onError: (err) => setActionError(toMessage(err)),
    onSuccess: () => {
      refresh();
      setConfirm(null);
    },
  });

  const confirmPending =
    confirm?.kind === "gate" ? deleteGate.isPending : deleteAll.isPending;

  const gates = history.data?.gates ?? [];
  const hasHistory = gates.length > 0;

  return (
    <div className="rx-scroll mx-auto flex w-full max-w-[880px] flex-col gap-[18px] lg:h-full lg:min-h-0 lg:overflow-y-auto lg:pr-1">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-[3px]">
          <h1 className="font-display text-[22px] font-bold text-foreground">
            Historial
          </h1>
          <span className="text-[13px] text-muted">
            Tus respuestas aprobadas, agrupadas por gateway.
          </span>
        </div>
        {hasHistory && (
          <button
            className="rx-focus inline-flex h-9 shrink-0 items-center gap-[7px] rounded-[9px] px-[14px] font-display text-[13px] font-semibold text-danger"
            style={{
              background:
                "color-mix(in oklch, var(--danger) 14%, transparent)",
              border:
                "1px solid color-mix(in oklch, var(--danger) 38%, transparent)",
            }}
            type="button"
            onClick={() => {
              setActionError(null);
              setConfirm({ kind: "all" });
            }}
          >
            <Icon name="trash" size={15} />
            Borrar todo
          </button>
        )}
      </div>

      {actionError && <Notice status="danger">{actionError}</Notice>}

      {history.isLoading && (
        <div
          className="overflow-hidden rounded-[var(--radius)] bg-surface"
          style={{ border: "1px solid var(--border)" }}
        >
          <PanelSkeleton rows={4} />
        </div>
      )}

      {history.isError && (
        <Notice status="danger">
          No pudimos cargar el historial. Recarga la página.
        </Notice>
      )}

      {history.data && !hasHistory && (
        <div
          className="flex flex-col items-center gap-2 rounded-[var(--radius)] bg-surface px-5 py-[54px]"
          style={{ border: "1px solid var(--border)" }}
        >
          <svg
            fill="none"
            height="28"
            stroke="var(--faint)"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="1.5"
            viewBox="0 0 24 24"
            width="28"
          >
            <path d="M3 3v5h5M3.05 13A9 9 0 106 5.3L3 8" />
            <path d="M12 7v5l3 2" />
          </svg>
          <span className="text-[13.5px] text-muted">Aún no hay historial.</span>
        </div>
      )}

      {gates.map((gate) => (
        <div
          key={gate.name ?? "__null__"}
          className="overflow-hidden rounded-[var(--radius)] bg-surface"
          style={{ border: "1px solid var(--border)" }}
        >
          {/* Gate header — NAME (display) + optional "Comando visible"
              (display_value) sub-pill, with a success count pill on the right.
              "Sin gate" (name null) is self-describing, so it carries no
              sub-pill and no per-gate delete. */}
          <div
            className="flex items-center justify-between gap-[10px] px-4 py-[13px]"
            style={{ borderBottom: "1px solid var(--separator)" }}
          >
            <div className="flex min-w-0 items-center gap-[10px]">
              <span className="truncate whitespace-nowrap font-display text-[14.5px] font-bold text-foreground">
                {gate.name ?? gate.display_value}
              </span>
              {gate.name !== null && (
                <span className="shrink-0 rounded-[7px] bg-surface-tertiary px-2 py-0.5 font-mono text-[11px] text-muted">
                  {gate.display_value}
                </span>
              )}
            </div>
            <span
              className="inline-flex h-6 shrink-0 items-center gap-[5px] rounded-full px-[10px] font-mono text-[12px] font-semibold text-success"
              style={{
                background:
                  "color-mix(in oklch, var(--success) 16%, transparent)",
              }}
            >
              {gate.count}
            </span>
          </div>
          <div className="rx-scroll max-h-[360px] overflow-y-auto">
            {gate.items.map((item) => (
              <HistoryRow
                key={item.id}
                item={item}
                pending={pendingId === item.id}
                onDelete={() => deleteResponse.mutate(item.id)}
              />
            ))}
          </div>
          {/* "Sin gate" (name === null) has no gate name to target, so it
              carries no per-gate delete — only per-response + Borrar todo. */}
          {gate.name !== null && (
            <div
              className="flex justify-end px-4 py-[9px]"
              style={{ borderTop: "1px solid var(--border)" }}
            >
              <button
                className="rx-focus inline-flex items-center gap-1.5 font-mono text-[11.5px] text-danger transition-opacity hover:opacity-80"
                type="button"
                onClick={() => {
                  setActionError(null);
                  setConfirm({
                    kind: "gate",
                    name: gate.name as string,
                    label: gate.name ?? gate.display_value,
                  });
                }}
              >
                <Icon name="trash" size={13} />
                Borrar historial de este gateway
              </button>
            </div>
          )}
        </div>
      ))}

      {/* Shared confirm for the two heavier deletes (gate + all). The
          per-response trash deletes on click (light, no modal). */}
      <ConfirmDialog
        confirmLabel={confirmPending ? "Borrando…" : "Borrar"}
        confirmVariant="danger"
        heading={
          confirm?.kind === "gate"
            ? `¿Borrar todo el historial del gateway "${confirm.label}"? Esta acción no se puede deshacer.`
            : "¿Borrar TODO tu historial de respuestas aprobadas? Esta acción no se puede deshacer."
        }
        open={confirm !== null}
        pending={confirmPending}
        onConfirm={() => {
          if (confirm?.kind === "gate") deleteGate.mutate(confirm.name);
          else if (confirm?.kind === "all") deleteAll.mutate();
        }}
        onOpenChange={(open) => {
          if (!open) {
            setConfirm(null);
            setActionError(null);
          }
        }}
      >
        {actionError && <Notice status="danger">{actionError}</Notice>}
      </ConfirmDialog>
    </div>
  );
}
