---
title: 'Cookie vault as a modal — available during send, with paste + visible input'
type: 'feature'
created: '2026-06-25'
status: 'done'
baseline_commit: 'f2c8004f51ca40909ca8c5b8f1508a382a09d5d0'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The cookie-vault manager (`CookieManager`) only mounts as an inline `SectionCard` while the surface is **idle** (`send-form.tsx:452` — `!isLive && pickedGate?.cookie_mode`), so a client cannot top up cookies mid-send except when the worker hard-stalls (`cookies_exhausted`). It also stacks vertically in the cockpit column, crowding the mobile flow, the secret input is `type="password"` (hidden while typing/pasting), and it lacks the one-tap "Pegar" the líneas box has.

**Approach:** Replace the inline render with a compact **"Cookies (N)" trigger button** shown whenever the active gate (picked-while-idle OR live) is cookie-mode, opening `CookieManager` inside a lightweight **modal** (reusing the `KeyModal` backdrop pattern). Add a "Pegar" button to the cookie field and switch that field to plain visible `text`.

## Boundaries & Constraints

**Always:** Reuse the existing `KeyModal` backdrop idiom (backdrop click + Escape close, `role="dialog"`); resolve the gate id from `effectiveGate` (already live-aware in `send-form.tsx`); list rows keep showing only the backend `masked_value`; the backend stays authoritative on `invalid_cookie`/`cookie_limit_reached`/`gate_not_cookie_mode`; `useListCookies`' `refetchOnMount: "always"` must survive (re-open refetches a list the engine may have purged).

**Ask First:** —

**Never:** Touch backend (`api/cookies.py`, repos, schema) — frontend-only. Do not echo a raw stored value anywhere (the input field reflects only what the user just typed; saved rows stay masked). Do not change the `cookies-exhausted-notice` inline-vault flow (it keeps inlining `CookieManager` and inherits the field/paste changes for free). Do not add a new dependency or a HeroUI Modal primitive — the codebase hand-rolls modals.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Idle, cookie-mode gate picked | gate selected | "Cookies (N)" button visible; click → modal with form + list | N/A |
| Live (sending/paused/stopping) on cookie-mode gate | append surface locked | Button still visible (id from `effectiveGate`); modal opens; can add cookies mid-batch | N/A |
| Non-cookie-mode gate, or no gate picked | — | No button, no modal | N/A |
| Click "Pegar" in cookie field | clipboard has text | Field is FILLED with clipboard text (replaces field; review then Guardar) | Clipboard absent/denied → no-op, manual paste still works |
| Live gate not yet resolved from catalog | catalog still loading | Button hidden until `effectiveGate` resolves (same as exhausted-notice idiom) | N/A |

</frozen-after-approval>

## Code Map

- `frontend/components/batch/cookie-manager.tsx` -- the vault form+list `SectionCard`; change input `type` and add a "Pegar" button to the cookie field.
- `frontend/components/batch/cookie-modal.tsx` -- NEW; `KeyModal`-style backdrop wrapping `<CookieManager>`, scrollable on mobile.
- `frontend/components/batch/send-form.tsx` -- remove the bottom inline render; add the trigger button (gated on `effectiveGate?.cookie_mode`) + modal open state + count via `useListCookies`.
- `frontend/components/keys/key-modal.tsx` -- reference backdrop/Escape/close pattern (do not edit).
- `frontend/lib/cookies.ts` -- `useListCookies` for the button's count badge (already deduped via TanStack).

## Tasks & Acceptance

**Execution:**
- [x] `frontend/components/batch/cookie-manager.tsx` -- dropped `type="password"` on the cookie `Field` (now visible text); added a "Pegar" button (copy icon) in a header row above the field, `pasteCookie()` best-effort `navigator.clipboard?.readText()` → `setValue` + clear error. List rows stay `masked_value`.
- [x] `frontend/components/batch/cookie-modal.tsx` -- NEW `CookieModal({ gateId, open, onClose })`: `KeyModal` backdrop (fixed inset-0, backdrop click + Escape close, X, initial input focus), `<CookieManager>` in a `max-h-[85vh] overflow-y-auto` panel; mounts nothing when `!open`.
- [x] `frontend/components/batch/send-form.tsx` -- removed the inline `{!isLive && pickedGate?.cookie_mode && <CookieManager/>}` block (and the now-unused `pickedGate`); added `cookiesOpen` state, `cookieGate = effectiveGate?.cookie_mode ? effectiveGate : null`, `useListCookies` count; render a full-width "Cookies (N)" `Btn` (key icon) under the gate block when `cookieGate`; render `<CookieModal>`.

**Acceptance Criteria:**
- Given a cookie-mode gate is the active gate (idle-picked or live), when I look at the send form, then a "Cookies (N)" button is shown and opens a modal with the full add-form + masked list.
- Given a live (sending/paused) cookie-mode batch, when I open the modal and add a cookie, then it is stored against the live gate without leaving/altering the running batch.
- Given the cookie field, when I type or paste a value, then the characters are visible (plain text), and "Pegar" fills the field from the clipboard.
- Given I reopen the modal after the engine purged dead cookies during a send, then the list refetches (no stale purged rows).
- Given a non-cookie-mode gate or no gate picked, then no cookie button or modal appears.

## Design Notes

`effectiveGate` (`send-form.tsx:169`) already resolves to the live gate (by `display_value`) when `isLive`, else the picked gate — both carry `cookie_mode?` and `id`. That single source drives the button condition for idle AND live, removing the old idle-only restriction with no new live-state plumbing. The modal reuses `CookieManager` wholesale; its `SectionCard` chrome reads fine as the dialog body. `cookies-exhausted-notice.tsx` is intentionally untouched — same component, inherits the visible-input + Pegar improvements.

## Verification

**Commands:**
- `cd frontend && npm run build` -- expected: tsc passes (the real gate; lint alone misses type errors).
- `cd frontend && npm run lint` -- expected: clean.

**Manual checks:**
- Dev (`npm run dev`): pick a cookie-mode gate → "Cookies (N)" appears → modal opens, field is visible text, Pegar fills it, Guardar adds a masked row. Start a batch on that gate → button still present mid-send → add a cookie → succeeds. Resize to mobile width → cockpit column no longer carries the vault inline.

## Suggested Review Order

**Gating + lifecycle (design intent)**

- Entry point: live-aware gate resolution — `cookieGate` drives the button idle AND mid-send.
  [`send-form.tsx:176`](../../frontend/components/batch/send-form.tsx#L176)

- The load-bearing review fix: modal renders off a gate id CAPTURED at open, not the volatile live `cookieGate` (kills unmount-mid-paste / auto-reopen / wrong-gate-on-Guardar).
  [`send-form.tsx:183`](../../frontend/components/batch/send-form.tsx#L183)

- Trigger captures the id; badge shows `…` while the count query is pending.
  [`send-form.tsx:426`](../../frontend/components/batch/send-form.tsx#L426)

- Modal mounts off the captured id (`open` + `gateId` + `onClose`).
  [`send-form.tsx:479`](../../frontend/components/batch/send-form.tsx#L479)

**The modal**

- Focus-once effect keyed on `[open]` ONLY — avoids the per-WS-frame caret steal during a live send.
  [`cookie-modal.tsx:33`](../../frontend/components/batch/cookie-modal.tsx#L33)

- KeyModal-style backdrop + scrollable panel wrapping the unchanged `CookieManager`.
  [`cookie-modal.tsx:50`](../../frontend/components/batch/cookie-modal.tsx#L50)

**Field changes (visible input + paste + credential hygiene)**

- Visible text + `autoComplete="off"` + `spellCheck={false}` — keeps the plaintext credential out of autofill/remote spellcheck.
  [`cookie-manager.tsx:144`](../../frontend/components/batch/cookie-manager.tsx#L144)

- `pasteCookie()` — best-effort clipboard fill (mirrors líneas).
  [`cookie-manager.tsx:50`](../../frontend/components/batch/cookie-manager.tsx#L50)

- Supporting: `spellCheck` passthrough added to the shared `Field`.
  [`field.tsx:101`](../../frontend/components/ui/field.tsx#L101)
