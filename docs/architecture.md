# Architecture — Ranger-X Check

> Generated: 2026-06-22. Multi-part monorepo: `backend/` (FastAPI engine) + `frontend/` (Next.js SPA). For invariants and the legacy/production split, see [CLAUDE.md](../CLAUDE.md).

## Executive Summary

Ranger-X Check is a multi-tenant Telegram forwarder. Many tenants submit batches of lines; a single async backend sends them through **one shared Telegram user account** (Telethon/MTProto — a user account, not a bot) to a checker bot, paced and round-robined fairly across tenants. The bot's ✅/❌ replies are captured, attributed back to the originating line/tenant via the send log, and persisted as two derived views (Completa / Filtrada). A watchdog protects the shared account from bans.

```
Tenants ─┐
         ├─▶ FastAPI (backend/) ── send worker ──▶ Telethon ──▶ checker bot / CC groups
Frontend ┘        │  scheduler (round-robin + pacing)              │
   (cockpit)      │  send_log (write-ahead intent)                 │ ✅/❌ replies
   (admin)        ▼                                                ▼
            PostgreSQL  ◀── capture consumer ◀── attribution ◀── reply queue
                 ▲
                 └── WebSocket /ws (server→client live state) ──▶ Frontend
```

---

## Backend

**Path:** `backend/` · **Stack:** Python 3.12, FastAPI, async SQLAlchemy 2 (asyncpg), PostgreSQL, Telethon, Alembic, argon2-cffi, pydantic-settings.

Single async FastAPI app. `app/main.py` builds it and owns the **lifespan**: restores any persisted watchdog latch, connects the Telegram gateway (non-fatal — boots even if unauthorized; sending then 503s), runs the **account-identity guard** (latches a fail-closed pause if `anon.session` was re-authed to a *different* account while attribution data exists — see below), starts the **send worker** task, runs boot recovery (reconciles unconfirmed `message_id`s), then releases the **capture consumer**. Errors surface as `{code, message}` JSON via one `AppError` handler (`app/errors.py`) — `code` is machine-readable snake_case, `message` is Spanish user copy.

### Layers

```
backend/app/
├── main.py            # app factory + lifespan (gateway, worker, capture, recovery)
├── config.py          # pydantic-settings; loads backend/.env
├── errors.py          # AppError → {code, message} handler
├── api/               # routers (REST) + WebSocket + request identity
│   ├── deps.py        # get_current_user (session cookie → identity), require_role
│   ├── auth.py batches.py sessions.py gates.py public.py cookies.py
│   ├── admin.py targets.py keys.py watchdog.py observability.py history.py
│   ├── ws.py          # the WebSocket (server→client only)
│   └── health.py
├── core/              # the engine
│   ├── telegram.py    # SOLE owner of the Telethon client + send target; only Telethon importer
│   ├── send_worker.py # infinite async send loop (write-ahead + fail-stop)
│   ├── scheduler.py   # round-robin + constant-interval pacing + FloodWait governor
│   ├── capture.py     # single consumer draining the reply queue → persist
│   ├── attribution.py # (chat_id, reply_to_msg_id) → send_log → tenant/batch/line
│   ├── cc_extract.py  # CC: extraction (legacy extraer_cc parity)
│   ├── reconciler.py  # boot + periodic reply reconciliation (unconfirmed sends, late edits)
│   ├── watchdog.py    # latched GLOBAL pause on session loss / reply-rate collapse
│   ├── alerts.py      # observe-only ban-guardrail sliding-window alerts
│   ├── broadcaster.py # tenant-scoped WS fan-out (emit / emit_global)
│   ├── cookie_verdict.py display_transform.py redact.py  # Amazon cookie-mode helpers
│   │                  #   display_transform → LIVE/DEAD branded card; redact → strip secrets + dot dividers
├── db/                # async SQLAlchemy + repos + Alembic models
│   ├── base.py models.py
│   └── repos/         # flush-not-commit repos; FOR UPDATE on read-modify-write
├── services/          # orchestration (auth, batches, admission, plans, users,
│                      #   exports, targets, gift_keys, pacing,
│                      #   account_guard, live_forward)
├── migrations/        # Alembic (head: c4e2f7a1b903)
├── scripts/           # bootstrap_owner, seed_user, telegram_auth, load tests
└── tests/             # pytest (40+ test modules)
```

### The send/capture engine (production equivalent of the legacy `Engine`)

- **`telegram.py`** — sole owner of the Telethon client and the resolved send targets. The only module allowed to import Telethon; re-exports `FloodWaitError`/`SessionLostError`. Sends with `parse_mode=None` (load-bearing — markdown would corrupt data lines and break attribution). Round-robins over the enabled, resolvable `send_targets`.
- **`send_worker.py`** — infinite loop. Picks a tenant (`scheduler.pick_next`), claims one line, and **records intent in `send_log` in the same transaction before sending** (write-ahead). Recording the `message_id` after delivery is **retry-forever / fail-stop**: if the DB is down post-send, nothing else sends until it commits (prevents double-sends and orphaned attribution). Handles FloodWait, errors (bounded retries), pause/stop (cancelable waits), plan-expiry mid-batch, boot recovery, and the Amazon cookie-mode serialize gate (send the `.cookie`/`.amz` pair, then HOLD the tenant until the verdict arrives or times out).
- **`scheduler.py`** — **constant** send interval `G_min` (owner decision: default 4s, editable 2–30s; not the old adaptive `P(n)/n` band) + round-robin across active senders with bounded owner/admin priority. A FloodWait raises `G_min` ×1.5; it decays over idle. **Process-memory state, reset on restart.**
- **`capture.py` / `attribution.py`** — a single consumer drains an async reply queue. Attribution resolves `(chat_id, reply_to_msg_id) → send_log → tenant/batch/line`. CC data is extracted (`cc_extract.py`) and persisted. Transient DB errors retry forever (the DB-down reply buffer); non-transient errors are bounded (poison item after 5).
- **`reconciler.py`** — boot reconciliation resolves `send_log` rows whose `message_id` was never confirmed; a periodic sweep catches late ✅ edits.
- **`watchdog.py`** — latches a **global** pause on Telegram session loss or reply-rate collapse; never auto-resumes; persisted to `watchdog_state`; the owner resumes via endpoint. `alerts.py` is observe-only (never pauses).
- **`services/account_guard.py`** — boot-time account-identity fence. Compares the connected account's id against the last-seen fingerprint in `system_settings.telegram_account_id`. A plain restart reuses the same account → silent `ok`. A real account swap **with existing `send_log`/`responses`** returns `LOCKED` (the per-chat `message_id` sequences restarted → a new reply could mis-attribute to another tenant's line — a cross-tenant leak), and the lifespan latches the watchdog (`REASON_ACCOUNT_CHANGED`) until the owner wipes that state and resumes. `_classify` is a pure decision matrix (`FIRST_BOOT`/`OK`/`ADOPTED`/`LOCKED`/`SKIPPED`), tested without a DB or Telethon.
- **`services/live_forward.py`** — forwards every fresh Amazon **live** (an `Approved` cookie-mode verdict) to an owner-configured GLOBAL Telegram channel (`system_settings.live_forward_channel`, empty = disabled). Best-effort and **out-of-band**: it does NOT go through the send worker/scheduler (no pacing, no `send_log`, no attribution), forwards the redacted `clean_text` verbatim (never the LIVE/DEAD rebrand), and swallows every failure so a forward can never disrupt capture. Triggered from `capture.py` once per line's first ✅ (`has_ok_revision` guard).

### Response storage (Completa vs Filtrada)

One `responses` table, discriminated by `kind` — see [data-models.md](./data-models.md#responses).
- `kind='full'` → **Completa**: one row per captured message revision; `status` (`ok`/`rejected`) from the ✅/❌ glyph. Latest revision per `(chat_id, message_id)` is the durable per-message state.
- `kind='cc'` → **Filtrada**: extracted `CC:` data, deduplicated **tenant-lifetime** by the partial unique index `uq_responses_session_cc` (one perpetual capture-session per tenant, so the per-session dedup spans the tenant's whole history).
- **Limpiar** is a non-destructive view-cutoff: an id high-water-mark stored in `capture_sessions.cleared_response_id`, applied only to the cockpit display reads and cockpit export (`Response.id > cutoff`). It deletes nothing — integrity, attribution, reconciliation, dedup, credits, and awaiting-reply all ignore it. The separate **Historial** (PR-2) is the one DESTRUCTIVE path: it deletes `responses` rows (only `responses` — never `batches`/`send_log`/`batch_lines`).

### Roles & admission

Roles `owner` / `admin` / `client` (in `users.role`, app-enforced). `deps.py` is the only source of request identity: validates the HttpOnly session cookie and applies gates in order (blocked → plan-expired → must-change-password). **`tenant_id` always comes from the session, never from body/path.** Plans (owner-managed catalog) drive expiry, per-tenant antispam interval, line caps, and credit grants. Admission control caps concurrent active senders (`system_settings.max_active_senders`) with a FIFO waiting queue.

---

## Frontend

**Path:** `frontend/` · **Stack:** Next.js 16 (App Router), React 19, HeroUI 3, Tailwind CSS 4, TanStack Query, next-themes. Dark+light themed, Spanish copy.

`middleware.ts` gates by auth/role at the edge (no cookie → `/login`; plan expired → `/expired`; must-change-password → `/change-password`; `/admin/*` requires admin/owner).

```
frontend/
├── app/
│   ├── page.tsx                # public landing (/)
│   ├── login register expired change-password   # public auth pages
│   ├── app/                    # the cockpit ("Envío") — client surface
│   │   ├── page.tsx            #   send form + progress + live response panels
│   │   └── historial/          #   history (approved-✅ grouped by gate + destructive deletes)
│   └── admin/                  # users, gates, plans, keys, destinos, tenants/[id]
├── components/
│   ├── batch/                  # send-form, progress-ring, notices, cookie-manager
│   ├── sessions/               # response-views (Completa/Filtrada), response-row
│   ├── landing/ keys/ ui/      # landing sections, gift-key claim, design-system primitives
├── lib/
│   ├── ws.ts                   # WebSocket store (useSyncExternalStore singleton, auto-reconnect)
│   ├── api.ts                  # fetch wrapper (credentials: include, {code,message})
│   ├── query-client.ts use-persisted.ts cookies.ts
├── types/api.ts                # generated from backend OpenAPI (npm run generate:api)
└── config/ styles/ public/
```

- **Cockpit** (`app/app/page.tsx`) — **sessionless**: send form (paste + category→gate selectors), progress ring, failed/pending lines, flood/watchdog/plan notices, the cookie manager (Amazon cookie-mode gates), and the three live panels — **Completa** (one row per message — the latest revision, deduped client-side by `(chat_id, message_id)` / `Response.id`, not every edit), **Aprobadas** (only ✅ messages, full text; its count is **server-authoritative**), and **Datos CC** (extracted CC). A single non-destructive **Limpiar** button clears all three panels via the view-cutoff (it never deletes). Live state comes from the WebSocket store (`lib/ws.ts`, a reducer over `snapshot`/`batch.*`/`response.captured`/`session.active`/`flood`/`watchdog`/`credits.updated`); the gate catalog comes from a REST query. **Client-visible label is "Gateway"** (the internal table/API/`value` stay "gate").
- **History** (`app/app/historial/`) — read-only list of approved-✅ responses (latest `kind='full'` revision is `status='ok'`) grouped by the batch's client-visible gate, fed by `GET /api/history` **independently of the Limpiar cutoff** (it reads `responses` directly). Adds three DESTRUCTIVE deletes — one message (`DELETE /api/history/response/{id}`), one gate (`DELETE /api/history/gate?name=`), or all (`DELETE /api/history`) — which remove only `responses` rows.
- **Admin** (`app/admin/`) — `users` (CRUD, plan renew/block, password reset, credits, admission cap, interval), `gates` (catalog + categories), `plans`, `keys` (gift keys), `destinos` (send targets), `tenants/[id]` (audited read-only cross-tenant browser).

---

## Integration

- **REST (`/api/*`)** carries every command (create/append/pause/resume/stop a batch, the Limpiar view-cutoff (`POST /api/sessions/clear`), the Historial reads/deletes (`/api/history`), admin actions, auth). See [api-contracts.md](./api-contracts.md).
- **WebSocket (`/ws`) is server→client ONLY** — clients send only keep-alives; all commands go through REST. Envelope: `{event, data}`. Events: `snapshot` (full state, first frame), `batch.state`, `batch.progress`, `response.captured`, `session.active` (perpetual session refreshed — gate snapshot / Limpiar cutoff), `flood.wait`, `watchdog.paused|resumed`, `guardrail.alert`, `credits.updated`. Fan-out is tenant-scoped (`broadcaster.emit` per-tenant; `emit_global` for system events). State lives in the backend/DB, not the socket — it survives reconnects.
- In dev, `next.config.mjs` proxies `/api` + `/ws` → `127.0.0.1:8000`; in prod, Caddy does the same.

---

## Deployment

Single VPS **37.27.12.92**, domain **ranger-x.lohari.com.mx**.

- **systemd units:** `cc-core` (uvicorn backend :8000), `cc-web` (Next.js :3100 — 3000 is taken by another site), `cc-backup` (daily `pg_dump` timer).
- **Caddy v2** reverse-proxies `/api` + `/ws` → :8000 and everything else → :3100; auto-HTTPS via Let's Encrypt; installed as an imported `/etc/caddy/cc.caddy` (never overwrite the shared main Caddyfile).
- **PostgreSQL runs in Docker** (`lohari-postgres`); the backend connects **directly to the container IP** (not pgbouncer — transaction-pool mode breaks asyncpg prepared statements; the IP is unstable across Docker recreates).
- **Deploys are automatic:** every push to `main` triggers GitHub Actions (`.github/workflows/deploy.yml`), which SSHes to the VPS, runs the idempotent `deploy/deploy.sh` (pull → pip → `alembic upgrade head` → npm build → restart `cc-core`/`cc-web`) and smoke-tests `/api/health`. Migrations always run before the restart. Manual fallback: `sudo bash /srv/cc/deploy/deploy.sh`.

See `deploy/` (Caddyfile, systemd units, `deploy.sh`, `backup_db.sh`) and `docs/runbooks/`.

## Testing Strategy

- **Backend:** `pytest` + `pytest-asyncio` + `httpx`, 40+ test modules under `backend/tests/` covering auth, batches, scheduler, attribution, reconciler, watchdog, admission, plans, gift keys, cookies, Amazon rotation, redaction, support views, the account-swap guard (`test_account_guard.py`), live-forward (`test_live_forward.py`), and Historial display parity (`test_history_display_parity.py`). `ruff` + `mypy` configured in `pyproject.toml`.
- **Frontend:** `eslint` (`npm run lint`); the real gate is `npm run build` (runs `tsc` — lint alone does not catch type errors and once broke a deploy).
