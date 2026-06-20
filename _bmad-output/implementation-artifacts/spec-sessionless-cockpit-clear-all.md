---
title: 'Cockpit sin sesiones + Limpiar literal (PR-1)'
type: 'feature'
created: '2026-06-20'
status: 'done'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The cockpit exposes a full session lifecycle (list/detail/rename/continue/new/delete) and a "Limpiar" that only soft-hides ❌ rows from Completa, while sessions rotate per gate. Clients want a continuous, session-free console and one button that visibly clears all three live panels.

**Approach:** Collapse to exactly ONE ever-living `capture_session` per tenant (get-or-create, never rotated/renamed/continued/closed). Remove the user-facing session concept from the UI and its client REST surface. Replace the clear with a single "Limpiar" that clears all three live panels (Completa, Aprobadas-✅, Datos-CC) via a NON-destructive per-session `cleared_response_id` view-cutoff (an `id` high-water-mark) plus a client-side live-store reset — persisted ✅ rows survive untouched for a deferred PR-2 history.

## Boundaries & Constraints

**Always:** `tenant_id` only from the session cookie (never body/path); unknown/foreign/oversized ids 404 identically. Attribution keys on `send_log(chat_id, message_id)` — untouched. Activation/creation of the perpetual session is API-only (batch-start), never from the capture path. Telethon only in `core/telegram.py` with `parse_mode=None`. Repos flush-not-commit. Only ✅/❌ revisions persist. `cleared_response_id` is a DISPLAY filter applied ONLY in the cockpit/snapshot read path — never in any integrity/attribution/reconciler/dedup/credit/`awaiting_reply` query.

**Decided invariant change (was Ask-First, now settled):** CC dedup widens from per-rotating-session to **tenant-lifetime**. With one perpetual session, `uq_responses_session_cc(capture_session_id, text)` dedups a CC value for the tenant's whole life (Limpiar is cutoff-only and does NOT touch the `add_new_cc` dedup SELECT). This is accepted and tested, not questioned.

**Decided (was Ask-First, now settled):** the cockpit `.txt` export footer **respects the cutoff** — it exports only the live (post-Limpiar) view, consistent with "limpiar literal". The cockpit export read path therefore threads `cleared_response_id` exactly like the on-screen panels. (Full-history dumps belong to the deferred PR-2 history, not the cockpit.)

**Never:** Do NOT DELETE any `responses` rows (Limpiar is a view cutoff only). Do NOT drop the `capture_sessions` table, `uq_capture_sessions_one_active_per_tenant`, or `uq_responses_session_cc`. Do NOT call `ensure_perpetual` (or otherwise INSERT/activate a session) from the capture/backfill path. Do NOT run a deactivate-all-active UPDATE on the batch hot path. Do NOT build PR-2 (Historial-por-gate: approved-✅ grouped by gate + delete-one/by-gate/all) — out of scope; PR-1 only leaves it intact. Do NOT apply the cutoff to admin support reads or integrity queries.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First send ever | Tenant has no capture_session | `ensure_perpetual` (API/batch-start only) SELECTs FOR UPDATE; none found ⇒ INSERTs one `is_active=true` row; batch binds to it | Concurrent first-ever INSERT → IntegrityError on partial index → rollback + re-SELECT returns the single row |
| Gate change | Active perpetual session, batch on a different gate | Reuse the SAME session; refresh `gate_value/gate_name/gate_display_value/special_mode/cookie_mode` snapshots in place; NO second row, NO `is_active`/id churn | N/A |
| Late-reply backfill (session exists) | Reply for a NULL-bound batch; tenant HAS a perpetual session | `resolve_for_backfill` READ-ONLY SELECTs the active session, rebinds the batch to it, attributes the reply | Never raises the partial-index IntegrityError → never hits capture poison-drop |
| Late-reply backfill (no session yet) | Reply for a pre-PR-1 NULL-bound batch; tenant has NEVER created a perpetual session | `resolve_for_backfill` returns None → the reply is bucketed UNMATCHED (NOT persisted: `responses.capture_session_id` is NOT NULL and minting a session from the capture path is forbidden). Accepted: near-zero reachability (an old NULL-bound batch getting a late reply before the tenant's first sessionless batch); visible in the unmatched/guardrail bucket, not silently lost | Read-only by design — never poison-drops |
| Limpiar | Active session with rows in all 3 panels | `cleared_response_id = MAX(responses.id)` stamped (session FOR UPDATE); re-emit `session.active` (post-cutoff slice, all 3 panels empty); client store reset; 0 `responses` rows deleted | 404 identically if no session resolvable for tenant |
| Limpiar non-destructive | ✅ rows captured pre-clear | Approved rows stay in DB (queryable without cutoff); cockpit shows them hidden | N/A |
| Reconnect after Limpiar | New WS snapshot post-cutoff | Snapshot path merges `active_session_data` → same cutoff applied → panels stay empty (no resurrection) | N/A |
| Late reply for pre-cutoff line | Reply arrives after Limpiar for a line sent before it | Persisted + attributed normally; hidden from cockpit (id ≤ cutoff); `awaiting_reply` unaffected | N/A |
| Integrity after Limpiar | `responded_line_count` / reconciler run | Counts ALL rows ignoring cutoff → "esperando respuesta" does NOT spike, no re-fetch | N/A |
| CC re-capture after Limpiar / cross-gate | Same CC text seen again on any gate, any time | Still deduped by `uq_responses_session_cc` (cutoff does not touch dedup SELECT); never re-inserts | N/A |
| Cookie-mode hold | Cookie-mode gate batch | Serialize gate/hold lives on `Batch.awaiting_verdict_until/awaiting_message_id`, NOT on capture_session → unaffected by the collapse | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/db/models.py` -- `CaptureSession` + `uq_capture_sessions_one_active_per_tenant`; add `cleared_response_id`. `Response` (`hidden_at`, `id` PK monotonic, `created_at` server_default `func.now()` = txn-start ⇒ ties), `uq_responses_session_cc`.
- `backend/migrations/versions/f6a2d9c4e1b7_responses_hidden_at.py` -- current Alembic head; the new migration chains `down_revision='f6a2d9c4e1b7'` and MUST be the sole new head.
- `backend/app/db/repos/capture_sessions.py` -- add `ensure_perpetual` (SELECT-FOR-UPDATE / INSERT / IntegrityError-re-SELECT); rewrite `resolve_for_batch` to reuse the one session + refresh gate snapshots in place; make `resolve_for_backfill` READ-ONLY (plain SELECT, no INSERT/activate); add `clear_view`. `create_active`/`activate`/`continue`/`delete` stay defined but become dead-but-callable (unwired from client AND admin).
- `backend/app/db/repos/responses.py` -- thread KEYWORD-ONLY `cleared_response_id` into DISPLAY reads only: `_list_last`/`list_full`/`list_cc`/`full_count`/`cc_count` (note `list_cc` currently has NO `include_hidden`/`status` params — keep its positional `(session, id, limit)` and add `*, cleared_response_id=None`). Leave `last_full_revision`/`has_ok_revision`/`responded_line_count`/`add_new_cc` cutoff-agnostic.
- `backend/app/api/admin.py` (1465-1466, 1497) -- calls `list_full`/`list_cc` POSITIONALLY with `None`; keyword-only `cleared_response_id` keeps these correct (no cutoff on admin reads).
- `backend/app/services/batches.py` -- `active_session_data` (185): read the session cutoff, pass to the 4 display reads; keep `awaiting_reply` (237, via `awaiting_reply_count`) cutoff-agnostic. `snapshot` (261) merges `active_session_data` at 294/339 → cutoff covers reconnect by construction.
- `backend/app/api/sessions.py` -- remove list/detail/rename/continue/new/delete from the CLIENT surface; repurpose `clear_declined`→ POST `/api/sessions/clear` (no path id; resolve perpetual session FOR UPDATE, stamp cutoff, re-emit). Keep `SessionOut`/`SessionDetailOut`/`session_to_out`/export for admin + PR-2.
- `backend/app/api/batches.py` -- batch-create binding: bind via `ensure_perpetual` instead of rotate-on-gate-mismatch; keep IntegrityError fallback.
- `backend/app/core/attribution.py` / `core/capture.py` -- backfill stays read-only; capture poison-drop path (`_is_transient` is connectivity-shaped) never sees a session-index IntegrityError.
- `frontend/lib/ws.ts` -- add `clearCockpit()` (empty responses/cc + zero `responsesTotal`/`responsesOkTotal`/`ccNew`, KEEP `sessionId` and KEEP `awaitingReply`); drop `renameActiveSession`. `response.captured` session-guard is already inert (one perpetual `sessionId`) — leave as-is.
- `frontend/types/api.ts` -- openapi-typescript codegen ("do not edit"): REGENERATE after route removal, do not hand-edit.
- `frontend/app/app/page.tsx` -- remove `import { ActiveSessionCard }` AND `<ActiveSessionCard/>` render; drop clear-declined flow; add one non-destructive Limpiar.
- `frontend/components/sessions/response-views.tsx` -- single Limpiar clears all 3 panels; pass the new disable/clear props to BOTH `ResponseColumns` and `ResponseTabs`; keep "onClear absent ⇒ no button" for history/admin consumers.
- `frontend/components/sessions/active-session-card.tsx`, `frontend/app/app/sessions/page.tsx`, `frontend/app/app/sessions/[id]/page.tsx` -- DELETE these files in PR-1; drop "Historial" nav in `client-nav.tsx` + `admin-shell.tsx`.

## Tasks & Acceptance

**Execution:**
- [ ] `backend/app/db/models.py` -- add nullable `cleared_response_id BigInteger` to `CaptureSession` (NULL = nothing cleared); keep `uq_capture_sessions_one_active_per_tenant` + `uq_responses_session_cc`.
- [ ] `backend/migrations/versions/<new>_capture_sessions_cleared_response_id.py` -- `op.add_column` nullable `cleared_response_id`, `down_revision='f6a2d9c4e1b7'`, no backfill; verify it is the ONLY new head (no multi-head).
- [ ] `backend/app/db/repos/capture_sessions.py` -- `ensure_perpetual(session, tenant_id)`: SELECT active FOR UPDATE → return if found; else INSERT one `is_active=true` row; on IntegrityError rollback + re-SELECT + return. NO deactivate-UPDATE, NO id/`is_active` churn. Rewrite `resolve_for_batch` to reuse the one session + refresh gate snapshots in place. Make `resolve_for_backfill` READ-ONLY (plain SELECT; if none, defer to send_log/batch attribution without inserting/activating). Add `clear_view(session, cs)` stamping `cleared_response_id = (SELECT MAX(id) FROM responses)` (flush-not-commit).
- [ ] `backend/app/db/repos/responses.py` -- add KEYWORD-ONLY `cleared_response_id=None` to `_list_last`/`list_full`/`list_cc`/`full_count`/`cc_count`; when set, AND `Response.id > cleared_response_id`. Keep `list_cc`'s existing positional `(session, id, limit)` shape. Leave `add_new_cc`/`responded_line_count`/`has_ok_revision`/`last_full_revision` untouched.
- [ ] `backend/app/api/batches.py` -- bind batch via `ensure_perpetual` (no rotate-on-gate-mismatch); keep IntegrityError fallback.
- [ ] `backend/app/services/batches.py` -- in `active_session_data` pass the session's `cleared_response_id` to the 4 display reads; keep `awaiting_reply` cutoff-agnostic. Confirm `snapshot` inherits the cutoff via its `active_session_data` merge.
- [ ] `backend/app/api/sessions.py` -- remove client list/detail/rename/continue/new/delete; add POST `/api/sessions/clear` (resolve perpetual session FOR UPDATE, `clear_view`, commit, re-emit `session.active`); keep shared schemas/`session_to_out`/export for admin + PR-2.
- [ ] `backend/app/services/exports.py` + the cockpit export endpoint -- the cockpit `.txt` export (the panels' `↓ .txt` footer, on the perpetual session) passes the session's `cleared_response_id` to the export builders so the file mirrors the live post-Limpiar view; the ADMIN/PR-2 export path stays cutoff-agnostic (full history).
- [ ] `frontend/lib/ws.ts` -- add `clearCockpit()` (responses:[], cc:[], `ccNew`/`responsesTotal`/`responsesOkTotal`:0; KEEP `sessionId`, `awaitingReply`, batch, watchdog, credits); remove `renameActiveSession`. Do NOT zero `awaitingReply` (it is cutoff-agnostic server-side; zeroing causes a 0→N flicker on the next frame). Leave the now-inert `response.captured` `session_id` guard unedited.
- [ ] `frontend/types/api.ts` -- regenerate via openapi-typescript so removed `/api/sessions/{id}` routes drop from the types (do not hand-edit).
- [ ] `frontend/components/sessions/response-views.tsx` -- one Limpiar clears all 3 panels; disable when ALL panels empty (`responsesTotal===0 && ccNew===0`), not the old `declinedCount`; thread the prop to BOTH `ResponseColumns` and `ResponseTabs`; preserve "onClear absent ⇒ no button".
- [ ] `frontend/app/app/page.tsx` -- remove `import { ActiveSessionCard }` + `<ActiveSessionCard/>`; remove clear-declined mutation/dialog; wire single non-destructive Limpiar (confirm primary → POST `/api/sessions/clear` → `clearCockpit()`); disable when all panels empty.
- [ ] DELETE `frontend/components/sessions/active-session-card.tsx`, `frontend/app/app/sessions/page.tsx`, `frontend/app/app/sessions/[id]/page.tsx`; drop "Historial" nav in `client-nav.tsx`/`admin-shell.tsx`; clean dead imports/helpers (`clearSession` removed if now unused) → `npm run build` (tsc) passes.
- [ ] `backend/tests/test_clear_declined.py` -- rewrite to cutoff semantics: clear hides all 3 panels' display reads, deletes 0 rows, `responded_line_count`/dedup/`has_ok_revision` unchanged, 404 tenant isolation; add a case that a CC value cleared then re-seen (incl. cross-gate) is NOT re-inserted (tenant-lifetime dedup); add same-instant tie case (two rows same `created_at`, id high-water cleanly splits them).
- [ ] `backend/tests/test_sessions.py` -- prune the removed-client-endpoint tests (list/detail/rename/continue/new/delete + their slice of the cross-tenant 404 sweep); keep admin export + schema/`session_to_out` coverage; add a 404-isolation case for the NEW POST `/api/sessions/clear` so the suite stays green.

**Acceptance Criteria:**
- Given a tenant with no capture_session, when a batch is created, then exactly one `is_active=true` row exists and the batch binds to it; a concurrent first-ever create resolves to that single row.
- Given the perpetual session on gate A, when a batch on gate B is created, then no second session row exists, `is_active`/id are unchanged, and the gate snapshots are refreshed in place.
- Given a late reply with no session yet created, when backfill runs in the capture consumer, then it never INSERTs/activates a session and the reply is never poison-dropped.
- Given rows in all three panels, when Limpiar is pressed, then all three live panels empty in the acting tab and after reconnect, zero `responses` rows are deleted, and the "esperando respuesta" badge does not flicker to 0.
- Given approved ✅ rows captured before Limpiar, when queried without the cutoff, then every ✅ row is still returned (PR-2-ready).
- Given a Limpiar, when `responded_line_count`/the reconciler runs, then "esperando respuesta" does not spike and no reply is re-fetched.
- Given a CC value seen on gate A, when the same value reappears on gate B much later, then it is NOT re-inserted (tenant-lifetime dedup).
- Given a cookie-mode batch, when the session collapses, then the verdict hold/rotation (on `Batch.*`) is unaffected.
- Given the cockpit UI, when rendered, then no session list/detail/rename/continue/new/delete control or "Historial" nav link is present and `npm run build` passes.
- Given a foreign/unknown tenant context, when POST `/api/sessions/clear` is called, then it 404s identically with `tenant_id` taken only from the session.
- Given rows captured before a Limpiar, when the cockpit `.txt` export is downloaded after the clear, then the file contains only post-cutoff rows (mirrors the live panels), while the admin/PR-2 export of the same data still returns the full history.

## Design Notes

`cleared_response_id` follows the `hidden_at` discipline exactly: DISPLAY reads filter it, every integrity/attribution/reconciler/dedup query ignores it; they compose. The cutoff is an **id high-water-mark, not a timestamp** — `Response.created_at` uses `server_default=func.now()` (Postgres txn-start time), so rows in one capture transaction share a timestamp and `created_at >` would leak/hide boundary rows; `Response.id` is monotonic and already the `_list_last` sort key, so `Response.id > cleared_response_id` is tie-immune. Multi-tab/reconnect consistency reuses the proven path: Limpiar re-emits `session.active` (post-cutoff slice) and `snapshot` merges the same `active_session_data`, so both the active-tab re-emit and reconnect apply one cutoff by construction.

**Perpetual-session get-or-create:** `ensure_perpetual` is a pure singleton get-or-create — there is by definition no "prior active row to clear", so the "clear prior row FIRST" flip pattern does NOT apply here; the partial unique index only guards the single first-ever-creation race, covered by the IntegrityError re-SELECT. Creation/activation is API-only; backfill is read-only — preserving the existing "activation is an API-only act" invariant the capture poison-drop path depends on.

**Known accepted regression — `awaiting_reply` drift:** over one perpetual session the "esperando respuesta" badge (`sent − responded`, cutoff-agnostic) accumulates every never-answered line for the tenant's whole history with no reset. This is intentional in PR-1 (the badge means "lines still waiting", which Limpiar does not answer). A future per-session awaiting high-water-mark MAY be added without touching integrity/reconciler queries — out of scope here.

**PR-2 note (leave intact):** the perpetual session's gate snapshot is overwritten per batch, so PR-2's group-by-gate MUST key on `responses.batch_id → batches.gate_*`, NOT the session snapshot — keep `responses.batch_id`/`line_id` populated and the join path alive. `create_active`/`activate`/`continue`/`delete` remain defined for PR-2 but are unwired from BOTH client and admin so no path can mint a second active session.

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic heads` -- expected: a single head (the new `cleared_response_id` migration), no multi-head.
- `cd backend && .venv/bin/alembic upgrade head` -- expected: new migration applies clean, chains off `f6a2d9c4e1b7`.
- `cd backend && .venv/bin/pytest` -- expected: rewritten `test_clear_declined.py` + pruned `test_sessions.py` + existing suite green; no responses-row deletion on clear.
- `cd frontend && npm run build` -- expected: tsc passes; no dangling `ActiveSessionCard`/`clearSession`/session-route imports; regenerated `types/api.ts` has no removed session paths.

**Manual checks (if no CLI):**
- In the cockpit: only the send form + three live panels + one "Limpiar" button render; pressing Limpiar empties all three panels with no "esperando respuesta" 0→N flicker, a reload keeps them empty, and a direct (no-cutoff) `responses` query still returns the ✅ rows.
