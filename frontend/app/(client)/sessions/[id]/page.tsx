"use client";

// Session detail (Story 3.3, AC 2 + 3): the SAME dual Completa/Filtrada
// views as Envío — CompletaPanel/FiltradaPanel/ResponseTabs reused verbatim
// (they are props-driven on purpose; that reusability was a 3.2 design
// requirement). The data arrives COMPLETE by REST (`limit=None` server-side
// — the snapshot's 200-row cap is reconnection-only); the WS store only
// signals "something new" for the live-follow refetch. Export `↓ .txt`
// (Story 3.5): always present here — the session exists, and closed or in
// progress both export (AC 2).
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button } from "@heroui/react";

import { api, ApiError } from "@/lib/api";
import { useLiveBatch, type CcRow, type ResponseRow } from "@/lib/ws";
import {
  CompletaPanel,
  FiltradaPanel,
  ResponseTabs,
} from "@/components/sessions/response-views";
import { EmptyState } from "@/components/ui/empty-state";
import { MonoChip } from "@/components/ui/mono-chip";
import { PageHeader } from "@/components/ui/page-header";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { StatePill } from "@/components/ui/state-pill";

// Local mirrors of the backend session schemas (snake_case end-to-end) —
// the row shapes are the snapshot's, so the 3.2 mappers apply verbatim.
interface SessionResponseRow {
  id: number;
  message_id: number;
  status: "ok" | "rejected";
  text: string;
  created_at: string;
}

interface SessionCcRow {
  id: number;
  text: string;
}

interface SessionDetailOut {
  id: number;
  name: string | null;
  gate_value: string;
  gate_name: string;
  is_active: boolean;
  created_at: string;
  responses: SessionResponseRow[];
  cc: SessionCcRow[];
  responses_total: number;
  cc_total: number;
}

// POST /{id}/continue answers the plain session shape (no rows).
interface SessionOut {
  id: number;
  name: string | null;
  gate_value: string;
  gate_name: string;
  is_active: boolean;
  created_at: string;
}

// ids are int4 server-side — anything beyond can't exist (same guard as the
// backend's _PG_INT_MAX): render the not-found state without a round trip.
const PG_INT_MAX = 2147483647;

// Mirror of the list page's fallback (legacy `nombre_bonito`): local
// "YYYY-MM-DD HH:MM", padStart idiom, no locale.
function fallbackName(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");

  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    ` ${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

// Not-found / bad-id state — never a dead-end (UX-DR16).
function NotFound() {
  return (
    <EmptyState
      action={
        <Link className="text-accent underline" href="/sessions">
          Volver a Historial
        </Link>
      }
      className="py-24"
      eyebrow="Historial"
      message="Esa sesión no existe."
    />
  );
}

export default function SessionDetailPage() {
  const { id: idParam } = useParams<{ id: string }>();
  // Non-numeric or out-of-int4 ids can't exist — skip the fetch entirely.
  const parsed = /^\d{1,10}$/.test(idParam) ? Number(idParam) : null;
  const sessionId =
    parsed !== null && parsed > 0 && parsed <= PG_INT_MAX ? parsed : null;

  // Cache under the NORMALIZED id, not the raw route param: a non-canonical
  // URL like /sessions/0123 must share the ["session", "123"] entry that the
  // list page's rename/delete invalidations target — keying on idParam would
  // leave that copy stale for the tab's lifetime.
  const detail = useQuery({
    enabled: sessionId !== null,
    queryKey: ["session", String(sessionId)],
    queryFn: () => api.get<SessionDetailOut>(`/api/sessions/${sessionId}`),
  });

  // Live-follow (AC 3) — the literal port of the legacy history browser's
  // "debounced refresh on each respuesta event": when the live store's
  // session IS this one, any captured-row signal (responsesTotal / ccNew)
  // triggers a REST refetch; react-query dedupes in-flight fetches and the
  // bot's pace (≥ send interval) is the natural debounce. Navigating to
  // another session changes `idParam` ⇒ the guard stops matching — the
  // follow stops BY CONSTRUCTION. The auto-scroll pinning lives in
  // PanelList (reused), so "stays pinned" comes free.
  const live = useLiveBatch();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (sessionId !== null && live.sessionId === sessionId) {
      queryClient.invalidateQueries({
        queryKey: ["session", String(sessionId)],
      });
    }
  }, [live.responsesTotal, live.ccNew, live.sessionId, sessionId, queryClient]);

  // Continuar (Story 3.4) from the detail header (EXPERIENCE Flow 2). NO
  // local seed — the WS `session.active` rebinds Envío; on refetch
  // `is_active` flips the badge to "En curso" and, since the store's
  // sessionId now IS this id, the live-follow effect above starts following
  // the continued session by construction. (Mutation duplicated in
  // sessions/page.tsx — App Router pages cannot export helpers; accepted
  // 3.3 precedent.)
  const [continueError, setContinueError] = useState<string | null>(null);
  const continuar = useMutation({
    mutationFn: () =>
      api.post<SessionOut>(`/api/sessions/${sessionId}/continue`),
    onSuccess: () => {
      setContinueError(null);
      // The session that WAS active changed badge too — its cached detail
      // would go stale; the prefix invalidation covers both details
      // (mirrors sessions/page.tsx).
      queryClient.invalidateQueries({ queryKey: ["session"] });
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (err) => {
      // batch_live carries the AC 3 copy verbatim — rendered as-is;
      // session_not_found (deleted in another tab) refetches into NotFound.
      if (err instanceof ApiError && err.code === "session_not_found") {
        setContinueError(null);
        queryClient.invalidateQueries({
          queryKey: ["session", String(sessionId)],
        });
        queryClient.invalidateQueries({ queryKey: ["sessions"] });

        return;
      }
      setContinueError(
        err instanceof ApiError
          ? err.message
          : "No pudimos conectar. Intenta de nuevo.",
      );
    },
  });

  if (sessionId === null) return <NotFound />;

  if (detail.isLoading) {
    return (
      <div className="grid gap-6 lg:grid-cols-2">
        <PanelSkeleton rows={8} />
        <PanelSkeleton className="hidden lg:flex" rows={8} />
      </div>
    );
  }

  if (detail.isError || !detail.data) {
    if (
      detail.error instanceof ApiError &&
      detail.error.code === "session_not_found"
    ) {
      return <NotFound />;
    }

    return (
      <div className="flex flex-col gap-3">
        <Alert status="danger">
          No pudimos cargar la sesión. Recarga la página.
        </Alert>
        <Link
          className="self-start text-sm text-accent underline"
          href="/sessions"
        >
          Volver a Historial
        </Link>
      </div>
    );
  }

  const data = detail.data;
  // Export `↓ .txt` (Story 3.5) — same paths as Envío, built on `data.id`.
  const exportBase = `/api/sessions/${data.id}/export`;
  const exportCompleta = `${exportBase}?view=completa`;
  const exportFiltrada = `${exportBase}?view=filtrada`;
  // REST rows → the 3.2 panel shapes: snapshot-style keys (`s-${id}`),
  // `nueva: false` everywhere — the "nueva" highlight belongs to Envío's
  // live landing; the detail is a read surface.
  const responses: ResponseRow[] = data.responses.map((row) => ({
    key: `s-${row.id}`,
    messageId: row.message_id,
    status: row.status,
    text: row.text,
    capturedAt: row.created_at,
    nueva: false,
  }));
  const cc: CcRow[] = data.cc.map((row) => ({
    key: `s-${row.id}`,
    text: row.text,
    nueva: false,
  }));

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <PageHeader
          actions={
            <>
              {/* Only on "Cerrada" (AC 1) — not destructive: secondary, no
                  confirm. */}
              {!data.is_active && (
                <Button
                  isDisabled={continuar.isPending}
                  size="sm"
                  variant="secondary"
                  onPress={() => continuar.mutate()}
                >
                  {continuar.isPending ? "Continuando…" : "Continuar"}
                </Button>
              )}
              <StatePill tone={data.is_active ? "accent" : "muted"}>
                {data.is_active ? "En curso" : "Cerrada"}
              </StatePill>
            </>
          }
          back={{ href: "/sessions", label: "Historial" }}
          title={data.name ?? fallbackName(data.created_at)}
        />
        {/* Gate chip + creation date; the internal id is debug data and no
            longer shown (ui-polish-spec §3.8). */}
        <div className="flex items-center gap-2">
          <MonoChip>{data.gate_value}</MonoChip>
          <span className="font-mono text-[11px] text-muted">
            {fallbackName(data.created_at)}
          </span>
        </div>
      </div>

      {continueError && <Alert status="danger">{continueError}</Alert>}

      {/* Desktop: the same two side-by-side panels as Envío; internal
          scroll — the detail competes with no cockpit. */}
      <div className="lg:grid lg:grid-cols-2 lg:gap-6">
        <CompletaPanel
          className="hidden lg:flex"
          exportPath={exportCompleta}
          listClassName="lg:max-h-[calc(100vh-12rem)]"
          responses={responses}
          total={data.responses_total}
        />
        <FiltradaPanel
          cc={cc}
          className="hidden lg:flex"
          exportPath={exportFiltrada}
          listClassName="lg:max-h-[calc(100vh-12rem)]"
          total={data.cc_total}
        />
      </div>

      {/* Mobile: the same segmented Completa | Filtrada tabs. */}
      <ResponseTabs
        cc={cc}
        ccTotal={data.cc_total}
        className="lg:hidden"
        exportPathCompleta={exportCompleta}
        exportPathFiltrada={exportFiltrada}
        responses={responses}
        responsesTotal={data.responses_total}
      />
    </div>
  );
}
