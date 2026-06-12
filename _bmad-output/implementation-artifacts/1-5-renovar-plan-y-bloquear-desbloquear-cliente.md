---
baseline_commit: a2d3aa3
---

# Story 1.5: Renovar plan y bloquear/desbloquear cliente

Status: done

## Story

As an admin or owner,
I want to renew plans and block problem clients from the user table,
so that the client lifecycle is manageable without touching the database.

## Acceptance Criteria

1. **Given** a client row in `/admin/users`
   **When** an admin renews the plan (add days or set a new expiration date)
   **Then** the new `expires_at` is persisted and visible in the table

2. **Given** an expired client whose plan was renewed
   **When** they log in again
   **Then** access works normally

3. **Given** a client row
   **When** an admin blocks the client
   **Then** lockout is immediate: server-side sessions are revoked and the next login shows the blocked notice (per Story 1.2)

4. **Given** a blocked client
   **When** an admin unblocks them
   **Then** the client can log in again normally

## Tasks / Subtasks

- [x] Task 1: Error codes + plan service extension (AC: 1, 2)
  - [x] `backend/app/errors.py` — add two factories in the established pattern, under a `# --- Codes this story (1.5) defines ---` section:
    - `def invalid_renewal() -> AppError` → `status_code=400, code="invalid_renewal", message="Indica los días del plan o una fecha de vencimiento futura."` — used when the renew payload provides neither or both fields, or a past/invalid `expires_at`.
    - Reuse the existing `invalid_plan_days()` (400) for a `plan_days` that is `<= 0` or `> PLAN_DAYS_MAX` — do NOT create a new code for that path.
  - [x] `backend/app/services/plans.py` (EXTEND — this is the extension Story 1.4 explicitly reserved: "Story 1.5 extends THIS file with renew/extend and block/unblock"). Keep the module's purity rule where possible; renew/block need the DB, so they take an `AsyncSession` like `services/users.create_account` does (service orchestrates, router maps errors, caller commits):
    - `def compute_renewed_expiry(current: datetime | None, plan_days: int) -> datetime` — pure: `max(datetime.now(UTC), current or now) + timedelta(days=plan_days)`. Anchor on `max(now, current)` so renewing an ACTIVE plan extends it (days stack) and renewing an EXPIRED plan grants days from today (otherwise an admin adding 30 days to a plan that lapsed 60 days ago would produce a still-expired account and AC2 would fail). Document this anchor rule in the docstring.
    - `async def renew_plan(session, user: User, *, plan_days: int | None, expires_at: datetime | None) -> User` — applies exactly one of the two modes: `plan_days` → `compute_renewed_expiry`; `expires_at` → set verbatim (already validated future+aware by the router). Mutates `user.expires_at`, flushes, returns the row. No commit (caller's job).
    - `async def block_user(session, user: User) -> User` — sets `user.is_blocked = True` AND revokes every live auth session of that user via the new repo function (Task 2). Idempotent: blocking an already-blocked user just re-runs the (no-op) revocation. No commit.
    - `async def unblock_user(session, user: User) -> User` — sets `user.is_blocked = False`. Does NOT restore revoked sessions — the client simply logs in again (AC4). Idempotent. No commit.
  - [x] Keep `is_plan_expired` untouched — renew works precisely because login/`get_current_user` re-evaluate that predicate against the new `expires_at`; no other expiry code needs changes for AC2.
- [x] Task 2: Repo — revoke all sessions for a user (AC: 3)
  - [x] `backend/app/db/repos/users.py` — add `async def revoke_all_sessions_for_user(session: AsyncSession, user_id: int) -> None`: UPDATE-style revocation of every `AuthSession` row with `user_id == user_id AND revoked_at IS NULL`, setting `revoked_at = datetime.now(UTC)`. Use a single `update()` statement (`sqlalchemy.update`), not a select-then-loop — one round trip, race-free. The existing `mark_session_revoked` is per-token (logout/expiry path) and stays untouched.
- [x] Task 3: Admin lifecycle endpoints (AC: 1, 2, 3, 4)
  - [x] `backend/app/api/admin.py` (EXTEND) — three POST action routes per the architecture's non-CRUD convention (`POST /api/batches/{id}/pause` style), all gated by the existing module-level `require_admin_or_owner` singleton (do NOT call `require_role(...)` in argument defaults — ruff B008, solved in 1.3):
    - `POST /api/admin/users/{user_id}/renew` → body `RenewPlanRequest(plan_days: int | None = None, expires_at: AwareDatetime | None = None)` → 200 `UserOut`.
      - Use pydantic v2 `AwareDatetime` (from `pydantic`) for `expires_at` so a naive datetime is rejected at the boundary — `users.expires_at` is timestamptz and naive comparisons raise `TypeError` (1.4 lesson).
      - Route validation order: target exists (else `user_not_found()`) → `target.role == "client"` (else `forbidden()` — admins/owners carry no plan, same guard style as `delete_user`'s role check) → exactly one of `plan_days` / `expires_at` provided (else `invalid_renewal()`) → if `plan_days`: positive and `<= PLAN_DAYS_MAX` (else `invalid_plan_days()`) → if `expires_at`: strictly in the future vs `datetime.now(UTC)` (else `invalid_renewal()`).
      - Then `plans_service.renew_plan(...)` → `await session.commit()` → `_to_out(user)`.
    - `POST /api/admin/users/{user_id}/block` → no body → 200 `UserOut`. Target exists → `role == "client"` (else `forbidden()`) → `plans_service.block_user(...)` → commit → `_to_out`.
    - `POST /api/admin/users/{user_id}/unblock` → no body → 200 `UserOut`. Same guards → `plans_service.unblock_user(...)` → commit → `_to_out`.
  - [x] Renewing or unblocking does NOT touch the other flag: a blocked client with a renewed plan stays blocked; an unblocked client with an expired plan still hits `plan_expired` at login. The two gates are independent (login order: password → blocked → expired — see `api/auth.py`).
  - [x] Do NOT add a per-request `is_blocked` check to `get_current_user` — immediate lockout is achieved by revoking sessions AT BLOCK TIME (this task), which was the documented resolution of the refuted 1.4 review finding. The next request with a revoked cookie falls into the existing `401 not_authenticated` branch and middleware sends the user to `/login`; their next login attempt shows the blocked notice (Story 1.2's `account_blocked`, already implemented).
- [x] Task 4: Backend tests (AC: all)
  - [x] `backend/tests/test_admin_lifecycle.py` (NEW) — ASGI integration tests via httpx `ASGITransport` + cookies, `loop_scope="session"`, reusing the shared conftest helpers (`unique_email`, `seed_user`, `login`, `cleanup_users`, `PASSWORD` — extracted to `backend/tests/conftest.py` during the 1.4 review). Cover at minimum:
    - renew with `plan_days` on an active client → 200, `expires_at` in the response moved forward (≈ old expiry + days; assert with a tolerance window, not exact equality).
    - renew an EXPIRED client with `plan_days` (set `expires_at` to the past directly in the DB first) → 200 with future `expires_at` → that client can now `POST /api/auth/login` successfully (AC2 end-to-end).
    - renew with explicit future `expires_at` → 200, persisted verbatim.
    - invalid payloads → neither field / both fields / past `expires_at` → 400 `invalid_renewal`; `plan_days = 0` and `plan_days > 36500` → 400 `invalid_plan_days`.
    - block: client logs in (cookie A) → admin blocks them → 200 with `is_blocked: true` → cookie A on `GET /api/auth/me` → 401 `not_authenticated` (sessions revoked, AC3) → fresh login as that client → 403 `account_blocked`.
    - unblock: admin unblocks → 200 with `is_blocked: false` → client login works (AC4).
    - authorization: target is an admin → 403 `forbidden` on all three actions; unknown `user_id` → 404 `user_not_found`; a client-role caller → 403 `forbidden` (via `require_admin_or_owner`); both admin AND owner actors can renew/block clients (FR4 says "admin or owner").
  - [x] Keep the existing 22 backend tests green — this story must not modify any existing test.
- [x] Task 5: Frontend — row actions + blocked state in the table (AC: 1, 3, 4)
  - [x] `frontend/app/admin/users/page.tsx` (EXTEND). Keep the page's established idioms exactly: TanStack Query + `useMutation`, `invalidateQueries({ queryKey: USERS_KEY })` on success, error routing by `ApiError.code` with the server's Spanish `message` as the displayed text, inline confirm-expansion (the `DeleteAdminAction` pattern) instead of modals.
    - Add an **Estado** column: client rows show "Bloqueado" (danger-toned text or HeroUI `Chip`) when `is_blocked`, otherwise "Activo" (muted); admin rows show "—".
    - Client-row actions (replace the current `"—"` placeholder for client rows; keep `DeleteAdminAction` for admin rows untouched):
      - **Renovar** — inline expansion with two fields: "Días" (`type="number"`, the primary mode) and "Hasta" (`type="date"`, alternative mode), plus Renovar/Cancelar buttons. Exactly one field must be filled; if days is filled send `{plan_days: Number.parseInt(days, 10)}`, else send `{expires_at}` built from the date input as `` `${date}T23:59:59Z` `` (plan valid through the chosen day, UTC — matches `formatExpiry`'s date-only display). On success: collapse, invalidate `USERS_KEY` (the refreshed table shows the new Vence value — AC1's "visible in the table"). On `ApiError`: show `err.message` inline (codes: `invalid_renewal`, `invalid_plan_days`).
      - **Bloquear** / **Desbloquear** — label and target endpoint switch on `u.is_blocked`. Bloquear uses an inline confirm ("¿Bloquear a {email}? Su sesión se cerrará al instante.") mirroring `DeleteAdminAction`; Desbloquear may act on a single press (restoring access is not destructive). Both invalidate `USERS_KEY` on success and surface `ApiError.message` inline on failure.
  - [x] All mutations go through `lib/api.ts` (`api.post`) — no raw fetch. No new components files are required; if the row-action JSX grows unwieldy, extracting a `ClientLifecycleActions` component inside the same file (like `DeleteAdminAction`) is fine.
- [x] Task 6: OpenAPI types + gates + manual verification (AC: all)
  - [x] New endpoints exist → run `npm run generate:api` and commit the regenerated `frontend/types/api.ts` (never hand-edit it). The page keeps its explicit local interfaces per the established idiom.
  - [x] Gates green: backend `ruff check .` + `mypy app` + `pytest` (22 prior + new); frontend `npm run lint` + `npx tsc --noEmit` + `next build`.
  - [x] Manual verification: create a client → renew with días (Vence moves) → renew with fecha (Vence matches) → in psql set `expires_at` to the past → client locked out (`/expired`) → renew from the admin table → client logs in normally (AC2) → block while the client has an open tab → client's next navigation lands on `/login` (revoked session) → their login attempt shows the blocked notice with contact buttons (Story 1.2 UI) → unblock → login works (AC4) → throughout, admin/owner sessions unaffected.

## Dev Notes

### ⚠️ Scope rule (inherited from Stories 1.1–1.4 — still in force)

`_bmad-output/project-context.md` documents the **legacy single-user app** (`core.py`, `app.py`, `auto_sender.py`, `static/`). Those rules (Spanish identifiers, no new deps, 5 env vars) apply ONLY to the legacy files, which this story **must not touch**. For all `backend/`/`frontend/` code the architecture wins: **English-only identifiers**; user-facing UI text stays **Spanish (tuteo)**. Hard 🔒 rules apply everywhere: never read `respuestas/` contents; never commit/print `.env` (root or `backend/`); never touch/delete `anon.session` [Source: project-context.md; 1-4-...md#Scope rule].

### What this story IS (and is NOT)

IS: the two remaining admin lifecycle actions on existing schema — renew/extend a client's plan (FR4) and block/unblock (the write side of the `is_blocked` flag that login has read since Story 1.2) — as three POST action endpoints + row actions in `/admin/users`, with immediate lockout on block via revoke-all-sessions.

IS NOT — resist building these (each is its own later story):

- **No password reset / forced-password-change** → Story 1.6 (the remaining `/admin/users` row action from UX-DR18).
- **No mid-batch cancellation on block or expiry** → there is no scheduler/batch until Epic 2; Story 2.5 handles expiry-mid-batch. Blocking today only needs session revocation.
- **No audit log** — the `audit_log` table doesn't exist yet; audited paths arrive with cross-tenant support (Story 3.6). Don't create tables ahead of need.
- **No new tables, no migration** — `users.is_blocked` (migration #1, Story 1.2) and `users.expires_at` (migration #3, Story 1.3) already exist. This story only WRITES them. Any `alembic revision` here is a mistake.
- **No new deps, no new env vars, no WS** — nothing in this story needs them.
- **No per-request `is_blocked` gate in `get_current_user`** — see the design decision below.

### Existing code this story builds on (READ before writing)

- `backend/app/api/admin.py` — the router to extend. Already has: `require_admin_or_owner` / `require_owner` module-level singletons (B008 pattern), `PLAN_DAYS_MAX = 36500` (reuse it for renew validation), `UserOut` (already carries `expires_at` + `is_blocked` — no schema change needed), `_to_out()`, and `delete_user` showing the target-lookup → role-guard → action → commit shape your three new routes follow [Source: backend/app/api/admin.py].
- `backend/app/services/plans.py` — currently only `is_plan_expired`. Its module docstring says "Story 1.5 extends THIS file with renew/extend and block/unblock" — this is that extension. Clock note: the module deliberately uses the APP clock (`datetime.now(UTC)`), documented exception to the SQL-`now()` convention; keep that for `compute_renewed_expiry` [Source: backend/app/services/plans.py].
- `backend/app/db/repos/users.py` — `mark_session_revoked(session, token)` is per-token; your new `revoke_all_sessions_for_user(session, user_id)` is the per-user bulk variant. `get_user_by_id` (GLOBAL, not tenant-scoped — admin management is cross-tenant by design, see the module note) is the target lookup [Source: backend/app/db/repos/users.py].
- `backend/app/api/auth.py` — `login()` order: throttle → email → password verify → `is_blocked` (→ 403 `account_blocked`) → throttle reset → expiry (→ 403 `plan_expired`) → create session. **This story changes nothing here**: AC2 (renewed → login works) and AC3/AC4 (blocked notice / unblock → login works) fall out of the existing gates reading the values you now write [Source: backend/app/api/auth.py].
- `backend/app/api/deps.py` — `get_current_user` 401s on revoked sessions; that branch is what makes block-time revocation an immediate lockout. Note its 1.4-review lesson: a commit inside the dependency uses its OWN short-lived session. Your routes don't have that problem — route handlers own the request session and `await session.commit()` directly (exactly like `create_user`/`delete_user`) [Source: backend/app/api/deps.py].
- `backend/app/errors.py` — `AppError` + factory pattern; `forbidden`, `user_not_found`, `invalid_plan_days`, `account_blocked` all exist. Add only `invalid_renewal` [Source: backend/app/errors.py].
- `backend/app/services/users.py` — `create_account` shows the service-layer shape (session param, flush not commit, router validates first) [Source: backend/app/services/users.py].
- `frontend/app/admin/users/page.tsx` — the page to extend: `USERS_KEY`/`ME_KEY` query keys, `formatExpiry` (date-only `es` locale — the renewed Vence renders through it), `CreateUserForm` error routing by code, `DeleteAdminAction` inline-confirm pattern (state machine: button → confirm row with pending labels + error line + Cancelar). Mirror these; don't invent new patterns [Source: frontend/app/admin/users/page.tsx].
- `frontend/lib/api.ts` — `api.post<T>(path, body)`, `ApiError` with `.code`/`.status`; global `plan_expired` → `/expired` routing already lives here. No changes expected [Source: frontend/lib/api.ts].
- `frontend/middleware.ts` — already does the authoritative `/me` round-trip for every matched route (1.4). A blocked client's revoked cookie → backend 401 → middleware redirects `/login`. No changes expected [Source: frontend/middleware.ts].

### Design decision: block = revoke sessions, not a per-request flag check

The 1.4 code review explicitly evaluated a request-time `is_blocked` check in `get_current_user` and **refuted it as unnecessary** with this rationale: "Story 1.5 closes it by revoking sessions at block time." Honor that contract:

- `block_user` sets the flag AND bulk-revokes the user's live sessions in the same transaction. The user's very next request hits the existing `auth_session is None → 401 not_authenticated` branch.
- This is cheaper (no extra per-request branch), and the flag's read side stays exactly where it has been since 1.2: `login()`.
- Sequence for an online client being blocked: next request → 401 → middleware → `/login` → login attempt → 403 `account_blocked` → blocked notice with WhatsApp/Telegram buttons (already built, `ContactPanel`). No new frontend lockout work is needed for AC3's "next login shows the blocked notice".

> **⚠️ SUPERSEDED by the 1.5 senior review (2026-06-11):** revoke-at-block-time alone has a race the 1.4 refutation missed — a login concurrent with the block can commit its session AFTER the bulk revoke ran (login's `is_blocked` read sees `False` pre-commit), leaving a live session no revocation ever touches. `get_current_user` now ALSO checks `is_blocked` per request (revokes the surviving session on its own short-lived DB session and raises 401, preserving the documented UX: `/login` → blocked notice). Block-time revocation stays as the primary mechanism; the per-request check is defense-in-depth.

### Renewal semantics (exact, no interpretation room)

- **Two modes, exactly one per request** (FR4: "add days or set a new expiration date"): `plan_days` (int) XOR `expires_at` (datetime). Neither or both → 400 `invalid_renewal`.
- **Add-days anchor:** `max(now(UTC), current_expires_at or now) + days`. Active plan → extends from current expiry (days stack, paying early doesn't lose days). Expired plan → counts from today (AC2: renewal must actually restore access).
- **Set-date mode:** persisted verbatim; must be timezone-aware (`AwareDatetime` at the boundary) and strictly future (a past date is not a "renewal" — it's a lockout, and `block` is the tool for that) → else 400 `invalid_renewal`.
- **Bounds:** `plan_days` positive and `<= PLAN_DAYS_MAX` (36500 — same overflow guard as creation) → else 400 `invalid_plan_days`.
- **Independence:** renew never touches `is_blocked`; block/unblock never touch `expires_at`. Login evaluates both gates in its existing order.
- **Targets:** `role == "client"` only — owner/admin rows carry no plan by construction (`expires_at IS NULL`) and admins are managed exclusively by the owner (1.3). Non-client target → 403 `forbidden` on all three actions.
- All datetimes UTC/timestamptz; compare with `datetime.now(UTC)`; never strip tzinfo (1.4 lesson: naive comparison raises `TypeError`).

### API contract (new endpoints)

| Method/Path | Body | Success | Errors |
|---|---|---|---|
| `POST /api/admin/users/{user_id}/renew` | `{"plan_days": 30}` XOR `{"expires_at": "2026-08-01T23:59:59Z"}` | 200 `UserOut` | 401/403 auth · 404 `user_not_found` · 403 `forbidden` (non-client target) · 400 `invalid_renewal` · 400 `invalid_plan_days` |
| `POST /api/admin/users/{user_id}/block` | — | 200 `UserOut` | 401/403 auth · 404 `user_not_found` · 403 `forbidden` |
| `POST /api/admin/users/{user_id}/unblock` | — | 200 `UserOut` | 401/403 auth · 404 `user_not_found` · 403 `forbidden` |

Returning the updated `UserOut` (not 204) gives the UI the fresh `expires_at`/`is_blocked` without an extra fetch; the page still invalidates `USERS_KEY` to refresh the whole table — both are consistent with the architecture's "success: direct payload" format rule. Action-suffix POST routes follow the architecture's non-CRUD convention (`/api/batches/{id}/pause`) [Source: architecture.md#API Naming Conventions, #Format Patterns].

### Frontend notes (UX-DR18)

- `/admin/users` row actions per UX: "renovar plan, bloquear, resetear contraseña" — this story ships renovar + bloquear/desbloquear; resetear is 1.6. Keep the actions cell compact; HeroUI `Table` already in place [Source: epics.md#UX-DR18; EXPERIENCE.md#Key Screen Inventory].
- Inline confirm-expansion (the `DeleteAdminAction` idiom), NOT modals — modal stacks max one level (UX-DR10) and the existing page has zero modals; stay consistent.
- Spanish tuteo microcopy; show the backend's `message` for known codes rather than re-stating copy (the page's established error idiom).
- Blocked visibility: admins need to SEE who is blocked to unblock them — hence the Estado column. `UserOut.is_blocked` is already in every list response; no backend change needed for it.
- HeroUI v3 (`@heroui/react@3.1.0`) API differs from v2 docs — mirror this page's existing imports/components (`Button`, `Alert`, `Table`, `TextField`, `Input`, `Label`, `FieldError`); don't import v2-only names (1.3/1.4 lesson).

### Conventions snapshot (unchanged from 1.1–1.4)

- Python: snake_case, type hints on every new def (`disallow_untyped_defs`), pydantic v2 request models. Errors = `{code, message}`, snake_case codes, Spanish messages; JSON snake_case end-to-end.
- TypeScript: strict; never hand-edit `types/api.ts` (regenerate via `npm run generate:api`).
- Commits: Conventional Commits with scope — e.g. `feat(backend,frontend): story 1.5 plan renewal + block/unblock`.
[Source: architecture.md#Code Naming Conventions, #Format Patterns; 1-4-...md#Conventions snapshot.]

### Testing

`backend/tests/` has 22 passing tests; `conftest.py` carries the shared helpers (`PASSWORD`, `unique_email`, `seed_user`, `login`, `cleanup_users`) extracted in the 1.4 review — import them, don't redefine. Idiom: ASGI transport + cookie jar, self-seeding, self-cleaning, direct DB mutation for state setup (e.g. pushing `expires_at` into the past), `loop_scope="session"` everywhere (function-scoped loops break the shared engine pool — "another operation is in progress"). The critical tests are the two lockout round-trips: block → live cookie 401s + relogin 403 `account_blocked` (AC3) and expired → renew → login 200 (AC2). No new test frameworks [Source: backend/tests/conftest.py; 1-4-...md#Testing].

### Quality gates (must pass before done)

Backend `ruff check .` + `mypy app` + `pytest`; frontend `npm run lint` + `npx tsc --noEmit` + `next build`. All green is the definition-of-done gate inherited from 1.1–1.4 [Source: architecture.md#Enforcement Guidelines].

### Previous Story Intelligence (Story 1.4)

- Local Postgres in Docker `cc-pg` (`postgres:16`, db `cc`, `127.0.0.1:5432`); recreate: `docker run -d --name cc-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=cc -p 5432:5432 postgres:16`. `backend/.env` has the `DATABASE_URL`.
- `:8000` may be held by the **legacy `app.py`** — stop it before running the new backend (dev proxy + middleware `/me` target `:8000`).
- `cookie_secure=False` in local dev or the cookie is silently dropped.
- ruff B008: never call a dependency factory in argument defaults — module-level singletons (`require_admin_or_owner` already exists; reuse it).
- Post-action navigation on auth-state changes uses full `window.location.assign(...)` — NOT needed for this story's admin mutations (they don't change the actor's own auth state; query invalidation suffices).
- The 1.4 review hardened `middleware.ts` (prefetch skip, fail-open outside `/admin`, stale-cookie cleanup) and `lib/api.ts` (global `plan_expired` routing) — don't regress those while touching the frontend.
- Owner bootstrap: `python -m scripts.bootstrap_owner <email> <password>`; client seeding via the admin UI or `scripts/seed_user.py`.
- Manual psql/browser walkthroughs can be covered equivalently by ASGI tests; the live browser pass is for the reviewer [Source: 1-4-...md#Previous Story Intelligence, #Dev Agent Record].

### Git Intelligence

Pattern from a2d3aa3/29f5191/fb5eb0c: branch-per-story (`story/1.X-...`) merged to main; one feature commit + one review-fixes commit, scoped `feat(backend,frontend): story 1.X ...`. Start from current main (a2d3aa3 — 1.4 merged). Files this story extends were all touched in 1.3/1.4 with the same idioms: `api/admin.py` (1.3), `services/plans.py` + `errors.py` (1.4), `admin/users/page.tsx` (1.3).

### Project Structure Notes

New/changed files land in the architecture's prescribed tree [Source: architecture.md#Complete Project Directory Structure]:

```
backend/app/
  services/plans.py        # EXTEND — compute_renewed_expiry, renew_plan, block_user, unblock_user
  db/repos/users.py        # EXTEND — revoke_all_sessions_for_user (bulk UPDATE)
  api/admin.py             # EXTEND — POST /users/{id}/renew|block|unblock
  errors.py                # EXTEND — invalid_renewal
backend/tests/test_admin_lifecycle.py  # NEW
frontend/
  app/admin/users/page.tsx # EXTEND — Estado column, Renovar + Bloquear/Desbloquear row actions
  types/api.ts             # REGENERATED (npm run generate:api) — never hand-edit
```

No migration, no new deps, no settings changes, no middleware/auth changes. Architecture maps `services/plans.py` to "expiry checks, renew/extend, block" — this story completes that file's intended scope.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.5] — story statement + 4 ACs (authoritative); FR4 (renew), FR2 (admin manages clients: renew/block)
- [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements UX-DR18] — admin user table row actions: renovar plan, bloquear
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] — "server-side sessions in Postgres (revocable on block/expiry)"
- [Source: _bmad-output/planning-artifacts/architecture.md#API Naming Conventions] — non-CRUD actions as POST verb suffix
- [Source: _bmad-output/planning-artifacts/architecture.md#Format Patterns] — `{code, message}` contract, snake_case codes, Spanish messages
- [Source: _bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/EXPERIENCE.md#Flow 3/Flow 4] — admin renews → client's next login works; "Bloquear — immediate lockout"
- [Source: backend/app/api/admin.py, services/plans.py, services/users.py, db/repos/users.py, api/auth.py, api/deps.py, errors.py] — existing code this story extends
- [Source: frontend/app/admin/users/page.tsx, lib/api.ts, middleware.ts] — proven idioms + extension points
- [Source: _bmad-output/implementation-artifacts/1-4-expiracion-automatica-del-plan-y-lockout-total.md] — prior-story learnings; the refuted-finding contract this story fulfills (revoke at block time)
- [Source: _bmad-output/project-context.md] — legacy-only scope rule + the three hard 🔒 rules

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Opus 4.8, 1M context) — BMad dev-story workflow.

### Debug Log References

- ruff `import order`: placed `from sqlalchemy.ext.asyncio import AsyncSession` in the third-party group BEFORE the `app.*` first-party group in `services/plans.py` (matches `repos/users.py` grouping).
- ruff F401: removed unused `unique_email` import from the new test module.
- eslint `--fix`: applied padding-line/prettier formatting to `page.tsx` (warnings only, no errors).
- `npm run generate:api` requires the backend live on `:8000`; ran the new `uvicorn app.main:app` on `:8000` (port was free — legacy `app.py` not running), regenerated, then stopped it.

### Completion Notes List

- **Task 1** — `errors.py`: added `invalid_renewal()` (400) under the 1.5 section; reused `invalid_plan_days()` for bad `plan_days`. `services/plans.py` extended (the reservation 1.4 documented): `compute_renewed_expiry` (pure, `max(now, current)+days` anchor — stacks for active plans, counts-from-today for expired → AC2), `renew_plan`/`block_user`/`unblock_user` (take `AsyncSession`, flush, no commit). `is_plan_expired` untouched — AC2 falls out of login re-reading the new `expires_at`.
- **Task 2** — `repos/users.py`: `revoke_all_sessions_for_user` as a single `update()` over `user_id == … AND revoked_at IS NULL` (race-free, one round trip). `mark_session_revoked` untouched.
- **Task 3** — `api/admin.py`: three POST action routes (`/users/{id}/renew|block|unblock`), all gated by the existing `require_admin_or_owner` singleton. `RenewPlanRequest` uses pydantic `AwareDatetime` (rejects naive at the boundary — 1.4 timestamptz lesson). Validation order per spec; shared `_require_client_target` helper (404 unknown / 403 non-client). No per-request `is_blocked` gate in `get_current_user` (honored the refuted-1.4-finding contract: lockout = revoke at block time).
- **Task 4** — `tests/test_admin_lifecycle.py` (NEW, 12 tests): renew active (stack), renew expired → relogin 200 (AC2 e2e), explicit future date verbatim, invalid payloads (neither/both/past → `invalid_renewal`; 0 / >36500 → `invalid_plan_days`), block → live cookie 401 + relogin 403 `account_blocked` (AC3), unblock → relogin 200 (AC4), block idempotent, gate independence (renew leaves `is_blocked`), authz (admin target 403, unknown 404, client caller 403, both admin+owner can act). Full suite: **34 passed** (22 prior unchanged + 12 new).
- **Task 5** — `app/admin/users/page.tsx`: added **Estado** column (client → Bloqueado/Activo, staff → —) and `ClientLifecycleActions` (= `RenewAction` inline Días/​Hasta expansion + `BlockAction` inline-confirm Bloquear / single-press Desbloquear), all via `api.post`, `invalidateQueries(USERS_KEY)` on success, `ApiError.message` inline on failure — mirrors `DeleteAdminAction`. Date mode sends `${date}T23:59:59Z`.
- **Task 6** — regenerated `types/api.ts` (`npm run generate:api`, +169 lines for the new endpoints; not hand-edited). Gates green: backend `ruff` + `mypy app` + `pytest` (34); frontend `eslint` (0 errors) + `tsc --noEmit` + `next build`. Manual psql/browser walkthrough deferred to reviewer (covered equivalently by the two ASGI lockout round-trips — 1.4 precedent).

### Senior Code Review Fixes (2026-06-11)

10 findings (6 correctness + 4 cleanup), all fixed; gates re-run green (backend ruff + mypy + pytest **36** passed; frontend eslint + tsc + next build):

1. **Block bypass race (security)** — `get_current_user` never checked `is_blocked`; a login racing the block commits a session AFTER the bulk revoke → blocked client keeps access. Fixed: per-request `is_blocked` check in `deps.py` (revokes the surviving session via the extracted `_revoke_own_session`, raises 401 — same UX as block-time revocation). Supersedes the 1.4 "no per-request gate" contract; rationale documented in Dev Notes. Test: `test_blocked_flag_alone_locks_out_live_session`.
2. **Renew-by-date could silently SHORTEN an active plan** — only "strictly future" was validated. Fixed: new 400 `renewal_would_shorten` (`errors.py`) when `expires_at < target.expires_at`. Test: `test_renew_date_cannot_shorten_active_plan`.
3. **Timezone day-shift in the date mode** — hardcoded `${date}T23:59:59Z` vs local-tz `formatExpiry` rendered the day AFTER the one picked (UTC+ admins). Fixed: payload now `new Date(\`${date}T23:59:59\`).toISOString()` (end-of-day in the admin's timezone).
4. **`parseInt` mis-parse on days inputs** — `'1e2'` → 1 day, `'30.5'` → 30, NaN → JSON null. Fixed: `isPositiveInt` digit-gate in both `RenewAction.submit` and `CreateUserForm.onSubmit`; payloads use `Number(...)`.
5. **OverflowError → 500** — a stored far-future `expires_at` (e.g. 9999-12-31) made a later add-days renewal exceed `datetime.max`. Fixed: upper bound `now + PLAN_DAYS_MAX days` on the date mode (mirrors the days bound). Covered in `test_renew_invalid_payloads`.
6. **Lost-update race on concurrent renewals** — ORM read-modify-write without a lock. Fixed: `_require_client_target` fetches `FOR UPDATE` (`get_user_by_id(..., for_update=True)`).
7. **Duplicated plan-days bounds** (create vs renew) → single `_validate_plan_days` helper.
8. **block/unblock copy-paste across both layers** → one `plans_service.set_blocked(session, user, *, blocked)` + shared `_set_blocked` route body.
9. **Exactly-one-mode invariant enforced 3× (incl. dead `else: raise AssertionError`)** → route branches on the present mode and resolves `new_expiry` itself; `renew_plan(session, user, new_expiry)` is now a plain setter; assert + dead branch deleted. (Also: `compute_renewed_expiry` reads the clock once.)
10. **`ctx` fixture duplicated verbatim** in `test_admin_users.py` / `test_admin_lifecycle.py` → promoted to `tests/conftest.py`.

### File List

- `backend/app/errors.py` (M) — `invalid_renewal()`, `renewal_would_shorten()` (review fix 2)
- `backend/app/services/plans.py` (M) — `compute_renewed_expiry`, `renew_plan` (resolved-expiry setter), `set_blocked` (replaces `block_user`/`unblock_user`)
- `backend/app/db/repos/users.py` (M) — `revoke_all_sessions_for_user`; `get_user_by_id` gains `for_update`
- `backend/app/api/admin.py` (M) — `RenewPlanRequest`, `_validate_plan_days`, `_require_client_target` (FOR UPDATE), `_set_blocked`, POST `renew`/`block`/`unblock`
- `backend/app/api/deps.py` (M) — per-request `is_blocked` gate + `_revoke_own_session` (review fix 1)
- `backend/tests/conftest.py` (M) — shared `ctx` fixture (review fix 10)
- `backend/tests/test_admin_users.py` (M) — local `ctx` removed (uses conftest's)
- `backend/tests/test_admin_lifecycle.py` (A) — 14 integration tests (12 + 2 review)
- `frontend/app/admin/users/page.tsx` (M) — Estado column + `ClientLifecycleActions`/`RenewAction`/`BlockAction`; `isPositiveInt` + local end-of-day date (review fixes 3–4)
- `frontend/types/api.ts` (M) — regenerated (new endpoints)

## Change Log

| Date       | Change                                                      |
|------------|-------------------------------------------------------------|
| 2026-06-11 | Story 1.5 drafted (context engine). Status → ready-for-dev. |
| 2026-06-11 | Implemented renew + block/unblock (backend + frontend), 12 new tests (34 total green), gates green. Status → review. |
| 2026-06-11 | Senior code review: 10 findings fixed (block-bypass race → per-request `is_blocked` gate, `renewal_would_shorten`, tz-correct date mode, integer-gated days inputs, overflow bound, `FOR UPDATE` on lifecycle targets, dedup/simplification). 36 tests green, all gates green. |
