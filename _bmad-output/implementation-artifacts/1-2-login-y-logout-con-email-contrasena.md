---
baseline_commit: c15b742
---

# Story 1.2: Login y logout con email + contraseña

Status: done

## Story

As a client,
I want to log in with my email and password,
so that I access my own private space securely.

## Acceptance Criteria

1. **Given** a registered user with a valid plan
   **When** they submit correct credentials at `/login`
   **Then** the password is verified against its argon2id hash, a server-side session row is created, and an httpOnly+Secure+SameSite cookie is set
   **And** they land on their role's home surface

2. **Given** wrong credentials
   **When** the form is submitted
   **Then** an inline field-level error "Correo o contraseña incorrectos." is shown, the email stays filled, and no redirect happens

3. **Given** repeated failed attempts from the same account+IP
   **When** the throttle threshold is exceeded
   **Then** further attempts are rejected temporarily (login throttling)

4. **Given** a blocked account
   **When** it attempts login
   **Then** a blocking notice "Tu cuenta está bloqueada. Escríbenos por WhatsApp o Telegram para reactivarla." is shown with external-channel buttons — never a dead-end

5. **Given** an authenticated user
   **When** they log out
   **Then** the server-side session is revoked and the cookie cleared

6. **Given** an unauthenticated visitor
   **When** they request any protected route
   **Then** Next.js middleware redirects them to `/login`
   **And** `/api/auth/me` returns the session's user and role for authenticated requests

## Tasks / Subtasks

- [x] Task 1: Migration #2 — session token, revocation, blocked flag (AC: 1, 4, 5)
  - [x] Add to `users` model: `is_blocked: Mapped[bool]` (`server_default=sa.false()`, not null) — login reads it (AC4); the admin action that sets it is Story 1.5
  - [x] Add to `auth_sessions` model: `token: Mapped[str]` (`String(64)`, **unique**, indexed — the opaque cookie value, `secrets.token_urlsafe(32)`); `revoked_at: Mapped[datetime | None]` (nullable `timestamptz`)
  - [x] `alembic revision --autogenerate -m "auth session token, revocation, user blocked flag"`; review the file; confirm `down_revision = "282b9bd6744a"`; run `alembic upgrade head`
  - [x] Do NOT add a `plans` table or `expires_at`/`force_password_change` here — expiry is Story 1.4, forced change is Story 1.6 (no tables/columns ahead of need)
- [x] Task 2: Auth service — hashing, sessions, throttle (AC: 1, 2, 3, 5)
  - [x] `backend/app/services/auth.py`: a single module-level `argon2.PasswordHasher()` instance; `hash_password(raw) -> str`, `verify_password(hash, raw) -> bool` (catch `argon2.exceptions.VerifyMismatchError` → `False`; treat any verify exception as failure)
  - [x] `verify_password` must run argon2 on a **dummy hash** when the email is unknown, so the "no such user" path and the "wrong password" path take the same time (no user-enumeration timing oracle)
  - [x] Session helpers (over a repo): `create_session(user)` → insert row with fresh `token`, `expires_at = now + SESSION_TTL` (default 14 days); `get_valid_session(token)` → row where `revoked_at IS NULL AND expires_at > now()`; `revoke_session(token)` → set `revoked_at = now()`
  - [x] Login throttle: in-process counter keyed by `(email_lowercased, client_ip)` — after N failures (default 5) within a window (default 15 min) reject with `429` and code `too_many_attempts` until the window passes. Document that this is per-process and resets on restart (acceptable at MVP single-process scale)
- [x] Task 3: Auth repository + current-user dependency (AC: 1, 6)
  - [x] `backend/app/db/repos/users.py`: `get_by_email(session, email)` (case-insensitive match), `get_active_session_with_user(session, token)` (joins user; returns `None` if revoked/expired)
  - [x] `backend/app/api/deps.py`: `get_current_user` dependency — reads the cookie, looks up the valid session, returns the `User` (with `tenant_id` available for later tenant scoping); raises `401` code `not_authenticated` when absent/invalid. Add `require_role(*roles)` factory now (used by `/api/auth/me` is not gated, but admin stories reuse it)
- [x] Task 4: Auth API router (AC: 1, 2, 3, 4, 5, 6)
  - [x] `backend/app/api/auth.py` (router prefix `/api/auth`), Pydantic v2 schemas (snake_case): `LoginRequest {email, password}`, `MeResponse {id, email, role, tenant_id}`
  - [x] `POST /api/auth/login`: throttle check → lookup user → if `is_blocked` return `403` code `account_blocked` → `verify_password` → on success create session, `response.set_cookie(...)` (see Dev Notes for exact flags), return `MeResponse` + the role's home path; on failure increment throttle counter, return `401` code `invalid_credentials`
  - [x] `POST /api/auth/logout`: revoke the session for the cookie token, `response.delete_cookie(...)`, return `204`
  - [x] `GET /api/auth/me`: via `get_current_user`, return `MeResponse`; `401` when unauthenticated
  - [x] Register the router and a structured exception handler mapping domain errors → `{code, message}` + status (the project's error contract). Wire into `create_app()` in `app/main.py`
- [x] Task 5: Dev seed for a testable user (AC: 1 — no creation UI until Story 1.3)
  - [x] `backend/scripts/seed_user.py` (or a documented one-off): insert a `tenant` + a `user` with an argon2id `password_hash` so login can be verified end-to-end. Keep it dev-only; do NOT build any admin/creation route (that's Story 1.3)
- [x] Task 6: Frontend login page (AC: 1, 2, 4)
  - [x] `frontend/app/login/page.tsx`: HeroUI form (email + password `Input`, submit `Button`); on submit POST `/api/auth/login` via the fetch wrapper; on `200` redirect to the returned home path; on `invalid_credentials` show inline "Correo o contraseña incorrectos." keeping the email filled; on `account_blocked` show the blocking notice + WhatsApp/Telegram buttons; on `429` show a "demasiados intentos" notice
  - [x] `frontend/lib/api.ts`: fetch wrapper — `credentials: "include"`, JSON, parses the `{code, message}` error contract into a typed error the UI maps by `code` (fallback to `message` verbatim)
  - [x] Login page must render OUTSIDE the authenticated chrome (no Navbar/footer from the demo layout) — it is the unauthenticated surface
- [x] Task 7: Middleware + authenticated landing stubs (AC: 1, 6)
  - [x] `frontend/middleware.ts`: redirect unauthenticated requests (no/invalid session cookie) to `/login`; allow `/login` and static assets. Validate by forwarding the cookie to `/api/auth/me` (middleware can't reach the DB) OR gate on cookie presence and let the page's server check finalize — see Dev Notes for the recommended approach and `matcher`
  - [x] Minimal authenticated home stubs so the post-login redirect resolves: client → `/(client)/` (`app/(client)/page.tsx` stub "Envío — próximamente"), admin/owner → `/admin/users` (`app/admin/users/page.tsx` stub). Full surfaces arrive in Stories 2.2 / 1.3 — keep these as placeholders
  - [x] Remove the starter demo routes (`app/about`, `app/blog`, `app/docs`, `app/pricing`) and the demo Navbar links — they are not part of the product and would otherwise be caught by middleware
- [x] Task 8: Regenerate API types + verification gates (AC: 6, all)
  - [x] With the backend running, re-run `npm run generate:api` so `frontend/types/api.ts` includes the new auth schemas (never hand-edit it)
  - [x] Gates green: backend `ruff check .` + `mypy app`; frontend `npm run lint` (eslint) + `npx tsc --noEmit`
  - [x] Manual verification: seed a user → login (happy path lands on role home) → `/api/auth/me` returns the user → logout clears cookie → unauthenticated visit to a protected route redirects to `/login` → wrong password shows inline error → blocked user shows blocking notice → repeated failures hit the throttle

### Review Findings

- [x] [Review][Patch] logout `delete_cookie` missing `secure`/`httponly` flags — `set_cookie` (auth.py:99) sets `httponly=True, secure=settings.cookie_secure` but `delete_cookie` only passes `path`/`samesite`; AC5 requires deletion with the same attributes [backend/app/api/auth.py:181]

## Dev Notes

### ⚠️ Scope rule (inherited from Story 1.1 — still in force)

`_bmad-output/project-context.md` documents the **legacy single-user app** (`core.py`, `app.py`, `auto_sender.py`, `static/`). Those rules (Spanish identifiers, no new deps, 5 env vars, no tests) apply ONLY to the legacy files, which this story **must not touch**. For all new `backend/` and `frontend/` code the architecture wins: **English-only identifiers**; client-facing UI text stays **Spanish (tuteo)**. Hard 🔒 rules still apply everywhere: never read `respuestas/` contents; never commit/print `.env` (root or `backend/`); never touch/delete `anon.session` [Source: 1-1-...md#Scope rule; project-context.md].

### What this story is (and is NOT)

IS: email+password login, server-side sessions (argon2id), httpOnly cookie, logout/revocation, login throttling, blocked-account handling at login, Next.js middleware redirect, `/api/auth/me`.

IS NOT — resist building these (each is its own later story):
- **No `plans` table, no expiry gate** → Story 1.4. The AC's "valid plan" precondition is trivially true until 1.4 adds expiry; do NOT invent a plans table or `expires_at` check here. [Assumption flagged below.]
- **No forced-password-change flag/flow** → Story 1.6.
- **No admin/user-creation UI, no owner bootstrap** → Story 1.3. Use the dev seed (Task 5) to get a testable user.
- **No block/unblock admin action** → Story 1.5. This story only *reads* `is_blocked` at login and adds the column.
- **No Telethon, no scheduler, no batches** → Epic 2.

### Existing code this story builds on (READ before writing)

- `backend/app/db/models.py` — `Tenant`, `User`, `AuthSession` already exist (int PKs, `User.role` String(20) `owner|admin|client`, `User.email` unique, `User.password_hash` String(255), `AuthSession.user_id`/`expires_at`/`created_at`). **Extend** these models; don't recreate. `User` currently has NO `is_blocked`; `AuthSession` has NO `token`/`revoked_at` — Task 1 adds them [Source: backend/app/db/models.py].
- `backend/app/db/base.py` — `Base` (with mandatory `NAMING_CONVENTION`), `engine`, `async_session_factory`, `get_session()` FastAPI dependency (rolls back on exception). Use `get_session` for DB access; do not create a second engine/sessionmaker [Source: backend/app/db/base.py].
- `backend/app/main.py` — `create_app()` factory; lifespan disposes the engine (DB-only — do NOT add Telethon). Register the new auth router and exception handler here, next to `health_router` [Source: backend/app/main.py].
- `backend/app/config.py` — `Settings` (pydantic-settings, reads `backend/.env`), currently only `database_url`. Add new settings here (cookie flags, session TTL, throttle knobs) with sensible defaults; update `backend/.env.example`; never commit `backend/.env` [Source: backend/app/config.py].
- `backend/app/api/health.py` — router pattern reference (`APIRouter(prefix="/api", ...)`). Auth router uses `prefix="/api/auth"` [Source: architecture.md#API Naming Conventions].
- `backend/migrations/versions/282b9bd6744a_initial_...py` — migration #1; new migration's `down_revision = "282b9bd6744a"` [Source: that file].
- `argon2-cffi` is ALREADY a dependency in `backend/pyproject.toml` — do not add it again. No new backend deps required (use stdlib `secrets`, `time`/`datetime`) [Source: backend/pyproject.toml].
- Frontend: HeroUI v3 (`@heroui/react` 3.1.0) + Next.js 16.2.6 + next-themes already wired; `frontend/app/layout.tsx` sets `className="dark"` default surface and the `Providers`. Demo routes (`about/blog/docs/pricing`) and `@/components/navbar` are starter scaffolding to remove. **TanStack Query is NOT installed** — see decision below [Source: frontend/package.json, app/layout.tsx].

### Migration #2 — exact shape

`down_revision = "282b9bd6744a"`. Naming conventions are enforced by `Base.metadata` (`ix_`, `uq_`, `fk_`, `pk_`) — autogenerate emits stable names [Source: backend/app/db/base.py].

- `users`: ADD `is_blocked BOOLEAN NOT NULL DEFAULT false` (model: `mapped_column(server_default=sa.false())`).
- `auth_sessions`: ADD `token VARCHAR(64) NOT NULL` with `uq_auth_sessions_token` (model `unique=True`) — store `secrets.token_urlsafe(32)` (~43 chars); ADD `revoked_at TIMESTAMPTZ NULL`.
- A session is valid iff `revoked_at IS NULL AND expires_at > now()`. The cookie carries only the opaque `token`; the server resolves it — no signing needed because the token is unguessable and DB-backed [Source: architecture.md#Authentication & Security — "server-side sessions in Postgres (revocable on block/expiry)"].

### Cookie flags (exact)

`response.set_cookie(key=SESSION_COOKIE_NAME, value=token, httponly=True, samesite="lax", secure=<settings>, max_age=SESSION_TTL_SECONDS, path="/")`.
- `httponly=True` always (JS must never read it).
- `secure` must be **configurable**: `True` in production (HTTPS via Caddy, Story 1.7), `False` in local dev (plain http on :3000/:8000) — otherwise the cookie is silently dropped in dev and login "works" but no session sticks. Add `cookie_secure: bool = False` to `Settings` (override to `True` in prod `.env`). This is the #1 thing that will look like a mysterious "login does nothing" bug.
- `samesite="lax"` is correct for a same-site app behind one proxy; the form POST and `/api/auth/me` are same-origin through the Next rewrite / Caddy.
- `logout` uses `response.delete_cookie(key=..., path="/", samesite="lax")` with the same attributes so the browser actually clears it.
[Source: architecture.md#Authentication & Security; NFR5.]

### argon2-cffi usage

```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
_ph = PasswordHasher()  # library defaults are the recommended argon2id params
def hash_password(raw: str) -> str: return _ph.hash(raw)
def verify_password(stored_hash: str, raw: str) -> bool:
    try: return _ph.verify(stored_hash, raw)
    except (VerifyMismatchError, InvalidHashError): return False
```
`PasswordHasher()` defaults to **argon2id** — meets NFR5. For the unknown-email path, verify against a precomputed dummy hash to equalize timing (no user enumeration) [Source: architecture.md#Authentication & Security — argon2id; NFR5].

### Login throttling

Architecture: "Login throttling per account+IP" and explicitly leaves the mechanism to the implementer — "`slowapi` or simple in-process counter (implementer's choice within the pattern)" [Source: architecture.md#Authentication & Security; #Nice-to-have]. **Recommendation: in-process counter** (no new dep) — a `dict[(email, ip), (count, window_start)]`. Reject with `429` + `{"code": "too_many_attempts", "message": "Demasiados intentos. Espera unos minutos."}` past the threshold. Note in the code comment that it is per-process / resets on restart — acceptable at single-process MVP scale (one `cc-core`). Get the client IP from the request (behind Caddy in prod, trust `X-Forwarded-For` only if Caddy sets it; in dev use `request.client.host`).

### Error contract & codes (this story introduces them)

HTTP status + `{"code": "<snake_case>", "message": "<Spanish>"}`; `code` is machine-readable, `message` user-facing. Add a domain-exception → handler mapping in `app/main.py` (the pattern every later story reuses). Codes this story defines:
- `invalid_credentials` (401) → "Correo o contraseña incorrectos."
- `account_blocked` (403) → "Tu cuenta está bloqueada. Escríbenos por WhatsApp o Telegram para reactivarla."
- `too_many_attempts` (429) → "Demasiados intentos. Espera unos minutos."
- `not_authenticated` (401) → "No has iniciado sesión." (drives middleware/`/me`)
The frontend maps known `code`s to the Spanish copy above; AC2/AC4 copy strings are verbatim from the UX spec [Source: architecture.md#Format Patterns; EXPERIENCE.md#State Patterns; UX-DR15/UX-DR16].

### Role → home surface map (AC1 "land on their role's home")

- `client` → `/(client)/` (route group root) — Envío. Full surface is Story 2.2; this story ships a stub page so the redirect resolves.
- `admin` / `owner` → `/admin/users`. Full surface is Story 1.3; ship a stub page.
The login response returns the resolved home path so the client redirects without guessing [Source: EXPERIENCE.md#Information Architecture route table; architecture.md project tree].

### Frontend middleware (AC6)

`frontend/middleware.ts` redirects unauthenticated users to `/login`, gates by role, and (later) routes expiry/forced-change. For THIS story implement: unauthenticated → `/login`. Middleware runs on the Edge runtime and **cannot touch Postgres** — two viable approaches:
1. **Recommended:** gate on session-cookie presence in middleware (cheap), then let each protected page/server component finalize via `/api/auth/me` (authoritative). Keeps middleware fast and DB-free.
2. Forward the cookie from middleware to `/api/auth/me` (a `fetch` to the backend) and redirect on `401`. More authoritative but adds a network hop per navigation.
Use approach 1 for the redirect, and rely on `/api/auth/me` for the actual identity/role in pages. `matcher` should exclude `/login`, `/_next/*`, static files, and `/api/*` (the backend owns API auth). Note the Story 1.1 caveat: the dev `/ws` rewrite is flaky but irrelevant here (no WS until Epic 2) [Source: architecture.md#Frontend Architecture — "Next.js middleware redirects unauthenticated/expired users"; 1-1-...md#Dev proxy caveat].

### TanStack Query — decision for this story

The architecture mandates **TanStack Query v5** as the REST data layer [Source: architecture.md#Frontend Architecture]. It is **not yet installed**. Login itself is a single POST + a redirect and does NOT need it. **Recommendation:** keep this story lean — implement `lib/api.ts` (the fetch wrapper + error-contract parsing) now, which every later story uses, and **defer** installing `@tanstack/react-query` + `lib/query-client.ts` to the first story with real client-side list/query state (Story 2.2 / 1.3). If you prefer to establish the provider now, add it minimally — but do not over-build query hooks for a form. Flag this choice in the Completion Notes.

### Blocked-account external buttons (AC4)

The blocking notice reuses the same external-channel buttons as `/expired` (WhatsApp / Telegram). The `/expired` PAGE is Story 1.4 — do NOT build it here. For AC4, render the two contact buttons inline on `/login`. The actual WhatsApp/Telegram links are supplied by Richard at implementation — use a clearly-marked placeholder constant (e.g. in `config/site.ts`) so it is trivially swappable [Source: EXPERIENCE.md#State Patterns "Cuenta bloqueada"; Flow 4 ASSUMPTION on links].

### Tenant scoping (forward-looking, light here)

Every `User` carries `tenant_id`; `get_current_user` should expose it so later stories inject tenant context from the session — handlers must NEVER read `tenant_id` from request bodies. This story has no tenant-scoped data yet, but establish `deps.py` as the single place identity+tenant come from [Source: architecture.md#Tenant Scoping (mandatory); #Enforcement Guidelines].

### Conventions snapshot

- Python: snake_case funcs/vars, PascalCase classes, type hints on all new defs (`disallow_untyped_defs` is on). Pydantic v2 models for every request/response body.
- API: success = direct payload (no `{success,data}` wrapper); errors = `{code, message}`; JSON snake_case end-to-end (generated TS matches — no camelCase mapping layer).
- TypeScript: camelCase vars/functions, PascalCase components/types, component files kebab-case (`login-form.tsx`). TS strict stays on.
- Commits: Conventional Commits with scope, e.g. `feat(backend): auth login/logout`, `feat(frontend): login page + middleware`.
[Source: architecture.md#Code Naming Conventions, #Format Patterns; 1-1-...md#Conventions snapshot.]

### Testing

No gate requires committed tests for this story's ACs, but `backend/tests/` + `conftest.py` already exist and `pytest`/`pytest-asyncio`/`httpx` are dev deps. `test_auth.py` is named in the architecture tree — **optional but recommended** here: cover password hash/verify round-trip, session validity (revoked/expired rejected), throttle threshold, and blocked-login rejection. If added, keep them as standalone pytest files; don't invent a new framework [Source: architecture.md project tree `tests/test_auth.py`; pyproject dev deps].

### Quality gates (must pass before done)

Backend `ruff check .` + `mypy app`; frontend `npm run lint` + `npx tsc --noEmit`. All four green is the definition-of-done gate inherited from Story 1.1 [Source: architecture.md#Enforcement Guidelines; 1-1-...md#Quality gates].

### Previous Story Intelligence (Story 1.1)

- Local Postgres runs in Docker container `cc-pg` (`postgres:16`, db `cc`, `127.0.0.1:5432`); `backend/.env` (gitignored) holds `DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/cc`. Recreate: `docker run -d --name cc-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=cc -p 5432:5432 postgres:16`.
- Migration #1 applied as revision `282b9bd6744a` (tenants, users, auth_sessions + `ix_users_tenant_id`, `ix_auth_sessions_user_id`).
- Frontend boots on Turbopack; :3000 may be occupied by the legacy `app.py` — the new stack is fine on any free port. The frozen legacy `app.py` may still own :8000 locally; stop it or run the new backend on another port when verifying the `/api` proxy end-to-end.
- ESLint flat-config was fixed in 1.1 (`eslint.config.mjs`) — don't regress it.
- Ruff excludes `migrations/versions` (machine-written) — keep new migration files out of style scope.
[Source: 1-1-...md#Debug Log References, #Completion Notes List.]

### Project Structure Notes

New files land in the architecture's prescribed tree [Source: architecture.md#Complete Project Directory Structure]:
```
backend/app/
  services/auth.py        # NEW — argon2id, sessions, throttle
  api/deps.py             # NEW — get_current_user, require_role, tenant ctx
  api/auth.py             # NEW — /api/auth/login|logout|me
  db/repos/users.py       # NEW — user + session queries
  db/models.py            # EXTEND — User.is_blocked; AuthSession.token, revoked_at
  config.py               # EXTEND — cookie/session/throttle settings
  main.py                 # EXTEND — register auth router + exception handler
backend/scripts/seed_user.py   # NEW — dev-only testable user
backend/migrations/versions/<rev>_auth_session_token_*.py  # NEW — migration #2
frontend/
  middleware.ts           # NEW — unauth → /login
  app/login/page.tsx      # NEW — login form (unauthenticated chrome)
  app/(client)/page.tsx   # NEW stub — client home (full = Story 2.2)
  app/admin/users/page.tsx# NEW stub — admin home (full = Story 1.3)
  lib/api.ts              # NEW — fetch wrapper + error contract
  config/site.ts          # EXTEND — WhatsApp/Telegram placeholder links
  types/api.ts            # REGENERATED — never hand-edit
  app/about|blog|docs|pricing  # REMOVE — starter demo routes
```
Variances/notes: `db/repos/` is created now (architecture has it; first repo lands here). `services/` dir is new. Authenticated home pages are intentionally stubs — full surfaces are later stories; this keeps AC1's "land on role home" verifiable without scope creep.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.2] — story statement + ACs
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] — argon2id, httpOnly+Secure+SameSite cookie, server-side revocable sessions, role deps, login throttle, anon.session perms
- [Source: _bmad-output/planning-artifacts/architecture.md#API & Communication Patterns / #Format Patterns / #API Naming Conventions] — REST shapes, error contract `{code, message}`, `/api/auth/login|logout|me`
- [Source: _bmad-output/planning-artifacts/architecture.md#Frontend Architecture] — middleware redirect, session cookie + `/api/me`, TanStack Query data layer
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure] — file locations (`services/auth.py`, `api/deps.py`, `api/auth.py`, `db/repos/`, `middleware.ts`, `app/login`)
- [Source: _bmad-output/planning-artifacts/architecture.md#Tenant Scoping (mandatory) / #Enforcement Guidelines] — tenant context from session, never from body
- [Source: _bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/EXPERIENCE.md#State Patterns, #Voice and Tone, #Information Architecture, Flow 4] — verbatim Spanish copy, route map, blocked/expired behavior
- [Source: epics.md#UX Design Requirements UX-DR15, UX-DR16, UX-DR17] — Spanish microcopy, error/edge states, route map + middleware gates
- [Source: backend/app/db/models.py, db/base.py, main.py, config.py, api/health.py, pyproject.toml] — existing skeleton to extend
- [Source: backend/migrations/versions/282b9bd6744a_initial_tenants_users_auth_sessions.py] — migration #1 (down_revision target)
- [Source: _bmad-output/implementation-artifacts/1-1-inicializar-proyecto-desde-starter-esqueleto-backend.md] — prior-story learnings, local dev setup, gates
- [Source: _bmad-output/project-context.md] — legacy-only scope rule + the three hard 🔒 rules

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (Claude Opus 4.8, 1M context) — BMad Dev Story workflow.

### Debug Log References

- Migration #2 autogenerated as revision `c9296faba8c5` (`down_revision = 282b9bd6744a`); applied to local `cc-pg`. Verified columns via `information_schema`: `auth_sessions.token` (NOT NULL, unique index `ix_auth_sessions_token`), `auth_sessions.revoked_at` (nullable), `users.is_blocked` (NOT NULL boolean).
- Ruff B008 flagged FastAPI's `Depends()`-in-defaults idiom; resolved project-wide via `[tool.ruff.lint.flake8-bugbear] extend-immutable-calls` (Depends/Query/Security) rather than per-line noqa — the pattern recurs every story.
- Backend ACs verified end-to-end through the ASGI stack (httpx `ASGITransport`): wrong-password → 401 `invalid_credentials`; client login → 200 + cookie + `home_path:"/"`; admin login → `home_path:"/admin/users"`; `/api/auth/me` with cookie → 200 (incl. `tenant_id`); logout → 204 then `/me` → 401 `not_authenticated`; blocked user → 403 `account_blocked`; 6th rapid failure → 429 `too_many_attempts`.
- `:8000` is held by the legacy `app.py` locally, so the new backend was run on `:8001` for `openapi-typescript` generation; the dev proxy in `next.config.mjs` still targets `:8000` (correct for normal dev — run the new backend on `:8000` after stopping legacy).
- `next build` deprecation: Next 16.2 prefers a `proxy` file over `middleware`; the story prescribes `frontend/middleware.ts`, which still works (registered as "Proxy (Middleware)"). Kept as specified.

### Completion Notes List

- **Migration #2 only** added `users.is_blocked`, `auth_sessions.token`, `auth_sessions.revoked_at` — no `plans` table, no `expires_at`/`force_password_change` (deferred to Stories 1.4/1.6 per scope rule).
- **Timing-safe login**: unknown email path runs argon2 against a precomputed `DUMMY_HASH` so it costs the same as a real verify (no user-enumeration oracle).
- **Error contract introduced**: `AppError` + a single exception handler in `main.py` renders `{code, message}` + status. Codes defined: `invalid_credentials` (401), `account_blocked` (403), `too_many_attempts` (429), `not_authenticated` (401), `forbidden` (403). Every later story reuses this.
- **Throttle** is an in-process per-`(email, ip)` fixed-window counter (default 5 / 15 min) — documented as per-process / resets on restart (acceptable at single-process MVP scale). Unit-tested.
- **TanStack Query deliberately NOT installed** (per Dev Notes decision): login is a single POST + redirect. Implemented `lib/api.ts` (fetch wrapper + error-contract parsing) which later stories reuse; defer the query provider to the first real list/query story (1.3 / 2.2).
- **`require_role` factory** added in `deps.py` but unused by 1.2's endpoints (`/api/auth/me` is open to any authenticated user) — established now for admin stories.
- **Frontend chrome**: removed the starter demo Navbar/footer from the root layout and deleted demo routes (`about`/`blog`/`docs`/`pricing`) and the demo home (`app/page.tsx`, now the `(client)` route group root); login renders on the bare unauthenticated surface.
- **Blocked-account links** are clearly-marked placeholders in `config/site.ts` (`contact.whatsapp`/`contact.telegram`) — Richard swaps them at deploy.
- **Gates all green**: backend `ruff check .` + `mypy app` (15 files) + `pytest` (8 passed); frontend `eslint` (0 errors/0 warnings — generated `types/api.ts` added to ignores), `tsc --noEmit`, and `next build`.
- **Tests**: `backend/tests/test_auth.py` covers hash/verify round-trip, malformed-hash safety, dummy-hash, and throttle threshold/window/reset/keying (DB-backed flows covered by the ASGI verification above; no committed-test gate required).
- Legacy single-user app (`core.py`/`app.py`/`auto_sender.py`/`static/`) untouched; `respuestas/`, `.env`, and `anon.session` not read/committed/touched (🔒 rules honored).

### File List

**Backend — new**
- `backend/app/services/__init__.py`
- `backend/app/services/auth.py` — argon2id hash/verify (+ DUMMY_HASH), session helpers, `LoginThrottle`
- `backend/app/db/repos/__init__.py`
- `backend/app/db/repos/users.py` — `get_by_email`, `get_active_session_with_user`, `add_session`, `mark_session_revoked`
- `backend/app/api/deps.py` — `get_current_user`, `require_role`
- `backend/app/api/auth.py` — `/api/auth/login|logout|me` + schemas
- `backend/app/errors.py` — `AppError` + error-contract factories
- `backend/scripts/seed_user.py` — dev-only testable user
- `backend/migrations/versions/c9296faba8c5_auth_session_token_revocation_user_.py` — migration #2
- `backend/tests/test_auth.py` — hashing + throttle unit tests

**Backend — modified**
- `backend/app/db/models.py` — `User.is_blocked`; `AuthSession.token`, `AuthSession.revoked_at`
- `backend/app/config.py` — cookie/session/throttle settings + `session_ttl_seconds`
- `backend/app/main.py` — register auth router + `AppError` exception handler
- `backend/pyproject.toml` — ruff bugbear `extend-immutable-calls` for FastAPI `Depends`
- `backend/.env.example` — documented auth/session/throttle env vars

**Frontend — new**
- `frontend/app/login/page.tsx` — HeroUI login form (unauthenticated surface)
- `frontend/app/(client)/page.tsx` — client home stub ("Envío — próximamente")
- `frontend/app/admin/users/page.tsx` — admin home stub
- `frontend/middleware.ts` — unauthenticated → `/login`
- `frontend/lib/api.ts` — fetch wrapper + error-contract parsing

**Frontend — modified**
- `frontend/app/layout.tsx` — stripped demo Navbar/footer chrome; `lang="es"`
- `frontend/config/site.ts` — product name + WhatsApp/Telegram placeholder links
- `frontend/eslint.config.mjs` — ignore generated `types/api.ts`
- `frontend/types/api.ts` — regenerated with auth schemas (machine-generated)

**Frontend — deleted (starter demo)**
- `frontend/app/page.tsx`, `frontend/app/{about,blog,docs,pricing}/{page,layout}.tsx`
- `frontend/components/navbar.tsx`, `frontend/components/counter.tsx`

## Change Log

| Date       | Change                                                                 |
|------------|------------------------------------------------------------------------|
| 2026-06-11 | Story 1.2 drafted (context engine). Status → ready-for-dev.            |
| 2026-06-11 | Story 1.2 implemented: email+password login/logout, server-side revocable sessions (argon2id), httpOnly cookie, login throttle, blocked-account handling, Next.js middleware redirect, `/api/auth/me`, error contract. All gates green. Status → review. |
