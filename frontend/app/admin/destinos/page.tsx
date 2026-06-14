"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { AdminShell } from "@/components/ui/admin-shell";
import { Btn } from "@/components/ui/btn";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Field } from "@/components/ui/field";
import { MonoChip } from "@/components/ui/mono-chip";
import { Notice } from "@/components/ui/notice";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";
import { Select } from "@/components/ui/select";
import { StatePill } from "@/components/ui/state-pill";

// Local response shapes mirror the backend target schemas (snake_case,
// end-to-end) — same explicit-interface idiom as the gates/users pages.
interface TargetOut {
  id: number;
  chat_id: number;
  label: string;
  enabled: boolean;
  resolved: boolean; // live: does the gateway currently have this chat resolved?
  created_at: string;
}

interface TargetListResponse {
  items: TargetOut[];
  total: number;
}

interface DiscoveredChat {
  chat_id: number;
  title: string;
}

const TARGETS_KEY = ["admin-targets"] as const;
const LABEL_MAX = 80;

// Mirror of the backend label validator: required, ≤80 chars.
function validateLabel(raw: string): string | null {
  const label = raw.trim();

  if (!label) return "Ingresá una etiqueta.";
  if (label.length > LABEL_MAX) return `Máximo ${LABEL_MAX} caracteres.`;

  return null;
}

export default function AdminDestinosPage() {
  const queryClient = useQueryClient();

  const targets = useQuery({
    queryKey: TARGETS_KEY,
    queryFn: () => api.get<TargetListResponse>("/api/admin/targets"),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: TARGETS_KEY });

  return (
    // Only the owner reaches this page (backend guard + middleware) → owner nav.
    <AdminShell gatesVisible title="Destinos">
      <div className="grid gap-6 lg:grid-cols-[340px_1fr]">
        {/* Left zone: discover + add (sticky on desktop). */}
        <div className="flex flex-col gap-5 lg:sticky lg:top-6 lg:self-start">
          <AddTargetBlock
            existing={targets.data?.items ?? []}
            onAdded={invalidate}
          />
        </div>

        {/* Right zone: the destination list. */}
        <SectionCard legend="DESTINOS" padding="none">
          {targets.isLoading && <PanelSkeleton rows={5} />}

          {targets.isError && (
            <Notice className="m-3" status="danger">
              No pudimos cargar los destinos. Recarga la página.
            </Notice>
          )}

          {targets.data &&
            (targets.data.items.length === 0 ? (
              <EmptyState message="No hay destinos. Agregá al menos uno." />
            ) : (
              <div className="overflow-x-auto">
                <table
                  aria-label="Destinos de envío"
                  className="w-full text-sm"
                >
                  <thead>
                    <tr className="border-b border-separator text-left">
                      <th className="px-3 py-2.5 font-display text-[10px] font-bold uppercase tracking-[0.1em] text-muted">
                        Etiqueta
                      </th>
                      <th className="px-3 py-2.5 font-display text-[10px] font-bold uppercase tracking-[0.1em] text-muted">
                        Chat ID
                      </th>
                      <th className="px-3 py-2.5 font-display text-[10px] font-bold uppercase tracking-[0.1em] text-muted">
                        Estado
                      </th>
                      <th className="px-3 py-2.5 font-display text-[10px] font-bold uppercase tracking-[0.1em] text-muted">
                        Acciones
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {targets.data.items.map((t) => (
                      <tr
                        key={t.id}
                        className="border-b border-separator last:border-b-0"
                      >
                        <td className="px-3 py-2.5 text-foreground">
                          {t.label}
                        </td>
                        <td className="px-3 py-2.5">
                          <MonoChip>{String(t.chat_id)}</MonoChip>
                        </td>
                        <td className="px-3 py-2.5">
                          <StatusPills target={t} />
                        </td>
                        <td className="px-3 py-2.5">
                          <div className="flex gap-2">
                            <ToggleTargetAction
                              target={t}
                              onChanged={invalidate}
                            />
                            <DeleteTargetAction
                              target={t}
                              onDeleted={invalidate}
                            />
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
        </SectionCard>
      </div>
    </AdminShell>
  );
}

// --- Status pills (enabled + live resolution) -------------------------------

function StatusPills({ target }: { target: TargetOut }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <StatePill tone={target.enabled ? "success" : "warning"}>
        {target.enabled ? "Activo" : "Pausado"}
      </StatePill>
      {target.enabled && (
        <StatePill tone={target.resolved ? "accent" : "danger"}>
          {target.resolved ? "Resuelto" : "No resuelto"}
        </StatePill>
      )}
    </div>
  );
}

// --- Discover + add ---------------------------------------------------------

function AddTargetBlock({
  existing,
  onAdded,
}: {
  existing: TargetOut[];
  onAdded: () => void;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const [label, setLabel] = useState("");
  const [labelError, setLabelError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  // On-demand: the owner clicks "Buscar chats" to hit Telegram (a network call
  // on the shared account), not on every page load.
  const discover = useQuery({
    queryKey: ["admin-targets-discover"],
    queryFn: () => api.get<DiscoveredChat[]>("/api/admin/targets/discover"),
    enabled: false,
    retry: false,
  });

  const existingIds = new Set(existing.map((t) => t.chat_id));
  const available = (discover.data ?? []).filter(
    (c) => !existingIds.has(c.chat_id),
  );

  const create = useMutation({
    mutationFn: () =>
      api.post<TargetOut>("/api/admin/targets", {
        chat_id: selected,
        label,
      }),
    onSuccess: () => {
      setSelected(null);
      setLabel("");
      setBanner(null);
      onAdded();
    },
    onError: (err) => {
      // Backend sends user-facing Spanish in `message` (duplicate /
      // unresolvable / unauthorized) — surface it verbatim in the banner.
      setBanner(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (create.isPending) return;
    setBanner(null);
    setLabelError(null);

    if (selected === null) {
      setBanner("Elegí un chat de la lista.");

      return;
    }
    const invalid = validateLabel(label);

    if (invalid) {
      setLabelError(invalid);

      return;
    }
    create.mutate();
  }

  const discoverError =
    discover.error instanceof ApiError
      ? discover.error.message
      : discover.isError
        ? "No pudimos listar los chats. Intenta de nuevo."
        : null;

  // Native select options — disabled empty-catalog hint when there's nothing
  // new to add (matches the gates page's "Sin categorías" placeholder idiom).
  const chatOptions =
    available.length === 0
      ? [
          {
            id: "__none",
            label: discover.data
              ? "No hay chats nuevos."
              : "Buscá chats primero.",
          },
        ]
      : available.map((c) => ({ id: String(c.chat_id), label: c.title }));

  return (
    <SectionCard legend="AGREGAR DESTINO" legendAs="h2">
      <div className="flex flex-col gap-3">
        {banner && <Notice status="danger">{banner}</Notice>}

        <Btn
          full
          disabled={discover.isFetching}
          icon="search"
          variant="secondary"
          onClick={() => discover.refetch()}
        >
          {discover.isFetching ? "Buscando…" : "Buscar chats"}
        </Btn>

        {discoverError && <Notice status="danger">{discoverError}</Notice>}

        <form className="flex flex-col gap-3" onSubmit={onSubmit}>
          <Select
            label="Chat"
            options={chatOptions}
            placeholder="Elegí un chat"
            value={selected === null ? null : String(selected)}
            onChange={(key) => {
              // The disabled empty-catalog hint carries no real id — ignore it.
              if (key === "__none") return;
              const id = Number(key);

              setSelected(id);
              const chat = available.find((c) => c.chat_id === id);

              if (chat) setLabel(chat.title);
              if (labelError) setLabelError(null);
            }}
          />

          <Field
            required
            error={labelError}
            label="Etiqueta"
            name="label"
            placeholder="CC1"
            value={label}
            onChange={(v) => {
              setLabel(v);
              if (labelError) setLabelError(null);
            }}
          />

          <Btn
            full
            disabled={create.isPending}
            icon="plus"
            type="submit"
            variant="primary"
          >
            {create.isPending ? "Agregando…" : "Agregar destino"}
          </Btn>
        </form>
      </div>
    </SectionCard>
  );
}

// --- Toggle enabled ---------------------------------------------------------

function ToggleTargetAction({
  target,
  onChanged,
}: {
  target: TargetOut;
  onChanged: () => void;
}) {
  const mutation = useMutation({
    mutationFn: () =>
      api.patch<TargetOut>(`/api/admin/targets/${target.id}`, {
        enabled: !target.enabled,
      }),
    onSuccess: () => onChanged(),
    onError: (err) => {
      // Deleted in another tab → the row is gone server-side; just refresh.
      if (err instanceof ApiError && err.code === "telegram_target_not_found") {
        onChanged();
      }
    },
  });

  return (
    <Btn
      disabled={mutation.isPending}
      icon={target.enabled ? "pause" : "play"}
      size="sm"
      variant="secondary"
      onClick={() => mutation.mutate()}
    >
      {target.enabled ? "Pausar" : "Activar"}
    </Btn>
  );
}

// --- Delete (confirm dialog) ------------------------------------------------

function DeleteTargetAction({
  target,
  onDeleted,
}: {
  target: TargetOut;
  onDeleted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => api.delete<void>(`/api/admin/targets/${target.id}`),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      onDeleted();
    },
    onError: (err) => {
      // Already gone in another tab → the desired outcome; just refresh.
      if (err instanceof ApiError && err.code === "telegram_target_not_found") {
        setOpen(false);
        onDeleted();

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
        icon="trash"
        size="sm"
        variant="danger"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        Eliminar
      </Btn>

      <ConfirmDialog
        confirmLabel={mutation.isPending ? "Eliminando…" : "Eliminar"}
        confirmVariant="danger"
        heading={`¿Eliminar el destino “${target.label}”?`}
        open={open}
        pending={mutation.isPending}
        onConfirm={() => mutation.mutate()}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
      >
        {error && <Notice status="danger">{error}</Notice>}
      </ConfirmDialog>
    </>
  );
}
