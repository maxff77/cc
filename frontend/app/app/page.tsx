"use client";

// Envío surface (Story 2.2; controls + FloodWait notice since 2.3; live response
// views since 3.2). Live state is driven ONLY by the WS store (UX-DR12 — no
// optimistic state beyond the server-confirmed POST seed). Layout (Ranger-X
// handoff): a 320px cockpit column (ring → session → controls → form) that
// scrolls within a viewport-height grid, beside the three result panels —
// side-by-side on desktop, segmented tabs on phone/tablet. The grid is capped to
// the viewport on lg so the page never grows into a runaway scroll; each pane
// scrolls on its own. BOTH columns inherit the cap (lg:h-full lg:min-h-0) and
// the right column passes `fill` so its panels stretch to the cap — lists scroll
// inside each panel and no dead space opens below short results.
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useLiveBatch } from "@/lib/ws";
import { ClaimKey } from "@/components/keys/claim-key";
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
  const queryClient = useQueryClient();
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

  // "Limpiar" la vista Completa (clear-completa-view): a VIEW-only declutter,
  // local to this tab — it hides the rows currently shown in Completa WITHOUT
  // deleting anything (data stays in Postgres) and WITHOUT touching Aprobadas
  // (which filters the same `live.responses`). We snapshot the visible row keys
  // into `clearedKeys`; Completa renders only rows whose key isn't hidden, so
  // replies captured AFTER the clear (fresh keys) keep appearing. The keys are
  // monotonic and get reassigned on reconnect/session change, so the set
  // self-invalidates on the next snapshot (rows reappear) and stays bounded.
  const [clearedKeys, setClearedKeys] = useState<Set<string>>(() => new Set());

  // A clear is scoped to the session the operator was watching: drop it when
  // the active session changes (gate swap / Continuar) so the stale key set
  // never lingers across sessions.
  useEffect(() => {
    setClearedKeys(new Set());
  }, [live.sessionId]);
  const completaResponses = useMemo(
    () => live.responses.filter((row) => !clearedKeys.has(row.key)),
    [live.responses, clearedKeys],
  );
  // Rows of the (500-capped) live list currently hidden by the clear. When >0
  // the badge mirrors the VISIBLE count so it can never read a phantom number
  // over an empty list (a clear with a server-capped total used to leave the
  // badge at `total − 500`). When 0 — nothing cleared, or the cleared rows aged
  // out of the cap / a reconnect re-keyed them — it falls back to the
  // authoritative server total, preserving the capped "real total" badge.
  const hiddenCount = live.responses.length - completaResponses.length;
  const completaTotal =
    hiddenCount > 0 ? completaResponses.length : live.responsesTotal;
  const handleClearCompleta = () =>
    setClearedKeys(new Set(live.responses.map((row) => row.key)));

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
    <div className="grid gap-5 overflow-hidden lg:h-full lg:min-h-0 lg:grid-cols-[320px_minmax(0,1fr)]">
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

        <ActiveSessionCard />
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

        {/* Redeem a gift key from the cockpit (active client): +days on the
            current plan. On success refresh /me so the plan badge updates. */}
        <SectionCard legend="Canjear key" legendAs="h2">
          <ClaimKey
            onClaimed={() =>
              queryClient.invalidateQueries({ queryKey: ["me"] })
            }
          />
        </SectionCard>
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
            completaResponses={completaResponses}
            completaTotal={completaTotal}
            exportPathCompleta={exportCompleta}
            exportPathFiltrada={exportFiltrada}
            exportPathFiltradaCompleta={exportFiltradaCompleta}
            fill
            responses={live.responses}
            responsesOkTotal={live.responsesOkTotal}
            responsesTotal={live.responsesTotal}
            onClearCompleta={handleClearCompleta}
          />
        </div>
        <ResponseTabs
          cc={live.cc}
          ccTotal={live.ccNew}
          className="lg:hidden"
          completaResponses={completaResponses}
          completaTotal={completaTotal}
          exportPathCompleta={exportCompleta}
          exportPathFiltrada={exportFiltrada}
          exportPathFiltradaCompleta={exportFiltradaCompleta}
          responses={live.responses}
          responsesOkTotal={live.responsesOkTotal}
          responsesTotal={live.responsesTotal}
          onClearCompleta={handleClearCompleta}
        />
      </div>
    </div>
  );
}
