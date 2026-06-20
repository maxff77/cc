"use client";

// Envío surface (Story 2.2; controls + FloodWait notice since 2.3; live response
// views since 3.2). Live state is driven ONLY by the WS store (UX-DR12 — no
// optimistic state beyond the server-confirmed POST seed). Layout (Ranger-X
// handoff): a 360px cockpit column (ring → session → controls → form) that
// scrolls within a viewport-height grid, beside the three result panels —
// side-by-side on desktop, segmented tabs on phone/tablet. The grid is capped to
// the viewport on lg so the page never grows into a runaway scroll; each pane
// scrolls on its own. BOTH columns inherit the cap (lg:h-full lg:min-h-0) and
// the right column passes `fill` so its panels stretch to the cap — lists scroll
// inside each panel and no dead space opens below short results.
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import { clearCockpit, useLiveBatch } from "@/lib/ws";
import { AwaitingReply } from "@/components/batch/awaiting-reply";
import { BatchControls } from "@/components/batch/batch-controls";
import { CookiesExhaustedNotice } from "@/components/batch/cookies-exhausted-notice";
import { FailedLines } from "@/components/batch/failed-lines";
import { FloodNotice } from "@/components/batch/flood-notice";
import { PendingLines } from "@/components/batch/pending-lines";
import { PlanExpiryNotice } from "@/components/batch/plan-expiry-notice";
import {
  CompletionRing,
  IdleRing,
  ProgressRing,
  type RunSummary,
} from "@/components/batch/progress-ring";
import { SendForm, type GateOut } from "@/components/batch/send-form";
import { VerdictTimeoutNotice } from "@/components/batch/verdict-timeout-notice";
import { WaitingNotice } from "@/components/batch/waiting-notice";
import { WatchdogNotice } from "@/components/batch/watchdog-notice";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";
import { Notice } from "@/components/ui/notice";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  ResponseColumns,
  ResponseTabs,
} from "@/components/sessions/response-views";

interface GateListResponse {
  items: GateOut[];
  total: number;
}

export default function EnvioPage() {
  const live = useLiveBatch();
  const gates = useQuery({
    queryKey: ["gates"],
    queryFn: () => api.get<GateListResponse>("/api/gates"),
  });

  // A paused/stopping lote keeps its ring on screen — only idle hides it.
  const isLive = live.state !== "idle";

  // Completion moment (P2): on the active→idle transition show a brief success
  // summary in the ring slot, then auto-revert to the idle em-dash. Totals must
  // be SNAPSHOTTED here: the store zeroes `sent`/`total` the instant it goes
  // idle, so we read them from the PREVIOUS render via a ref. Duration is
  // derived from a first-live timestamp — no backend call.
  const [completed, setCompleted] = useState<RunSummary | null>(null);
  const prevRef = useRef({
    state: live.state,
    sent: live.sent,
    ccNew: live.ccNew,
  });
  const startedAtRef = useRef<number | null>(null);

  useEffect(() => {
    const prev = prevRef.current;

    if (prev.state === "idle" && live.state !== "idle") {
      startedAtRef.current = Date.now();
      setCompleted(null);
    }

    if (prev.state !== "idle" && live.state === "idle" && prev.sent > 0) {
      const startedAt = startedAtRef.current;

      setCompleted({
        sent: prev.sent,
        ccCaptured: prev.ccNew,
        durationSeconds:
          startedAt !== null ? (Date.now() - startedAt) / 1000 : null,
      });
      startedAtRef.current = null;
    }

    prevRef.current = {
      state: live.state,
      sent: live.sent,
      ccNew: live.ccNew,
    };
  }, [live.state, live.sent, live.ccNew]);

  useEffect(() => {
    if (completed === null) return;
    const id = window.setTimeout(() => setCompleted(null), 6000);

    return () => window.clearTimeout(id);
  }, [completed]);

  // "Limpiar" literal (PR-1): vacía las TRES vistas en vivo a la vez (Completa,
  // Aprobadas-✅, Datos-CC). No borra ninguna fila de `responses` — el backend
  // sella un corte de vista por sesión (`cleared_response_id`, un high-water de
  // id) sobre la única sesión perpetua del tenant y re-emite `session.active`
  // con el slice (ya vacío) posterior al corte; las ✅ aprobadas sobreviven en
  // la base para el historial diferido (PR-2). Al confirmar también reseteamos
  // el store local (`clearCockpit`) para que la pestaña que actúa vea las vistas
  // vacías al instante (las demás reconcilian con el evento / su próximo
  // snapshot). El botón se habilita sólo si alguna vista tiene filas.
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);
  const allPanelsEmpty = live.responsesTotal === 0 && live.ccNew === 0;
  const clear = useMutation({
    mutationFn: () =>
      api.post<{ cleared_response_id: number | null }>("/api/sessions/clear"),
    onSuccess: () => {
      clearCockpit();
      setConfirmOpen(false);
      setClearError(null);
    },
    onError: (err) =>
      setClearError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      ),
  });

  // Export `↓ .txt` (cockpit): the perpetual-session export at GET
  // /api/sessions/export (no path id) RESPECTS the Limpiar cutoff — the file
  // mirrors the live post-clear view. Same `view` selector the footer already
  // sends. NOT gated on isLive: export works DURING the lote and after. (The
  // admin/PR-2 full-history export keeps its own `/{id}/export` route.)
  // Gated on a perpetual session existing — a brand-new tenant that never sent
  // a batch has none, so we hide the footer (undefined ⇒ no button) instead of
  // letting a click hit a 404.
  const exportBase = "/api/sessions/export";
  const canExport = live.sessionId !== null;
  const exportCompleta = canExport ? `${exportBase}?view=completa` : undefined;
  const exportFiltradaCompleta = canExport
    ? `${exportBase}?view=filtrada_completa`
    : undefined;
  const exportFiltrada = canExport ? `${exportBase}?view=filtrada` : undefined;

  return (
    <div className="grid gap-5 overflow-hidden lg:h-full lg:min-h-0 lg:grid-cols-[360px_minmax(0,1fr)]">
      {/* Master — ring, session, controls, form. On wide screens the grid is
          capped to the viewport (≈ chrome offset) and each column scrolls on its
          own, so the page itself never grows into a runaway scroll. Below lg the
          page flows normally (no height cap). */}
      <div className="flex flex-col gap-4 lg:h-full lg:min-h-0 lg:overflow-y-auto rx-scroll lg:pr-1">
        {/* Waiting (4.2): the queue position replaces the ring — a 0% ring
            would read as a silent stall. Idle renders the ring at 0 so starting
            a lote causes no layout jump. */}
        {live.state === "waiting" ? (
          <WaitingNotice live={live} />
        ) : isLive ? (
          <ProgressRing live={live} />
        ) : completed ? (
          <CompletionRing summary={completed} />
        ) : (
          <IdleRing />
        )}

        <PlanExpiryNotice />
        <BatchControls live={live} />
        <WatchdogNotice />
        {/* Cookies-exhausted prompt (Phase 2): only when the live batch paused
            for that reason — add cookies + Reanudar without leaving the cockpit. */}
        {live.state === "paused" &&
          live.pauseReason === "cookies_exhausted" && (
            <CookiesExhaustedNotice />
          )}
        {/* Verdict-timeout prompt (Phase 2, patch #6): the gate went silent past
            the retry-once budget — no CookieManager, just an explanation + Reanudar. */}
        {live.state === "paused" &&
          live.pauseReason === "verdict_timeout" && <VerdictTimeoutNotice />}
        <FloodNotice />
        <FailedLines live={live} />
        <PendingLines live={live} />
        <AwaitingReply live={live} />

        {gates.isLoading && (
          <SectionCard legend="Nuevo lote" padding="none">
            <PanelSkeleton rows={2} />
          </SectionCard>
        )}
        {gates.isError && (
          <Notice status="danger">
            No pudimos cargar el catálogo. Recarga la página.
          </Notice>
        )}
        {gates.data && <SendForm gates={gates.data.items} live={live} />}
      </div>

      {/* Detail — the Completa/Filtrada views the operator watches. Three
          side-by-side panels on desktop; segmented tabs on phone/tablet. Always
          rendered (never gated on isLive): in idle it shows the empty states or
          the still-active session's rows — the data survives the lote. On lg the
          column inherits the grid's viewport cap and `fill` stretches the panels
          to it, so each list scrolls INSIDE its panel and no dead space opens
          below short/empty results. Below lg the tabs flow with the page. */}
      <div className="min-w-0 lg:h-full lg:min-h-0">
        <div className="hidden lg:block lg:h-full lg:min-h-0">
          <ResponseColumns
            cc={live.cc}
            ccTotal={live.ccNew}
            clearDisabled={allPanelsEmpty}
            exportPathCompleta={exportCompleta}
            exportPathFiltrada={exportFiltrada}
            exportPathFiltradaCompleta={exportFiltradaCompleta}
            fill
            responses={live.responses}
            responsesOkTotal={live.responsesOkTotal}
            responsesTotal={live.responsesTotal}
            onClear={() => setConfirmOpen(true)}
          />
        </div>
        <ResponseTabs
          cc={live.cc}
          ccTotal={live.ccNew}
          className="lg:hidden"
          clearDisabled={allPanelsEmpty}
          exportPathCompleta={exportCompleta}
          exportPathFiltrada={exportFiltrada}
          exportPathFiltradaCompleta={exportFiltradaCompleta}
          responses={live.responses}
          responsesOkTotal={live.responsesOkTotal}
          responsesTotal={live.responsesTotal}
          onClear={() => setConfirmOpen(true)}
        />
      </div>

      {/* Confirmación de "Limpiar" (literal, PR-1) — vacía las tres vistas en
          vivo; no se borra ningún dato del servidor (corte de vista). */}
      <ConfirmDialog
        confirmLabel={clear.isPending ? "Limpiando…" : "Limpiar"}
        confirmVariant="primary"
        heading="¿Limpiar las tres vistas (Completa, Aprobadas y Datos CC)? Quedarán vacías en pantalla; no se borra ningún dato del servidor."
        open={confirmOpen}
        pending={clear.isPending}
        onConfirm={() => clear.mutate()}
        onOpenChange={(open) => {
          setConfirmOpen(open);
          if (!open) setClearError(null);
        }}
      >
        {clearError && <Notice status="danger">{clearError}</Notice>}
      </ConfirmDialog>
    </div>
  );
}
