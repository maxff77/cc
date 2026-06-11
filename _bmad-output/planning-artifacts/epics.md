---
stepsCompleted: [1, 2, 3, 4]
status: complete
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-cc-2026-06-10/prd.md
  - _bmad-output/planning-artifacts/prds/prd-cc-2026-06-10/addendum.md
  - _bmad-output/planning-artifacts/architecture.md
  - _bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/DESIGN.md
  - _bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/EXPERIENCE.md
---

# cc - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for cc, decomposing the requirements from the PRD, UX Design if it exists, and Architecture requirements into implementable stories.

## Requirements Inventory

### Functional Requirements

**F1 — Access & client accounts**

FR1: An admin or owner creates client accounts manually (email + initial password). No self-registration.
FR2: Three roles — owner (full control, manages admins), admin (manages clients only: create, renew, block, reset password), client (operates only their own space).
FR3: Each client has a time-based plan measured in days with an expiration date; access cuts off automatically at expiry.
FR4: An admin or owner can renew/extend a client's plan (add days or set a new expiration date).
FR5: On plan expiry the client is fully locked out (no sending, no space access) and sees a message directing them to an external contact channel (WhatsApp/Telegram) to renew.
FR6: An admin or owner can reset a client's password: the system generates a secure random temporary password shown once on screen, delivered out-of-band (no automated email in MVP).
FR7: After a reset, the client is forced to change their password at next login before operating.
FR8: Email + password authentication. Strict tenant isolation: each client only sees and operates their own data.

**F2 — Controlled batch sending**

FR9: The client loads a batch by pasting text lines, picks a prefix from the global catalog (never free-text), and triggers the send.
FR10: The scheduler distributes the send channel round-robin across active clients; no client can monopolize the channel; all in-flight batches advance interleaved.
FR11: The owner has send priority: owner lines jump ahead of the client rotation (deliberate exception to FR10 fairness).
FR12: The interval between sends is system-controlled and never editable by the client (protects the shared account).
FR13: The interval adapts to concurrency: more simultaneous active clients → longer interval; fewer → shorter (target band ~10–20s per client).
FR14: No batch size cap in MVP: unlimited while the client's plan is active.
FR15: During a live batch the client can pause, resume, and stop their own send, with live progress and ETA; controls are per-client and never affect other clients' batches.

**F3 — Response capture & session management per client**

FR16: When the bot replies, the system captures the response, stores the complete response, and extracts `CC:` data into a session-deduplicated filtered view. Every response is attributed and saved to the correct client's space.
FR17: Each send produces a session grouped by prefix. The client can view, rename, and continue a session (resuming deduplication), with Completa and Filtrada views and live follow.
FR18: The client can export/download their results (complete and filtered views) as `.txt`.
FR19: The client can delete their own sessions (delete only; no content editing in MVP).
FR20: Owner and admins can view the content of any client's sessions for support purposes.

### NonFunctional Requirements

NFR1: Account protection (critical) — with up to 50 concurrent clients the send rhythm stays within safe Telegram limits: no sustained FloodWait, no bans of the shared account.
NFR2: Concurrency — MVP supports up to 50 concurrent clients (sending simultaneously), not necessarily 50 total active-plan clients.
NFR3: Tenant isolation — strict data separation between clients; no client accesses another's data, sessions, or sends.
NFR4: Fairness & graceful degradation — no client can hog the channel (round-robin); under rising concurrency the service gets slower, never falls over.
NFR5: Security — passwords stored with slow-derivation hash (bcrypt/argon2 class); `anon.session` file protected from unauthorized access; all web access over HTTPS on the subdomain.
NFR6: Durability — each client's sessions and results persist and survive service restarts.

### Additional Requirements

**Starter template (impacts Epic 1 Story 1):**

- Frontend initialized from the official HeroUI `next-app-template`: `npx heroui-cli@latest init frontend -t app` (Node.js 22+; Next.js 16.2.x LTS + HeroUI v3 + Tailwind CSS v4 + TypeScript strict). Architecture states project initialization with this command should be the first implementation story. Backend has no starter — brownfield evolution of `core.py`/`app.py` logic into a new FastAPI app skeleton (`pyproject.toml`, Alembic init, schema migration #1).

**Data & persistence:**

- PostgreSQL (existing instance on the VPS) as the single store: tenants/users, roles, plans+expiry, global prefix catalog, batches, queued lines, send log (`message_id → tenant`), capture sessions, responses (full + filtered/deduped rows), auth sessions, audit log. No flat files for multi-tenant data.
- SQLAlchemy 2.0.x async + asyncpg; migrations with Alembic (every schema change = a migration). Pydantic v2 validation at every API boundary.
- Durability (NFR6): queued lines and in-flight batch state live in Postgres, not memory; restart resumes pending work. Attribution map is a table, surviving restarts.
- Tenant isolation: every tenant-owned table carries `tenant_id`; all queries go through a repository layer requiring tenant context; handlers never read `tenant_id` from request bodies; admin cross-tenant access via explicit audited `for_tenant(id)` support paths.

**Auth & security implementation:**

- FastAPI owns auth end-to-end: email+password, argon2id hashing (`argon2-cffi`), httpOnly+Secure+SameSite session cookie, server-side sessions in Postgres (revocable on block/expiry).
- Roles enforced by FastAPI dependencies on every route and on the WebSocket handshake.
- Plan expiry checked at auth time — expired plan invalidates the session and returns the contact-channel message; forced-password-change flag blocks everything except the change-password endpoint; login throttling per account+IP.
- `anon.session` file mode 600, owned by the service user, outside the web root; HTTPS everywhere via reverse proxy with auto-TLS.

**Scheduler & send pipeline (hardening rules from architecture risk deep-dive):**

- Adaptive interval formula: `G = max(G_min, P(n)/n)` with `G_min = 3.0s` starting value (to be load-tested), `P(n)` linear 10s→20s for n=1→5; each active client gets a turn every `G×n`; paused tenants excluded from `n`.
- Owner priority bound: owner takes at most 50% of send slots while clients are active.
- Per-line retry cap = 3 (replaces legacy retry-forever): after 3 failures mark line `failed`, emit event, continue — one bad line must never block all tenants.
- Write-ahead send log: record send intent in `send_log` BEFORE calling Telegram; fill `message_id` after.
- Fail-stop without DB: if Postgres is unreachable, sending stops; incoming replies buffer in memory + Telethon `catch_up=True`; flush on recovery.
- FloodWait governor: repeated FloodWait auto-raises `G_min`; every FloodWait broadcasts a global `flood.wait` event.
- Plan expiry mid-batch: remaining queued lines cancelled; responses to already-sent lines still attributed and saved.
- Restart reconciliation: lines in `sending` state at boot are reconciled against recent outgoing chat messages — confirm or re-queue, never double-send.
- Admission control (owner-configurable knob, recommended): cap concurrent active senders (e.g. 10) with a waiting queue showing queue position.

**Response attribution & capture:**

- Attribution via `reply_to_msg_id` resolved against the `send_log` table (`message_id → tenant, batch, line`) — mechanism empirically verified; edits preserve `message_id` so ❌→✅ transitions keep attribution.
- Unmatched replies logged to a monitoring bucket (ban-guardrail observability).
- Reply-rate watchdog: if reply rate collapses over a window, alert owner and auto-pause global sending (bot is a second single point of failure).
- CC extraction/dedup ported from legacy `extraer_cc`/`RE_CC` (each captured value truncated at literal `Status`); continue-session preloads the dedup set from `responses` rows.

**Service & process model:**

- Single Python backend process (`cc-core`: uvicorn = FastAPI + scheduler + single Telethon client in one asyncio loop) owns `anon.session`; never run two. Telethon confined to `backend/app/core/telegram.py`.
- REST JSON commands + WebSocket server→client only (`/ws`, tenant-scoped by session cookie, envelope `{"event", "data"}`, snapshot-first on connect); error contract `{code, message}` (machine snake_case code, Spanish user message).
- Monorepo: `backend/` (FastAPI: api/, core/, db/, services/) + `frontend/` (Next.js starter layout). Shared API types generated from OpenAPI into `frontend/types/api.ts` — never hand-written.
- English-only identifiers in new code; DB naming conventions (plural snake_case tables, `tenant_id` FKs, `timestamptz` UTC); WS event names snake_case dot-scoped (`batch.progress`, `response.captured`, `flood.wait`…).

**Infrastructure & deployment:**

- Single VPS (37.27.12.92), subdomain, Caddy reverse proxy with automatic HTTPS: `/` → Next.js, `/api` + `/ws` → FastAPI (nginx+certbot fallback if already present).
- Two systemd services: `cc-web` (Next.js) and `cc-core` (uvicorn). Python 3.12+, FastAPI 0.136.x.
- Deploy script: git pull → `alembic upgrade head` → systemd restart. No containers in MVP.
- Telegram re-authentication ON the VPS via CLI script as part of first deploy (datacenter IP may invalidate a copied `anon.session`); re-auth runbook: detect `AuthKeyError` → global pause + owner alert.
- Gradual client ramp-up in the first weeks (warm-up) to mitigate content-pattern ban risk.

**Observability & quality gates:**

- Structured logs: per-tenant send counts, FloodWait events, unmatched replies; FloodWait alerting as leading ban indicator.
- Backend tests: scheduler (fairness, owner priority, adaptive interval), attribution (reply mapping, edits, unmatched), tenant isolation (cross-tenant access must fail), auth; fake Telegram client fixture.
- Verification gate: `ruff` + `mypy` (backend), `eslint` + `tsc` (frontend).
- Pre-launch gates (ops, not blocking implementation): load test to validate `G_min=3.0s`; attribution volume test with real prefix commands; daily `pg_dump` backup cron.

### UX Design Requirements

UX-DR1: Theme layer — apply the fixed token set from `imports/heroui-theme.css` verbatim onto HeroUI v3 (oklch palette, 0.25rem radius, Public Sans as `--font-sans`); dark mode is the default surface, light mode fully supported; no new colors, no gradients (only the ring's conic), no per-prefijo color coding, no shadow hierarchy (tonal surface ladder + 1px borders only).
UX-DR2: Typography system — Public Sans for all UI text; monospace strictly confined to data (CC rows, timestamps, counters, ETA digits, prefijo chips, session ids) using the defined ramp (`data-mono` 11px, `metric` 18px, `metric-lg` 26px, `label-caps` 10px tracked uppercase).
UX-DR3: Progress ring component — HeroUI `CircularProgress` (~128px mobile): accent stroke while `sending`, warning amber while `paused`; center shows % (`metric-lg`) + fraction (`data-mono`); flank shows exactly three metrics (enviadas · en cola, ETA, CC nuevas in success green) — no other stats anywhere.
UX-DR4: State pill component — HeroUI `Chip`, full radius, uppercase tracked 10px, mirrors `batch.state` verbatim (Enviando accent-tint / En pausa amber-tint / Deteniendo), hidden at `idle`.
UX-DR5: Control buttons component — Pausar (surface bg, warning text), Detener (surface bg, danger text), Reanudar (solid success fill — the only solid control); visible set follows the state machine (`sending`→Pausar+Detener, `paused`→Reanudar+Detener, `stopping`→disabled, `idle`→hidden); presses fire REST, UI flips only on the resulting `batch.state` event — no optimistic state jumps; Detener stays instant (no confirm), Eliminar requires confirm modal.
UX-DR6: Dual-view Completa/Filtrada — segmented HeroUI `Tabs` on mobile, two side-by-side panels on desktop; live mono count badges (Filtrada's in success green); new `response.captured` rows append with "nueva" highlight (success-tint bg); auto-scroll only if the pane was already at the bottom; per-view export button (`↓ .txt`) available during a live lote and on closed sessions.
UX-DR7: Data row component — the only console-density element: `data-mono` 11px, 1px separator dividers, muted timestamp/index left, ellipsized content, status glyph right (✅ success / ❌ danger).
UX-DR8: FloodWait notice component — amber informational strip (never danger styling) with live mono countdown, copy "Telegram pidió esperar Ns — reanudamos solos."; appears on `flood.wait`, self-dismisses when sending resumes.
UX-DR9: Prefijo selector — HeroUI `Select` over the global catalog fetched by API; never free text; required before sending; prefijo chip shows the active prefix verbatim with its dot in mono.
UX-DR10: Navigation — exactly two client sections, Envío | Historial: bottom nav on mobile (with 6px live dot: success while sending, warning while paused), inline header nav on desktop; modal stacks max one level deep.
UX-DR11: Session row (Historial) — friendly name (heading), mono sub-line `prefijo · session-id` muted, right badge "En curso" (accent-tint) / "Cerrada" (muted); actions: Renombrar (inline, REST-persisted), Continuar (reopens as active session, dedup preserved; rejected with Spanish error if a lote is live), Eliminar (confirm modal).
UX-DR12: Batch state machine in frontend — single source of truth is the WS `batch.state` event (`idle | sending | paused | stopping`); every control, pill and ring color derives from the last received state; UI never invents a state.
UX-DR13: WebSocket UX contract — single auto-reconnecting `/ws`; every new connection receives a full snapshot first and the UI renders entirely from it (tab opened mid-lote shows correct ring/counts/rows immediately); reconnect reconciles silently; no offline UX beyond this (no banners, no queued offline actions).
UX-DR14: ETA display — honest adaptive estimate derived from `G×n` ("~12 min"), recomputed on each `batch.progress`; relabeled "ETA al reanudar" while paused; never a fake-precise countdown; degradation visible, not mysterious.
UX-DR15: Spanish tuteo microcopy throughout (neutral LatAm, operational tone, product terms verbatim: cliente, prefijo, sesión, lote, Completa/Filtrada, pausar/reanudar/detener); error contract maps known `code`s to Spanish copy with server `message` as fallback.
UX-DR16: Per-surface empty/error/edge states as specified: Envío idle ("Pega tus líneas y elige un prefijo."), cold-load skeletons, login invalid-credentials inline error (form stays filled), blocked-account notice with external-channel buttons, empty Historial/Completa/Filtrada/admin-table copy, forced-password-change single screen, `/expired` hard lockout with WhatsApp/Telegram contact buttons (never a dead-end), permission-denied middleware redirect (no blocked screen).
UX-DR17: Route map verbatim — `/login`, `/(client)/` Envío, `/(client)/sessions` Historial, `/(client)/sessions/[id]` detail, `/admin/users` (admin+owner), `/admin/prefixes` (owner only), `/admin/tenants/[id]` support view, `/expired`; Next.js middleware enforces auth, role gates, expiry redirect, forced-password-change block.
UX-DR18: Admin surfaces — HeroUI `Table` reusing the same theme (no separate admin theme): user table with row actions (crear cliente, renovar plan, bloquear, resetear contraseña → temp password shown once); owner-only prefijo catalog CRUD; cross-tenant session viewer reusing the dual-view component read-only; admin tables responsive (usable on phone, no special mobile design).
UX-DR19: Responsive recomposition — mobile-first single column (~390px reference: header → ring → controls → data panel filling remaining height → bottom nav; cockpit never scrolls away while a batch is live; data panel scrolls internally); desktop ≥lg is a 3-column grid (`300px 1fr 1fr`) recomposing the same components, never a separate feature set; tablets reuse the single-column layout.
UX-DR20: Accessibility floor — HeroUI v3 component defaults only, unmodified (explicit MVP scope cut; no extra audits, no custom ARIA/screen-reader work, no reduced-motion handling).
UX-DR21: Banned interactions — free-text prefijo entry, user-editable send interval (display-only), filler stats/vanity counters, modal stacks >1, celebratory animations, push notifications, hover-only affordances on touch viewports; none of the legacy `static/index.html` visual patterns carry over.

### FR Coverage Map

FR1: Epic 1 - Manual client account creation by admin/owner
FR2: Epic 1 - Three-role model (owner / admin / client)
FR3: Epic 1 - Time-based plan (days) with automatic expiry cutoff
FR4: Epic 1 - Plan renewal/extension by admin/owner
FR5: Epic 1 - Full lockout on expiry with external contact channel message
FR6: Epic 1 - Password reset with one-time temp password shown on screen
FR7: Epic 1 - Forced password change at next login after reset
FR8: Epic 1 - Email+password auth with strict tenant isolation
FR9: Epic 2 - Paste batch + prefix from global catalog (no free text)
FR10: Epic 2 - Round-robin scheduling across active clients
FR11: Epic 2 - Owner send priority (bounded at 50% of slots)
FR12: Epic 2 - System-controlled, non-editable send interval
FR13: Epic 2 - Concurrency-adaptive interval (~10–20s band)
FR14: Epic 2 - No batch size cap while plan is active
FR15: Epic 2 - Per-client pause/resume/stop with live progress and ETA
FR16: Epic 3 - Response capture, attribution to correct client, CC: extraction with session dedup
FR17: Epic 3 - Sessions per prefix: view, rename, continue (dedup preserved), Completa/Filtrada, live follow
FR18: Epic 3 - Export complete and filtered views as .txt
FR19: Epic 3 - Client deletes own sessions (no content editing)
FR20: Epic 3 - Owner/admin cross-tenant session view for support
NFR1/NFR4 (operational): Epic 4 - Ban-guardrail operations: reply-rate watchdog, admission control, observability, runbooks, pre-launch gates

## Epic List

### Epic 1: Plataforma accesible y cuentas de clientes
Admins and the owner provision, renew, block and password-reset clients; clients log in securely at the subdomain with a valid day-based plan. Includes project bootstrap (HeroUI starter + backend skeleton + initial schema — first story per architecture mandate) and the deploy walking skeleton (Caddy + systemd + HTTPS + Telegram re-auth on the VPS) so login works in production from the start.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6, FR7, FR8

### Epic 2: Envío en lote controlado
The client pastes a batch, picks a prefix from the catalog and sends; the system schedules all tenants' sends over the shared channel (round-robin, owner priority bounded at 50%, adaptive interval `G = max(G_min, P(n)/n)`, retry cap 3, write-ahead send log, FloodWait governor, fail-stop without DB, restart reconciliation). Pause/resume/stop with progress ring, honest ETA and amber FloodWait notice in the Envío UI.
**FRs covered:** FR9, FR10, FR11, FR12, FR13, FR14, FR15

### Epic 3: Captura de respuestas, sesiones e historial
Every bot reply reaches the correct client (`reply_to_msg_id` attribution + send_log), with live Completa/Filtrada views, `CC:` dedup, `.txt` export, Historial (rename, continue with preserved dedup, delete) and the cross-tenant support view for owner/admins.
**FRs covered:** FR16, FR17, FR18, FR19, FR20

### Epic 4: Protección operativa de la cuenta (guardarraíl de baneo)
The owner operates the service without ban risk: reply-rate watchdog with global auto-pause, configurable admission control (waiting queue with position), structured logs + FloodWait alerting, `AuthKeyError` re-auth runbook, daily Postgres backup, and pre-launch gates (`G_min` load test, attribution volume test). Direct owner value: the ~0-bans counter-metric becomes operable.
**FRs covered:** none new — operationalizes NFR1/NFR4 and the architecture risk deep-dive mitigations

## Epic 1: Plataforma accesible y cuentas de clientes

Admins and the owner provision, renew, block and password-reset clients; clients log in securely at the subdomain with a valid day-based plan. Includes project bootstrap (HeroUI starter + backend skeleton + initial schema) and the deploy walking skeleton (Caddy + systemd + HTTPS + Telegram re-auth on the VPS) so login works in production from the start.

### Story 1.1: Inicializar proyecto desde starter + esqueleto backend

As a developer,
I want the monorepo initialized from the HeroUI starter with a working FastAPI backend skeleton,
So that every later story builds on the mandated stack and conventions.

**Acceptance Criteria:**

**Given** a clean repo
**When** `npx heroui-cli@latest init frontend -t app` is run (Node.js 22+)
**Then** `frontend/` exists with Next.js 16.2.x + HeroUI v3 + Tailwind CSS v4 + TypeScript strict and the dev server starts without errors

**Given** the generated frontend
**When** the theme layer is applied
**Then** the tokens from `imports/heroui-theme.css` are applied verbatim (oklch palette, 0.25rem radius), dark mode is the default surface, light mode works, and Public Sans is loaded as `--font-sans` (UX-DR1, UX-DR2)

**Given** the repo root
**When** the backend skeleton is created
**Then** `backend/` contains `pyproject.toml` (FastAPI 0.136.x, SQLAlchemy 2.0.x async, asyncpg, Alembic, argon2-cffi, pydantic-settings, ruff + mypy config), an app factory in `app/main.py`, env-based config in `app/config.py`, and a health route responding under `/api`

**Given** Alembic is initialized
**When** migration #1 is applied
**Then** only the `tenants`, `users` (with role) and `auth_sessions` tables exist — no other tables are created ahead of need

**Given** both apps
**When** running `uvicorn app.main:app --reload` (port 8000) and `npm run dev` (port 3000)
**Then** the frontend proxies `/api` and `/ws` to 8000 via rewrites
**And** `ruff`, `mypy`, `eslint` and `tsc` all pass

**Given** the backend OpenAPI schema
**When** the type-generation step runs (`openapi-typescript`)
**Then** `frontend/types/api.ts` is generated from it — API types are never hand-written

### Story 1.2: Login y logout con email + contraseña

As a client,
I want to log in with my email and password,
So that I access my own private space securely.

**Acceptance Criteria:**

**Given** a registered user with a valid plan
**When** they submit correct credentials at `/login`
**Then** the password is verified against its argon2id hash, a server-side session row is created, and an httpOnly+Secure+SameSite cookie is set
**And** they land on their role's home surface

**Given** wrong credentials
**When** the form is submitted
**Then** an inline field-level error "Correo o contraseña incorrectos." is shown, the email stays filled, and no redirect happens

**Given** repeated failed attempts from the same account+IP
**When** the throttle threshold is exceeded
**Then** further attempts are rejected temporarily (login throttling)

**Given** a blocked account
**When** it attempts login
**Then** a blocking notice "Tu cuenta está bloqueada. Escríbenos por WhatsApp o Telegram para reactivarla." is shown with external-channel buttons — never a dead-end

**Given** an authenticated user
**When** they log out
**Then** the server-side session is revoked and the cookie cleared

**Given** an unauthenticated visitor
**When** they request any protected route
**Then** Next.js middleware redirects them to `/login`
**And** `/api/auth/me` returns the session's user and role for authenticated requests

### Story 1.3: Alta manual de clientes y gestión de roles

As an admin or owner,
I want to create client accounts manually and have roles enforced everywhere,
So that only paying clients access the service.

**Acceptance Criteria:**

**Given** a fresh deployment
**When** the owner bootstrap (env/CLI seed) runs
**Then** the owner account exists and can log in

**Given** an admin or owner on `/admin/users`
**When** they create a client with email, initial password and plan days
**Then** the client account exists with role `client` and can log in immediately

**Given** an existing client email
**When** an admin tries to create a duplicate
**Then** the API returns an error code and the UI shows "Ya existe un cliente con ese email."

**Given** a logged-in client
**When** they request any `/admin` route
**Then** middleware redirects them away — no "blocked" screen is rendered

**Given** a logged-in admin
**When** they view `/admin/users`
**Then** they see and manage only clients — admin accounts are not manageable by admins

**Given** the owner on `/admin/users`
**When** they create or remove an admin
**Then** the change takes effect — owner is the only role that manages admins
**And** the empty table state shows "Todavía no hay clientes."

### Story 1.4: Expiración automática del plan y lockout total

As the owner,
I want expired clients fully locked out automatically,
So that access always matches payment.

**Acceptance Criteria:**

**Given** a client whose plan `expires_at` has passed
**When** they attempt any request (page or API)
**Then** the auth check invalidates their session and every route resolves to `/expired`
**And** API requests return `{"code": "plan_expired"}` with the proper status

**Given** the `/expired` page
**When** an expired client lands on it
**Then** it shows "Tu plan venció. Escríbenos por WhatsApp o Telegram y lo reactivamos." with direct external contact buttons — no partial access, no degraded mode

**Given** a client mid-session
**When** their plan expires
**Then** the next auth check cuts access automatically with no admin action needed

### Story 1.5: Renovar plan y bloquear/desbloquear cliente

As an admin or owner,
I want to renew plans and block problem clients from the user table,
So that the client lifecycle is manageable without touching the database.

**Acceptance Criteria:**

**Given** a client row in `/admin/users`
**When** an admin renews the plan (add days or set a new expiration date)
**Then** the new `expires_at` is persisted and visible in the table

**Given** an expired client whose plan was renewed
**When** they log in again
**Then** access works normally

**Given** a client row
**When** an admin blocks the client
**Then** lockout is immediate: server-side sessions are revoked and the next login shows the blocked notice (per Story 1.2)

**Given** a blocked client
**When** an admin unblocks them
**Then** the client can log in again normally

### Story 1.6: Reset de contraseña con cambio forzado

As an admin or owner,
I want to reset a client's password to a one-time temp password,
So that I can restore access without email infrastructure.

**Acceptance Criteria:**

**Given** a client row in `/admin/users`
**When** an admin triggers a password reset
**Then** the system generates a secure random temporary password, shows it exactly once on screen, and stores only its argon2id hash
**And** the account is flagged for forced password change

**Given** a client flagged for forced change
**When** they log in with the temp password
**Then** the only reachable screen is "Elige una contraseña nueva para continuar" — middleware blocks every other route and API except the change-password action

**Given** the forced-change screen
**When** the client sets a new password
**Then** the flag clears and they land on their normal home surface

### Story 1.7: Despliegue en producción con HTTPS y re-auth de Telegram en el VPS

As the owner,
I want the platform deployed at the subdomain with HTTPS and the Telegram session authenticated on the VPS,
So that clients log in to a real production service.

**Acceptance Criteria:**

**Given** the VPS (37.27.12.92) with the subdomain pointed at it
**When** Caddy is configured from `deploy/Caddyfile`
**Then** `/` routes to Next.js, `/api` and `/ws` route to uvicorn, and HTTPS works with automatic TLS

**Given** the systemd units `cc-core.service` and `cc-web.service`
**When** they are enabled and started
**Then** both services run, restart on failure, and exactly one process (`cc-core`) will own `anon.session`

**Given** `deploy/deploy.sh`
**When** it runs
**Then** it performs git pull → `alembic upgrade head` → restart of both services

**Given** the Telegram re-auth CLI script
**When** the owner runs it ON the VPS (phone → code → optional 2FA)
**Then** `anon.session` is created on the VPS with file mode 600, owned by the service user, outside the web root — never copied from another machine

**Given** the deployed stack
**When** a user opens the subdomain
**Then** the login flow (Story 1.2) works end-to-end in production over HTTPS

## Epic 2: Envío en lote controlado

The client pastes a batch, picks a prefix from the catalog and sends; the system schedules all tenants' sends over the shared channel (round-robin, owner priority bounded at 50%, adaptive interval, retry cap, write-ahead send log, FloodWait governor, fail-stop without DB, restart reconciliation). Pause/resume/stop with progress ring, honest ETA and amber FloodWait notice in the Envío UI.

### Story 2.1: Catálogo global de prefijos

As the owner,
I want to manage the global prefix catalog,
So that clients only ever pick from approved prefixes.

**Acceptance Criteria:**

**Given** the schema
**When** the migration for this story is applied
**Then** the `prefixes` table exists (global catalog, prefix stored verbatim with its dot, e.g. `.zo`)

**Given** the owner on `/admin/prefixes`
**When** they create, edit or delete a catalog entry
**Then** the change persists and is visible in the table
**And** the empty state shows "El catálogo está vacío."

**Given** an admin or client
**When** they request `/admin/prefixes`
**Then** middleware redirects them away — the catalog surface is owner-only

**Given** an authenticated client
**When** the frontend requests the prefix catalog API
**Then** it returns the catalog entries for use in the prefijo selector (read-only for clients)

**Given** a prefix referenced by existing batches or sessions
**When** the owner deletes it from the catalog
**Then** it is retired (soft-delete): it disappears from the client selector, but historical batches and sessions keep displaying their prefix verbatim

### Story 2.2: Enviar un lote (un cliente) con progreso en vivo

As a client,
I want to paste my lines, pick a prefix and send the batch watching live progress,
So that my batch goes out hands-free.

> _Size note: largest story in the document — the architecture fully specifies every component (gateway, tables, API, worker, WS, UI). If a dev agent runs short on context, split backend (gateway + API + worker + WS) from the Envío UI._

**Acceptance Criteria:**

**Given** the backend service `cc-core`
**When** it starts
**Then** the Telethon client connects in the FastAPI lifespan, lives only in `core/telegram.py`, and is the single owner of `anon.session`

**Given** the schema
**When** this story's migration is applied
**Then** `batches` and `batch_lines` tables exist with `tenant_id`, state and ordering columns

**Given** a client with a valid plan on Envío
**When** they paste lines, pick a prefix from the HeroUI Select (catalog-fed, never free text) and tap Enviar
**Then** `POST /api/batches` validates the plan and the prefix against the catalog, applies the prefix with in-batch dedup, and persists the queued lines (no batch size cap)

**Given** an empty or whitespace-only paste
**When** the client taps Enviar
**Then** the request is rejected with an error code and no batch is created

**Given** the owner
**When** they open the send surface
**Then** they can paste, pick a prefix and send exactly like a client — owner batches enter the scheduler flagged for owner priority (route gating admits the owner role to Envío)

**Given** a queued batch
**When** the send worker drains it
**Then** lines go out at the system-controlled interval (not editable by the client) and each line's state updates in Postgres

**Given** Telegram responds with FloodWait
**When** the worker hits it
**Then** it waits the requested duration and retries the same line (no line lost)

**Given** the WebSocket endpoint `/ws`
**When** a client connects
**Then** the cookie handshake authenticates the tenant, a full snapshot arrives first, and subsequent `batch.progress` / `batch.line_sent` events are tenant-scoped — a tab opened mid-batch renders correct state immediately

**Given** the Envío surface
**When** a batch is live
**Then** the progress ring shows % + fraction and the flank shows exactly three metrics (enviadas · en cola, ETA, CC nuevas) — no other stats
**And** navigation is exactly Envío | Historial (bottom nav mobile, header nav desktop)
**And** at idle the surface shows "Pega tus líneas y elige un prefijo."

**Given** a live batch
**When** the client submits more lines
**Then** new lines append to the existing queue (no second batch)

**Given** a dropped WebSocket
**When** it auto-reconnects
**Then** the fresh snapshot reconciles all state silently — no banners, no offline UX

### Story 2.3: Pausar, reanudar y detener con ETA honesto

As a client,
I want to pause, resume or stop my own batch with an honest ETA,
So that I control my send without affecting anyone else.

**Acceptance Criteria:**

**Given** a live batch
**When** the client calls `POST /api/batches/{id}/pause|resume|stop`
**Then** only that client's batch is affected and the resulting `batch.state` event (`idle | sending | paused | stopping`) is the single source of truth — the UI never invents a state and makes no optimistic jumps

**Given** the state machine
**When** state changes arrive
**Then** the pill mirrors it verbatim (Enviando / En pausa / Deteniendo, hidden at idle), controls follow (`sending`→Pausar+Detener, `paused`→Reanudar+Detener, `stopping`→disabled, `idle`→hidden), and the ring switches accent↔warning

**Given** a pause request mid-interval
**When** the worker is sleeping
**Then** the sleep is interrupted instantly (cancelable wait)

**Given** a stop request
**When** it executes
**Then** the remaining queue clears and Detener acts instantly — no confirmation modal

**Given** a live batch
**When** `batch.progress` events arrive
**Then** the ETA shows an honest estimate ("~12 min") recomputed each event; while paused it relabels to "ETA al reanudar"; never a fake-precise countdown

**Given** a `flood.wait` event
**When** it fires
**Then** an amber informational notice appears with live countdown and copy "Telegram pidió esperar N s — reanudamos solos.", self-dismisses on resume, and is never styled as an error
**And** the nav live dot shows success while sending, warning while paused

### Story 2.4: Planificador multi-tenant: round-robin, prioridad owner, intervalo adaptativo

As the owner,
I want all tenants' sends scheduled fairly over the shared channel at a safe adaptive pace,
So that no client monopolizes the account and the account stays safe.

**Acceptance Criteria:**

**Given** multiple clients with live batches
**When** the scheduler assigns send slots
**Then** the channel rotates round-robin across active clients and all in-flight batches advance interleaved — no client monopolizes

**Given** `n` active (non-paused) senders
**When** the interval is computed
**Then** `G = max(G_min, P(n)/n)` with `G_min = 3.0s` (configurable, to be load-tested) and `P(n)` linear from 10s (n=1) to 20s (n≥5); each client gets a turn every `G×n`
**And** paused tenants are excluded from `n`

**Given** the owner sends while clients are active
**When** owner lines enter the rotation
**Then** they jump ahead of the client rotation but take at most 50% of send slots

**Given** repeated FloodWait events
**When** the governor detects them
**Then** `G_min` auto-raises (self-tuning toward the safe band) and every FloodWait broadcasts a global `flood.wait` event so stalled ETAs are explained

**Given** the backend test suite
**When** scheduler tests run
**Then** they cover fairness (round-robin), bounded owner priority, the adaptive formula, and paused-tenant exclusion — all passing

### Story 2.5: Endurecimiento del pipeline de envío

As the owner,
I want the send pipeline to survive bad lines, DB outages and restarts,
So that one failure never blocks all tenants or double-sends.

**Acceptance Criteria:**

**Given** the schema
**When** this story's migration is applied
**Then** the `send_log` table exists (`message_id → tenant, batch, line`)

**Given** a line about to be sent
**When** the worker dispatches it
**Then** the send intent is recorded in `send_log` BEFORE calling Telegram and `message_id` is filled in after — a crash between send and record cannot create orphan replies

**Given** a line that fails to send
**When** it has failed 3 times
**Then** it is marked `failed`, an event is emitted, and the queue continues — retry-forever is gone; one bad line never blocks other tenants

**Given** a line marked `failed`
**When** the client views their queue
**Then** the line is visibly marked as failed and an inline Spanish notice (mapped by `code`) explains it — the batch keeps going

**Given** Postgres becomes unreachable
**When** the worker detects it
**Then** sending stops (fail-stop: no attribution possible = no sends); incoming replies buffer in memory with Telethon `catch_up=True` and flush on DB recovery

**Given** a service restart
**When** lines are found in `sending` state at boot
**Then** they are reconciled against recent outgoing chat messages — confirmed or re-queued, never double-sent

**Given** a client's plan expires mid-batch
**When** the expiry check fires
**Then** remaining queued lines are cancelled while responses to already-sent lines are still attributed and saved

**Given** the running pipeline
**When** sends and FloodWaits occur
**Then** structured logs record per-tenant send counts and FloodWait events

## Epic 3: Captura de respuestas, sesiones e historial

Every bot reply reaches the correct client (`reply_to_msg_id` attribution + send_log), with live Completa/Filtrada views, `CC:` dedup, `.txt` export, Historial (rename, continue with preserved dedup, delete) and the cross-tenant support view for owner/admins.

### Story 3.1: Captura y atribución de respuestas del bot

As a client,
I want every bot reply captured and saved to MY space automatically,
So that my results are mine and complete.

**Acceptance Criteria:**

**Given** the schema
**When** this story's migration is applied
**Then** `capture_sessions` and `responses` tables exist (full revisions + filtered/deduped rows, all tenant-scoped)

**Given** the Telethon client
**When** `cc-core` starts
**Then** `NewMessage` and `MessageEdited` handlers are registered once and capture bot replies

**Given** a client sends a batch with a prefix
**When** no capture session is active for that tenant+prefix
**Then** one is created and bound automatically at batch start (matching legacy `/api/enviar` semantics) and all subsequent attributed responses save to it

**Given** a bot reply with `reply_to_msg_id`
**When** the capture handler processes it
**Then** the id resolves against `send_log` to the exact tenant, batch and line, and the response saves to that tenant's active capture session — never to anyone else's

**Given** an already-captured message that the bot edits
**When** the edit arrives
**Then** `message_id` is preserved so attribution holds, ❌→✅ transitions move the counters, and duplicate edits are deduped (per-message_id state)

**Given** a response containing `CC:` data
**When** extraction runs (port of `extraer_cc`/`RE_CC`, each value truncated at the literal `Status`)
**Then** only session-new CC lines are added to the filtered rows (per-session dedup persisted in Postgres)

**Given** a reply that matches no `send_log` record
**When** it arrives
**Then** it is logged to the unmatched-replies monitoring bucket (ban-guardrail observability)

**Given** a captured response
**When** it is saved
**Then** a tenant-scoped `response.captured` WS event is emitted

**Given** the backend test suite
**When** attribution and isolation tests run
**Then** they cover reply mapping, edits, unmatched replies, and cross-tenant access (which must fail) — all passing

### Story 3.2: Vistas Completa/Filtrada en vivo en Envío

As a client,
I want live Completa and Filtrada views while my batch runs,
So that I watch data land without manual work.

**Acceptance Criteria:**

**Given** the Envío surface
**When** rendered on mobile
**Then** Completa | Filtrada are segmented tabs; on desktop (≥lg) they are two side-by-side panels with COMPLETA / FILTRADA headers — same components, recomposed

**Given** the dual views
**When** responses exist
**Then** rows render console-density (mono 11px, 1px separators, muted timestamp/index left, ellipsized content, ✅ success / ❌ danger glyph right) and each tab/panel shows a live mono count badge — Filtrada's in success green

**Given** a `response.captured` event
**When** it arrives
**Then** the row appends to Completa (and to Filtrada if it carries new deduped CC data) with the "nueva" success-tint highlight, and the ring's CC nuevas metric increments

**Given** a pane scrolled away from the bottom
**When** new rows arrive
**Then** the view stays pinned — auto-scroll only happens if the pane was already at the bottom

**Given** no responses yet
**When** the views render
**Then** Completa shows "Aún no hay respuestas." and Filtrada shows "Aún no hay datos CC: capturados." with counters at 0 — no fake rows

### Story 3.3: Historial: listar, ver detalle, renombrar y eliminar sesiones

As a client,
I want to browse my sessions, rename them and delete the ones I don't need,
So that my history stays organized.

**Acceptance Criteria:**

**Given** `/(client)/sessions`
**When** the client opens it
**Then** their sessions list grouped by prefix, newest first, each row showing the friendly name, a mono sub-line `prefijo · session-id`, and a right badge "En curso" (accent-tint) or "Cerrada" (muted)

**Given** a session row
**When** tapped
**Then** `/(client)/sessions/[id]` opens with the same dual Completa/Filtrada views

**Given** the detail view of a session that is currently live
**When** `response.captured` events arrive
**Then** the view live-follows; navigating to another session stops the follow

**Given** a session row
**When** the client renames it inline
**Then** the new name persists via REST and shows immediately
**And** names are capped at 200 characters

**Given** a session row
**When** the client deletes it
**Then** a confirm modal asks "¿Eliminar esta sesión? No se puede deshacer." and on confirm the session and its rows are gone — content editing does not exist anywhere

**Given** the session bound to a live batch
**When** the client tries to delete it
**Then** the request is rejected with an error code and the UI shows "Detén el lote antes de eliminar esta sesión."

**Given** a client with no sessions
**When** Historial renders
**Then** it shows "Todavía no tienes sesiones. Tu primer lote crea una." with a link to Envío

**Given** any session request
**When** it resolves
**Then** only the requesting tenant's sessions are reachable (isolation)

### Story 3.4: Continuar una sesión con dedup preservado

As a client,
I want to continue a previous session,
So that re-sent data doesn't duplicate my filtered results.

**Acceptance Criteria:**

**Given** a closed session in Historial
**When** the client taps Continuar
**Then** the session reopens as the active capture session, a `session.active` event fires, and Envío binds to it

**Given** the reopened session
**When** new batches send and replies arrive
**Then** the dedup set was preloaded from the session's existing `responses` rows — previously captured CC lines do NOT reappear in Filtrada; only genuinely new data lands, highlighted
**And** new sends append to the same session

**Given** a live batch in progress
**When** the client tries to continue another session
**Then** the request is rejected with an error code and the UI shows "Termina o detén el lote actual antes de continuar otra sesión."

### Story 3.5: Exportar resultados como .txt

As a client,
I want to download my complete and filtered views as .txt files,
So that I use my data outside the platform.

**Acceptance Criteria:**

**Given** a session with responses
**When** the client taps `↓ .txt` on a view
**Then** the backend generates the file on the fly from rows (no cache) and the browser downloads it — one button per view (completa / filtrada)

**Given** the export buttons
**When** shown in Envío or Historial detail
**Then** they work both during a live batch and on closed sessions

**Given** any export request
**When** it resolves
**Then** only the requesting tenant's own sessions are exportable

### Story 3.6: Vista de soporte cross-tenant para owner/admins

As an admin or owner,
I want to view any client's sessions read-only,
So that I can support clients from their own data view.

**Acceptance Criteria:**

**Given** `/admin/tenants/[id]`
**When** an admin or owner opens it
**Then** the target client's sessions list and detail render read-only, reusing the same dual-view component

**Given** a cross-tenant read
**When** it executes
**Then** it goes through the explicit `for_tenant(id)` support path and is audit-logged — the only place tenant isolation is intentionally crossed

**Given** a client
**When** they request `/admin/tenants/[id]`
**Then** middleware redirects them away

**Given** a client with no sessions
**When** the support view renders
**Then** it shows "Este cliente no tiene sesiones."

## Epic 4: Protección operativa de la cuenta (guardarraíl de baneo)

The owner operates the service without ban risk: reply-rate watchdog with global auto-pause, configurable admission control (waiting queue with position), structured logs + FloodWait alerting, `AuthKeyError` re-auth runbook, daily Postgres backup, and pre-launch gates (`G_min` load test, attribution volume test). Direct owner value: the ~0-bans counter-metric becomes operable.

### Story 4.1: Watchdog de respuestas y detección de pérdida de sesión

As the owner,
I want automatic global pause when the bot stops replying or the Telegram session dies,
So that silent failures never burn the shared account.

**Acceptance Criteria:**

**Given** active sending
**When** the reply rate collapses over a sliding window (bot silently blocking the account)
**Then** the watchdog alerts the owner and auto-pauses global sending

**Given** the Telethon client
**When** an `AuthKeyError` or deauthorization is detected
**Then** global sending pauses immediately and the owner is alerted — the trigger for the re-auth runbook

**Given** a watchdog-triggered global pause
**When** the owner has resolved the cause
**Then** resuming is an explicit owner action — never automatic

**Given** watchdog activity
**When** it fires or recovers
**Then** every event is recorded in the structured logs

### Story 4.2: Admission control con cola de espera

As the owner,
I want a configurable cap on concurrent active senders,
So that per-client cadence stays near the 10–20s band instead of degrading everyone.

**Acceptance Criteria:**

**Given** the owner-configurable cap (e.g. 10)
**When** a new batch would exceed it
**Then** the batch enters a FIFO waiting queue instead of degrading every active sender's interval

**Given** a waiting batch
**When** the client views Envío
**Then** they see their queue position — not a dead-slow drip and not a silent stall

**Given** an active sender finishes, stops, or frees a slot
**When** the slot opens
**Then** the next waiting batch starts automatically

**Given** the cap is disabled
**When** batches arrive
**Then** behavior falls back to pure adaptive-interval degradation (Epic 2 semantics)

### Story 4.3: Observabilidad del guardarraíl de baneo

As the owner,
I want FloodWait alerting and send-pattern visibility,
So that the ~0-bans counter-metric is operable, not aspirational.

**Acceptance Criteria:**

**Given** FloodWait events
**When** they exceed a threshold within a window
**Then** the owner is alerted (FloodWait is the leading ban indicator)

**Given** the structured logs
**When** the owner inspects them
**Then** per-tenant send counts, FloodWait events, governor `G_min` raises, and unmatched replies are all queryable

**Given** the unmatched-replies bucket
**When** it grows abnormally
**Then** an alert fires (attribution health is part of the ban guardrail)

### Story 4.4: Preparación de lanzamiento: gates, backups y runbooks

As the owner,
I want the pre-launch gates executed and recovery procedures documented,
So that real clients onboard onto a validated, recoverable service.

**Acceptance Criteria:**

**Given** the staging/production environment
**When** the load test runs
**Then** `G_min = 3.0s` is validated or adjusted based on real FloodWait behavior before onboarding real clients

**Given** real prefix commands
**When** the attribution volume test runs
**Then** the bot-always-replies-with-`reply_to` assumption is validated at volume, with unmatched replies ≈ 0

**Given** the VPS
**When** the backup cron is installed
**Then** `pg_dump` runs daily and produces restorable dumps

**Given** the operations docs
**When** the re-auth runbook is written
**Then** it covers: detect `AuthKeyError` → global pause → re-authenticate ON the VPS → explicit resume

**Given** the launch plan
**When** clients onboard
**Then** ramp-up is gradual over the first weeks (content-pattern ban mitigation) — documented as an operating rule
