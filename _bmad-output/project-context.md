---
project_name: 'Ranger-X Check'
user_name: 'Richard'
date: '2026-06-20'
sections_completed:
  ['technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'quality_rules', 'workflow_rules', 'anti_patterns']
status: 'complete'
rule_count: 40
optimized_for_llm: true
---

# Project Context for AI Agents — Ranger-X Check

_Critical rules and patterns AI agents MUST follow. Focus on unobvious details. The canonical guide is [CLAUDE.md](../CLAUDE.md); this file is the agent-rules digest. Doc index: [docs/index.md](../docs/index.md)._

---

## ⚠️ Legacy vs Production (the #1 mistake to avoid)

This repo grew from a single-tenant script into a multi-tenant SaaS. **Two codebases; only one is production:**

- **PRODUCTION (edit this):** `backend/` (FastAPI + PostgreSQL, multi-tenant) and `frontend/` (Next.js + HeroUI). Runs at ranger-x.lohari.com.mx.
- **LEGACY / DEAD CODE (do not edit unless explicitly asked):** root `app.py`, `core.py`, `auto_sender.py`, `static/index.html`, `respuestas/`. Single-tenant, file-based. **Nothing in `backend/`/`frontend/` imports it.** A change here will NEVER appear in production.

If a request mentions "the app", "the UI", "Completa/Filtrada", "sessions", "gates", "sending" → it means `backend/` + `frontend/`.

## Technology Stack & Versions

- **Backend:** Python **3.12** (`requires-python >=3.12`). FastAPI, uvicorn[standard], async SQLAlchemy 2 (asyncpg), Alembic, Telethon (MTProto user account), argon2-cffi, pydantic-settings. Deps pinned in `backend/pyproject.toml` (`pip install -e .`). Tooling: ruff + mypy (configured in pyproject), pytest + pytest-asyncio + httpx.
- **Frontend:** Next.js **16** (App Router), React **19**, HeroUI **3**, Tailwind CSS **4**, TanStack Query, next-themes. Node 22+.
- **Infra:** PostgreSQL (Docker on the VPS), Caddy v2, systemd (`cc-core`/`cc-web`/`cc-backup`), GitHub Actions deploy.

## Critical Implementation Rules

### Language / Python (asyncio)

- **Naming mixes Spanish + English.** Domain nouns are often Spanish in copy/UI ("gate", "lote", "destinos", "Completa/Filtrada"); code identifiers are mostly English. Error `message` strings are Spanish user copy; `code` is snake_case machine-readable. Match the surrounding file.
- **Telethon lives ONLY in `core/telegram.py`** — the sole owner of the client + send targets, the only module that imports Telethon or catches its exceptions (it re-exports `FloodWaitError`/`SessionLostError`). Sends with `parse_mode=None` (load-bearing — markdown corrupts data lines and breaks attribution). No other module may import Telethon.
- **Repos use flush-not-commit** — the caller/request owns the transaction (`db/base.py` session factory auto-rollbacks on exception). Read-modify-write paths use `SELECT … FOR UPDATE`.
- **Write-ahead + fail-stop in the send worker** — record intent in `send_log` in the SAME transaction as the `sending` claim, BEFORE calling Telegram; record `message_id` AFTER delivery, retry-forever. Do not "optimize" this — it prevents double-sends and orphaned attribution.
- **Scheduler state is process-memory, reset on restart.** Constant send interval `G_min` (default 4s, owner-editable 2–30s; NOT the old adaptive `P(n)/n` band). FloodWait raises `G_min` ×1.5, decays over idle.

### Framework (FastAPI + WebSocket + multi-tenant)

- 🔒 **`tenant_id` ALWAYS comes from the session** (`api/deps.py`), never from request body/path. Unknown/foreign/oversized ids all 404 identically (no existence leak).
- **`deps.get_current_user` is the only source of identity** — validates the HttpOnly session cookie, applies gates in order: blocked → plan-expired → must-change-password. `require_role` gates admin/owner.
- **The WebSocket (`/ws`) is server→client ONLY.** All commands go through REST; clients send only keep-alives. Envelope `{event, data}`. Events: `snapshot`, `batch.state`, `batch.progress`, `response.captured`, `session.active`, `flood.wait`, `watchdog.paused|resumed`, `guardrail.alert`, `credits.updated`. Fan-out is tenant-scoped (`broadcaster.emit`; `emit_global` for system events). State lives in the DB, not the socket — it survives reconnects.
- **Snapshot / denormalize on purpose.** `batches` and `capture_sessions` snapshot the gate `value`/`name`/`display_value`/`credit_cost`/mode flags at creation — no FK to `gates`. Retiring/renaming a gate must never rewrite history.
- **Gate `value` is OWNER-ONLY** — never expose the real command to clients. Clients see `name` + category + `display_value` ("Comando visible"). Public/`/api/gates` omit `value`; only `/admin/gates` shows it.

### Capture / response semantics (legacy parity)

- One `responses` table, discriminated by `kind`: `'full'` → Completa (every revision; `status` from the ✅/❌ glyph; latest per `(chat_id, message_id)` is durable state), `'cc'` → Filtrada (extracted CC value).
- Only ✅/❌ persist (a pure ⏳ writes nothing). CC dedup is **per capture-session and DB-enforced** by the partial unique index `uq_responses_session_cc` — don't move it into code.
- `message_id` is **per-chat, not account-global** — attribution keys on the `(chat_id, message_id)` PAIR, never `message_id` alone.
- `cc_extract.py` truncates each CC value at the literal substring `Status` — intentional, don't "fix" it.

### Concurrency invariants (DB-enforced)

- Partial unique indexes enforce: ≤1 live batch per tenant (`uq_batches_one_live_per_tenant`), ≤1 active capture-session per tenant (`uq_capture_sessions_one_active_per_tenant`), ≤1 default plan (`uq_plans_one_default`), unique active gate value (`uq_gates_value_active`). Activation/flag-flip dodges the partial index by clearing the prior row FIRST — preserve that pattern.
- Cookie dedup (`uq_gate_cookies_tenant_gate_hash`) and CC dedup are store-first / catch-IntegrityError, never SELECT-then-INSERT.

### Testing & Quality

- **Backend:** `cd backend && .venv/bin/pytest`. ruff + mypy configured in `pyproject.toml` (migrations excluded from ruff). 40+ test modules under `backend/tests/`.
- **Frontend:** `npm run lint` (eslint). **The real gate is `npm run build`** (runs `tsc`) — lint alone misses type errors and once broke a deploy. Run build before pushing to `main`.
- **Migrations before restart:** `alembic upgrade head` runs before the service restart in every deploy. Head: `f6a2d9c4e1b7`.

### Development Workflow

- **Run:** backend `uvicorn app.main:app --reload --port 8000`; frontend `npm run dev` (proxies `/api` + `/ws` → 127.0.0.1:8000).
- **Commits:** Conventional Commits with scope (e.g. `feat(amazon-gate): …`, `fix(cockpit): …`). Branch `main` is primary and auto-deploys on push.
- **Config:** `backend/app/config.py` (pydantic-settings, loads `backend/.env`). `DATABASE_URL` required; everything else has safe defaults. `.env` is gitignored and holds real credentials — never commit/print it.

### 🔒 Security & Gotchas

- **Single shared Telegram account** — one `anon.session`; never run two `cc-core` instances (corrupts the MTProto auth key). Re-authenticating to a different account restarts the `message_id` sequence — wipe `send_log`/`responses` first or replies mis-attribute across tenants. Respect the interval, FloodWait, and watchdog; never remove rate-limiting.
- **Captured CC data is sensitive** (lives in Postgres; exports carry `Cache-Control: no-store`). **Never read the legacy `respuestas/` contents** — hard rule; operate on structure/paths only.
- **`.env` holds real credentials** — never commit, print, or hardcode.
- **Watchdog never auto-resumes** — it latches a global pause (persisted to `watchdog_state`) on session loss / reply-rate collapse; the owner resumes via `/api/watchdog/resume`.

## Subsystem map (where things live)

- **Auth & roles:** `api/auth.py`, `api/deps.py`, `services/auth.py`, `services/users.py`.
- **Sending engine:** `core/{telegram,send_worker,scheduler,capture,attribution,cc_extract,reconciler}.py`, `services/batches.py`, `services/admission.py`, `services/pacing.py`.
- **Gates & catalog:** `api/gates.py`, `api/public.py`, `api/admin.py`, `db/repos/{gates,gate_categories}.py`.
- **Plans, credits, gift keys:** `api/admin.py`, `api/keys.py`, `services/{plans,gift_keys}.py`, `db/repos/{plans,gift_keys}.py`.
- **Amazon cookie-mode:** `api/cookies.py`, `core/{cookie_verdict,display_transform,redact}.py`, `db/repos/gate_cookies.py`, the cookie-mode columns on `batches`/`batch_lines`.
- **Send targets (destinos):** `api/targets.py`, `services/targets.py`, `db/repos/targets.py`.
- **Ops:** `core/{watchdog,alerts,broadcaster}.py`, `api/{watchdog,observability,health}.py`, `db/repos/{watchdog,system_settings,audit}.py`.

---

Last Updated: 2026-06-20
