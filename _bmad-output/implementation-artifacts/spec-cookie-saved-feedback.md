---
title: 'Cookie-saved success feedback in the gate cookie vault'
type: 'feature'
created: '2026-06-19'
status: 'done'
route: 'one-shot'
---

# Cookie-saved success feedback in the gate cookie vault

## Intent

**Problem:** Storing a cookie in the Amazon-gate cookie vault (`CookieManager`) gave no confirmation — the success handler only cleared the form, so the client could not tell whether the save landed.

**Approach:** On a successful store, show an inline `<Notice status="success">` reading "Cookie guardada correctamente." — mirroring the existing `claim-key.tsx` success idiom (persists until the next submit). The banner is cleared at the start of every submit and when a delete begins, so it never contradicts the visible state. The message is a hardcoded literal: the raw credential is never interpolated.

## Suggested Review Order

- The success state + the store-time confirmation (also covers the backend's idempotent re-POST dedup).
  [`cookie-manager.tsx:63`](../../frontend/components/batch/cookie-manager.tsx#L63)
- The success `Notice` render (status="success" → `role="status"` polite live region).
  [`cookie-manager.tsx:98`](../../frontend/components/batch/cookie-manager.tsx#L98)
- Staleness guard: a row delete clears the lingering "guardada" banner (review patch — this component, unlike the cited idiom, has a sibling destructive action).
  [`cookie-manager.tsx:185`](../../frontend/components/batch/cookie-manager.tsx#L185)
- The cited idiom this change mirrors (persist-until-next-submit inline success).
  [`claim-key.tsx:67`](../../frontend/components/keys/claim-key.tsx#L67)
