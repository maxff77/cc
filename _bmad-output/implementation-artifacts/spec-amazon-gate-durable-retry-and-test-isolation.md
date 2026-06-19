---
title: 'Amazon gate Phase 2 hardening — durable verdict-timeout retry + test isolation'
type: 'bugfix'
created: '2026-06-19'
status: 'done'
context: []
baseline_commit: 'c5ca505'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Two deferred Phase-2 items. (1) The cookie-mode verdict-timeout retry-once budget lives in a process-memory set (`send_worker._timeout_retried`), reset on restart. In a crash loop around the 90s timeout, boot recovery re-arms a fresh 90s window but cannot restore the retried flag, so a permanently-silent line gets a fresh retry EVERY restart — re-sending a fresh `.cookie`+`.amz` pair on the shared account instead of pausing after the single mandated retry. (2) The backend test suite is not isolation-clean: the autouse conftest resets scheduler/capture/watchdog/alerts but NOT the send-worker process-memory singletons (`_sent_by_tenant`, the `cookie_verdict` queue), so state leaks across the full run (~10-20% intermittent failures when modules run after others).

**Approach:** Persist the retry budget as a per-line column (`batch_lines.verdict_timeout_retries`) mutated atomically in the same txn/lock as the existing requeue+await changes; the sweep and boot recovery then read durable state instead of process memory. Add an autouse `reset_send_worker` conftest fixture mirroring the existing `reset_*` pattern, and make the one wall-clock-fragile scheduler test deterministic, so the suite is reliably green.

## Boundaries & Constraints

**Always:** Preserve the exact retry-ONCE semantic (one timeout-retry, then pause `verdict_timeout` + owner WARNING). Mutate the counter ONLY inside the existing batch `FOR UPDATE` txns (no post-commit process-memory write). A fresh `.amz` attempt = a fresh budget: reset the counter to 0 at the single choke point `requeue_line_with_intent_reset` (covers rotation, resume, and the timeout-resend base); `_resend_cookie_line` then increments it. Boot recovery keeps re-arming the await ONLY — never touch the persisted counter. Migration runs before restart; `down_revision = a7c3e9f1b204` (current head). New test fixtures reset process memory only (never DB rows / the watchdog row).

**Ask First:** The wall-clock flake `test_flood_window_gates_the_next_claim` is a SEPARATE deferred item (scheduler timing, predates AMZ, fails in isolation too) folded into Goal 2 only so the suite is truly green — confirm at checkpoint, or keep it deferred. Column shape: a SMALLINT counter (future-proof for retry-N) vs a boolean flag — counter chosen; flag if a boolean is preferred.

**Never:** No change to the 90s timeout, the atomic-pair/serialize-gate/attempt-fence/rotation semantics, or reply classification. No global FakeGateway message-id offset (tests hardcode `reply_to_msg_id`; renumbering breaks them) — isolation comes from resetting the singletons, not renumbering ids. No frontend, no new product behavior. Do not touch legacy `app.py`/`core.py`/`static/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First timeout elapse | awaiting line, `awaiting_verdict_until` passed, `verdict_timeout_retries=0` | resend pair (fresh cookie + new message_id); counter → 1 persisted in-txn | DB down → retry-forever (existing fail-stop) |
| Second timeout elapse | same line, counter `>=1` | pause batch `verdict_timeout` + owner WARNING; no resend | N/A |
| Restart after one retry | counter `=1`, crash, boot re-arms fresh 90s | next elapse reads persisted `1` → PAUSE (not another resend on the shared account) | N/A |
| Fresh attempt re-queue | cookie-dead rotation / resume / timeout-resend base calls `requeue_line_with_intent_reset` | counter reset to 0 (new cookie attempt gets its own one-retry budget) | N/A |
| Suite isolation | affected modules run after heavy ones | autouse `reset_send_worker` clears `_sent_by_tenant` + `cookie_verdict` per test → deterministic | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/db/models.py:473` -- `BatchLine`; add `verdict_timeout_retries` column near `failed_cookie_id`.
- `backend/app/db/repos/batches.py:581` -- `requeue_line_with_intent_reset` (reset counter to 0); add `mark_verdict_retried`. `awaited_line_id:543` already resolves the in-flight line under the lock (reused by the sweep).
- `backend/app/core/send_worker.py:125` -- `_timeout_retried` set (remove); `_resend_cookie_line:885` (mark durable in-txn), `_apply_verdict:976/1038` (remove discards), `_sweep_verdict_timeouts:1101/1114` (read the column). Boot recovery re-arm `:1516` stays UNCHANGED.
- `backend/migrations/versions/d3f1a8c5e9b2_batch_line_verdict_timeout_retries.py` -- add the column, `down_revision='a7c3e9f1b204'`.
- `backend/tests/conftest.py` -- new autouse `reset_send_worker` (process memory) + autouse async `clean_send_capture_domain` (per-test DB wipe of the send/capture tables), both beside `reset_capture`.
- `backend/tests/test_amazon_rotation.py:92` -- local fixture drops the now-removed `_timeout_retried`; add a durable-retry-across-restart regression.
- `backend/tests/test_send_hardening.py:613` -- make `test_flood_window_gates_the_next_claim` clock-deterministic.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/models.py` -- added `verdict_timeout_retries: Mapped[int]` (`SmallInteger`, `nullable=False`, `server_default="0"`) to `BatchLine`. Used the string `"0"` (not `text("0")`) — inside the class body the imported `text` is shadowed by the `text` column.
- [x] `backend/migrations/versions/d3f1a8c5e9b2_batch_line_verdict_timeout_retries.py` -- new revision (`down_revision='a7c3e9f1b204'`): `add_column` `server_default='0'`, `nullable=False`; `drop_column` on downgrade. Deploy-safe NOT NULL (server_default fills the pre-restart window).
- [x] `backend/app/db/repos/batches.py` -- `requeue_line_with_intent_reset` sets `line.verdict_timeout_retries = 0` (fresh intent ⇒ fresh budget); added `mark_verdict_retried(session, line)` → `+= 1; flush`.
- [x] `backend/app/core/send_worker.py` -- deleted `_timeout_retried`; `_resend_cookie_line` calls `mark_verdict_retried` inside the existing txn before commit (replacing the post-commit `.add`); `_sweep_verdict_timeouts` reads `line.verdict_timeout_retries` under the lock (carried in the tuple) and branches `>=1` → pause else resend; removed the `.discard` calls in `_apply_verdict`. Boot recovery untouched.
- [x] `backend/tests/conftest.py` -- autouse `reset_send_worker` fixture: `send_worker._sent_by_tenant.clear()` + `cookie_verdict.reset()` before/after each test (mirror `reset_capture`).
- [x] `backend/tests/conftest.py` -- autouse async `clean_send_capture_domain` fixture: after each test delete `Response`/`SendLog`/`BatchLine`/`Batch`/`CaptureSession` (child→parent, FK-safe). DB-isolation sibling — kills the cross-test leaks the global `count_active_senders` / `(chat_id,message_id)` attribution see (the residual flake the Design Notes authorized as the fallback). Verified compatible with sync test files.
- [x] `backend/tests/test_amazon_rotation.py` -- removed the redundant local `reset_cookie_verdict` fixture (referenced the now-deleted `_timeout_retried`; conftest covers the queue/sent reset); fixed the stale `_timeout_retried` comment; added `test_verdict_timeout_retry_budget_is_durable_across_restart` (retry once → wipe process memory → second elapse pauses `verdict_timeout`, no further send).
- [x] `backend/tests/test_send_hardening.py` -- rewrote `test_flood_window_gates_the_next_claim` deterministically: stub `send_worker.sleep_paced` to record its duration and return instantly (no real sleep, no cancellation — `sleep_paced` is uninterruptible), assert step slept ~the open 30s window before claiming + sent one line.

**Acceptance Criteria:**
- Given a cookie line that already used its one timeout-retry (`verdict_timeout_retries=1` persisted) and the worker restarts (boot re-arms a fresh await), when the new window elapses silent, then the batch pauses `verdict_timeout` after the single retry — NOT another `.cookie`+`.amz` resend per restart.
- Given a cookie-dead rotation or a pause-resume re-queues the awaited line, when it is re-queued via `requeue_line_with_intent_reset`, then `verdict_timeout_retries` is 0 (the new cookie attempt owns a fresh one-retry budget).
- Given the first timeout elapse on a `counter=0` line, when the sweep runs, then exactly one resend occurs and the counter is 1 (the pre-existing behavior, now durable across restart).
- Given the full backend suite runs the affected modules after the heavy ones, when repeated 3×, then no isolation-caused failures remain.
- Given `alembic upgrade head` then `downgrade -1`, when run, then the column adds and drops cleanly.

## Spec Change Log

**Review — boot-recovery counter-reset finding → REJECTED (false positive).** An adversarial reviewer flagged `send_worker._boot_recovery`'s cookie-mode re-arm (`set_awaiting_verdict`) for NOT resetting `verdict_timeout_retries`, arguing the re-armed line should get a fresh retry. This is the EXACT OPPOSITE of the frozen Intent + AC: the spec exists because a crash loop must NOT grant a fresh retry per restart. Boot recovery re-arms the SAME in-flight attempt that existed at crash (the resent `.amz` already burned the one retry → counter=1); on the next elapse it must PAUSE, which the durable counter correctly does. Resetting on boot would reintroduce the documented crash-loop bug. No code change. `test_verdict_timeout_retry_budget_is_durable_across_restart` locks this behavior. KEEP: boot recovery touches ONLY the await, never the counter.

**Verification — AC "no isolation failures" partially met; residual re-deferred.** The process-memory `reset_send_worker` + the DB after-wipe `clean_send_capture_domain` eliminated the DATA-leak isolation flakes (`test_paused_tenant…`, `test_delete_guarded…`, `test_plans_catalog`, the dedup test were stable across 5 full-suite runs). A RESIDUAL ~1/5 intermittent flake remains in the `test_amazon_rotation` multiline verdict-timeout tests (0/10 in isolation; only in the full suite) — rooted in the shared asyncpg connection pool under ONE session-scoped event loop, the deeper issue the deferred note named ("ideally a function-scoped event loop"). Two clean attempts were REJECTED: a pre-test wipe broke a dedup test (deleted setup data); a test-only `NullPool` broke 3 unrelated tests (transactional-visibility/timing change). The proper fix is a function-scoped event-loop rewrite (large, risky test-harness change) — re-deferred to `deferred-work.md`. The durable-retry product code (Goal 1) is unaffected and fully verified.

## Design Notes

Centralize the budget reset at `requeue_line_with_intent_reset` — it is the ONE point every fresh `.amz` attempt flows through (cookie-dead rotation at `_apply_verdict:1018`, resume via `requeue_failed_cookie_line:632`, timeout-resend base at `_resend_cookie_line:869`). Resetting there means "fresh intent ⇒ fresh budget" falls out automatically; `_resend_cookie_line` is the only caller that then bumps the counter to 1. The durable column IS the flag the old process-memory set could never survive a crash for — boot recovery already re-arms `awaiting_verdict_until`, so the persisted counter + the re-armed await together reproduce the in-process retry-once across a crash loop with zero boot-recovery changes.

Isolation: the suite's `reset_scheduler`/`reset_capture`/`reset_watchdog`/`reset_alerts` are the template — add `reset_send_worker` for the singletons the conftest predated (cookie-mode is Phase-2). FakeGateway id renumbering is deliberately NOT done: many modules hardcode `reply_to_msg_id=1` against the first send, so a global offset breaks them; per-test singleton reset is the correct, non-breaking lever.

The process-memory reset alone left a RESIDUAL flake (different tests failed across repeated full-suite runs: `test_paused_tenant…`, `test_delete_guarded…`). Root cause is the DB-row leak the deferred note also named: the worker queries are GLOBAL (`count_active_senders` spans all tenants; attribution keys on `(chat_id, message_id)` which the FakeGateway restarts at 1 each test), and a test that posts a batch via a session-scoped tenant (owner/admin) without stopping/draining it leaks 'sending'/'paused' batches + `send_log` rows that a LATER test's global count/attribution then picks up. The fix is the DB-level sibling of `reset_send_worker`: the autouse `clean_send_capture_domain` fixture wipes the send/capture tables after each test. It is an async fixture (must run on the session loop — the asyncpg engine is loop-bound, so `asyncio.run` in a sync fixture would attach to the wrong loop) and is verified harmless to pure-sync test files. This is the "function-scoped DB cleanup" fallback this note already authorized.

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` -- expected: head = the new revision, no error.
- `cd backend && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head` -- expected: column drops + re-adds cleanly.
- `cd backend && .venv/bin/python -c "import app.core.send_worker"` -- expected: imports clean (no `_timeout_retried`).
- `cd backend && grep -rn "_timeout_retried" app/ tests/` -- expected: only doc/comment mentions, no code references.
- `cd backend && .venv/bin/pytest tests/test_amazon_rotation.py tests/test_send_hardening.py -q` -- expected: all green in isolation.
- `cd backend && for i in 1 2 3 4; do .venv/bin/pytest tests/ -q -p no:randomly || break; done` -- expected: deterministic green across all runs (isolation flake eliminated).

## Suggested Review Order

**Durable verdict-timeout retry (Goal 1)**

- Entry point — the decision: read the durable counter under the batch lock, branch `>=1` → pause else resend (replaces the process-memory set).
  [`send_worker.py:1056`](../../backend/app/core/send_worker.py#L1056)

- The durable state itself — the per-line counter that survives a restart.
  [`models.py:524`](../../backend/app/db/models.py#L524)

- The single choke point: every fresh `.amz` attempt zeroes the budget here; `mark_verdict_retried` bumps it.
  [`batches.py:601`](../../backend/app/db/repos/batches.py#L601)

- The one place the budget is burned — in-txn, atomic with the requeue (no post-commit process memory).
  [`send_worker.py:876`](../../backend/app/core/send_worker.py#L876)

- The crux comment: why boot recovery re-arms the await ONLY and never the counter (the crash-loop fix).
  [`send_worker.py:121`](../../backend/app/core/send_worker.py#L121)

- Schema: deploy-safe NOT NULL with `server_default='0'`, `down_revision='a7c3e9f1b204'`.
  [`d3f1a8c5e9b2_…py:33`](../../backend/migrations/versions/d3f1a8c5e9b2_batch_line_verdict_timeout_retries.py#L33)

**Test isolation (Goal 2)**

- Process-memory reset — the singletons the conftest predated (mirror of `reset_capture`).
  [`conftest.py:217`](../../backend/tests/conftest.py#L217)

- DB after-wipe — the data-leak sibling; best-effort, FK-safe, sync-test compatible.
  [`conftest.py:237`](../../backend/tests/conftest.py#L237)

- Flood-window test made deterministic — stub `sleep_paced`, assert it slept the window before claiming (no wall-clock race).
  [`test_send_hardening.py:613`](../../backend/tests/test_send_hardening.py#L613)

- The durable-retry regression — retry once, wipe process memory (restart), second elapse pauses not resends.
  [`test_amazon_rotation.py:1108`](../../backend/tests/test_amazon_rotation.py#L1108)
