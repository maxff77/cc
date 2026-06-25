# Project Documentation Index — Ranger-X Check

> Generated: 2026-06-24 · Mode: full rescan (lean) · Primary entry point for AI-assisted development.

## Project Overview

- **Type:** Multi-part monorepo (Python backend + TypeScript frontend) — multi-tenant SaaS.
- **Product:** Ranger-X Check — a multi-tenant Telegram message forwarder. Clients paste lines of text; the platform relays them through **one shared Telegram user account** (Telethon/MTProto) to a **configurable target chat**, paced and round-robined fairly across tenants, then captures the target's ✅/❌ replies and attributes each back to its originating line and tenant. The platform transports text and records replies; it does not interpret line content.
- **Live:** https://ranger-x.lohari.com.mx (VPS `37.27.12.92`).
- **Primary language:** Python 3.12 (backend), TypeScript/React 19 (frontend).

### Two derived views of captured replies
- **Completa** — every captured reply revision (✅ and ❌), latest revision per message = durable state.
- **Filtrada** — `CC:` data extracted from replies, deduplicated **per-message** (DB-enforced via `uq_responses_session_msg_cc`), so Datos CC mirrors Aprobadas one-row-per-approved-reply.

The cockpit is sessionless — three live panels (Completa, Aprobadas ✅, Datos CC) with one non-destructive **Limpiar** view-cutoff — while a separate read-only **Historial** lists approved-✅ responses grouped by gate.

## Parts

| Part | Path | Stack | Doc |
|---|---|---|---|
| Backend (API + engine) | `backend/` | FastAPI, async SQLAlchemy 2, PostgreSQL, Telethon, Alembic | [architecture.md](./architecture.md#backend) |
| Frontend (cockpit + admin) | `frontend/` | Next.js 16 (App Router), React 19, HeroUI 3, TanStack Query | [architecture.md](./architecture.md#frontend) |

Backend ↔ frontend integration: REST under `/api/*` (commands) + a **server→client-only** WebSocket at `/ws` (live state). See [architecture.md › Integration](./architecture.md#integration).

## Generated Documentation

- [Architecture](./architecture.md) — both parts, the send/capture engine, integration, deploy topology.
- [Data Models](./data-models.md) — all 18 PostgreSQL tables (incl. the global `credentials` vault), keys, partial unique indexes, snapshot/denormalization rules. Alembic head `b3f1a7c9d2e4`.
- [API Contracts](./api-contracts.md) — every REST route by router (incl. the `X-Api-Key` `/api/credentials` vault and the owner `/admin/monitor` observability panel) + the WebSocket event envelope.

## Existing Documentation (authoritative, hand-maintained)

- [CLAUDE.md](../CLAUDE.md) — the canonical agent guide: legacy-vs-production split, architecture, critical invariants. **Read this first.**
- [PRODUCT.md](../PRODUCT.md) — product purpose, users, brand personality, design principles.
- [DESIGN.md](../DESIGN.md) — Ranger-X design system.
- [docs/runbooks/](./runbooks/) — ops runbooks: backups & restore, subdomain change, launch gates, launch plan, Telegram re-auth.
- [deploy/README.md](../deploy/README.md) — VPS deploy topology (systemd, Caddy, Docker Postgres).

## Quick Reference

- **Backend dev:** `cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000` (after `pip install -e .` + `alembic upgrade head`).
- **Frontend dev:** `cd frontend && npm run dev` (proxies `/api` + `/ws` → `127.0.0.1:8000`).
- **Tests:** `cd backend && .venv/bin/pytest`. **Lint:** `npm run lint`. **Build gate:** `npm run build` (runs `tsc` — lint alone misses type errors).
- **Deploy:** push to `main` → GitHub Actions SSHes to the VPS, runs `deploy/deploy.sh`, smoke-tests `/api/health`.

## ⚠️ Legacy vs Production (do not confuse)

The repo root holds a **dead** single-tenant prototype (`app.py`, `core.py`, `auto_sender.py`, `static/`, `respuestas/`). It is **not** production and nothing in `backend/`/`frontend/` imports it. All production work happens in `backend/` + `frontend/`. See CLAUDE.md for the full rule.

## Critical Invariants (excerpt — full list in CLAUDE.md)

- 🔒 **Single shared Telegram account** — one `anon.session`; never run two `cc-core` instances. `(chat_id, message_id)` is the attribution key. A boot-time **account-swap guard** (`services/account_guard.py`) latches the watchdog fail-closed if `anon.session` is re-authed to a different account while attribution data exists.
- 🔒 **`tenant_id` only from the session** — never from request body/path.
- 🔒 **Telethon stays in `core/telegram.py`** — `parse_mode=None` is load-bearing.
- **Write-ahead + fail-stop** in the send worker (intent before send; `message_id` after, retry-forever).
- 🔒 **Captured CC data is sensitive** — never read the legacy `respuestas/` contents.
