"use client";

// Paste-first send form (Story 2.2): textarea + TWO-STEP gate selector
// (category → gate, AC 13) + Enviar. Never free text for the gate (UX-DR9).
// During a live lote the selects lock and a chip shows the active gate; the
// textarea + Enviar stay usable — submitting APPENDS (AC 10).
import type { LiveBatchState } from "@/lib/ws";

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  FieldError,
  Form,
  Label,
  ListBox,
  Select,
  TextArea,
  TextField,
} from "@heroui/react";

import { api, ApiError } from "@/lib/api";
import { seedFromBatch } from "@/lib/ws";
import { LabelCaps } from "@/components/ui/label-caps";
import { MonoChip } from "@/components/ui/mono-chip";
import { SectionCard } from "@/components/ui/section-card";

// Mirrors backend GateOut (snake_case end-to-end, users/gates-page idiom).
export interface GateOut {
  id: number;
  value: string;
  name: string;
  category_id: number;
  category_name: string;
  created_at: string;
}

interface BatchOut {
  id: number;
  gate_name: string;
  gate_value: string;
  state: string;
  sent: number;
  queued: number;
  failed: number;
  total: number;
  appended: boolean;
  added: number;
  // FIFO admission position (Story 4.2) — null unless state === "waiting".
  queue_position: number | null;
}

const GATES_KEY = ["gates"] as const;

export function SendForm({
  gates,
  live,
}: {
  gates: GateOut[];
  live: LiveBatchState;
}) {
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [categoryKey, setCategoryKey] = useState<string | null>(null);
  const [gateKey, setGateKey] = useState<string | null>(null);
  const [textError, setTextError] = useState<string | null>(null);
  const [selectError, setSelectError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  // Any live state (sending/paused/stopping) locks the selector and shows
  // the gate chip — appending to a PAUSED lote is allowed (Story 2.3).
  const isLive = live.state !== "idle";

  // Client category source: NO extra endpoint — group the catalog items by
  // category_name (only categories with active gates matter to clients).
  const categories = useMemo(
    () =>
      Array.from(new Set(gates.map((g) => g.category_name))).sort((a, b) =>
        a.localeCompare(b, "es"),
      ),
    [gates],
  );

  const gatesInCategory = useMemo(
    () => gates.filter((g) => g.category_name === categoryKey),
    [gates, categoryKey],
  );

  // On append the backend validates the id but applies the LIVE gate's value
  // regardless — submit the live gate's id (any valid id works as fallback).
  const liveGateId = useMemo(() => {
    if (!isLive) return null;
    const match = gates.find((g) => g.value === live.gateValue);

    return (match ?? gates[0])?.id ?? null;
  }, [isLive, gates, live.gateValue]);

  const mutation = useMutation({
    mutationFn: (payload: { text: string; gate_id: number }) =>
      api.post<BatchOut>("/api/batches", payload),
    onSuccess: (data) => {
      setText("");
      if (!data.appended) {
        // Flip into live mode immediately; snapshot/batch.state stay the
        // source of truth from here on (UX-DR12).
        seedFromBatch(data);
      }
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.code === "empty_batch") {
          setTextError(err.message);
        } else if (err.code === "gate_not_found") {
          // Retired in another tab — refresh the catalog and re-pick.
          queryClient.invalidateQueries({ queryKey: GATES_KEY });
          setCategoryKey(null);
          setGateKey(null);
          setSelectError(err.message);
        } else {
          // telegram_unauthorized (503) and anything else → banner with the
          // server's Spanish message.
          setBanner(err.message);
        }
      } else {
        setBanner("No pudimos conectar. Intenta de nuevo.");
      }
    },
  });

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    // Enter can re-submit while a POST is in flight (2.1 review lesson).
    if (mutation.isPending) return;
    setTextError(null);
    setSelectError(null);
    setBanner(null);

    // Defense in both layers (AC 4): whitespace-only never fires the request.
    if (!text.trim()) {
      setTextError("No hay líneas para enviar.");

      return;
    }

    const gateId = isLive ? liveGateId : gateKey ? Number(gateKey) : null;

    if (gateId == null) {
      setSelectError(
        isLive
          ? "No hay gates en el catálogo."
          : "Elige una categoría y un gate.",
      );

      return;
    }
    mutation.mutate({ text, gate_id: gateId });
  }

  return (
    // Rack instrument (ui-polish-spec §4.1): the form is the NUEVO LOTE
    // plate of the cockpit; legendAs="h2" keeps a heading in the outline.
    <SectionCard
      className="flex flex-col gap-4"
      legend="NUEVO LOTE"
      legendAs="h2"
    >
      {banner && <Alert status="danger">{banner}</Alert>}

      <Form className="flex flex-col gap-4" onSubmit={onSubmit}>
        {isLive ? (
          // Active-gate chip (UX-DR9 / prefijo-chip token): name · value.
          <div className="flex flex-wrap items-center gap-2">
            <LabelCaps>Gate activo</LabelCaps>
            <MonoChip>
              {live.gateName} · {live.gateValue}
            </MonoChip>
            {/* "No hay gates en el catálogo." — no select to anchor to while
                live, so it speaks as an operation Alert (§1.8 case 2). */}
            {selectError && (
              <Alert className="w-full" status="danger">
                {selectError}
              </Alert>
            )}
          </div>
        ) : (
          // Stacked vertical selects (ui-polish-spec §4.5): inside the 300px
          // cockpit column there is no side-by-side; the shared selectError
          // anchors to the guilty select via isInvalid + FieldError.
          <div className="flex flex-col gap-3">
            <Select
              className="w-full"
              isDisabled={isLive}
              isInvalid={selectError !== null && categoryKey == null}
              placeholder="Elige una categoría"
              selectedKey={categoryKey}
              onSelectionChange={(key) => {
                setCategoryKey(key == null ? null : String(key));
                setGateKey(null); // changing category resets the gate pick
                if (selectError) setSelectError(null);
              }}
            >
              <Label>Categoría</Label>
              <Select.Trigger>
                <Select.Value />
                <Select.Indicator />
              </Select.Trigger>
              {selectError !== null && categoryKey == null && (
                <FieldError>{selectError}</FieldError>
              )}
              <Select.Popover>
                <ListBox>
                  {categories.map((name) => (
                    <ListBox.Item key={name} id={name} textValue={name}>
                      {name}
                    </ListBox.Item>
                  ))}
                </ListBox>
              </Select.Popover>
            </Select>

            <Select
              className="w-full"
              isDisabled={isLive || categoryKey == null}
              isInvalid={selectError !== null && categoryKey != null}
              placeholder="Elige un gate"
              selectedKey={gateKey}
              onSelectionChange={(key) => {
                setGateKey(key == null ? null : String(key));
                if (selectError) setSelectError(null);
              }}
            >
              <Label>Gate</Label>
              <Select.Trigger>
                <Select.Value />
                <Select.Indicator />
              </Select.Trigger>
              {selectError !== null && categoryKey != null && (
                <FieldError>{selectError}</FieldError>
              )}
              <Select.Popover>
                <ListBox>
                  {gatesInCategory.map((g) => (
                    <ListBox.Item
                      key={g.id}
                      id={String(g.id)}
                      textValue={`${g.name} ${g.value}`}
                    >
                      <span>{g.name}</span>{" "}
                      <span className="font-mono text-muted">{g.value}</span>
                    </ListBox.Item>
                  ))}
                </ListBox>
              </Select.Popover>
            </Select>
          </div>
        )}

        <TextField
          className="flex flex-col gap-1"
          isInvalid={textError !== null}
          name="lines"
          value={text}
          onChange={(v) => {
            setText(v);
            if (textError) setTextError(null);
          }}
        >
          <Label>Líneas</Label>
          <TextArea
            className="min-h-40 font-mono"
            placeholder="Pega tus líneas"
          />
          {textError && <FieldError>{textError}</FieldError>}
        </TextField>

        {/* Appending while 'stopping' is rejected server-side (409
            batch_stopping) — disable here too, defense in both layers. */}
        <Button
          isDisabled={mutation.isPending || live.state === "stopping"}
          type="submit"
          variant="primary"
        >
          {mutation.isPending ? "Enviando…" : "Enviar"}
        </Button>
      </Form>
    </SectionCard>
  );
}
