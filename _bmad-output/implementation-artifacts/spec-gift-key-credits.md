---
title: 'Gift keys grant admin-chosen credits (optional), with credits-only (days=0) keys'
type: 'feature'
created: '2026-06-23'
status: 'done'
baseline_commit: '4a4202267b8c3aa2b8b846c66937881270afbe1c'
context: ['{project-root}/CLAUDE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Gift keys today grant **days only** (a hard anti-abuse rule). Giving a client credits requires the owner-only manual recharge in `/admin/users` (done by hand). The owner wants a key the client redeems himself that can also carry **credits** — days-only, credits-only, or **both** in one key. A `days=0` key with `credits>0` is a **credits-only** key.

**Approach:** Add an optional `credits` column to `gift_keys`, chosen by the admin at mint (the owner explicitly accepts relaxing "admin never picks value" for credits). Allow `days=0` (a key must still grant something). Claiming adds days exactly as today **and** adds `credits` to the tenant's `credit_balance`, emitting the same `credits.updated` WS event the recharge path does. The owner-only manual recharge is untouched.

## Boundaries & Constraints

**Always:**
- 🔒 **A key must grant something.** `days` in `0..KEY_DAYS_MAX`, `credits` in `0.._PG_INT_MAX`; reject `days==0 AND credits==0` (new error `empty_gift_key`). All three combos ship: days-only, credits-only, days+credits.
- 🔒 **Days/plan logic gated on `days > 0`.** When `days>0`: extend `expires_at` via `compute_renewed_expiry` + assign snapshot plan ONLY if `user.plan_id IS NULL` (unchanged today). When `days==0`: do NOT touch `plan_id` or `expires_at` — never assign an instantly-expired plan.
- 🔒 **Credits via the existing money path.** `tenants_repo.add_credits(session, user.tenant_id, key.credits)` (row-locked, returns new balance) in the SAME transaction as the single-use claim lock — granted exactly once. After commit emit `credits.updated {"balance": new}` to the claimer's tenant (mirror `admin.recharge_credits`).
- 🔒 **Single-use + concurrency unchanged.** Claim locks key + user `FOR UPDATE`; only `status='active'` claimable. Snapshot plan still required (`plan_id` NOT NULL even credits-only → still `no_default_plan` with no active default). Identity from session; generate/list/revoke `require_role("admin","owner")`; claim `role=='client'` via `get_current_user_allow_expired`.
- `credits` server_default `'0'`, not null. Flush-not-commit; migration before restart; Alembic naming convention; down_revision `e7d2c9a4b1f8`.

**Ask First:**
- Restricting credit-bearing key minting to owner-only, or a per-key credit cap below `_PG_INT_MAX` (this round: admins may mint any non-negative amount, matching the user's choice).

**Never:** Touching the owner-only manual recharge endpoint. Granting credits when `days>0 && credits==0` (a plain days key adds zero credits). Assigning a plan or extending expiry on a `days==0` key. Re-pricing outstanding keys when the default plan changes. Touching Telethon / send-worker / legacy `app.py`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error |
|----------|--------------|----------|-------|
| Mint days-only | admin, days 30, credits 0 | 201, key days=30 credits=0 | — |
| Mint credits-only | admin, days 0, credits 50 | 201, key days=0 credits=50 | — |
| Mint days+credits | admin, days 30, credits 50 | 201, key days=30 credits=50 | — |
| Mint empty | days 0, credits 0 | rejected | 400 `empty_gift_key` |
| Mint bad days | days <0 / >max | rejected | 400 `invalid_key_days` |
| Mint bad credits | credits <0 / >_PG_INT_MAX | rejected | 400 `invalid_credits` |
| Claim days+credits, new client | plan_id NULL, days 30 credits 50, balance 0 | plan=snapshot, expires≈now+30d, balance 50; key→claimed; `credits.updated` emitted | — |
| Claim credits-only, active client | active plan, 5d left, days 0 credits 50, balance 10 | plan KEPT, expires UNCHANGED, balance 60; key→claimed | — |
| Claim days-only (credits 0) | days 7 credits 0 | days added, balance UNCHANGED, no `credits.updated` | — |
| Claim credits-only, plan-less client | plan_id NULL, days 0 credits 50 | balance +50, plan_id STILL NULL, expires UNCHANGED (credits ≠ access) | — |
| Concurrent double-claim | two requests, same key | exactly one applies credits+days; other rejected | 409 `key_already_claimed` |

</frozen-after-approval>

## Code Map

- `backend/app/db/models.py` -- `GiftKey.credits` (int, server_default `'0'`, not null), mirroring `Plan.credits`.
- `backend/migrations/versions/<new>_gift_key_credits.py` -- add `gift_keys.credits` (default 0). down_revision `e7d2c9a4b1f8` (current head). No seed.
- `backend/app/db/repos/gift_keys.py` -- `create(...)` gains `credits: int = 0`, sets it on the row. `list_all` already returns the whole `GiftKey` row → `credits` rides along.
- `backend/app/services/gift_keys.py` -- `generate(...)` gains `credits: int = 0` (snapshots onto the key). `claim(...)`: gate days/plan block on `key.days > 0`; if `key.credits > 0` call `tenants_repo.add_credits`; return `(user, days_added, credits_added, new_balance)`.
- `backend/app/api/keys.py` -- `GenerateKeyRequest.credits: int = 0`; route validates days `0..KEY_DAYS_MAX`, credits `0.._PG_INT_MAX`, both-zero → `empty_gift_key`. `GiftKeyOut.credits`, `_key_to_out` passes it. `ClaimKeyResult.credits_added`; claim route emits `credits.updated` when `credits_added>0` (import `broadcaster`, mirror `admin.recharge_credits`).
- `backend/app/errors.py` -- add `empty_gift_key()` (400, Spanish), mirroring `invalid_key_days`. Reuse existing `invalid_credits` / `invalid_key_days`.
- `frontend/app/admin/keys/page.tsx` -- `GiftKeyOut.credits`; add "Créditos" field to `GenerateKeyForm`; relax validation (allow days 0, require days≥1 OR credits≥1); send `{days, credits}`; log row + success notice show credits; fix helper copy.
- `frontend/components/keys/claim-key.tsx` -- `ClaimResult.credits_added`; success message composes "+N días" and/or "+N créditos".

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/models.py` -- add `GiftKey.credits`.
- [x] `backend/migrations/versions/f4b9c2e7a1d3_gift_key_credits.py` -- add column, down_revision `e7d2c9a4b1f8`. Applied: `upgrade head` ran clean.
- [x] `backend/app/errors.py` -- `empty_gift_key()` factory; also relaxed `invalid_key_days` message ("0 o más") since days now bound `0..`.
- [x] `backend/app/db/repos/gift_keys.py` -- `create` accepts/sets `credits` (default 0).
- [x] `backend/app/services/gift_keys.py` -- `generate` snapshots credits; `claim` grants credits when >0, days/plan only when days>0, returns `(user, days, credits, new_balance)`.
- [x] `backend/app/api/keys.py` -- request/response schemas + validation (days `0..`, credits `0.._PG_INT_MAX`, both-zero → `empty_gift_key`) + `credits.updated` emit on claim.
- [x] `frontend/app/admin/keys/page.tsx` -- credits field + relaxed validation + `grantLabel` display.
- [x] `frontend/components/keys/claim-key.tsx` -- `credits_added` in claim result + composed success copy. (Mounts in `/expired` + `key-modal` consume only `onClaimed()`, no field reads — unaffected.)
- [x] `backend/tests/test_gift_keys.py` -- 5 new tests (credits-only/both/empty/bad-credits mint; claim credits-only active, days+credits new client, credits-only plan-less). Fixed `test_generate_invalid_days` params (dropped `0`, now valid). **23 pass; full suite 546 pass.**

**Acceptance Criteria:**
- Given an admin mints a key with days 0 and credits 50, then it is created as a credits-only key; a key with days 0 and credits 0 is rejected `empty_gift_key`.
- Given a client with an active plan (5 days left) claims a days-0 credits-50 key, then their plan and `expires_at` are unchanged and `credit_balance` rises by 50.
- Given a plan-less client claims a days-30 credits-50 key, then they get the basic plan, `expires_at≈now+30d`, and `credit_balance` rises by 50.
- Given any claim that adds credits, then a `credits.updated` event reaches the claimer's tenant with the new balance; a days-only (credits 0) claim emits none and leaves the balance untouched.
- Given two simultaneous claims of one active key, then exactly one applies the credits and days; the other is rejected.

## Design Notes

Claim core (single transaction; lock serializes double-claims; days/plan only when days>0):
```python
# services/gift_keys.py
if key.days > 0:
    if user.plan_id is None:        # plan-less only — never re-assign/downgrade
        user.plan_id = key.plan_id
    user.expires_at = plans_service.compute_renewed_expiry(user.expires_at, key.days)
new_balance = None
if key.credits > 0:                 # credits-only keys have days==0; both can be >0
    new_balance = await tenants_repo.add_credits(session, user.tenant_id, key.credits)
await gift_keys_repo.mark_claimed(session, key, claimed_by_user_id=user.id)
return user, key.days, key.credits, new_balance
```
**Credits ≠ access (intended):** a credits-only key claimed by a plan-less/expired client adds credits but does NOT restore access — they stay locked until they redeem a days key or the owner renews. Credits are a balance, not time; the typical credits-only consumer is an already-active client topping up.

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` -- `gift_keys.credits` column exists.
- `cd backend && .venv/bin/pytest tests/test_gift_keys.py -q` -- all pass (new + existing).
- `cd backend && .venv/bin/pytest -q` -- no regressions.
- `cd frontend && npm run build` -- tsc + build pass (the real gate, not just lint).

## Suggested Review Order

**The claim engine (start here)**

- Credit grant + days-gating: plan/expiry only when `days>0`, credits added on the existing money path inside the single-use lock; reports what was actually granted.
  [`gift_keys.py:115`](../../backend/app/services/gift_keys.py#L115)
- Live cockpit update: emit `credits.updated` after commit only when credits actually landed (`new_balance is not None`).
  [`keys.py:198`](../../backend/app/api/keys.py#L198)

**Mint validation (anti-empty / bounds)**

- Days `0..MAX`, credits `0.._PG_INT_MAX`, reject the empty key (both zero) → `empty_gift_key`.
  [`keys.py:130`](../../backend/app/api/keys.py#L130)
- New error factory + relaxed `invalid_key_days` copy (days now allow 0).
  [`errors.py:490`](../../backend/app/errors.py#L490)

**Schema**

- `GiftKey.credits` column (server_default 0, not null) — mirrors `Plan.credits`.
  [`models.py:980`](../../backend/app/db/models.py#L980)
- Migration adds the column; down_revision `e7d2c9a4b1f8`.
  [`f4b9c2e7a1d3:24`](../../backend/migrations/versions/f4b9c2e7a1d3_gift_key_credits.py#L24)

**Frontend**

- Mint form: days + credits fields, relaxed validation (at least one > 0).
  [`page.tsx:232`](../../frontend/app/admin/keys/page.tsx#L232)
- Claim success copy composes days and/or credits.
  [`claim-key.tsx:56`](../../frontend/components/keys/claim-key.tsx#L56)
- `/expired` redirect guard: only enter the app when a key granted DAYS (credits-only stays put, no bounce).
  [`expired/page.tsx:78`](../../frontend/app/expired/page.tsx#L78)

**Tests (last)**

- Credits-only / both / empty / bad-credits mint + claim balance assertions.
  [`test_gift_keys.py:411`](../../backend/tests/test_gift_keys.py#L411)
