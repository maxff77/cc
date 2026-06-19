---
title: 'Owner/admin fully exempt from credits (no charge, no UI block)'
type: 'bugfix'
created: '2026-06-19'
status: 'done'
baseline_commit: '11ba0ae33dfae221ff6677a7802fa154e16f470e'
context: ['{project-root}/CLAUDE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Owner/admin ("house") tenants get no plan grant, so their `credit_balance` is 0. The cockpit's pre-submit guard (`send-form.tsx`) blocks any costed gate at balance ≤ 0 for everyone, so admins can't send on costed gates at all. The backend already exempts them from the create/append guard (`priority > 0`), but the capture consumer still *charges* their balance on a ✅. Owner/admin should have no credit restriction whatsoever — never blocked, never charged.

**Approach:** Mirror the backend's existing `priority`-based exemption in two remaining places: (1) the capture charge skips when the originating batch's snapshotted `priority > 0`; (2) the frontend pre-submit credit block applies only to clients (`role === "client"`). Confirmed out of scope: the per-✅ charge rule itself is already correct (charges once per message that reaches ✅/approved, never on ❌/⏳) and stays untouched.

## Boundaries & Constraints

**Always:**
- 🔒 Charge only client batches: gate the existing `charge_if_first_ok` call on `batch_row.priority == 0` (0 = client; 1 = admin; 2 = owner — the `Batch.priority` snapshot, same value the create/append guard uses). Owner/admin batches: no debit, no `credits.updated` emit.
- 🔒 Preserve all current capture invariants: still call `add_full` unconditionally (capture parity), still in the same transaction, clamp logic unchanged for clients, charge stays once-per-message on first ✅.
- Frontend: a costed gate blocks the send only when the user's `role === "client"`. Owner/admin never see `blockedByCredits`. Keep the credits strip display behavior unchanged for clients.
- Backend stays authoritative; the FE change only stops a false pre-submit block — the create/append guard already lets owner/admin through.

**Ask First:** (none — scope is locked.)

**Never:** Change the per-✅ charge semantics for clients (once per message on first ✅/approved; never ❌/⏳). Touch Telethon / `core/telegram.py` / `parse_mode`, the send-worker write-ahead/fail-stop, or special-mode parsing. Add new columns or migrations (reuse the existing `Batch.priority` snapshot). Edit legacy `app.py`/`core.py`/`static/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Owner batch reaches ✅ | costed gate, owner tenant balance 30, `priority=2` | balance stays 30, NO `credits.updated` emitted, revision persisted | N/A |
| Client batch reaches ✅ | costed gate, client balance 50, `priority=0` | balance→40, `credits.updated` emitted (unchanged) | N/A |
| Admin on costed gate, FE | `role="admin"`, gate cost 10, balance 0 | send NOT blocked; submit allowed | N/A |
| Client on costed gate, FE | `role="client"`, gate cost 10, balance 0 | send blocked pre-submit (unchanged) | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/core/capture.py` (~L376) -- the `status == STATUS_OK and batch_id is not None` charge block; add `and batch_row.priority == 0` so only client batches debit/emit.
- `backend/app/api/batches.py:60` -- `_PRIORITY_BY_ROLE = {"owner":2,"admin":1,"client":0}`; the create/append guard already uses `priority == 0` (reference only, no change).
- `backend/app/db/models.py:316` -- `Batch.priority` snapshot column (0=client/1=admin/2=owner); the exemption key (reference only).
- `frontend/components/batch/send-form.tsx:63-66,153` -- `Me.role` already fetched; `blockedByCredits` ignores role. Gate it on `role === "client"`.
- `backend/tests/test_plan_credits.py` -- has `test_staff_bypass_costed_gate_at_zero_balance` (create-guard only); add a capture-charge exemption test.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/core/capture.py` -- add `batch_row.priority == 0` to the charge condition so owner/admin batches never debit nor emit `credits.updated`; client path unchanged.
- [x] `frontend/components/batch/send-form.tsx` -- derive `isMetered = (me.data?.role ?? "client") === "client"` and gate `blockedByCredits` (and the submit guard at ~L237) on it; credits strip hidden for non-clients.
- [x] `backend/tests/test_plan_credits.py` -- add `test_owner_batch_never_charged_on_ok`: owner client, costed gate, owner tenant balance set > 0, post+drain a batch, feed a ✅ to `capture.process_incoming`, assert balance unchanged and no `credits.updated`.

**Acceptance Criteria:**
- Given a costed gate and an owner/admin batch (`priority > 0`), when a reply first reaches ✅, then the tenant balance is unchanged and no `credits.updated` fires, yet the revision is still persisted.
- Given a client batch (`priority == 0`) on the same costed gate, when a reply first reaches ✅, then the balance decrements once and `credits.updated` fires (no regression).
- Given an owner/admin user in the cockpit on a costed gate at balance 0, when they submit, then the FE does not block (backend already admits them); a client in the same state is still blocked pre-submit.

## Design Notes

The `Batch.priority` snapshot is the single source of "is this a house tenant" available on the capture path — capture is tenant-keyed and the consumer has no role lookup, but it already loads `batch_row` for the cost. Reusing `priority` keeps the hot path a single extra comparison and stays consistent with the create/append guard (`batches.py:189,318`), so all three credit gates key off the same value.

```python
# capture.py, inside the status == STATUS_OK branch
if batch_row is not None and batch_row.gate_credit_cost > 0 and batch_row.priority == 0:
    charged_balance = await responses_repo.charge_if_first_ok(...)
```

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest tests/test_plan_credits.py -q` -- expected: all pass, incl. the new owner-exemption test.
- `cd backend && .venv/bin/pytest -q` -- expected: no regressions.
- `cd frontend && npm run build` -- expected: tsc + build pass (build gate, not just lint).

## Suggested Review Order

**The exemption (start here)**

- Entry point — the charge now also requires `priority == 0`, so owner/admin batches never debit nor emit `credits.updated`.
  [`capture.py:385`](../../backend/app/core/capture.py#L385)
- Why `priority` is the key: it's the same `Batch.priority` snapshot the create/append guard already uses (1=admin, 2=owner) — the comment documents the coupling.
  [`capture.py:375`](../../backend/app/core/capture.py#L375)

**Frontend mirror**

- Meter only non-staff, by explicit list, and not until `/me` resolves — keeps lockstep with backend and kills the staff block-flash.
  [`send-form.tsx:159`](../../frontend/components/batch/send-form.tsx#L159)
- The block itself now gated on `isMetered`; submit guard + disabled button both key off it.
  [`send-form.tsx:164`](../../frontend/components/batch/send-form.tsx#L164)
- Credits strip hidden for staff (would only show a misleading "Créditos: 0").
  [`send-form.tsx:338`](../../frontend/components/batch/send-form.tsx#L338)

**Test (last)**

- Owner batch reaches ✅ → balance untouched, no `credits.updated`; mutation-verified (removing the guard fails it).
  [`test_plan_credits.py:317`](../../backend/tests/test_plan_credits.py#L317)
