"use client";

// Paste-first send form (Story 2.2): textarea + TWO-STEP gate selector
// (category → gate, AC 13) + Enviar. Never free text for the gate (UX-DR9).
// During a live lote the selects lock and a chip shows the active gate; the
// textarea + Enviar stay usable — submitting APPENDS (AC 10).
import type { LiveBatchState } from "@/lib/ws";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { seedFromBatch } from "@/lib/ws";
import { usePersisted } from "@/lib/use-persisted";
import { CookieManager } from "@/components/batch/cookie-manager";
import { LabelCaps } from "@/components/ui/label-caps";
import { MonoChip } from "@/components/ui/mono-chip";
import { SectionCard } from "@/components/ui/section-card";
import { Select } from "@/components/ui/select";
import { Area } from "@/components/ui/area";
import { Btn } from "@/components/ui/btn";
import { Notice } from "@/components/ui/notice";

// Mirrors backend PublicGateOut (snake_case end-to-end). The real command
// `value` is owner-only and NOT exposed here — clients see `display_value`
// ("Comando visible") instead.
export interface GateOut {
  id: number;
  name: string;
  display_value: string;
  // Credits charged per captured ✅ (credits feature). 0 ⇒ free gate.
  credit_cost: number;
  category_id: number;
  category_name: string;
  // Cookie-vault Phase 1: a plain UX boolean (sourced from
  // `gate.category.cookie_mode`) — when true the cockpit shows the cookie
  // manager for this gate. Never carries the gate `value`. Optional so a backend
  // that predates the field (or a non-cookie gate) reads as `undefined` → false.
  cookie_mode?: boolean;
  created_at: string;
}

interface BatchOut {
  id: number;
  gate_name: string;
  gate_display_value: string;
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
const ME_KEY = ["me"] as const;

// The client's plan slice on /api/auth/me — null for owner/admin or a
// pre-catalog client (plan_id IS NULL → no line cap). Only max_lines_per_batch
// is read here for the pre-submit guard.
interface PlanSummary {
  name: string;
  antispam_seconds: number | string;
  max_lines_per_batch: number;
}

interface Me {
  id: number;
  email: string;
  role: string;
  tenant_id: number;
  expires_at: string | null;
  plan: PlanSummary | null;
}

// Count of lines the backend would queue from a paste: trimmed, blank-skipped,
// deduplicated — mirrors services/batches.apply_gate (the gate prefix never
// changes the count). UX-only; the backend re-counts and stays authoritative.
function countLines(text: string): number {
  const seen = new Set<string>();

  for (const raw of text.split("\n")) {
    const line = raw.trim();

    if (line) seen.add(line);
  }

  return seen.size;
}

export function SendForm({
  gates,
  live,
}: {
  gates: GateOut[];
  live: LiveBatchState;
}) {
  const queryClient = useQueryClient();
  // The plan's max_lines_per_batch fuels a pre-submit cap guard (UX only — the
  // backend enforces batch_line_limit authoritatively). Shared ["me"] query so
  // it dedupes with anything else reading identity; null plan → no cap.
  const me = useQuery({
    queryKey: ME_KEY,
    queryFn: () => api.get<Me>("/api/auth/me"),
  });
  const lineCap = me.data?.plan?.max_lines_per_batch ?? null;
  // Persisted across refresh/new tabs (localStorage) so a reload doesn't wipe
  // the operator's category/gate pick or a half-pasted batch. Cleared like
  // before — on a successful create the setText("") write empties the store.
  const [text, setText] = usePersisted<string>("rx.send.text", "");
  const [categoryKey, setCategoryKey] = usePersisted<string | null>(
    "rx.send.category",
    null,
  );
  const [gateKey, setGateKey] = usePersisted<string | null>(
    "rx.send.gate",
    null,
  );
  const [textError, setTextError] = useState<string | null>(null);
  const [selectError, setSelectError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  // Any live state (sending/paused/stopping) locks the selector and shows the
  // gate chip — appending to a PAUSED lote is allowed (Story 2.3).
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

  // The gate currently PICKED in the idle selector (null while live or until a
  // gate is chosen). Drives the cookie-vault manager: it shows ONLY for a
  // cookie-mode gate while the surface is idle (cookie-vault Phase 1) — appending
  // to a live lote locks the selector, so there is no gate to manage then.
  const pickedGate = useMemo(
    () => gatesInCategory.find((g) => String(g.id) === gateKey) ?? null,
    [gatesInCategory, gateKey],
  );

  // On append the backend validates the id but applies the LIVE gate's value
  // regardless — submit the live gate's id (any valid id works as fallback).
  const liveGateId = useMemo(() => {
    if (!isLive) return null;
    const match = gates.find((g) => g.display_value === live.gateDisplayValue);

    return (match ?? gates[0])?.id ?? null;
  }, [isLive, gates, live.gateDisplayValue]);

  // The gate this submit applies to (credits feature): the live batch's gate
  // when appending, the picked gate otherwise. Its credit_cost drives the
  // balance display + pre-submit guard. The balance is the WS store's live
  // value (snapshot + credits.updated keep it current — owner recharge reflects
  // live, AC).
  const effectiveGate = useMemo(() => {
    if (isLive)
      return (
        gates.find((g) => g.display_value === live.gateDisplayValue) ?? null
      );

    return gatesInCategory.find((g) => String(g.id) === gateKey) ?? null;
  }, [isLive, gates, live.gateDisplayValue, gatesInCategory, gateKey]);
  const gateCost = effectiveGate?.credit_cost ?? 0;
  // Only non-staff tenants are metered — mirrors the backend's priority>0
  // exemption (_PRIORITY_BY_ROLE in batches.py: owner/admin are exempt, every
  // other role meters). Exempt by an explicit staff list (not `=== "client"`)
  // so a future/unknown role stays metered in lockstep with the backend. While
  // /me is still loading (or errored), DON'T meter: never flash a credit block
  // or a misleading "Créditos: 0" at staff — the backend stays authoritative
  // and a client briefly un-gated is harmless (the create/append guard 403s).
  const isMetered = me.data
    ? !["owner", "admin"].includes(me.data.role)
    : false;
  // A costed gate with no balance blocks the send for clients only (backend
  // authoritative).
  const blockedByCredits = isMetered && gateCost > 0 && live.creditBalance <= 0;

  const mutation = useMutation({
    mutationFn: (payload: { text: string; gate_id: number }) =>
      api.post<BatchOut>("/api/batches", payload),
    onSuccess: (data) => {
      setText("");
      if (!data.appended) {
        // Flip into live mode immediately; snapshot/batch.state stay the source
        // of truth from here on (UX-DR12).
        seedFromBatch(data);
      }
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.code === "empty_batch" || err.code === "batch_line_limit") {
          // batch_line_limit: the authoritative backend cap rejection (e.g. a
          // stale plan in the ["me"] cache) — surface its Spanish copy on the
          // textarea, same lane as empty_batch.
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

    // Plan line-cap guard (plan-catalog feature): block before the request when
    // a NEW batch's paste would exceed the client's max_lines_per_batch. Only
    // the create case is guarded pre-submit: there is no pending queue to dedup
    // against, so countLines exactly matches what the backend will queue. On
    // APPEND the backend dedups the paste against the already-pending texts
    // before counting (batches.py), and the cockpit only holds a CAPPED pending
    // list — so a client-side resulting-size estimate would over-count and
    // falsely block a paste the backend would accept. The backend stays
    // authoritative there (its batch_line_limit message surfaces in the banner).
    // No plan → no cap.
    if (lineCap !== null && !isLive) {
      const incoming = countLines(text);

      if (incoming > lineCap) {
        setTextError(`Máximo ${lineCap} líneas; tienes ${incoming}.`);

        return;
      }
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

    // Credit guard (credits feature): block a costed gate with no balance before
    // the request (the backend's insufficient_credits stays authoritative). Free
    // gates (cost 0) never block.
    if (blockedByCredits) {
      setSelectError(
        `Sin créditos para el gate “${effectiveGate?.name ?? ""}”. Recarga para continuar.`,
      );

      return;
    }
    mutation.mutate({ text, gate_id: gateId });
  }

  const categoryOptions = useMemo(
    () => categories.map((name) => ({ id: name, label: name })),
    [categories],
  );
  const gateOptions = useMemo(
    () =>
      gatesInCategory.map((g) => ({
        id: String(g.id),
        label: g.name,
        mono: g.display_value,
      })),
    [gatesInCategory],
  );

  return (
    <div className="flex flex-col gap-4">
      {/* Rack instrument (ui-polish-spec §4.1): the form is the Nuevo lote plate of
        the cockpit; legendAs="h2" keeps a heading in the outline. */}
      <SectionCard
        className="flex flex-col gap-4"
        legend="Nuevo lote"
        legendAs="h2"
      >
        {banner && <Notice status="danger">{banner}</Notice>}
        <form className="flex flex-col gap-3.5" onSubmit={onSubmit}>
          {isLive ? (
            // Active-gate chip (UX-DR9): name · comando visible.
            <div className="flex flex-wrap items-center gap-2">
              <LabelCaps>Gate activo</LabelCaps>
              <MonoChip>
                {live.gateName} · {live.gateDisplayValue}
              </MonoChip>
              {selectError && (
                <Notice className="w-full" status="danger">
                  {selectError}
                </Notice>
              )}
            </div>
          ) : (
            // Stacked vertical selects (ui-polish-spec §4.5): inside the cockpit
            // column there is no side-by-side; the shared selectError anchors to
            // the guilty select.
            <div className="flex flex-col gap-3">
              <Select
                error={
                  selectError !== null && categoryKey == null
                    ? selectError
                    : null
                }
                label="Categoría"
                options={categoryOptions}
                placeholder="Elige una categoría"
                value={categoryKey}
                onChange={(key) => {
                  setCategoryKey(key);
                  setGateKey(null); // changing category resets the gate pick
                  if (selectError) setSelectError(null);
                }}
              />
              <Select
                disabled={categoryKey == null}
                error={
                  selectError !== null && categoryKey != null
                    ? selectError
                    : null
                }
                label="Gate"
                options={gateOptions}
                placeholder="Elige un gate"
                value={gateKey}
                onChange={(key) => {
                  setGateKey(key);
                  if (selectError) setSelectError(null);
                }}
              />
            </div>
          )}
          {/* Credits strip (credits feature): the tenant's live balance + the
            selected gate's per-✅ cost. Turns into a warning when a costed gate
            has no balance (the send is blocked). Clients only — owner/admin are
            exempt from credits, so the strip would only show a misleading
            "Créditos: 0". */}
          {isMetered && (
            <div className="flex items-center justify-between text-[11px]">
              <span
                className={
                  live.creditBalance <= 0 ? "text-danger" : "text-muted"
                }
              >
                Créditos:{" "}
                <span className="tabular-nums">{live.creditBalance}</span>
              </span>
              {gateCost > 0 && (
                <span
                  className={blockedByCredits ? "text-danger" : "text-muted"}
                >
                  {gateCost} créd./✅
                </span>
              )}
            </div>
          )}
          <Area
            error={textError}
            label="Líneas"
            placeholder="Pega tus líneas"
            rows={5}
            value={text}
            onChange={(v) => {
              setText(v);
              if (textError) setTextError(null);
            }}
          />
          {/* The commit action wears the brand gradient (a text-free-ish commit
            moment); appending while 'stopping' is rejected server-side. */}
          <Btn
            full
            disabled={
              mutation.isPending ||
              live.state === "stopping" ||
              blockedByCredits
            }
            icon="send"
            type="submit"
            variant="primary"
          >
            {mutation.isPending ? "Enviando…" : "Enviar"}
          </Btn>
        </form>
      </SectionCard>

      {/* Cookie vault (cookie-vault Phase 1): the manager mounts ONLY for a
          cookie-mode gate while the surface is idle — a live lote locks the
          selector, so there's no picked gate to manage. */}
      {!isLive && pickedGate?.cookie_mode && (
        <CookieManager gateId={pickedGate.id} />
      )}
    </div>
  );
}
