---
name: Ranger-X Check
description: Operator's control room for a multi-tenant Telegram forwarder — calm dark plates, energy only where live state lives.
colors:
  # === Brand identity (NORMATIVE — the Ranger-X rebrand) ===
  accent-from: "oklch(58% 0.22 295)"        # Brand Violet — gradient start
  accent-to: "oklch(78% 0.13 195)"          # Brand Cyan — gradient end
  accent: "oklch(60% 0.19 292)"             # Solid Violet — focus, selection, links, state-accent
  accent-foreground: "oklch(99.11% 0 0)"    # text/icon on a solid accent fill
  # === LEGACY — do not extend; migrate to the brand accent above ===
  legacy-blue: "oklch(55.00% 0.12 243)"     # the old single accent the rebrand escapes
  # === Neutrals (as-built, dark theme = canonical default) ===
  background: "oklch(12.00% 0.0026 243)"    # app body, behind every plate
  surface: "oklch(21.03% 0.0051 243)"       # plate face — cards, panels
  surface-secondary: "oklch(25.70% 0.0038 243)"  # chips, inset fields, badges
  surface-tertiary: "oklch(27.21% 0.0038 243)"   # muted pills, deepest inset
  foreground: "oklch(99.11% 0.0026 243)"    # primary text
  muted: "oklch(70.50% 0.0051 243)"         # legends, secondary text, indices
  border: "oklch(28.00% 0.0026 243)"        # plate edge, 1px hairline
  separator: "oklch(25.00% 0.0026 243)"     # data-row dividers
  field-background: "oklch(21.03% 0.0051 243)"   # input/textarea face
  field-placeholder: "oklch(70.50% 0.0051 243)"  # held to body contrast, not muted gray
  # === Semantics (as-built — one meaning per tone) ===
  success: "oklch(73.29% 0.1945 149.51)"    # ✅ captured-ok, CC count, "nueva"
  danger: "oklch(59.40% 0.1977 23.33)"      # ❌ rejected, stop/destructive
  warning: "oklch(82.03% 0.1395 75.04)"     # paused / waiting / flood
typography:
  readout:
    fontFamily: "Fira Code, ui-monospace, monospace"
    fontSize: "26px"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.03em"
  title:
    fontFamily: "Public Sans, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Public Sans, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  data:
    fontFamily: "Fira Code, ui-monospace, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "normal"
  label:
    fontFamily: "Public Sans, system-ui, sans-serif"
    fontSize: "10px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.1em"
rounded:
  sm: "0.25rem"     # 4px — the system radius: plates, fields, chips, badges
  md: "0.375rem"    # 6px — count/"nueva" tags only
  full: "9999px"    # state pills ONLY
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"        # plate gutter (p-3)
  lg: "16px"        # inter-control gap (gap-4)
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-foreground}"
    rounded: "{rounded.sm}"
    padding: "8px 16px"
  section-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.sm}"
    padding: "12px"
  input-field:
    backgroundColor: "{colors.field-background}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
  state-pill:
    backgroundColor: "{colors.surface-tertiary}"
    textColor: "{colors.muted}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
  mono-chip:
    backgroundColor: "{colors.surface-secondary}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.sm}"
    padding: "2px 6px"
---

# Design System: Ranger-X Check

## 1. Overview

**Creative North Star: "The Control Room"**

Ranger-X Check is an operator's console for a live system, not a brochure and not a toy. Clients fire batches and watch them drain; owner/admins keep one shared Telegram account alive across every tenant. Both surfaces are designed to the same bar: calm dark plates that recede, legible state that never lies, and a single charge of brand color reserved for the moments that matter. The interface should disappear into the task — the operator reads state at a glance and trusts what they see.

The system is built like rack-mounted equipment. Panels are flat machined plates (`bg-surface`) with a 1px hairline edge and a legend *engraved over the top border*, not floated above it. State reads like panel instrumentation: LED-style pills, a progress ring that switches stroke color with the run, monospace readouts with tabular figures so digits never jitter. Depth is tonal, never lifted — there are no shadows. Energy is punctuation: the Ranger-X **violet→cyan** gradient appears on the mark, the primary action, and a live pulse, surrounded by restraint everywhere else. It is never wallpaper.

This system explicitly rejects four things. It is **not** a crypto/pump dashboard — no exchange neons, no garish glow. It is **not** generic blue SaaS — the product shipped exactly that (a single hue-243 blue accent) and the rebrand exists to escape it; that blue is now legacy. It is **not** loud gamer/esports — no chrome bevels, 3D extrude, or circuit-trace overlays; we keep the gradient *concept* and drop the chrome. And it is **not** childish — no pastels, no emoji confetti, no bubbly kid-app rounding.

**Key Characteristics:**
- Control-room calm by default; brand energy only in live moments.
- Flat plates, tonal depth, zero elevation.
- Monospace + tabular figures for every number and identifier.
- Engraved caps legends over plate edges as the signature affordance.
- One meaning per semantic tone; state never conveyed by color alone.
- Dark-first, but both themes are first-class and pass contrast independently.

## 2. Colors

A near-monochrome neutral field (cool gray, chroma ≈ 0 so it reads as true gray) carrying three semantic tones, lit by one brand gradient that earns its rarity.

### Primary
- **Brand Violet → Brand Cyan** (gradient: `oklch(58% 0.22 295)` → `oklch(78% 0.13 195)`): the Ranger-X signature. Reserved for *moments* — the wordmark/logo, the primary `Enviar` action, and live-pulse accents. A linear sweep, violet at the origin, cyan at the destination. **Never** clipped to text, never a section background, never repeated per card.
- **Solid Violet** (`oklch(60% 0.19 292)`): the workhorse accent where a gradient doesn't fit — focus rings, current selection, links, the "sending" state, and the progress ring's active stroke. This is the single solid accent token; everything interactive that isn't a moment uses this.

### Legacy (do not extend)
- **Legacy Blue** (`oklch(55% 0.12 243)`): the original single accent. It is the "generic blue SaaS" the rebrand escapes. Present in code today; treat every occurrence as debt to migrate to Solid Violet. Do not introduce new uses.

### Neutral
The neutral ramp is cool gray at near-zero chroma (hue 243, chroma ≈ 0.003 — effectively hue-agnostic). Dark theme is the canonical default; light values follow in parens.
- **Background** (`oklch(12% …)` / light `oklch(97.02% …)`): the deepest layer, behind every plate.
- **Surface** (`oklch(21.03% …)` / light `oklch(100% …)`): the plate face — every card, panel, field sits here.
- **Surface Secondary / Tertiary** (`oklch(25.7% …)` / `oklch(27.21% …)`): tonal insets — chips, badges, muted pills. Depth is built by stepping these, not by shadow.
- **Foreground** (`oklch(99.11% …)` / light `oklch(21.03% …)`): primary text.
- **Muted** (`oklch(70.5% …)` / light `oklch(55.17% …)`): legends, secondary text, row indices. Held above the 4.5:1 floor; never lighter "for elegance."
- **Border / Separator** (`oklch(28% …)` / `oklch(25% …)`): the 1px plate edge and the data-row divider — the system's two hairlines.

### Semantic
One meaning per tone, paired always with an icon, glyph, or label — never color alone.
- **Success** (`oklch(73.29% 0.1945 149.51)`): ✅ captured-ok, CC counts, the "nueva" highlight (success at 12% alpha).
- **Danger** (`oklch(59.4% 0.1977 23.33)` dark / `oklch(65.32% 0.234 24.44)` light): ❌ rejected replies, stop/destructive actions.
- **Warning** (`oklch(82.03% 0.1395 75.04)` dark / `oklch(78.19% 0.1593 71.03)` light): paused, waiting in the admission queue, flood-wait. On tints use `text-warning` (the `-foreground` token is the near-black for solid fills only).

### Named Rules
**The Moments Rule.** The brand gradient lives on ≤3 surfaces of any screen: the mark, the primary action, a live pulse. If a fourth element wants the gradient, it doesn't get it — it gets Solid Violet or nothing. The rarity is the identity.

**The True-Gray Rule.** Neutrals carry hue but ~zero chroma. Don't "warm" or "cool" the grays toward the brand to feel branded — branding is carried by the accent and the gradient, never by tinting the field.

## 3. Typography

**Body / UI Font:** Public Sans (with system-ui, sans-serif fallback)
**Data / Mono Font:** Fira Code (with ui-monospace, monospace fallback)

**Character:** One humanist sans does all UI work — headings, buttons, labels, prose — tuned tight, never decorative. Every number, identifier, gate value, timestamp, and captured line is set in Fira Code with tabular figures, so digits hold their column and a `0` never reads as `O`. The sans/mono split *is* the type system: chrome is sans, data is mono. There is no display face — this is a tool, not a magazine.

### Hierarchy
- **Readout** (Fira Code, 800, 26px, line-height 1, tracking -0.03em, tabular): the big numeric center of the progress ring (percent, fraction). The one oversized element; reserved for the single most-watched number on screen.
- **Title** (Public Sans, 700, 1.125rem, tracking -0.01em): page titles. The top of the document outline; fixed rem (it sits in a 300px cockpit column and must not fluidly shrink).
- **Body** (Public Sans, 400, 0.875rem, line-height 1.5): prose, helper text, list content. Cap prose at 65–75ch.
- **Data** (Fira Code, 400, 11px, line-height 1.4, tabular): console rows, gate values, captured text, sub-lines. Console density is allowed to run tighter than prose.
- **Label** (Public Sans, 700, 10px, uppercase, tracking 0.1em, color muted): the engraved caps legend — section legends, back links, pill text, metric labels.

### Named Rules
**The One-Tracking Rule.** There is exactly one tracked-caps style: 10px / 700 / uppercase / 0.1em / muted (`LabelCaps`). Every divergent 0.08em / 0.12em copy is wrong and should collapse into it. Caps are visual only — never ship an all-caps *string* (screen readers spell it out); uppercase via CSS.

**The Tabular Rule.** Any digit that updates live (counts, ETAs, percentages, indices) is `tabular-nums` in Fira Code. Proportional figures jitter as values change; in a real-time tool that reads as instability.

## 4. Elevation

Flat by doctrine. There are no shadows anywhere in the system — `box-shadow` is not part of the vocabulary. Depth is built entirely by tonal layering: `background` (12%) → `surface` (21%) → `surface-secondary` (25.7%) → `surface-tertiary` (27.21%) in dark mode, each step ~3–5% lighter than the last. Plates are separated from the field by a single 1px `border` hairline, not a lift. The engraved legend reinforces this: it is masked to sit *across* the plate's top border (background above the 50% line, surface below), so it reads as etched into the edge rather than floating over it.

### Named Rules
**The Flat-Plate Rule.** Surfaces are machined plates, not raised cards. If you reach for a shadow to separate two regions, step the tonal surface or add the 1px hairline instead. A drop shadow anywhere is the tell that this stopped being a control room.

## 5. Components

Built on HeroUI primitives (Button, Select, TextField, ProgressCircle, Alert, Chip) with a thin layer of hand-rolled signature pieces. Affordances are standard, executed precisely — no invented controls for standard jobs.

### Buttons
- **Shape:** 4px radius (`rounded.sm`) — same as every plate and field.
- **Primary:** the `Enviar` action and other commit actions wear the brand violet→cyan gradient (a *moment*); `accent-foreground` text. HeroUI `Button variant="primary"`. Padding ~8px 16px.
- **States:** default → hover (slight gradient brighten) → `focus-visible` (2px Solid Violet ring) → active → disabled (loses gradient, drops to muted surface) → loading (label swaps to "Enviando…", button disabled). Ship all of them; a half-stated button is a bug.
- **Secondary / Ghost:** neutral surface or transparent with a 1px border; text in foreground. Used for non-commit actions (rename, cancel, back).

### State Pill (signature)
- **Style:** the system's **only** full-round shape (`rounded-full`). An alpha tint of a semantic token over its own text color — `bg-accent/22 text-accent`, `bg-warning/18 text-warning`, `bg-danger/18 text-danger`, `bg-surface-tertiary text-muted` — never a hardcoded fill. 10px caps, tracking 0.1em.
- **One tone, one meaning:** accent = live send, warning = paused/waiting, danger = stopping/destructive, muted = closed/inactive.
- **Dot:** optional 1.5px leading dot in `currentColor`; `pulse` (motion-safe animate-pulse) for live, `static` otherwise. The pulse is one of the sanctioned brand "moments."

### Section Card / Rack Plate (signature)
- **Corner Style:** 4px radius. Flat `bg-surface` with a 1px `border` edge. Hand-rolled `<section>`, deliberately **not** HeroUI Card — it needs the overlapping legend and zero elevation.
- **Engraved legend:** a caps label mounted *over* the top border via a vertical split-gradient mask (background above 50%, surface below). It interrupts the edge; it must never paint a darker rectangle onto the plate.
- **Live-state rail:** an optional 2px left border that encodes run state — `border-l-accent` (sending) or `border-l-warning` (paused). This is the **one sanctioned colored left-border** in the system; it is functional instrumentation, never decoration (see Don'ts).
- **Internal padding:** 12px gutter (`p-3`).

### Data Row (signature)
- **Style:** the system's only console-density element. Fira Code 11px, `items-start` so the index/glyph pins to the first line, content **wraps** to show full text (`break-words` for long unbroken tokens), 1px `separator` divider below.
- **Anatomy:** muted index/timestamp at left → wrapping content → optional status glyph at right (✅ success / ❌ danger; Filtrada rows carry none — they are data, not states).
- **New-capture highlight:** newly captured rows wear `bg-success/12` + success text + a small "nueva" tag (`rounded.md`).

### Inputs / Fields
- **Style:** `field-background` face, transparent `field-border`, 4px radius. HeroUI `Select` / `TextField` / `TextArea`. The paste textarea is Fira Code (operators paste data lines).
- **Focus:** 2px Solid Violet ring (`--focus`), visible on every interactive element.
- **Placeholder:** held to body contrast (`field-placeholder`), never muted gray.
- **Error:** `isInvalid` + inline `FieldError` anchored to the guilty field; operation-level failures speak as a danger `Alert` at the top of the plate.
- **Selects:** never free text where a catalog exists — the gate is always a two-step category→gate pick.

### Mono Chip / Count Badge
- **Mono Chip:** `surface-secondary` face, 1px border, Fira Code 11px tabular — for gate values and short machine identifiers (`name · value`).
- **Count Badge:** `surface-secondary` LED-style readout, mono tabular, visible at 0; success tone for live CC counts.

### Progress Ring (signature)
- HeroUI `ProgressCircle` ~128px. Stroke is **Solid Violet while sending, warning while paused/stopping**. Center is a Readout-step percent over a muted fraction. Idle renders the same 128px footprint at 0% with a muted em-dash center — zero layout jump when a batch starts. Exactly three flank metrics (enviadas·en cola / ETA / CC nuevas); no others.

### Notices / Alerts (signature)
The live-system status family — how the app tells an operator something changed account-wide. A tinted strip: 1px semantic border at ~50% alpha over a ~10% fill, 12px text, `role="alert"`, optional action.
- **Tone carries the doctrine.** Amber/warning = *waiting, not broken* (flood-wait, admission queue): "espera, la cuenta está bien." Danger/red = *failure* (watchdog global pause: session lost or reply-rate collapse): a real stop. Never dress a wait in red or a failure in amber — the color is the diagnosis.
- **Action is scoped and real.** A notice only shows a button to the role that can act (e.g. only the owner sees "Reanudar envíos" on the watchdog strip; everyone else reads "Solo el owner puede reanudar"). No dead buttons.
- **Server-truth, never optimistic.** The strip clears on the inbound WS event (`watchdog.resumed`, `flood` expiry), not on the click — every tab clears together.

### Empty State
- **Style:** an invitation *inside the panel it belongs to*, not a full-page void. Centered stack: optional engraved-caps eyebrow → one plain `text-muted` sentence → optional **real** action.
- **Rule:** teach the interface, never "nothing here." The action must do something (start a batch, pick a gate); never a decorative dead button. Idle cockpit shows the em-dash ring + "Pega tus líneas y elige un gate."

### Skeleton (Loading)
- **Style:** row-height bars (`h-4`, 4px radius) shaped like the data panel/table they replace, stacked in the panel's own gutter. **Never** a floating centered spinner over content.
- **Rule:** the skeleton's shape must predict the content — same row rhythm, same count ballpark — so the layout doesn't jump when data lands. 150–250ms transitions; no choreography.

### Navigation
- Client cockpit and admin shell share a top/side nav in Public Sans. Default muted, hover foreground, active in Solid Violet. Standard patterns only — no invented nav affordances.

## 6. Do's and Don'ts

### Do:
- **Do** reserve the violet→cyan gradient for ≤3 moments per screen — the mark, the primary action, a live pulse. Everywhere else is calm neutral. (The Moments Rule.)
- **Do** set every live-updating number in Fira Code `tabular-nums` so digits don't jitter. (The Tabular Rule.)
- **Do** build depth with tonal surface steps and the 1px hairline, never a shadow. (The Flat-Plate Rule.)
- **Do** pair every semantic color with a glyph, icon, or label (✅/❌/state text) — state must survive color-blindness and grayscale.
- **Do** route new accent usage to Solid Violet (`oklch(60% 0.19 292)`); treat any legacy hue-243 blue as debt to migrate.
- **Do** keep both themes passing contrast independently, placeholders at body contrast (4.5:1), and a visible 2px Solid-Violet focus ring on every control.
- **Do** use the engraved caps legend (10px/700/uppercase/0.1em/muted) as the single section-label voice.
- **Do** diagnose with notice color: amber = waiting (the account is fine), red = failure (a real stop). Show the action only to the role that can act.
- **Do** load with shape-faithful row skeletons and teach with real empty states — never a centered spinner, never a "nothing here" void.

### Don't:
- **Don't** clip the gradient to text (`background-clip: text`). The brand gradient lives on fills, the mark, and the pulse — **never** on letters. Emphasis comes from weight and size.
- **Don't** reintroduce the generic blue-SaaS accent (hue-243). It is the exact anti-reference the rebrand escapes; new uses are prohibited.
- **Don't** import crypto/pump aesthetics — exchange neons, "moon" glow, garish glowing financial dashboards.
- **Don't** add gamer/esports chrome — bevels, 3D extrude, circuit-trace overlays, Twitch-overlay energy. Keep the gradient concept, drop the chrome.
- **Don't** go childish — pastels, emoji confetti, bubbly kid-app rounding. The system radius is a restrained 4px.
- **Don't** add a drop shadow anywhere. If two regions need separating, step the tonal surface or add a hairline.
- **Don't** use a colored `border-left` as a decorative stripe on cards, callouts, or list items. The **only** sanctioned 2px left rail is the rack-plate's live-state indicator (accent=sending / warning=paused); it encodes state and nothing else.
- **Don't** wallpaper the gradient across panels, backgrounds, or per-card. Rarity is the point.
- **Don't** introduce a display font or oversized fluid headings. This is a control room; type is functional, scale is fixed-rem.
