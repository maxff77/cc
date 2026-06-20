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
import { Btn } from "@/components/ui/btn";
import { Icon } from "@/components/ui/icon";
import { Notice } from "@/components/ui/notice";
import { SectionCard } from "@/components/ui/section-card";
import { CountBadge } from "@/components/ui/count-badge";
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
    <div className="flex items-start gap-2 border-b border-separator px-3 py-1.5 font-mono text-[11px] leading-[1.4] last:border-b-0">
      <span className="shrink-0 text-muted tabular-nums">
        {formatTime(item.captured_at)}
      </span>
      <div className="min-w-0 flex-1">
        <span className="block break-words">{item.text}</span>
        {item.cc.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {item.cc.map((cc, i) => (
              <span
                key={`${cc}-${i}`}
                className="rounded bg-success/15 px-1.5 py-0.5 text-[10px] leading-none text-success"
              >
                {cc}
              </span>
            ))}
          </div>
        )}
      </div>
      <span aria-hidden className="shrink-0 text-success">
        ✅
      </span>
      <button
        aria-label="Eliminar esta respuesta"
        className="rx-focus shrink-0 text-muted transition-colors hover:text-danger disabled:opacity-40"
        disabled={pending}
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
    <div className="flex flex-col gap-4 lg:h-full lg:min-h-0 lg:overflow-y-auto rx-scroll lg:pr-1">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-display text-lg font-bold text-foreground">
            Historial
          </h1>
          <p className="mt-0.5 text-sm text-muted">
            Tus respuestas aprobadas (✅), agrupadas por gate.
          </p>
        </div>
        {hasHistory && (
          <Btn
            size="sm"
            variant="danger"
            onClick={() => {
              setActionError(null);
              setConfirm({ kind: "all" });
            }}
          >
            <Icon name="trash" size={15} />
            Borrar todo
          </Btn>
        )}
      </div>

      {actionError && <Notice status="danger">{actionError}</Notice>}

      {history.isLoading && (
        <SectionCard padding="none">
          <PanelSkeleton rows={4} />
        </SectionCard>
      )}

      {history.isError && (
        <Notice status="danger">
          No pudimos cargar el historial. Recarga la página.
        </Notice>
      )}

      {history.data && !hasHistory && (
        <SectionCard>
          <p className="px-1 py-6 text-center text-sm text-muted">
            Aún no hay historial.
          </p>
        </SectionCard>
      )}

      {gates.map((gate) => (
        <SectionCard
          key={gate.name ?? "__null__"}
          legend={gate.name ?? gate.display_value}
          legendAs="h2"
          legendRight={<CountBadge tone="success" value={gate.count} />}
          padding="none"
        >
          {/* Gate NAME is the engraved legend; the "Comando visible"
              (display_value) rides below as a muted subtitle. "Sin gate"
              (name null) is self-describing, so it carries no subtitle. */}
          {gate.name !== null && (
            <div className="border-b border-separator px-3 py-1.5 font-mono text-[11px] text-muted">
              {gate.display_value}
            </div>
          )}
          <div className="rx-scroll max-h-[60vh] overflow-y-auto">
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
            <div className="flex justify-end border-t border-border px-3 py-2">
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
                Borrar historial de este gate
              </button>
            </div>
          )}
        </SectionCard>
      ))}

      {/* Shared confirm for the two heavier deletes (gate + all). The
          per-response trash deletes on click (light, no modal). */}
      <ConfirmDialog
        confirmLabel={confirmPending ? "Borrando…" : "Borrar"}
        confirmVariant="danger"
        heading={
          confirm?.kind === "gate"
            ? `¿Borrar todo el historial del gate "${confirm.label}"? Esta acción no se puede deshacer.`
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
