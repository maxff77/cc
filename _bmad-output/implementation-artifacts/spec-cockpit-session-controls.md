---
title: 'Cockpit session controls — show active, rename, start new'
type: 'feature'
created: '2026-06-12'
status: 'done'
baseline_commit: 'c9c0c7a17a826326d6d0cb0bcaa089b551cf8b20'
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/3-4-continuar-una-sesion-con-dedup-preservado.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Capture-session lifecycle is fully implicit. A session is born only on batch send and only closes when a batch goes out on a *different* gate (`resolve_for_batch`). So a client working one gate has a single ever-active session: he can never start a clean one (dedup never resets), never gets a "Retomar" row in Historial (that button renders only on *closed* sessions), and the cockpit never shows *which* session he is in. User reports: no way to start, retake, or rename a session, and no visible active session.

**Approach:** Add an "active session" strip to the Envío cockpit that (1) shows the active session's name + gate, (2) renames it inline (existing `PATCH /api/sessions/{id}`), and (3) starts a fresh session on the *same gate* via a new `POST /api/sessions/new` — which closes the current one (making it a "Continuar"-able Historial row) and arms a clean dedup set. No Historial changes: its Retomar already works; this just makes closed sessions exist and surfaces session identity where the user sends.

## Boundaries & Constraints

**Always:**
- `tenant_id` comes ONLY from the session (`user.tenant_id`), never body/path.
- `POST /api/sessions/new` mirrors `continue_session` exactly: live-batch guard ⇒ 409 `batch_live`; `IntegrityError` on the one-active-per-tenant index ⇒ 409 `session_conflict`; emit `session.active` (verbatim `active_session_data`) post-commit; return `SessionOut`.
- "Nueva sesión" forks the **currently active session's gate** via `capture_sessions_repo.create_active` (reuse the existing primitive — do not write new repo code).
- Cockpit live state stays WS-driven (UX-DR12): rename/new seed nothing optimistic beyond what the server-confirmed response/event carries; the `session.active` emit is what rebinds every tab.
- Frontend name validation mirrors backend `RenameSessionRequest` (trim, non-empty, no invisible chars, ≤200) — reuse the `validateSessionName`/`NON_PRINTABLE_RE` logic already in `sessions/page.tsx` (duplicate it; App Router pages can't export helpers — accepted precedent).

**Ask First:**
- If forking only the active gate is too narrow (user wants to pick an arbitrary gate for the new session from the cockpit). Current design: no gate picker — to start a session on a new gate, send a batch on it (existing behavior).

**Never:**
- Do NOT touch the legacy root app (`app.py`/`core.py`/`static/`).
- Do NOT add a Historial "Nueva sesión" button or change its Retomar/rename/delete rows.
- Do NOT auto-close sessions anywhere else (logout, plan expiry, batch completion) — closing stays an explicit user act.
- Do NOT remove the `resolve_for_batch` same-gate reuse — "Nueva sesión" is the explicit opt-out, not a replacement.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| New session, idle, has active session | `POST /api/sessions/new`, no live batch | Closes current, inserts fresh active session on same gate, emits `session.active` (empty slice), returns 200 `SessionOut` | N/A |
| New session, no active session exists | `POST /api/sessions/new`, tenant has zero sessions | 404 `session_not_found` (nothing to fork; cockpit hides the button anyway) | 404 |
| New session, batch live/paused/stopping | `POST /api/sessions/new` while a batch lives | 409 `batch_live` — copy verbatim | 409 |
| New session, concurrent activation race | two new/continue/batch-start commit together | 409 `session_conflict` (never raw 500) | 409 |
| Rename active from cockpit | inline edit → `PATCH /api/sessions/{sessionId}` | Name updates; `session.active`/Historial reflect it; cockpit strip shows new name | 422 invalid name → inline error; `session_not_found` → strip clears |
| Show active session | snapshot/`session.active` carries `session_name` + gate | Strip shows name (or created-at fallback) + gate chip + "En curso" | N/A |
| No active session | `sessionId === null` | Strip hidden (empty-state "tu primer lote crea una" still applies) | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/api/sessions.py` -- `continue_session` (sessions.py:269) is the exact template for the new route.
- `backend/app/db/repos/capture_sessions.py` -- `create_active` + `get_active` reused as-is (no change).
- `backend/app/services/batches.py:110` `active_session_data` -- the shared snapshot/`session.active` slice to extend.
- `frontend/lib/ws.ts` -- WS store + reducers; session fields survive the idle reset.
- `frontend/components/sessions/active-session-card.tsx` -- NEW cockpit strip.
- `frontend/app/(client)/page.tsx` -- cockpit column host.
- `backend/tests/test_sessions.py` -- existing continue tests to parallel.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/services/batches.py` -- extended `active_session_data` with `session_name`/`session_gate_name`/`session_gate_value` (DISTINCT keys, not `gate_name`/`gate_value`, to avoid the snapshot spread colliding with the live batch's top-level gate; null in the no-active branch).
- [x] `backend/app/api/sessions.py` -- added `POST /api/sessions/new`: `get_active` (404 if none), live-batch guard (`batch_live`), `create_active(tenant, active.gate_value, active.gate_name)`, commit, `IntegrityError`→`session_conflict`, emit `session.active`, return `SessionOut`.
- [x] `frontend/lib/ws.ts` -- threaded `sessionName`/`sessionGate*` through store, IDLE, payload types, and the snapshot/`session.active`/`batch.state`-idle reducers; added `renameActiveSession` REST-confirmed local seed (PATCH emits no WS event).
- [x] `frontend/components/sessions/active-session-card.tsx` -- new card: name (or "Sesión sin nombre") + gate chip + "En curso"; inline rename (`PATCH`, mirrored validation); "Nueva sesión" (`POST /api/sessions/new`) with a confirm; hidden when `sessionId === null`. Refinement: only "Nueva sesión" is disabled mid-batch (backend 409s it); rename stays allowed (backend rename is unguarded — legacy parity).
- [x] `frontend/app/(client)/page.tsx` -- mounted `ActiveSessionCard` under the ring in the cockpit column.
- [x] `backend/tests/test_sessions.py` -- new endpoint tests (happy fork + empty emit, independent dedup, no-active 404, batch-live 409); fixed the existing continue/idle-snapshot assertions for the 3 new payload keys.

**Acceptance Criteria:**
- Given an active session and no live batch, when the user clicks "Nueva sesión", then a fresh empty session on the same gate becomes active, the old one becomes closed (and appears with "Continuar" in Historial), and every tab's cockpit/panels rebind via `session.active`.
- Given an active session, when the user renames it from the cockpit strip, then the new name shows immediately in the strip and in Historial.
- Given any state, when a session is active, then the cockpit shows its name + gate; when none is active, the strip is absent.
- Given a live/paused/stopping batch, when the user attempts "Nueva sesión", then it is blocked with the `batch_live` copy and no session changes.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest tests/test_sessions.py` -- expected: all pass, including new `POST /api/sessions/new` cases.
- `cd frontend && npm run lint` -- expected: no errors.
- `cd frontend && npm run build` -- expected: type-checks clean (new ws fields + card compile).

**Manual checks:**
- Logged in as a client: send a batch on gate A, let it finish, click "Nueva sesión" → cockpit panels clear, Historial shows the prior session as "Cerrada" with a "Continuar" button. Rename from the cockpit → name updates in both places.

## Suggested Review Order

**The new capability (start here)**

- Entry point — the whole cockpit feature in one component.
  [`active-session-card.tsx:50`](../../frontend/components/sessions/active-session-card.tsx#L50)
- The "Nueva sesión" backend primitive — forks the active gate, mirrors `continue_session`.
  [`sessions.py:325`](../../backend/app/api/sessions.py#L325)

**Session identity through the WS layer**

- Backend payload gains distinct `session_name`/`session_gate_*` (no collision with the batch gate).
  [`batches.py:146`](../../backend/app/services/batches.py#L146)
- Store field + reducers thread it; idle reset preserves it.
  [`ws.ts:85`](../../frontend/lib/ws.ts#L85)
- The trickiest reducer path — `adopting` picks up the batch's gate on a null→bound session.
  [`ws.ts:421`](../../frontend/lib/ws.ts#L421)
- `session.active` (continue + new) rebinds identity in every tab.
  [`ws.ts:526`](../../frontend/lib/ws.ts#L526)

**Concurrency**

- `get_active(for_update=True)` serializes Nueva-sesión against concurrent continue/batch-start.
  [`capture_sessions.py:20`](../../backend/app/db/repos/capture_sessions.py#L20)

**Supporting**

- Mount under the ring in the cockpit column.
  [`page.tsx:76`](<../../frontend/app/(client)/page.tsx#L76>)
- Endpoint tests: fork+empty emit, independent dedup, no-active 404, batch-live 409.
  [`test_sessions.py:555`](../../backend/tests/test_sessions.py#L555)
