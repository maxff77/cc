"use client";

// Envío surface (Story 2.2; controls + FloodWait notice since 2.3; live
// Completa/Filtrada views since 3.2). Live state is driven ONLY by the WS
// store (UX-DR12 — no optimistic state beyond the server-confirmed POST
// seed). Desktop ≥lg: 3-col grid 300px 1fr 1fr (UX-DR19) — cockpit left, the
// Completa and Filtrada panels side by side.
import { useQuery } from "@tanstack/react-query";
import { Alert } from "@heroui/react";

import { api } from "@/lib/api";
import { useLiveBatch } from "@/lib/ws";
import { BatchControls } from "@/components/batch/batch-controls";
import { FailedLines } from "@/components/batch/failed-lines";
import { FloodNotice } from "@/components/batch/flood-notice";
import { IdleRing, ProgressRing } from "@/components/batch/progress-ring";
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
        ) : (
          <IdleRing />
        )}

        {/* Active capture session (show / rename / nueva) — sits under the
            ring so the user always knows which session he's filling. Renders
            nothing when no session is active. */}
        <ActiveSessionCard />

        {/* Mobile order per DESIGN.md: ring → controls → data panels → form. */}
        <BatchControls live={live} />
        {/* Watchdog global pause (4.1): danger banner + owner-only resume —
            above FloodNotice (a latched pause outranks a transient wait). */}
        <WatchdogNotice />
        <FloodNotice />
        {/* Failed lines (2.5, AC 4): visibility comes from this panel — the
            ring keeps EXACTLY three metrics (UX-DR21). */}
        <FailedLines live={live} />

        {/* Mobile dual views (3.2): segmented tabs, capped height with
            internal scroll — the form below stays reachable. Always rendered
            (never gated on isLive): in idle they show the empty states or
            the still-active session's rows — the data survives the lote. */}
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
          <SectionCard legend="NUEVO LOTE" padding="none">
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

      {/* Desktop data panels (3.2): COMPLETA / FILTRADA side by side; the
          lists scroll internally — the cockpit stays sticky. */}
      <CompletaPanel
        className="hidden lg:flex"
        exportPath={exportCompleta}
        listClassName="lg:max-h-[calc(100vh-8rem)]"
        responses={live.responses}
        total={live.responsesTotal}
      />
      <FiltradaConResponsePanel
        className="hidden lg:flex"
        exportPath={exportFiltradaCompleta}
        listClassName="lg:max-h-[calc(100vh-8rem)]"
        responses={live.responses}
        total={live.responsesOkTotal}
      />
      <FiltradaPanel
        cc={live.cc}
        className="hidden lg:flex"
        exportPath={exportFiltrada}
        listClassName="lg:max-h-[calc(100vh-8rem)]"
        total={live.ccNew}
      />
    </div>
  );
}
