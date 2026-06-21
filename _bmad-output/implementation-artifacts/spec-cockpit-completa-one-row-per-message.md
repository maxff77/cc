---
status: done
slug: cockpit-completa-one-row-per-message
---

# Cockpit Completa: one row per message (fix refresh duplication)

## Problem

Cockpit live panels (Completa/Aprobadas/Datos CC) showed far MORE rows after a
page refresh than during the live session — e.g. 10 captured replies became 39
on reload. The earlier fix `20471f2` deduped the live re-seed race by
`Response.id`, but **deliberately kept every revision** ("Completa keeps every
revision"). So a reply the checker bot edits several times (⏳→✅, then ✅ card
edits) persists one `kind='full'` row per revision, and the snapshot read
(`list_full`) returned ALL of them, while the live append path showed fewer in
practice. Historial already collapsed to the latest revision per message
(`history_grouped`, `DISTINCT ON (chat_id, message_id)`); the cockpit did not —
that inconsistency was the bug.

Root cause: NOT duplicate writes. `capture.py` no-op guard (text+status) blocks
identical rows, and the reconciler only re-feeds sends with NO `full` row
(`awaiting_sent_keys` → `~answered`), so it never re-inserts captured replies.
The 39 rows are genuine distinct revisions; the cockpit just listed every one.

## Decision (owner, Richard)

Completa shows **one row per message = the LATEST revision** per
`(chat_id, message_id)`, matching Historial. Revisions stay in the DB (storage
unchanged); only the VIEW collapses. This overrides the documented "Completa
keeps every revision" invariant by explicit owner choice.

## Changes

### Backend — `backend/app/db/repos/responses.py`
- New `_latest_full_ids(...)` — `DISTINCT ON ((chat_id, message_id)) ORDER BY …,
  id DESC` subquery (cutoff + `hidden_at` filter ELIGIBLE revisions before the
  pick; `status` applied by callers on the outer row → "latest is ✅").
- `list_full` → returns the latest revision per message (was every revision via
  `_list_last`). Preserves `status`, `limit`/reverse cap, `cleared_response_id`,
  `include_hidden`. `_list_last` stays for `list_cc` (CC unaffected).
- `full_count` → counts distinct MESSAGES (latest set); `status=ok` counts
  messages whose LATEST revision is ✅. Badge now matches the collapsed list.
- Display/export-only: no integrity path (`responded_line_count`, reconciler
  `_answered_full_exists`, CC dedup, credits) touches these two functions.

### Frontend — `frontend/lib/ws.ts`
- `response.captured` reducer: **upsert by `messageId`** instead of append. A
  later revision of an on-screen message replaces its row in place; a new
  message appends. Exact re-delivery (same `responseId`) is a no-op (subsumes the
  old re-seed-race dedup). `responsesTotal` +1 only for a new message;
  `responsesOkTotal` adjusts by an ok-delta (✅→❌ subtracts, ❌→✅ adds).

### Tests
- `test_attribution.py::test_snapshot_collapses_revisions_to_one_row_per_message`
  (new): 2 revisions of one message → 1 snapshot row (latest) + `responses_total
  == 1`; both rows still in storage.
- `test_sessions.py::test_export_completa_is_one_block_per_message_latest_revision`
  (renamed/updated from `…carries_every_revision…`): completa export = one block
  per message (latest); storage parity asserted.

## Verification
- `pytest`: 512 passed, 1 failed — the 1 failure (`test_history.py` "Sin gate"
  vs "Sin gateway") is PRE-EXISTING from `b91c2dc` (Gate→Gateway rename),
  unrelated to this change.
- `npm run build`: TypeScript clean.

## Downstream consistency (intended)
`completa` export + admin per-session export + admin support view now collapse to
latest-per-message too (all go through `list_full`). Consistent with the cockpit.
