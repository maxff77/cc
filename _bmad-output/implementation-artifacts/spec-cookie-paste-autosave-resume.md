---
title: 'Cookie vault: paste auto-saves, closes the modal, and resumes a stalled send'
type: 'feature'
created: '2026-06-25'
status: 'done'
baseline_commit: 'd028904c581405e188fc28ceb5f162ef2523ba01'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Clients almost always paste a single Amazon cookie at a time, but the vault makes them paste, then click "Guardar cookie", then close the modal, and — when a live send stalled on `cookies_exhausted` — also click "Reanudar". Four steps for the one common case.

**Approach:** Treat a paste into the cookie field as the whole action: auto-save it, close the modal on success, and if the live batch is paused for `cookies_exhausted`, auto-resume it. Typing + the explicit "Guardar cookie" button keep working unchanged for the rare manual/multi-cookie case.

## Boundaries & Constraints

**Always:** Auto-save fires only on a real paste (native `onPaste` — covers Ctrl/Cmd+V, long-press, right-click — plus the existing "Pegar" clipboard button), never on keystroke typing. The backend stays authoritative: a rejected cookie (`invalid_cookie`, `cookie_limit_reached`) shows its error in the field and does NOT close the modal or resume. Auto-resume targets ONLY a batch in `state === "paused"` with `pauseReason === "cookies_exhausted"`, via the existing `POST /api/batches/{id}/resume`; the resulting `batch.state` WS event stays the single source of truth (no optimistic clear). The cookie value is a sensitive credential — keep the no-log / masked-row guarantees intact.

**Ask First:** Extending auto-resume to any other pause reason (e.g. a manual pause or `verdict_timeout`).

**Never:** Auto-saving on plain typing. Splitting a multi-line paste into several cookies (one paste = one stored value, unchanged). Adding a new dependency or a debounce/timer. Touching backend/send-worker logic.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Paste in modal, idle | Non-empty cookie pasted, no live batch | Cookie saved, modal closes | — |
| Paste in modal, stalled send | Pasted while `paused`/`cookies_exhausted` | Saved, modal closes, batch resumes | — |
| Paste in exhausted notice | Pasted into inline manager during `cookies_exhausted` | Saved, batch resumes (no modal to close) | — |
| Re-paste same cookie | Value already stored (idempotent 200) | Treated as success: closes/resumes | — |
| Invalid / over-cap paste | Backend 4xx (`invalid_cookie`/`cookie_limit_reached`) | Modal stays open, error under field, no resume | Show backend Spanish copy |
| Empty / whitespace paste | Clipboard blank | No save, no close; native paste proceeds | Ignored silently |
| Paste while a save is in flight | `add.isPending` | Ignored (no double-submit) | — |

</frozen-after-approval>

## Code Map

- `frontend/components/ui/field.tsx` -- shared text input; needs an optional `onPaste` passthrough (does not forward it today)
- `frontend/components/batch/cookie-manager.tsx` -- the vault form; owns save logic, "Pegar" button, the cookie `Field`. Add paste→save + an `onSaved` callback
- `frontend/components/batch/cookie-modal.tsx` -- wraps `CookieManager`; forward `onSaved` through to its `onClose`
- `frontend/components/batch/send-form.tsx` -- owns the modal open-state + `live`; wires modal `onSaved` to close + conditional resume
- `frontend/components/batch/cookies-exhausted-notice.tsx` -- inline `CookieManager` + manual Reanudar; wire `onSaved` to its existing resume mutation
- `frontend/lib/ws.ts` -- `LiveBatchState` (`state`, `batchId`, `pauseReason`) read for the resume guard (reference only)

## Tasks & Acceptance

**Execution:**
- [x] `frontend/components/ui/field.tsx` -- add optional `onPaste?: React.ClipboardEventHandler<HTMLInputElement>` to `FieldProps` and pass it to the `<input>` -- so the cookie field can observe native pastes
- [x] `frontend/components/batch/cookie-manager.tsx` -- extract a `saveValue(v: string)` from `onSubmit` (same validate→`add.mutate` path); call it from a new `onPaste` handler (read `e.clipboardData.getData("text")`, trim, skip if empty, `e.preventDefault()` + `setValue` so the field shows it) and from the "Pegar" button after it fills; add optional `onSaved?: () => void` prop and invoke it inside `add.mutate`'s `onSuccess` (after clearing the field). Keep the `add.isPending` guard
- [x] `frontend/components/batch/cookie-modal.tsx` -- accept optional `onSaved?: () => void`; pass it to `CookieManager`
- [x] `frontend/components/batch/send-form.tsx` -- pass `onSaved` to `CookieModal`: close the modal (`setCookieModalGateId(null)`) and, when `live.state === "paused" && live.pauseReason === "cookies_exhausted" && live.batchId != null`, `POST /api/batches/{live.batchId}/resume` (fire-and-forget mutation; `batch.state` clears the pause)
- [x] `frontend/components/batch/cookies-exhausted-notice.tsx` -- pass `onSaved={() => resume.mutate()}` to its inline `CookieManager` (reuse the existing `resume` mutation; keep the manual Reanudar button)

**Acceptance Criteria:**
- Given the cookie modal is open and no batch is live, when the client pastes a valid cookie, then it is saved and the modal closes with no further clicks.
- Given a live batch paused on `cookies_exhausted`, when the client pastes a valid cookie (in the modal or the exhausted notice), then the cookie is saved and the batch resumes automatically.
- Given the client types a cookie character-by-character (no paste), when they stop typing, then nothing is saved until they press Enter or "Guardar cookie".
- Given a paste the backend rejects, when the error returns, then the modal stays open, the field shows the Spanish error, and no resume fires.

## Verification

**Commands:**
- `cd frontend && npm run build` -- expected: type-checks + builds clean (the real gate; lint alone misses type errors)
- `cd frontend && npm run lint` -- expected: no new warnings

**Manual checks:**
- Dev cockpit, cookie-mode gate: open Cookies modal → Ctrl/Cmd+V a cookie → saves + modal closes. Repeat mid-send while paused on `cookies_exhausted` → batch resumes. Type a value by hand → no auto-save until "Guardar cookie" (and that manual save leaves the modal open).

## Suggested Review Order

**Paste = the action (auto-save)**

- Entry point: one save path; `fromPaste` decides whether the host acts (close/resume) or just stores.
  [`cookie-manager.tsx:74`](../../frontend/components/batch/cookie-manager.tsx#L74)

- A native paste saves the cookie immediately — the only path that fires the host callback.
  [`cookie-manager.tsx:135`](../../frontend/components/batch/cookie-manager.tsx#L135)

- `onSaved` fires only on the paste flow; manual "Guardar cookie" saves but leaves the modal open.
  [`cookie-manager.tsx:103`](../../frontend/components/batch/cookie-manager.tsx#L103)

**Close + resume orchestration (the host decides)**

- Modal save → close the modal, and resume only when paused on `cookies_exhausted` (fully guarded).
  [`send-form.tsx:252`](../../frontend/components/batch/send-form.tsx#L252)

- The fire-and-forget resume mutation; the resulting `batch.state` WS event clears the pause.
  [`send-form.tsx:243`](../../frontend/components/batch/send-form.tsx#L243)

- Inline exhausted-notice mirrors the auto-resume, guarded like its own Reanudar button.
  [`cookies-exhausted-notice.tsx:78`](../../frontend/components/batch/cookies-exhausted-notice.tsx#L78)

- Modal forwards `onSaved` straight through to its host.
  [`cookie-modal.tsx:81`](../../frontend/components/batch/cookie-modal.tsx#L81)

**Shared plumbing**

- Optional `onPaste` passthrough on the shared Field (undefined elsewhere → no regression).
  [`field.tsx:111`](../../frontend/components/ui/field.tsx#L111)
