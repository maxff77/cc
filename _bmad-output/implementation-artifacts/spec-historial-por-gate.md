---
title: 'Historial de lives — responses aprobadas por gate (PR-2)'
type: 'feature'
created: '2026-06-20'
status: 'ready-for-dev'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** PR-1 removed the session-based "Historial". Clients still need a history — but of their **approved (✅) captured responses grouped by gate**, not sessions. They must be able to delete a single response, a whole gate's history, or everything.

**Approach:** A new read-only client history (`GET /api/history`) that groups the tenant's approved-✅ captured messages by the batch's gate snapshot, plus three DESTRUCTIVE deletes (one response / one gate / all). It reads the persisted `responses` rows DIRECTLY and is fully independent of the cockpit Limpiar cutoff. Re-add the "Historial" nav + a new page.

## Boundaries & Constraints

**Always:** `tenant_id` ONLY from the session cookie; unknown/foreign/oversized ids 404 identically (no existence leak). Group + key on `batches.gate_name` + `gate_display_value` (client-visible). History IGNORES `cleared_response_id` (Limpiar never affects history). Deletes are destructive BY DESIGN (the client's own data) — they remove `responses` rows; `responses` is the child of `batches`/`batch_lines`, so deleting it has no cascade blocker and never touches batches/send_log/lines. An "approved" message = the message whose LATEST `kind='full'` revision is `status='ok'`.

**Never:** 🔒 NEVER expose `gate_value` to the client (owner-only) — group/key/return only `gate_name` + `gate_display_value`. Do NOT touch the live capture/attribution/cockpit paths. Do NOT add the cutoff filter to history reads. Do NOT delete `batches`/`send_log`/`batch_lines` rows (only `responses`). Do NOT regenerate `frontend/types/api.ts` (hand-curated aliases; define history types inline).

## API contract (both slices implement to THIS)

- `GET /api/history` → `{ gates: [{ name, display_value, count, items: [{ id, text, captured_at, cc: string[] }] }] }`. `items` = approved-✅ messages (the latest `kind='full'` revision per `(chat_id, message_id)` whose status is `ok`) for the tenant, each with its extracted `cc` values, grouped by the batch's `gate_name`. `id` = that latest ✅ revision's `responses.id` (the delete handle). Responses with `batch_id` NULL (no gate) → a trailing group `{ name: null, display_value: "Sin gate", … }`. Gates ordered by most-recent activity; items newest-first.
- `DELETE /api/history/response/{response_id}` → resolve `(chat_id, message_id)` from that row (404 if not the tenant's), delete EVERY `responses` row (full revisions + cc) for that `(tenant_id, chat_id, message_id)`. Returns `{ deleted: n }`.
- `DELETE /api/history/gate?name=<gate_name>` → delete every `responses` row for the tenant whose `batch.gate_name == name`. Returns `{ deleted: n }`.
- `DELETE /api/history` → delete EVERY `responses` row for the tenant. Returns `{ deleted: n }`.

## I/O & Edge-Case Matrix

| Scenario | State | Behavior | Error |
|---|---|---|---|
| List | tenant has ✅ + ❌ + ⏳ captures over gates A,B | only messages whose latest full revision is ✅ appear, grouped by A,B; ❌-latest and ⏳-only excluded; cc per message included | empty ⇒ `{gates: []}` |
| List ignores Limpiar | tenant pressed Limpiar (cutoff set) | history still returns ALL ✅ (cutoff NOT applied) | N/A |
| Delete one | item id belongs to tenant | all full+cc rows of that message gone; other messages untouched | foreign/unknown id ⇒ 404 identical |
| Delete gate | gate_name=A | only gate-A responses gone; gate B + "Sin gate" intact | unknown name ⇒ `{deleted:0}` 200 |
| Delete all | any | every responses row for the tenant gone | another tenant's rows untouched |
| Gate value safety | any | response payload + keys carry gate_name/display_value only, NEVER gate_value | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/db/repos/responses.py` -- add `history_grouped(session, tenant_id)` (DISTINCT ON `(chat_id,message_id)` ORDER BY id DESC, keep rows whose latest full revision is `STATUS_OK`, LEFT JOIN `Batch` for gate_name/display_value, attach cc per message), `delete_message_group(session, tenant_id, response_id) -> int` (resolve chat_id/message_id, tenant-check, delete all rows for that message), `delete_by_gate(session, tenant_id, gate_name) -> int`, `delete_all_for_tenant(session, tenant_id) -> int`. None touch the cutoff.
- `backend/app/api/history.py` -- NEW router (`/api/history`): GET + the 3 DELETEs; `get_current_user` for identity, tenant from session, Pydantic out-models (gate_name/display_value/count/items). Shapes the repo rows into the contract.
- `backend/app/main.py` -- include the history router.
- `frontend/components/client-nav.tsx` -- add `{ href: "/app/historial", label: "Historial" }` to `ITEMS`.
- `frontend/app/app/historial/page.tsx` -- NEW page: TanStack Query `GET /api/history`; gate groups (cards) each listing its ✅ responses (reuse the row look from `components/sessions/response-row.tsx` if clean, else a simple row) showing text + time + cc; per-response trash button (`DELETE /api/history/response/{id}`), per-gate "Borrar historial de este gate" (`DELETE /api/history/gate?name=`), global "Borrar todo" (`DELETE /api/history`). `ConfirmDialog` (@/components/ui/confirm-dialog, destructive) for gate + all; invalidate the query on success; empty state. Define the response TS types INLINE (do not edit types/api.ts).

## Tasks & Acceptance

**Execution:**
- [ ] `backend/app/db/repos/responses.py` -- add the 4 functions above; reuse `KIND_FULL`/`KIND_CC`/`STATUS_OK`; deletes are plain `delete()` filtered by `tenant_id` (+ message / + gate join). No cutoff anywhere.
- [ ] `backend/app/api/history.py` -- NEW router with GET + 3 DELETEs per contract; tenant only from session; foreign/unknown ids 404 identically; never serialize `gate_value`.
- [ ] `backend/app/main.py` -- mount the history router.
- [ ] `frontend/components/client-nav.tsx` -- add the "Historial" → `/app/historial` nav item.
- [ ] `frontend/app/app/historial/page.tsx` -- NEW page rendering gate-grouped ✅ responses + the three delete controls with confirm dialogs + query invalidation + empty state; inline TS types.
- [ ] `backend/tests/test_history.py` -- NEW: GET groups only ✅-latest by gate (excludes ❌-latest/⏳, ignores Limpiar cutoff, never leaks gate_value); delete-one removes a message's full+cc and 404s cross-tenant; delete-by-gate scopes to one gate; delete-all wipes only the acting tenant; tenant isolation.

**Acceptance Criteria:**
- Given captures of ✅, ❌, and ⏳ across gates A and B, when `GET /api/history`, then only messages whose latest full revision is ✅ appear, grouped by gate (name + display_value, never value), each with its cc.
- Given a tenant that pressed Limpiar, when `GET /api/history`, then every ✅ message is still returned (the cutoff is not applied).
- Given a history item id, when `DELETE /api/history/response/{id}`, then all full+cc rows of that message are deleted and other messages remain; a foreign/unknown id 404s identically.
- Given gate A, when `DELETE /api/history/gate?name=A`, then only gate-A responses are deleted; gate B and "Sin gate" remain.
- Given `DELETE /api/history`, then every responses row for the acting tenant is gone and another tenant's rows are untouched.
- Given the cockpit, when rendered, then a "Historial" nav link points to `/app/historial` and the page lists gate groups with working per-response / per-gate / delete-all controls; `npm run build` passes.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest` -- expected: new `test_history.py` + existing suite green.
- `cd frontend && npm run build` -- expected: tsc passes; new `/app/historial` route present.

**Manual checks:**
- Capture a ✅ on a gate → it appears under that gate in /app/historial; press Limpiar in the cockpit → it STILL appears in history; delete it → gone; delete-by-gate / delete-all clear as scoped.
