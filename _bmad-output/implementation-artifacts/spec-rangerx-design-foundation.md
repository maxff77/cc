---
title: 'Ranger-X neon design substrate (tokens, fonts, backdrop, brand marks)'
type: 'feature'
created: '2026-06-13'
status: 'done'
baseline_commit: 'a381cfb7737c1fe9f7481cb2bb11669d920c7619'
context:
  - '{project-root}/_bmad-output/planning-artifacts/ux-designs/ranger-x-handoff/theme.css'
  - '{project-root}/_bmad-output/planning-artifacts/ux-designs/ranger-x-handoff/lib.jsx'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The frontend wears a restrained HeroUI "sobria" violet→cyan theme. The owner approved the louder **Ranger-X Check neon identity** (Claude Design handoff): cyan→violet→magenta spectrum, neon glow, circuit backdrop, gradient-clipped wordmark, Saira display font, 12px radius. Forcing it onto HeroUI looked broken. The whole frontend must move to it screen by screen — but each screen first needs a shared neon substrate.

**Approach:** Ship the **substrate only** — one app-wide reskin of the token layer. Port the handoff tokens into `globals.css` (retune values, never rename — HeroUI maps token *names* to utilities), wire Saira + JetBrains Mono fonts, add the base CSS utilities/keyframes + circuit `RxBackdrop`, and add the brand `Logo`/`Mark` SVGs. Retuning token **values** re-skins every HeroUI component + existing primitive at once, touching no screen markup. Per-screen reskins and the native form primitives (Btn/Field/Select/Checkbox) are **deferred** (`deferred-work.md`).

## Boundaries & Constraints

**Always:**
- Retune token **values**, keep every existing token **name** (`--background --accent --surface --field-* --success --warning --danger --muted --border --separator --focus --radius --field-radius` …) — renaming breaks HeroUI's `@theme inline` utility mapping.
- Port **both** dark and light token sets; app stays hardcoded `dark` (toggle deferred). New Ranger-X tokens (`--cyan --blue --magenta --faint --border-strong --accent-soft --brand-gradient --brand-gradient-soft --circuit --glow --density` + design radii) go in the `:root`/`.dark`/`.light` blocks *after* the `@heroui/styles` import so they win by source order.
- SVG marks use `style={{ fontFamily: "var(--font-display)" }}` (literal `"Saira"` won't resolve — `next/font` hashes the name). Keep `--glow:1`/`--density:1` static. Update `layout.tsx` `themeColor` hexes to the neon `--background`.

**Ask First:**
- If retuning a token value visibly breaks a HeroUI component (e.g. contrast/legibility regression on Button/Input/Alert/Select), stop and confirm the value rather than guessing.

**Never:**
- Do not modify any screen/page or existing component markup, and do not migrate the ~44 direct HeroUI `<Button>/<Input>/<Select>/<TextArea>` call sites — those belong to the deferred per-screen goals.
- Do not create the native form primitives (Btn/Field/Area/Select/Checkbox) here — deferred.
- Do not add a theme/accent toggle, Tweaks panel, or `data-accent` JS here — deferred.
- Do not remove the `.gradient-moment` class (4 live callers); redefine it onto `--brand-gradient` for back-compat.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Existing HeroUI screen renders | dark theme, retuned tokens | Same layout/markup, now neon palette + 12px radius + Saira/JetBrains fonts; no broken/invisible elements | N/A |
| `Logo` / `Mark` rendered | any surface, light or dark | Gradient cyan→violet→magenta fill, Saira wordmark, glow scales with `--glow`; legible on both themes | N/A |
| `RxBackdrop` mounted | fixed, `z-index:0`, `pointer-events:none` | Circuit grid + corner neon bloom behind content; never intercepts clicks | N/A |
| `--glow` set to 0 | tunable override | All neon glows collapse to 0 intensity; surfaces stay legible | N/A |

</frozen-after-approval>

## Code Map

- `frontend/styles/globals.css` -- the token layer + base utilities; the single highest-leverage file (retune values, add Ranger-X tokens, add `.rx-backdrop/.gradient-text/.brand-fill/.glow-accent/.glow-soft/.legend-mask/.rx-scroll/.rx-focus/.font-display` + keyframes `rx-pulse/rx-dash/rx-fade-up`+`.rx-enter`+`::selection`; add `--font-display` to `@theme`; redefine `.gradient-moment`).
- `frontend/config/fonts.ts` -- `next/font/google` declarations; add `fontDisplay` (Saira) + swap mono Fira→JetBrains.
- `frontend/app/layout.tsx` -- apply `fontDisplay.variable` on `<body>`; fix `themeColor` hexes; remove the `impeccable-live` `<script>`.
- `frontend/components/ui/logo.tsx` -- NEW: `Logo` (gradient SVG wordmark) + `Mark` (shield-X) — port from `ranger-x-handoff/lib.jsx`.
- `frontend/components/ui/rx-backdrop.tsx` -- NEW: `RxBackdrop` (fixed circuit/bloom backdrop).
- `frontend/.gitignore` (or repo `.gitignore`) -- ignore `frontend/.impeccable/` (local preview tool).
- Reference (do not edit): `_bmad-output/planning-artifacts/ux-designs/ranger-x-handoff/{theme.css,lib.jsx}`.

## Tasks & Acceptance

**Execution:**
- [x] `git` hygiene (Task 0) -- stash `frontend/app/login/page.tsx` (the superseded sobria WIP), `git checkout -- .impeccable/live/config.json`, and add `frontend/.impeccable/` to `.gitignore` -- clean tree before the substrate lands; keep the `site.ts` rename.
- [x] `frontend/styles/globals.css` -- retune dark+light HeroUI token values to the `theme.css` neon palette, add the new Ranger-X tokens + base utilities/keyframes, set `--radius: 0.75rem`/`--field-radius: 0.5625rem`, add `--font-display` to `@theme`, redefine `.gradient-moment` onto `--brand-gradient` -- the app-wide reskin.
- [x] `frontend/config/fonts.ts` -- add `fontDisplay` (Saira, weights 700/800, italic+normal, `--font-display`) and swap mono to JetBrains Mono (`--font-jetbrains-mono`) -- typography identity.
- [x] `frontend/app/layout.tsx` -- add `fontDisplay.variable` to `<body>`, update `themeColor` light/dark hexes to the neon `--background`, delete the `impeccable-live` script block -- wire fonts + metadata.
- [x] `frontend/components/ui/logo.tsx` -- NEW `Logo`+`Mark` ported from handoff `lib.jsx`, font via `var(--font-display)`, glow via `var(--glow)` -- brand marks for screens to consume.
- [x] `frontend/components/ui/rx-backdrop.tsx` -- NEW `RxBackdrop` ambient backdrop component -- reusable page chrome.

**Acceptance Criteria:**
- Given the dark app, when any existing HeroUI page (e.g. `/login`) renders, then it shows the neon palette, 12px radius, and Saira/JetBrains fonts with no broken, invisible, or unreadable elements and no console errors.
- Given a light surface, when `Logo` and `Mark` render, then the gradient fill and wordmark are legible on both themes and the glow scales with `--glow` (0 = no glow).
- Given `RxBackdrop` is mounted, when a user clicks through it, then it never intercepts pointer events and sits behind all content.
- Given `npm run build` and `npm run lint`, when run, then both pass.

## Design Notes

Token merge rule: HeroUI's `@theme inline` derives `--radius-*`/`--*-soft` from base tokens; because `globals.css` declares its blocks *after* the import, explicit Ranger-X values override the derived ones — keep names, change values, add new tokens freely. Golden source for every value/utility/SVG is `ranger-x-handoff/theme.css` + `lib.jsx` — port faithfully (the `Logo` lightning slash + italic Saira wordmark + cyan→blue→accent→magenta gradient + glow filter), swapping only the literal `fontFamily="Saira"` for `var(--font-display)`.

## Verification

**Commands:**
- `cd frontend && npm run build` -- expected: build succeeds, no type errors.
- `cd frontend && npm run lint` -- expected: passes.
- `cd frontend && npm run dev` then open `/login` -- expected: neon dark palette, rounded fields, Saira heading; no console errors.

**Manual checks:**
- Toggle `:root { --glow: 0 }` in devtools -- all glows vanish, surfaces stay legible (proves glow is wired to the multiplier, not hardcoded).

## Suggested Review Order

**Token substrate (start here)**

- Entry point — the keep-names / retune-values merge rule that makes the whole app-wide reskin safe.
  [`globals.css:5`](../../frontend/styles/globals.css#L5)

- The principal (dark) neon palette: every HeroUI token name kept, values retuned + Ranger-X tokens added.
  [`globals.css:107`](../../frontend/styles/globals.css#L107)

- `.gradient-moment` re-aliased to the 4-stop brand gradient — upgrades its 4 existing callers for free.
  [`globals.css:197`](../../frontend/styles/globals.css#L197)

**Typography**

- Saira display loaded under a DISTINCT `--font-saira` var (a self-ref would break the chain).
  [`fonts.ts:27`](../../frontend/config/fonts.ts#L27)

- `@theme` maps the `font-display` utility ← `var(--font-saira)`; mono swapped to JetBrains.
  [`globals.css:178`](../../frontend/styles/globals.css#L178)

- Body wires `fontDisplay.variable` so descendants resolve `var(--font-display)`.
  [`layout.tsx:46`](../../frontend/app/layout.tsx#L46)

**Base utilities + brand marks**

- Circuit-grid + corner-bloom backdrop; bloom scales with `--glow`.
  [`globals.css:206`](../../frontend/styles/globals.css#L206)

- Hardened reduced-motion reset (kills transitions + infinite-loop strobing).
  [`globals.css:256`](../../frontend/styles/globals.css#L256)

- `Logo`/`Mark` SVG — SSR-safe `useId()` ids, gradient fill, font via `var(--font-display)`.
  [`logo.tsx:19`](../../frontend/components/ui/logo.tsx#L19)

- `RxBackdrop` — `pointer-events:none`, behind all content.
  [`rx-backdrop.tsx:8`](../../frontend/components/ui/rx-backdrop.tsx#L8)

**Metadata / config**

- `themeColor` hexes resynced to the neon `--background` (a meta tag can't read a CSS var).
  [`layout.tsx:24`](../../frontend/app/layout.tsx#L24)

- Brand rename `cc` → `Ranger-X Check`.
  [`site.ts:4`](../../frontend/config/site.ts#L4)
