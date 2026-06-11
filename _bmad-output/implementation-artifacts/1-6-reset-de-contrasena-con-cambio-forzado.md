---
baseline_commit: 8c0acf4
---

# Story 1.6: Reset de contraseña con cambio forzado

Status: done

## Story

As an admin or owner,
I want to reset a client's password to a one-time temp password,
so that I can restore access without email infrastructure.

## Acceptance Criteria

1. **Given** a client row in `/admin/users`
   **When** an admin triggers a password reset
   **Then** the system generates a secure random temporary password, shows it exactly once on screen, and stores only its argon2id hash
   **And** the account is flagged for forced password change

2. **Given** a client flagged for forced change
   **When** they log in with the temp password
   **Then** the only reachable screen is "Elige una contraseña nueva para continuar" — middleware blocks every other route and API except the change-password action

3. **Given** the forced-change screen
   **When** the client sets a new password
   **Then** the flag clears and they land on their normal home surface

## Tasks / Subtasks

- [x] Task 1: Migration #4 + model — `must_change_password` flag (AC: 1)
  - [x] `backend/app/db/models.py` — add to `User`, right after `is_blocked` (same idiom):
    ```python
    # Set by the admin password-reset action (Story 1.6); read at auth time.
    # While True, get_current_user 403s everything except change-password.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), nullable=False
    )
    ```
  - [x] `alembic revision -m "user must_change_password flag"` (autogenerate or hand-write following `05348659d1b6_user_plan_expiry_expires_at.py`): `op.add_column('users', sa.Column('must_change_password', sa.Boolean(), server_default=sa.false(), nullable=False))`; downgrade drops it. `down_revision` = current head (`alembic heads` to confirm — should be the 1.3 migration `05348659d1b6`). Run `alembic upgrade head` against the dev DB.
  - [x] This is the ONLY schema change. No new tables (no `audit_log` — that's 3.6).
- [x] Task 2: Error codes (AC: 2, 3)
  - [x] `backend/app/errors.py` — add under a `# --- Codes this story (1.6) defines ---` section:
    - `def password_change_required() -> AppError` → `status_code=403, code="password_change_required", message="Elige una contraseña nueva para continuar."` — raised by `get_current_user` for every gated route/API while the flag is set; middleware and `lib/api.ts` route on this code.
    - `def password_reuse() -> AppError` → `status_code=400, code="password_reuse", message="Elige una contraseña distinta a la temporal."` — the new password must not equal the current (temp) one, or the "one-time" property dies.
- [x] Task 3: Auth service + deps — flag gate (AC: 2)
  - [x] `backend/app/services/auth.py` — add `def generate_temp_password() -> str: return secrets.token_urlsafe(12)` (16 url-safe chars, ~96 bits — `secrets` is already imported). Do NOT log or persist the plaintext anywhere; it exists only in the reset response.
  - [x] `backend/app/api/deps.py` — refactor, do not duplicate: extract the current body of `get_current_user` (cookie → session → blocked check → expiry check) into `async def _resolve_session_user(request, session) -> User`. Then:
    - `get_current_user` = `_resolve_session_user(...)` + new final gate: `if user.must_change_password: raise password_change_required()`. Order stays blocked → expired → flag (a blocked or expired client must keep seeing those states, not the change screen).
    - `async def get_current_user_allow_pending_password(request, session=Depends(get_session)) -> User` = `_resolve_session_user(...)` only (no flag gate). Used EXCLUSIVELY by the change-password endpoint — the single hole the architecture mandates ("flag on user; middleware blocks everything except the change-password endpoint").
    - Unlike the blocked/expired branches, the flag gate does NOT revoke the session (the session is legitimate — the user must be able to complete the change with it) and the 403 is repeatable (NOT one-shot like `plan_expired`), so middleware/prefetch consumption is harmless.
  - [x] `logout` keeps working while flagged — it reads the cookie directly, no `get_current_user`. Verify, don't change.
- [x] Task 4: Reset endpoint (admin) + change-password endpoint (auth) (AC: 1, 2, 3)
  - [x] `backend/app/api/admin.py` — `POST /api/admin/users/{user_id}/reset-password`, gated by the module-level `require_admin_or_owner` singleton (B008 pattern), following the 1.5 lifecycle-route shape:
    - `class ResetPasswordResponse(BaseModel): temp_password: str`
    - Body: `target = await _require_client_target(session, user_id)` (existing helper — 404 unknown / 403 non-client / `FOR UPDATE`) → `temp = auth_service.generate_temp_password()` → `target.password_hash = auth_service.hash_password(temp)` → `target.must_change_password = True` → `await users_repo.revoke_all_sessions_for_user(session, target.id)` → `await session.commit()` → `return ResetPasswordResponse(temp_password=temp)`.
    - Session revocation at reset time mirrors block (1.5): any live session dies instantly and the client's next access is a fresh login with the temp password (AC2's entry point). Works on blocked/expired clients too — no special casing; their login gates still apply in the existing order.
    - The plaintext appears ONLY in this one response body — never in `UserOut`, logs, or the DB.
  - [x] `backend/app/api/auth.py` — `POST /api/auth/change-password`:
    - `class ChangePasswordRequest(BaseModel)` with `new_password: str` + a `field_validator` enforcing min length 8 (mirror `CreateUserRequest._password_length` — a short password is a 422, same boundary contract as creation).
    - `class ChangePasswordResponse(BaseModel): home_path: str`
    - Depends on `get_current_user_allow_pending_password` (NOT `get_current_user` — that would 403 the only allowed action).
    - Body: `if not user.must_change_password: raise forbidden()` (this endpoint serves ONLY the forced flow — a voluntary change without current-password verification would be a session-hijack escalation; out of MVP scope) → `if auth_service.verify_password(user.password_hash, body.new_password): raise password_reuse()` → `user.password_hash = auth_service.hash_password(body.new_password)` → `user.must_change_password = False` → `await session.commit()` → `return ChangePasswordResponse(home_path=_home_path_for(user.role))`.
    - Do NOT revoke any session here: the CURRENT session stays alive (the user continues straight to their home surface, AC3 — no re-login), and no other session can exist (reset already bulk-revoked them all).
  - [x] `backend/app/api/auth.py` — `login()`: ONE change — the success response's `home_path` becomes `"/change-password"` when `user.must_change_password` else `_home_path_for(user.role)`. Gate order is untouched (throttle → email → password → blocked → throttle-reset → expired → session): the flag never blocks login itself — a flagged user logging in with the temp password gets a normal session whose every subsequent request is gated by deps (Task 3). The existing login page already navigates to `res.home_path`, so NO login-page change is needed.
- [x] Task 5: Middleware + api client routing (AC: 2)
  - [x] `frontend/middleware.ts` — in the existing 403 branch (which already parses the body for `plan_expired`), add: `if (body?.code === "password_change_required")` → if `request.nextUrl.pathname === "/change-password"` return `NextResponse.next()` (the one allowed page — prevents a redirect loop), else redirect to `/change-password` **keeping the cookie** (the session is valid; deleting it would strand the user at /login). `/change-password` stays INSIDE the matcher (no exclusion edit): a no-cookie visitor hitting it still bounces to `/login` via the first branch, and a non-flagged 200 user just renders the page (its POST will 403 `forbidden` — harmless edge).
  - [x] Note the 403 ordering in middleware: check `plan_expired` first (existing), then `password_change_required`, then fall through to `staleSessionRedirect()`. Unlike `plan_expired`, do NOT delete the cookie.
  - [x] `frontend/lib/api.ts` — alongside the existing global `plan_expired` routing, add: `403` + `code === "password_change_required"` + `window.location.pathname !== "/change-password"` → `window.location.assign("/change-password")`. Covers any client-side API call a flagged user's open tab still fires.
- [x] Task 6: Frontend — `/change-password` page + Resetear row action (AC: 1, 2, 3)
  - [x] `frontend/app/change-password/page.tsx` (NEW) — mirror `login/page.tsx` exactly (client component, `Form`/`TextField`/`Input`/`Label`/`FieldError`/`Alert`/`Button` from `@heroui/react`, `api`/`ApiError` from `@/lib/api`):
    - Single screen, heading **"Elige una contraseña nueva para continuar"** (UX-DR16 copy, verbatim). One password field ("Contraseña nueva"), no confirm field (single-screen minimalism; a typo'd password is recoverable by another admin reset).
    - Client-side guard: length < 8 → inline error "La contraseña debe tener al menos 8 caracteres." before calling the API.
    - Submit → `api.post<{ home_path: string }>("/api/auth/change-password", { new_password })` → on success `window.location.assign(res.home_path)` (full navigation so middleware re-evaluates — same idiom as login).
    - Errors by `code`: `password_reuse` → inline field error with `err.message`; `not_authenticated` (401) → `window.location.assign("/login")`; anything else → danger `Alert` banner with `err.message` (fallback "No pudimos conectar. Intenta de nuevo."). Disable the button while pending ("Guardando…"), reset in `finally` (bfcache lesson from login).
  - [x] `frontend/app/admin/users/page.tsx` — add **Resetear** to `ClientLifecycleActions` (third action, after Renovar and Bloquear/Desbloquear), as a `ResetPasswordAction` component following the `BlockAction` inline-confirm idiom:
    - Idle → `Button` "Resetear" (size sm, variant secondary).
    - Confirm step → "¿Resetear la contraseña de {email}? Su sesión se cerrará al instante." + "Sí, resetear" (danger) / "Cancelar".
    - `useMutation` → `api.post<{ temp_password: string }>(`/api/admin/users/${user.id}/reset-password`)`. On success: store `temp_password` in local state, `onChanged()` (invalidates `USERS_KEY` — harmless, the local state survives), render the reveal step.
    - Reveal step (the EXACTLY-ONCE display, AC1): the temp password in monospace (`font-mono` — UX-DR2: data is mono) + helper text "Cópiala ahora: no volverá a mostrarse." + a "Copiar" button (`navigator.clipboard.writeText`, flips to "Copiada" briefly) + "Listo" button that clears the local state — once cleared it is unrecoverable by design (only a new reset produces a new one).
    - Errors: `ApiError.message` inline (the page's established idiom); non-ApiError → "No pudimos completar la acción. Intenta de nuevo."
  - [x] No other frontend surfaces change. `UserOut` does not expose `must_change_password` — no Estado column change (not in any AC).
- [x] Task 7: Backend tests (AC: all)
  - [x] `backend/tests/test_password_reset.py` (NEW) — ASGI integration via the shared conftest (`ctx` fixture, `seed_user`, `login`, `unique_email`, `cleanup_users`, `PASSWORD`, `loop_scope="session"` everywhere). Cover at minimum:
    - reset happy path: admin resets a client → 200 with non-empty `temp_password` ≠ `PASSWORD`; old password login → 401 `invalid_credentials`; temp password login → 200 with `home_path == "/change-password"` (AC1 + AC2 entry).
    - reset revokes live sessions: client logs in (cookie A) → reset → cookie A on `GET /api/auth/me` → 401 `not_authenticated`.
    - flag gates everything: logged in with temp password → `GET /api/auth/me` → 403 `password_change_required` (repeatable: call twice, both 403 — NOT one-shot).
    - forced change happy path: temp-password session → `POST /api/auth/change-password {new_password}` → 200 with role home `home_path` ("/") → same cookie now passes `GET /api/auth/me` 200 (flag cleared, session kept) → temp password login now 401, new password login → 200 with normal `home_path` (AC3 end-to-end).
    - change-password guards: reuse temp as new → 400 `password_reuse`; 7-char password → 422; without cookie → 401; with a NON-flagged session → 403 `forbidden`.
    - authorization on reset: target admin → 403 `forbidden`; unknown id → 404 `user_not_found`; client caller → 403 `forbidden`; both admin and owner actors succeed (FR6 "admin or owner").
    - independence: reset a BLOCKED client → 200; their temp login still 403 `account_blocked` (gate order untouched).
  - [x] The existing 36 tests stay green — this story must not modify any existing test (conftest additions only if genuinely shared).
- [x] Task 8: OpenAPI types + gates + manual verification (AC: all)
  - [x] New endpoints → `npm run generate:api` (backend live on `:8000`) and commit the regenerated `frontend/types/api.ts` (never hand-edit). Page keeps explicit local interfaces per idiom.
  - [x] Gates green: backend `ruff check .` + `mypy app` + `pytest` (36 prior + new); frontend `npm run lint` + `npx tsc --noEmit` + `next build`.
  - [x] Manual verification: create client → log them in (tab A) → as admin, Resetear → confirm → temp password shows once in mono → "Listo" → it's gone → tab A's next navigation lands on `/login` (revoked) → client logs in with temp password → lands on `/change-password` → manually typing `/` or `/admin/users` in the URL bounces back to `/change-password` → API probe (e.g. devtools `fetch('/api/auth/me')`) → 403 `password_change_required` → set a short password → inline error → set the temp password again → "Elige una contraseña distinta…" → set a real password → lands on Envío home (`/`) → logout/login with the new password works; throughout, admin session unaffected.

### Review Findings

- [x] [Review][Patch] (resolved Decision: option 1) Second temp-password session survives the forced change — change-password must revoke all OTHER sessions for the user (current session stays alive, AC3 intact). Supersedes the spec's "Do NOT revoke any session here" [backend/app/api/auth.py change_password]
- [x] [Review][Patch] (resolved Decision: option 1) Login racing the reset can complete the forced change with the OLD-password session — change-password must require `current_password` (the temp password) in the request body and verify it against the stored hash; API contract + page gain the field [backend/app/api/auth.py · frontend/app/change-password/page.tsx]
- [x] [Review][Patch] Reveal-step fragility: temp password survives in React Query mutation cache after "Listo" (needs `mutation.reset()`), and `onChanged()` fires while the one-time secret is on screen — a refetch-driven remount (re-sort/filter) destroys it before the admin copies; defer `onChanged()` to "Listo" [frontend/app/admin/users/page.tsx:306-329]
- [x] [Review][Patch] `navigator.clipboard.writeText` unguarded — rejects (or is undefined over plain HTTP) with no feedback; silent copy failure of a never-shown-again value [frontend/app/admin/users/page.tsx:331-336]
- [x] [Review][Patch] Flag-already-cleared dead end: second tab's submit gets 403 `forbidden` and lands in the generic banner with no way forward; route `forbidden` → `window.location.assign("/")` [frontend/app/change-password/page.tsx catch branch]
- [x] [Review][Patch] Server 422 renders an empty/garbage banner — pydantic validation errors have no `code`/`message`, so `err.message` is not the human string; add a fallback message for non-AppError shapes [frontend/app/change-password/page.tsx catch branch]
- [x] [Review][Patch] `new_password` has no upper length bound on an unthrottled endpoint — multi-MB body drives two argon2 ops per request (sync, inside async handler); add a max length to the validator [backend/app/api/auth.py:52-59]
- [x] [Review][Patch] Concurrent reset vs change-password is a lost update — change-password loads the user without a row lock while reset uses `FOR UPDATE`; interleaving silently overwrites the reset and the admin's temp password is dead on arrival; re-select the user `FOR UPDATE` in change-password [backend/app/api/auth.py:197-203]
- [x] [Review][Defer] Generated API types document only 200/422 — the 400/401/403/404 codes the frontend routes on are untyped [frontend/types/api.ts:552-622] — deferred, pre-existing generator behavior across all stories

## Dev Notes

### ⚠️ Scope rule (inherited from Stories 1.1–1.5 — still in force)

`_bmad-output/project-context.md` documents the **legacy single-user app** (`core.py`, `app.py`, `auto_sender.py`, `static/`). Those rules (Spanish identifiers, no new deps, 5 env vars) apply ONLY to the legacy files, which this story **must not touch**. For all `backend/`/`frontend/` code the architecture wins: **English-only identifiers**; user-facing UI text stays **Spanish (tuteo)**. Hard 🔒 rules apply everywhere: never read `respuestas/` contents; never commit/print `.env` (root or `backend/`); never touch/delete `anon.session` [Source: project-context.md; 1-5-...md#Scope rule].

### What this story IS (and is NOT)

IS: the last `/admin/users` row action (UX-DR18: "resetear contraseña") + the forced-change flow it triggers — FR6 (one-time temp password, shown once, argon2id-hashed) and FR7 (forced change at next login before anything else works). One new column, two new endpoints, one new page, one new row action.

IS NOT — resist building these:

- **No email delivery** — temp password is delivered out-of-band by the admin (WhatsApp); "no automated email in MVP" is explicit in FR6.
- **No voluntary password change** — the change-password endpoint REQUIRES the flag (else 403 `forbidden`). A self-service change (with current-password verification) is post-MVP; building it now without current-password verification would let a hijacked session take over the account permanently.
- **No reset for admin/owner accounts** — FR6 scopes reset to clients; `_require_client_target` enforces it (403 on admin targets). The owner resetting an admin is not in any AC.
- **No `must_change_password` in `UserOut`/table UI** — no AC shows reset-pending state in the admin table. Don't add it.
- **No new tables, no audit log** (3.6), **no new deps, no new env vars, no WS work**.
- **No login-page changes** — it already navigates to `res.home_path`; the backend steers flagged users by returning `/change-password` there.

### Existing code this story builds on (READ before writing)

- `backend/app/api/deps.py` — THE file to refactor carefully. Current `get_current_user`: cookie → `get_valid_session` → `is_blocked` check (revoke + 401, 1.5 review) → `is_plan_expired` check (revoke + 403). Your flag gate goes AFTER both, raises WITHOUT revoking, and the extraction into `_resolve_session_user` must not change any existing behavior — `test_auth.py`/`test_plan_expiry.py`/`test_admin_lifecycle.py` all exercise these branches. Note `_revoke_own_session`'s lesson: never commit on the request-scoped session inside a dependency [Source: backend/app/api/deps.py].
- `backend/app/api/admin.py` — `_require_client_target` (404/403 + `FOR UPDATE` — reuse it verbatim for reset), `require_admin_or_owner` module singleton (B008), the 1.5 lifecycle routes showing the exact route shape, `_PASSWORD_MIN = 8` (the same bound your `ChangePasswordRequest` validator enforces — admin.py keeps its private copy; importing it into auth.py or duplicating the literal are both fine, but the bound must match creation's) [Source: backend/app/api/admin.py].
- `backend/app/api/auth.py` — `login()` (only `home_path` changes), `_home_path_for(role)` (reuse in change-password's response), `_set_session_cookie`, `logout` (cookie-direct, unaffected by the flag — verify only). `LoginResponse.home_path` already exists; the frontend already consumes it [Source: backend/app/api/auth.py].
- `backend/app/services/auth.py` — `hash_password` / `verify_password` (use both; `verify_password` returns plain bool, never raises) and `secrets` already imported for `generate_temp_password` [Source: backend/app/services/auth.py].
- `backend/app/db/repos/users.py` — `revoke_all_sessions_for_user` (1.5, single bulk UPDATE) — reuse for reset-time revocation; do NOT write a new revocation query [Source: backend/app/db/repos/users.py].
- `backend/app/errors.py` — `AppError` + factory pattern; `forbidden`, `user_not_found`, `not_authenticated`, `invalid_credentials`, `account_blocked` all exist. Add only the two 1.6 codes [Source: backend/app/errors.py].
- `frontend/middleware.ts` — the 403 branch already parses the body for `plan_expired`; your new code branch slots in right after it. Respect the 1.4 hardening: prefetch skip, fail-open outside /admin, stale-cookie cleanup — none of those change [Source: frontend/middleware.ts].
- `frontend/lib/api.ts` — the global `plan_expired` → `/expired` routing is the exact template for `password_change_required` → `/change-password` [Source: frontend/lib/api.ts].
- `frontend/app/login/page.tsx` — the page idiom your new `/change-password` page mirrors (HeroUI v3 imports, `Form` onSubmit, inline `FieldError`, `finally`-reset pending state, `window.location.assign` full navigation) [Source: frontend/app/login/page.tsx].
- `frontend/app/admin/users/page.tsx` — `ClientLifecycleActions` (add the third action there), `BlockAction` (the inline-confirm state machine `ResetPasswordAction` copies), `isPositiveInt`/error idioms, `USERS_KEY` invalidation [Source: frontend/app/admin/users/page.tsx].

### Design decisions (exact, no interpretation room)

- **Reset = new hash + flag + revoke-all, atomically.** One transaction: `password_hash = hash(temp)`, `must_change_password = True`, `revoke_all_sessions_for_user`. Revocation mirrors block (1.5): the client's live tabs die to `/login` on next navigation, making "log in with the temp password" (AC2) the only path forward. The temp plaintext exists only in the single 200 response.
- **The flag never blocks login** — it steers it. Login gate order is UNCHANGED (password → blocked → expired). A flagged user authenticates normally and gets a session; `home_path: "/change-password"` routes them, and the deps gate (server-side, authoritative) 403s everything else they might try. Blocked/expired flagged users keep seeing blocked/expired states — those gates fire first, both at login and in deps.
- **Server-side enforcement lives in `get_current_user`** (one place gates every protected route and API at once, exactly like the expiry gate); the change-password endpoint opts out via `get_current_user_allow_pending_password`. Middleware is the UX mirror, not the boundary.
- **`password_change_required` is 403, repeatable, non-revoking.** Unlike `plan_expired` (one-shot — revokes as it answers), this 403 can be consumed any number of times (middleware, prefetch, parallel API calls) because the session must stay alive for the change itself. Hence middleware does NOT delete the cookie on this code.
- **Change-password requires the flag** (else 403 `forbidden`), rejects reuse of the current password (400 `password_reuse` — otherwise "one-time" is a lie), enforces min 8 via pydantic validator (422, same contract as creation), clears the flag, and KEEPS the current session (AC3: "they land on their normal home surface" — no re-login).
- **Temp password: `secrets.token_urlsafe(12)`** — 16 chars, ~96 bits, URL-safe (no ambiguous quoting when pasted into WhatsApp). Generated in `services/auth.py` next to the other secrets logic.

### API contract (new/changed endpoints)

| Method/Path | Body | Success | Errors |
|---|---|---|---|
| `POST /api/admin/users/{user_id}/reset-password` | — | 200 `{"temp_password": "..."}` | 401/403 auth · 404 `user_not_found` · 403 `forbidden` (non-client target) |
| `POST /api/auth/change-password` | `{"new_password": "..."}` | 200 `{"home_path": "/"}` | 401 `not_authenticated` · 403 `forbidden` (not flagged) · 400 `password_reuse` · 422 (short password) |
| `POST /api/auth/login` (changed) | unchanged | 200 `LoginResponse` — `home_path` is `"/change-password"` when flagged | unchanged |
| any gated route while flagged | — | — | 403 `{"code": "password_change_required"}` |

Action-suffix POST routes per the architecture's non-CRUD convention; `{code, message}` contract, snake_case codes, Spanish messages [Source: architecture.md#API Naming Conventions, #Format Patterns].

### Frontend notes (UX-DR16, UX-DR18, UX-DR2)

- `/change-password` is the "forced-password-change single screen" of UX-DR16; copy verbatim: **"Elige una contraseña nueva para continuar"**. It is a top-level route (`frontend/app/change-password/page.tsx`), inside the middleware matcher, reachable only with a valid session (no-cookie → `/login`).
- The reveal step is the product moment of FR6 ("shown once on screen, delivered out-of-band"): mono font for the password (UX-DR2 — credentials are data), explicit "no volverá a mostrarse" copy, clipboard button, and a deliberate dismiss. No toast-only display (too easy to lose on a phone).
- Inline confirm-expansion (the `BlockAction` idiom), NOT modals (UX-DR10; the page has zero modals — keep it that way).
- Spanish tuteo microcopy; show the backend `message` for known codes rather than re-stating copy, except the verbatim UX strings above.
- HeroUI v3 (`@heroui/react@3.1.0`) API differs from v2 docs — mirror the existing pages' imports exactly; don't import v2-only names (1.3/1.4/1.5 lesson).

### Conventions snapshot (unchanged from 1.1–1.5)

- Python: snake_case, type hints on every new def (`disallow_untyped_defs`), pydantic v2 request models. Errors = `{code, message}`, snake_case codes, Spanish messages; JSON snake_case end-to-end.
- TypeScript: strict; never hand-edit `types/api.ts` (regenerate via `npm run generate:api`).
- Every schema change = an Alembic migration (this story's is #4); never mutate schema manually.
- Commits: Conventional Commits with scope — e.g. `feat(backend,frontend): story 1.6 password reset + forced change`.
[Source: architecture.md#Code Naming Conventions, #Format Patterns, #Enforcement Guidelines; 1-5-...md#Conventions snapshot.]

### Testing

`backend/tests/` has **36 passing tests**; `conftest.py` carries the shared helpers (`PASSWORD`, `unique_email`, `seed_user`, `login`, `cleanup_users`) AND the shared `ctx` fixture (owner+admin clients, promoted there in the 1.5 review) — import/use them, don't redefine. Idiom: ASGI transport + cookie jar, self-seeding, self-cleaning, direct DB mutation for state setup, `loop_scope="session"` everywhere (function-scoped loops break the shared engine pool). The critical round-trips: reset → old sessions 401 + temp login lands flagged (AC1/AC2 entry), flagged session 403s `/me` repeatably (AC2), change → same session works + new password logs in (AC3). For login-as-the-target tests, build a fresh `AsyncClient` per actor (the `ctx` clients belong to owner/admin). No new test frameworks [Source: backend/tests/conftest.py; 1-5-...md#Testing].

### Quality gates (must pass before done)

Backend `ruff check .` + `mypy app` + `pytest`; frontend `npm run lint` + `npx tsc --noEmit` + `next build`. All green is the definition-of-done gate inherited from 1.1–1.5 [Source: architecture.md#Enforcement Guidelines].

### Previous Story Intelligence (Story 1.5)

- Local Postgres in Docker `cc-pg` (`postgres:16`, db `cc`, `127.0.0.1:5432`); recreate: `docker run -d --name cc-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=cc -p 5432:5432 postgres:16`. `backend/.env` has the `DATABASE_URL`.
- `:8000` may be held by the **legacy `app.py`** — stop it before running the new backend; `npm run generate:api` needs the new backend live on `:8000`.
- `cookie_secure=False` in local dev or the cookie is silently dropped.
- ruff B008: never call a dependency factory in argument defaults — module-level singletons (`require_admin_or_owner` exists; reuse).
- ruff import order: third-party group (e.g. `sqlalchemy`) BEFORE the `app.*` first-party group.
- Post-action navigation on auth-state changes uses full `window.location.assign(...)` — applies to the change-password success (it changes the actor's own auth-adjacent state; middleware must re-run).
- The 1.5 review's race lesson generalizes here: a login can commit a session concurrently with an admin action. For block, a per-request `is_blocked` check closes it; for reset it is closed by construction — the racing login used the OLD password but the flag/hash write means any session surviving the revoke belongs to a user whose `must_change_password` is now `True`, and the deps flag gate catches it on its next request. No extra code needed — but do NOT reorder the deps gates.
- `FOR UPDATE` on lifecycle targets via `_require_client_target` — reset reuses it, serializing with concurrent renew/block.
- Owner bootstrap: `python -m scripts.bootstrap_owner <email> <password>`; client seeding via the admin UI or `scripts/seed_user.py`.
- Manual psql/browser walkthroughs can be covered equivalently by ASGI tests; the live browser pass is for the reviewer [Source: 1-5-...md#Previous Story Intelligence, #Senior Code Review Fixes].

### Git Intelligence

Pattern from 8c0acf4/5e43c82/a2d3aa3: branch-per-story (`story/1.X-...`) merged to main; one feature commit + one review-fixes commit, scoped `feat(backend,frontend): story 1.X ...`. Start from current main (8c0acf4 — 1.5 merged). Files this story extends were all touched in 1.2–1.5 with the same idioms: `api/auth.py` + `deps.py` (1.2/1.4/1.5), `api/admin.py` (1.3/1.5), `errors.py` (every story), `admin/users/page.tsx` (1.3/1.5), `middleware.ts` + `lib/api.ts` (1.2/1.4).

### Project Structure Notes

New/changed files land in the architecture's prescribed tree [Source: architecture.md#Complete Project Directory Structure]:

```
backend/
  migrations/versions/<rev>_user_must_change_password_flag.py  # NEW — migration #4
  app/db/models.py         # EXTEND — User.must_change_password
  app/errors.py            # EXTEND — password_change_required, password_reuse
  app/services/auth.py     # EXTEND — generate_temp_password
  app/api/deps.py          # REFACTOR — _resolve_session_user + flag gate + allow-pending variant
  app/api/auth.py          # EXTEND — change-password endpoint; login home_path steering
  app/api/admin.py         # EXTEND — POST /users/{id}/reset-password
backend/tests/test_password_reset.py  # NEW
frontend/
  app/change-password/page.tsx  # NEW — forced-change single screen
  app/admin/users/page.tsx      # EXTEND — ResetPasswordAction (third lifecycle action)
  middleware.ts                 # EXTEND — 403 password_change_required branch
  lib/api.ts                    # EXTEND — global password_change_required routing
  types/api.ts                  # REGENERATED (npm run generate:api) — never hand-edit
```

No new deps, no settings changes. Architecture maps FR7 to "middleware + flag" and the auth service to "argon2id, login throttle, forced change" — this story completes that mapping.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.6] — story statement + 3 ACs (authoritative); FR6 (reset + temp password), FR7 (forced change)
- [Source: _bmad-output/planning-artifacts/epics.md#Additional Requirements — Auth & security] — "forced-password-change flag blocks everything except the change-password endpoint"
- [Source: _bmad-output/planning-artifacts/epics.md#UX Design Requirements UX-DR16, UX-DR18, UX-DR2] — forced-change single screen; reset row action with temp password shown once; data in mono
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] — argon2id, flag on user, middleware blocks all but change-password
- [Source: _bmad-output/planning-artifacts/architecture.md#API Naming Conventions, #Format Patterns] — `/api/auth/change-password` route name (predefined!), POST action suffix, `{code, message}` contract
- [Source: _bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/EXPERIENCE.md#Flow 3] — "Resetear contraseña → new temp password, delivered out-of-band… at next login the middleware forces the change — nothing else is reachable; then he lands on Envío as usual"
- [Source: backend/app/api/deps.py, api/auth.py, api/admin.py, services/auth.py, db/repos/users.py, errors.py] — existing code this story extends
- [Source: frontend/app/login/page.tsx, app/admin/users/page.tsx, middleware.ts, lib/api.ts] — proven idioms + extension points
- [Source: _bmad-output/implementation-artifacts/1-5-renovar-plan-y-bloquear-desbloquear-cliente.md] — prior-story learnings (revoke-all reuse, FOR UPDATE, race lesson, test idioms)
- [Source: _bmad-output/project-context.md] — legacy-only scope rule + the three hard 🔒 rules

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Claude Fable 5)

### Debug Log References

- All gates first-pass green: backend `ruff check .` + `mypy app` (18 files) + `pytest` (44 = 36 prior + 8 new); frontend `eslint` + `tsc --noEmit` + `next build` (route `/change-password` present in build output).
- Migration #4 autogenerated as `e497cdd16d32` (down_revision `05348659d1b6`, confirmed via `alembic heads`); `alembic upgrade head` applied cleanly to the dev DB.
- `types/api.ts` regenerated with the backend live on `:8000` (port was free; uvicorn started/stopped around `npm run generate:api`).

### Completion Notes List

- **Task 1**: `User.must_change_password` (Boolean, `server_default=false()`, NOT NULL) added right after `is_blocked`; migration #4 `e497cdd16d32_user_must_change_password_flag.py` (add_column/drop_column only). No other schema change.
- **Task 2**: `password_change_required()` (403, repeatable) and `password_reuse()` (400) added to `errors.py` under a 1.6 section.
- **Task 3**: `deps.py` refactored — existing `get_current_user` body extracted verbatim into `_resolve_session_user` (cookie → session → blocked → expired, comments preserved); `get_current_user` = resolve + flag gate (raises WITHOUT revoking — the session stays legitimate, 403 repeatable); `get_current_user_allow_pending_password` = resolve only, used exclusively by change-password. `generate_temp_password()` (`secrets.token_urlsafe(12)`) added to `services/auth.py`. Verified `logout` reads the cookie directly (no `get_current_user`) — unchanged.
- **Task 4**: `POST /api/admin/users/{id}/reset-password` — `_require_client_target` (404/403/FOR UPDATE) → new hash + flag + `revoke_all_sessions_for_user` in one transaction → plaintext only in the single response. `POST /api/auth/change-password` — depends on the allow-pending variant; 403 `forbidden` when not flagged, 400 `password_reuse` on temp reuse, 422 under 8 chars (pydantic validator mirroring creation), clears the flag, keeps the current session, returns role `home_path`. `login()` — single change: `home_path` becomes `/change-password` when flagged; gate order untouched.
- **Task 5**: `middleware.ts` — new 403 branch after `plan_expired`: `password_change_required` → pass through on `/change-password` (loop guard) else redirect there, cookie KEPT. `lib/api.ts` — global routing mirroring the `plan_expired` template.
- **Task 6**: `frontend/app/change-password/page.tsx` (NEW) — single screen, verbatim "Elige una contraseña nueva para continuar" heading, one password field, client-side min-8 guard, error routing by code (`password_reuse` inline, 401 → /login, else danger Alert), pending state reset in `finally`. `ResetPasswordAction` added as third lifecycle action — idle → inline confirm ("Sí, resetear" danger) → reveal step: temp password in `font-mono`, "Cópiala ahora: no volverá a mostrarse.", Copiar (clipboard, flips to "Copiada"), Listo clears local state (unrecoverable by design). `UserOut` not extended — no Estado column change.
- **Task 7**: `backend/tests/test_password_reset.py` (NEW, 8 tests) — reset happy path (temp ≠ seed, old password 401, temp login → `/change-password`); reset revokes live sessions (cookie A → 401); flag gates `/me` repeatably (two consecutive 403s); forced change end-to-end (200 + home `/` + same cookie passes + temp dead + new password normal login); guards (reuse 400, short 422, no cookie 401, non-flagged 403 forbidden); reset authorization (admin target 403, unknown 404, client caller 403); both admin and owner succeed; blocked client reset → temp login still 403 `account_blocked`. No existing test modified; conftest untouched.
- **Task 8**: `types/api.ts` regenerated (not hand-edited). All six gates green. Manual browser walkthrough left for the reviewer per the 1.5 precedent — every step of the listed walkthrough is covered equivalently by the ASGI suite at the API level.

### File List

- backend/app/db/models.py (modified)
- backend/migrations/versions/e497cdd16d32_user_must_change_password_flag.py (new)
- backend/app/errors.py (modified)
- backend/app/services/auth.py (modified)
- backend/app/api/deps.py (modified)
- backend/app/api/auth.py (modified)
- backend/app/api/admin.py (modified)
- backend/tests/test_password_reset.py (new)
- frontend/app/change-password/page.tsx (new)
- frontend/app/admin/users/page.tsx (modified)
- frontend/middleware.ts (modified)
- frontend/lib/api.ts (modified)
- frontend/types/api.ts (regenerated)

## Change Log

| Date       | Change                                                      |
|------------|-------------------------------------------------------------|
| 2026-06-11 | Story 1.6 drafted (context engine). Status → ready-for-dev. |
| 2026-06-11 | Story 1.6 implemented: migration #4 (must_change_password), reset + change-password endpoints, deps flag gate, middleware/api routing, /change-password page, Resetear row action, 8 new tests (44 total green). Status → review. |
| 2026-06-11 | Code review: 8 patches applied (change-password now requires `current_password` proof, locks the row FOR UPDATE, revokes all other sessions; max password length; page handles forbidden/invalid_credentials/422; reveal-step hardening + clipboard fallback), 1 deferred (typed error responses). 45 tests green, all six gates pass, types regenerated. Status → done. |
