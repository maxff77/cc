"use client";

// Envío surface (Story 2.2; controls + FloodWait notice since 2.3; live
// response views since 3.2). Live state is driven ONLY by the WS store
// (UX-DR12 — no optimistic state beyond the server-confirmed POST seed).
// Desktop ≥lg: 4-col grid 300px 1fr 1fr 1fr (UX-DR19) — cockpit left, then the
// three response panels (todas / aprobadas / datos CC) side by side.
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert } from "@heroui/react";

import { api } from "@/lib/api";
import { useLiveBatch } from "@/lib/ws";
import { BatchControls } from "@/components/batch/batch-controls";
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
import { WaitingNotice } from "@/components/batch/waiting-notice";
import { WatchdogNotice } from "@/components/batch/watchdog-notice";
import { ActiveSessionCard } from "@/components/sessions/active-session-card";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { SectionCard } from "@/components/ui/section-card";
import {
  CompletaPanel,
  FiltradaConResponsePanel,
  FiltradaPanel,
  ResponseTabs,
  ResponseViewsLegend,
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
  // summary in the ring slot, then auto-revert to the idle em-dash (calmest for
  // a daily tool — no banner to dismiss). Totals must be SNAPSHOTTED here: the
  // store zeroes `sent`/`total` the instant it goes idle (the batch.state idle
  // reducer), so we read them from the PREVIOUS render via a ref. `ccNew`
  // survives the idle reset (session-scoped) but we snapshot it too so the
  // summary is frozen against late captures. Duration is derived from a
  // first-live timestamp — no backend call. Auto-dismiss after a few seconds OR
  // immediately when a new lote starts (the effect re-runs and clears it), so
  // it never blocks starting again.
  const [completed, setCompleted] = useState<RunSummary | null>(null);
  const prevRef = useRef({
    state: live.state,
    sent: live.sent,
    ccNew: live.ccNew,
  });
  const startedAtRef = useRef<number | null>(null);

  useEffect(() => {
    const prev = prevRef.current;

    // Mark the run's start the first time it goes live (waiting/sending) from
    // idle — used only to derive the on-screen duration.
    if (prev.state === "idle" && live.state !== "idle") {
      startedAtRef.current = Date.now();
      // A fresh lote starts ⇒ retire any lingering completion summary at once.
      setCompleted(null);
    }

    // active → idle with a real run (sent > 0): show the payoff. A stop with
    // nothing sent (or an empty drain) reverts silently — no false celebration.
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

  // Auto-dismiss the summary a few seconds after it appears (control-room calm:
  // it fades back to the clean idle em-dash). A new lote also clears it (above).
  useEffect(() => {
    if (completed === null) return;
    const id = window.setTimeout(() => setCompleted(null), 6000);

    return () => window.clearTimeout(id);
  }, [completed]);

  // Export `↓ .txt` (Story 3.5): paths exist only once a session does —
  // before the first lote there is nothing to export, so the link is not
  // rendered (never a dead disabled button). NOT gated on isLive: export
  // works DURING the lote (AC 2) and after — the session and its sessionId
  // survive the lote ("capture stays armed").
  const exportBase =
    live.sessionId !== null ? `/api/sessions/${live.sessionId}/export` : null;
  const exportCompleta = exportBase ? `${exportBase}?view=completa` : undefined;
  const exportFiltradaCompleta = exportBase
    ? `${exportBase}?view=filtrada_completa`
    : undefined;
  const exportFiltrada = exportBase ? `${exportBase}?view=filtrada` : undefined;

  return (
    <div className="mx-auto w-full max-w-[1600px] lg:grid lg:grid-cols-[300px_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)] lg:items-start lg:gap-6">
      {/* Cockpit column — pinned on desktop, single column on mobile. */}
      <div className="flex flex-col gap-5 lg:sticky lg:top-6 lg:self-start">
        {/* Waiting (4.2): the queue position replaces the ring — a 0% ring
            would read as a silent stall (AC 2). Idle renders the ring at 0
            (ui-polish-spec §4.2) so starting a lote causes no layout jump. */}
        {live.state === "waiting" ? (
          <WaitingNotice live={live} />
        ) : isLive ? (
          <ProgressRing live={live} />
        ) : completed ? (
          // Completion payoff (P2): occupies the ring slot briefly on the
          // active→idle transition, then auto-reverts to IdleRing. Same
          // footprint → no layout jump on either swap.
          <CompletionRing summary={completed} />
        ) : (
          <IdleRing />
        )}

        {/* Active capture session (show / rename / nueva) — sits under the
            ring so the user always knows which session he's filling. Renders
            nothing when no session is active. */}
        <ActiveSessionCard />

        {/* Plan-expiry PRE-warning (P2): amber heads-up in the days before the
            plan locks out, so the client isn't yanked mid-batch by the edge
            redirect. Dormant until MeResponse surfaces `expires_at` (see
            component TODO) — renders nothing without the field. */}
        <PlanExpiryNotice />

        {/* Mobile order per DESIGN.md: ring → controls → data panels → form. */}
        <BatchControls live={live} />
        {/* Watchdog global pause (4.1): danger banner + owner-only resume —
            above FloodNotice (a latched pause outranks a transient wait). */}
        <WatchdogNotice />
        <FloodNotice />
        {/* Failed lines (2.5, AC 4): visibility comes from this panel — the
            ring keeps EXACTLY three metrics (UX-DR21). */}
        <FailedLines live={live} />
        {/* Pendientes: the still-queued lines, draining one-by-one as they
            send (replaces the "textarea clears all at once" feel). */}
        <PendingLines live={live} />

        {/* Mobile dual views (3.2): segmented tabs, capped height with
            internal scroll — the form below stays reachable. Always rendered
            (never gated on isLive): in idle they show the empty states or
            the still-active session's rows — the data survives the lote. The
            legend spells out the set-relationship so the tabs aren't a memory
            tax (aprobadas ⊂ todas; datos CC extracted from them). */}
        <ResponseViewsLegend className="lg:hidden" />
        <ResponseTabs
          cc={live.cc}
          ccTotal={live.ccNew}
          className="lg:hidden"
          exportPathCompleta={exportCompleta}
          exportPathFiltrada={exportFiltrada}
          exportPathFiltradaCompleta={exportFiltradaCompleta}
          responses={live.responses}
          responsesOkTotal={live.responsesOkTotal}
          responsesTotal={live.responsesTotal}
        />

        {/* Gates loading (ui-polish-spec §4.8): skeleton with the form's own
            plate — never a floating centered spinner. */}
        {gates.isLoading && (
          <SectionCard legend="Nuevo lote" padding="none">
            <PanelSkeleton rows={2} />
          </SectionCard>
        )}
        {gates.isError && (
          <Alert status="danger">
            No pudimos cargar el catálogo. Recarga la página.
          </Alert>
        )}
        {gates.data && <SendForm gates={gates.data.items} live={live} />}
      </div>

      {/* Desktop data panels (3.2): todas / aprobadas / datos CC side by side;
          the lists scroll internally — the cockpit stays sticky. Wrapped in a
          single grid cell spanning columns 2–4 so the legend can sit ABOVE the
          three panels (a bare grid-row span would push them below the tall
          cockpit column). The wrapper re-creates the 3-col track internally. */}
      <div className="hidden lg:col-span-3 lg:flex lg:flex-col lg:gap-3">
        {/* Explains the set-relationship of the three views so their relabeled
            headers aren't a memory tax. */}
        <ResponseViewsLegend />
        <div className="grid grid-cols-[repeat(3,minmax(0,1fr))] items-start gap-6">
          <CompletaPanel
            className="flex"
            exportPath={exportCompleta}
            listClassName="lg:max-h-[calc(100vh-10rem)]"
            responses={live.responses}
            total={live.responsesTotal}
          />
          <FiltradaConResponsePanel
            className="flex"
            exportPath={exportFiltradaCompleta}
            listClassName="lg:max-h-[calc(100vh-10rem)]"
            responses={live.responses}
            total={live.responsesOkTotal}
          />
          <FiltradaPanel
            cc={live.cc}
            className="flex"
            exportPath={exportFiltrada}
            listClassName="lg:max-h-[calc(100vh-10rem)]"
            total={live.ccNew}
          />
        </div>
      </div>
    </div>
  );
}
