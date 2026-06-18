---
title: 'Special-mode gate categories: strict Approveds validity + stats/Credits redaction'
type: 'feature'
created: '2026-06-18'
status: 'done'
baseline_commit: 'd6b5df7fb35e09d8893c7214d6931d0acab0fa71'
context: ['{project-root}/CLAUDE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Capture marks a reply `ok` when its text merely contains `✅`. Gates in the **Mass mode Specials** category emit an aggregate stats line — `↳ Approveds! ✅: 0 ヾ⌿ Deads! ❌: 1 ヾ⌿ Credits: 999996044 ヾ⌿ Time: 32.95s` — so a fully-dead reply still carries a `✅` glyph and is counted `ok`: a false positive that wrongly charges a credit and pollutes Completa/Filtrada/CC. That same line also leaks the owner's balance (`Credits: 999996044`) into client views.

**Approach:** Add an owner-toggleable `special_mode` flag on gate categories, snapshotted onto the capture session at batch start. In a special-mode session, derive status from the `Approveds! ✅: N` count (N≥1 → `ok`, else `rejected`) instead of glyph presence, and strip the `Approveds!`/`Deads!` segments from the stored/visible reply (keep `Time:`). Globally (every category), strip the `Credits: <n>` segment — at capture and on read.

## Boundaries & Constraints

**Always:**
- `tenant_id`/attribution, `parse_mode=None`, and Telethon-only-in-`core/telegram.py` stay untouched.
- `special_mode` is a SNAPSHOT on the capture session (idiom of `gate_credit_cost`): set at session creation from the gate's category; reused active session refreshes to the current gate's value at batch start.
- Credits stripping rides the existing two-place `redact_reply_text` pattern (capture + every read/export builder) → pre-existing rows scrubbed with NO data migration.
- Preserve legacy capture semantics: `✅`/`❌` revisions persist; the `⏳` "keep-previous / first-⏳ writes nothing" rule holds (special mode: a reply with no `Approveds!` line yet IS that intermediate state).
- New columns NOT NULL, `server_default false`; `alembic upgrade head` before restart.

**Ask First:**
- If a real captured sample differs from the example literals (`ヾ⌿` separator, `Approveds!`, `Deads!`, `Credits:`), confirm before hardcoding the regexes.
- Stripping anything beyond `Approveds!` / `Deads!` / `Credits:` (the user kept `Time:`).

**Never:**
- Don't snapshot the category IDENTITY onto batch/session — only the derived boolean (the model docstrings forbid category snapshots; the flag is a behavior snapshot, justified like `gate_credit_cost`).
- Don't move CC dedup into code, don't expose `special_mode` or `gate_value` to clients, don't add env/config vars, don't touch legacy `app.py`/`core.py`/`static/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Special, approved | special session, reply `… Approveds! ✅: 3 ヾ⌿ Deads! ❌: 0 ヾ⌿ Credits: 999 ヾ⌿ Time: 9s` | status `ok`; stored/emitted text has Approveds!/Deads!/Credits removed, `Time:` kept; CC extracted; credit charged | N/A |
| Special, all dead | reply `↳ Approveds! ✅: 0 ヾ⌿ Deads! ❌: 1 ヾ⌿ Credits: 999996044 ヾ⌿ Time: 32.95s` | status `rejected` (❌ row in Completa); NO CC; NO credit charge; Approveds!/Deads!/Credits stripped | N/A |
| Special, still processing | intermediate edit, no `Approveds!` line | keep previous status (legacy ⏳); first such edit persists nothing | N/A |
| Non-special | normal session, reply contains `✅` | status `ok` — glyph logic unchanged | N/A |
| Credits, any category | reply contains `Credits: 999996044` | stored + displayed + exported text never contains `Credits:` | N/A |
| Old row read | pre-feature stored row with `Credits:` | read/export builder strips `Credits:` on the fly (no migration) | N/A |

</frozen-after-approval>

## Code Map

- `backend/migrations/versions/` -- new revision, down_revision `d7c1a9e3f2b8`
- `backend/app/db/models.py` -- `GateCategory` (line 132), `CaptureSession` (line 421): add `special_mode`
- `backend/app/db/repos/gate_categories.py` -- `create` (l37): accept `special_mode`
- `backend/app/db/repos/capture_sessions.py` -- `create_active` (l98), `resolve_for_batch` (l159), `resolve_for_backfill` (l181): thread `special_mode`
- `backend/app/api/batches.py` -- gate resolve (l142) + `resolve_for_batch` call (l218): derive & pass `special_mode`
- `backend/app/api/sessions.py` -- `new_session` `create_active` (l366): pass `active.special_mode`
- `backend/app/core/redact.py` -- global Credits strip + special stats helpers
- `backend/app/core/capture.py` -- special status from Approveds count + special strip (l303-360)
- `backend/app/api/admin.py` -- category request/out models + CRUD (l941-1024)
- `frontend/app/admin/gates/page.tsx` -- `CategoryOut.special_mode` + toggle (create form + `CategoryRow`)

## Tasks & Acceptance

**Execution:**
- [x] `backend/migrations/versions/<rev>_category_special_mode.py` -- add NOT NULL `special_mode` (server_default false) to `gate_categories` and `capture_sessions` -- down_revision `d7c1a9e3f2b8`
- [x] `backend/app/db/models.py` -- add `special_mode: Mapped[bool]` (Boolean, `server_default=false()`, not null) to `GateCategory` and `CaptureSession`
- [x] `backend/app/db/repos/capture_sessions.py` -- `create_active`/`resolve_for_batch`/`resolve_for_backfill` take `special_mode: bool = False`; set it on each insert; in `resolve_for_batch`, refresh the reused active session's `special_mode` to the passed value
- [x] `backend/app/db/repos/gate_categories.py` -- `create` takes `special_mode: bool = False`
- [x] `backend/app/api/batches.py` -- after resolving the gate, load its category via `gate_categories_repo.get_by_id(session, gate.category_id)`, read `special_mode` (default False if missing), pass it into `resolve_for_batch`
- [x] `backend/app/api/sessions.py` -- `new_session` passes `special_mode=active.special_mode` to `create_active`
- [x] `backend/app/core/redact.py` -- extend `redact_reply_text` to also remove the `Credits:\s*\d+` segment (+ one adjacent `ヾ⌿` separator), idempotent; add `parse_approveds(text) -> int | None` (`Approveds!\s*✅\s*:\s*(\d+)`) and `strip_special_stats(text) -> str` (remove `Approveds!`/`Deads!` segments + adjacent separator, keep `Time:`)
- [x] `backend/app/core/capture.py` -- load `capture_session.special_mode` for the attributed session; when special: parse the Approveds count from the redacted-but-unstripped text BEFORE stripping (N≥1 → `STATUS_OK`, N==0 → `STATUS_REJECTED`, no line → previous-status/⏳), then `strip_special_stats` the text before the dedup compare and persist; non-special path unchanged
- [x] `backend/app/api/admin.py` -- add `special_mode: bool = False` to `CreateCategoryRequest`/`UpdateCategoryRequest`/`CategoryOut`/`_category_to_out`; `update_gate_category` sets `category.special_mode`
- [x] `frontend/app/admin/gates/page.tsx` -- add `special_mode` to local `CategoryOut`; a "Modo especial" switch in the create form and an inline toggle in `CategoryRow` (PATCH `/api/admin/gate-categories/{id}`)
- [x] `backend/tests/` -- unit-test the I/O matrix rows (parse/strip helpers + special status decision)

**Acceptance Criteria:**
- Given a special-mode session, when a reply with `Approveds! ✅: 0` is captured, then it is stored `rejected`, no CC row is created, and no credit is charged.
- Given a special-mode session, when a reply with `Approveds! ✅: 2` is captured, then status is `ok` and the stored/emitted text contains neither `Approveds!`, `Deads!`, nor `Credits:`, but still contains `Time:`.
- Given any category, when a reply containing `Credits: <n>` is captured OR an old row containing it is read, then no client-facing view or export shows `Credits:`.
- Given the owner enables `special_mode` on a category, when a client next STARTS a batch on a gate in that category, then that capture session runs in special mode.
- Given a non-special session, when any reply is captured, then status derivation and stored text are unchanged from current behavior.

## Spec Change Log

### 2026-06-18 — review patch (CRITICAL: dedup before status)
- **Finding (edge-case hunter):** the original ordering (strip → `previous.text == clean_text` dedup → derive status) drops a real approval. `strip_special_stats` removes the `Approveds! ✅: N` count from the stored text, so two revisions of one `message_id` differing ONLY in the count (`✅: 0` → `✅: 2`, same `Time:`) reduce to byte-identical `clean_text`; the text-only no-op swallowed the rejected→ok flip — never persisted, never charged, never emitted.
- **Amended:** derive `status` BEFORE the no-op check; key the no-op on `(text AND status)` (`capture.py`). Added capture-path regression test (`tests/test_special_mode_capture.py`).
- **Known-bad avoided:** silent loss of the very approval the feature exists to capture (plus a revenue miss — the first-✅ charge never fired).
- **KEEP:** parse the count from the pre-strip redacted text; strip Approveds!/Deads! before persist; special validity = `Approveds ≥ 1 ⇒ ok, == 0 ⇒ rejected, absent ⇒ ⏳ keep-previous`. Credits scrub stays global (capture + read).

### 2026-06-18 — review patches (HIGH/MED, no loopback)
- **Credits regex hardened** to `(?i)Credits\s*:\s*[\d.,]+` (was `\d+`, case-sensitive): a grouped balance like `999,996044` no longer leaks its tail, and case variance no longer defeats the scrub. Approveds/Deads/count matchers made case-insensitive too.
- **Model docstring corrected** (`CaptureSession.special_mode`): claimed "never rewritten in-flight", but `resolve_for_batch` deliberately refreshes the reused session at batch start (per the Always boundary). Wording now matches the behavior.

## Design Notes

Golden example (special mode):
```
in : ↳ Approveds! ✅: 0 ヾ⌿ Deads! ❌: 1 ヾ⌿ Credits: 999996044 ヾ⌿ Time: 32.95s
out: ↳ Time: 32.95s        # status=rejected (Approveds==0)
```

- **Order is load-bearing in capture:** parse the Approveds count from the redacted (Credits/Checked-By removed) but NOT-yet-stats-stripped text, then strip Approveds!/Deads!. Derive `status` BEFORE the no-op dedup, and key the no-op on **(text AND status)**, not text alone — the strip erases the count from `clean_text`, so a rejected→ok flip whose only other content is an unchanged `Time:` reduces to identical text and a text-only dedup would silently drop the approval (see Spec Change Log 2026-06-18).
- **Flag lives on the session** (always present; `batch_id` is SET-NULL-able). `resolve_for_batch` refreshes the reused same-gate session's `special_mode` to the current gate value so an owner's toggle takes effect on the next batch.

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` -- expected: revision applies cleanly
- `cd backend && .venv/bin/pytest` -- expected: all pass (incl. new capture/redact tests)
- `cd frontend && npm run build` -- expected: tsc typecheck + build succeed

## Suggested Review Order

**Validity + redaction (the core behavior)**

- Entry point — the special-mode verdict: Approveds≥1 ⇒ ok, ==0 ⇒ rejected, absent ⇒ ⏳ keep-previous.
  [`capture.py:329`](../../backend/app/core/capture.py#L329)
- CRITICAL fix — no-op dedup keys on (text AND status); the strip erases the count from `clean_text`.
  [`capture.py:356`](../../backend/app/core/capture.py#L356)
- The pure helpers: count parse + Approveds!/Deads! strip; tolerant, case-insensitive, idempotent.
  [`redact.py:83`](../../backend/app/core/redact.py#L83)
- Global `Credits:` scrub (capture + read); hardened `[\d.,]+` so a grouped balance can't leak its tail.
  [`redact.py:42`](../../backend/app/core/redact.py#L42)

**Snapshot plumbing (category flag → session → capture)**

- Owner toggle source of truth; CaptureSession snapshot read by the pipeline.
  [`models.py:153`](../../backend/app/db/models.py#L153)
- Reused active session refreshes its flag to the gate's current value at batch start.
  [`capture_sessions.py:184`](../../backend/app/db/repos/capture_sessions.py#L184)
- Batch start derives the flag from the gate's category and threads it in.
  [`batches.py:159`](../../backend/app/api/batches.py#L159)
- "Nueva sesión" fork copies the flag off the active session.
  [`sessions.py:372`](../../backend/app/api/sessions.py#L372)

**Schema**

- Two NOT NULL `server_default false` columns; backfills existing rows in one step.
  [`e1c7a4b9d2f0_category_special_mode.py:33`](../../backend/migrations/versions/e1c7a4b9d2f0_category_special_mode.py#L33)

**Admin API + UI**

- `special_mode` on the owner-only CategoryOut; PATCH `None` leaves it untouched (rename never resets).
  [`admin.py:1034`](../../backend/app/api/admin.py#L1034)
- Per-row toggle (immediate PATCH) + create-form checkbox.
  [`page.tsx:475`](../../frontend/app/admin/gates/page.tsx#L475)

**Tests (peripheral)**

- Capture-path: Approveds:0 ⇒ rejected/no-CC/no-charge, and the 0→1 flip regression guard.
  [`test_special_mode_capture.py:84`](../../backend/tests/test_special_mode_capture.py#L84)
- Pure-function matrix: Credits scrub, parse, strip, golden `↳ Time: 32.95s`.
  [`test_redact.py:40`](../../backend/tests/test_redact.py#L40)
