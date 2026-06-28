---
title: 'Antispam decoupled from plans — global default + per-user override'
type: 'refactor'
created: '2026-06-27'
status: 'done'
baseline_commit: '9702315c68bb9a4efb4df9846b7fa207e319fda7'
context: ['{project-root}/_bmad-output/implementation-artifacts/spec-plan-catalog.md', '{project-root}/_bmad-output/implementation-artifacts/spec-configurable-send-interval.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Today the per-tenant antispam cooldown is a `plans.antispam_seconds` field, so it differentiates pricing tiers and is locked to whatever plan a client is on. The owner wants antispam to be the SAME baseline for everyone on a plan, and then sell a faster (lower) antispam to individual clients on request — independent of their plan.

**Approach:** Remove `antispam_seconds` from the plan concept. Add (1) an owner-editable GLOBAL default cooldown in `system_settings` (key `default_antispam_seconds`, mirrors the configurable interval), applied to every plan-holding client by default, and (2) a nullable per-user override `users.antispam_seconds` the owner sets when a client buys a faster speed offline. The scheduler cooldown resolves to `coalesce(user.antispam_seconds, global_default)`; the global `g_min` floor remains the account-wide ban protector — an override can only re-pick a tenant faster, never push the shared account past `g_min`.

## Boundaries & Constraints

**Always:**
- 🔒 Cooldown resolution = `coalesce(User.antispam_seconds, default_antispam_seconds)`. The plan is NEVER consulted for antispam. `g_min` still paces every send globally — override/default can only SLOW a tenant relative to the account, never speed the account past `1/g_min`.
- Global default + per-user override are owner-only (`require_owner`). A client/admin can never set either.
- Buying = offline; the owner sets `users.antispam_seconds` from `/admin/users`. No credits, no payment flow.
- Bounds: global default `1.0–30.0s`; per-user override `0.0–30.0s` OR `null` (null clears → falls back to the global default). `0.0` = no per-tenant cooldown (paced by `g_min` alone — the fastest a client can be sold). 30s mirrors the governor ceiling `_G_MIN_CEIL`.
- Validate on both layers: backend authoritative (`invalid_antispam` 400), frontend pre-submit.
- Global default lives in `system_settings` (hot, durable) — read per worker loop, NOT baked into the scheduler singleton at boot (it's a query-time bind param, not the scheduler floor).
- Repos flush-not-commit; one new migration off the current head; Alembic naming convention honored.

**Ask First:**
- Changing the 1s/30s bounds, or letting a non-owner edit either value (read-only display is fine).
- Exposing a client-facing "request faster speed" button (this round: owner edits only).

**Never:**
- Reading `plan.antispam_seconds` anywhere (the column is being removed).
- Letting any per-tenant value send FASTER than `g_min`.
- Touching the FloodWait governor, `pick_next` priority/rotation, Telethon/`core/telegram.py`, or legacy root `app.py`/`core.py`/`static/`.
- Seeding the global default (absent row ⇒ env-safe fallback, like the interval).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Owner sets global default | owner `PUT /api/admin/antispam {antispam_seconds: 15}` | persisted; next `active_senders` resolves NULL-override tenants to 15s cooldown | out of 1–30 → 400 `invalid_antispam` |
| Owner reads, no row | `GET /api/admin/antispam`, empty | env default (config fallback) | N/A |
| Owner sets per-user override | owner `POST /api/admin/users/{id}/antispam {antispam_seconds: 4}` | `users.antispam_seconds=4`; that tenant re-picked at 4s | non-client / out of 0–30 → 400 `invalid_antispam` |
| Owner clears override | `POST .../antispam {antispam_seconds: null}` | `users.antispam_seconds=NULL`; tenant reverts to global default | N/A |
| Non-owner write | client/admin PUT/POST either | rejected, unchanged | 403 (`require_owner`) |
| Override vs default mix | tenant A override 4s, B at default 15s, both active | A re-picked ~every 4s, B ~every 15s; global gap still ≥ `g_min` | N/A |
| Plan create/edit | owner sends `antispam_seconds` in plan body | ignored — field no longer exists on plans, catalog, `/me`, or public | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/db/models.py` -- `User`: add `antispam_seconds: Mapped[Decimal | None]` (`Numeric(6,2)`, nullable). `Plan` (~L920): REMOVE `antispam_seconds`.
- `backend/migrations/versions/<new>.py` -- off head `e9b3d6c1f2a4`: `add_column users.antispam_seconds` (nullable); `drop_column plans.antispam_seconds`.
- `backend/app/services/antispam.py` (NEW) -- mirror `services/pacing.py`: `DEFAULT_ANTISPAM_KEY="default_antispam_seconds"`, `ANTISPAM_MIN=1.0`, `ANTISPAM_MAX=30.0`, `_parse(raw)`, `get_default(session)->float` (parsed-or-env-default), `set_default(session, v)`.
- `backend/app/errors.py` -- `invalid_antispam()` → 400 code `invalid_antispam`, Spanish message (mirror `invalid_send_interval`, ~L377).
- `backend/app/db/repos/batches.py` (L281–369) -- `active_senders`: rename dead `global_interval` param → `default_antispam`; drop the `Plan` outerjoin; `antispam = func.coalesce(User.antispam_seconds, default_antispam)`; update `ActiveSender` docstring.
- `backend/app/core/send_worker.py` -- where it calls `active_senders(...)`: resolve `default_antispam = await antispam_service.get_default(session)` and pass it. `scheduler.note_sent` path unchanged.
- `backend/app/api/admin.py` -- (a) NEW `GET/PUT /api/admin/antispam` owner-only mirroring `/interval` (L884), bounds→`invalid_antispam`, `set_default`+commit; (b) NEW `POST /api/admin/users/{id}/antispam` owner-only mirroring `recharge_credits` (L488) — set/clear `users.antispam_seconds`; (c) REMOVE `antispam_seconds` from `CreatePlanRequest`/`UpdatePlanRequest`/`PlanOut`/`_plan_to_out`/`_validate_plan_fields`/`PLAN_ANTISPAM_MAX`; (d) add `antispam_seconds` to `UserOut`.
- `backend/app/db/repos/plans.py` -- drop `antispam_seconds` from `create`/`update` signatures.
- `backend/app/services/plans.py` -- drop any `antispam_seconds` passthrough.
- `backend/app/api/auth.py` -- remove `antispam_seconds` from `/me` `PlanSummary` (L83, L330).
- `frontend/types/api.ts` -- drop antispam from `PlanOut`/`me.plan`; add `antispam_seconds` to the admin user type + `AntispamOut`; add error code `invalid_antispam`.
- `frontend/app/admin/plans/page.tsx` -- remove the antispam input from plan create/edit.
- `frontend/app/admin/users/page.tsx` -- (a) NEW `DefaultAntispamCard` owner-only mirroring `SendIntervalCard` (L526); (b) per-client antispam edit control mirroring the credit recharge (sets/clears the override).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/models.py` -- add `User.antispam_seconds` nullable; remove `Plan.antispam_seconds`.
- [x] `backend/migrations/versions/<new>.py` -- add `users.antispam_seconds`, drop `plans.antispam_seconds`.
- [x] `backend/app/services/antispam.py` -- new service per Code Map (parse-or-default, get/set).
- [x] `backend/app/errors.py` -- `invalid_antispam()`.
- [x] `backend/app/db/repos/batches.py` -- resolve cooldown from `coalesce(User.antispam_seconds, default_antispam)`; drop Plan join; rename param.
- [x] `backend/app/core/send_worker.py` -- resolve + pass `default_antispam` into `active_senders`.
- [x] `backend/app/api/admin.py` -- antispam global GET/PUT, per-user POST, strip antispam from plan schemas, add to `UserOut`.
- [x] `backend/app/db/repos/plans.py` + `backend/app/services/plans.py` -- drop antispam.
- [x] `backend/app/api/auth.py` -- drop antispam from `/me`.
- [x] `backend/tests/test_antispam.py` (NEW) -- global default parse/bounds/GET-default/PUT happy+out-of-range+owner-only; per-user override set/clear+bounds; `active_senders` resolves `coalesce(override, default)`; scheduler cooldown still gates by the resolved value.
- [x] `backend/tests/test_plans_catalog.py` -- remove plan-antispam assertions (CRUD, cooldown-from-plan).
- [x] `frontend/types/api.ts` -- types + error code.
- [x] `frontend/app/admin/plans/page.tsx` -- remove antispam input.
- [x] `frontend/app/admin/users/page.tsx` -- `DefaultAntispamCard` + per-client override control.

**Acceptance Criteria:**
- Given the owner sets the global default to 15s, when a plan-holding client with `antispam_seconds=NULL` sends, then its scheduler cooldown is 15s (no plan value consulted).
- Given the owner sets a client's override to 4s, when it and a default-15s client are both active, then the override client is re-picked ~every 4s while the other waits ~15s, and the global send gap stays ≥ `g_min`.
- Given the owner clears the override (null), when the client sends, then it reverts to the current global default.
- Given a plan create/edit, when submitted, then antispam is neither accepted nor returned by the catalog, `/me`, or the public plans endpoint.
- Given the full backend suite, when `pytest` runs, then it is green with no surviving `plan.antispam_seconds` references.

## Spec Change Log

- **Review patch (2026-06-27, step-04):** edge-case review found the env fallback `settings.scheduler_default_antispam_seconds` bypassed the [1,30] bound (a `.env` value >30 would exceed `_prune_cooldowns`' 30s cutoff and re-pick a tenant before its cooldown elapsed). Added a `field_validator` in `config.py` clamping it to [1,30] at load (tests neutralize via direct assignment, which bypasses validation). Added `test_config_default_antispam_clamped_to_band`. Other review findings (blind + edge-case + self-audit) classified reject: import removals verified clean, CASE float cast normalizes Decimal, delete→orphan resolves to 0.0 (pre-feature behavior, not a regression).

- **Impl deviation (2026-06-27):** added `backend/app/config.py` `scheduler_default_antispam_seconds = 15.0` as the `get_default` fallback (the I/O matrix said "config fallback"; not in the Code Map). Mirrors `scheduler_g_min_seconds`.
- **Impl deviation (2026-06-27):** `active_senders` resolves the cooldown with a SQL `CASE` — house tenant (no client row) → `0.0`; client with override → override; else `default_antispam` — NOT a bare `coalesce(User.antispam_seconds, default)`. A plain coalesce would also hand owner/admin house tenants the default and throttle priority lanes (regression). The `coalesce` shorthand in the frozen Boundaries holds for client tenants only.
- **Impl deviation (2026-06-27):** `plans.antispam_seconds` column DROPPED (migration `c7d4e1a9b305`); per-plan values discarded on purpose. `tests/conftest.py` pins the suite's global default to `0.0` (pre-feature pace) so worker-driven integration tests aren't gated by the new default — the cooldown is tested explicitly in `tests/test_antispam.py`.

## Design Notes

`active_senders` already accepts a now-dead `global_interval` param (the flat-interval refactor neutered it); repurpose that slot as `default_antispam`:
```python
async def active_senders(session, *, default_antispam: float) -> list[ActiveSender]:
    antispam = func.coalesce(User.antispam_seconds, default_antispam)  # no Plan join
```
The default is a query-time bind param read once per worker loop (`antispam_service.get_default`), NOT pushed into the scheduler singleton — the cooldown lives on each `ActiveSender`, nothing to boot-apply. For "buy faster" to have headroom, the owner should set the default well above `g_min` (e.g. 15–20s vs 4s) — guidance, not enforced. Dropping `plans.antispam_seconds` discards per-plan values on purpose (renegotiates `spec-plan-catalog`).

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` -- expected: `users.antispam_seconds` exists, `plans.antispam_seconds` gone.
- `cd backend && .venv/bin/pytest tests/test_antispam.py -q` -- expected: pass.
- `cd backend && .venv/bin/pytest -q` -- expected: no regressions.
- `cd backend && .venv/bin/ruff check . && .venv/bin/mypy app` -- expected: clean.
- `cd frontend && npm run build` -- expected: tsc + build pass (build gate, not just lint).

## Suggested Review Order

**Cooldown resolution (the design)**

- Entry point — the SQL `CASE` that resolves each tenant's cooldown: house→0, override, else default. The plan is gone.
  [`batches.py:338`](../../backend/app/db/repos/batches.py#L338)
- The worker reads the owner default once per loop and passes it in (replaces the dead `global_interval`).
  [`send_worker.py:339`](../../backend/app/core/send_worker.py#L339)
- The global-default service: parse-or-config-fallback, bounds 1–30 (mirrors `pacing.py`).
  [`antispam.py:55`](../../backend/app/services/antispam.py#L55)

**Schema change**

- The per-user override column (nullable ⇒ use the global default).
  [`models.py:103`](../../backend/app/db/models.py#L103)
- Migration: add `users.antispam_seconds`, drop `plans.antispam_seconds`.
  [`c7d4e1a9b305:21`](../../backend/migrations/versions/c7d4e1a9b305_antispam_per_user.py#L21)
- Config fallback clamped to [1,30] at load (review patch — env footgun closed).
  [`config.py:79`](../../backend/app/config.py#L79)

**Owner API (owner-only)**

- Global default `GET`/`PUT /api/admin/antispam` (bounds + isfinite → `invalid_antispam`).
  [`admin.py:996`](../../backend/app/api/admin.py#L996)
- Per-user override `POST /api/admin/users/{id}/antispam` (set / clear / 0, client-target only).
  [`admin.py:540`](../../backend/app/api/admin.py#L540)

**UI binding (owner-only)**

- Global-default card (mirrors `SendIntervalCard`).
  [`users/page.tsx:896`](../../frontend/app/admin/users/page.tsx#L896)
- Per-client override dialog (empty = default, 0 = fastest).
  [`users/page.tsx:1286`](../../frontend/app/admin/users/page.tsx#L1286)

**Tests (supporting)**

- Service bounds, owner endpoints, per-user override, `active_senders` resolution + house→0.
  [`test_antispam.py:1`](../../backend/tests/test_antispam.py#L1)
- Suite pins the default to 0.0 (pre-feature pace) so worker tests aren't gated.
  [`conftest.py:40`](../../backend/tests/conftest.py#L40)
