---
title: 'Owner-managed pricing plan catalog (days + antispam + max lines)'
type: 'feature'
created: '2026-06-16'
status: 'in-review'
context: ['{project-root}/CLAUDE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Today a client's plan is only an `expires_at` date; the per-line send interval ("antispam") is a single GLOBAL `system_settings` value and there is no max-lines-per-batch cap. The owner cannot sell differentiated tiers (price + duration + antispam + line cap) without code changes.

**Approach:** Add an owner-managed `plans` catalog (CRUD from `/admin/plans`) holding `name, price_usd, duration_days, antispam_seconds, max_lines_per_batch, is_active`. Link each client via `users.plan_id` (nullable). Assigning/renewing a plan computes `expires_at` from `duration_days`. The send engine reads the tenant's plan antispam as a **per-tenant cooldown** in the scheduler (never below the global floor); batch create/append enforces the plan's max-lines (back + front).

## Boundaries & Constraints

**Always:**
- 🔒 Per-tenant antispam is a **cooldown in `scheduler.pick_next`** (tenant skipped until `last_send + antispam`), NOT the global inter-send sleep. The global `g_min` floor remains the account-wide pacing and the hard ban protector — the account-wide send rate never exceeds `1/g_min` regardless of plan values.
- 🔒 `tenant_id` only from the session; CRUD endpoints owner-only via `require_role`.
- Plan values validated on BOTH layers: backend authoritative, frontend pre-submit. `max_lines_per_batch` enforced on create AND append.
- `plan_id = NULL` ⇒ current behavior preserved (global interval, no line cap).
- Global floor bounds widen to **1–30s** (was 0–30s); default stays 4s; floor never auto-drops below the configured value.
- Plan field bounds: `antispam_seconds >= 1`, `duration_days >= 1`, `max_lines_per_batch >= 1`, `price_usd >= 0`.
- Repos flush-not-commit; new migration runs before restart; Alembic naming convention honored.

**Ask First:**
- Adding `priority_weight` / per-tenant scheduler priority (explicitly deferred this round).
- Any change that lets a plan or per-tenant value send FASTER than the global floor.

**Never:**
- `priority_weight`, per-day line caps, auto-lowering the floor under concurrency (all deferred — see deferred-work.md).
- Touching Telethon / `core/telegram.py` or `parse_mode`.
- Editing legacy root `app.py`/`core.py`/`static/`.
- Seeding any plans (table ships empty; owner creates them).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Create plan | owner POST valid fields | 201, plan row, appears in list | duplicate name → 409 `plan_name_taken` |
| Invalid plan field | antispam 0 / days 0 / lines 0 / negative price | 400 `invalid_plan` | field-specific message |
| Assign plan on client create | `plan_id` of active plan | client created, `expires_at = now + duration_days`, `plan_id` set | unknown/inactive plan → 400 `invalid_plan` |
| Renew via plan | POST renew `plan_id` | `expires_at = max(now, current) + duration_days`, `plan_id` updated | — |
| Send within cap | client plan `max_lines=10`, pastes 10 | batch accepted | — |
| Send over cap | pastes 12 (cap 10) | 400 `batch_line_limit`, nothing queued; FE blocks pre-submit | message states cap + count |
| No-plan client | `plan_id=NULL` sends 500 lines | accepted (no cap), global interval used | — |
| Antispam pacing | plan antispam 20s, tenant alone | its lines spaced ≥20s; other tenants still sent during the gap | — |
| Delete plan in use | plan referenced by ≥1 user | 409 `plan_in_use`; suggest deactivate | — |
| Floor below min | owner PUT interval 0.5 | 400 (min 1s) | bounds message |

</frozen-after-approval>

## Code Map

- `backend/app/db/models.py` -- add `Plan` model + `User.plan_id` FK (RESTRICT).
- `backend/migrations/versions/<new>.py` -- create `plans`, add `users.plan_id`; empty table.
- `backend/app/db/repos/plans.py` (new) -- plan CRUD + `count_users_with_plan`; flush-not-commit.
- `backend/app/services/plans.py` -- plan-catalog ops; extend `create`/`renew` expiry to derive from a plan's `duration_days`.
- `backend/app/services/users.py` -- `create_account` accepts `plan_id`, sets it + computes expiry from plan.
- `backend/app/api/admin.py` -- `/api/admin/plans` GET/POST/PATCH/DELETE (owner-only); extend create-user & renew schemas with `plan_id`; widen interval `INTERVAL_MIN`→1.
- `backend/app/services/pacing.py` -- `INTERVAL_MIN = 1.0`.
- `backend/app/db/repos/batches.py` -- `active_senders` resolves per-tenant `antispam_seconds` = `coalesce(plan.antispam_seconds, global_interval)`; `ActiveSender` gains `antispam_seconds`.
- `backend/app/core/scheduler.py` -- per-tenant cooldown: track `_last_send_at[tenant_id]`; `pick_next` skips tenants in cooldown; `note_sent(tenant_id)`.
- `backend/app/core/send_worker.py` -- after a successful send call `scheduler.note_sent(tenant_id)`; global sleep stays `interval()`.
- `backend/app/api/batches.py` -- enforce plan `max_lines_per_batch` on create + append → `batch_line_limit`.
- `backend/app/api/auth.py` -- `/me` returns the client's plan summary (`name, antispam_seconds, max_lines_per_batch`).
- `frontend/types/api.ts` -- `PlanOut`, `me.plan` summary, new error codes.
- `frontend/lib/admin-shell` (`components/ui/admin-shell.tsx`) + `frontend/middleware.ts` -- register owner-only `/admin/plans`.
- `frontend/app/admin/plans/page.tsx` (new) -- CRUD page mirroring `app/admin/gates/page.tsx`.
- `frontend/app/admin/users/page.tsx` -- replace `plan_days` input with a plan selector (create + renew).
- `frontend/components/batch/send-form.tsx` -- pre-submit max-lines guard from `me.plan`.

## Tasks & Acceptance

**Execution:**
- [ ] `backend/app/db/models.py` -- add `Plan` table + `User.plan_id` FK (nullable, `ondelete=RESTRICT`).
- [ ] `backend/migrations/versions/<new>.py` -- autogenerate-style migration: create `plans`, add `users.plan_id`; no seed data.
- [ ] `backend/app/db/repos/plans.py` -- create/list/get/update/delete + `count_users_with_plan`.
- [ ] `backend/app/services/plans.py` -- plan-catalog service ops; expiry helper derives from `duration_days`; reject delete when in use.
- [ ] `backend/app/services/users.py` -- `create_account(plan_id=...)`: set `plan_id`, `expires_at = now + plan.duration_days`.
- [ ] `backend/app/api/admin.py` -- plans router (owner-only) + Pydantic schemas; `plan_id` in create-user & renew (XOR with existing modes); `INTERVAL_MIN` path bound 1s.
- [ ] `backend/app/services/pacing.py` -- `INTERVAL_MIN = 1.0`.
- [ ] `backend/app/db/repos/batches.py` -- `active_senders` join to resolve `antispam_seconds`; add field to `ActiveSender`.
- [ ] `backend/app/core/scheduler.py` -- per-tenant cooldown filter in `pick_next` + `note_sent`; uses `ActiveSender.antispam_seconds`.
- [ ] `backend/app/core/send_worker.py` -- call `scheduler.note_sent(tenant_id)` after a confirmed send.
- [ ] `backend/app/api/batches.py` -- enforce `max_lines_per_batch` on create + append.
- [ ] `backend/app/api/auth.py` -- include plan summary in `/me`.
- [ ] `backend/tests/test_plans_catalog.py` (new) -- cover the I/O matrix: CRUD, invalid fields, assign/renew expiry math, line-cap on create+append, NULL-plan no-cap, delete-in-use, floor min, scheduler cooldown skip.
- [ ] `frontend/types/api.ts` -- `PlanOut`, `me.plan`, error codes.
- [ ] `frontend/components/ui/admin-shell.tsx` + `frontend/middleware.ts` -- owner-only `/admin/plans`.
- [ ] `frontend/app/admin/plans/page.tsx` -- CRUD UI mirroring gates.
- [ ] `frontend/app/admin/users/page.tsx` -- plan selector in create + renew.
- [ ] `frontend/components/batch/send-form.tsx` -- max-lines pre-submit guard.

**Acceptance Criteria:**
- Given an owner, when they create/edit/deactivate a plan in `/admin/plans`, then it persists and shows in the client create/renew selector (active plans only).
- Given a client on a plan with `duration_days=15`, when assigned, then `expires_at ≈ now + 15d`; on renew it extends from `max(now, current)`.
- Given a client on a plan with `antispam_seconds=20`, when their batch runs alone, then consecutive sends for that tenant are ≥20s apart while a second active tenant still gets interleaved sends within that window.
- Given a client on a plan with `max_lines_per_batch=10`, when they submit 11 lines, then both the frontend (pre-submit) and backend (`batch_line_limit`) reject it and nothing is queued.
- Given a client with `plan_id=NULL`, when they send, then no line cap applies and the global interval is used (unchanged behavior).
- Given the owner sets the global interval below 1s, then it is rejected; the floor is never auto-lowered below the configured value.

## Design Notes

Scheduler cooldown (the load-bearing subtlety): keep the global inter-send sleep = `scheduler.interval()` (`g_min`). Add a per-tenant gate so a tenant isn't *re-picked* until its antispam elapses:

```python
# scheduler.py — monotonic clock (time.monotonic), process-memory like the rest
def pick_next(self, active):
    now = time.monotonic()
    eligible = [s for s in active
                if now - self._last_send_at.get(s.tenant_id, -1e9) >= s.antispam_seconds]
    # existing round-robin + owner-priority over `eligible`
def note_sent(self, tenant_id):
    self._last_send_at[tenant_id] = time.monotonic()
```

When no tenant is eligible (all cooling down), `pick_next` returns `None`; the worker already handles the idle case (short sleep, re-poll). `antispam_seconds` rides on `ActiveSender` so the scheduler stays DB-free. Account safety is unchanged: only one send per `g_min` globally, so plan antispam can only slow a tenant, never speed the account.

`me.plan` lets the cockpit show/enforce the cap client-side; the backend remains authoritative. Plan delete uses FK `RESTRICT` + an explicit `plan_in_use` guard so historical assignments never dangle — retire via `is_active=false` instead.

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` -- expected: migration applies, `plans` + `users.plan_id` exist.
- `cd backend && .venv/bin/pytest tests/test_plans_catalog.py -q` -- expected: all pass.
- `cd backend && .venv/bin/pytest -q` -- expected: no regressions.
- `cd frontend && npm run build` -- expected: tsc + build pass (build gate, not just lint).
