---
title: 'Fix Envío cockpit layout — control overflow + runaway page scroll'
type: 'bugfix'
created: '2026-06-15'
status: 'done'
baseline_commit: '3d229c3da75c850ed3433d3eafc887873fcd06f7'
context:
  - '{project-root}/CLAUDE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** On the Envío cockpit, while a batch is live the **Pausar/Detener** buttons overflow their "Controles" card (broken layout), and the whole page scrolls far past the viewport as if it had infinite content — the surface reads as a misconfigured container instead of a contained dashboard.

**Approach:** Two isolated frontend fixes, both scoped to the Envío surface only. (1) Make the control buttons share the row as equal-width flex columns instead of two `w-full` buttons that fight a hard `shrink-0`. (2) Bound the desktop cockpit grid to the viewport height so the page itself does not scroll; the left cockpit column and the right result panels each scroll internally (a true two-pane app shell).

## Boundaries & Constraints

**Always:**
- Keep all behavior changes desktop-gated (`lg:`). On phone/tablet (`< lg`) the page keeps normal document flow scroll with the fixed bottom nav — do not bound height there.
- Fix the button overflow at the call site (`batch-controls.tsx`), not in the shared `Btn` primitive — `Btn`'s `shrink-0` is correct for its other (nav/toolbar) callers.
- Preserve the exact control state machine: sending → Pausar+Detener · paused → Reanudar+Detener · waiting → Detener only · stopping → frozen/disabled · idle → nothing. Variants, icons, disabled logic, single-tap WS-confirmed behavior all unchanged.
- Preserve the live-state-driven rendering and the existing card order in the cockpit column.

**Ask First:**
- If a non-magic-number height is required. Current plan caps the grid with `lg:h-[calc(100dvh-…)]` (an approximate chrome offset); per-column internal scroll absorbs ±error, so the approximation is acceptable and does NOT need a redesign of the shared layout.

**Never:**
- Do NOT edit the shared `app/(client)/layout.tsx` — it also wraps Historial, which must keep normal scroll.
- Do NOT edit `components/sessions/response-views.tsx` — `ResponseColumns`/`COLUMN_LIST` are also used by the Historial detail page; its per-panel `max-h` internal scroll stays as-is.
- Do NOT touch the legacy root app (`app.py`/`core.py`/`static/`).
- Do NOT change WS store, REST calls, or any batch-control semantics.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Two controls (sending/paused) | state `sending` or `paused` | Pausar+Detener (or Reanudar+Detener) sit side by side, each ~50%, fully inside the card with the `gap` between them | N/A |
| One control (waiting) | state `waiting` | Detener alone fills the row, inside the card | N/A |
| Tall cockpit column, desktop | many cards + send form taller than viewport, `lg` | Left column scrolls internally; the page (document) does not grow past the viewport | N/A |
| Result panels, desktop | many captured rows, `lg` | Right panels stay within the bounded grid and scroll internally; no second page-level scrollbar | N/A |
| Mobile/tablet | `< lg` | Normal full-page scroll preserved; bottom nav still fixed; no height cap applied | N/A |

</frozen-after-approval>

## Code Map

- `frontend/components/batch/batch-controls.tsx` -- the overflowing row (`flex gap-2.5` with `<Btn full>` ×2). Bug #1 lives here.
- `frontend/components/ui/btn.tsx` -- base `Btn`: `full` → `w-full`, base class hard-codes `shrink-0` (read-only context; do not edit).
- `frontend/app/(client)/page.tsx` -- the cockpit grid (`lg:grid-cols-[320px_minmax(0,1fr)] lg:items-start`) with a `lg:sticky` left column. Bug #2 lives here.
- `frontend/app/(client)/layout.tsx` -- shared chrome (read-only context; explains the ~chrome height; must NOT change).
- `frontend/components/sessions/response-views.tsx` -- `COLUMN_LIST` per-panel internal scroll (read-only context; shared with Historial; must NOT change).

## Tasks & Acceptance

**Execution:**
- [x] `frontend/components/batch/batch-controls.tsx` -- replaced `full` on the three control `<Btn>`s with `className="flex-1"` so each takes `flex:1 1 0%` (basis-0, equal share) and the pair fits the card. The `null` waiting slot stays; the lone Detener still fills via `flex-1`.
- [x] `frontend/app/(client)/page.tsx` -- bounded the cockpit grid on desktop: added `lg:h-[calc(100dvh-7.5rem)] lg:overflow-hidden` to the grid; dropped `lg:items-start`. Left column scrolls internally (`lg:h-full lg:min-h-0 lg:overflow-y-auto rx-scroll lg:pr-1`, removed `lg:sticky lg:top-6`). Right column contained (`lg:h-full lg:min-h-0 lg:overflow-hidden`) with its inner `hidden lg:block` wrapper at `lg:h-full`. No mobile changes.

**Acceptance Criteria:**
- Given a live batch on a desktop viewport, when the controls render, then Pausar and Detener (or Reanudar and Detener) sit fully inside the "Controles" card, equal width, with the gap between them and nothing clipped or overflowing.
- Given a desktop viewport, when the cockpit column or the result panels have more content than fits, then each scrolls within its own pane and the page itself does not develop a long runaway scrollbar.
- Given a `< lg` viewport, when the page renders, then it scrolls as a normal document with the fixed bottom nav and no height cap — unchanged from today.

## Design Notes

The button overflow is a flexbox-basis bug: `full` emits `width:100%` (≈ `flex-basis:100%`), and the base `Btn` adds `flex-shrink:0`, so two side-by-side buttons each claim 100% and refuse to shrink → 200% → overflow. `flex-1` (`flex:1 1 0%`) sets basis to 0, so even with the inherited `shrink-0` the buttons grow from 0 to share the row equally — no overflow.

The "infinite scroll" is a height-mismatch: the `lg:sticky` left column can exceed the viewport (ring + cards + send form), so the document grows to its height while the right panels independently cap their own internal scroll — two competing scroll surfaces. Bounding the grid to ~viewport height and giving each column its own internal overflow turns it into a single contained app-shell. The `100dvh - 7.5rem` offset is approximate (sticky header ~3.3rem + `main` `pt-6`/`pb-10`); per-column internal scroll absorbs any small error, so an exact pixel value is not required.

## Verification

**Commands:**
- `cd frontend && npm run lint` -- expected: no errors.
- `cd frontend && npm run build` -- expected: type-checks clean (Tailwind class changes only; no type surface touched).

**Manual checks:**
- Desktop: start a batch (or force `state=sending`) → Pausar/Detener fit the card; scroll → page stays put, left column and panels scroll on their own.
- Resize below `lg` → page scrolls normally, bottom nav fixed, controls stack full-width as before.

## Suggested Review Order

**Runaway page scroll (bug #2)**

- Entry point — the grid is capped to the viewport on `lg` and clips, so the page itself never grows.
  [`page.tsx:113`](../../../frontend/app/(client)/page.tsx#L113)
- The left cockpit column now scrolls internally (replaces `lg:sticky`) — the real source of the runaway scroll.
  [`page.tsx:118`](../../../frontend/app/(client)/page.tsx#L118)

**Control overflow (bug #1)**

- Three controls switch from `full` (two `w-full` → 200% overflow) to `flex-1` (basis-0, equal share).
  [`batch-controls.tsx:63`](../../../frontend/components/batch/batch-controls.tsx#L63)

**Review-driven robustness (kept response-views untouched)**

- Right column stays uncapped `min-w-0`; panels self-cap via response-views' own scroll — identical to Historial/admin, so no shared-file edit.
  [`page.tsx:152`](../../../frontend/app/(client)/page.tsx#L152)
