---
title: 'Discreet app version badge'
type: 'feature'
created: '2026-06-23'
status: 'done'
route: 'one-shot'
---

# Discreet app version badge

## Intent

**Problem:** The app shipped no visible version, so neither the owner nor clients could tell which build/channel (alfa/beta/stable) they were looking at.

**Approach:** Single source of truth = `frontend/package.json` `version` (semver with channel suffix, e.g. `1.0.0-alfa`). `next.config.mjs` inlines it as `NEXT_PUBLIC_APP_VERSION`; a tiny non-interactive `VersionBadge` mounted in the root layout renders `v{version}` in the bottom-right on every page. Change the channel by editing one field.

## Suggested Review Order

1. [`frontend/package.json`](../../frontend/package.json) — the version value itself (`1.0.0-alfa`); the only edit needed to rev a channel.
2. [`frontend/next.config.mjs`](../../frontend/next.config.mjs) — how that value reaches the client: `createRequire` reads package.json, `env: { NEXT_PUBLIC_APP_VERSION }` inlines it at build.
3. [`frontend/components/ui/version-badge.tsx`](../../frontend/components/ui/version-badge.tsx) — the stamp: `aria-hidden` + `pointer-events-none`, `z-30` (under modals), `bottom-16 lg:bottom-2` (clears the mobile cockpit nav).
4. [`frontend/app/layout.tsx`](../../frontend/app/layout.tsx) — mount point inside `<Providers>` so it appears on every route.
