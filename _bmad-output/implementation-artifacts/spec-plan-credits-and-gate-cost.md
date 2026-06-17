---
title: 'Credit balances per tenant with per-gate credit cost'
type: 'feature'
created: '2026-06-17'
status: 'done'
baseline_commit: '14484801c18c615ddcc522bf98b6c3f933e32f1c'
context: ['{project-root}/CLAUDE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Plans only sell time (days), antispam, and a line cap. The owner wants to meter usage by *successful results*: gates like `specials` should cost credits — e.g. 10 per captured ✅ — so clients pay per delivered check, not just per day.

**Approach:** Add a per-tenant `credit_balance`. Plans grant `credits` (added on assign/renew); the owner can also recharge a client manually. Each gate gets a `credit_cost`; a batch snapshots that cost at creation. When a captured reply *first* reaches ✅ for a message, deduct the snapshotted cost from the originating tenant (clamped at 0, once per message). At balance 0 the client keeps using free (cost-0) gates but is blocked from costed ones.

## Boundaries & Constraints

**Always:**
- 🔒 Charge **once per message** (`chat_id`+`message_id`) that reaches `status='ok'`, never per revision. Idempotent across re-edits (✅→❌→✅) and capture retries.
- 🔒 Deduct inside the **capture consumer**, in the **same transaction** as `add_full`, with `SELECT … FOR UPDATE` on the tenant row. Clamp at 0 (never negative); never block or drop a capture for credits — capture parity is sacred.
- 🔒 Charge the **snapshot** `batches.gate_credit_cost` (copied at create like `gate_value`), not the live gate — editing a gate's cost never re-prices in-flight/historical batches.
- 🔒 `credit_balance` lives on `tenants` (capture is tenant-keyed); plan grant and recharge write it via the user's `tenant_id`.
- Block costed gates (`credit_cost > 0`) at batch **create AND append** when `credit_balance <= 0` → `insufficient_credits`. Free gates always allowed. Backend authoritative; FE pre-submit mirrors it.
- `tenant_id` only from session; all credit/gate/plan/recharge endpoints owner-only via `require_role`. Bounds `>= 0`, all default 0 (backward compatible: existing gates cost 0, tenants start at 0).
- Repos flush-not-commit; migration before restart; Alembic naming convention.

**Ask First:** Stopping a *running* batch when its tenant hits 0 mid-batch (this round it continues; overrun ✅s charge 0 via the clamp). Any per-line/per-send cost (this round is per-✅ only).

**Never:** Negative balances or per-revision charging. Touching Telethon/`core/telegram.py`/`parse_mode` or the send-worker write-ahead/fail-stop. Exposing gate `value` to clients (cost may show; `value` stays owner-only). Editing legacy `app.py`/`core.py`/`static/`. Seeding data.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error |
|----------|--------------|----------|-------|
| First ✅, costed gate | cost 10, balance 50 | balance→40, response persisted | — |
| Re-edit / capture retry | message already charged | balance unchanged, revision persisted | retry-forever, no double charge |
| Overrun past zero | balance 5, cost 10, ✅ | balance→0 (clamp), persisted | — |
| Free gate | cost 0 | no deduction, no block | — |
| Create/append, broke | cost>0, balance 0 | 403 `insufficient_credits`, nothing queued; FE blocks | message names gate |
| Assign / renew plan | plan `credits=100` | balance += 100 each time | — |
| Owner recharge | set balance 200 | balance=200 | <0 → 400 |
| Invalid bounds | negative cost / credits | 400 `invalid_gate` / `invalid_plan` | field message |

</frozen-after-approval>

## Code Map

- `backend/app/db/models.py` -- add `Tenant.credit_balance`, `Plan.credits`, `Gate.credit_cost`, `Batch.gate_credit_cost` (Integer, default 0, NOT NULL).
- `backend/migrations/versions/<new>.py` -- add the 4 columns, server_default `0`; no seed.
- `backend/app/db/repos/responses.py` -- `charge_if_first_ok(tenant_id, chat_id, message_id, cost) -> int|None`: prior-`ok` existence check (excl. row being added); if none and cost>0, `SELECT tenant FOR UPDATE`, `balance=max(0,balance-cost)`, return new balance; else None.
- `backend/app/db/repos/tenants.py` -- `set_credit_balance`, `add_credits` (FOR UPDATE).
- `backend/app/core/capture.py` -- on `status='ok'`, call `charge_if_first_ok` with `batch.gate_credit_cost` in the add_full transaction; emit `credits.updated` only on a real charge.
- `backend/app/db/repos/batches.py` -- snapshot `gate_credit_cost` at create; expose on the capture-side batch read.
- `backend/app/services/batches.py` + `app/api/batches.py` -- snapshot cost; enforce `insufficient_credits` on create + append.
- `backend/app/services/plans.py` + `app/services/users.py` -- add `plan.credits` to balance on assign/renew.
- `backend/app/api/admin.py` -- gate `credit_cost` + plan `credits` schemas/validation; owner-only `POST /users/{id}/credits` (set absolute balance).
- `backend/app/api/gates.py` -- public gate read includes `credit_cost` (not `value`).
- `backend/app/api/auth.py` -- `/me` includes `credits: { balance }`.
- `frontend/types/api.ts` + `frontend/lib/ws.ts` -- types; `credits.updated` + snapshot balance reducer.
- `frontend/components/batch/send-form.tsx` -- per-gate cost + balance; pre-submit block on costed gates at 0.
- `frontend/app/admin/{gates,plans,users}/page.tsx` -- cost input, credits input, balance + recharge control.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/models.py` -- 4 new columns (Integer, default 0, NOT NULL).
- [x] `backend/migrations/versions/<new>.py` -- migration for the 4 columns, server_default 0.
- [x] `backend/app/db/repos/responses.py` -- `charge_if_first_ok` (prior-ok check + clamped FOR-UPDATE decrement).
- [x] `backend/app/db/repos/tenants.py` -- balance set/add (FOR UPDATE).
- [x] `backend/app/core/capture.py` -- charge with batch snapshot cost in the add_full txn; emit `credits.updated`.
- [x] `backend/app/db/repos/batches.py` -- snapshot `gate_credit_cost`; include in capture-side read.
- [x] `backend/app/{services,api}/batches.py` -- `insufficient_credits` guard on create + append.
- [x] `backend/app/services/{plans,users}.py` -- grant `plan.credits` on assign/renew.
- [x] `backend/app/api/admin.py` -- gate cost + plan credits schemas/validation; recharge endpoint.
- [x] `backend/app/api/{gates,auth}.py` -- expose `credit_cost`; `me.credits.balance`.
- [x] `backend/tests/test_plan_credits.py` (new) -- cover the I/O matrix: first-✅ charge, no double-charge on re-edit/retry, clamp, free gate, create/append block, plan grant, renew accumulate, recharge, invalid bounds.
- [x] `frontend/types/api.ts` + `frontend/lib/ws.ts` -- types, reducer, snapshot balance.
- [x] `frontend/components/batch/send-form.tsx` -- cost/balance display + pre-submit block.
- [x] `frontend/app/admin/{gates,plans,users}/page.tsx` -- cost input, credits input, recharge control.

**Acceptance Criteria:**
- Given a `credit_cost=10` gate and tenant balance 50, when a reply first shows ✅, then balance becomes 40 and stays 40 across later edits of that same message.
- Given balance 0, when starting or appending a batch on a costed gate, then FE and backend reject `insufficient_credits`, yet a cost-0 gate still sends.
- Given a plan with `credits=100`, when assigned then renewed, then balance rises by 100 each time; owner recharge to 200 sets it to 200 and the cockpit updates live.
- Given balance 5 on a cost-10 gate, when a ✅ is captured, then balance clamps to 0 (never negative) and the response is still persisted.

## Design Notes

Charge ordering inside the capture transaction (single consumer ⇒ no capture-vs-capture race; FOR UPDATE guards vs owner recharge):

```python
# capture.py, status == 'ok'
new_balance = await responses_repo.charge_if_first_ok(
    tenant_id, chat_id, message_id, cost=batch.gate_credit_cost)
await responses_repo.add_full(...)        # always — capture parity preserved
# same session/transaction → atomic commit
if new_balance is not None:
    await broadcaster.emit(tenant_id, "credits.updated", {"balance": new_balance})
```

The prior-`ok` existence check runs before `add_full` (or excludes the new row) so "once per message" holds; it returns `None` when no charge happened (not first ok, or cost 0) so the WS event fires only on a real deduction. Balance on `tenants` keeps the hot capture path a single tenant-keyed update; plan grants and recharges reach it through the user→tenant link.

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` -- expected: 4 columns added, default 0.
- `cd backend && .venv/bin/pytest tests/test_plan_credits.py -q` -- expected: all pass.
- `cd backend && .venv/bin/pytest -q` -- expected: no regressions.
- `cd frontend && npm run build` -- expected: tsc + build pass (build gate, not just lint).

## Suggested Review Order

**The charge engine (start here)**

- Entry point — the once-per-✅ debit, before add_full, same transaction, emit on real charge.
  [`capture.py:342`](../../backend/app/core/capture.py#L342)
- The idempotent predicate + clamped FOR-UPDATE decrement (handles ✅→❌→✅).
  [`responses.py:117`](../../backend/app/db/repos/responses.py#L117)
- Prior-`ok` existence check keyed on (chat_id, message_id).
  [`responses.py:91`](../../backend/app/db/repos/responses.py#L91)
- Balance read/modify/write under a row lock (charge + recharge + grant share it).
  [`tenants.py:41`](../../backend/app/db/repos/tenants.py#L41)

**Schema (the snapshot is load-bearing)**

- Four columns, all Integer default 0 NOT NULL — tenant balance, gate cost, batch snapshot, plan grant.
  [`models.py:42`](../../backend/app/db/models.py#L42)
- Migration: adds the 4 columns server_default 0; backward compatible.
  [`c5b8e1f9a2d4_credits.py:1`](../../backend/migrations/versions/c5b8e1f9a2d4_credits.py#L1)

**Guards & API surface**

- Snapshot the gate cost at batch create + block costed gates at balance 0 (clients only).
  [`batches.py:152`](../../backend/app/api/batches.py#L152)
- Owner-only recharge endpoint (absolute set, bounds, live emit).
  [`admin.py:485`](../../backend/app/api/admin.py#L485)
- Plan grant tops up the tenant on renewal (assign sets in create_account).
  [`admin.py:386`](../../backend/app/api/admin.py#L386)

**Frontend binding**

- Per-gate cost + balance pre-submit guard in the cockpit.
  [`send-form.tsx:143`](../../frontend/components/batch/send-form.tsx#L143)
- WS store: `credits.updated` reducer + snapshot balance, tenant-scoped.
  [`ws.ts:411`](../../frontend/lib/ws.ts#L411)

**Tests (last)**

- I/O matrix coverage: charge-once, clamp, free gate, create/append block, staff bypass, grant, recharge, bounds.
  [`test_plan_credits.py:1`](../../backend/tests/test_plan_credits.py#L1)
