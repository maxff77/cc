---
title: 'Fix duplicate response rows in the cockpit live panels'
type: 'bugfix'
created: '2026-06-20'
status: 'done'
context: []
baseline_commit: '91c4132'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Since the sessionless-cockpit refactor (commit `1c403a2`), the cockpit live panels (Completa / Aprobadas / Datos CC) render the same captured response twice. The `response.captured` reducer in `frontend/lib/ws.ts` dedups an incoming live row ONLY against the last row in the list. A row already present in a server-pushed slice — the `snapshot` after every reconnect, or the `session.active` re-emit on Limpiar / gate-refresh — that an in-flight `response.captured` repeats *non-consecutively* slips past that check and is appended again as a second `l-N` twin of the existing `s-${id}` row. The perpetual session (the list never resets between batches now) makes these twins accumulate and stay on screen. The "datos de otro user" the client screenshotted are these duplicate rows, NOT cross-tenant data.

**Approach:** Add the persisted `Response.id` (the stable per-revision identity the backend already exposes as the snapshot row id / `s-${id}` key) to the `response.captured` WS emit, then dedup each incoming event by that id — skip it iff a row with the same `responseId` is already in the list. A re-delivery from the snapshot / `session.active` re-seed race carries the SAME id and is dropped; a genuine new revision (including a re-flip to a prior exact state) carries a NEW id and is appended. The only backend change is adding the already-persisted id to that one emit; no schema/migration.

## Boundaries & Constraints

**Always:** Dedup `response.captured` by the persisted `Response.id` (`d.id`) — the exact physical revision identity. Do NOT dedup by `(messageId, status, text)`: Completa keeps every revision, so that triple legitimately recurs (a `❌→✅→❌` re-flip to a prior exact state; a cookie-mode CC re-emit collapsing to the same display text). A triple match either wrongly drops a real revision (whole-list) or re-admits a raced duplicate of a non-current revision (latest-only). The id is unique per persisted revision and present on BOTH snapshot rows (`s-${id}`) and the live emit, so it distinguishes re-delivery from new revision with zero ambiguity. Keep `responsesTotal` / `responsesOkTotal` guarded by the same `isDupRow`.

**Ask First:** Anything beyond the id-dedup + its single emit field — resetting the singleton store on auth change, backend tenant-consistency guards, unifying the `l-`/`s-` key namespaces. HALT and confirm before touching any of these.

**Never:** Do NOT change capture/attribution LOGIC, persistence, or tenant-scoping (proven correct; tenant_id from session; no Telegram re-auth occurred) — the ONLY backend edit is adding the already-persisted `Response.id` to the `response.captured` emit dict. No schema change, no migration. Do NOT add a frontend test framework (repo has none; build+lint is the gate). Do NOT remove the `_LIVE_ROWS` cap, the session guard, or the CC dedup. Do NOT unify the `l-`/`s-` React-key namespaces (keep `l-${seq}` for live rows).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Re-delivery (re-seed race) | snapshot/`session.active` seeded a row with `Response.id = R`; an in-flight `response.captured` with `id = R` arrives after | NOT re-appended (id R already present); `responsesTotal` unchanged | N/A |
| Brand-new revision | `response.captured` whose `id` is not in the list | appended once (`l-N`); totals +1 | N/A |
| Re-flip to a prior exact state | M history `✅"T"(id1), ❌"T"(id2)`; new `response.captured` `✅"T"` with `id3` | appended (id3 is new) — the legit re-flip is KEPT | N/A |
| Multi-revision raced | seed holds `❌X(id1), ✅Y(id2)`; in-flight re-deliveries of BOTH (id1 and id2) land after | both dropped (ids already present) — no phantom twins | N/A |
| Cookie-mode CC re-emit | two distinct revisions, identical display text, different ids | each appended once (distinct ids); the CC value added to Datos CC | N/A |
| Duplicate past the cap | the row with id R scrolled beyond the 500-row `_LIVE_ROWS` cap | a re-delivery of R may re-append once (off-screen) — accepted ceiling | N/A |

</frozen-after-approval>

## Code Map

- `frontend/lib/ws.ts` (`response.captured` case ~577-657; `ResponseCapturedData` ~244; `ResponseRow` ~42; `snapshot` map ~389; `session.active` map ~676) — add `id`/`responseId`, dedup by id. **THE fix.**
- `backend/app/core/capture.py` (~493 `add_full` call, ~567 pre-commit stash block, ~630 emit dict) — capture the returned row id, stash before commit, add `"id"` to the emit. ONLY backend edit.
- `backend/app/db/repos/responses.py` (`add_full` ~65) — REFERENCE: already returns the `Response` with `.id` populated by `flush`; no change.
- `frontend/app/admin/tenants/[id]/page.tsx` (~288) — also builds `ResponseRow[]`; the new required `responseId` field is set from `row.id` here too (read-only support view, build-gate fallout).
- `backend/tests/test_awaiting_reply.py` (~195) — asserts the captured emit carries `id`.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/core/capture.py` -- capture the `add_full` return value (`full_row = await responses_repo.add_full(...)`), stash `response_id = full_row.id` in the pre-commit "capture everything the emission needs" block (alongside `tenant_id` etc., BEFORE `session.commit()` so no expired-attribute lazy-load), and add `"id": response_id` to the `response.captured` emit data dict. No other capture logic changes.
- [x] `frontend/lib/ws.ts` -- add `id: number` to `ResponseCapturedData` and `responseId: number` to `ResponseRow`; set `responseId` from the row `id` in the `snapshot` and `session.active` row maps and from `d.id` on the live append; replace the dedup with `const isDupRow = store.responses.some((row) => row.responseId === d.id);`. Keep the `l-${seq}` keys, the CC dedup, and the `responsesTotal`/`responsesOkTotal` `isDupRow` guards.

**Acceptance Criteria:**
- Given a row with `Response.id = R` already in the list, when a `response.captured` with `id = R` arrives, then no row is appended and `responsesTotal` does not increment.
- Given a `response.captured` whose `id` is not present in the list, when it arrives, then exactly one row is appended — even if its `(messageId, status, text)` equals an existing row (a legit re-flip / re-emit).
- Given the re-seed race re-delivers BOTH an older and the current revision of a message (two distinct ids both already seeded), when they arrive, then neither is re-appended.
- Given the backend, when a `response.captured` is emitted, then its `data` carries the persisted `Response.id` as `id`.

## Spec Change Log

### 2026-06-20 — loopIteration 3 (review loopback)

- **Triggering finding (edge-case HIGH, 2nd review):** the latest-revision tail scan re-admits a duplicate in the re-seed race — because the snapshot/`session.active` slice carries EVERY revision (backend `_list_last`, last 200 by id, not one-per-message), an in-flight re-delivery of a NON-current revision finds the message's latest (different) revision, judges it new, and re-appends a phantom twin. Neither frontend-only key — whole-list nor latest-only — can be fully correct, because `response.captured` lacked a stable per-revision identity.
- **Amended (human approved crossing the no-backend boundary):** dedup now keys on the persisted `Response.id`, added to the `response.captured` emit (the backend already exposes it as the snapshot `s-${id}` key). Approach/Always/Never/Ask-First, the I/O Matrix, ACs, Code Map, and Tasks all re-cast to id-dedup; the Never boundary now permits exactly that one emit field (still no schema/migration, no capture-logic change).
- **Known-bad state avoided:** BOTH prior failure modes — whole-list dropping a legit re-flip (showing stale state), AND latest-only re-admitting a raced phantom duplicate.
- **KEEP:** the re-seed-race fix intent; `responsesTotal`/`responsesOkTotal` `isDupRow` guards; CC dedup; `l-${seq}` live keys (no namespace unification); session guard; `_LIVE_ROWS` cap; the "cross-tenant ruled out" Design Note.
- **Re-review (3 reviewers — blind CORRECT, edge-case SOLID, acceptance ALL PASS) → 1 patch applied:** guard the dedup with `d.id != null &&` so the brief deploy rollover (new frontend, old id-less backend) can't collide two `undefined` ids and drop a real reply. Other notes rejected as non-issues: O(n) `.some` over ≤500 is sub-ms; snapshot/`session.active` REPLACE (don't merge), so dedup living only on the live path is sufficient; `expire_on_commit=False` (`db/base.py`) makes the pre-commit id read safe. Pre-existing limitation logged to deferred-work: a dup slipping past the 500-row live cap nudges the total until the next snapshot.

### 2026-06-20 — loopIteration 2 (review loopback)

- **Triggering finding (blind hunter HIGH + edge-case #1 HIGH):** the whole-list `(messageId, status, text)` `.some()` dedup drops a LEGITIMATE non-consecutive revision that repeats an earlier display triple — a special-mode `❌→✅` re-flip (count stripped ⇒ byte-identical `clean_text`) or a cookie-mode CC re-emit (`display_transform` collapses distinct revisions to one display string while the backend no-op guard compares pre-transform `clean_text`).
- **Amended:** frozen Approach + Always re-scoped the dedup from the WHOLE list to the message's LATEST revision (tail scan to the first same-`messageId` row); I/O Matrix gained a "Re-flip to an earlier triple" row (KEEP) and a "Cookie-mode identical display" row (residual collapse, accepted); ACs rewritten to latest-revision semantics; Tasks rewritten to the tail scan.
- **Known-bad state avoided:** silently dropping a genuine `❌→✅` re-approval (or a re-emitted revision) from the live Completa/Aprobadas panels because an earlier same-triple revision exists somewhere up the list.
- **KEEP:** the re-seed-race fix itself (the original bug — `l-`/`s-` twins from snapshot/`session.active` re-delivery); the `(status, text)` comparison fields; the `responsesTotal`/`responsesOkTotal` `isDupRow` guards; the CC `Set` dedup; frontend-only / no-backend boundary; the "cross-tenant ruled out" Design Note.
- **Out of scope (residual):** cookie-mode revisions with byte-identical display text cannot be told apart by ANY frontend-only key (the emitted payload is lossy) — collapsing them is acceptable (visually identical row, CC preserved in Datos CC, count self-heals on snapshot). A true fix needs a backend discriminator (e.g. a stable row id on `response.captured`); deferred.

## Design Notes

- **Cross-tenant ruled out (do not chase it).** Backend reads are tenant-scoped (tenant_id from the session cookie; capture_session resolved per tenant via `get_active(tenant_id)`). No Telegram re-auth happened (the only documented mis-attribution trigger). Login/logout use `window.location.assign(...)` — a FULL page load that re-evaluates `ws.ts` and resets the singleton store + socket — so no foreign-tenant rows can survive an account switch in the same browser. The symptom is duplicates, not a leak.
- **ponytail: no new test framework.** The repo has zero frontend tests; `npm run build` (tsc) is the established gate. Verification is build/lint + the manual reconnect check below.
- **ponytail: dedup by `Response.id` reuses the backend's existing PK** (already the snapshot `s-${id}` key) — no new state, no schema, no triple gymnastics. `store.responses.some(r => r.responseId === d.id)` is O(n) over ≤500, sub-ms. Ceiling: a re-delivery of a row already evicted past the `_LIVE_ROWS` cap can re-append once (off-screen); the next snapshot reconciles. `id` is captured BEFORE `session.commit()` so accessing it never triggers an expired-attribute lazy-load.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest tests/test_awaiting_reply.py tests/test_attribution.py tests/test_reconciler.py tests/test_clear_declined.py -q` -- expected: pass (capture-emit path; one asserts the captured event carries `id`)
- `cd frontend && npm run lint` -- expected: clean
- `cd frontend && npm run build` -- expected: tsc + next build pass (the real gate)

**Manual checks:**
- Open the cockpit, run a batch, then force a WS reconnect (toggle network, or restart `cc-core`) while ✅/❌ replies are landing → no row appears twice in Completa / Aprobadas / Datos CC, and the Completa badge count matches the visible rows.

## Suggested Review Order

**Backend — stamp the revision id on the emit (the source of truth)**

- Entry point: the `response.captured` emit now carries the persisted `Response.id` — the whole dedup hinges on this.
  [`capture.py:641`](../../backend/app/core/capture.py#L641)
- The id read post-flush, pre-commit (no expired-attribute lazy-load).
  [`capture.py:575`](../../backend/app/core/capture.py#L575)
- Capture `add_full`'s already-returned row (was discarded before).
  [`capture.py:493`](../../backend/app/core/capture.py#L493)

**Frontend — dedup by that id**

- THE fix: drop only an exact re-delivery (same id), keep every genuine revision; `d.id != null` tolerates deploy rollover.
  [`ws.ts:610`](../../frontend/lib/ws.ts#L610)
- WS contract mirror: `id` on the emit payload type.
  [`ws.ts:251`](../../frontend/lib/ws.ts#L251)
- The dedup identity field on the row model.
  [`ws.ts:47`](../../frontend/lib/ws.ts#L47)
- Live append + snapshot + session.active rows all carry `responseId`.
  [`ws.ts:632`](../../frontend/lib/ws.ts#L632) · [`ws.ts:398`](../../frontend/lib/ws.ts#L398) · [`ws.ts:690`](../../frontend/lib/ws.ts#L690)

**Peripherals**

- Read-only support view also builds `ResponseRow[]` — sets `responseId` (build-gate fallout).
  [`page.tsx:290`](../../frontend/app/admin/tenants/[id]/page.tsx#L290)
- Asserts the emit carries `id`.
  [`test_awaiting_reply.py:198`](../../backend/tests/test_awaiting_reply.py#L198)
