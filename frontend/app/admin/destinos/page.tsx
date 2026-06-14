"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Alert,
  AlertDialog,
  Button,
  FieldError,
  Form,
  Input,
  Label,
  ListBox,
  Select,
  Table,
  TextField,
} from "@heroui/react";

import { api, ApiError } from "@/lib/api";
import { AdminShell } from "@/components/ui/admin-shell";
import { EmptyState } from "@/components/ui/empty-state";
import { MonoChip } from "@/components/ui/mono-chip";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";

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

const PILL = "rounded px-2 py-0.5 text-[11px] font-medium";

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
            <Alert className="m-3" status="danger">
              No pudimos cargar los destinos. Recarga la página.
            </Alert>
          )}

          {targets.data && (
            <Table>
              <Table.Content aria-label="Destinos de envío">
                <Table.Header>
                  <Table.Column isRowHeader>Etiqueta</Table.Column>
                  <Table.Column>Chat ID</Table.Column>
                  <Table.Column>Estado</Table.Column>
                  <Table.Column>Acciones</Table.Column>
                </Table.Header>
                <Table.Body
                  items={targets.data.items}
                  renderEmptyState={() => (
                    <EmptyState message="No hay destinos. Agregá al menos uno." />
                  )}
                >
                  {(t) => (
                    <Table.Row id={t.id}>
                      <Table.Cell>{t.label}</Table.Cell>
                      <Table.Cell>
                        <MonoChip>{String(t.chat_id)}</MonoChip>
                      </Table.Cell>
                      <Table.Cell>
                        <StatusPills target={t} />
                      </Table.Cell>
                      <Table.Cell>
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
                      </Table.Cell>
                    </Table.Row>
                  )}
                </Table.Body>
              </Table.Content>
            </Table>
          )}
        </SectionCard>
      </div>
    </AdminShell>
  );
}

// --- Status pills (enabled + live resolution) -------------------------------

function StatusPills({ target }: { target: TargetOut }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <span
        className={clsx(
          PILL,
          target.enabled
            ? "bg-success/15 text-success"
            : "bg-warning/18 text-warning",
        )}
      >
        {target.enabled ? "Activo" : "Pausado"}
      </span>
      {target.enabled && (
        <span
          className={clsx(
            PILL,
            target.resolved
              ? "bg-accent/15 text-accent"
              : "bg-danger/15 text-danger",
          )}
        >
          {target.resolved ? "Resuelto" : "No resuelto"}
        </span>
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

  return (
    <SectionCard legend="AGREGAR DESTINO" legendAs="h2">
      <div className="flex flex-col gap-3">
        {banner && <Alert status="danger">{banner}</Alert>}

        <Button
          className="w-full"
          isDisabled={discover.isFetching}
          variant="secondary"
          onPress={() => discover.refetch()}
        >
          {discover.isFetching ? "Buscando…" : "Buscar chats"}
        </Button>

        {discoverError && <Alert status="danger">{discoverError}</Alert>}

        <Form className="flex flex-col gap-3" onSubmit={onSubmit}>
          <Select
            className="w-full"
            placeholder="Elegí un chat"
            selectedKey={selected === null ? null : String(selected)}
            onSelectionChange={(key) => {
              const id = key == null ? null : Number(key);

              setSelected(id);
              const chat = available.find((c) => c.chat_id === id);

              if (chat) setLabel(chat.title);
              if (labelError) setLabelError(null);
            }}
          >
            <Label>Chat</Label>
            <Select.Trigger>
              <Select.Value />
              <Select.Indicator />
            </Select.Trigger>
            <Select.Popover>
              <ListBox>
                {available.length === 0 ? (
                  <ListBox.Item isDisabled id="__none" textValue="Sin chats">
                    {discover.data
                      ? "No hay chats nuevos."
                      : "Buscá chats primero."}
                  </ListBox.Item>
                ) : (
                  available.map((c) => (
                    <ListBox.Item
                      key={c.chat_id}
                      id={String(c.chat_id)}
                      textValue={c.title}
                    >
                      {c.title}
                    </ListBox.Item>
                  ))
                )}
              </ListBox>
            </Select.Popover>
          </Select>

          <TextField
            isRequired
            className="flex w-full flex-col gap-1"
            isInvalid={labelError !== null}
            name="label"
            value={label}
            onChange={(v) => {
              setLabel(v);
              if (labelError) setLabelError(null);
            }}
          >
            <Label>Etiqueta</Label>
            <Input placeholder="CC1" />
            {labelError && <FieldError>{labelError}</FieldError>}
          </TextField>

          <Button
            className="w-full"
            isDisabled={create.isPending}
            type="submit"
            variant="primary"
          >
            {create.isPending ? "Agregando…" : "Agregar destino"}
          </Button>
        </Form>
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
    <Button
      isDisabled={mutation.isPending}
      size="sm"
      variant="secondary"
      onPress={() => mutation.mutate()}
    >
      {target.enabled ? "Pausar" : "Activar"}
    </Button>
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
      <Button
        size="sm"
        variant="secondary"
        onPress={() => {
          setError(null);
          setOpen(true);
        }}
      >
        Eliminar
      </Button>

      <AlertDialog
        isOpen={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) setError(null);
        }}
      >
        <AlertDialog.Backdrop>
          <AlertDialog.Container>
            <AlertDialog.Dialog>
              <AlertDialog.Header>
                <AlertDialog.Heading>
                  ¿Eliminar el destino “{target.label}”?
                </AlertDialog.Heading>
              </AlertDialog.Header>
              {error && (
                <AlertDialog.Body>
                  <Alert status="danger">{error}</Alert>
                </AlertDialog.Body>
              )}
              <AlertDialog.Footer>
                <Button
                  isDisabled={mutation.isPending}
                  size="sm"
                  variant="secondary"
                  onPress={() => {
                    setOpen(false);
                    setError(null);
                  }}
                >
                  Cancelar
                </Button>
                <Button
                  isDisabled={mutation.isPending}
                  size="sm"
                  variant="danger"
                  onPress={() => mutation.mutate()}
                >
                  {mutation.isPending ? "Eliminando…" : "Eliminar"}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      </AlertDialog>
    </>
  );
}
