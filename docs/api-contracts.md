# API Contracts — Ranger-X Check

> Generated: 2026-06-20. Source: `backend/app/api/*.py` + `backend/app/main.py`. All routers mount under `/api`. Live OpenAPI at `/openapi.json` (the frontend regenerates `types/api.ts` from it via `npm run generate:api`).

## Conventions

- **Auth:** an HttpOnly opaque session cookie (`cc_session`). `deps.get_current_user` validates it and applies gates in order: blocked (403) → plan-expired (`plan_expired`) → must-change-password (`password_change_required`). `require_role` gates admin/owner endpoints.
- 🔒 **`tenant_id` always comes from the session**, never from body/path. Unknown/foreign/oversized ids all 404 identically (no existence leak).
- **Errors:** `{code, message}` JSON — `code` machine-readable snake_case, `message` Spanish user copy. Validation/identity gates use specific codes the frontend maps (`plan_expired`, `password_change_required`, `session_conflict`, `category_in_use`, `plan_in_use`, …).
- **Commands are REST. Live state is the WebSocket** (`/ws`, server→client only).
- Status codes: `201` create, `204` no-content mutation, `503` when the Telegram gateway is unauthorized.

---

## Public — `/api/public` (no auth)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/public/gates` | Landing gate catalog (name/category/`display_value` only — never the real `value`). |
| GET | `/api/public/plans` | Landing pricing (active plans). |
| GET | `/api/health` | Liveness (smoke-tested by the deploy workflow). |

## Auth — `/api/auth` (throttled per email+IP)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/register` | Self-registration → `LoginResponse` (201). |
| POST | `/api/auth/login` | Login → sets session cookie, `LoginResponse`. |
| POST | `/api/auth/logout` | Revoke session (204). |
| GET | `/api/auth/me` | Current identity (role, plan, flags). |
| POST | `/api/auth/change-password` | Change password (clears `must_change_password`). |

## Gates — `/api/gates` (authenticated client read)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/gates` | Client-visible gate catalog (name/category/`display_value`/cookie-mode flag; no real `value`). |

## Batches — `/api/batches`

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/batches` | Create a batch, or **append** lines to the tenant's live batch (201). Resolves gate from the catalog; charges credits / admission per plan. |
| POST | `/api/batches/{id}/pause` | Pause (204). |
| POST | `/api/batches/{id}/resume` | Resume (204). |
| POST | `/api/batches/{id}/stop` | Stop and drain the queue (204). |

## Cookies — `/api/cookies` (tenant-scoped, cookie-mode gates)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/cookies` | Store one per-account cookie for a cookie-mode gate (201). Value never echoed back. |
| GET | `/api/cookies` | List the tenant's cookies (masked values). |
| DELETE | `/api/cookies/{id}` | Delete one cookie (204). |

## Sessions (cockpit Completa / Aprobadas / Datos-CC) — `/api/sessions`

The cockpit now runs on ONE perpetual capture session per tenant (never rotated/renamed/continued/closed). Only three routes remain:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/sessions/clear` | **Limpiar** — non-destructive view-cutoff that clears all 3 cockpit panels at once. Resolves the perpetual session `FOR UPDATE`, stamps `cleared_response_id = MAX(responses.id)`, commits, re-emits `session.active`. Returns `{"cleared_response_id": <int\|null>}`. **Deletes 0 rows** (display-only filter). 404 identically if the tenant has no session. |
| GET | `/api/sessions/export?view=completa\|filtrada\|filtrada_completa` | Cockpit `.txt` export over the perpetual session, **cutoff-respecting** (only the live post-Limpiar view). `filtrada_completa` = "Aprobadas" (full text of only the ✅ revisions). `Cache-Control: no-store`; empty ⇒ 200 empty body; 404 if no session. |
| GET | `/api/sessions/{id}/export?view=completa\|filtrada\|filtrada_completa` | Admin/PR-2 per-session export, **cutoff-agnostic** (full history). Unknown/foreign/oversized id 404s identically. |

> The old client list/detail/new/rename/continue/clear-declined/delete endpoints (`GET /api/sessions`, `GET /api/sessions/{id}`, `POST /api/sessions/new`, `PATCH /api/sessions/{id}`, `POST /api/sessions/{id}/continue`, `POST /api/sessions/{id}/clear-declined`, `DELETE /api/sessions/{id}`) were **removed** in the sessionless redesign.

## History (Historial) — `/api/history`

Client-owned history of approved (✅) captures, grouped by gate. **Cutoff-agnostic** (reads `responses` directly, ignores the Limpiar cutoff). `gate_value` is NEVER serialized — only the client-visible `gate_name`/`gate_display_value` snapshot.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/history` | Approved-✅ messages (latest `kind='full'` revision `status='ok'`) grouped by gate → `{gates: [{name, display_value, count, items: [{id, text, captured_at, cc: []}]}]}`. Gates ordered by most-recent activity; items newest-first; null-gate → trailing `{name:null, display_value:"Sin gate"}` group. Empty ⇒ `{gates: []}`. |
| DELETE | `/api/history/response/{id}` | **DESTRUCTIVE** — delete one message's full revisions + cc rows → `{deleted:<int>}`. 404 identical for foreign/unknown/out-of-int4 id. |
| DELETE | `/api/history/gate?name=<gate_name>` | **DESTRUCTIVE** — delete one gate's history → `{deleted:<int>}`. Unknown name ⇒ `{deleted:0}`. |
| DELETE | `/api/history` | **DESTRUCTIVE** — delete the tenant's entire history → `{deleted:<int>}`. |

> These are the ONLY client path that hard-deletes captured data. They remove ONLY `responses` rows (children) — never `batches`/`send_log`/`batch_lines`, so attribution/integrity history stays intact. `tenant_id` only from the session; the endpoint owns the transaction.

## Gift keys (client) — `/api/keys`

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/keys/claim` | Claim a single-use key → adds days (`ClaimKeyResult`). |

## Watchdog — `/api/watchdog` (owner)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/watchdog` | Global pause status. |
| POST | `/api/watchdog/resume` | Owner resume of the latched pause (204). |

## Observability — `/api/observability` (owner)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/observability` | Owner dashboard snapshot (system health, senders, guardrail). |

---

## Admin — `/api/admin` (admin/owner)

### Users
`GET /api/admin/users` · `POST /api/admin/users` (201) · `DELETE /api/admin/users/{id}` (204) · `POST /api/admin/users/{id}/renew` · `.../block` · `.../unblock` · `.../contact` · `.../credits` · `.../reset-password`.

### Gates & categories
`GET|POST /api/admin/gates` · `PATCH|DELETE /api/admin/gates/{id}` (admin gates view shows the real `value`) · `GET|POST /api/admin/gate-categories` · `PATCH|DELETE /api/admin/gate-categories/{id}`.

### Plans
`GET /api/admin/plans` · `GET /api/admin/plans/active` · `POST /api/admin/plans` (201) · `PATCH|DELETE /api/admin/plans/{id}` · `POST /api/admin/plans/{id}/default` (flag the gift-key default tier).

### Gift keys (admin) — `/api/admin/keys`
`POST /api/admin/keys` (mint, 201) · `GET /api/admin/keys` (list = audit trail) · `POST /api/admin/keys/{id}/revoke` (204).

### Send targets (destinos) — `/api/admin/targets`
`GET /api/admin/targets` · `GET /api/admin/targets/discover` (resolvable chats from the live gateway) · `POST /api/admin/targets` (201) · `PATCH|DELETE /api/admin/targets/{id}`.

### System knobs
`GET|PUT /api/admin/admission` (the `max_active_senders` cap) · `GET|PUT /api/admin/interval` (the constant scheduler interval) · cross-tenant **audited** support views (`GET /api/admin/...` under `tenants/[id]` — every read writes an `audit_log` row).

---

## WebSocket — `/ws`

**Server→client ONLY.** Clients send only keep-alives; every command goes through REST. Envelope: `{event, data}`. A newly connected tab receives a full `snapshot` first.

| Event | When |
|---|---|
| `snapshot` | First frame — full tenant state. |
| `batch.state` | Batch state transition (incl. `pause_reason` for cookie-mode pauses). |
| `batch.progress` | Per-line progress / counters. |
| `response.captured` | A ✅/❌ reply (or new CC datum) was attributed and stored. |
| `session.active` | Perpetual session refreshed (gate snapshot, or a Limpiar view-cutoff). |
| `flood.wait` | A Telegram FloodWait is in effect. |
| `watchdog.paused` / `watchdog.resumed` | Global watchdog latch toggled. |
| `guardrail.alert` | Observe-only ban-guardrail alert (FloodWaits, unmatched replies). |
| `credits.updated` | Tenant credit balance changed. |

Fan-out is tenant-scoped (`broadcaster.emit` per tenant; `emit_global` for system events like watchdog). State lives in the backend/DB, so it survives reconnects.
