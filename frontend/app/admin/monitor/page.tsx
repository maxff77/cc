"use client";

// Owner monitoring panel: a live read-only dashboard over GET /api/observability
// (owner-only — cross-tenant volumes). Polls every 5s so it doubles as a wall
// display. Cards: Telegram connection, watchdog latch, concurrency-vs-cap, ritmo
// /alerts; then a per-tenant activity table (live since restart + durable
// today/24h from send_log). Owner-only nav/route is enforced by middleware, so
// gatesVisible is always on here.
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { AdminShell } from "@/components/ui/admin-shell";
import { SectionCard } from "@/components/ui/section-card";
import { StatePill, type PillTone } from "@/components/ui/state-pill";
import { PanelSkeleton } from "@/components/ui/panel-skeleton";
import { Notice } from "@/components/ui/notice";
import { EmptyState } from "@/components/ui/empty-state";

interface TenantActivity {
  tenant_id: number;
  name: string;
  email: string | null;
  sent_live: number;
  sent_today: number;
  sent_24h: number;
}

interface Observability {
  tenants: TenantActivity[];
  sent_total: number;
  sent_today_total: number;
  sent_24h_total: number;
  telegram: { authorized: boolean; ready: boolean; targets_resolved: number };
  flood: {
    events_total: number;
    governor_raises: number;
    g_min: number;
    events_in_window: number;
    alert_active: boolean;
  };
  unmatched: { total: number; events_in_window: number; alert_active: boolean };
  watchdog: {
    paused: boolean;
    reason: string | null;
    detail: string | null;
    paused_at: string | null;
  };
  admission: { max_active_senders: number; admitted: number; waiting: number };
}

const OBSERVABILITY_KEY = ["admin-observability"] as const;

// One big-number cell — label over a tabular figure, with an optional pill.
function Metric({
  label,
  value,
  pill,
}: {
  label: string;
  value: React.ReactNode;
  pill?: { tone: PillTone; text: string };
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs uppercase tracking-[0.08em] text-muted">
        {label}
      </span>
      <span className="text-2xl font-semibold tabular-nums">{value}</span>
      {pill && <StatePill tone={pill.tone}>{pill.text}</StatePill>}
    </div>
  );
}

export default function AdminMonitorPage() {
  const obs = useQuery({
    queryKey: OBSERVABILITY_KEY,
    queryFn: () => api.get<Observability>("/api/observability"),
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
  });

  const d = obs.data;

  // Telegram connection tone: operative > authed-but-no-targets > disconnected.
  const tg = d?.telegram;
  const tgPill: { tone: PillTone; text: string } = !tg
    ? { tone: "muted", text: "—" }
    : tg.ready
      ? { tone: "success", text: "Operativo" }
      : tg.authorized
        ? { tone: "warning", text: "Sin destinos" }
        : { tone: "danger", text: "Desconectado" };

  // Concurrency: at/over cap → warning; cap 0 → no limit.
  const adm = d?.admission;
  const atCap =
    !!adm &&
    adm.max_active_senders > 0 &&
    adm.admitted >= adm.max_active_senders;
  const concPill: { tone: PillTone; text: string } = !adm
    ? { tone: "muted", text: "—" }
    : adm.max_active_senders === 0
      ? { tone: "muted", text: "Sin límite" }
      : atCap
        ? { tone: "warning", text: "Al tope" }
        : { tone: "success", text: "Con margen" };

  const alerting = !!d && (d.flood.alert_active || d.unmatched.alert_active);

  return (
    <AdminShell gatesVisible title="Monitoreo">
      {obs.isLoading && <PanelSkeleton rows={6} />}

      {obs.isError && (
        <Notice status="danger">
          No pudimos cargar el estado del sistema. Reintentando…
        </Notice>
      )}

      {d && (
        <div className="flex flex-col gap-6">
          {/* Status cards */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <SectionCard legend="TELEGRAM">
              <Metric
                label="Conexión"
                pill={tgPill}
                value={`${tg?.targets_resolved ?? 0} destino${
                  (tg?.targets_resolved ?? 0) === 1 ? "" : "s"
                }`}
              />
            </SectionCard>

            <SectionCard
              legend="WATCHDOG"
              rail={d.watchdog.paused ? "warning" : "none"}
            >
              <Metric
                label="Estado del bot"
                pill={
                  d.watchdog.paused
                    ? { tone: "danger", text: d.watchdog.reason ?? "Pausado" }
                    : { tone: "success", text: "En marcha" }
                }
                value={d.watchdog.paused ? "Pausado" : "Activo"}
              />
              {d.watchdog.paused && d.watchdog.detail && (
                <p className="mt-2 text-xs leading-relaxed text-muted">
                  {d.watchdog.detail}
                </p>
              )}
            </SectionCard>

            <SectionCard legend="CONCURRENCIA">
              <Metric
                label="Enviando ahora"
                pill={concPill}
                value={
                  adm
                    ? `${adm.admitted}${
                        adm.max_active_senders > 0
                          ? ` / ${adm.max_active_senders}`
                          : ""
                      }`
                    : "—"
                }
              />
              {!!adm && adm.waiting > 0 && (
                <p className="mt-2 text-xs text-muted">
                  {adm.waiting} en cola de espera
                </p>
              )}
            </SectionCard>

            <SectionCard
              legend="RITMO / ALERTAS"
              rail={alerting ? "warning" : "none"}
            >
              <Metric
                label="Intervalo actual"
                pill={
                  alerting
                    ? { tone: "warning", text: "Alerta activa" }
                    : { tone: "success", text: "Estable" }
                }
                value={`${d.flood.g_min.toFixed(1)} s`}
              />
              <p className="mt-2 text-xs text-muted">
                FloodWaits: {d.flood.events_in_window} (ventana) ·{" "}
                {d.flood.events_total} total · Sin atribuir:{" "}
                {d.unmatched.events_in_window}
              </p>
            </SectionCard>
          </div>

          {/* Per-tenant activity */}
          <SectionCard legend="ACTIVIDAD POR CLIENTE">
            <p className="mb-3 text-xs text-muted">
              En vivo {d.sent_total} (desde el último reinicio) · Hoy{" "}
              {d.sent_today_total} · 24 h {d.sent_24h_total}
            </p>
            {d.tenants.length === 0 ? (
              <EmptyState
                eyebrow="Sin actividad"
                message="Nadie ha enviado en las últimas 24 horas."
              />
            ) : (
              <div className="overflow-x-auto rx-scroll">
                <table className="w-full min-w-[28rem] border-collapse text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-[0.06em] text-muted">
                      <th className="py-2 pr-4 font-medium">Cliente</th>
                      <th className="py-2 pr-4 text-right font-medium">
                        En vivo
                      </th>
                      <th className="py-2 pr-4 text-right font-medium">Hoy</th>
                      <th className="py-2 text-right font-medium">24 h</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.tenants.map((t) => (
                      <tr
                        key={t.tenant_id}
                        className="border-t border-border align-top"
                      >
                        <td className="py-2 pr-4">
                          <div className="flex min-w-0 flex-col">
                            <span className="truncate font-medium text-foreground">
                              {t.name}
                            </span>
                            {t.email && (
                              <span className="truncate text-xs text-muted">
                                {t.email}
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums">
                          {t.sent_live}
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums">
                          {t.sent_today}
                        </td>
                        <td className="py-2 text-right tabular-nums font-semibold">
                          {t.sent_24h}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </SectionCard>
        </div>
      )}
    </AdminShell>
  );
}
