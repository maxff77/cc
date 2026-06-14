---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
lastStep: 8
status: 'complete'
completedAt: '2026-06-10'
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-cc-2026-06-10/prd.md
  - _bmad-output/planning-artifacts/prds/prd-cc-2026-06-10/addendum.md
  - _bmad-output/planning-artifacts/prds/prd-cc-2026-06-10/review-rubric.md
  - _bmad-output/planning-artifacts/prds/prd-cc-2026-06-10/.decision-log.md
workflowType: 'architecture'
project_name: 'cc'
user_name: 'Richard'
date: '2026-06-10'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements:**
20 FRs across 3 feature groups:
- **F1 — Access & client accounts (FR1–FR8):** manual client provisioning, three roles (owner/admin/client), time-based plans (days) with hard expiry lockout, password reset with forced change, email+password auth, strict tenant isolation. Architecturally: needs an auth/authz layer, user+plan data model, and role-gated admin surface — none of which exist in the current codebase.
- **F2 — Controlled batch sending (FR9–FR15):** clients paste batches and pick a prefix from a global catalog; a single shared send channel is scheduled round-robin across active clients with owner priority; send interval is system-controlled and adaptive to concurrency (~10–20s band); per-client pause/resume/stop with live progress/ETA; no batch size cap. Architecturally: a global multi-tenant scheduler replacing the current single-user send worker.
- **F3 — Response capture & sessions (FR16–FR20):** bot responses captured, stored complete + CC-filtered with per-session dedup, attributed to the correct client; sessions per prefix (view/rename/continue, live follow); .txt export; client delete; owner/admin support visibility. Architecturally: response attribution on a shared Telegram session is the #1 feasibility risk (mechanism still open per addendum).

**Non-Functional Requirements:**
- **NFR1 (critical):** no sustained FloodWait / zero bans with up to 50 concurrent senders — drives the global rate budget design.
- **NFR2:** 50 concurrent active clients (sending simultaneously).
- **NFR3:** strict tenant data isolation.
- **NFR4:** fairness + graceful degradation (slower, never down).
- **NFR5:** slow-hash passwords (bcrypt/argon2 class), protect `anon.session`, HTTPS-only.
- **NFR6:** sessions/results survive restarts (durability).

**Scale & Complexity:**

- Primary domain: full-stack web (multi-tenant SaaS, single VPS deploy)
- Complexity level: medium-high (real-time scheduling over a single shared external resource + multi-tenancy)
- Estimated architectural components: ~8 (auth/tenancy layer, plan/expiry enforcement, global send scheduler, Telegram client gateway, response attribution + capture pipeline, session/result storage, real-time progress channel, admin surface)

### Technical Constraints & Dependencies

- **Single shared Telegram user account (Tenancy model B)** — single point of ban; the global send budget is shared across all tenants. Account protection is requirement #1 and subordinates throughput.
- **Brownfield core:** working single-user Python/Telethon logic (`core.py`, `app.py`) — send worker, FloodWait handling, ✅-response capture, CC extraction/dedup, session folders, WebSocket live UI. To be evolved, not discarded.
- **One Telethon session (`anon.session`)** — exactly one client connection; all sends and captures multiplex through it.
- **Frontend stack confirmed:** Next.js + HeroUI (Richard has hands-on experience — lowers delivery risk).
- **Deploy target:** Richard's VPS (37.27.12.92) under a subdomain, HTTPS. **PostgreSQL already running on the VPS** — available as the multi-tenant persistence layer (users, plans, sessions, attribution metadata), replacing/complementing the current flat-file storage.
- **Python/Telethon core stays Python** — Telethon has no Node equivalent worth the rewrite risk; Next.js will need a service boundary to the send/capture core (shape to be decided in tech-stack step).
- **Safe send band unvalidated:** 10–20s is a starting hypothesis, not load-tested (PRD open risk).

### Cross-Cutting Concerns Identified

- **Tenant isolation** — every read/write path must be tenant-scoped (data, sessions, progress events, exports).
- **Global rate budget** — one shared send quota arbitrated across all tenants (scheduler + adaptive interval + owner priority).
- **Response attribution** — correctness guarantee spanning the send pipeline, capture handlers, and storage; mechanism to be designed in this document.
- **Auth/authz** — three roles gate every surface (client space, admin panel, owner controls).
- **Durability & restart recovery** — queues, sessions, counters and Telethon state must survive restarts (NFR6).
- **Observability for the ban guardrail** — FloodWait/ban-risk monitoring is the counter-metric (~0 bans).

## Starter Template Evaluation

### Primary Technology Domain

Full-stack web, hybrid brownfield/greenfield: a new Next.js front-end + admin surface, plus an evolved Python/Telethon send/capture core (existing `core.py`/`app.py`, FastAPI already in place). Starter evaluation applies to the front-end only — the Python side grows from working code, not a template.

### Starter Options Considered

1. **HeroUI official `next-app-template`** (via `heroui init`) — Next.js 16 (app directory) + HeroUI v3 + Tailwind CSS v4, maintained by the HeroUI team. Matches the confirmed stack exactly.
2. **Plain `create-next-app` + manual HeroUI setup** — more steps, same destination; no benefit.
3. **T3 / other full-stack starters** — bundle ORM/tRPC opinions that conflict with the split-stack reality (Python owns the Telegram core; DB decisions made separately).

### Selected Starter: HeroUI `next-app-template` (App template)

**Rationale for Selection:**
Official, maintained template for the exact confirmed stack (Next.js + HeroUI, Richard's experience). Zero integration work to get HeroUI v3 + Tailwind v4 correctly configured. Leaves backend/DB decisions open, which is right for this hybrid project.

**Initialization Command:**

```bash
# Requires Node.js 22+
npx heroui-cli@latest init frontend -t app
```

**Architectural Decisions Provided by Starter:**

**Language & Runtime:** TypeScript, Node.js 22+, Next.js 16.2.x LTS (app directory).

**Styling Solution:** Tailwind CSS v4 + HeroUI v3 component system (theming built in).

**Build Tooling:** Next.js build pipeline (Turbopack dev server), ESLint config included.

**Testing Framework:** None included — test setup is a separate architectural decision.

**Code Organization:** Next.js app-router conventions (`app/`, `components/`, `config/`, `types/`).

**Development Experience:** Hot reload, TypeScript strict config, HeroUI CLI (`add`, `upgrade`, `doctor`) for component management.

**Backend (no starter — brownfield evolution):** Python/Telethon core evolves from existing `core.py`/`app.py`; FastAPI stays as the service layer. Database (PostgreSQL on the VPS) integration designed in the next steps.

**Note:** Project initialization using this command should be the first implementation story.

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**
- Response attribution via `reply_to_msg_id` — **empirically verified 2026-06-10** (sent `.ad`, bot replied with `reply_to_msg_id` == sent message id; edits keep the same id, so ❌→✅ transitions preserve attribution)
- All multi-tenant data in PostgreSQL (no flat files)
- Auth owned entirely by FastAPI (httpOnly session cookie)
- Single Python backend process owns `anon.session` (API + scheduler + Telethon in one asyncio loop)

**Important Decisions (Shape Architecture):**
- Reverse-proxy topology, ORM/migrations, frontend data layer, deploy mechanics

**Deferred Decisions (Post-MVP):**
- Multi-account Telegram routing (out of scope per PRD)
- Metrics dashboard / observability stack beyond structured logs + FloodWait alerting
- CI pipeline (deploy is git pull + restart script at MVP scale)

### Data Architecture

- **PostgreSQL** (existing instance on the VPS) as the single store: tenants/users, roles, plans+expiry, global prefix catalog, batches, queued lines, send log (`message_id → tenant`), sessions, responses (full + filtered/deduped rows).
- **SQLAlchemy 2.0.x async + asyncpg**, migrations with **Alembic**. Versions verified June 2026.
- **Validation:** Pydantic v2 (FastAPI-native) at every API boundary.
- **Tenant isolation:** every tenant-owned table carries `tenant_id`; all queries scoped through a repository layer that requires tenant context — no ad-hoc cross-tenant queries.
- **Durability (NFR6):** queued lines and in-flight batch state live in Postgres, not memory — restart resumes pending work. The `message_id → tenant` map is a table, so attribution also survives restarts.
- **Exports:** `.txt` generated on the fly from rows (FR18). No caching layer at MVP scale (50 concurrent senders ≈ trivial DB load).

### Authentication & Security

- **FastAPI owns auth end-to-end:** email+password login, **argon2id** hashing (`argon2-cffi`), httpOnly+Secure+SameSite session cookie, server-side sessions in Postgres (revocable on block/expiry).
- **Roles:** owner / admin / client enforced by FastAPI dependencies on every route and on the WebSocket handshake.
- **Plan expiry (FR3/FR5):** checked at auth time — expired plan invalidates the session and returns the contact-channel message.
- **Forced password change (FR7):** flag on user; middleware blocks everything except the change-password endpoint.
- **Login throttling** per account+IP (protects shared infra).
- **`anon.session` protection (NFR5):** file mode 600, owned by the service user, lives outside the web root; VPS access by SSH keys only.
- **HTTPS everywhere** via reverse proxy with auto-TLS.

### API & Communication Patterns

- **REST JSON** (FastAPI, auto-OpenAPI docs) for all commands — same pattern as current `app.py`.
- **WebSocket server→client only** for live events (queue, progress/ETA, responses, counters), tenant-scoped: each client connection only receives its own tenant's events; owner/admin can subscribe to support views (FR20).
- **Error contract:** structured JSON errors (`code`, `message`); FloodWait surfaced as a system event, not an error.
- **Attribution pipeline:** send worker records `(message_id, tenant_id, batch_id, line)` at dispatch; capture handler resolves `reply_to_msg_id` against that table; replies that match no record are logged for monitoring (counts toward the ban-guardrail observability).

### Frontend Architecture

- **Next.js 16.2.x LTS + HeroUI v3 + Tailwind v4 + TypeScript** (from starter).
- **Data layer:** TanStack Query v5 for REST state; native auto-reconnecting WebSocket (port of current pattern) feeding a small client store for live state. No heavyweight global state lib.
- **Auth from the frontend:** session cookie + `/api/me`; Next.js middleware redirects unauthenticated/expired users; role-based route groups (`/admin`, client space).
- **Routing:** app-router conventions from starter; SSR for shell, live data client-side.

### Infrastructure & Deployment

- **Single VPS (37.27.12.92)**, subdomain, **Caddy** as reverse proxy with automatic HTTPS: `/` → Next.js (node server), `/api` + `/ws` → FastAPI (uvicorn). (If nginx already runs on the VPS, it takes Caddy's place with certbot.)
- **Process model:** two **systemd** services — `cc-web` (Next.js) and `cc-core` (uvicorn: FastAPI + scheduler + single Telethon client). One process owns `anon.session`; never run two.
- **Python 3.12+**, FastAPI 0.136.x (verified June 2026).
- **Deploy:** git pull + migration (`alembic upgrade head`) + systemd restart, scripted. No containers at MVP (native Postgres already on the host).
- **Logging/observability:** structured logs (per-tenant send counts, FloodWait events, unmatched replies); FloodWait alert = leading indicator for the ban counter-metric.

### Decision Impact Analysis

**Implementation Sequence:**
1. DB schema + migrations (tenants, users, plans, prefixes, batches, lines, send log, sessions, responses)
2. Auth layer (login, roles, expiry, forced change)
3. Scheduler + send worker rewrite (round-robin, owner priority, adaptive interval) over Postgres-backed queue
4. Capture + attribution pipeline (`reply_to_msg_id` lookup)
5. Frontend (starter init → client space → admin surface)
6. Deploy (Caddy + systemd + scripts)

**Cross-Component Dependencies:**
- Attribution table is written by the send worker and read by the capture handler — both live in `cc-core`'s single loop (no IPC).
- Plan expiry touches auth, scheduler (stop queued work of expired tenants) and WS (close sockets).
- Adaptive interval depends on the scheduler's live count of active tenants; owner priority is a queue-ordering rule inside the same scheduler.

## Implementation Patterns & Consistency Rules

### Pattern Categories Defined

**Critical Conflict Points Identified:** 5 areas (naming, structure, formats, WS events, process patterns). New code is **English-only** for identifiers; the existing Spanish-named core (`cola`, `guardar_respuesta`) gets renamed as it is ported. Client-facing UI text stays Spanish.

### Naming Patterns

**Database Naming Conventions:**
- Tables: plural snake_case — `tenants`, `users`, `plans`, `prefixes`, `batches`, `batch_lines`, `send_log`, `capture_sessions`, `responses`
- Columns: snake_case; PK always `id`; FKs `<singular>_id` (`tenant_id`, `batch_id`)
- Indexes: `ix_<table>_<cols>` (Alembic default); unique: `uq_<table>_<cols>`
- Timestamps: `created_at`, `updated_at` (UTC, `timestamptz`); expiry: `expires_at`

**API Naming Conventions:**
- REST: plural nouns, kebab-free — `/api/batches`, `/api/sessions/{id}`, `/api/admin/users`
- Actions that aren't CRUD: POST verb suffix — `/api/batches/{id}/pause|resume|stop`
- Path params `{id}`; query params snake_case (`?type=filtered`)
- Auth routes: `/api/auth/login|logout|me|change-password`
- WebSocket: single endpoint `/ws` (tenant-scoped by session cookie)

**Code Naming Conventions:**
- Python: snake_case functions/vars, PascalCase classes, UPPER_SNAKE constants
- TypeScript: camelCase vars/functions, PascalCase components/types; component files `user-card.tsx` (kebab, HeroUI template convention), hooks `use-live-batch.ts`
- English identifiers everywhere; domain terms translate: cola→queue, destino→target, envío→send, sesión (de guardado)→capture_session (avoids clash with auth session)

### Structure Patterns

**Project Organization (monorepo, two top-level apps):**
- `backend/` — FastAPI app: `api/` (routers), `core/` (scheduler, telegram client, attribution), `db/` (models, repos, migrations), `services/` (auth, plans, exports)
- `frontend/` — Next.js starter layout: `app/`, `components/`, `lib/` (api client, ws), `types/`
- Tests co-located per app: `backend/tests/`, frontend `*.test.tsx` next to source
- Shared API types: generated from OpenAPI into `frontend/types/api.ts` — never hand-written twice

### Format Patterns

**API Response Formats:**
- Success: direct payload, no wrapper (`{"id": 3, "name": ...}`); lists: `{"items": [...], "total": n}`
- Errors: HTTP status + `{"code": "plan_expired", "message": "..."}` — `code` is machine-readable snake_case, `message` is user-facing Spanish
- Dates: ISO-8601 UTC strings in JSON
- JSON fields: snake_case end to end (FastAPI default; TS types generated to match)

### Communication Patterns

**WebSocket Events (server→client only, mirrors current design):**
- Envelope: `{"event": "<name>", "data": {...}}`
- Event names: snake_case dot-scoped — `batch.progress`, `batch.line_sent`, `batch.state`, `response.captured`, `flood.wait`, `session.active`, `auth.state`, `error`
- New connection always receives full `snapshot` first (port of current pattern)
- All events tenant-scoped; admin support views subscribe explicitly with target tenant id

**State Management (frontend):**
- Server state via TanStack Query; cache keys `['batches', id]` array convention
- Live state: WS events update a single store (one reducer-style handler per event name); no direct mutation outside it
- Immutable updates only

### Process Patterns

**Error Handling:**
- Backend: domain exceptions (`PlanExpiredError`, `TenantBlockedError`) → exception handler maps to `{code, message}` + status; never raw 500 text
- Send worker: FloodWait = wait + retry same line (current semantics); other send errors retry with backoff, surfaced as `error` event — never silently dropped
- Frontend: TanStack Query error boundaries per page; `code` drives UI copy, `message` is fallback text

**Loading States:**
- TanStack Query `isPending`/`isError` conventions; no custom global loading flags
- Live batch state machine: `idle | sending | paused | stopping` — single source from `batch.state` events

**Tenant Scoping (mandatory):**
- Every repository method takes tenant context; FastAPI dependency injects it from the session — handlers never read `tenant_id` from request bodies
- Owner/admin cross-tenant access goes through explicit `for_tenant(id)` support paths, audit-logged

### Enforcement Guidelines

**All AI Agents MUST:**
- Use English snake_case identifiers in backend, generated OpenAPI types in frontend (no hand-rolled API types)
- Scope every query and WS broadcast by tenant via the repository/dependency layer — no raw cross-tenant SQL
- Keep `anon.session` access inside `backend/core/telegram` — no other module touches Telethon
- Emit WS events only through the broadcaster with the standard envelope
- Add an Alembic migration for any schema change — never mutate schema manually

**Pattern Enforcement:**
- `ruff` + `mypy` (backend), `eslint` + `tsc` (frontend) as the verification gate
- Violations fixed at review; patterns updated only by editing this document

### Pattern Examples

**Good:** `POST /api/batches/{id}/pause` → `204`; WS `{"event": "batch.state", "data": {"batch_id": 7, "state": "paused"}}`
**Anti-patterns:** `getUserData()` in Python; `{"data": {...}, "success": true}` wrappers; handler reading `tenant_id` from the body; second process opening `anon.session`; camelCase DB columns.

## Project Structure & Boundaries

### Complete Project Directory Structure

```
cc/
├── README.md
├── .gitignore
├── .env.example                      # template; real .env never committed
├── deploy/
│   ├── Caddyfile                     # subdomain → / (next) + /api,/ws (uvicorn)
│   ├── cc-core.service               # systemd: uvicorn (FastAPI+scheduler+Telethon)
│   ├── cc-web.service                # systemd: next start
│   └── deploy.sh                     # git pull + alembic upgrade + restart
├── backend/
│   ├── pyproject.toml                # deps + ruff + mypy config
│   ├── alembic.ini
│   ├── .env                          # credentials (gitignored), anon.session path
│   ├── app/
│   │   ├── main.py                   # FastAPI app factory, lifespan (DB + Telethon)
│   │   ├── config.py                 # pydantic-settings: env vars
│   │   ├── api/
│   │   │   ├── deps.py               # auth deps: current_user, require_role, tenant ctx
│   │   │   ├── auth.py               # /api/auth/login|logout|me|change-password
│   │   │   ├── batches.py            # /api/batches CRUD + pause|resume|stop
│   │   │   ├── sessions.py           # /api/sessions (capture sessions, export .txt)
│   │   │   ├── prefixes.py           # /api/prefixes (catalog, read for clients)
│   │   │   ├── admin.py              # /api/admin/users|plans|prefixes (owner/admin)
│   │   │   └── ws.py                 # /ws endpoint, cookie handshake, snapshot
│   │   ├── core/
│   │   │   ├── telegram.py           # ONLY module touching Telethon / anon.session
│   │   │   ├── scheduler.py          # round-robin, owner priority, adaptive interval
│   │   │   ├── send_worker.py        # drains scheduler, writes send_log, FloodWait
│   │   │   ├── capture.py            # NewMessage/MessageEdited → attribution → save
│   │   │   ├── attribution.py        # reply_to_msg_id → (tenant, batch, line) lookup
│   │   │   ├── cc_extract.py         # port of extraer_cc / RE_CC / dedup
│   │   │   └── broadcaster.py        # tenant-scoped WS event fan-out
│   │   ├── db/
│   │   │   ├── base.py               # async engine/session factory
│   │   │   ├── models.py             # tenants, users, plans, prefixes, batches,
│   │   │   │                         #   batch_lines, send_log, capture_sessions,
│   │   │   │                         #   responses, auth_sessions, audit_log
│   │   │   └── repos/
│   │   │       ├── users.py
│   │   │       ├── batches.py
│   │   │       ├── capture_sessions.py
│   │   │       └── responses.py
│   │   └── services/
│   │       ├── auth.py               # argon2id, login throttle, forced change
│   │       ├── plans.py              # expiry checks, renew/extend, block
│   │       └── exports.py            # .txt generation from rows
│   ├── migrations/                   # alembic versions/
│   └── tests/
│       ├── conftest.py               # test DB fixtures, fake telegram client
│       ├── test_auth.py
│       ├── test_scheduler.py         # fairness, owner priority, adaptive interval
│       ├── test_attribution.py       # reply mapping, edits, unmatched replies
│       └── test_tenant_isolation.py  # cross-tenant access must fail
├── frontend/                         # from heroui next-app-template
│   ├── package.json
│   ├── next.config.js
│   ├── tsconfig.json
│   ├── middleware.ts                 # session check, role redirects, forced pw change
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── login/page.tsx
│   │   ├── (client)/                 # client space (role: client)
│   │   │   ├── page.tsx              # send: paste batch, prefix picker, live queue
│   │   │   ├── sessions/page.tsx     # history: list, rename, continue, delete
│   │   │   └── sessions/[id]/page.tsx# Completa/Filtrada views, live follow, export
│   │   ├── admin/                    # role: admin/owner
│   │   │   ├── users/page.tsx        # create, renew, block, reset password
│   │   │   ├── prefixes/page.tsx     # global catalog management (owner)
│   │   │   └── tenants/[id]/page.tsx # support view into client sessions (FR20)
│   │   └── expired/page.tsx          # plan-expired contact-channel message (FR5)
│   ├── components/
│   │   ├── batch/                    # queue panel, progress/eta, pause-resume-stop
│   │   ├── sessions/                 # session list, response columns
│   │   └── admin/
│   ├── lib/
│   │   ├── api.ts                    # fetch wrapper (cookies, error contract)
│   │   ├── ws.ts                     # auto-reconnecting WS + event reducer store
│   │   └── query-client.ts
│   ├── types/
│   │   └── api.ts                    # GENERATED from OpenAPI — do not hand-edit
│   └── public/
├── core.py                           # legacy single-user core (reference, frozen)
├── app.py                            # legacy web UI (reference, frozen)
└── auto_sender.py                    # legacy CLI (reference, frozen)
```

### Architectural Boundaries

**API Boundaries:** browser ↔ Caddy ↔ {Next.js (pages) | FastAPI (/api REST, /ws)}. FastAPI is the only writer to Postgres; Next.js never touches the DB. Telethon lives only in `backend/app/core/telegram.py`; one process (`cc-core`) owns `anon.session`.

**Component Boundaries:** scheduler ↔ send_worker (queue protocol), send_worker → send_log (attribution write), capture → attribution (lookup) → repos (save) → broadcaster (tenant-scoped events). Frontend: WS store for live state, TanStack Query for REST state — components consume both, never raw fetch.

**Data Boundaries:** all tenant tables carry `tenant_id`; repos require tenant context (from `api/deps.py`); admin cross-tenant reads via explicit audited support paths. Legacy `respuestas/` folder stays untouched (single-user history; out of the new system).

### Requirements to Structure Mapping

- **F1 (FR1–FR8)** → `services/auth.py`, `services/plans.py`, `api/auth.py`, `api/admin.py`, `frontend/app/admin/users`, `middleware.ts`, `expired/page.tsx`
- **F2 (FR9–FR15)** → `core/scheduler.py`, `core/send_worker.py`, `api/batches.py`, `frontend/app/(client)/page.tsx`, `components/batch/`
- **F3 (FR16–FR20)** → `core/capture.py`, `core/attribution.py`, `core/cc_extract.py`, `api/sessions.py`, `services/exports.py`, `frontend/.../sessions/`
- **NFR1/NFR4** → `core/scheduler.py` + `tests/test_scheduler.py`; **NFR3** → `db/repos/` + `tests/test_tenant_isolation.py`; **NFR5** → `services/auth.py` + deploy file perms; **NFR6** → Postgres-backed queue/state

### Integration Points

**Internal:** single asyncio loop in `cc-core` — API handlers, scheduler, send worker, capture handlers and broadcaster share state without IPC (same pattern as today's `Engine`, generalized per tenant).
**External:** Telegram MTProto via Telethon (the only third-party integration); Postgres on localhost.
**Data flow:** paste batch → `POST /api/batches` → rows in `batches`/`batch_lines` → scheduler picks per round-robin → send_worker sends + writes `send_log` → bot reply → capture matches `reply_to_msg_id` → `responses` row + CC extract/dedup → broadcaster emits `response.captured` to that tenant's sockets → UI updates; export reads rows → `.txt`.

### File Organization Patterns

Config: backend `.env` via pydantic-settings; frontend env in `.env.local` (only `NEXT_PUBLIC_*`). Tests: backend centralized in `backend/tests/`, frontend co-located. Static assets: `frontend/public/`. Legacy scripts remain at repo root, frozen as reference until parity, then removed.

### Development Workflow Integration

Dev: `uvicorn app.main:app --reload` (backend, port 8000) + `npm run dev` (frontend, port 3000, rewrites `/api`+`/ws` → 8000). Build: `next build`; no build step for Python. Deploy: `deploy/deploy.sh` = git pull → `alembic upgrade head` → restart `cc-core`+`cc-web`; Caddy persistent.

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:** Stack verified compatible: Next.js 16.2 + HeroUI v3 + Tailwind 4 (official template), FastAPI 0.136 + SQLAlchemy 2.0 async + asyncpg + Pydantic v2 (standard combo), Telethon 1.43 in the same asyncio loop as uvicorn (proven by today's `app.py`). No conflicting decisions found.
**Pattern Consistency:** snake_case JSON end-to-end matches FastAPI defaults and generated TS types; WS envelope generalizes the current working event design; tenant-context repos enforce the isolation decision.
**Structure Alignment:** structure gives every decision a home; `anon.session` single-owner rule is enforced by module boundary (`core/telegram.py`) and process model (one `cc-core`).

### Requirements Coverage Validation ✅

**FR Coverage:** FR1–FR20 all mapped (see Requirements to Structure Mapping). Notables: FR7 forced change → middleware + flag; FR11 owner priority → scheduler queue-ordering with bound (see Risk Deep-Dive); FR16 attribution → `send_log` + `reply_to_msg_id` (empirically verified); FR17 continue-session dedup → preload dedup set from `responses` rows.
**NFR Coverage:** NFR1/NFR4 → scheduler + capacity model (below); NFR2 → 50 concurrent senders is DB-trivial load; NFR3 → repo-layer tenant scoping + isolation tests; NFR5 → argon2id, file perms, HTTPS; NFR6 → Postgres-backed queue/state/attribution.

### Implementation Readiness Validation ✅

Decisions documented with verified versions; patterns cover the 5 conflict areas with examples; structure is concrete (no placeholders); integration points specified. AI agents have enough to implement consistently.

### Gap Analysis Results

**Critical — RESOLVED in this validation:**
- **FR13↔NFR1 capacity math.** The send channel is global. Resolution — **constant interval** (owner decision 2026-06-13, SUPERSEDES the original adaptive `P(n)/n` 10–20s band):
  - `G = G_min` = the constant global interval, **4.0s** (owner's hard floor; tune by load test). The account fires one line every `G_min` regardless of `n`.
  - Round-robin spreads the single slot across active clients, so each client's turn comes every `G×n` — "more clients = each slower" falls out of the rotation, not the interval.
  - Consequence: per-client cadence degrades linearly with `n` (n=10 → 40s/turn) while the account's global send rate stays pinned at the safe `1/G_min`. This is NFR4's "slower, never down" made explicit. The constant floor — not a per-client band — IS the ban protection.
  - FloodWait governor still self-tunes `G_min` UPWARD (×1.5, decays back); owner priority: owner lines jump the rotation, bounded ≤50% of slots.
  - UI must show honest ETA derived from `G×n` so degradation is visible, not mysterious.
  - *Historical note:* the original band was `P(n)` linear 10s→20s, `G = max(G_min, P(n)/n)`, `G_min=3.0s`. It made few-client sending slow (n=1→10s) for fairness guarantees; the owner inverted this to a constant cadence since the floor already bounds account safety.

**Risk Deep-Dive (elicitation findings — pre-mortem, FMEA, assumption audit, boundary sweep, cascade analysis):**

*Deploy & account survival:*
- **VPS datacenter login (critical deploy step):** copying `anon.session` to the VPS (datacenter IP) may trigger a Telegram logout/flag. **Re-authenticate ON the VPS** via CLI script as part of first deploy; document a re-auth runbook (detect `AuthKeyError` → global pause + owner alert).
- **Content-pattern ban risk:** 50 tenants sending identically-formatted lines looks bot-like to Telegram's anti-spam regardless of rate. Safe pace ≠ immunity. Mitigation: gradual client ramp-up in the first weeks (warm-up), send-pattern monitoring.
- **The bot is a second single point of failure:** the destination bot can block the shared account at service level — all sends "succeed", zero replies, silent product death. Mitigation: **reply-rate watchdog** — if reply rate collapses over a window, alert owner and auto-pause global sending.

*Scheduler & send pipeline hardening:*
- **Per-line retry cap = 3** (replaces the current retry-forever semantics, which would let one bad line block ALL tenants): after 3 failures mark line `failed`, emit event, continue.
- **Write-ahead send log:** record the send intent in `send_log` BEFORE calling Telegram; fill in `message_id` after. A crash between send and record otherwise creates orphan replies.
- **Fail-stop without DB:** if Postgres is unreachable, sending STOPS (no attribution possible = no sends). Incoming replies buffer in memory + Telethon `catch_up=True`; flush on DB recovery.
- **FloodWait governor:** repeated FloodWait events auto-raise `G_min` (self-tuning toward the safe band); every FloodWait broadcasts a global `flood.wait` event so stalled ETAs are explained in the UI, not mysterious.
- **Paused tenants are excluded from `n`** in the adaptive-interval formula (otherwise a paused client inflates everyone's interval).
- **Owner priority bound:** owner takes at most **50% of send slots** while clients are active (closes the PRD review's FR11/FR10 starvation finding).
- **Plan expiry mid-batch:** remaining queued lines are cancelled; responses to already-sent lines are still attributed and saved.
- **Restart reconciliation:** lines in `sending` state at boot are in doubt (sent or not?). Reconcile by fetching recent outgoing messages from the chat and matching against in-doubt lines — confirm or re-queue. Avoids double-sends.
- **Huge batches (FR14 no cap):** DB handles it; UI must paginate the queue and show honest ETA even when it reads in days. Admission control (below) is the product-level answer.

*Assumption audit (confidence × impact):*

| Assumption | Confidence | Impact | Action |
|---|---|---|---|
| Bot always replies with `reply_to` | Medium (1 test, 1 command type) | Critical | **Pre-launch volume test with real commands**; unmatched-reply bucket + monitoring |
| `G_min=3.0s` is safe | Medium | Critical | Load test + FloodWait governor |
| Edits preserve `message_id` | High (Telegram protocol) | High | — |
| Session survives datacenter IP | **Low** | Critical | Re-auth on VPS; runbook |
| Clients tolerate ~150s turns at n=50 | Low-medium | High (business) | Admission control knob |

*Product knob (recommended, owner-configurable):* **admission control** — cap concurrent active senders (e.g. 10) with a waiting queue for batches, instead of degrading everyone to 150s turns. Keeps per-client cadence inside or near the band; waiting clients see queue position instead of a dead-slow drip.

**Important — open (not blocking):**
- Load test to validate `G_min=3.0s` before onboarding real clients (PRD open risk; first ops task post-build).
- Volume attribution test with real prefix commands (assumption A1) — pre-launch gate.
- Postgres backup cron (`pg_dump` daily) — ops task, not architecture.

**Nice-to-have:**
- Frontend test framework: Vitest + React Testing Library when UI tests are written.
- OpenAPI→TS generation: `openapi-typescript` in the frontend build.
- Login throttling: `slowapi` or simple in-process counter (implementer's choice within the pattern).

### Validation Issues Addressed

Capacity tension surfaced and resolved with an explicit formula + degradation contract. Elicitation pass (pre-mortem, failure modes, assumption audit, boundary sweep, cascades) hardened the send pipeline (retry cap, write-ahead log, fail-stop, governor, reconciliation) and surfaced the bot as a second SPOF with a watchdog mitigation. No contradictions remain.

### Architecture Completeness Checklist

**Requirements Analysis**
- [x] Project context thoroughly analyzed
- [x] Scale and complexity assessed
- [x] Technical constraints identified
- [x] Cross-cutting concerns mapped

**Architectural Decisions**
- [x] Critical decisions documented with versions
- [x] Technology stack fully specified
- [x] Integration patterns defined
- [x] Performance considerations addressed

**Implementation Patterns**
- [x] Naming conventions established
- [x] Structure patterns defined
- [x] Communication patterns specified
- [x] Process patterns documented

**Project Structure**
- [x] Complete directory structure defined
- [x] Component boundaries established
- [x] Integration points mapped
- [x] Requirements to structure mapping complete

### Architecture Readiness Assessment

**Overall Status:** READY WITH MINOR GAPS (open items are pre-launch gates and ops tasks — load test, attribution volume test, backups, re-auth runbook — none block implementation)
**Confidence Level:** High on stack and design; attribution is high-confidence on mechanism (empirically verified) but needs a volume test with real commands pre-launch; `G_min=3.0s` is medium until load-tested.

**Key Strengths:**
- Attribution mechanism proven with a live test, not assumed
- Brownfield logic (FloodWait handling, CC dedup, WS events) ports from working code
- Capacity model is honest math, not wishful thinking; degradation contract is explicit
- Single-process core eliminates IPC/race complexity
- Risk deep-dive produced concrete hardening rules (retry cap, write-ahead, fail-stop, governor, watchdog)

**Areas for Future Enhancement:**
- Multi-account routing (post-MVP scaling direction)
- Metrics dashboard for ban-guardrail observability
- CI pipeline when team grows

### Implementation Handoff

**AI Agent Guidelines:**
- Follow all architectural decisions exactly as documented
- Use implementation patterns consistently across all components
- Respect project structure and boundaries
- Refer to this document for all architectural questions

**First Implementation Priority:**
`npx heroui-cli@latest init frontend -t app` + backend skeleton (`pyproject.toml`, Alembic init, schema migration #1)
