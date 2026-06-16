---
title: 'Awaiting-reply counter in the cockpit'
type: 'feature'
created: '2026-06-15'
status: 'done'
baseline_commit: '7735531279cacfedb8f32cbf67065c3f73900a3c'
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-pending-lines-drain.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The cockpit shows what is queued-to-send (`Pendientes`) and what came back (Completa/Filtrada), but nothing tells the client how many already-sent lines are still *waiting for the bot's ✅/❌*. After a lote goes out the operator can't see how many replies are still outstanding.

**Approach:** Add a single authoritative, session-scoped counter `awaiting_reply` = (lines sent with a `message_id`) − (distinct `message_id`s that already have a `kind='full'` reply), clamped at ≥0. Ship it on the same WS events that already move the response badges (`snapshot`, `session.active`, `batch.progress`, `response.captured`) and render it as a small read-only badge in the cockpit. This is NOT the existing `Pendientes` list (those are pending-to-send, not pending-reply).

## Boundaries & Constraints

**Always:** Backend is the source of truth — the frontend ASSIGNS the backend's authoritative number, never computes deltas (same contract as `ccNew = cc_total`). Counter is SESSION-scoped, surviving across batches like `responsesTotal` (legacy "counters never reset"). It resets to 0 only on a session change (the `sessionChanged` path in `batch.state`) and `clearSession`; it survives the idle reset and `seedFromBatch`. Spanish, sober copy; match surrounding idiom. Read-only counts run over indexed columns (`ix_send_log_message_id`, `ix_responses_message_id`).

**Ask First:** Changing the metric's scope from session to batch. Surfacing it anywhere beyond the cockpit (Historial, etc.).

**Never:** Do NOT add a 4th metric to the `ProgressRing` flank (UX-DR21 locks it to EXACTLY three). Do NOT persist the count as a column or maintain it via client-side increment/decrement. Do NOT touch capture/attribution semantics or the ⏳ no-row rule. A line the bot never answers stays counted (honest "still waiting") — that is intended, not a bug.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Line delivered | `send_log.message_id` filled, `batch.progress` fires | `awaiting_reply` +1 (recomputed authoritative) | N/A |
| First ✅/❌ reply | `response.captured`, `previous_status` null | `awaiting_reply` −1 | N/A |
| ⏳-only reply | no row persisted, no `response.captured` emit | counter unchanged | N/A |
| Edit of an answered msg | revision of an already-counted `message_id` | counter unchanged (DISTINCT message_id) | N/A |
| Reconnect mid-session | fresh `snapshot` | counter rebuilt authoritative from snapshot | N/A |
| New/changed session | gate-change or Nueva → `sessionChanged` | counter resets to 0 for the new session | N/A |
| No active session | idle, nothing sent | counter 0, badge hidden | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/db/repos/send_log.py` -- add `sent_count_for_session` (send_log ⨝ batches on `capture_session_id`, `message_id IS NOT NULL`).
- `backend/app/db/repos/responses.py` -- add `responded_message_count` (`COUNT(DISTINCT message_id)` where `kind=KIND_FULL` and `capture_session_id`); mirror `full_count` idiom.
- `backend/app/services/batches.py` -- add `awaiting_reply_count(session, capture_session_id)` = `max(0, sent − responded)`; inject `awaiting_reply` into `active_session_data` (0 when no active session) and `progress_data` (use `batch.capture_session_id`; 0 if None).
- `backend/app/core/capture.py` -- add `awaiting_reply` to the `response.captured` payload (recompute after commit via `capture_session_id`).
- `frontend/lib/ws.ts` -- add `awaitingReply` to `LiveBatchState`/`IDLE`; extend `SnapshotData`/`ProgressData`/`ResponseCapturedData`/`SessionActiveData`; assign in `snapshot`/`batch.progress`/`response.captured`/`session.active`; preserve on idle & `seedFromBatch`, reset on `sessionChanged` & `clearSession`.
- `frontend/components/batch/awaiting-reply.tsx` -- NEW small badge ("N esperando respuesta"), shown when `sessionId !== null`; align with `Metric`/`LabelCaps` tokens.
- `frontend/app/(client)/page.tsx` -- render `<AwaitingReply live={live} />` in the master column (after `<PendingLines/>`).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/repos/send_log.py` -- add `sent_count_for_session(session, capture_session_id) -> int`.
- [x] `backend/app/db/repos/responses.py` -- add `responded_message_count(session, capture_session_id) -> int`.
- [x] `backend/app/services/batches.py` -- add `awaiting_reply_count` helper; add `awaiting_reply` to `active_session_data` + `progress_data`.
- [x] `backend/app/core/capture.py` -- add `awaiting_reply` to the `response.captured` emit (post-commit recompute).
- [x] `frontend/lib/ws.ts` -- thread `awaitingReply` through state, payload types, and all four reducer cases + reset/seed paths.
- [x] `frontend/components/batch/awaiting-reply.tsx` + `frontend/app/(client)/page.tsx` -- new badge + wire-up.
- [x] `backend/tests/` -- unit-test the I/O matrix rows for `awaiting_reply_count` (sent+1, first-reply−1, ⏳ no-op, edit no-op, clamp ≥0).

**Acceptance Criteria:**
- Given an active session with 10 lines sent and 6 replied, when the cockpit renders, then it shows "4 esperando respuesta".
- Given a sent line awaiting reply, when its first ✅/❌ lands, then the counter drops by one within the same `response.captured` frame (no reload).
- Given a tab reconnects mid-session, when the `snapshot` arrives, then the counter matches the server's authoritative value (no drift).
- Given the client starts a new session (gate change or Nueva), when the new session binds, then the counter resets to 0.
- Given no active session, when the cockpit is idle, then no badge is shown.

## Design Notes

`previous_status is None` already marks a message's first full revision, but the reducer needs no delta logic: every event carries the authoritative `awaiting_reply`, so the frontend just assigns it (mirrors `ccNew: d.cc_total`). `progress_data` fires after every `_record_sent`/`_record_failed`, so the +1 on send is free; `response.captured` carries the −1. `responses.message_id` equals our sent `send_log.message_id` (attribution key), so `sent − distinct_responded` over the session is exact.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest` -- expected: new `awaiting_reply_count` tests pass.
- `cd frontend && npm run build` -- expected: tsc clean (build gate — lint alone misses type errors).

**Manual checks:**
- Send a lote; confirm the badge climbs as lines go out and falls as ✅/❌ replies land; reload mid-run and confirm the number is preserved.

## Suggested Review Order

**The metric (start here)**

- The single definition of the counter — `max(0, delivered − answered)`, session-scoped.
  [`batches.py:69`](../../backend/app/services/batches.py#L69)

- Numerator: delivered lines across every batch of the session (send_log ⨝ batches).
  [`send_log.py:100`](../../backend/app/db/repos/send_log.py#L100)

- Denominator: answered lines via `COUNT(DISTINCT message_id)` — the revision-collapse rationale.
  [`responses.py:176`](../../backend/app/db/repos/responses.py#L176)

**Emit wiring (where it climbs / drops)**

- Snapshot + `session.active` carry the authoritative value (reconnect-safe).
  [`batches.py:212`](../../backend/app/services/batches.py#L212)

- `batch.progress` (fires per send) → the +1-on-send vehicle.
  [`batches.py:154`](../../backend/app/services/batches.py#L154)

- `response.captured` recompute after flush, before commit → the −1-on-reply.
  [`capture.py:340`](../../backend/app/core/capture.py#L340)

**Frontend (assign, never sum)**

- The state field — the "assigned, not delta-summed" contract.
  [`ws.ts:113`](../../frontend/lib/ws.ts#L113)

- The one load-bearing reset: `0` on session change, preserved otherwise.
  [`ws.ts:510`](../../frontend/lib/ws.ts#L510)

- The badge — hidden when no active session.
  [`awaiting-reply.tsx:18`](../../frontend/components/batch/awaiting-reply.tsx#L18)

- Cockpit wire-up (after Pendientes).
  [`page.tsx:140`](../../frontend/app/(client)/page.tsx#L140)

**Tests (peripheral)**

- The I/O matrix in one file (climb, drop, ⏳ no-op, edit no-op, session-scope, clamp).
  [`test_awaiting_reply.py:1`](../../backend/tests/test_awaiting_reply.py#L1)
