---
baseline_commit: 24cc87a
---

# Story 1.3: Alta manual de clientes y gestión de roles

Status: review

## Story

As an admin or owner,
I want to create client accounts manually and have roles enforced everywhere,
so that only paying clients access the service.

## Acceptance Criteria

1. **Given** a fresh deployment
   **When** the owner bootstrap (env/CLI seed) runs
   **Then** the owner account exists and can log in

2. **Given** an admin or owner on `/admin/users`
   **When** they create a client with email, initial password and plan days
   **Then** the client account exists with role `client` and can log in immediately

3. **Given** an existing client email
   **When** an admin tries to create a duplicate
   **Then** the API returns an error code and the UI shows "Ya existe un cliente con ese email."

4. **Given** a logged-in client
   **When** they request any `/admin` route
   **Then** middleware redirects them away — no "blocked" screen is rendered

5. **Given** a logged-in admin
   **When** they view `/admin/users`
   **Then** they see and manage only clients — admin accounts are not manageable by admins

6. **Given** the owner on `/admin/users`
   **When** they create or remove an admin
   **Then** the change takes effect — owner is the only role that manages admins
   **And** the empty table state shows "Todavía no hay clientes."

## Tasks / Subtasks

- [x] Task 1: Migration #3 — `users.expires_at` (plan expiry column) (AC: 2)
  - [x] Add to `User` model (`backend/app/db/models.py`): `expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)` — the client's plan expiry. **Nullable**: owner/admin rows carry no plan; only `client` rows get an `expires_at`. Place it near `is_blocked` with a comment "plan expiry; set at client creation = now + plan_days. Enforcement/lockout is Story 1.4."
  - [x] `cd backend && alembic revision --autogenerate -m "user plan expiry (expires_at)"`; review the file; confirm `down_revision = "c9296faba8c5"`; `alembic upgrade head`
  - [x] Do NOT add a separate `plans` table, `force_password_change`, block/unblock UI, or the `/expired` page here — those are Stories 1.4 (expiry enforcement + `/expired`), 1.5 (renew/block), 1.6 (forced change). One column only, no schema ahead of need.
- [x] Task 2: Owner bootstrap script (AC: 1)
  - [x] `backend/scripts/bootstrap_owner.py` — idempotent, dev/deploy-only (NOT a product route). Reads `OWNER_EMAIL` + `OWNER_PASSWORD` from the process env, with `argv[1]`/`argv[2]` overrides. Lowercases the email (canonical storage); if a user with that email exists, update its password + ensure `role="owner"` (idempotent re-run); else create a **new tenant** + the owner `User` (`role="owner"`, `expires_at=None`). Print the result. Mirror `seed_user.py`'s structure (it stays for dev *client* seeding) [Source: backend/scripts/seed_user.py].
  - [x] Document `OWNER_EMAIL`/`OWNER_PASSWORD` in `backend/.env.example` as deploy-time bootstrap vars (commented; not loaded by `Settings`). Do NOT add them to `app/config.py` `Settings` — the script reads `os.environ` directly so owner credentials never live in the app-wide settings object.
- [x] Task 3: Repo + service for user management (AC: 2, 3, 5, 6)
  - [x] Extend `backend/app/db/repos/users.py`: `create_tenant(session, name) -> Tenant`; `create_user(session, *, tenant_id, email, password_hash, role, expires_at) -> User` (insert + flush); `list_by_roles(session, roles: Sequence[str]) -> list[User]` (`WHERE role IN :roles ORDER BY id`, **global — NOT tenant-scoped**, see Dev Notes); `get_user_by_id(session, user_id) -> User | None`; `delete_user(session, user) -> None`. Reuse existing `get_by_email` (case-insensitive) for the duplicate check.
  - [x] `backend/app/services/users.py` (NEW) — `create_account(session, *, email, password, role, plan_days) -> User`: lowercase email → `get_by_email` duplicate check (raise `email_taken` if hit) → `create_tenant(name=email)` (tenant-per-user) → `expires_at = now(UTC) + timedelta(days=plan_days)` **only when `role == "client"`** else `None` → `create_user(... password_hash=hash_password(password) ...)`. Multi-step, so it lives in the service, not the router (routers never orchestrate ORM directly) [Source: architecture.md#Structure Patterns].
- [x] Task 4: Error code `email_taken` (AC: 3)
  - [x] Add to `backend/app/errors.py`: `def email_taken() -> AppError` → `status_code=409, code="email_taken", message="Ya existe un cliente con ese email."` (verbatim per AC3). The existing `forbidden()` (403) covers role-violation rejections; reuse it.
- [x] Task 5: Admin API router (AC: 2, 3, 5, 6)
  - [x] `backend/app/api/admin.py` (NEW), `APIRouter(prefix="/api/admin", tags=["admin"])`. Pydantic v2 schemas (snake_case): `CreateUserRequest {email: str, password: str, role: str = "client", plan_days: int | None = None}`, `UserOut {id, email, role, tenant_id, expires_at: datetime | None, is_blocked: bool}`, `UserListResponse {items: list[UserOut], total: int}`.
  - [x] `GET /api/admin/users` — dep `require_role("admin", "owner")`. **admin caller → `list_by_roles(["client"])`; owner caller → `list_by_roles(["client", "admin"])`** (owner manages admins too; never lists other owners). Return `UserListResponse`.
  - [x] `POST /api/admin/users` — dep `require_role("admin", "owner")`. Authorization rules enforced **server-side** (the security boundary — never trust the UI):
    - `role` must be `"client"` or `"admin"`; anything else → `forbidden()`.
    - Creating `role="admin"` is **owner-only** → if caller is `admin`, raise `forbidden()` (AC5/AC6).
    - For `role="client"`: `plan_days` is required and must be a positive int → else a `400` validation error (`invalid_plan_days` code, message "Indica los días del plan."). For `role="admin"`: ignore/null `plan_days`.
    - Call `services.users.create_account(...)`, `await session.commit()`, return the created `UserOut` (201).
  - [x] `DELETE /api/admin/users/{user_id}` — dep `require_role("owner")` (**owner-only**, AC6 "remove an admin"). Look up by id; if absent → `404` (`user_not_found`); if target role is **not** `"admin"` → `forbidden()` (1.3 only removes admins; client removal/block is Story 1.5; never let the owner delete themselves or another owner). `delete_user`, commit, return `204`. (User→Tenant has `cascade="all, delete-orphan"` only from Tenant side; deleting the `User` row is enough — its empty tenant may be left orphaned, which is acceptable at MVP; note it in Completion Notes.)
  - [x] Register the router in `create_app()` (`app/main.py`) next to `auth_router` [Source: backend/app/main.py].
- [x] Task 6: Frontend data layer — install TanStack Query v5 (AC: 2, 3, 5, 6)
  - [x] `npm install @tanstack/react-query@5` in `frontend/` (the architecture-mandated REST data layer, deferred from Story 1.2 to "the first real list/query surface" — that is this story) [Source: 1-2-...md#TanStack Query decision; architecture.md#Frontend Architecture].
  - [x] `frontend/lib/query-client.ts` (NEW) — export a `makeQueryClient()` / singleton `QueryClient` with sane defaults (e.g. `staleTime: 30_000`, `retry: 1`).
  - [x] Extend `frontend/app/providers.tsx` — wrap children in `<QueryClientProvider client={...}>` **inside** the existing `NextThemesProvider`. Keep it a client component (`"use client"` already present).
- [x] Task 7: Admin route role-gate in middleware (AC: 4)
  - [x] Extend `frontend/middleware.ts`: it currently only checks cookie **presence**. For requests whose path starts with `/admin`, additionally resolve the role authoritatively by forwarding the session cookie to the backend `GET /api/auth/me` (middleware can't reach Postgres). On `401` → redirect `/login`; on role `"client"` → redirect `/` (AC4: redirected away, **no blocked screen**); `admin`/`owner` → `NextResponse.next()`. Non-`/admin` protected paths keep the cheap cookie-presence gate. See Dev Notes for the exact fetch pattern (absolute URL from `request.nextUrl.origin`, forward `cookie` header). Keep the existing `matcher`.
- [x] Task 8: Admin users page — real surface (AC: 2, 3, 5, 6)
  - [x] Replace the stub `frontend/app/admin/users/page.tsx` with the real management surface using HeroUI v3 `Table` (UX-DR18) + TanStack Query. **Verify component/prop names against the installed `@heroui/react` 3.1.0** — v3 API differs from older docs; mirror the import/idiom style already used in `app/login/page.tsx` (`TextField`/`Label`/`Input`/`FieldError`/`Button`/`Form`/`Alert`) [Source: frontend/app/login/page.tsx].
  - [x] `useQuery(['admin-users'], () => api.get<UserListResponse>('/api/admin/users'))` renders the table (columns: email, role, plan expiry for clients, actions). **Empty state** (no rows) shows "Todavía no hay clientes." (verbatim, AC6) in the table's empty slot [Source: EXPERIENCE.md#empty-admin-table].
  - [x] **Crear cliente** action → modal/inline form (email, password, **plan days** number) → `useMutation` POST `/api/admin/users` `{role:"client", plan_days}`; on success invalidate `['admin-users']`; on `ApiError` `email_taken` show "Ya existe un cliente con ese email." inline on the email field (AC3); on `invalid_plan_days` show its message.
  - [x] **Role-conditional UI**: read the current user's role via `useQuery(['me'], () => api.get('/api/auth/me'))`. When role is `owner`, also show admin rows and a **Crear admin** action (POST `{role:"admin"}`) and a **Eliminar** action per admin row (DELETE `/api/admin/users/{id}` → confirm modal "¿Eliminar este admin?" → invalidate). When role is `admin`, neither admin rows nor admin actions appear (defense-in-depth; the API already filters/forbids). UI gating is cosmetic — the server is the boundary [Source: architecture.md#Enforcement Guidelines].
  - [x] Minimal admin chrome: a header with a **Cerrar sesión** button calling `POST /api/auth/logout` then `window.location.assign('/login')` (reuse the 1.2 logout endpoint) — optional polish, not an AC; keep it lean.
- [x] Task 9: Regenerate API types + verification gates (AC: all)
  - [x] With the backend running, re-run `npm run generate:api` so `frontend/types/api.ts` includes the new admin schemas (never hand-edit it) [Source: 1-1-...md; 1-2-...md].
  - [x] Gates green: backend `ruff check .` + `mypy app`; frontend `npm run lint` (eslint) + `npx tsc --noEmit` + `next build`.
  - [x] Manual verification: bootstrap owner → owner logs in → create a client (email+password+plan days) → that client logs in immediately and lands on `/` → duplicate email shows "Ya existe un cliente con ese email." → as admin, GET shows only clients and no admin actions → as owner, create+delete an admin → log in as a client and hit `/admin/users` → redirected to `/` (no blocked screen) → empty table shows "Todavía no hay clientes."

## Dev Notes

### ⚠️ Scope rule (inherited from Stories 1.1 / 1.2 — still in force)

`_bmad-output/project-context.md` documents the **legacy single-user app** (`core.py`, `app.py`, `auto_sender.py`, `static/`). Those rules (Spanish identifiers, no new deps, 5 env vars, no tests) apply ONLY to the legacy files, which this story **must not touch**. For all new `backend/`/`frontend/` code the architecture wins: **English-only identifiers**; client/admin-facing UI text stays **Spanish (tuteo)**. Hard 🔒 rules apply everywhere: never read `respuestas/` contents; never commit/print `.env` (root or `backend/`); never touch/delete `anon.session` [Source: project-context.md; 1-2-...md#Scope rule].

### What this story IS (and is NOT)

IS: owner bootstrap script; admin/owner create clients (email + password + plan days → `role=client`, `expires_at` set); duplicate-email rejection; owner-only admin create/remove; role-filtered user listing; middleware role-gate so clients can't reach `/admin`; the real `/admin/users` HeroUI table; TanStack Query installed as the REST data layer.

IS NOT — resist building these (each is its own later story):
- **No plan-expiry enforcement / lockout / `/expired` page** → Story 1.4. We only *store* `expires_at`; nothing reads it yet. AC2's "can log in immediately" is trivially true because no expiry gate exists at auth time until 1.4. Do NOT add an expiry check to login here.
- **No `plans` table** — store the plan as a single `users.expires_at` column (1:1 with the user; a join table adds nothing at MVP). The architecture's table list names `plans`, but the project rule "no tables ahead of need" + MVP 1:1 cardinality favors the column. [Decision flagged — see end.]
- **No renew/extend, no block/unblock** → Story 1.5 (`services/plans.py`).
- **No password reset / forced-password-change** → Story 1.6.
- **No `/admin/prefixes`, no `/admin/tenants/[id]` support view** → Stories 2.1 / 3.6.
- **No Telethon, scheduler, batches, sessions** → Epics 2/3.

### Existing code this story builds on (READ before writing)

- `backend/app/db/models.py` — `User` has `id, tenant_id, email (unique, 320), password_hash, role (String(20) owner|admin|client), is_blocked, created_at, updated_at`. **Add `expires_at` only.** `Tenant` has `id, name (200), created_at, updated_at` and `users` relationship with `cascade="all, delete-orphan"`. Don't recreate models [Source: backend/app/db/models.py].
- `backend/app/db/repos/users.py` — has `get_by_email` (case-insensitive, returns first), `get_active_session_with_user`, `add_session`, `mark_session_revoked`. **Extend** with the create/list/get/delete methods. Follow the existing async `select()`/`execute()` style [Source: backend/app/db/repos/users.py].
- `backend/app/services/auth.py` — `hash_password(raw)` is here; import it in `services/users.py`. Don't duplicate hashing [Source: backend/app/services/auth.py].
- `backend/app/api/deps.py` — `require_role(*roles)` factory **already exists and is unused** — this is its first consumer. `get_current_user` returns the `User` (with `.role`, `.tenant_id`). Use `require_role("admin","owner")` / `require_role("owner")` as route deps [Source: backend/app/api/deps.py].
- `backend/app/errors.py` — `AppError` + factories (`invalid_credentials`, `account_blocked`, `forbidden`, `not_authenticated`, …). Add `email_taken` (and `invalid_plan_days`, `user_not_found`) following the same factory pattern [Source: backend/app/errors.py].
- `backend/app/main.py` — `create_app()` registers routers + the single `AppError` handler. Register `admin_router` here. The handler already renders `{code, message}` + status for any `AppError` you raise [Source: backend/app/main.py].
- `backend/scripts/seed_user.py` — the pattern to mirror for `bootstrap_owner.py` (async session, tenant reuse/create, idempotent upsert). Keep `seed_user.py` for dev client seeding [Source: backend/scripts/seed_user.py].
- Migration chain: `282b9bd6744a` (#1) → `c9296faba8c5` (#2). Migration #3 `down_revision = "c9296faba8c5"`. Ruff excludes `migrations/versions/` — keep migration files out of style scope [Source: backend/migrations/versions/; 1-2-...md].
- Frontend: `app/providers.tsx` (NextThemesProvider — extend with QueryClientProvider), `app/admin/users/page.tsx` (stub to replace), `app/(client)/page.tsx` (client home stub — leave), `lib/api.ts` (`api.get`/`api.post`, `ApiError` with `.code`/`.status` — reuse; **add `api.delete` if needed** for the DELETE call, or call `api`-style with method DELETE), `middleware.ts` (extend), `config/site.ts`. `@heroui/react` 3.1.0, Next 16.2.6, React 19. **TanStack Query NOT yet installed** [Source: frontend/lib/api.ts, middleware.ts, app/providers.tsx, package.json].

### Migration #3 — exact shape

`down_revision = "c9296faba8c5"`. Single change: `users` ADD `expires_at TIMESTAMPTZ NULL` (model `mapped_column(DateTime(timezone=True), nullable=True)`). Naming conventions are enforced by `Base.metadata` — autogenerate emits stable names. Review the autogenerated file before applying; confirm it contains ONLY the `add_column` (no spurious drops) [Source: backend/app/db/base.py; 1-2-...md#Migration #2].

### Tenant model: one tenant per user (decision)

`users.tenant_id` is `NOT NULL`, so every created user needs a tenant. **Create a fresh `Tenant` per user** (client, admin, and the bootstrap owner) — `name = email` for traceability. Rationale: clients require their own tenant for the strict data isolation of Epics 2/3 (sessions/batches are tenant-scoped); admins/owner own no tenant data but still need the NOT-NULL FK, so a 1:1 personal tenant is the uniform, simplest model (no special "staff tenant" concept). Do NOT reuse one shared tenant across clients — that would break isolation later [Source: epics.md#Data & persistence "every tenant-owned table carries tenant_id"; architecture.md#Tenant Scoping].

### Admin user-management is GLOBAL, not tenant-scoped (critical — don't break it)

The mandatory tenant-scoping rule (`every repository method takes tenant context`) governs **client-owned data** (sessions, batches, responses). **User management by admin/owner is inherently cross-tenant**: an admin manages *all* clients regardless of tenant. So `list_by_roles` / `get_user_by_id` / `delete_user` here are **global, role-filtered queries** — do NOT inject `tenant_id` into them or you'll only ever see the admin's own (empty) tenant. This is distinct from, and not to be confused with, the audited `for_tenant(id)` support path for *session data* (Story 3.6). The authorization boundary for these endpoints is the `require_role` dependency, not a tenant filter [Source: architecture.md#Tenant Scoping (mandatory); epics.md FR2/FR20].

### Authorization matrix (server-enforced — the UI only mirrors it)

| Action | client | admin | owner |
|---|---|---|---|
| `GET /api/admin/users` | ✗ (redirected) | ✓ sees clients only | ✓ sees clients + admins |
| `POST` create `client` | ✗ | ✓ | ✓ |
| `POST` create `admin` | ✗ | ✗ `forbidden` | ✓ |
| `DELETE` (admin target) | ✗ | ✗ `forbidden` | ✓ |

Never derive the actor's role from the request body — only from `require_role`/`get_current_user` (the session). Handlers must not read `role`/`tenant_id` from the body [Source: architecture.md#Enforcement Guidelines; #Tenant Scoping].

### Middleware role-gate (AC4) — exact approach

UX-DR17 makes Next.js middleware the role-gate. The current `middleware.ts` only checks cookie presence (Story 1.2). Extend it so `/admin/*` resolves the role authoritatively (the Edge/Node middleware can't touch Postgres, so it asks the backend):

```ts
// inside middleware(), after the cookie-presence check passes:
if (request.nextUrl.pathname.startsWith("/admin")) {
  const meUrl = new URL("/api/auth/me", request.nextUrl.origin);
  const res = await fetch(meUrl, {
    headers: { cookie: request.headers.get("cookie") ?? "" },
  });
  if (res.status === 401) return NextResponse.redirect(new URL("/login", request.url));
  const me = await res.json();
  if (me.role === "client") return NextResponse.redirect(new URL("/", request.url)); // AC4
}
return NextResponse.next();
```

Notes: the `/api/*` rewrite (dev) / Caddy (prod) routes `/api/auth/me` to the backend, so the absolute-origin fetch works in both. Forward the inbound `cookie` header so the backend sees the session. This adds one round-trip per `/admin` navigation — negligible for low-traffic admin surfaces. The page/server components still call `/api/auth/me` for the role-conditional UI, so this is belt-and-suspenders, not the only check [Source: EXPERIENCE.md UX-DR17; 1-2-...md#Frontend middleware; architecture.md#Frontend Architecture].

### Owner bootstrap (AC1)

A run-once (idempotent) script, NOT an API route — there is no UI to create the first owner. Read `OWNER_EMAIL`/`OWNER_PASSWORD` from `os.environ` (argv overrides for local use), lowercase the email, upsert the owner + a fresh tenant. On deploy (Story 1.7) this runs on the VPS. Keep owner credentials OUT of `app/config.py Settings` (read env directly in the script) so they aren't loaded into the app process. Document the two vars in `backend/.env.example` (commented). Re-running must not error or duplicate [Source: epics.md Story 1.3 AC1; backend/scripts/seed_user.py pattern].

### Frontend: TanStack Query is now in scope

Story 1.2 deliberately deferred installing `@tanstack/react-query` to "the first real list/query story (1.3/2.2)". This is it. Install v5, add `lib/query-client.ts`, and wrap `providers.tsx`. Use `useQuery` for the user list + `/api/auth/me`, `useMutation` (with `queryClient.invalidateQueries(['admin-users'])`) for create/delete. Cache-key convention is array-style `['admin-users']` / `['me']` [Source: 1-2-...md#TanStack Query decision; architecture.md#State Management "cache keys ['batches', id] array convention"].

### HeroUI v3 caveat

`@heroui/react@3.1.0` is a **v3** release — its `Table`/`Dialog`/`Modal`/`NumberField` APIs differ from v2 docs and from most online examples. Before coding the table/modal, check the actual exports of the installed package (or context7 for HeroUI v3) and mirror the component idiom already proven in `app/login/page.tsx` (`Form`/`TextField`/`Label`/`Input`/`FieldError`/`Button`/`Alert`). Don't import v2-only component names. UX-DR18: reuse the same theme — no separate admin theme; admin tables must stay operable on a phone (responsive, no special mobile design) [Source: frontend/app/login/page.tsx; epics.md UX-DR18, UX-DR20].

### Error contract & codes

Reuse the established `AppError` → `{code, message}` handler. Codes this story adds: `email_taken` (409, "Ya existe un cliente con ese email."), `invalid_plan_days` (400, "Indica los días del plan."), `user_not_found` (404, "Usuario no encontrado."). Reuse `forbidden` (403) for role violations. The frontend maps known `code`s to copy via `ApiError.code` (fallback to `message`) — same pattern as the login page's `COPY` map [Source: backend/app/errors.py; frontend/app/login/page.tsx; architecture.md#Format Patterns].

### Conventions snapshot (unchanged from 1.1/1.2)

- Python: snake_case funcs/vars, PascalCase classes, type hints on every new def (`disallow_untyped_defs` on), Pydantic v2 for every request/response body. API success = direct payload (lists = `{items, total}`); errors = `{code, message}`; JSON snake_case end-to-end (generated TS matches — no camelCase mapping). `Depends()` in defaults is allowed (ruff bugbear `extend-immutable-calls` already configured).
- TypeScript: camelCase vars/functions, PascalCase components/types, component files kebab-case; TS strict on; never hand-edit `types/api.ts`.
- Commits: Conventional Commits with scope — e.g. `feat(backend): admin user management + roles`, `feat(frontend): admin users table + role gate`.
[Source: architecture.md#Code Naming Conventions, #Format Patterns; 1-2-...md#Conventions snapshot.]

### Testing

No committed-test gate is required, but `backend/tests/` + `conftest.py` exist and `pytest`/`pytest-asyncio`/`httpx` are dev deps. **Recommended** `backend/tests/test_admin_users.py` (architecture names `test_tenant_isolation.py` for cross-tenant — relevant later): cover create-client (role `client`, `expires_at` populated, fresh tenant), duplicate email → `email_taken`, admin creating an admin → `forbidden`, admin `GET` excludes admins / owner `GET` includes them, owner deletes an admin, owner deleting a non-admin → `forbidden`. Drive through the ASGI stack with httpx `ASGITransport` + a logged-in cookie, as Story 1.2 did. Keep them standalone pytest files; don't invent a new framework [Source: 1-2-...md#Debug Log References; architecture.md project tree].

### Quality gates (must pass before done)

Backend `ruff check .` + `mypy app`; frontend `npm run lint` + `npx tsc --noEmit` + `next build`. All green is the definition-of-done gate inherited from 1.1/1.2 [Source: architecture.md#Enforcement Guidelines; 1-1-...md, 1-2-...md#Quality gates].

### Previous Story Intelligence (Story 1.2)

- Local Postgres in Docker `cc-pg` (`postgres:16`, db `cc`, `127.0.0.1:5432`); `backend/.env` holds `DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/cc`. Recreate: `docker run -d --name cc-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=cc -p 5432:5432 postgres:16`.
- `:8000` is held locally by the **legacy `app.py`** — stop it or run the new backend on another port (1.2 used `:8001` for `openapi-typescript` generation; the dev proxy in `next.config.mjs`/`.js` targets `:8000`). Run the new backend on `:8000` after stopping legacy for the `/api` proxy + middleware `/me` fetch to work end-to-end.
- Error contract + `AppError` handler are live; `require_role` exists unused (this story's first use). `cookie_secure=False` in local dev (else the cookie is dropped and "login does nothing"). Throttle is in-process.
- ESLint flat-config (`eslint.config.mjs`) and `types/api.ts` in eslint ignores were set in 1.2 — don't regress. Next 16.2 registers `middleware.ts` as "Proxy (Middleware)"; it still works as specified.
- Login does a **full** `window.location.assign(home_path)` so middleware re-reads the new cookie — do the same for any post-action navigation [Source: 1-2-...md#Debug Log References, #Completion Notes].

### Project Structure Notes

New/changed files land in the architecture's prescribed tree [Source: architecture.md#Complete Project Directory Structure]:
```
backend/app/
  api/admin.py            # NEW — /api/admin/users (GET, POST, DELETE)
  services/users.py       # NEW — create_account (tenant + user + expiry)
  db/repos/users.py       # EXTEND — create_tenant, create_user, list_by_roles, get_user_by_id, delete_user
  db/models.py            # EXTEND — User.expires_at
  errors.py               # EXTEND — email_taken, invalid_plan_days, user_not_found
  main.py                 # EXTEND — register admin_router
backend/scripts/bootstrap_owner.py   # NEW — idempotent owner seed (env/CLI)
backend/migrations/versions/<rev>_user_plan_expiry.py  # NEW — migration #3
backend/.env.example      # EXTEND — OWNER_EMAIL/OWNER_PASSWORD (commented)
backend/tests/test_admin_users.py    # NEW (recommended)
frontend/
  app/admin/users/page.tsx  # REPLACE stub — real HeroUI Table + create/delete
  app/providers.tsx         # EXTEND — QueryClientProvider
  middleware.ts             # EXTEND — /admin role gate via /api/auth/me
  lib/query-client.ts       # NEW — QueryClient
  lib/api.ts                # EXTEND if needed — api.delete helper
  types/api.ts              # REGENERATED — never hand-edit
  package.json              # +@tanstack/react-query
```
Variances: `services/users.py` is new (architecture lists `services/auth|plans|exports` — user-creation orchestration fits a thin `users` service; alternatively fold into `services/plans.py`, but that file is 1.4/1.5's). `api/admin.py` matches the tree exactly. The `plans` table from the tree is intentionally replaced by `users.expires_at` (see decision above).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.3] — story statement + 6 ACs (authoritative); FR1/FR2/FR8
- [Source: _bmad-output/planning-artifacts/epics.md#Data & persistence, #Auth & security] — tenant-per-client, argon2id, roles by FastAPI deps, no flat files
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] — roles enforced by deps on every route
- [Source: _bmad-output/planning-artifacts/architecture.md#Naming Patterns / #Format Patterns] — `/api/admin/users`, `{items,total}`, `{code,message}`, snake_case end-to-end
- [Source: _bmad-output/planning-artifacts/architecture.md#Tenant Scoping (mandatory) / #Enforcement Guidelines] — role gate boundary, no body-derived tenant/role, global-vs-scoped distinction
- [Source: _bmad-output/planning-artifacts/architecture.md#Frontend Architecture / #State Management] — TanStack Query v5 data layer, middleware role gates, array cache keys
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure] — `api/admin.py`, `services/`, `db/repos/users.py`, `middleware.ts`, `app/admin/users`
- [Source: _bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/EXPERIENCE.md] — `/admin/users` table (UX-DR18), empty state "Todavía no hay clientes.", Flow 3 alta, permission-denied middleware redirect (no blocked screen), Spanish tuteo
- [Source: epics.md#UX Design Requirements UX-DR17, UX-DR18, UX-DR20] — middleware role gates, admin Table reusing theme, accessibility floor
- [Source: backend/app/db/models.py, db/repos/users.py, services/auth.py, api/deps.py, errors.py, main.py, scripts/seed_user.py] — existing skeleton to extend
- [Source: backend/migrations/versions/c9296faba8c5_*] — migration #2 (down_revision target)
- [Source: frontend/app/login/page.tsx, lib/api.ts, middleware.ts, app/providers.tsx, package.json] — proven idioms + extension points
- [Source: _bmad-output/implementation-artifacts/1-2-login-y-logout-con-email-contrasena.md] — prior-story learnings (TanStack deferral, dev setup, gates, require_role)
- [Source: _bmad-output/project-context.md] — legacy-only scope rule + the three hard 🔒 rules

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (Claude Code, bmad-dev-story workflow)

### Debug Log References

- **Migration #3**: `alembic revision --autogenerate` → `05348659d1b6` (down_revision `c9296faba8c5`); reviewed — single `add_column('users','expires_at', DateTime(tz), nullable=True)`, no spurious drops; `alembic upgrade head` applied; column verified `timestamp with time zone`, `is_nullable=YES`.
- **Owner bootstrap**: ran `python -m scripts.bootstrap_owner owner@cc.local …` twice → `created` then `updated` (idempotent), owner id=4.
- **ruff B008** on `Depends(require_role(...))` (factory call in arg defaults): hoisted to module-level singletons `require_admin_or_owner` / `require_owner` in `api/admin.py`. ruff + mypy(app) green.
- **pytest async / shared engine**: function-scoped event loops broke the SQLAlchemy async engine pool (`another operation is in progress`). Fixed by pinning the fixture + all tests to `loop_scope="session"` (pytest-asyncio 1.4). 17/17 backend tests pass.
- **eslint jsx-a11y/aria-role**: a `role="client"|"admin"` prop on `<CreateUserForm>` was misread as an invalid ARIA role → renamed the prop to `kind`. lint + `tsc --noEmit` + `next build` all green.
- **API smoke test** (curl, backend on :8000): owner login 200; create client → role=client, expires_at populated, fresh tenant; client logs in (home_path `/`); duplicate → 409 `email_taken`; missing plan_days → 400 `invalid_plan_days`; admin lists clients only; admin→create admin 403 `forbidden`; owner lists clients+admins; owner create+delete admin 204. (Admin→DELETE 403 confirmed by `test_admin_delete_is_forbidden_for_admin_actor`; the curl run returned 401 only because that admin's own row had just been deleted — test-ordering artifact, not a defect.)

### Completion Notes List

- ✅ AC1 — `scripts/bootstrap_owner.py` (idempotent, env+argv, reads `os.environ` directly so owner creds never enter `Settings`); owner seeds + logs in. Vars documented in `backend/.env.example` (commented).
- ✅ AC2 — `POST /api/admin/users` creates a `client` with `expires_at = now + plan_days` and its own fresh tenant; client logs in immediately (no expiry gate at login — that is Story 1.4).
- ✅ AC3 — duplicate email → 409 `email_taken`; UI maps the code to "Ya existe un cliente con ese email." inline on the email field.
- ✅ AC4 — `middleware.ts` resolves `/admin/*` role via `GET /api/auth/me` (forwards the session cookie); `client` → redirect `/` (no blocked screen), 401 → `/login`, admin/owner pass.
- ✅ AC5 — admin lists clients only and cannot create/delete admins (server-enforced `forbidden`); UI hides admin rows/actions for non-owners (cosmetic; server is the boundary).
- ✅ AC6 — owner lists clients+admins and can create/remove admins; empty table renders "Todavía no hay clientes." Owner never lists/deletes other owners; DELETE only targets admins (client removal is Story 1.5).
- User management is GLOBAL/cross-tenant by design (`list_by_roles`/`get_user_by_id`/`delete_user` carry no tenant filter) — the `require_role` dep is the boundary. Documented in `db/repos/users.py`.
- **Orphan tenant note**: deleting an admin leaves its now-empty personal tenant row orphaned (acceptable at MVP, as specified).
- TanStack Query v5 installed as the REST data layer (`lib/query-client.ts`, wrapped in `providers.tsx` inside `NextThemesProvider`); cache keys `['admin-users']` / `['me']`.
- HeroUI v3 `Table` is react-aria-components-based — used `Table.Header/Column/Body/Row/Cell` with `Table.Body renderEmptyState`. Create forms are inline (story permits "modal/inline"); delete uses an inline confirm ("¿Eliminar este admin?") — avoids the v3 Modal overlay-state plumbing.
- `types/api.ts` regenerated via `npm run generate:api` (now includes the admin schemas); never hand-edited.
- Recommended tests added: `backend/tests/test_admin_users.py` (8 ASGI integration tests, self-seeding + self-cleaning).
- **Gates green**: backend `ruff check .` + `mypy app` + `pytest` (17 passed); frontend `eslint` + `tsc --noEmit` + `next build`. Legacy app untouched.

### File List

**Backend (new):**
- `backend/app/api/admin.py`
- `backend/app/services/users.py`
- `backend/scripts/bootstrap_owner.py`
- `backend/migrations/versions/05348659d1b6_user_plan_expiry_expires_at.py`
- `backend/tests/test_admin_users.py`

**Backend (modified):**
- `backend/app/db/models.py` — `User.expires_at`
- `backend/app/db/repos/users.py` — `create_tenant`, `create_user`, `list_by_roles`, `get_user_by_id`, `delete_user`
- `backend/app/errors.py` — `email_taken`, `invalid_plan_days`, `user_not_found`
- `backend/app/main.py` — register `admin_router`
- `backend/.env.example` — `OWNER_EMAIL`/`OWNER_PASSWORD` (commented)

**Frontend (new):**
- `frontend/lib/query-client.ts`

**Frontend (modified):**
- `frontend/app/admin/users/page.tsx` — real management surface (replaces stub)
- `frontend/app/providers.tsx` — `QueryClientProvider`
- `frontend/middleware.ts` — `/admin` role gate
- `frontend/lib/api.ts` — `api.delete`
- `frontend/package.json` — `@tanstack/react-query@5`
- `frontend/types/api.ts` — regenerated

## Change Log

| Date       | Change                                                       |
|------------|--------------------------------------------------------------|
| 2026-06-11 | Story 1.3 drafted (context engine). Status → ready-for-dev.  |
| 2026-06-11 | Story 1.3 implemented: migration #3 (`expires_at`), owner bootstrap, admin user-management API + roles, middleware role gate, TanStack Query + `/admin/users` surface. Gates green; 17 backend tests pass. Status → review. |
