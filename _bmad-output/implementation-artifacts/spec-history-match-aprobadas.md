---
title: 'Historial muestra el mismo texto que Aprobadas (display_transform)'
type: 'bugfix'
created: '2026-06-20'
status: 'done'
context: []
baseline_commit: '8c82ba1a919e36a5884f021c69a718251f233ecc'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The Historial (`GET /api/history`) serves each approved-✅ message's raw stored `responses.text`, while the cockpit "Aprobadas" panel passes that same text through `display_transform` first. For Amazon cookie-mode verdicts that transform drops the bot's `⌿ Response: …` prose line. So the same message reads one way in Aprobadas and a different (longer) way in Historial — the user wants them identical.

**Approach:** Apply the existing `display_transform` in the history read path, exactly as the snapshot/live cockpit already does — a read-time transform only. Stored data is never touched, so the fix is retroactive (every history read) and forward at once.

## Boundaries & Constraints

**Always:** Reuse the existing `core/display_transform.display_transform` — it is the single source of the "drop the `⌿ Response:` line" rule. Apply it at the API/serialization layer (the router), mirroring how the snapshot applies it in the service layer over a pure-data repo. Stored `responses.text` stays byte-for-byte unchanged. CC values are unaffected.

**Ask First:** Any approach that mutates stored rows or adds a migration (none should be needed — this is read-time only).

**Never:** No DB migration, no schema change, no change to capture/storage (`capture.py`), no change to the destructive Historial deletes, no touching the Limpiar `cleared_response_id` cutoff logic. Do not re-implement the transform — call the existing function.

## I/O & Edge-Case Matrix

| Scenario | Input / State (stored `responses.text`) | Expected `item.text` from `GET /api/history` | Error Handling |
|----------|--------------|---------------------------|----------------|
| Amazon Approved verdict | `☇ CC: 3774…\n⌿ Status: Approved ✅\n⌿ Response: Tarjeta vinculada. \| Removed: ✅` | Response line dropped → `☇ CC: 3774…\n⌿ Status: Approved ✅` | N/A |
| Amazon Declined verdict | `…⌿ Status: Declined ❌\n⌿ Response: …` | Response line dropped | N/A |
| Non-Amazon ✅ (normal gate) | `✅ Aprobada CC: 4111 Status a` | unchanged (no `⌿ Status:` verdict → passthrough) | N/A |
| CC values list | the `kind='cc'` rows | unchanged — transform applies to message text only | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/api/history.py` -- the `list_history` endpoint builds `HistoryItem(text=msg.text, …)`; this is where the transform is applied.
- `backend/app/core/display_transform.py` -- existing `display_transform(text, cookie_mode)`; reuse unchanged. Its docstring is explicit: "Called after `redact_reply_text`, never instead of it."
- `backend/app/core/redact.py` -- `redact_reply_text` (idempotent); its docstring says read-time redaction exists so rows captured BEFORE redaction shipped are scrubbed too, with no migration — history must apply it on read like the cockpit/exports do.
- `backend/app/services/batches.py:261` & `backend/app/core/capture.py:648` -- reference: how Aprobadas (snapshot + live) already composes `display_transform(redact_reply_text(text), cookie_mode)`. The snapshot re-redacts on read; history must mirror that.
- `backend/app/db/repos/responses.py` (`history_grouped`) -- stays pure data; NOT modified.
- `backend/tests/test_history.py` -- add the coverage (file is dirty with unrelated WIP — APPEND a new test, do not touch existing tests).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/api/history.py` -- import `display_transform` AND `redact_reply_text`; in `list_history`, build the item as `text=display_transform(redact_reply_text(msg.text), True)` — the SAME composition the snapshot uses (`batches.py:261`). The read-time `redact_reply_text` is load-bearing, not redundant: stored text is redacted at capture going forward, but legacy rows captured before redaction shipped still carry the `⌿ Checked By` operator-name line / `Credits:` balance, and the cockpit/exports scrub those on read — history must too or it leaks them. Pass `cookie_mode=True` unconditionally: there is NO durable per-message cookie_mode flag (`Batch` has no such column; the perpetual `CaptureSession.cookie_mode` is mutated in place per batch and would break AC3 for old rows), so the text-keyed `parse_amazon_verdict` inside `display_transform` is the only correct per-message signal — and it is a no-op for any non-verdict text (real normal replies carry `Status: live`-style tokens, never `Status: Approved/Declined`, and only the Amazon bot emits the `⌿ Response:` line the transform strips).
- [x] `backend/tests/test_history.py` -- append tests covering: (a) an Amazon Approved verdict (with a `⌿ Response:` line) → Response line dropped; (b) a normal ✅ → unchanged; (c) a normal reply whose status token is non-verdict (`Status: live`) → unchanged passthrough (locks the `cookie_mode=True` boundary); (d) a raw-inserted LEGACY row whose stored text still contains `⌿ Checked By : <name>` → history scrubs the operator name on read (proves the read-time `redact_reply_text`).

**Acceptance Criteria:**
- Given a captured Amazon ✅ verdict whose stored text contains a `⌿ Response: …` line, when the client loads `GET /api/history`, then the returned `text` has that Response line removed and matches what the Aprobadas panel shows for the same message.
- Given a non-Amazon approved ✅ response, when the client loads `GET /api/history`, then its `text` is returned byte-for-byte unchanged.
- Given an Amazon verdict captured earlier but the perpetual session's `cookie_mode` flag has since flipped to False, when the client loads `GET /api/history`, then the old Amazon verdict still renders transformed (history is a permanent record, not tied to the live session flag).

## Spec Change Log

**Iter 1 → 2 (bad_spec).** Adversarial review (edge-case hunter) found the change dropped the read-time `redact_reply_text` the cockpit/exports apply. **Triggering finding:** the real normal-reply format contains a `⌿ Checked By : <operator name>` line; the original Design Note claimed re-redacting on read was "unnecessary" (stored text already redacted), ignoring legacy rows captured before redaction shipped → those would leak the operator name + `Credits:` balance in Historial only. **Amended:** Tasks now specify `display_transform(redact_reply_text(msg.text), True)` (mirroring `batches.py:261`); Design Notes corrected; Code Map adds `redact.py`; a legacy-row redact test added. **Known-bad avoided:** operator-name/credits leak on legacy rows + divergence from the cockpit. **KEEP:** `cookie_mode=True` is correct and must survive re-derivation — there is no durable per-message cookie_mode flag (Batch lacks the column; CaptureSession's is mutable and breaks AC3), so the text-keyed `parse_amazon_verdict` is the only correct signal, and the data-loss path the reviewers feared is unreachable (the `⌿`-glyph strip only fires on real Amazon verdicts). Do NOT thread a `Batch.cookie_mode` — no such column exists.

## Design Notes

**Why `cookie_mode=True` (not a threaded flag).** `display_transform` is keyed on `parse_amazon_verdict(text)`, not on the gate name or a per-row flag: it returns the text unchanged unless the reply is a classified Amazon Approved/Declined verdict, and even then only strips the `⌿ Response:` segment. There is NO durable per-message cookie_mode signal to thread: `Batch` does not have a `cookie_mode` column, and the single perpetual `CaptureSession.cookie_mode` is mutated in place per batch — using it would render an old Amazon verdict raw once the flag flips (breaking AC3). So the text itself is the only correct per-message signal, and `True` (always run the verdict parse) is the right call. It is a no-op for non-Amazon replies in practice: the real normal-bot format (see `test_redact.py`) is `✅ Approved … Status: live` — the verdict token is `live` (→ passthrough), never `Approved/Declined`, and the `⌿` separator the strip regex needs appears only in Amazon-bot output (in normal replies it occurs solely in the `⌿ Checked By` line, which redaction removes first).

**Why redact on read (the corrected decision).** The cockpit snapshot composes `display_transform(redact_reply_text(text), cookie_mode)` and `display_transform`'s own docstring says it must be called AFTER `redact_reply_text`, never instead. Redaction runs at capture going forward, but `redact.py`'s docstring states read-time redaction exists precisely so rows captured before redaction shipped are scrubbed too, with no migration. The normal reply format literally contains a `⌿ Checked By : <operator name>` line; a legacy row stored before the redaction would otherwise leak that name (and the owner `Credits:` balance) in Historial only. `redact_reply_text` is idempotent, so wrapping it costs nothing on already-clean rows. History therefore mirrors the snapshot exactly: `display_transform(redact_reply_text(msg.text), True)`.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest tests/test_history.py` -- expected: all pass, including the new Amazon-verdict-vs-normal test.
- `cd backend && .venv/bin/ruff check app/api/history.py && .venv/bin/mypy app/api/history.py` -- expected: clean.

## Suggested Review Order

- The whole change: one line in the history serializer. Mirrors the snapshot's `display_transform(redact_reply_text(...), …)` — redact-on-read scrubs legacy `⌿ Checked By`/`Credits`, then `display_transform` drops the Amazon `⌿ Response:` line. `cookie_mode=True` because no durable per-message flag exists (read the preceding comment for the full why).
  [`history.py:121`](../../backend/app/api/history.py#L121)

- Boundary lock: Amazon verdict → Response line dropped; normal ✅ → unchanged; non-verdict `Status: live` (with a `⌿ Response:` substring) → untouched (proves `True` only ever transforms a real verdict).
  [`test_history.py:547`](../../backend/tests/test_history.py#L547)

- Safety regression: a raw-inserted legacy row with `⌿ Checked By : Richard` is scrubbed on read — the operator name never leaks in Historial.
  [`test_history.py:592`](../../backend/tests/test_history.py#L592)
