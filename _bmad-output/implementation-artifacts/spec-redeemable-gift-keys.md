---
title: 'Redeemable gift keys (admin-generated, days-only, fixed basic plan) with claim + audit log'
type: 'feature'
created: '2026-06-17'
status: 'in-progress'
baseline_commit: 'fde30028ad49729e550160567a51b0e7a494f18d'
context: ['{project-root}/CLAUDE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The only way to grant a client time today is the admin `renew` action (which also grants the plan's credits). The owner wants admins to mint redeemable **gift keys** that a client claims themselves to add **days only** (never credits) — like a competitor's `/claim KEY` — and wants every key auditable to catch admin abuse.

**Approach:** New `gift_keys` table. An admin/owner **generates** a single-use key choosing only `days` — they CANNOT pick the tier. The key always grants the **owner-designated basic plan** ("bronze"): the owner flags one catalog plan as the default (`Plan.is_default`), generation snapshots it onto the key. A client **claims** a key from the cockpit (active plan) or the `/expired` page (a just-registered/lapsed user): the claim extends `expires_at` by the key's days and — **only if the client has no plan yet** — assigns the snapshotted basic plan; it never touches `credit_balance`. The table itself is the audit log (who minted, who claimed, when); keys are revocable while unclaimed.

## Boundaries & Constraints

**Always:**
- 🔒 **Claim = days only, never credits.** `expires_at = max(now, current) + key.days`. Do NOT call `add_credits` / the plan credit-grant path. `days >= 1`.
- 🔒 **Basic plan assigned only when `user.plan_id IS NULL`** (a new/plan-less user). An existing client KEEPS their current plan; the key just adds days.
- 🔒 **Admins never choose the tier (anti-abuse).** Generation takes only `days`; the plan is the owner-designated default (`Plan.is_default`), resolved + snapshotted onto `gift_keys.plan_id` at generation. **Only the owner** sets `is_default`; **at most one** active default (DB-enforced partial unique index `WHERE is_default`); flipping the default clears the prior one first (dodge the index, mirroring the `is_active` flip pattern). No default configured → generation fails `no_default_plan`.
- 🔒 **Single-use under a row lock.** Claim does `SELECT … FOR UPDATE` on the key by `code`; only `status='active'` is claimable; on success set `status='claimed'`, `claimed_by_user_id`, `claimed_at` in the SAME transaction as the user update. No double-claim.
- 🔒 **Claim must work for an EXPIRED/plan-less client.** Add `get_current_user_allow_expired` (refactor `_resolve_session_user` with an `enforce_expiry` flag); the claim route uses it. The **blocked** hard-revoke and `tenant_id`-from-session rules are preserved. Claim is `role=='client'` only.
- 🔒 Identity only from session. Generate/list/revoke are `require_role("admin","owner")`; `is_default` toggle is `require_role("owner")`. Code is high-entropy from `secrets` (no ambiguous chars), format `RangerX-XXXX-XXXX-XXXX`, unique index, regenerate on collision.
- `days` in `1..PLAN_DAYS_MAX`. Repos flush-not-commit; migration before restart; Alembic naming convention. Owner oversight: the keys log exposes `created_by`, `created_at`, `claimed_by`, `claimed_at`, `status`, plan name.

**Ask First:**
- A per-(user,ip) throttle on claim beyond the high code entropy (this round: none — 71-bit codes make guessing infeasible).
- Multi-use / quota keys, unclaimed-key expiry, key-based public signup (the parallel registration session owns signup; claim only consumes an existing session).

**Never:** Letting an admin choose the key's tier or pick premium. Granting credits via a key. Re-assigning/downgrading an existing client's plan on claim. Reusing `audit_log` (tenant-scoped support reads) for key events. Touching Telethon/`core/telegram.py`/send-worker. Editing legacy `app.py`/`core.py`/`static/`. Seeding keys/plans.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error |
|----------|--------------|----------|-------|
| Generate key | admin, days 30, a default plan set | 201, key `status=active`, plan=default snapshot, code returned | days 0/neg/>max → 400 `invalid_key_days` |
| Generate, no default | no plan flagged `is_default` | rejected, no key | 409 `no_default_plan` |
| Set default plan | owner flags plan B | B is default, prior default A cleared | non-owner → 403 |
| Claim, new client | `plan_id NULL`, key days 3 | `plan_id`=key's basic plan, `expires_at=now+3d`, balance unchanged; key→claimed | — |
| Claim, existing client | active plan, 5d left, key days 3 | plan KEPT, `expires_at=now+8d`, balance unchanged | — |
| Claim by expired client | on `/expired`, gated session | claim succeeds (expiry bypassed), `/me` flips 200 → app | — |
| Claim already-claimed | key `status=claimed` | rejected, nothing applied | 409 `key_already_claimed` |
| Claim revoked / unknown | revoked or no such code | rejected | 409 `key_revoked` / 404 `key_not_found` |
| Concurrent double-claim | two requests, same code | exactly one applies; other rejected | 409 `key_already_claimed` |
| Revoke unclaimed | admin, `status=active` | `status=revoked`, `revoked_by/at` set | claimed → 409 `key_already_claimed` |
| Non-client claims | admin/owner session | rejected | 403 `forbidden` |

</frozen-after-approval>

## Code Map

- `backend/app/db/models.py` -- add `Plan.is_default` (bool, default false); add `GiftKey` model (code unique, days, plan_id FK RESTRICT snapshot, status, created_by/claimed_by/revoked_by user FKs, created_at/claimed_at/revoked_at).
- `backend/migrations/versions/<new>.py` -- add `plans.is_default` + partial unique index `WHERE is_default`; create `gift_keys` + unique `code` index; no seed.
- `backend/app/db/repos/plans.py` -- `get_default`, `set_default(plan_id)` (clear prior then set, dodging the partial index).
- `backend/app/db/repos/gift_keys.py` (new) -- `create`, `get_by_code(for_update)`, `mark_claimed`, `revoke`, `list_all` (join plan name + creator/claimer emails); flush-not-commit.
- `backend/app/services/gift_keys.py` (new) -- `generate` (resolve default plan or `no_default_plan`; code gen + collision retry; validate days); `claim` (lock key → status → if `user.plan_id is None` set snapshot → `compute_renewed_expiry(expires_at, days)` → mark claimed; NO credits); `revoke`.
- `backend/app/services/plans.py` -- `set_default` orchestration; reuse `compute_renewed_expiry`.
- `backend/app/api/keys.py` (new) -- admin router (`/api/admin/keys` POST generate {days}, GET log, `/{id}/revoke`) `require_role("admin","owner")`; client router (`/api/keys/claim`) using `get_current_user_allow_expired`, `role=='client'` guard.
- `backend/app/api/admin.py` -- extend plan schemas/route with `is_default` (owner-only set via `plans_service.set_default`).
- `backend/app/main.py` -- include both key routers.
- `backend/app/api/deps.py` -- `enforce_expiry` flag on `_resolve_session_user`; new `get_current_user_allow_expired` (skips expiry + password gates, keeps blocked revoke).
- `backend/app/errors.py` -- `invalid_key_days`, `key_not_found`, `key_already_claimed`, `key_revoked`, `no_default_plan`.
- `frontend/types/api.ts` + `frontend/lib/api.ts` -- `GiftKeyOut`, claim result, `is_default` on `PlanOut`, new error codes.
- `frontend/components/keys/claim-key.tsx` (new) -- shared claim input/submit; success → invalidate `/me` (cockpit) or `replace('/')` (expired).
- `frontend/app/admin/keys/page.tsx` (new) -- generate form (**days only**) + keys log table + revoke; mirrors `app/admin/plans/page.tsx`.
- `frontend/app/admin/plans/page.tsx` -- owner-only `is_default` toggle per plan.
- `frontend/components/ui/admin-shell.tsx` + `frontend/middleware.ts` -- register `/admin/keys` (admin+owner).
- `frontend/app/(client)/page.tsx` + `frontend/app/expired/page.tsx` -- mount `<ClaimKey/>`.

## Tasks & Acceptance

**Execution:**
- [ ] `backend/app/db/models.py` -- `Plan.is_default`; `GiftKey` model + FKs (plan RESTRICT; user FKs).
- [ ] `backend/migrations/versions/<new>.py` -- `plans.is_default` + partial unique index; `gift_keys` + unique `code`; no seed.
- [ ] `backend/app/db/repos/plans.py` -- `get_default`, `set_default`.
- [ ] `backend/app/db/repos/gift_keys.py` -- create/get_by_code(FOR UPDATE)/mark_claimed/revoke/list_all.
- [ ] `backend/app/services/gift_keys.py` -- generate (default-plan resolve + code + validate), claim (lock→status→conditional plan→days, no credits), revoke.
- [ ] `backend/app/services/plans.py` + `backend/app/api/admin.py` -- `set_default` (owner-only) wired into the plan schema/route.
- [ ] `backend/app/api/deps.py` -- `enforce_expiry` param + `get_current_user_allow_expired`.
- [ ] `backend/app/api/keys.py` + `backend/app/main.py` -- routers (client claim guarded to `role=='client'`); register.
- [ ] `backend/app/errors.py` -- 5 new error factories (Spanish messages).
- [ ] `backend/tests/test_gift_keys.py` (new) -- cover the I/O matrix: generate bounds + no-default, set-default single-default invariant, new-vs-existing claim (plan + days + no-credit assertion), expired-client claim, claimed/revoked/unknown, concurrent double-claim (one wins), revoke states, non-client 403.
- [ ] `frontend/types/api.ts` + `frontend/lib/api.ts` -- types, `is_default`, error codes.
- [ ] `frontend/components/keys/claim-key.tsx` -- shared claim component.
- [ ] `frontend/app/admin/keys/page.tsx` -- generate (days only) + log + revoke UI.
- [ ] `frontend/app/admin/plans/page.tsx` -- `is_default` toggle (owner).
- [ ] `frontend/components/ui/admin-shell.tsx` + `frontend/middleware.ts` -- `/admin/keys` nav + gate.
- [ ] `frontend/app/(client)/page.tsx` + `frontend/app/expired/page.tsx` -- mount claim component.

**Acceptance Criteria:**
- Given an admin generates a key (30 days) and a default ("bronze") plan is set, then it appears in the keys log as `active` with their identity as `created_by` and plan "bronze", and the code copies in `RangerX-XXXX-XXXX-XXXX` form; with no default plan set, generation is rejected `no_default_plan`.
- Given a plan-less client claims that key, then their plan becomes "bronze", `expires_at ≈ now+30d`, and `credit_balance` is unchanged.
- Given a client with an active plan (5 days left) claims a 3-day key, then their plan is unchanged and `expires_at ≈ now+8d`.
- Given an expired client on `/expired` claims a valid key, then the claim succeeds despite the plan-expired gate and the page recovers them into the app.
- Given a claimed or revoked key, when anyone tries to claim it, then it is rejected and no user state changes; two simultaneous claims of one active key yield exactly one success.
- Given the owner flags a second plan as default, then only that plan is default; and the keys log shows who minted and who claimed each key.

## Design Notes

Claim core (single transaction; lock serializes double-claims; credits untouched):
```python
# services/gift_keys.py
key = await gift_keys_repo.get_by_code(session, code, for_update=True)
if key is None: raise key_not_found()
if key.status == "revoked": raise key_revoked()
if key.status != "active": raise key_already_claimed()
if user.plan_id is None:            # new/plan-less only — never re-assign / downgrade
    user.plan_id = key.plan_id      # the snapshotted basic plan
user.expires_at = plans_service.compute_renewed_expiry(user.expires_at, key.days)
await gift_keys_repo.mark_claimed(session, key, user.id)
# NO add_credits — keys are time-only. caller commits.
```
Generation resolves `plans_repo.get_default()` (→ `no_default_plan` if absent) and snapshots its id onto the key, so admins can never mint a premium tier and a later default change won't re-price outstanding keys. The expiry bypass is the load-bearing subtlety: `_resolve_session_user(..., enforce_expiry=False)` lets a `plan_expired` client reach exactly one route (claim) while every other gate (blocked→401, tenant scoping) still holds — mirrors the existing `get_current_user_allow_pending_password` hole. After a claim the `/expired` poll of `/me` flips 403→200 and re-enters the user.

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` -- expected: `gift_keys` table, `plans.is_default` + both unique indexes exist.
- `cd backend && .venv/bin/pytest tests/test_gift_keys.py -q` -- expected: all pass.
- `cd backend && .venv/bin/pytest -q` -- expected: no regressions (esp. auth/deps/plans).
- `cd frontend && npm run build` -- expected: tsc + build pass (build gate, not just lint).
