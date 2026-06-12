"use client";

// Cross-tenant support view (Story 3.6, AC 1 + 4): the target client's
// sessions list + detail, READ-ONLY, reusing the SAME dual Completa/Filtrada
// panels the client sees (props-driven on purpose — 3.2 design). REST only,
// by recorded decision: the WS broadcaster is tenant-scoped to the ACTOR, so
// an admin socket never carries the target tenant's events — this surface is
// a photo on load/select; re-selecting refreshes. No exportPath is passed ⇒
// the panels render no footer (zero dead buttons by construction; export is
// tenant-scoped to the owner of the data and not mounted on admin surfaces —
// 3.5 boundary). Detail is LOCAL selection (no sub-route): UX names only
// /admin/tenants/[id].
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Spinner, Table } from "@heroui/react";
import clsx from "clsx";

import { api, ApiError } from "@/lib/api";
import { type CcRow, type ResponseRow } from "@/lib/ws";
import {
  CompletaPanel,
  FiltradaPanel,
  ResponseTabs,
} from "@/components/sessions/response-views";

// Local mirrors of the backend schemas (snake_case end-to-end) — explicit
// interfaces per the admin/users idiom; shapes copied from the client
// sessions pages (the support view serves them VERBATIM).
interface SessionOut {
  id: number;
  name: string | null;
  gate_value: string;
  gate_name: string;
  is_active: boolean;
  created_at: string;
}

interface SupportSessionsResponse {
  tenant_id: number;
  email: string;
  items: SessionOut[];
  total: number;
}

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

interface SessionDetailOut extends SessionOut {
  responses: SessionResponseRow[];
  cc: SessionCcRow[];
  responses_total: number;
  cc_total: number;
}

// ids are int4 server-side — anything beyond can't exist (same guard as the
// backend's _PG_INT_MAX): render the not-found state without a round trip.
const PG_INT_MAX = 2147483647;

// Mirror of the client session pages' fallback (legacy `nombre_bonito`):
// local "YYYY-MM-DD HH:MM", padStart idiom, no locale. (Duplicated — App
// Router pages don't export helpers; accepted 3.3 precedent.)
function fallbackName(iso: string): string {
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");

  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    ` ${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

function SessionBadge({ isActive }: { isActive: boolean }) {
  return (
    <span
      className={clsx(
        "shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.12em]",
        isActive
          ? "bg-accent/22 text-accent"
          : "bg-surface-tertiary text-muted",
      )}
    >
      {isActive ? "En curso" : "Cerrada"}
    </span>
  );
}

// Unknown / non-client / bad tenant id — never a dead-end (UX-DR16).
function TenantNotFound() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
      <p className="text-muted">Ese cliente no existe.</p>
      <Link className="text-accent underline" href="/admin/users">
        ← Usuarios
      </Link>
    </div>
  );
}

export default function AdminTenantSessionsPage() {
  const { id: idParam } = useParams<{ id: string }>();
  // Non-numeric or out-of-int4 ids can't exist — skip the fetch entirely.
  const parsed = /^\d{1,10}$/.test(idParam) ? Number(idParam) : null;
  const tenantId =
    parsed !== null && parsed > 0 && parsed <= PG_INT_MAX ? parsed : null;

  // Detail by LOCAL selection — no sub-route (UX names only
  // /admin/tenants/[id]; list → detail is page state, like the Historial's
  // desktop list + detail).
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const queryClient = useQueryClient();

  // staleTime: 0 overrides the app-wide 30s default — without it, returning
  // to this page or re-selecting a session within 30s would serve the cached
  // photo with NO refetch, breaking the recorded decision ("re-seleccionar
  // refresca") with nothing on a no-WS surface to signal staleness.
  const list = useQuery({
    enabled: tenantId !== null,
    queryKey: ["admin-tenant-sessions", String(tenantId)],
    queryFn: () =>
      api.get<SupportSessionsResponse>(
        `/api/admin/tenants/${tenantId}/sessions`,
      ),
    staleTime: 0,
  });

  const detail = useQuery({
    enabled: tenantId !== null && selectedId !== null,
    queryKey: ["admin-tenant-session", String(tenantId), String(selectedId)],
    queryFn: () =>
      api.get<SessionDetailOut>(
        `/api/admin/tenants/${tenantId}/sessions/${selectedId}`,
      ),
    staleTime: 0,
  });

  // The session vanished between list and click (the client deleted it):
  // back to the list and refresh it — never a dead detail pane.
  useEffect(() => {
    if (
      selectedId !== null &&
      detail.error instanceof ApiError &&
      detail.error.code === "session_not_found"
    ) {
      setSelectedId(null);
      queryClient.invalidateQueries({
        queryKey: ["admin-tenant-sessions", String(tenantId)],
      });
    }
  }, [detail.error, selectedId, tenantId, queryClient]);

  if (tenantId === null) return <TenantNotFound />;

  if (list.isLoading) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  if (list.isError || !list.data) {
    if (
      list.error instanceof ApiError &&
      list.error.code === "tenant_not_found"
    ) {
      return <TenantNotFound />;
    }

    return (
      <main className="mx-auto w-full max-w-4xl px-6 py-10">
        <Alert status="danger">
          No pudimos cargar las sesiones. Recarga la página.
        </Alert>
      </main>
    );
  }

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-6 px-6 py-10">
      <header className="flex items-baseline gap-4">
        <Link
          className="shrink-0 text-sm text-default-500 underline"
          href="/admin/users"
        >
          ← Usuarios
        </Link>
        <h1 className="truncate text-2xl font-semibold">
          Sesiones de {list.data.email}
        </h1>
      </header>

      {selectedId === null ? (
        <Table>
          <Table.Content aria-label="Sesiones del cliente">
            <Table.Header>
              <Table.Column isRowHeader>Nombre</Table.Column>
              <Table.Column>Gate</Table.Column>
              <Table.Column>Estado</Table.Column>
              <Table.Column>Acciones</Table.Column>
            </Table.Header>
            <Table.Body
              items={list.data.items}
              renderEmptyState={() => "Este cliente no tiene sesiones."}
            >
              {(s) => (
                <Table.Row id={s.id}>
                  <Table.Cell>
                    {s.name ?? fallbackName(s.created_at)}
                  </Table.Cell>
                  <Table.Cell>
                    <span className="font-mono">{s.gate_value}</span>
                  </Table.Cell>
                  <Table.Cell>
                    <SessionBadge isActive={s.is_active} />
                  </Table.Cell>
                  <Table.Cell>
                    <Button
                      size="sm"
                      variant="secondary"
                      onPress={() => setSelectedId(s.id)}
                    >
                      Ver
                    </Button>
                  </Table.Cell>
                </Table.Row>
              )}
            </Table.Body>
          </Table.Content>
        </Table>
      ) : detail.isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : detail.isError || !detail.data ? (
        // session_not_found bounces back to the list via the effect above;
        // anything else surfaces here with a way back.
        <div className="flex flex-col gap-3">
          <Alert status="danger">
            No pudimos cargar la sesión. Recarga la página.
          </Alert>
          <Button
            className="self-start"
            size="sm"
            variant="secondary"
            onPress={() => setSelectedId(null)}
          >
            ← Sesiones
          </Button>
        </div>
      ) : (
        <SessionDetail data={detail.data} onBack={() => setSelectedId(null)} />
      )}
    </main>
  );
}

// The read-only detail: SAME dual panels as the client's own view (AC 1 —
// "their own data view"), fed by REST rows; no export footer, no actions.
function SessionDetail({
  data,
  onBack,
}: {
  data: SessionDetailOut;
  onBack: () => void;
}) {
  // REST rows → the 3.2 panel shapes: snapshot-style keys (`s-${id}`),
  // `nueva: false` everywhere — the "nueva" highlight belongs to Envío's
  // live landing; support is a read surface (same criterion as 3.3).
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
    <div className="flex flex-col gap-4">
      <header className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold">
            {data.name ?? fallbackName(data.created_at)}
          </h2>
          <p className="truncate font-mono text-[11px] text-muted">
            {data.gate_value} · {data.id}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <Button size="sm" variant="secondary" onPress={onBack}>
            ← Sesiones
          </Button>
          <SessionBadge isActive={data.is_active} />
        </div>
      </header>

      {/* Desktop: the same two side-by-side panels; NO exportPath ⇒ no
          footer renders (read-only by construction). */}
      <div className="lg:grid lg:grid-cols-2 lg:gap-6">
        <CompletaPanel
          className="hidden lg:flex"
          listClassName="lg:max-h-[calc(100vh-16rem)]"
          responses={responses}
          total={data.responses_total}
        />
        <FiltradaPanel
          cc={cc}
          className="hidden lg:flex"
          listClassName="lg:max-h-[calc(100vh-16rem)]"
          total={data.cc_total}
        />
      </div>

      {/* Mobile: the same segmented Completa | Filtrada tabs. */}
      <ResponseTabs
        cc={cc}
        ccTotal={data.cc_total}
        className="lg:hidden"
        responses={responses}
        responsesTotal={data.responses_total}
      />
    </div>
  );
}
