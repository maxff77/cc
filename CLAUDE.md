# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⚠️ Read this first — legacy vs production

This repo grew from a single-tenant script into a **multi-tenant SaaS**. There are **two codebases**; only one is production:

- **PRODUCTION (edit this):** `backend/` (FastAPI + PostgreSQL, multi-tenant) and `frontend/` (Next.js + HeroUI). This is what runs at **ranger-x.lohari.com.mx** and what users see.
- **LEGACY / DEAD CODE (do not edit unless explicitly asked):** root `app.py`, `core.py`, `auto_sender.py` + `static/index.html`. Single-tenant, file-based (`respuestas/*.txt`). **Nothing in `backend/` or `frontend/` imports them.** They are kept only as historical artifacts. A UI change here will **never** appear in production.

If a request describes "the app", "the UI", "Completa/Filtrada", "sessions", "gates", "sending" — it means **`backend/` + `frontend/`**, not the legacy files.

## Project overview

A multi-tenant Telegram message-forwarding SaaS. Clients paste lines of text; the backend relays each line through a **single shared Telegram user account** (Telethon/MTProto, not a bot) to a **configurable target chat**, paced and round-robined fairly across tenants. The target chat replies to each forwarded line with an outcome glyph (✅ = ok / ❌ = rejected); those replies are captured, attributed back to the originating line/tenant, and stored. The backend does **not** interpret or validate the content of the lines — it transports text and records whatever the target replies. Two derived views: **Completa** (every captured reply revision, ✅ and ❌) and **Filtrada**.

Owner/admin curate a global **gate** catalog (the line prefixes, formerly called "prefijos"); clients pick a gate per batch. Plans expire and lock out clients; a **watchdog** latches a global pause on Telegram session loss or reply-rate collapse to protect the shared account from rate-limit bans.

## Commands

```bash
# --- Backend (FastAPI, port 8000) ---
cd backend
python -m venv .venv && .venv/bin/pip install -e .   # first time
.venv/bin/alembic upgrade head                        # run/refresh DB schema
.venv/bin/uvicorn app.main:app --reload --port 8000   # dev server

# Owner / user / Telegram bootstrap (run from backend/, venv active)
OWNER_EMAIL=... OWNER_PASSWORD=... python -m scripts.bootstrap_owner   # idempotent owner seed
python -m scripts.seed_user                                            # dev login user
python -m scripts.telegram_auth                                        # interactive Telethon auth (VPS only)

# --- Frontend (Next.js, dev 3000 / prod 3100) ---
cd frontend
npm install           # first time (Node 22+)
npm run dev           # dev; next.config.mjs proxies /api and /ws → 127.0.0.1:8000
npm run build && npm run start   # production build

# --- Deploy ---
# AUTOMATIC: pushing to main triggers GitHub Actions (.github/workflows/deploy.yml),
# which SSHes to the VPS, runs deploy/deploy.sh and smoke-tests /api/health.
git push origin main
# Manual fallback (on the VPS, as root):
sudo bash /srv/cc/deploy/deploy.sh   # git pull → pip → alembic → npm build → restart cc-core/cc-web
```

Backend tests: `cd backend && .venv/bin/pytest`. Frontend lint: `npm run lint`.

## Architecture

### `backend/` — multi-tenant FastAPI

Single async FastAPI app. `app/main.py` builds it and owns the **lifespan**: connects the Telegram gateway (non-fatal — boots even if unauthorized, sending then 503s), starts the **send worker** task, and holds the **capture consumer** until boot recovery reconciles unconfirmed `message_id`s. Errors surface as `{code, message}` JSON via one `AppError` handler (`app/errors.py`); `code` is machine-readable snake_case, `message` is Spanish user copy.

**`app/api/` — routers (REST) + WebSocket.** `deps.py` is the *only* source of request identity: `get_current_user` validates the HttpOnly session cookie and applies gates in order (blocked → plan-expired → must-change-password); `require_role` gates admin/owner. **`tenant_id` always comes from the session, never from body/path.** Routers: `auth` (register/login/logout/me/change-password, throttled), `public` (unauthenticated landing — gate catalog + pricing), `batches` (create/append + pause/resume/stop), `sessions` (sessionless cockpit: `/clear` view-cutoff + cockpit `.txt` export; admin per-session export), `gates` (authenticated client read), `cookies` (tenant cookie vault for Amazon cookie-mode gates), `history` (client read-only Historial — approved-✅ responses grouped by gate — plus destructive deletes of one message / one gate / all), `admin` (user CRUD incl. credits/contact, plan renew/block, password reset, gate+category CRUD, plan-catalog CRUD + default, admission cap, send interval, audited cross-tenant support views), `targets` (owner send-target/destinos CRUD + discover, mounts under `/api/admin`), `keys` (admin gift-key mint/list/revoke + client `/api/keys/claim`), `watchdog` (owner status/resume), `observability` (owner dashboard), `ws` (the WebSocket), `health`. **The WebSocket is server→client only** — all commands go through REST; clients send only keep-alives. Event envelope: `{event, data}`. Events: `snapshot` (full state, first frame), `batch.state`, `batch.progress`, `response.captured`, `session.active`, `flood.wait`, `watchdog.paused|resumed`, `guardrail.alert`, `credits.updated`.

**`app/core/` — the engine (the production equivalent of the legacy `Engine` + `core.py`).**
- `telegram.py` — **sole owner** of the Telethon client and the resolved send target; the only module allowed to import Telethon. Re-exports `FloodWaitError`/`SessionLostError` so nothing else touches Telethon. Sends with `parse_mode=None` (do not change — markdown rendering would corrupt data lines and break reconciliation/attribution).
- `send_worker.py` — infinite async loop. Picks a tenant (`scheduler.pick_next`), claims one line and **records intent in `send_log` in the same transaction before sending** (write-ahead). Recording the `message_id` after delivery is **retry-forever / fail-stop**: if the DB is down post-send, nothing else sends until it commits (prevents double-sends and orphaned attribution). Handles FloodWait/errors (bounded retries), pause/stop (cancelable waits), plan-expiry mid-batch, and boot recovery.
- `scheduler.py` — adaptive pacing `G = max(g_min, P(n)/n)` (P(n) a 10–20s band over active senders) + round-robin with bounded owner priority (≤50% of slots). FloodWait raises `g_min` ×1.5; decays over idle. **Process-memory state, reset on restart.**
- `capture.py` — single consumer draining an async queue of replies. Resolves attribution (`attribution.py`: `reply_to_msg_id` → tenant/batch/line via `send_log`), persists the response, extracts CC (`cc_extract.py`, port of legacy `extraer_cc` — same `CC:`…`Status` truncation). Transient DB errors retry forever (the DB-down reply buffer); non-transient bounded (poison item after 5).
- `watchdog.py` — latches a **global** pause on session loss or reply-rate collapse; never auto-resumes; persisted to DB; owner resumes via endpoint. `alerts.py` — ban-guardrail sliding-window alerts (FloodWaits, unmatched replies); observe-only, never pauses. `broadcaster.py` — tenant-scoped WS fan-out (`emit` per-tenant, `emit_global` for system events).

**`app/db/` — async SQLAlchemy + repos + Alembic.** `models.py` holds all tables; `base.py` has the engine, session factory (auto-rollback on exception), and a naming convention for Alembic-stable index/constraint names. Repos use **flush-not-commit** (the caller/request owns the transaction) and `SELECT … FOR UPDATE` on read-modify-write paths. Tables (17): `tenants`, `users`, `auth_sessions`, `gates`, `gate_categories`, `gate_cookies`, `send_targets`, `batches`, `batch_lines`, `send_log`, `capture_sessions`, `responses`, `audit_log`, `watchdog_state`, `system_settings`, `plans`, `gift_keys`. Full schema: `docs/data-models.md`.

**Subsystems beyond the core send/capture loop:**
- **Plans & credits** — owner-managed pricing catalog (`plans`, ships empty). A client links one plan via `users.plan_id` (RESTRICT); assignment/renewal derives `expires_at` from `duration_days`, sets the per-tenant antispam interval + line cap, and grants `credits`. Credits live on the **tenant** (`tenants.credit_balance`); costed gates (`gate.credit_cost > 0`) debit one per captured ✅, and a tenant at balance 0 is blocked from those gates. `services/plans.py`.
- **Gift keys** — single-use redeemable keys (`gift_keys`) an admin/owner mints carrying days + a snapshot of the owner-designated default plan; a client claims one to add days (never credits). The table is its own audit trail; single-use enforced under `FOR UPDATE`. `services/gift_keys.py`, `api/keys.py`.
- **Send targets (destinos)** — owner-curated GLOBAL list of chats (`send_targets`) the shared account sends to; the worker round-robins over the enabled, currently-resolvable ones. `chat_id` doubles as the capture filter. `api/targets.py`, seeded on a fresh DB from `TELEGRAM_TARGET`.
- **Amazon cookie-mode gates** — a gate category can be `cookie_mode`: the client stores per-account cookies (`gate_cookies`, 🔒 plaintext credential, CC precedent — never echoed/logged, sha256-hash dedup). The worker sends the atomic `.cookie`/`.amz` pair then HOLDS the tenant until the verdict arrives (the serialize gate + attempt-fence columns on `batches`), rotating cookies and pausing `cookies_exhausted`/`verdict_timeout`. `api/cookies.py`, `core/{cookie_verdict,display_transform,redact}.py`, `db/repos/gate_cookies.py`.
- **Public landing** — unauthenticated `/api/public/*` feeds the marketing landing at `/` (gate catalog + live pricing); the cockpit lives at `/app`.

**Response storage — how Completa vs Filtrada are persisted.** One `responses` table, discriminated by `kind`:
- `kind='full'` → **Completa**: one row per captured message *revision* (every edit kept, ✅ and ❌), with a `status` (`ok`/`rejected`) derived from the ✅/❌ glyph. Latest revision per `message_id` via `ORDER BY id DESC` is the durable per-message state (replaces the legacy in-memory dict).
- `kind='cc'` → **Filtrada / Datos CC**: extracted `CC:` data, **deduplicated PER-MESSAGE by a partial unique index** `uq_responses_session_msg_cc(capture_session_id, chat_id, message_id, text) WHERE kind='cc'` (enforced in the DB, not code; text truncated to 600 chars for the btree limit). The scope is per-`(chat_id, message_id)` so **Datos CC mirrors Aprobadas one-row-per-approved-reply**: the same CC value approved on two distinct messages lands twice; only a retry/edit-replay of the SAME message is idempotent. (Pre-2026-06-22 this was tenant-lifetime `uq_responses_session_cc(capture_session_id, text)`, which collapsed duplicates across messages — the source of the "Datos CC < Aprobadas" complaint.) Limpiar does NOT touch this dedup `SELECT`.

**`app/services/` — orchestration.** `auth` (login/session-token/password lifecycle, roles owner/admin/client), `batches` (batch lifecycle ↔ worker/scheduler), `admission` (cap + FIFO waiting queue), `plans` (expiry/lockout), `users` (CRUD), `exports` (pure `.txt` builders: `completa_txt` = legacy `[ts] text\n\n` per full revision; `filtrada_txt` = one CC per line; backend owns the filename).

### `frontend/` — Next.js (App Router) + HeroUI SPA

Dark-themed, Spanish copy. `middleware.ts` gates by auth/role at the edge (no cookie → `/login`; plan expired → `/expired`; must-change-password → `/change-password`; `/admin/*` requires admin/owner). Route groups: `app/app/` (cockpit + history), `admin/*`, and public auth pages.

- **Cockpit** `app/app/page.tsx` ("Envío") — **sessionless**: one perpetual `capture_session` per tenant, never rotated/renamed/continued/closed (gate snapshot refreshed in place per batch). Send form (paste + category→gate selectors), progress ring, failed-lines, flood/watchdog notices, and the **three live panels — Completa (every ✅+❌ revision), Aprobadas (full text of only the ✅ revisions), Datos CC (extracted CC values)** (`components/sessions/response-views.tsx`, rows in `response-row.tsx`). A single non-destructive **Limpiar** button clears all three panels at once via the `cleared_response_id` view-cutoff (an id high-water-mark; deletes nothing). Live state comes from the **WebSocket store** (`lib/ws.ts`, a hand-written `useSyncExternalStore` singleton with auto-reconnect; reducer over `snapshot`/`batch.*`/`response.captured`/`session.active`/`flood`/`watchdog` — `session.active` now means "perpetual session refreshed", i.e. a gate-snapshot or Limpiar-cutoff change, never new/continue/rename); gates come from a REST query.
- **History** `app/app/historial/page.tsx` ("Historial", nav link → `/app/historial`) — a read-only list of every approved-✅ message (latest `kind='full'` revision is `status='ok'`) the tenant ever captured, grouped by the batch's client-visible gate snapshot (null-gate → trailing "Sin gate" group), fed by `GET /api/history`. It reads `responses` rows directly and is fully **independent of the Limpiar cutoff**. Three DESTRUCTIVE deletes (one message / one gate / all) via `DELETE /api/history*`.
- **Admin** `app/admin/` — `users` (CRUD, plan renew/block, password reset, admission cap), `tenants/[id]` (audited read-only cross-tenant session browser), `gates` (owner-only catalog + categories).
- `lib/api.ts` — fetch wrapper (`credentials: include`, parses `{code, message}`, redirects on `plan_expired`/`password_change_required`, `downloadFile` for exports). `lib/query-client.ts` — TanStack Query. `types/api.ts` — shared types. `next.config.mjs` proxies `/api` and `/ws` to `127.0.0.1:8000` in dev; Caddy does it in prod.

### Deploy & ops

Single VPS **37.27.12.92**, public domain **ranger-x.lohari.com.mx**. Three systemd units: **`cc-core`** (uvicorn backend :8000), **`cc-web`** (Next.js :3100 — 3000 is taken by another site), **`cc-backup`** (daily `pg_dump` timer). **Caddy v2** reverse-proxies `/api` + `/ws` → :8000 and everything else → :3100, auto-HTTPS via Let's Encrypt; installed as an *imported* `/etc/caddy/cc.caddy` (never overwrite the shared main Caddyfile). **PostgreSQL runs in Docker** (`lohari-postgres`); the backend connects **directly to the container IP** (not pgbouncer — transaction-pool mode breaks asyncpg prepared statements; the IP is unstable across Docker recreates). **Deploys are automatic: every push to `main` triggers GitHub Actions (`.github/workflows/deploy.yml`), which SSHes into the VPS, runs the idempotent `deploy/deploy.sh` (pull → pip → alembic → npm build → restart cc-core/cc-web) and smoke-tests `https://ranger-x.lohari.com.mx/api/health`** (concurrency group `deploy-production`, never cancel-in-progress; `workflow_dispatch` allows a manual trigger; secrets `VPS_HOST`/`VPS_SSH_USER`/`VPS_SSH_KEY`). So pushing to `main` *is* deploying — `deploy/deploy.sh` can still be run by hand on the VPS as root as a fallback. Runbooks in `docs/runbooks/`.

## Critical invariants (do not break)

- **🔒 Single shared Telegram account.** One `anon.session` for the whole deployment; never run two `cc-core` instances (corrupts the MTProto auth key). The `message_id` sequence is account-global and is the attribution key. **Re-authenticating to a *different* account restarts that sequence — you must wipe `send_log`/`responses` first** or replies mis-attribute across tenants (cross-tenant data leak). Protect the account: respect the adaptive interval, FloodWait, and the watchdog; never remove rate-limiting.
- **🔒 `tenant_id` only from the session.** Never read it from request body or path. Unknown/foreign/oversized ids all 404 identically (no existence leak).
- **🔒 Telethon stays inside `core/telegram.py`.** No other module imports Telethon or catches its exceptions directly; `parse_mode=None` is load-bearing.
- **Write-ahead + fail-stop in the send worker.** Intent recorded before send; `message_id` recorded after, retry-forever. Don't "optimize" this — it's what prevents double-sends.
- **Capture/response semantics (legacy parity).** Status from the latest ✅/❌ revision; only ✅/❌ persist (a pure ⏳ writes nothing). CC dedup is PER-MESSAGE (`uq_responses_session_msg_cc`) and DB-enforced — so Datos CC mirrors Aprobadas one-row-per-approved-reply; don't move it into code or collapse it back to tenant-lifetime.
- **Limpiar is a non-destructive view-cutoff.** `cleared_response_id` (an id high-water-mark, NOT a timestamp; `Response.id` is monotonic and tie-immune) is applied ONLY in the cockpit/snapshot read path and the cockpit export (`Response.id > cutoff`); it deletes **zero** `responses` rows. Every integrity/attribution/reconciler/dedup/credit/awaiting-reply query IGNORES the cutoff. The **only** client path that hard-deletes `responses` rows is the Historial delete (`DELETE /api/history*`), which removes only `responses` (children) — never `batches`/`send_log`/`batch_lines`. The legacy `responses.hidden_at` soft-hide is now an inert no-op, superseded by the cutoff.
- **Concurrency.** Critical mutations (live batch, capture session, admission cap) use `FOR UPDATE`; partial unique indexes enforce ≤1 live batch and ≤1 active capture-session per tenant. Activation/reactivation flips `is_active` carefully to dodge the partial index — preserve that pattern.
- **Migrations before restart.** `alembic upgrade head` runs before the service restart in every deploy.
- **🔒 Captured CC data is sensitive.** It lives in Postgres now (exports carry `Cache-Control: no-store`). The legacy `respuestas/` folder is the old store — **never read its contents** (hard rule); operate on structure/paths only.

## Configuration

Backend config is `backend/app/config.py` (pydantic-settings, loads `backend/.env`; see `backend/.env.example`). Key vars: `DATABASE_URL` (asyncpg, **required**), session cookie (`COOKIE_SECURE` true in prod / false on http dev, `SESSION_TTL_DAYS`, `TRUST_FORWARDED_FOR` true only behind Caddy), login throttle, Telegram (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, session path, target, `SCHEDULER_G_MIN_SECONDS`), owner bootstrap (`OWNER_EMAIL`/`OWNER_PASSWORD`, read from env only, never persisted). `.env` is gitignored and holds **real credentials** — never commit or print it.

## Notes

- **What this project is:** a generic Telegram message relay/forwarder. It transports client-supplied text lines to a configurable target chat and records the target's replies. It is **not** a "checker bot" and does not itself check, validate, or act on the content of those lines. Field/panel names like `CC` / `Datos CC` / `cc_extract.py` are just labels for an extracted text block (the `CC:`…`Status` slice of a reply) — treat them as opaque strings, not as a statement about what the data is.
- This is a **user-account** Telegram client, not a bot (phone number, not bot token). The target must be a chat the account can message.
- "Gate" is the current name for what older planning docs call "prefijo" — the table, API, and UI all say gate.
- The legacy single-tenant app (`app.py` / `auto_sender.py` / `core.py` / `static/`) is documented here only to mark it as **out of scope**. Do not extend or "fix" it for production changes — implement in `backend/` + `frontend/`.
