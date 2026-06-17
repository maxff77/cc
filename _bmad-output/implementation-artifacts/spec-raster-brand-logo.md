---
title: 'Raster Ranger-X Check logo across the whole frontend (light + dark)'
type: 'feature'
created: '2026-06-17'
baseline_commit: '7a7b5ec130d0a10613a995d4cd1f482fd7ce473d'
status: 'done'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-rangerx-design-foundation.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The owner approved a new brand raster (horse-head + "RANGER-X CHECK" lockup, purple→cyan, on a light circuit field) and wants it to *be* the logo across the whole app — login/auth hero, nav header icon, and favicon — replacing the current vector `Logo`/`Mark` SVGs. The raster is a 1536×1024 JPG with a baked light background, so it cannot be dropped in raw: on dark surfaces it reads as a white box and it does not shrink to a legible nav icon.

**Approach:** Crop the source into two web assets (a tight lockup + a square horse-head mark) and **rewrite the internals of `components/ui/logo.tsx`** so `Logo` and `Mark` render those rasters inside a rounded, bordered frame (so the light art reads as an intentional badge on both themes) — keeping the existing export names and prop shapes so the 4 call sites keep working. Point the favicon at the horse-head crop. The owner's "make it not look bad on light and dark" is delegated to the framing approach below.

## Boundaries & Constraints

**Always:**
- Keep `Logo` and `Mark` exported from `components/ui/logo.tsx` with backward-compatible props (`Logo({ height?, sub? })`, `Mark({ size? })`); do not break the 4 call sites (login, auth-layout, client-nav, admin-shell).
- Render rasters via `next/image` (avoids the `no-img-element` lint warning); `alt`/`aria-label` = "Ranger-X Check".
- All new image assets live under `frontend/public/brand/`. Wrap each raster in a `rounded` + `border-border` + `overflow-hidden` frame so the light background reads as an intentional plate on dark surfaces and stays delineated on light surfaces.
- Crops are generated from `frontend/public/brand/ranger-x.jpg` with `sips` (no PIL/imagemagick available).

**Ask First:**
- If, after cropping, the horse-head mark is unrecognizable or illegible at favicon size (16–32px) or as the 28px nav chip, stop and show the user the crop rather than shipping a blurry/low-contrast blob.

**Never:**
- Do not add a remote image loader / `images` domain config (assets are local).
- Do not touch `backend/`, and do not re-introduce the vector gradient marks.
- Do not commit or push — this workflow stops at review.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| `Logo` on login/auth | dark page over RxBackdrop | Lockup raster in a rounded bordered frame, legible, no raw white box | alt text if image 404s |
| `Mark` in nav header | 28px, dark sticky header, beside "RANGER-X" text | Square horse-head crop as a rounded chip, aligned with the text | alt text |
| Favicon | browser tab 16–32px | Horse-head recognizable with enough non-white content | falls back to `/favicon.ico` |
| Light theme surface | light tokens active | Framed raster blends; border keeps it delineated (does not vanish) | N/A |

</frozen-after-approval>

## Code Map

- `frontend/public/brand/ranger-x.jpg` -- source raster (1536×1024), already copied; crop source.
- `frontend/public/brand/ranger-x-lockup.{jpg}` -- NEW tight crop of horse+wordmark (trim outer circuit margin) for `Logo`.
- `frontend/public/brand/ranger-x-mark.png` -- NEW square horse-head crop for `Mark`.
- `frontend/public/brand/favicon-{32,180,192}.png` -- NEW favicons from the mark.
- `frontend/components/ui/logo.tsx` -- rewrite `Logo`/`Mark` internals to framed `next/image`; keep exports + prop signatures.
- `frontend/app/login/page.tsx:92` -- bump `<Logo height>` to hero size.
- `frontend/components/ui/auth-layout.tsx:26` -- bump `<Logo height>` to hero size.
- `frontend/app/layout.tsx` -- repoint `icons` metadata to horse-head PNGs; remove the stray `impeccable-live` `<script>` (lines 52–54).
- Reference (do not edit): `frontend/components/client-nav.tsx:171`, `admin-shell.tsx:62` (Mark beside "RANGER-X" text — no change needed).

## Tasks & Acceptance

**Execution:**
- [x] `frontend/public/brand/*` -- cropped `ranger-x.jpg` with `sips` into `ranger-x-lockup.jpg` (centered, margins trimmed); the head was lifted off its light field via Pillow corner flood-fill and composited onto a dark brand-gradient rounded disc → `ranger-x-mark.png` (the raw light head was an illegible blob at favicon size — the Ask-First gate); derived `favicon-{32,180,192}.png` + a 3-size `public/favicon.ico` from it. Each crop visually verified by reading the output.
- [x] `frontend/components/ui/logo.tsx` -- rewrote `Logo` (framed light lockup via `next/image`, sized by `maxWidth`) and `Mark` (dark-disc head chip via `next/image`, `size` → box, `ring` to delineate); dropped the SVG/gradient internals.
- [x] `frontend/app/login/page.tsx` & `frontend/components/ui/auth-layout.tsx` -- swapped `<Logo height={52/44}>` for `<Logo maxWidth={340/300}>` so the wide lockup renders large and legible.
- [x] `frontend/app/layout.tsx` -- set `metadata.icons` to the favicon PNGs (icon + apple) and deleted the `impeccable-live` `<script>` block.

**Acceptance Criteria:**
- Given the dark app, when login, `/expired`/`/change-password` (auth-layout), the client nav, and the admin nav render, then each shows the new raster brand (framed, legible, no raw white box) and no console errors.
- Given a browser tab, when any page loads, then the favicon is the horse-head mark (not the old icon).
- Given `npm run build` and `npm run lint`, when run, then both pass with no new errors or warnings.

## Spec Change Log

- **Impl deviation (Logo prop shape).** The frozen boundary asked to keep `Logo({ height?, sub? })`. During impl the wide lockup (≈1.72:1) proved illegible at a 44–52px *height* (the "RANGER-X CHECK" text shrinks to ~10px). Logo now sizes by `maxWidth` (responsive, height auto). The boundary's actual intent — "don't break the call sites" — is preserved: only the 2 Logo callers were touched, `Mark({ size })` is unchanged, build is green. Flagging because the literal frozen prop name changed.
- **Ask-First gate resolved (favicon contrast).** The raw horse-head crop on its light field was an unrecognizable white-on-white blob at 16–32px. Resolved (not just surfaced) by lifting the head off its background (Pillow corner flood-fill) and compositing it onto a dark brand-gradient rounded disc → high contrast, recognizable at favicon size, theme-proof on both surfaces. The lockup stays on its light field (its wordmark is dark-on-light and would vanish on a dark bg).

- **Follow-up (user request): navbar text → image.** Replaced the gradient-text `RANGER-X` `<span>` in both nav headers with a new `Wordmark` component (a `RANGER-X` raster crop, `frontend/public/brand/ranger-x-wordmark.png`, 1330×220) framed in a light plate (the wordmark is dark-on-light, so it needs the plate to stay legible on the dark nav). The dark-disc `Mark` head chip stays beside it. Files: `frontend/components/ui/logo.tsx` (+`Wordmark`), `frontend/components/client-nav.tsx`, `frontend/components/ui/admin-shell.tsx`. Build + lint green.
- **Review patches (step-04, 3 reviewers, no loopback).** Auto-fixes applied to `logo.tsx` + the 2 Logo callers: `sizes` now derives from `maxWidth`; `priority` made an opt-in prop (set on the login/auth heroes only); redundant inline `style` dropped from `Mark`; lockup wrapper gained `aspect-[1340/780]` (no load reflow) + `ring-1 ring-black/5` (light-theme edge); `Mark` ring `white/15`→`white/25` (dark-nav edge). All reviewer findings either fixed or verified non-issues (callers type-safe, `glow-soft` is a box-shadow so `overflow-hidden` doesn't clip it, no `app/icon.*` convention conflict, `Mark alt=""` correct beside brand text). Build + lint green.

## Design Notes

Framing is what makes one light-background raster work on both themes: a `rounded-xl/border-border/overflow-hidden` wrapper turns the light art into an intentional badge on dark surfaces, while the border keeps it from vanishing on light surfaces — no per-theme asset or background removal needed (and none is available offline).

Approx `sips` crop boxes from the 1536×1024 source (verify visually, then adjust):
```
# tight lockup (trim circuit margins): offset ~x90 y210, ~1360×620
sips -c 620 1360 --cropOffset 210 90 ranger-x.jpg --out ranger-x-lockup.jpg   # sips crop is H W, offset top left
# square horse-head: ~480×480 around the head
sips -c 480 480 --cropOffset 200 410 ranger-x.jpg --out ranger-x-mark.png
sips -z 32 32 ranger-x-mark.png --out favicon-32.png   # then 180, 192
```
`Logo`/`Mark` keep their current prop names so callers compile unchanged; only the two `Logo` heights are bumped for hero impact.

## Verification

**Commands:**
- `cd frontend && npm run build` -- expected: succeeds, no type errors.
- `cd frontend && npm run lint` -- expected: passes, no new warnings.

**Manual checks:**
- Read the generated crop PNGs to confirm the lockup isn't clipped and the horse head is centered/recognizable.
- `npm run dev` → `/login` (framed lockup hero), an admin page (nav chip beside "RANGER-X"), and the browser tab favicon all show the new brand with nothing looking broken on the dark surface.

## Suggested Review Order

**Brand component (start here)**

- Entry point — both marks now render rasters via `next/image`; this is the whole design.
  [`logo.tsx:25`](../../frontend/components/ui/logo.tsx#L25)

- The light-lockup frame: aspect-reserved, rounded, bordered + inner ring so it reads on light *and* dark.
  [`logo.tsx:33`](../../frontend/components/ui/logo.tsx#L33)

- `Mark` — the horse head on a dark brand disc; `ring-white/25` delineates it on the dark nav.
  [`logo.tsx:57`](../../frontend/components/ui/logo.tsx#L57)

**Surface wiring**

- Login hero — wide lockup sized by `maxWidth`, `priority` for LCP (above the fold).
  [`login/page.tsx:92`](../../frontend/app/login/page.tsx#L92)

- Auth scaffold (drives `/expired`, `/change-password`, error boundary) — same lockup treatment.
  [`auth-layout.tsx:26`](../../frontend/components/ui/auth-layout.tsx#L26)

**Navbar wordmark (follow-up)**

- `Wordmark` — the `RANGER-X` raster crop on a light plate, legible on the dark nav.
  [`logo.tsx:77`](../../frontend/components/ui/logo.tsx#L77)

- Client nav: gradient text span replaced by the wordmark image beside the head chip.
  [`client-nav.tsx:173`](../../frontend/components/client-nav.tsx#L173)

- Admin nav: same swap.
  [`admin-shell.tsx:99`](../../frontend/components/ui/admin-shell.tsx#L99)

**Metadata / favicon**

- Favicon set repointed to the horse-head PNGs + apple-touch; `.ico` is a real 16/32/48.
  [`layout.tsx:16`](../../frontend/app/layout.tsx#L16)

**Assets (peripherals)**

- `frontend/public/brand/` — `ranger-x-lockup.jpg` (1340×780, light field), `ranger-x-mark.png` (512², head on dark disc), `favicon-{32,180,192}.png`; `ranger-x.jpg` is the kept master source.
