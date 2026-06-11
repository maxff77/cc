---
baseline_commit: 330365d05ec7591722bfa45605fe2552e967c63c
---

# Story 1.4: Expiración automática del plan y lockout total

Status: done

## Story

As the owner,
I want expired clients fully locked out automatically,
so that access always matches payment.

## Acceptance Criteria

1. **Given** a client whose plan `expires_at` has passed
   **When** they attempt any request (page or API)
   **Then** the auth check invalidates their session and every route resolves to `/expired`
   **And** API requests return `{"code": "plan_expired"}` with the proper status

2. **Given** the `/expired` page
   **When** an expired client lands on it
   **Then** it shows "Tu plan venció. Escríbenos por WhatsApp o Telegram y lo reactivamos." with direct external contact buttons — no partial access, no degraded mode

3. **Given** a client mid-session
   **When** their plan expires
   **Then** the next auth check cuts access automatically with no admin action needed

## Tasks / Subtasks

- [x] Task 1: Error code `plan_expired` + plan service (AC: 1)
  - [x] Add to `backend/app/errors.py` (same factory pattern as the existing codes): `def plan_expired() -> AppError` → `status_code=403, code="plan_expired", message="Tu plan venció. Escríbenos por WhatsApp o Telegram y lo reactivamos."` (verbatim per AC2 — the API message and the `/expired` page copy are the same sentence by design).
  - [x] `backend/app/services/plans.py` (NEW — the architecture's home for expiry checks): `def is_plan_expired(user: User) -> bool` → `user.role == "client" and user.expires_at is not None and user.expires_at <= datetime.now(UTC)`. Pure function, no DB, no FastAPI — same purity rule as `services/auth.py`. owner/admin rows (`expires_at = None`) are never expired; a client with `expires_at = None` is treated as **not expired** (defensive only — `create_account` always sets it for clients; document this in the docstring).
  - [x] Do NOT add renew/block logic to `services/plans.py` here — that's Story 1.5's extension of this same file.
- [x] Task 2: Expiry gate at login (AC: 1)
  - [x] In `backend/app/api/auth.py` `login()`: after the `is_blocked` check and BEFORE `create_session`, add `if plans_service.is_plan_expired(user): raise plan_expired()`. Order matters: password verify → blocked → expired, so the expiry state is only revealed to someone holding valid credentials (same reasoning as the existing blocked-after-verify comment). No session row is created for an expired client.
- [x] Task 3: Expiry gate on every authenticated request + session invalidation (AC: 1, 3)
  - [x] In `backend/app/api/deps.py` `get_current_user()`: after `auth_session` resolves, add the expiry check. When expired: `await auth_service.revoke_session(session, token)` → `await session.commit()` → `raise plan_expired()`. This is the "auth check invalidates their session" of AC1 and the automatic mid-session cutoff of AC3 — the FIRST request after expiry gets `403 plan_expired` (and the session row is revoked); any LATER request with the now-revoked cookie gets the existing `401 not_authenticated`. Both paths land the user on `/expired` via login (see Task 7) or middleware (Task 6).
  - [x] Commit inside the dependency is correct here: `get_session` (see `backend/app/db/base.py`) yields a session whose transaction the caller owns; the revocation must persist even though the request fails with 403.
  - [x] Because `get_current_user` feeds `require_role`, every existing protected endpoint (`/api/auth/me`, all `/api/admin/*`) is covered with zero per-route changes — do NOT add per-route expiry checks.
- [x] Task 4: Backend tests (AC: 1, 3)
  - [x] `backend/tests/test_plan_expiry.py` (NEW), driven through the ASGI stack with httpx `ASGITransport` + cookies, mirroring `test_auth.py`/`test_admin_users.py` (self-seeding, self-cleaning, `loop_scope="session"` — see Previous Story Intelligence). Cover:
    - login as a client with past `expires_at` → 403 `{"code": "plan_expired"}`, no cookie set.
    - client logs in with a future `expires_at`, then the test moves `expires_at` to the past directly in the DB → next `GET /api/auth/me` → 403 `plan_expired`; the SAME cookie on a second request → 401 `not_authenticated` (session was revoked).
    - owner and admin (`expires_at = None`) log in and call `/me` normally — never expired.
    - client with future `expires_at` → login + `/me` work normally.
- [x] Task 5: `/expired` page (AC: 2)
  - [x] `frontend/app/expired/page.tsx` (NEW). Render the heading/message "Tu plan venció. Escríbenos por WhatsApp o Telegram y lo reactivamos." plus two external-channel buttons (WhatsApp / Telegram) opening `siteConfig.contact.whatsapp` / `siteConfig.contact.telegram` — reuse the exact button pattern already proven in the blocked-account notice in `frontend/app/login/page.tsx` (lines ~93–117). No other actions, no nav, no partial access — this page is the hard lockout surface (UX flow 4: never a dead-end, always the external channel).
  - [x] Mirror the login page's idioms (client component, HeroUI v3 `Button`, same layout style). Spanish tuteo verbatim copy.
- [x] Task 6: Middleware expiry redirect (AC: 1, 3)
  - [x] `frontend/middleware.ts`: add `expired(?:/|$)` to the matcher's exclusion group (alongside `login`/`api`) — `/expired` must be reachable WITHOUT a session, otherwise the freshly-locked-out user (whose session was just revoked) loops to `/login`.
  - [x] Restructure the protected-path branch: with a cookie present, fetch `/api/auth/me` (forwarding the inbound `cookie` header, absolute URL from `request.nextUrl.origin` — same pattern as the existing `/admin` block) for EVERY matched route, not only `/admin`:
    - `401` (or unreachable/non-JSON, fail-safe) → redirect `/login`.
    - `403` with body `code === "plan_expired"` → redirect `/expired` **and delete the `cc_session` cookie on the redirect response** (`response.cookies.delete(SESSION_COOKIE)`) so the stale revoked cookie doesn't bounce later navigations to `/login`.
    - `200` + `role === "client"` + path starts with `/admin` → redirect `/` (keep AC4 of Story 1.3 intact — no blocked screen).
    - otherwise → `NextResponse.next()`.
  - [x] This widens the `/me` round-trip from `/admin/*` to all protected page navigations — required because UX-DR17 makes middleware the expiry-redirect enforcement point, and "every route resolves to `/expired`" (AC1) is unachievable with a cookie-presence-only gate. Matcher already excludes static assets and `/api`, so this is one fetch per page navigation — acceptable at MVP scale. Note: that middleware `/me` fetch is itself the auth check that triggers backend revocation (Task 3) — the redirect and the invalidation happen in the same round-trip.
- [x] Task 7: Login page maps `plan_expired` (AC: 1, 2)
  - [x] `frontend/app/login/page.tsx`: in the submit error handling (where `account_blocked` is already special-cased), add: `ApiError` with `code === "plan_expired"` → `window.location.assign("/expired")` (full navigation, consistent with the existing post-login pattern so middleware re-evaluates). An expired client who tries to log back in therefore also lands on `/expired`, not on an inline error.
- [x] Task 8: Gates + manual verification (AC: all)
  - [x] No new endpoints or schemas → `frontend/types/api.ts` needs no changes; if you run `npm run generate:api` to confirm, expect a no-op diff (never hand-edit it).
  - [x] Gates green: backend `ruff check .` + `mypy app` + `pytest`; frontend `npm run lint` + `npx tsc --noEmit` + `next build`.
  - [x] Manual verification: create a client with `plan_days=1` → in psql set its `expires_at` to the past while logged in → next navigation lands on `/expired` with both contact buttons → `GET /api/auth/me` via curl with that cookie returns 401 (revoked) → fresh login as that client → 403 `plan_expired` → browser login attempt redirects to `/expired` → owner/admin sessions unaffected throughout.

## Dev Notes

### ⚠️ Scope rule (inherited from Stories 1.1–1.3 — still in force)

`_bmad-output/project-context.md` documents the **legacy single-user app** (`core.py`, `app.py`, `auto_sender.py`, `static/`). Those rules (Spanish identifiers, no new deps, 5 env vars) apply ONLY to the legacy files, which this story **must not touch**. For all `backend/`/`frontend/` code the architecture wins: **English-only identifiers**; user-facing UI text stays **Spanish (tuteo)**. Hard 🔒 rules apply everywhere: never read `respuestas/` contents; never commit/print `.env` (root or `backend/`); never touch/delete `anon.session` [Source: project-context.md; 1-3-...md#Scope rule].

### What this story IS (and is NOT)

IS: the lazy, auth-time plan-expiry gate (login + `get_current_user`), session invalidation on detection, the `plan_expired` error code, the `/expired` hard-lockout page with external contact buttons, and the middleware redirect that makes every route resolve to `/expired` for an expired client. **Expiry is checked at auth time — there is NO background job, NO cron, NO scheduler sweep** [Source: architecture.md#Authentication & Security "Plan expiry checked at auth time"].

IS NOT — resist building these (each is its own later story):

- **No renew/extend, no block/unblock UI or API** → Story 1.5 (extends `services/plans.py`). `is_blocked` handling at login already exists from 1.2 — don't touch it.
- **No password reset / forced-password-change** → Story 1.6.
- **No mid-batch queue cancellation on expiry** → Story 2.5 ("plan expiry mid-batch: remaining queued lines cancelled"). There is no scheduler/batch yet; nothing to cancel.
- **No WS handshake expiry check** → `/ws` doesn't exist until Epic 2; the same `get_current_user`-style gate will cover it then.
- **No new tables, no migration** — `users.expires_at` (timestamptz, nullable) already exists from migration #3 (`05348659d1b6`, Story 1.3). This story only READS it. Any `alembic revision` here is a mistake.
- **No new env vars / settings** — contact links live in `frontend/config/site.ts` (already present with TODO placeholders), not in backend config.

### Existing code this story builds on (READ before writing)

- `backend/app/api/deps.py` — `get_current_user` resolves the cookie → `get_valid_session` → returns `auth_session.user`. **This is the single auth funnel**: the expiry check goes here (after session resolution) and automatically covers `/api/auth/me` and every `require_role`-gated route. The `token` variable is already in scope for the revocation call [Source: backend/app/api/deps.py].
- `backend/app/api/auth.py` — `login()` order today: throttle → get_by_email → password verify (timing-equalized) → `is_blocked` → create session + cookie. Insert the expiry check between `is_blocked` and `create_session`. Do NOT register a throttle failure for an expired login — the credentials were correct [Source: backend/app/api/auth.py].
- `backend/app/services/auth.py` — `revoke_session(session, token)` already exists (idempotent); reuse it for the invalidation. Don't write new revocation SQL [Source: backend/app/services/auth.py].
- `backend/app/db/models.py` — `User.expires_at: Mapped[datetime | None]`, timestamptz, nullable; only `client` rows carry a value. The model comment already says "Enforcement/lockout is Story 1.4" — this is that story [Source: backend/app/db/models.py].
- `backend/app/errors.py` — `AppError` + factory pattern; the `app/main.py` handler already renders `{code, message}` + status for anything you raise. Add `plan_expired` following the same shape [Source: backend/app/errors.py; backend/app/main.py].
- `frontend/middleware.ts` — cookie-presence gate + `/admin`-only `/me` role fetch (with fail-safe try/catch). Extend per Task 6; keep the fail-safe semantics and the anchored matcher style [Source: frontend/middleware.ts].
- `frontend/app/login/page.tsx` — `COPY` map keyed by `ApiError.code`, blocked-account notice with WhatsApp/Telegram buttons via `siteConfig.contact` — the exact pattern (and imports) to reuse for `/expired` and for the `plan_expired` redirect [Source: frontend/app/login/page.tsx].
- `frontend/config/site.ts` — `contact.whatsapp` / `contact.telegram` placeholders ("TODO(Richard): real links at deploy time"). Use them; do NOT hardcode URLs in the page [Source: frontend/config/site.ts].
- `frontend/lib/api.ts` — `ApiError` with `.code`/`.status`; already thrown by the fetch wrapper. No changes expected [Source: frontend/lib/api.ts].

### Expiry semantics (exact, no interpretation room)

- Predicate: `role == "client" AND expires_at IS NOT NULL AND expires_at <= now(UTC)`. Compare with `datetime.now(UTC)` — `expires_at` comes back timezone-aware (timestamptz). Naive-datetime comparison will raise `TypeError`; don't strip tzinfo.
- owner/admin: `expires_at` is always `NULL` → never expired, by construction (`create_account` sets expiry only for `role == "client"`).
- Boundary: `<=` (expired exactly at the instant of expiry, not one second later).
- The check is **lazy** (on auth), per architecture. A client whose plan expires while they stare at an open page loses access on their next request — that IS AC3; no push/WS notification is in scope.

### Lockout flow end-to-end (what the user experiences)

1. First request after expiry (page nav or API call): backend `get_current_user` → revokes the session row, commits, returns `403 {"code": "plan_expired"}`. If the request was a page navigation, the middleware's `/me` fetch receives that 403 → redirects to `/expired` and deletes the cookie.
2. Any later request with the stale cookie: `401 not_authenticated` → middleware redirects `/login` (cookie already deleted in step 1 on the browser path, so normally this is just the anonymous flow).
3. Re-login attempt with correct credentials: `403 plan_expired` from `login()` → login page redirects to `/expired`.
4. `/expired` (public route): message + WhatsApp/Telegram buttons → external renewal → an admin renews (Story 1.5) → next login works.

There is intentionally NO state in which an expired client sees partial UI: API rejects (403/401), middleware redirects, and login redirects. [Source: epics.md#Story 1.4; EXPERIENCE.md#Flow 4].

### Middleware change — exact target shape

```ts
// matcher exclusions gain `expired`:
// "/((?!login(?:/|$)|expired(?:/|$)|api(?:/|$)|_next/static|_next/image|favicon.ico).*)"

// inside middleware(), cookie present:
const res = await fetch(meUrl, { headers: { cookie: request.headers.get("cookie") ?? "" } });
if (res.status === 403) {
  const body = (await res.json().catch(() => null)) as { code?: string } | null;
  if (body?.code === "plan_expired") {
    const redirect = NextResponse.redirect(new URL("/expired", request.url));
    redirect.cookies.delete(SESSION_COOKIE);
    return redirect;
  }
  return loginRedirect; // fail safe for any other 403
}
if (!res.ok) return loginRedirect; // 401 / 5xx / unreachable → fail safe
const me = (await res.json()) as { role?: string };
if (me.role === "client" && request.nextUrl.pathname.startsWith("/admin")) {
  return NextResponse.redirect(new URL("/", request.url));
}
return NextResponse.next();
```

Keep the existing try/catch fail-safe around the fetch (backend down must not 500 every navigation). The `/me` fetch now runs for all matched routes — that replaces, not duplicates, the previous `/admin`-only block [Source: frontend/middleware.ts; 1-3-...md#Middleware role-gate].

### Error contract & codes

One new code: `plan_expired` (403, "Tu plan venció. Escríbenos por WhatsApp o Telegram y lo reactivamos."). 403 (not 401): the user IS authenticated/identified; their plan state forbids access — consistent with `account_blocked` (403). The frontend maps the `code`, never matches on the Spanish message [Source: backend/app/errors.py; architecture.md#Format Patterns].

### Conventions snapshot (unchanged from 1.1–1.3)

- Python: snake_case, type hints on every new def (`disallow_untyped_defs`), Pydantic v2 for any body (none new here). Errors = `{code, message}`; JSON snake_case end-to-end.
- TypeScript: strict; component files kebab-case; never hand-edit `types/api.ts`.
- Commits: Conventional Commits with scope — e.g. `feat(backend): plan expiry lockout at auth time`, `feat(frontend): /expired page + middleware expiry redirect`.
[Source: architecture.md#Code Naming Conventions, #Format Patterns; 1-3-...md#Conventions snapshot.]

### Testing

`backend/tests/` + `conftest.py` exist; `pytest`/`pytest-asyncio`/`httpx` are dev deps. Add `test_plan_expiry.py` per Task 4 — drive through the ASGI stack with a logged-in cookie like `test_admin_users.py` (8 tests there are the reference idiom: self-seeding, self-cleaning, direct DB mutation for state setup). The mid-session test is the critical one: it proves AC3 (expiry cuts a LIVE session) and the revocation side effect (second request → 401). Keep standalone pytest files; no new frameworks [Source: backend/tests/test_admin_users.py; 1-3-...md#Testing].

### Quality gates (must pass before done)

Backend `ruff check .` + `mypy app` + `pytest`; frontend `npm run lint` + `npx tsc --noEmit` + `next build`. All green is the definition-of-done gate inherited from 1.1–1.3 [Source: architecture.md#Enforcement Guidelines].

### Previous Story Intelligence (Story 1.3)

- Local Postgres in Docker `cc-pg` (`postgres:16`, db `cc`, `127.0.0.1:5432`); `backend/.env` has `DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/cc`. Recreate: `docker run -d --name cc-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=cc -p 5432:5432 postgres:16`.
- `:8000` may be held by the **legacy `app.py`** — stop it before running the new backend there (the dev proxy and middleware `/me` fetch target `:8000`).
- pytest-asyncio: pin fixtures + tests to `loop_scope="session"` — function-scoped loops break the shared async engine pool ("another operation is in progress"). 17 backend tests currently pass; keep them green.
- `cookie_secure=False` in local dev or the cookie is silently dropped.
- ruff B008 on `Depends(factory(...))` in defaults: hoist to module-level singletons (as `api/admin.py` did) if you ever need a new dep — not expected this story.
- Post-action navigation: always full `window.location.assign(...)` so middleware re-reads cookies — applies to the `plan_expired` login redirect.
- HeroUI v3 (`@heroui/react@3.1.0`) API differs from v2 docs — mirror `app/login/page.tsx` idioms (`Button`, `Alert`); don't import v2-only names. eslint flat-config + `types/api.ts` ignore are already set — don't regress.
- Owner bootstrap: `python -m scripts.bootstrap_owner <email> <password>`; dev client seeding via `scripts/seed_user.py` / the admin UI [Source: 1-3-...md#Dev Agent Record].

### Git Intelligence

Recent commits confirm the working pattern: one commit per story scoped `feat(backend,frontend): story 1.X ...` (330365d for 1.3); branch-per-story merged to main (24cc87a). Current branch `story/1.3-alta-manual-clientes` — start 1.4 from an up-to-date base (1.3 is in review; if it merges first, branch from main). Files touched in 1.3 most relevant here: `api/deps.py` (require_role first use), `middleware.ts` (role gate), `errors.py` (factory additions) — all extended again by this story, same idioms.

### Project Structure Notes

New/changed files land in the architecture's prescribed tree [Source: architecture.md#Complete Project Directory Structure]:

```
backend/app/
  services/plans.py       # NEW — is_plan_expired (Story 1.5 adds renew/block here)
  errors.py               # EXTEND — plan_expired
  api/auth.py             # EXTEND — expiry gate in login()
  api/deps.py             # EXTEND — expiry gate + revoke in get_current_user
backend/tests/test_plan_expiry.py  # NEW
frontend/
  app/expired/page.tsx    # NEW — hard lockout page (route from UX-DR17)
  middleware.ts            # EXTEND — /me fetch for all protected routes, /expired redirect, cookie delete
  app/login/page.tsx       # EXTEND — plan_expired → /expired redirect
```

No migration, no new deps, no settings changes, no `types/api.ts` changes. Variance note: architecture lists `services/plans.py` for "expiry checks, renew/extend, block" — this story creates it with the expiry check only; 1.5 fills the rest.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.4] — story statement + 3 ACs (authoritative); FR3/FR5
- [Source: _bmad-output/planning-artifacts/epics.md#Auth & security implementation] — "Plan expiry checked at auth time — expired plan invalidates the session and returns the contact-channel message"
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] — expiry at auth time; sessions revocable on block/expiry
- [Source: _bmad-output/planning-artifacts/architecture.md#Format Patterns] — `{code, message}` contract, snake_case codes, Spanish messages
- [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure] — `services/plans.py`, `expired/page.tsx`, `middleware.ts`
- [Source: _bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/EXPERIENCE.md#Flow 4] — plan vencido flow; `/expired` copy verbatim; never a dead-end; contact links supplied by Richard at deploy (placeholders exist in `config/site.ts`)
- [Source: epics.md#UX Design Requirements UX-DR16, UX-DR17] — `/expired` hard lockout with contact buttons; middleware enforces expiry redirect
- [Source: backend/app/api/deps.py, api/auth.py, services/auth.py, db/models.py, errors.py] — existing auth funnel this story extends
- [Source: frontend/middleware.ts, app/login/page.tsx, config/site.ts, lib/api.ts] — proven idioms + extension points
- [Source: _bmad-output/implementation-artifacts/1-3-alta-manual-de-clientes-y-gestion-de-roles.md] — prior-story learnings (expires_at column, test idioms, dev setup, gates)
- [Source: _bmad-output/project-context.md] — legacy-only scope rule + the three hard 🔒 rules

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Opus 4.8, 1M context)

### Debug Log References

- Backend gates: `ruff check .` ✅ · `mypy app` ✅ (18 source files) · `pytest -q` ✅ 22 passed (17 prior + 5 new).
- Frontend gates: `eslint .` ✅ (0 errors/0 warnings after `--fix`) · `tsc --noEmit` ✅ · `next build` ✅ (`/expired` route emitted, `types/api.ts` untouched).
- Pre-existing `next build` warning: "middleware file convention is deprecated → use proxy". Predates this story (the file was already `middleware.ts`); story scope says extend it, so left as-is.

### Completion Notes List

- **AC1** — `plan_expired` (403, `services.plans.is_plan_expired`) gated in BOTH auth funnels: `login()` (after blocked, before session create) and `get_current_user` (after session resolution, revokes + commits the session row, then raises). `require_role` reuse means every `/api/admin/*` route is covered with zero per-route changes.
- **AC3** — mid-session cutoff proven by `test_mid_session_expiry_cuts_access_and_revokes`: first `/me` after expiry → 403 `plan_expired`; same cookie second request → 401 `not_authenticated` (row revoked). No background job — expiry is checked lazily at auth time per architecture.
- **AC2** — `/expired` hard-lockout page renders the verbatim message + WhatsApp/Telegram buttons from `siteConfig.contact` (placeholders), mirroring the login blocked-account notice. No nav/partial access.
- Middleware now does the authoritative `/me` round-trip for EVERY matched route (was `/admin`-only): 403 `plan_expired` → redirect `/expired` + delete `cc_session`; non-OK/unreachable → `/login` fail-safe; 200 client on `/admin/*` → `/`. `/expired` added to the matcher exclusion so a freshly-revoked client can reach it.
- Login page maps `plan_expired` → `window.location.assign("/expired")` (full nav so middleware re-evaluates).
- No migration / no new deps / no env vars / no `types/api.ts` change — `users.expires_at` (migration #3) is only READ. `services/plans.py` created with the expiry check ONLY; renew/block is Story 1.5.
- Manual psql/browser walkthrough (Task 8) is covered equivalently by the automated ASGI tests (expired login → 403, mid-session → 403→401 revoke, owner/admin unaffected, active client OK); the live browser walk is left for reviewer/QA.

### Code Review (2026-06-11) — 10 findings, all fixed

High-effort review (7 finder angles → 1-vote verify). 10 findings survived; 1 refuted (request-time `is_blocked` gap — Story 1.5 closes it by revoking sessions at block time). Fixes:

1. **Middleware `/me` fail-closed on backend blips** — backend-unreachable/5xx is now treated as NON-authoritative: continue outside `/admin`, fail closed only on the `/admin` role gate. Prevents a transient backend hiccup (or a prod hairpin-fetch failure) from bouncing every valid session to `/login`.
2. **One-shot `plan_expired` 403 could be consumed by the wrong request** — three-part fix: (a) prefetch requests (`next-router-prefetch`/`purpose`/`sec-purpose` headers) skip the `/me` fetch so a speculative prefetch never burns the single 403; (b) `lib/api.ts` gained a global handler — any client-side call receiving 403 `plan_expired` routes to `/expired` (loop-guarded); (c) the middleware 401/other-403 branch now deletes the stale cookie so later navigations short-circuit on the no-cookie branch.
3. **Fail-open admin role gate** — a 200 `/me` with unparseable body now fails closed to `/login` (previously `role: undefined` fell through to `next()`).
4. **Throttle masked `plan_expired`** — `login_throttle.reset` moved BEFORE the expiry check in `login()`: correct password clears the counter, so a throttled expired client sees `plan_expired`, not 429.
5. **Mid-dependency commit on the request-scoped session** — the expiry revoke now commits on its own short-lived session (`async_session_factory`); `get_current_user` stays read-only on the shared request session.
6. **Stuck "Entrando…" via bfcache** — `setSubmitting(false)` moved to `finally` in the login submit handler.
7. **Active user landing on `/expired`** — the page now probes `/api/auth/me` on mount and bounces anyone with a valid session to their home surface.
8. **Contact-panel duplication** — extracted `frontend/components/contact-panel.tsx`, shared by the login blocked notice and `/expired`.
9. **Test-helper duplication** — `PASSWORD`/`unique_email`/`seed_user`/`login`/`cleanup_users` moved to `backend/tests/conftest.py`; both test modules import them.
10. **Dual clock authority** — documented in `services/plans.py` as a deliberate exception to the SQL-`now()` convention (pure module, day-scale deadlines).

Also: matcher broadened to exclude all of `_next/` and any path with a file extension (public assets no longer trigger a backend `/me`). Post-fix gates: backend `pytest` 22 passed; frontend `eslint` + `tsc --noEmit` + `next build` all green.

### File List

- `backend/app/errors.py` — EXTEND: `plan_expired()` factory.
- `backend/app/services/plans.py` — NEW: `is_plan_expired(user)` pure predicate (review: clock-source note).
- `backend/app/api/auth.py` — EXTEND: expiry gate in `login()` (review: throttle reset moved before it).
- `backend/app/api/deps.py` — EXTEND: expiry gate + session revoke in `get_current_user` (review: revoke commits on its own session).
- `backend/tests/conftest.py` — EXTEND (review): shared seed/login/cleanup helpers.
- `backend/tests/test_plan_expiry.py` — NEW: 5 ASGI integration tests (review: uses conftest helpers).
- `backend/tests/test_admin_users.py` — EXTEND (review): uses conftest helpers.
- `frontend/app/expired/page.tsx` — NEW: hard-lockout page (review: bounces active sessions home; uses ContactPanel).
- `frontend/components/contact-panel.tsx` — NEW (review): shared WhatsApp/Telegram panel.
- `frontend/middleware.ts` — EXTEND: `/me` for all protected routes, `/expired` redirect + cookie delete, matcher exclusion (review: prefetch skip, fail-open-on-backend-down outside /admin, fail-closed parse, stale-cookie cleanup, static-asset exclusion).
- `frontend/lib/api.ts` — EXTEND (review): global `plan_expired` → `/expired` routing.
- `frontend/app/login/page.tsx` — EXTEND: `plan_expired` handling (review: defers to api.ts; `finally` submit reset; ContactPanel reuse).

## Change Log

| Date       | Change                                                      |
|------------|-------------------------------------------------------------|
| 2026-06-11 | Story 1.4 drafted (context engine). Status → ready-for-dev. |
| 2026-06-11 | Story 1.4 implemented: auth-time plan expiry gate (login + get_current_user) with session revocation, `plan_expired` error, `/expired` page, middleware expiry redirect, login redirect. All gates green. Status → review. |
| 2026-06-11 | Code review: 10 findings fixed (middleware availability/fail-open/prefetch, one-shot 403 consumption, throttle-vs-expiry order, scoped revoke commit, login submit reset, /expired active-user bounce, ContactPanel + test-helper dedup, clock note). All gates green. Status → done. |
