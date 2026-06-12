---
title: 'Pending-lines list that drains one-by-one and survives reload'
type: 'feature'
created: '2026-06-12'
status: 'done'
context: []
baseline_commit: '414645207044e48b112864edd3fd0dcb44542130'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** When a client presses Enviar, the cockpit clears the whole textarea at once (`send-form.tsx` `setText("")`), so the operator loses sight of which pasted lines are still waiting. He wants the lines to drain one-by-one as each is actually sent. (Secondary, already true by design: sending must keep running with the page closed — the send worker lives server-side, so this only needs verification, no code.)

**Approach:** Add a read-only "Pendientes" list in the cockpit, fed from the backend (source of truth). Backend exposes the queued/sending line texts in the `snapshot` and emits a new `batch.lines_queued` event when lines are added (create or append). The list drains using the existing per-line `batch.line_sent` / `batch.line_failed` events (both already carry `position`). On reload the `snapshot` rebuilds the list, so it survives closing the page.

## Boundaries & Constraints

**Always:** Pending lines keyed by `position` (unique within the one live batch; guard frame application by `batchId` like `failedLines` does). Backend is the source of truth — the submitting tab does NOT optimistically seed pending from its own textarea. Cap the snapshot list at the existing `_SNAPSHOT_ROWS` (200) and the live list at `_LIVE_ROWS` (500); the count badge uses the authoritative `queued` count, never the (capped) list length. Match surrounding code idiom and Spanish copy.

**Ask First:** Changing the textarea behaviour (it still clears to empty on submit per the agreed design) — if any task tempts you to make the textarea itself shrink, stop.

**Never:** No new WS commands (WS stays server→client only). No change to the send pacing / worker claim order / rate-limiting. Do not touch `parse_mode`, attribution, or response/CC logic. Do not persist a new table — `batch_lines.text` already holds the text.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Send a batch | Paste N lines, Enviar | Textarea clears; Pendientes shows the N lines (top = next to send); badge = queued count | N/A |
| Line sent | `batch.line_sent {position}` | That line removed from Pendientes (top-down order) | Unknown position → no-op |
| Line failed | `batch.line_failed {position}` | Removed from Pendientes; appears in Fallidas | N/A |
| Append while live | Enviar again during live lote | New lines appended to Pendientes | N/A |
| Reload mid-batch | Close/reopen page | `snapshot.pending_lines` rebuilds the list | N/A |
| Batch drains fully | last line sent | Pendientes empty → panel hides | N/A |
| Huge paste (>200) | Paste 5000 lines | List capped (200 snap / 500 live); badge shows true queued count | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/db/repos/batches.py` -- add `queued_lines()` (mirror of `failed_lines`, l.216); make `add_lines` (l.159) return the inserted `list[BatchLine]` (positions assigned) for the emit.
- `backend/app/services/batches.py` -- `snapshot()` (l.178) adds `pending_lines`; new `lines_queued_data()` helper builds the event payload. `_SNAPSHOT_ROWS` cap already defined (l.22).
- `backend/app/api/batches.py` -- `create_or_append_batch` (l.84): emit `batch.lines_queued` after `add_lines` on create (l.152) and append (l.230), next to the existing `batch.progress` emits.
- `frontend/lib/ws.ts` -- `LiveBatchState`/`IDLE` gain `pending`; reducer: `snapshot`, new `batch.lines_queued`, extend `batch.line_sent` + `batch.line_failed` to remove by position; `seedFromBatch` starts pending clean.
- `frontend/components/batch/pending-lines.tsx` -- NEW, mirror `failed-lines.tsx`.
- `frontend/app/(client)/page.tsx` -- mount `<PendingLines live={live} />` in the cockpit column near `<FailedLines />`.
- `frontend/components/batch/failed-lines.tsx` -- read-only reference for structure/copy.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/repos/batches.py` -- add `queued_lines(session, batch_id, limit)` returning `BatchLine`s in `(LINE_QUEUED, LINE_SENDING)` ordered by `position`, limited. (`add_lines` already returns the created lines — no change needed.)
- [x] `backend/app/services/batches.py` -- `snapshot()`: add `"pending_lines": [{"position","text"}]` from `queued_lines` (capped `_SNAPSHOT_ROWS`) in the live branch, `[]` in the idle branch; add `lines_queued_data(batch_id, lines)` building `{"batch_id","lines":[{"position","text"}]}` (capped) -- snapshot-first parity with `failed_lines`.
- [x] `backend/app/api/batches.py` -- after each `add_lines`, `broadcaster.emit(tenant_id, "batch.lines_queued", lines_queued_data(...))` -- live list grows on create and append.
- [x] `frontend/lib/ws.ts` -- add `pending: PendingLine[]` ({position,text}) to state/IDLE; `snapshot` sets it from `pending_lines`; new `batch.lines_queued` case appends (batchId-guard + dedup by position, cap `_LIVE_ROWS`); `batch.line_sent` and `batch.line_failed` filter out the matching position; `seedFromBatch` + `batch.state` (new batch id) set `pending: []` -- the new batch's lines arrive via the event the POST triggers.
- [x] `frontend/components/batch/pending-lines.tsx` -- NEW: render "n líneas pendientes" (n = `live.queued`) and the line texts; render nothing when empty; mono font, mirror `failed-lines.tsx`.
- [x] `frontend/app/(client)/page.tsx` -- mount `<PendingLines live={live} />` in the cockpit column.
- [x] `backend/tests/test_batches.py` -- update idle/live snapshot-shape tests for `pending_lines`; add `queued_lines` order+cap repo test.

**Acceptance Criteria:**
- Given a live lote, when a line's `batch.line_sent` arrives, then exactly that line leaves Pendientes and the textarea is unaffected.
- Given a mid-batch reload, when the new tab gets its `snapshot`, then Pendientes shows the still-queued lines (no optimistic/textarea seed involved).
- Given the operator closes the page during a lote, when he reopens it, then the lote is still progressing (worker is server-side) and Pendientes reflects the current queue — verifying the secondary goal.
- Given a paste larger than the cap, when it sends, then the badge count stays truthful (from `queued`) even though the list is trimmed.

## Design Notes

`batch.line_sent` already fires per sent line with `{batch_id, position, text}` (send_worker `_record_sent`), and `batch.line_failed` with `{position,...}` — so draining needs NO worker change, only two extra filters in the existing ws.ts cases. `position` is unique within the single live batch (partial unique index ≤1 live batch/tenant), so it is a safe React key and removal key; still guard each frame with the existing `store.batchId !== d.batch_id` check (precedent: `batch.line_failed`, ws.ts:367). Pending is BATCH-scoped (unlike the session-scoped Completa/Filtrada rows): clear it whenever `batchId` changes / on the idle drain.

## Verification

**Commands:**
- `cd frontend && npm run lint` -- expected: clean.
- `cd backend && .venv/bin/pytest` -- expected: existing suite green (add a repo test for `queued_lines` ordering/limit if a nearby test module exists).

**Manual checks:**
- Paste 5 lines, Enviar: textarea empties, Pendientes shows 5, lines vanish top-down as the ring advances; the last send empties the panel.
- Mid-batch, reload the tab: lote still running, Pendientes rebuilt from snapshot.

## Suggested Review Order

**Backend — expose the pending queue (source of truth)**

- Entry point: still-queued line texts join the snapshot so a reload rebuilds the list.
  [`batches.py:257`](../../backend/app/services/batches.py#L257)

- The `batch.lines_queued` payload (capped, position+text) — the live-grow event.
  [`batches.py:91`](../../backend/app/services/batches.py#L91)

- The repo query feeding both: queued/sending lines, position order, capped.
  [`batches.py:226`](../../backend/app/db/repos/batches.py#L226)

- Emit `batch.lines_queued` on create and append, next to the existing progress emit.
  [`batches.py:205`](../../backend/app/api/batches.py#L205)

**Frontend — the draining store + panel**

- The reducer's new case: grow pending, dedup by position, keep lowest (next-to-send).
  [`ws.ts:654`](../../frontend/lib/ws.ts#L654)

- Drain hooks: `batch.line_sent` / `batch.line_failed` remove the sent/failed position.
  [`ws.ts:414`](../../frontend/lib/ws.ts#L414)

- Snapshot rebuilds pending on reconnect (survives page close).
  [`ws.ts:335`](../../frontend/lib/ws.ts#L335)

- The panel — driven by `queued` (never vanishes while work pending; `+N más` overflow).
  [`pending-lines.tsx:11`](../../frontend/components/batch/pending-lines.tsx#L11)

- Mounted in the cockpit column under Fallidas.
  [`page.tsx:90`](../../frontend/app/(client)/page.tsx#L90)

**Tests**

- Snapshot shape + `queued_lines` order/cap coverage.
  [`test_batches.py:467`](../../backend/tests/test_batches.py#L467)
