---
title: 'Cockpit /app overflow:hidden (both axes, all breakpoints)'
type: 'chore'
created: '2026-06-18'
status: 'done'
route: 'one-shot'
---

# Cockpit /app overflow:hidden (both axes, all breakpoints)

## Intent

**Problem:** The Envío cockpit at `/app` needed `overflow: hidden` (both axes) on its surface to contain overflow scroll. The grid wrapper only had `lg:overflow-hidden` (desktop-only); below `lg` it had none.

**Approach:** Promote `lg:overflow-hidden` → unconditional `overflow-hidden` on the cockpit page root grid (`frontend/app/app/page.tsx`). Scoped to `/app` only — NOT the shared `client-shell` `<main>` (which also wraps `/app/sessions` Historial), so the history list keeps its scroll. Desktop behavior byte-for-byte unchanged (already had the cap + clip); change only adds clipping below `lg`, where the grid is auto-height and clips nothing in flow.

## Suggested Review Order

- The only change: `lg:overflow-hidden` → `overflow-hidden` on the cockpit root grid; desktop unaffected, mobile now also clips.
  [`page.tsx:117`](../../frontend/app/app/page.tsx#L117)
