---
title: 'Keys view declutter — hide claimed + auto-purge expired/revoked'
type: 'feature'
created: '2026-06-26'
status: 'done'
context: []
baseline_commit: '5d3fc00801c6006cc09536dee930179fe72af492'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The admin gift-keys view (`/admin/keys`) lists every key forever — active, claimed (canjeada), and revoked — so it grows into clutter. Keys also never go away on their own.

**Approach:** (1) Frontend: default the list to **only `active`** keys, with a toggle to reveal the rest (claimed + any not-yet-purged revoked). (2) Backend: a daily in-process background task hard-DELETEs keys that are stale — an **unclaimed key once `created_at + days` has passed**, and **any revoked key**. Claimed keys are never deleted (audit), only hidden.

## Boundaries & Constraints

**Always:**
- `gift_keys` is GLOBAL and is the mint/claim audit trail. **Claimed keys are NEVER deleted** — only hidden by the toggle.
- Expiry rule for an unclaimed key = `created_at + key.days` days (the key's own grant size doubles as its shelf life).
- Purge is set-based DELETE in a task-owned transaction (open session → repo → commit); no `FOR UPDATE` (no read-modify-write). Errors are swallowed so the task never dies (mirror `run_reconciler`).
- The hide/toggle is **pure client-side filtering** of the existing `GET /api/admin/keys` response — no API change.

**Ask First:**
- Deleting claimed keys, or changing the expiry rule away from `created_at + days`.
- Switching the purge from the in-process task to a systemd timer (see Design Notes — current plan is in-process, NOT systemd).

**Never:**
- Touch generate / claim / revoke logic.
- Auto-expire a **credits-only key** (`days == 0`): `created_at + 0` would purge it instantly. Days-rule applies only when `days > 0`.
- Add backend query params for the view filter; import Telethon; run two purge passes concurrently (single cc-core instance only).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Expired unclaimed | `active`, `days=7`, created 8d ago | DELETEd by the daily pass | error swallowed, task survives, retries next pass |
| Fresh unclaimed | `active`, `days=7`, created 2d ago | kept | N/A |
| Credits-only | `active`, `days=0`, `credits=50` | kept (days-rule skipped) | N/A |
| Claimed | `status='claimed'` | kept forever; hidden in default view | N/A |
| Revoked | `status='revoked'` | DELETEd by the daily pass | N/A |
| View default | admin opens `/admin/keys` | only `active` rows render | N/A |
| Toggle on | clicks "Mostrar todas" | claimed (+ any unpurged revoked) also render | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/db/repos/gift_keys.py` -- add `delete_stale(session) -> int`: the set-based DELETE (revoked OR expired-unclaimed). Pure ORM, flush/commit owned by caller.
- `backend/app/core/key_purge.py` -- NEW. `purge_stale_keys() -> int` (opens a session, calls repo, commits) + `run_key_purge()` infinite loop (sleep-first, daily, swallow-all). Direct port of `core/reconciler.py` shape.
- `backend/app/main.py` -- lifespan: `asyncio.create_task(run_key_purge())`, cancel + await on shutdown (mirror `reconciler_task`).
- `backend/tests/test_key_purge.py` -- NEW. Unit-test `delete_stale` against the I/O matrix rows.
- `frontend/app/admin/keys/page.tsx` -- `showAll` state; default-filter `items` to `status === 'active'`; toggle control + hidden-count hint; keep `EmptyState` when the filtered list is empty.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/repos/gift_keys.py` -- `delete_stale(session)`: `DELETE FROM gift_keys WHERE status='revoked' OR (status='active' AND days > 0 AND created_at + make_interval(days => days) < now())`; return `result.rowcount`.
- [x] `backend/app/core/key_purge.py` -- daily loop (`_PURGE_INTERVAL_SECONDS = 86400`, sleep-first), calls `delete_stale` + commit, logs `event=key_purge deleted=N`, swallows every non-cancel error.
- [x] `backend/app/main.py` -- start/stop the purge task in lifespan alongside the reconciler.
- [x] `backend/tests/test_key_purge.py` -- assert: expired-active + revoked deleted; fresh-active, credits-only (`days=0`), and claimed kept.
- [x] `frontend/app/admin/keys/page.tsx` -- add the toggle (default off → only active), filter `items`, show how many are hidden.

**Acceptance Criteria:**
- Given a daily pass runs, when an `active` key's `created_at + days` is in the past, then its row is gone from the DB; a same-age key with `days=0` (credits-only) survives.
- Given a `revoked` key exists, when the daily pass runs, then it is deleted; given a `claimed` key, it is never deleted by the pass.
- Given an admin opens `/admin/keys` with no toggle, when the page renders, then only `active` keys show; clicking "Mostrar todas" additionally reveals claimed/revoked rows.
- Given the purge task raises mid-pass, when the loop continues, then cc-core keeps running and the next pass retries.

## Design Notes

**Why in-process, not a systemd timer.** You picked "job diario". The lifespan already runs exactly this shape — `run_reconciler` is a periodic in-process asyncio task that "runs whether or not anyone opens a view". Reusing that idiom meets the requirement with **zero new systemd units / deploy wiring** and a single guaranteed cc-core instance (a separate timer process would be more infra for the same effect). If you specifically want a systemd `.timer` (e.g. estilo `cc-backup`), say so — that's the "Ask First" above.

**Expiry SQL** (Postgres, asyncpg): `created_at + make_interval(days => gift_keys.days) < now()`. Guard `days > 0` so credits-only keys never self-purge.

**Toggle** is trivial state — `items.filter(k => k.status === "active")` by default; no new endpoint. The revoke/generate flows already `invalidate` the query, so the filtered view stays fresh.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest tests/test_key_purge.py` -- expected: all matrix rows pass.
- `cd backend && .venv/bin/ruff check app/core/key_purge.py app/db/repos/gift_keys.py && .venv/bin/mypy app/core/key_purge.py` -- expected: clean.
- `cd frontend && npm run build` -- expected: tsc passes (the real gate, not just lint).

**Manual checks:**
- Open `/admin/keys`: only active keys visible; toggle reveals claimed/revoked; hidden count matches.

## Suggested Review Order

**Purge predicate (the core decision)**

- Entry point: which keys are stale — revoked OR (active AND days>0 AND past `created_at+days`); `days>0` exempts credits-only.
  [`gift_keys.py:119`](../../backend/app/db/repos/gift_keys.py#L119)
- The `make_interval(0,0,0,days)` shelf-life clause + the `or_` grouping.
  [`gift_keys.py:134`](../../backend/app/db/repos/gift_keys.py#L134)

**Daily runner**

- Purge-FIRST then sleep (not sleep-first) so deploy-on-push restarts can't starve the 24h pass — the one review-fix.
  [`key_purge.py:41`](../../backend/app/core/key_purge.py#L41)
- One pass = open session → `delete_stale` → commit (task owns the txn).
  [`key_purge.py:32`](../../backend/app/core/key_purge.py#L32)
- Lifespan wiring: created, cancelled, and awaited alongside the reconciler.
  [`main.py:105`](../../backend/app/main.py#L105)

**Frontend declutter**

- Default `showAll=false` → filter to `status==='active'`; pure client-side, no API change.
  [`page.tsx:104`](../../frontend/app/admin/keys/page.tsx#L104)
- Toggle + hidden-count bar; empty-state when all hidden.
  [`page.tsx:147`](../../frontend/app/admin/keys/page.tsx#L147)

**Peripherals**

- I/O matrix test: expired+revoked deleted; fresh, credits-only, claimed survive.
  [`test_key_purge.py:77`](../../backend/tests/test_key_purge.py#L77)
