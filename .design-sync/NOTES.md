# design-sync notes — Ranger-X Check

## This is an OFF-SCRIPT sync

The design system's source is **not an npm package**. It's a single-file,
browser-global React prototype:
`_bmad-output/planning-artifacts/ux-designs/ranger-x-handoff/lib.jsx`
(`/* global React */`, `Object.assign(window, {...})`, inline styles + CSS vars,
tokens in the sibling `theme.css`).

To feed the package-shape converter, it's staged into a synthetic package at
`dsbuild/`:

- `dsbuild/src/index.jsx` — `lib.jsx` ESM-ified (import React + named exports
  instead of the `window` assign). **Derived** — regenerate if `lib.jsx` changes.
- `dsbuild/dist/index.d.ts` — **hand-written** prop contract (lib.jsx has no
  types). Update by hand when component props change. NOT regenerable.
- `dsbuild/theme.css` — copy of the handoff `theme.css` with a Google Fonts
  `@import` prepended on line 1 (Saira / JetBrains Mono / Public Sans).
- `dsbuild/build-dist.mjs` — esbuild step (react/react-dom external) → `dist/index.js`.

## Build / re-sync recipe (from repo root)

```sh
# 0. fresh clone only: stage dsbuild deps + the ESM entry
cp -r "<skill-base>"/{package-build,package-validate,package-capture,resync}.mjs "<skill-base>"/lib "<skill-base>"/storybook .ds-sync/
(cd .ds-sync && npm i esbuild ts-morph @types/react playwright && npx playwright install chromium)
(cd dsbuild && npm i react react-dom @types/react esbuild)
# regenerate src/index.jsx from lib.jsx ONLY if the handoff changed (see transform below)

# 1. build the runtime entry
(cd dsbuild && node build-dist.mjs)

# 2. converter — NOTE: --entry is relative to CWD, not the package
node .ds-sync/package-build.mjs --config .design-sync/config.json \
  --node-modules dsbuild/node_modules --entry ./dsbuild/dist/index.js --out ./ds-bundle
node .ds-sync/package-validate.mjs ./ds-bundle

# 3. final driver run before upload (first sync omits --remote; re-sync adds
#    --remote .design-sync/.cache/remote-sync.json after fetching _ds_sync.json)
node .ds-sync/resync.mjs --config .design-sync/config.json \
  --node-modules dsbuild/node_modules --entry ./dsbuild/dist/index.js --out ./ds-bundle
```

ESM transform (only when `lib.jsx` changes): prepend
`import React, { useState, useRef, useEffect } from "react";`, delete the
`const { useState... } = React;` line, replace `Object.assign(window, {...});`
with `export {...};`.

## Decisions / gotchas

- **Dark-principal theme + white preview cards.** The card template forces a white
  body but the DS tokens live on `:root` = dark, so bare text/icons vanished.
  Every `.design-sync/previews/<Name>.tsx` wraps content in a full-bleed dark
  `Frame` (`margin:-24; background:var(--background)`). **Never** fix this with a
  global `#root`/`body` rule in the shipped CSS — it leaks into every design.
- **cardMode: column on all 17** (`cfg.overrides`) — the full-bleed frames + wide
  rows (variant rows, icon grid) overflow narrow grid cells otherwise.
- **Fonts are remote** (`[FONT_REMOTE]`, expected). To self-host: download the
  three woff2 families + add `@font-face`, point `cfg.extraFonts` at it.
- Project: "Ranger-X Design System" → `2cebdc8c-82c0-48ca-95c2-de888afddaa8`
  (pinned in config.json). Separate from Richard's "Lohari HeroUI" project.

## Re-sync risks (watch-list)

- `dsbuild/src/index.jsx` is a **manual** transform of `lib.jsx` — upstream edits
  to the handoff do NOT propagate until you re-run the transform.
- `dsbuild/dist/index.d.ts` is **hand-maintained** — it silently drifts if a
  component's real props change in `lib.jsx`.
- `dsbuild/{node_modules,dist/index.js}` are gitignored & regenerable; the rest of
  `dsbuild/` is committed so a fresh clone can build.
- Fonts load over the network at render time.

## Known render warns

- `[FONT_REMOTE]` for Public Sans / Saira / JetBrains Mono — expected (remote @import), not new.
