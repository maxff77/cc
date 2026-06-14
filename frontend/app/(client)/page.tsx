"use client";

// Envío surface (Story 2.2; controls + FloodWait notice since 2.3; live response
// views since 3.2). Live state is driven ONLY by the WS store (UX-DR12 — no
// optimistic state beyond the server-confirmed POST seed). Layout (Ranger-X
// handoff): a sticky 320px cockpit (ring → session → controls → form) beside the
// three result panels — side-by-side on desktop, segmented tabs on phone/tablet.
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

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
import { Notice } from "@/components/ui/notice";
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

  // Export `↓ .txt` (Story 3.5): paths exist only once a session does. NOT gated
  // on isLive: export works DURING the lote (AC 2) and after.
  const exportBase =
    live.sessionId !== null ? `/api/sessions/${live.sessionId}/export` : null;
  const exportCompleta = exportBase ? `${exportBase}?view=completa` : undefined;
  const exportFiltradaCompleta = exportBase
    ? `${exportBase}?view=filtrada_completa`
    : undefined;
  const exportFiltrada = exportBase ? `${exportBase}?view=filtrada` : undefined;

  return (
    <div className="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)] lg:items-start">
      {/* Master — ring, session, controls, form. Pinned on wide screens so live
          state stays in view while the panels scroll. */}
      <div className="flex flex-col gap-4 lg:sticky lg:top-6">
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

        <ActiveSessionCard />
        <PlanExpiryNotice />
        <BatchControls live={live} />
        <WatchdogNotice />
        <FloodNotice />
        <FailedLines live={live} />
        <PendingLines live={live} />

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
          the still-active session's rows — the data survives the lote. */}
      <div className="min-w-0">
        <div className="hidden lg:block">
          <ResponseColumns
            cc={live.cc}
            ccTotal={live.ccNew}
            exportPathCompleta={exportCompleta}
            exportPathFiltrada={exportFiltrada}
            exportPathFiltradaCompleta={exportFiltradaCompleta}
            responses={live.responses}
            responsesOkTotal={live.responsesOkTotal}
            responsesTotal={live.responsesTotal}
          />
        </div>
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
      </div>
    </div>
  );
}
