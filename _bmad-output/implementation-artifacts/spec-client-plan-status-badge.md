---
title: 'Client plan-status badge in the global header'
type: 'feature'
created: '2026-06-15'
status: 'done'
baseline_commit: '100f3a6'
context: ['{project-root}/CLAUDE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A client has no persistent view of their plan status. `expires_at` lives on the `users` row but `/api/auth/me` never returns it, so the frontend cannot show how many days remain. Clients only discover expiry when they are abruptly locked out and redirected to `/expired`.

**Approach:** Surface `expires_at` through `/api/auth/me`, and render an always-visible plan badge in the global client header (`ClientNav`) showing days remaining plus a status tone (active / expiring soon / expired). Visible on every client screen (cockpit + history), desktop and mobile.

## Boundaries & Constraints

**Always:**
- `expires_at` for the badge comes from the session-authenticated user via `/me` — never from request body/path (🔒 tenant identity invariant).
- Days-remaining math anchors on the same boundary semantics as `is_plan_expired` (`expires_at <= now` ⇒ expired).
- Badge renders ONLY for `role === 'client'` (owner/admin carry `expires_at = null`).
- Reuse the existing `StatePill` component and Ranger-X tones; Spanish UI copy, tuteo.

**Ask First:**
- Changing the "expiring soon" threshold away from the proposed **≤ 3 days**.
- Adding a hard expiry date string to the badge (user opted for days + status only, no exact date).

**Never:**
- No new endpoint — extend the existing `/me` response only.
- No DB schema/migration change (`expires_at` already exists).
- Do not touch the legacy app (`app.py`/`core.py`/`static/`).
- Do not gate or block anything on the badge — it is display-only; lockout stays owned by middleware + `is_plan_expired`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Active plan | client, `expires_at` = now + 12d | Badge "12 días", success tone | N/A |
| Expiring soon | client, `expires_at` = now + 2d | Badge "2 días", warning tone | N/A |
| Last day | client, `expires_at` = now + 8h (same calendar boundary, <1d) | Badge "Vence hoy", warning tone | N/A |
| Expired | client, `expires_at` <= now | Badge "Vencido", danger tone | N/A |
| Staff | owner/admin, `expires_at` = null | No badge rendered | N/A |
| `/me` loading / no data | query pending or `expires_at` absent | No badge (no layout shift placeholder) | render nothing |

</frozen-after-approval>

## Code Map

- `backend/app/api/auth.py` -- `MeResponse` schema (line 37) + `me()` (191); add `expires_at: datetime | None` and populate from `user.expires_at`. `LoginResponse` inherits the field automatically.
- `backend/app/services/plans.py` -- `is_plan_expired` defines the `<= now(UTC)` boundary the days math mirrors (reference only, no change).
- `frontend/components/client-nav.tsx` -- global header; local `Me` interface (line 23) + `["me"]` query (110). Add `expires_at` to `Me`, render the badge in the right-side cluster.
- `frontend/components/ui/plan-badge.tsx` -- NEW. Pure presentational badge: takes `expiresAt: string | null`, computes days remaining, picks copy + tone, renders `StatePill`.
- `frontend/components/ui/state-pill.tsx` -- reused as-is (tones success/warning/danger).
- `frontend/types/api.ts` -- `MeResponse` type (~line 810); add `expires_at: string | null`.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/api/auth.py` -- add `expires_at: datetime | None = None` to `MeResponse`; pass `expires_at=user.expires_at` in `me()`. Pydantic serializes to ISO 8601 string. -- exposes plan deadline to the client.
- [x] `frontend/types/api.ts` -- add `expires_at: string | null` to the `MeResponse` type. -- keeps generated types in sync (hand-edit; no codegen run committed).
- [x] `frontend/components/ui/plan-badge.tsx` -- NEW pure component computing `daysLeft = ceil((expiresAt - now)/86400000)`; map to copy + tone per the I/O matrix; return `null` when `expiresAt` is null. -- isolates the date logic for testability.
- [x] `frontend/components/client-nav.tsx` -- add `expires_at: string | null` to local `Me`; render `<PlanBadge expiresAt={me.data?.expires_at ?? null} />` in the right cluster before `ThemeToggle`, only when `role === 'client'`. -- always-visible placement. (Placed before the Soporte button so it shows on mobile too, where Soporte is desktop-only.)

**Acceptance Criteria:**
- Given a logged-in client with a 12-day plan, when any client screen loads, then a header badge reads "12 días" with success tone on both cockpit and history, desktop and mobile.
- Given a client whose plan has ≤ 3 days left, when the header renders, then the badge uses warning tone; given the plan is past expiry, then it reads "Vencido" with danger tone.
- Given an owner or admin, when the header renders, then no plan badge appears.
- Given the `/me` query is still loading, when the header renders, then no badge placeholder causes layout shift.

## Design Notes

Days math (frontend, local clock — day-scale, skew irrelevant, matching the app-clock exception in `plans.py`):

```ts
const ms = new Date(expiresAt).getTime() - Date.now();
// ms <= 0            → danger,  "Vencido"
// 0 < ms < 1 day     → warning, "Vence hoy"   (checked BEFORE ceil — ceil would
//                                               read sub-day as "1 día")
// daysLeft = ceil(ms / 1 day)
// daysLeft 1..3      → warning, `${daysLeft} día(s)`
// daysLeft > 3       → success, `${daysLeft} días`
```

Pluralize: "1 día" vs "N días". Badge carries `aria-label="Plan: quedan N días"` (or "Plan vencido"). An expired client is normally redirected to `/expired` by middleware, so "Vencido" is a defensive/transient state, kept for correctness.

## Verification

**Commands:**
- `cd frontend && npm run build` -- expected: passes (tsc type-checks the new component + `Me`/`MeResponse` changes; build gate per project rule, lint alone misses type errors).
- `cd backend && .venv/bin/pytest` -- expected: existing auth/`/me` tests still pass; add/extend a test asserting `/me` includes `expires_at` for a client and `null` for staff.

**Manual checks:**
- Log in as a client → badge with days + tone visible in header on `/` and `/sessions`, desktop and < lg mobile widths. Log in as owner/admin → no badge.

## Suggested Review Order

**Data exposure (entry point)**

- The whole feature hinges on this: `/me` now returns the plan deadline.
  [`auth.py:46`](../../backend/app/api/auth.py#L46)

- Populated from the session user; `login` mirrors it for consistency.
  [`auth.py:205`](../../backend/app/api/auth.py#L205)

**Badge logic (highest-risk: date boundaries)**

- The boundary math — note the sub-day "Vence hoy" guard before `ceil`.
  [`plan-badge.tsx:21`](../../frontend/components/ui/plan-badge.tsx#L21)

**UI binding**

- Clients-only, first in the right cluster so it shows on mobile too.
  [`client-nav.tsx:197`](../../frontend/components/client-nav.tsx#L197)

**Peripherals**

- Generated type sync for the new `/me` field.
  [`api.ts:820`](../../frontend/types/api.ts#L820)

- Integration tests: `expires_at` present for client, null for staff.
  [`test_plan_expiry.py:157`](../../backend/tests/test_plan_expiry.py#L157)
