---
title: 'Reply reconciler — recover bot replies dropped by the Telegram update stream'
type: 'bugfix'
created: '2026-06-15'
status: 'done'
baseline_commit: 'd2031c8c9bd8246852ce8d48b8b78d40016b84c7'
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/backend/app/core/capture.py'
  - '{project-root}/backend/app/core/send_worker.py'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Bot replies are captured ONLY from live Telethon push events (`NewMessage`/`MessageEdited`). When the update stream drops events — `catch_up`/`differenceTooLong` gaps, a dropped ⏳→✅ edit, a brief disconnect — those replies are lost forever (the code itself notes "a dropped catch_up replay would be lost forever"). Confirmed incident: 300 lines sent and delivered, replies present in Telegram (each a reply-quote), but only 92 reached Completa. Silent, unbounded loss on the product's core promise.

**Approach:** Add a background **reply reconciler** that periodically re-reads the target chat history and re-feeds any reply belonging to one of our still-unanswered sends through the EXISTING `capture.process_incoming` path (already idempotent: text-equality dedup + DB-enforced CC uniqueness). It is the reply-side mirror of the send worker's `_boot_recovery`, which already reconciles *sent* lines via `gateway.recent_outgoing()`. Self-healing and retroactive: once deployed, the next pass recovers the existing 300-line batch.

## Boundaries & Constraints

**Always:**
- Telethon stays confined to `core/telegram.py`; reuse `capture.process_incoming` unchanged (dedup, attribution, status state-machine, CC uniqueness, `response.captured` emission stay the single source of truth).
- Targeted scan only: act ONLY on inbound messages whose `reply_to_msg_id` is in the set of OUR delivered-but-unanswered `send_log.message_id`s — so attribution always succeeds and the unmatched bucket is never inflated.
- Reconciled (old) replies MUST NOT fake liveness: no `watchdog.note_reply()`, no `alerts` unmatched window for reconciler-fed items (mirror of `_boot_recovery` not calling `watchdog.note_sent()`).
- Account safety: skip the Telegram scan when `watchdog.is_paused`, gateway not `ready`, or `scheduler.flood_remaining() > 0`; bound the scan; swallow read/`SessionLostError` — never crash the task, never latch the watchdog from a read.
- Cheap when idle: one indexed DB query per pass; if nothing awaits, sleep with no Telegram call. Interval/window/scan-cap are MODULE CONSTANTS, not new settings (2.5 rule).

**Ask First:**
- If a recency window on the awaiting-set would EXCLUDE the 300-line incident batch from recovery, surface it before narrowing.

**Never:**
- Don't change attribution, the status state-machine, CC dedup, or `send_log` write-ahead/fail-stop.
- Don't persist replies without attribution. Don't add a manual resync UI/endpoint here (defer). Don't run a second Telethon client/cc-core. Don't read `respuestas/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Behavior | Error Handling |
|----------|--------------|-------------------|----------------|
| Recovered | Send `M` delivered, line unanswered; Telegram has bot ✅ with `reply_to_msg_id=M` | Feed → attribute + persist ✅ revision + emit `response.captured` | N/A |
| Already captured | Same, but captured live already | Re-feed is a no-op (text dedup + CC unique index) — no dup row, no dup emit | N/A |
| Still ⏳ | Awaiting send, only reply so far is ⏳ | No row (legacy parity); stays awaiting, re-scanned next pass | N/A |
| Nothing awaiting | Every delivered send has a response | Return after one DB query; ZERO Telegram calls | N/A |
| Paused/flood/down | `watchdog.is_paused` / gateway not ready / open FloodWait | Skip scan this pass; sleep; retry next interval | log + skip |
| Read error | `recent_incoming` raises | Catch, log `event=reconcile_skipped`; task survives | swallow, no latch |
| Older than window | Oldest awaiting predates the scan window | Recover what's in-window; `log` how many left beyond it | log warning (no silent cap) |

</frozen-after-approval>

## Code Map

- `backend/app/core/send_worker.py` -- reference: `_boot_recovery` is the pattern to mirror (history reconcile + `run_worker` catch-log-continue loop).
- `backend/app/core/telegram.py` -- ADD `recent_incoming`; existing `recent_outgoing` is the template (auth-loss → `SessionLostError`).
- `backend/app/core/capture.py` -- ADD `reconcile_enqueue`; `process_incoming` unchanged.
- `backend/app/db/repos/send_log.py` -- ADD the awaiting-ids query.
- `backend/app/core/reconciler.py` -- NEW background task + constants + testable `reconcile_once()`.
- `backend/app/main.py` -- wire the task in the lifespan.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/repos/send_log.py` -- add `awaiting_sent_message_ids(session, *, within: datetime) -> set[int]`: `send_log JOIN batches ON batches.id = send_log.batch_id WHERE message_id IS NOT NULL AND batches.created_at >= within AND NOT EXISTS (SELECT 1 FROM responses r WHERE r.line_id = send_log.line_id AND r.kind = 'full')`.
- [x] `backend/app/core/telegram.py` -- add `recent_incoming(self, floor_id: int, limit: int) -> list[tuple[int, int | None, str]]`: per resolved target, `iter_messages` newest-first, keep `not message.out`, stop once `message.id < floor_id` or `limit` reached per target; dedup by id; auth-loss → `SessionLostError`. Return `(id, reply_to_msg_id, raw_text)`.
- [x] `backend/app/core/capture.py` -- add `reconcile_enqueue(reply: IncomingReply)` that ONLY `_queue.put_nowait(reply)` (no watchdog); one-line docstring contrasting with `enqueue`.
- [x] `backend/app/core/reconciler.py` -- NEW. Constants `_RECONCILE_INTERVAL_SECONDS` (~45), `_RECONCILE_WINDOW_HOURS` (≥48 so the incident is in-window), `_MAX_SCAN_PER_TARGET`. `reconcile_once()`: query awaiting ids; empty → return; skip-with-log if `watchdog.is_paused`/`not gateway.ready`/`scheduler.flood_remaining() > 0`; else `gateway.recent_incoming(min(awaiting), _MAX_SCAN_PER_TARGET)` and for each inbound with `reply_to_msg_id in awaiting`, `capture.reconcile_enqueue(IncomingReply(...))`. `run_reconciler()`: sleep one interval first (let boot recovery finish), then loop guarded like `run_worker` (catch-log-continue, re-raise `CancelledError`). Log `event=reconcile_pass awaiting=N fed=K`.
- [x] `backend/app/main.py` -- create/cancel/await `reconciler_task` in the lifespan alongside `capture_task`.
- [x] `backend/tests/` -- `tests/test_reconciler.py` (+ `FakeGateway.recent_incoming` in conftest): recovery persists + emits; targeted scan ignores foreign replies; re-feed is a no-op; nothing-awaiting makes zero Telegram calls; gateway-not-ready / watchdog-paused / FloodWait skip; read error swallowed; `reconcile_enqueue` does NOT call `watchdog.note_reply`.

**Acceptance Criteria:**
- Given a delivered send whose bot ✅ reply was never captured live, when a pass runs, then the ✅ revision persists and a `response.captured` event reaches the owning tenant — identical to a live capture.
- Given a reply already captured live, when re-fed, then no duplicate row and no re-emit.
- Given no delivered-unanswered sends, when a pass runs, then zero Telegram calls.
- Given paused/flood/gateway-down, when a pass runs, then the scan is skipped and the task keeps running.
- Given reconciled replies, when processed, then `watchdog.note_reply()` and the `alerts` unmatched window are NOT invoked for them.

## Design Notes

Floor bound is exact and cheap: a bot reply's `message_id` is always greater than the send it answers (sent later on the account-global sequence), so scanning inbound history down to `min(awaiting_ids)` covers every recoverable reply with no arbitrary depth.

Idempotency is already guaranteed by `process_incoming` (`if previous.text == clean_text: return`, plus `uq_responses_session_cc`) — the reconciler adds NO new dedup, only re-injects dropped events through the same single consumer that serializes live and reconciled replies (no locks, no races).

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest` -- expected: new reconciler tests pass; existing capture/worker tests unchanged.
- `cd frontend && npm run build` -- expected: unaffected, but run the build gate before any push to main.

**Manual checks:**
- After deploy, grep `cc-core` logs for `event=reconcile_pass`; for the 300-line session confirm Completa climbs toward ~300 within a few passes, with no duplicate rows (compare `responses` full-row count before/after).

## Suggested Review Order

**The reconciliation pass (design intent)**

- Entry point — one pass: query awaiting → safety-gate → scan history → re-feed matches.
  [`reconciler.py:56`](../../backend/app/core/reconciler.py#L56)
- Targeted match: only replies addressed to an awaiting send are fed (attribution always succeeds, unmatched bucket never inflated).
  [`reconciler.py:107`](../../backend/app/core/reconciler.py#L107)
- 🔒 Account safety: a history-read FloodWait opens the SAME global no-send window the worker honors.
  [`reconciler.py:91`](../../backend/app/core/reconciler.py#L91)
- The infinite loop: sleep-first (let boot recovery finish), catch-log-continue like `run_worker`.
  [`reconciler.py:126`](../../backend/app/core/reconciler.py#L126)

**Telethon boundary (the only new MTProto call)**

- `recent_incoming` mirrors `recent_outgoing`: inbound-only, dedup by id, stop below `floor_id`, auth-loss → `SessionLostError`.
  [`telegram.py:386`](../../backend/app/core/telegram.py#L386)

**The work-list (DB)**

- Delivered-but-unanswered sends within the window — the reconciler's targets.
  [`send_log.py:102`](../../backend/app/db/repos/send_log.py#L102)
- Beyond-window count — surfaced in the pass log so an aging tail of lost replies is never silently capped.
  [`send_log.py:143`](../../backend/app/db/repos/send_log.py#L143)

**Capture integration (idempotent, no fake liveness)**

- `reconcile_enqueue`: same single consumer as live, but skips `watchdog.note_reply()` (historical replies must not signal "bot alive").
  [`capture.py:130`](../../backend/app/core/capture.py#L130)

**Wiring & tests (peripheral)**

- Lifespan: create/cancel/await the reconciler task beside the capture consumer.
  [`main.py:73`](../../backend/app/main.py#L73)
- Coverage: recovery, idempotent re-feed, zero-call-when-idle, safety skips, FloodWait→scheduler, beyond-window log.
  [`test_reconciler.py:1`](../../backend/tests/test_reconciler.py#L1)
- `FakeGateway.recent_incoming` stand-in (history fixture).
  [`conftest.py:79`](../../backend/tests/conftest.py#L79)
