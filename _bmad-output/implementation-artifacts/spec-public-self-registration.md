---
title: 'Public self-registration with no-plan → Telegram-contact lockout'
type: 'feature'
created: '2026-06-17'
status: 'done'
baseline_commit: 'fde30028ad49729e550160567a51b0e7a494f18d'
context:
  - '{project-root}/CLAUDE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Accounts can only be created by an admin (`POST /api/admin/users`) — high onboarding friction; a prospect cannot sign up on their own.

**Approach:** Add a public `/register` page + `POST /api/auth/register` that creates a tenant+user with **no plan** (already-expired so the existing `plan_expired` gate locks them out), auto-logs them in, and lands them on the existing `/expired` surface showing the seller's Telegram contact. Activation stays a manual owner action → zero blast radius on the shared account (a no-plan user can send nothing). Also fix the latent login bug where a no-plan/expired user logging in fresh gets a 403 with no session and bounces in a `/login`↔`/expired` loop.

## Boundaries & Constraints

**Always:**
- New users: `role="client"`, `expires_at = now(UTC)` (already-expired → `is_plan_expired` True; never `None`, which reads as *not* expired), `plan_id=NULL`, `credit_balance=0`, `contact=NULL`, `is_blocked=False`, `must_change_password=False`.
- Register sets the same HttpOnly session cookie as login (auto-login) and returns `home_path=_home_path_for("client")` (=`/`); middleware routes the no-plan session to `/expired`.
- `tenant_id` is created by the endpoint, never read from the request. Email canonical lower-case; duplicates → existing `email_taken` (409). Password 8–128 (mirror admin `_PASSWORD_MIN`).
- Rate-limit register per client IP (reuse `LoginThrottle`) to bound DB-spam from unauthenticated callers.

**Ask First:** granting any trial plan/credits on signup; adding email verification, captcha, or an owner toggle to disable signup.

**Never:** no email-confirmation / captcha / signup-toggle in this scope (accepted MVP risk); do not collect name or the user's own Telegram handle (`contact` stays NULL); do not auto-grant a plan (no sending before owner activation); do not change the blocked-login path (still 403 `account_blocked`, no session).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Register new email | `POST /api/auth/register {email, password≥8}` | 201, session cookie set; tenant+user created (`client`, expired-now, no plan, 0 credits); body `home_path="/"` | N/A |
| Register duplicate email | email already exists (case-insensitive) | No row created | 409 `email_taken` |
| Register short password | password < 8 | No row created; client guards before submit | 422 (pydantic) — client validates first |
| Register flood | > throttle limit from one IP per window | Request refused | 429 `too_many_attempts` |
| Login as no-plan/expired client | valid creds, `expires_at ≤ now`, not blocked | 200, **session cookie set**, `home_path="/"` (middleware → `/expired`) | N/A |
| No-plan session hits app | session cookie present, any gated route | middleware/`/me` → 403 `plan_expired` → `/expired` shows Telegram contact; polls `/me`, auto-recovers when owner activates plan | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/api/auth.py` -- register endpoint + login fix; reuses `_client_ip`/`_set_session_cookie`/`_home_path_for`/`LoginResponse`.
- `backend/app/services/users.py` -- `register_account` (mirror `create_account` minus the plan branch).
- `backend/app/services/auth.py` -- `register_throttle` instance + `LoginThrottle` (existing).
- `backend/app/db/repos/users.py` -- `create_tenant`/`create_user` reused unchanged.
- `frontend/app/register/page.tsx` (NEW), `middleware.ts`, `login/page.tsx`, `expired/page.tsx` -- see tasks.
- `backend/tests/test_plan_expiry.py`, `backend/tests/test_register.py` (NEW) -- see tasks.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/services/auth.py` -- add `register_throttle = LoginThrottle(max_attempts=settings.throttle_max_attempts, window_seconds=settings.throttle_window_seconds)`. Throttle is per-IP (call with a constant email key like `"register"` so attempts from one IP share a bucket regardless of email).
- [x] `backend/app/services/users.py` -- add `register_account(session, *, email, password) -> User`: lower-case email, `email_taken` on duplicate, `create_tenant(name=email)`, `create_user(role="client", expires_at=datetime.now(UTC), contact=None)`; map flush `IntegrityError`→`email_taken`. Leave `plan_id`/`credit_balance` at defaults (NULL / 0).
- [x] `backend/app/api/auth.py` -- add `RegisterRequest` (email canonical via the same regex idiom, password validators: `<8`→too short, `>128`→too long) and `@router.post("/register", response_model=LoginResponse, status_code=201)`: no auth dep; check `register_throttle.is_blocked` → `too_many_attempts`; record the attempt (`register_failure`); call `users_service.register_account`; `create_session` + `commit` + `_set_session_cookie`; return `LoginResponse(..., home_path=_home_path_for("client"))`.
- [x] `backend/app/api/auth.py` -- in `login`, replace `raise plan_expired()` for expired clients with: `create_session` + `commit` + `_set_session_cookie` + `return LoginResponse(..., home_path=_home_path_for(user.role))`. Keep the blocked check raising. Update the surrounding comment (a session IS now created; it is gated server-side by deps).
- [x] `frontend/app/register/page.tsx` -- build the page mirroring `login/page.tsx` (RxBackdrop, Logo, branded card, `Field` email/password, `Btn`, `Notice`/`ContactPanel` for errors, footer Telegram link). Client-side guard password length ≥ 8. On success `window.location.assign(res.home_path)`. Map `email_taken`→inline/banner "Ya existe una cuenta con ese correo.", `too_many_attempts`→banner, fallback→`err.message`. Include "¿Ya tienes cuenta? Iniciar sesión" link → `/login`.
- [x] `frontend/middleware.ts` -- add `register(?:/|$)` to the matcher regex alongside `login`/`expired`; extend the explanatory comment.
- [x] `frontend/app/login/page.tsx` -- add a "Crear cuenta" link to `/register` near the support footer.
- [x] `frontend/app/expired/page.tsx` -- change `MESSAGE` and `AuthLayout title` to neutral copy that fits both a brand-new no-plan account and an expired one, e.g. title "Activá tu plan" / message "Tu cuenta no tiene un plan activo. Escríbenos por Telegram para activarlo." (keep the ContactPanel + poll/auto-recover logic untouched).
- [x] `backend/tests/test_plan_expiry.py` -- rewrite the expired-login assertion: login now returns 200 with a session cookie and `home_path`; a subsequent gated call (`GET /me`) on that cookie still returns 403 `plan_expired`. Rename the test to reflect "logs in but is gated".
- [x] `backend/tests/test_register.py` (new) -- cover the I/O matrix register rows: happy path (201, cookie set, `/me`→403 `plan_expired`, user row is expired/no-plan), duplicate email (409), short password (422), throttle (429).

**Acceptance Criteria:**
- Given a visitor on `/register`, when they submit a fresh email + valid password, then a tenant+user is created with no plan and they land on `/expired` (auto-logged-in) showing the Telegram contact.
- Given a self-registered no-plan user, when the owner activates a plan, then the open `/expired` tab auto-recovers into the app via its `/me` poll without re-login.
- Given a no-plan/expired user logging in via `/login`, when credentials are valid, then they receive a session and reach `/expired` (no `/login`↔`/expired` bounce loop).
- Given a self-registered user before activation, when they attempt any send/batch action, then it is refused by the existing `plan_expired` gate (zero blast radius on the shared account).

## Spec Change Log

- **2026-06-17 (step-04 review, patch — no loopback):** Edge-case review flagged that the frozen "Always" rule `expires_at = now(UTC)` leaves a sub-second skew window: the auth gate (`is_plan_expired`) compares against the Python clock while the send worker's guard (`tenant_plan_expired`) compares against the SQL clock (`func.now()`), so an app clock running ahead of the DB could read a no-plan tenant as still active on the SQL path. **Amended (code only, frozen intent unchanged):** `register_account` now sets `expires_at = now(UTC) - 1 day` — robustly already-expired under both clocks, restoring the legacy day-scale margin. This serves the unambiguous frozen intent ("already-expired, locked out, never None") more safely; the literal `now(UTC)` was a mechanism, not the intent. Avoids the known-bad state: brief unauthorized sending on the 🔒 shared Telegram account during clock skew. **KEEP:** never use `None` (reads as no-plan-limit), and keep the per-IP register throttle.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest` -- expected: all pass, including updated `test_plan_expiry.py` and new `test_register.py`.
- `cd frontend && npm run build` -- expected: clean tsc + build (memory: build gate catches type errors lint misses).
- `cd frontend && npm run lint` -- expected: no new lint errors.

**Manual checks:**
- Register a new email → verify the browser lands on `/expired` with the `@yesterWhite` Telegram contact, then have an owner assign a plan and confirm the tab auto-enters the cockpit.

## Suggested Review Order

**Registration endpoint (entry point — grasp the whole flow here)**

- Public signup: throttle → create no-plan client → session cookie → route to lockout.
  [`auth.py:173`](../../backend/app/api/auth.py#L173)

- The no-plan client row: `expires_at = now - 1 day` (robust expiry under both clocks), no plan, no credits.
  [`users.py:110`](../../backend/app/services/users.py#L110)

**Login bounce-loop fix (the riskiest change — auth semantics)**

- Expired client now gets a (gated) session + home_path instead of a 403 — kills the /login↔/expired loop.
  [`auth.py:259`](../../backend/app/api/auth.py#L259)

**Anti-abuse + validation**

- Per-IP register rate cap (counting successes is intentional — see Spec Change Log / deferred-work).
  [`auth.py(register_throttle):164`](../../backend/app/services/auth.py#L164)

- Email-canonical + 8–128 password bounds, mirroring admin creation.
  [`auth.py(_EMAIL_RE):34`](../../backend/app/api/auth.py#L34)

**Frontend surfaces**

- `/register` page: form, client-side password bounds, success → navigate to home_path.
  [`register/page.tsx:44`](../../frontend/app/register/page.tsx#L44)

- Middleware: `/register` is now a public route (segment-anchored, no leak).
  [`middleware.ts:185`](../../frontend/middleware.ts#L185)

- `/expired` copy neutralized to fit new no-plan + expired alike.
  [`expired/page.tsx:16`](../../frontend/app/expired/page.tsx#L16)

- "Crear cuenta" link from the login card.
  [`login/page.tsx:180`](../../frontend/app/login/page.tsx#L180)

**Tests (supporting)**

- Register: happy path (no-plan/expired row), duplicate, short password, rate-limit.
  [`test_register.py:53`](../../backend/tests/test_register.py#L53)

- Updated expiry contract: expired login is now 200-with-session, then /me 403.
  [`test_plan_expiry.py:54`](../../backend/tests/test_plan_expiry.py#L54)
