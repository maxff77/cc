---
title: 'Limpiar button always visible on mobile cockpit tabs'
type: 'bugfix'
created: '2026-06-25'
status: 'done'
route: 'one-shot'
---

# Limpiar button always visible on mobile cockpit tabs

## Intent

**Problem:** In the mobile cockpit (`ResponseTabs`), the "Limpiar" button — which clears all three live panels at once — only rendered inside the "Completa" tab. An operator watching the "Aprobadas" or "Datos CC" tab had no way to clear without first switching back to Completa. (Desktop `ResponseColumns` already shows it permanently in the always-visible Completa column.)

**Approach:** Hoist the `ClearButton` out of the in-tab `CompletaPanel` and render it once above the tab strip in `ResponseTabs`, gated on `onClear` so the read-only admin view (which passes no `onClear`) stays button-free. The in-tab `CompletaPanel` drops its `onClear`/`clearDisabled` props to avoid a duplicate.

## Suggested Review Order

1. [`../../frontend/components/sessions/response-views.tsx`](../../frontend/components/sessions/response-views.tsx) — the change: `ClearButton` hoisted above the tab strip in `ResponseTabs` (always visible when `onClear` present); removed from the in-tab `CompletaPanel`; refreshed prop doc.
2. [`../../frontend/app/app/page.tsx`](../../frontend/app/app/page.tsx) — cockpit caller; confirm `onClear` + `clearDisabled={allPanelsEmpty}` still wire through unchanged.
3. [`../../frontend/app/admin/tenants/[id]/page.tsx`](../../frontend/app/admin/tenants/[id]/page.tsx) — admin read-only caller; confirm it passes neither prop, so no button renders.
