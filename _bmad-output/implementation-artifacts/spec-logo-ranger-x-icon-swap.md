---
title: 'Swap Ranger-X square icon across favicon, navbar Mark & PWA'
type: 'chore'
created: '2026-06-30'
status: 'done'
route: 'one-shot'
---

# Swap Ranger-X square icon across favicon, navbar Mark & PWA

## Intent

**Problem:** The new Ranger-X square tile (silver "RX" + "RANGER X" on a purple-bordered black tile) needs to be the icon everywhere the app shows a square brand mark: browser tab favicon, the navbar `Mark`, and the installed PWA/home-screen icon.

**Approach:** Pure asset regeneration — no code. Resize the source tile with `sips` into every square slot the app already references (paths unchanged, so every surface picks it up automatically): `ranger-x-mark.png` @512 (navbar/admin/landing/footer/login Mark + PWA 512), `favicon-192.png`, `favicon-180.png` (apple-touch), `favicon-32.png`, and a stdlib-built PNG-in-ICO `favicon.ico` (16/32/48). The horizontal `Wordmark` and `Logo` lockup are left untouched — the tile has no wide form.

## Suggested Review Order

1. `frontend/public/brand/ranger-x-mark.png` — the 512 tile; drives the navbar `Mark` app-wide + PWA 512 icon. Confirm it reads as intended at ~30px in the nav.
2. `frontend/public/favicon.ico` + `frontend/public/brand/favicon-32.png` — browser-tab favicon (small-size legibility check).
3. `frontend/public/brand/favicon-192.png` + `favicon-180.png` — PWA install icon + iOS apple-touch. Note: changing manifest icon bytes triggers an Android WebAPK re-mint on next update check (non-breaking); browsers/optimizer cache the old bytes by URL until the deploy `npm run build` + a hard refresh.
4. `frontend/components/ui/logo.tsx` — unchanged; verify the `Mark`'s `rounded-[22%] ring-white/25` framing still looks right over the tile's own purple border (mild double-rounding at the corners).
