---
title: 'Owner monitoring panel (/admin/monitor)'
type: 'feature'
created: '2026-06-23'
status: 'done'
baseline_commit: '7d02e1b753a053d9cfdca1f6221eaf4def422e17'
context: ['{project-root}/_bmad-output/project-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Real users are live on v1, but the owner has no UI to watch the deployment. The backend already exposes `GET /api/observability` (who is sending, concurrency cap/queue, watchdog, FloodWaits) but **nothing in the frontend consumes it**, the per-tenant send counts are process-memory only (reset on every deploy), the tenant ids are raw numbers with no human label, and "estado del bot" only covers the watchdog latch — not the live Telegram connection.

**Approach:** Extend the existing owner-only `/api/observability` endpoint with (a) durable per-tenant send counts from `send_log` (since-midnight + rolling-24h) alongside the live process counter, (b) human labels (tenant name + a representative client email), and (c) a live Telegram-connection slice (authorized / ready / resolved-target count). Build a new owner-only page `/admin/monitor` that polls the endpoint every ~5s and renders: bot status, concurrency-vs-cap, FloodWait/unmatched alerts, and a per-tenant activity table.

## Boundaries & Constraints

**Always:**
- Owner-only (reuse the endpoint's `require_role("owner")`) — payload exposes cross-tenant volumes. Strictly READ (singletons + COUNT, never writes).
- SQL lives in repos (`db/repos/*`), not the router (router orchestrates, calls `*_repo`).
- Durable counts mirror live-counter semantics: count `send_log` rows with `message_id IS NOT NULL` (confirmed deliveries), grouped by `tenant_id`.
- Frontend reuses the custom UI kit (`AdminShell`, `SectionCard`, `StatePill`, `PanelSkeleton`, `Notice`) — NOT raw HeroUI. Spanish copy, dark theme.

**Ask First:**
- Any schema change / new DB index / migration (not expected — `send_log.created_at`/`tenant_id` exist; a COUNT seq-scan is fine at current volume — surface it if slow, don't index silently).
- Switching polling → WebSocket push.

**Never:**
- Touch the legacy single-tenant app or `respuestas/`; change the live counter (`send_worker._sent_by_tenant`) semantics; add any npm/pip dependency (stdlib `zoneinfo` for the tz boundary).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Owner loads panel | authed owner GET `/api/observability` | 200 with telegram slice, per-tenant activity rows (live + today + 24h), totals, flood/unmatched/watchdog/admission | N/A |
| Non-owner | admin/client hits endpoint or `/admin/monitor` | 403 `forbidden` (endpoint); middleware redirects `/admin/monitor` → `/admin/users` | gated |
| Telegram unauthorized | gateway not authed | `telegram.authorized=false`, `ready=false`, `targets_resolved=0`; UI shows red "Desconectado" | N/A |
| Tenant sent before last restart only | live counter empty, DB has rows | row appears, `sent_live=0`, today/24h from DB | N/A |
| No sends anywhere | empty counter + no DB rows in windows | `tenants=[]`, totals 0; UI empty-state | N/A |
| Cap disabled | `max_active_senders=0` | concurrency card shows "Sin límite", no "al tope" warning | N/A |
| At capacity | `admitted >= cap > 0` | concurrency StatePill `warning` "Al tope"; waiting>0 shown | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/api/observability.py` -- the owner-only endpoint to extend (inline pydantic schemas; currently returns `sent_by_tenant`/flood/unmatched/watchdog/admission).
- `backend/app/db/repos/send_log.py` -- add the durable per-tenant windowed COUNT (model after `sent_count_for_session`, line ~206; uses `SendLog.created_at`, `tenant_id`, `message_id`).
- `backend/app/db/repos/tenants.py` -- add tenant-label lookup (tenant `name` + representative `client` user email).
- `backend/app/core/telegram.py` -- read-only source: `gateway.authorized`, `gateway.ready` (property), `gateway.resolved_ids()` (set). Other live sources (send_worker/scheduler/admission/batches_repo) already wired, unchanged.
- `backend/tests/test_observability.py` -- existing test to extend.
- `frontend/app/admin/monitor/page.tsx` -- NEW owner page (polling `useQuery`).
- `frontend/components/ui/admin-shell.tsx` -- add `{ href: "/admin/monitor", label: "Monitoreo", ownerOnly: true }` to `ITEMS`.
- `frontend/middleware.ts` -- add owner-gate for `/admin/monitor` (mirror the `/admin/plans` block).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/repos/send_log.py` -- add `async def sent_counts_by_window(session, *, day_ago, today_start) -> dict[int, tuple[int, int]]` returning `{tenant_id: (sent_today, sent_24h)}`; single grouped `select(SendLog.tenant_id, count filter created_at>=today_start, count filter created_at>=day_ago).where(message_id is not null).group_by(tenant_id)`.
- [x] `backend/app/db/repos/tenants.py` -- add `async def labels(session, ids: list[int]) -> dict[int, tuple[str, str | None]]` → `{tenant_id: (name, email)}`; email = a user with `role='client'` for that tenant (any; `None` if none).
- [x] `backend/app/api/observability.py` -- compute `today_start` (America/Mexico_City midnight via stdlib `zoneinfo`) and `day_ago` (now−24h); call the two new repos; merge with `send_worker.sent_by_tenant()`; replace `sent_by_tenant` field with `tenants: list[TenantActivity]` (`tenant_id, name, email, sent_live, sent_today, sent_24h`, sorted by `sent_24h` desc then `sent_live` desc); add `sent_today_total`/`sent_24h_total`; add `telegram: TelegramSlice(authorized, ready, targets_resolved=len(gateway.resolved_ids()))`. Keep flood/unmatched/watchdog/admission unchanged.
- [x] `frontend/app/admin/monitor/page.tsx` -- new `"use client"` page wrapped in `<AdminShell gatesVisible title="Monitoreo">`; `useQuery({queryKey:["admin-observability"], queryFn:()=>api.get<...>("/api/observability"), refetchInterval:5000, refetchIntervalInBackground:true})`; local interface mirroring the payload; status cards (Telegram, Watchdog, Concurrencia, Flood/Unmatched) via `SectionCard`+`StatePill` with threshold tones; per-tenant table (name/email · En vivo · Hoy · 24h); `PanelSkeleton` while loading, `Notice status="danger"` on error, empty-state when no tenants.
- [x] `frontend/components/ui/admin-shell.tsx` -- add the Monitoreo nav item (ownerOnly).
- [x] `frontend/middleware.ts` -- redirect `/admin/monitor` → `/admin/users` when `role !== "owner"`.
- [x] `backend/tests/test_observability.py` -- extend: owner sees `telegram` slice + per-tenant `sent_today`/`sent_24h` from seeded `send_log` rows (confirmed only); non-owner forbidden; tenant present in live counter but absent from DB window still appears.

**Acceptance Criteria:**
- Given an authed owner, when the panel loads, then it shows live Telegram connection, concurrency vs cap (with "Al tope"/queue when applicable), active alerts, and a per-tenant table with live + today + 24h send counts, refreshing every ~5s.
- Given a deploy/restart just happened, when the owner views the panel, then today/24h counts are non-zero from the DB even though live counts reset to 0.
- Given a non-owner (admin or client), when they navigate to `/admin/monitor` or call the endpoint, then they are redirected away / receive `403 forbidden` and see no cross-tenant data.

## Design Notes

- **tz boundary:** "Hoy" = since local midnight in `America/Mexico_City`, computed with stdlib `zoneinfo`, passed as bound params — avoids rolling-vs-calendar / UTC-offset ambiguity. `# ponytail: deployment tz hardcoded; move to config if it ever serves another region.`
- **Merge shape:** tenant id set = live-counter keys ∪ DB-window keys; left-join labels; omit tenants with zero activity in all windows. Confirmed-only (`message_id IS NOT NULL`) ⇒ counts mean *delivered*; `reply_purged_at` ignored (a purged line was still sent).

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest tests/test_observability.py` -- expected: pass (new assertions green).
- `cd backend && .venv/bin/ruff check app/api/observability.py app/db/repos/send_log.py app/db/repos/tenants.py && .venv/bin/mypy app/api/observability.py` -- expected: clean.
- `cd frontend && npm run build` -- expected: tsc + build pass (real gate; lint alone misses type errors).

**Manual checks:**
- Log in as owner → `/admin/monitor`: cards render, table lists tenants, numbers update ~5s. As a client: redirected to `/app` (no `/admin`). Telegram card flips to "Desconectado" red when the gateway is unauthorized.

## Suggested Review Order

**Backend payload (start here)**

- Endpoint orchestration: merges live counter + DB windows + labels + telegram slice.
  [`observability.py:105`](../../backend/app/api/observability.py#L105)

- Per-tenant merge, zero-activity skip, sort by 24h.
  [`observability.py:122`](../../backend/app/api/observability.py#L122)

- New response shape (tenants list, totals, telegram slice).
  [`observability.py:92`](../../backend/app/api/observability.py#L92)

**Durable counts (SQL, repos)**

- Two-window delivered-only COUNT grouped by tenant (survives restart).
  [`send_log.py:230`](../../backend/app/db/repos/send_log.py#L230)

- Tenant name + representative client email lookup.
  [`tenants.py:20`](../../backend/app/db/repos/tenants.py#L20)

**Frontend panel (UI)**

- 5s-polling query over the owner endpoint.
  [`page.tsx:75`](../../frontend/app/admin/monitor/page.tsx#L75)

- Threshold→tone derivations (telegram / concurrency / alerts).
  [`page.tsx:86`](../../frontend/app/admin/monitor/page.tsx#L86)

- Per-tenant activity table.
  [`page.tsx:197`](../../frontend/app/admin/monitor/page.tsx#L197)

**Access & routing**

- Owner-only route gate (mirrors /admin/plans).
  [`middleware.ts:184`](../../frontend/middleware.ts#L184)

- Owner-only nav item.
  [`admin-shell.tsx:30`](../../frontend/components/ui/admin-shell.tsx#L30)

**Tests (peripheral)**

- Telegram slice + durable today/24h assertions.
  [`test_observability.py:281`](../../backend/tests/test_observability.py#L281)

- Live-only tenant (in counter, absent from DB) still appears.
  [`test_observability.py:343`](../../backend/tests/test_observability.py#L343)
