---
title: 'Installable PWA (desktop + mobile)'
type: 'feature'
created: '2026-06-30'
status: 'done'
baseline_commit: '2e2bfa24594e9db147c44707e3f3efda001ad08d'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Users can only reach Ranger-X through a browser tab. There is no way to "install" it so it lives as an app icon and opens in its own window on PC (Chrome/Edge) and mobile (Android Chrome, iOS Safari).

**Approach:** Make the existing Next.js app a Progressive Web App — add a web app manifest, app icons, and a minimal service worker. No PWA framework (`next-pwa`/Serwist) and no offline caching: the cockpit is a live WebSocket relay, so offline is useless and a caching SW would only risk serving stale assets. Scope is installability + standalone window only.

## Boundaries & Constraints

**Always:** Zero new npm dependencies — use Next 16's native `app/manifest.ts` and a hand-written SW. The service worker must NOT cache or intercept responses (no `respondWith`); an empty `fetch` handler exists only to satisfy install criteria. Register the SW in production only (a SW in `next dev` fights HMR). The installed app's `start_url` is `/app` (the cockpit = "the app"); existing auth middleware still governs it (logged-out → /login inside the standalone window).

**Ask First:** Adding offline caching, push notifications, or any new dependency. Generating new branded icon art (reuse existing `public/brand/` assets).

**Never:** Do not add `next-pwa`, Serwist, Workbox, or any service-worker build tooling. Do not cache app shell or API/WS traffic. Do not touch the backend. Do not edit the root landing copy, auth flow, or any business logic.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Logged-out manifest fetch | No session cookie, GET `/manifest.webmanifest` | 200 manifest JSON (matcher excludes `.webmanifest`) — NOT a 302 to /login | N/A |
| Chrome/Edge on HTTPS | Manifest + SW + 192/512 icons present | Browser offers "Install"; installs standalone window opening `/app` | N/A |
| iOS Safari Add to Home Screen | `appleWebApp` meta + apple-touch-icon | Icon + name on home screen; opens standalone (no Safari chrome) | N/A |
| Installed app launched logged-out | `start_url` `/app`, no cookie | Middleware routes to `/login` inside the standalone window | N/A |
| SW register in dev | `NODE_ENV !== "production"` | No registration | register() rejection swallowed |

</frozen-after-approval>

## Code Map

- `frontend/app/manifest.ts` -- NEW. Next-native manifest route → serves `/manifest.webmanifest`; Next auto-injects `<link rel="manifest">`.
- `frontend/public/sw.js` -- NEW. Minimal no-op service worker (install/activate/empty-fetch).
- `frontend/app/register-sw.tsx` -- NEW. `"use client"` component; registers `/sw.js` in production on mount.
- `frontend/app/layout.tsx` -- EDIT. Add `appleWebApp` to `metadata`; render `<RegisterSW />`.
- `frontend/middleware.ts` -- EDIT. Add `webmanifest` to the matcher exclusion list so a logged-out manifest fetch is not redirected to /login.
- `frontend/public/brand/ranger-x-mark.png` -- REUSE (already 512×512). The 512 icon; no generation needed.
- `frontend/public/brand/favicon-192.png` -- REUSE (192×192). The 192 icon.
- `frontend/config/site.ts` -- READ. Source of `name`/`description` for the manifest (stay DRY).

## Tasks & Acceptance

**Execution:**
- [x] `frontend/app/manifest.ts` -- Export default `manifest(): MetadataRoute.Manifest` returning: `id`/`start_url` `/app`, `scope` `/`, `display` `standalone`, `background_color`/`theme_color` `#16141d` (dark default shell), `lang` `es`, `name`/`description` from `siteConfig`, `short_name` `"Ranger-X"`, and `icons` = [`/brand/favicon-192.png` 192×192, `/brand/ranger-x-mark.png` 512×512], both `type image/png`, `purpose "any"`.
- [x] `frontend/public/sw.js` -- `install` → `skipWaiting()`; `activate` → `clients.claim()`; `fetch` → empty handler (no `respondWith`, no caching). Header comment names the no-cache intent + the self-unregister upgrade path.
- [x] `frontend/app/register-sw.tsx` -- Client component: in `useEffect`, bail unless `NODE_ENV === "production"` and `"serviceWorker" in navigator`, then `navigator.serviceWorker.register("/sw.js").catch(() => {})`. Returns `null`.
- [x] `frontend/app/layout.tsx` -- Add `appleWebApp: { capable: true, title: siteConfig.shortName, statusBarStyle: "default" }` to `metadata`; import and render `<RegisterSW />` in `<body>`. Do not set `metadata.manifest` (Next injects it from `app/manifest.ts`).
- [x] `frontend/middleware.ts` -- In the `matcher` regex extension alternation, add `webmanifest` (e.g. `...|json|webmanifest|png|...`).

**Acceptance Criteria:**
- Given a logged-out visitor on HTTPS, when they open the browser app menu, then an "Install Ranger-X" action is offered and produces a standalone window whose start page is `/app`.
- Given the installed app is launched while logged out, when it opens, then it lands on `/login` inside the standalone window (not a browser tab).
- Given a logged-out GET to `/manifest.webmanifest`, when middleware runs, then it returns the manifest body (no 302 to /login).
- Given `npm run build`, when it runs, then it succeeds, emits a `/manifest.webmanifest` route, and reports no type errors.

## Spec Change Log

**2026-06-30 — review patches (no loopback):** All three reviewers (blind/edge-case/acceptance) converged on the iOS status bar. `statusBarStyle: "black-translucent"` floated content under the notch (no `viewport-fit=cover`/`safe-area-inset-top` shipped) AND was theme-blind (white glyphs unreadable on the light-theme `#f6f5fa` bg). Patched to `statusBarStyle: "default"` — content sits below the bar, iOS picks readable glyphs in both themes, zero CSS. Also single-sourced the brand short name via new `siteConfig.shortName` (manifest `short_name` + apple `title`), and reworded the `sw.js` comment (the empty fetch handler is cross-engine install insurance, not a hard requirement). Maskable-icon + light-splash items deferred (deferred-work.md). KEEP: inert no-cache SW, `webmanifest` matcher exclusion, `start_url:/app`, zero new deps.

## Design Notes

Why no framework: install only needs a manifest + icons + a registered SW with a fetch handler over HTTPS — all native to Next 16. `next-pwa`/Serwist exist for offline/precaching, which is out of scope and harmful for a real-time app.

The SW is deliberately inert so it cannot brick the site or serve stale JS:

```js
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {}); // satisfies install criteria; intercepts nothing
```

Middleware gotcha: the `matcher` excludes static assets by an explicit extension list (`js|css|json|png|...`) but NOT `.webmanifest`. Without the fix, a logged-out browser fetching `/manifest.webmanifest` gets redirected to `/login`, and the manifest never loads. `/sw.js` is already covered by the `js` exclusion.

## Verification

**Commands:**
- `cd frontend && npm run build` -- expected: success; output lists a `/manifest.webmanifest` route; no TS errors.
- `cd frontend && npm run lint` -- expected: clean.

**Manual checks:**
- `npm run build && npm run start`, open `http://localhost:3000` in Chrome → DevTools ▸ Application ▸ Manifest: no errors, both icons resolve, Installability passes; install → standalone window opens `/app` (→ `/login` if logged out).
- iOS Safari: Share ▸ Add to Home Screen shows the Ranger-X icon + name; launching opens standalone (no Safari chrome).

## Suggested Review Order

**The install contract (start here)**

- Defines installable identity: standalone window, `start_url:/app`, 192/512 icons. The whole feature in one file.
  [`manifest.ts:10`](../../frontend/app/manifest.ts#L10)

**The integration gotcha (highest risk)**

- One-token regex edit: excludes `/manifest.webmanifest` so a logged-out browser isn't 302'd to /login before the manifest loads.
  [`middleware.ts:216`](../../frontend/middleware.ts#L216)

**Service worker — installability without footguns**

- Inert by design: empty fetch handler, no `respondWith`, caches nothing → can't serve stale assets on a live app.
  [`sw.js:14`](../../frontend/public/sw.js#L14)

- Prod-only registration; dev is skipped (SW vs HMR), failure swallowed (progressive enhancement).
  [`register-sw.tsx:13`](../../frontend/app/register-sw.tsx#L13)

**Layout wiring (iOS + mount)**

- iOS install metadata; `statusBarStyle:"default"` keeps content below the bar, readable in both themes (review patch).
  [`layout.tsx:32`](../../frontend/app/layout.tsx#L32)

- Mounts the SW registrar in the tree.
  [`layout.tsx:68`](../../frontend/app/layout.tsx#L68)

**Peripheral**

- Single-sources the short brand for manifest `short_name` + iOS title (no drift).
  [`site.ts:11`](../../frontend/config/site.ts#L11)
