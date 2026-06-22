---
title: 'Make the Aprobadas count server-authoritative (kill live drift vs Datos CC)'
type: 'bugfix'
created: '2026-06-22'
status: 'done'
route: 'one-shot'
---

# Make the Aprobadas count server-authoritative (kill live drift vs Datos CC)

## Intent

**Problem:** In the cockpit, the **Aprobadas** count (messages whose latest ✅/❌
revision is ✅) and the **Datos CC** count diverge *during* sending and reconcile
only after a page refresh. The two are NOT designed to be equal (Datos CC dedups
CC values and counts only ✅ replies carrying a `CC:` line — a ✅ with no CC, two
✅ sharing a CC, or one ✅ with several CCs already break equality), but the
*observed, self-correcting* drift is a real bug: **Datos CC** (`cc_total`/`ccNew`)
is server-authoritative — reassigned from the backend on every `response.captured`
event — while **Aprobadas** (`responsesOkTotal`) was **delta-summed in the
browser**, so a lost WS frame or a row evicted past the 500-row live cap left it
wrong until the next snapshot (hence "fixed on refresh").

**Approach:** Make Aprobadas authoritative too, mirroring the proven `cc_total`
pattern. The backend already computes `responses_ok_total` for the snapshot
(`full_count(status=ok)`, honoring the Limpiar cutoff); compute the same value
per captured reply (post-`add_full`-flush, same block as `cc_total`/`awaiting_reply`)
and add it to the `response.captured` emit. The reducer **assigns** it
(`d.responses_ok_total`) instead of summing `okDelta`, with a `?? store` fallback
for the brief deploy rollover. No schema/migration; no capture/attribution logic
change. Out of scope (same latent drift class, not requested): the Completa total
`responsesTotal`, still delta-summed.

## Suggested Review Order

**Backend — stamp the authoritative total on the emit (source of truth)**

- The new authoritative count, same query + Limpiar cutoff the snapshot uses, computed post-flush like `cc_total`.
  [`capture.py:574`](../../backend/app/core/capture.py#L574)
- Added to the `response.captured` emit dict (the one frame the reducer assigns from).
  [`capture.py:692`](../../backend/app/core/capture.py#L692)
- REFERENCE — the snapshot/`session.active` already emit the identical `full_count(status=ok)` value into the same field; emit and snapshot now reconcile instead of fighting.
  [`batches.py:244`](../../backend/app/services/batches.py#L244)

**Frontend — assign instead of sum**

- THE fix: assign `d.responses_ok_total` (drift-proof), `?? store` tolerates deploy rollover; the `okDelta` delta-sum is deleted.
  [`ws.ts:661`](../../frontend/lib/ws.ts#L661)
- WS contract mirror: the new field on the emit payload type.
  [`ws.ts:264`](../../frontend/lib/ws.ts#L264)

**Test**

- Asserts the emit carries `responses_ok_total == 1` after one ✅.
  [`test_awaiting_reply.py:199`](../../backend/tests/test_awaiting_reply.py#L199)
