---
name: cc
description: Cabina de datos (trimmed cockpit) for the cc Telegram forwarding SaaS. HeroUI v3 theme layer — dark mode default, light mode supported. This file specifies the delta on top of HeroUI v3; the palette is fixed verbatim by imports/heroui-theme.css.
status: final
updated: 2026-06-10
sources:
  - imports/heroui-theme.css
  - mockups/direction-cabina-refinada.html
  - .decision-log.md
colors:
  # FIXED palette — oklch values verbatim from imports/heroui-theme.css.
  # `token` = light mode, `token-dark` = dark mode (dark is the DEFAULT surface).
  # Tokens not listed here (focus, overlay, scrollbar, segment, default, etc.)
  # inherit verbatim from imports/heroui-theme.css — do not restate or alter them.
  accent: 'oklch(55.00% 0.1200 243.00)'
  accent-dark: 'oklch(55.00% 0.1200 243.00)'
  accent-foreground: 'oklch(99.11% 0 0)'
  accent-foreground-dark: 'oklch(99.11% 0 0)'
  background: 'oklch(97.02% 0.0026 243.00)'
  background-dark: 'oklch(12.00% 0.0026 243.00)'
  border: 'oklch(90.00% 0.0026 243.00)'
  border-dark: 'oklch(28.00% 0.0026 243.00)'
  danger: 'oklch(65.32% 0.2340 24.44)'
  danger-dark: 'oklch(59.40% 0.1977 23.33)'
  danger-foreground: 'oklch(99.11% 0 0)'
  danger-foreground-dark: 'oklch(99.11% 0 0)'
  field-background: 'oklch(100.00% 0.0013 243.00)'
  field-background-dark: 'oklch(21.03% 0.0051 243.00)'
  field-placeholder: 'oklch(55.17% 0.0051 243.00)'
  field-placeholder-dark: 'oklch(70.50% 0.0051 243.00)'
  foreground: 'oklch(21.03% 0.0026 243.00)'
  foreground-dark: 'oklch(99.11% 0.0026 243.00)'
  muted: 'oklch(55.17% 0.0051 243.00)'
  muted-dark: 'oklch(70.50% 0.0051 243.00)'
  separator: 'oklch(92.00% 0.0026 243.00)'
  separator-dark: 'oklch(25.00% 0.0026 243.00)'
  success: 'oklch(73.29% 0.1945 149.51)'
  success-dark: 'oklch(73.29% 0.1945 149.51)'
  success-foreground: 'oklch(21.03% 0.0059 149.51)'
  success-foreground-dark: 'oklch(21.03% 0.0059 149.51)'
  surface: 'oklch(100.00% 0.0013 243.00)'
  surface-dark: 'oklch(21.03% 0.0051 243.00)'
  surface-secondary: 'oklch(95.24% 0.0020 243.00)'
  surface-secondary-dark: 'oklch(25.70% 0.0038 243.00)'
  surface-tertiary: 'oklch(93.73% 0.0020 243.00)'
  surface-tertiary-dark: 'oklch(27.21% 0.0038 243.00)'
  warning: 'oklch(78.19% 0.1593 71.03)'
  warning-dark: 'oklch(82.03% 0.1395 75.04)'
  warning-foreground: 'oklch(21.03% 0.0059 71.03)'
  warning-foreground-dark: 'oklch(21.03% 0.0059 75.04)'
typography:
  # UI text is Public Sans (--font-sans per theme import). Monospace is confined
  # to DATA: numerals, CC rows, timestamps, prefijo, session ids.
  body:
    fontFamily: 'Public Sans'
    fontSize: 14px
    fontWeight: '400'
    lineHeight: '1.5'
  heading:
    fontFamily: 'Public Sans'
    fontSize: 15px
    fontWeight: '700'
    letterSpacing: -0.01em
  label-caps:
    fontFamily: 'Public Sans'
    fontSize: 10px
    fontWeight: '700'
    letterSpacing: 0.1em
  data-mono:
    fontFamily: 'ui-monospace, SF Mono, Cascadia Mono, Menlo, monospace'
    fontSize: 11px
    fontWeight: '400'
    lineHeight: '1.4'
  metric:
    fontFamily: 'ui-monospace, SF Mono, Cascadia Mono, Menlo, monospace'
    fontSize: 18px
    fontWeight: '800'
    letterSpacing: -0.01em
  metric-lg:
    fontFamily: 'ui-monospace, SF Mono, Cascadia Mono, Menlo, monospace'
    fontSize: 26px
    fontWeight: '800'
    letterSpacing: -0.03em
rounded:
  # Fixed by theme import: --radius and --field-radius are 0.25rem.
  DEFAULT: 0.25rem
  field: 0.25rem
  full: 9999px
spacing:
  '1': 4px
  '2': 8px
  '3': 12px
  '4': 16px
  gutter: 14px
  margin-mobile: 14px
  margin-desktop: 20px
components:
  progress-ring:
    primitive: 'HeroUI CircularProgress'
    color-sending: '{colors.accent}'
    color-paused: '{colors.warning}'
    track: '{colors.surface-tertiary}'
    center-pct: '{typography.metric-lg}'
    center-fraction: '{typography.data-mono}'
  state-pill:
    primitive: 'HeroUI Chip'
    radius: '{rounded.full}'
    sending-bg: 'oklch(55% 0.12 243 / .22)'
    paused-bg: 'oklch(78.19% 0.1593 71.03 / .18)'
    paused-bg-dark: 'oklch(82.03% 0.1395 75.04 / .18)'
  prefijo-chip:
    primitive: 'HeroUI Chip'
    font: '{typography.data-mono}'
    background: '{colors.surface-secondary}'
    border: '{colors.border}'
    radius: '{rounded.DEFAULT}'
  data-row:
    primitive: 'HeroUI Listbox/Table row'
    font: '{typography.data-mono}'
    divider: '{colors.separator}'
    new-highlight: 'oklch(73.29% 0.1945 149.51 / .12)'
    new-text: '{colors.success}'
  dual-view-tabs:
    primitive: 'HeroUI Tabs'
    active-bg: '{colors.surface-tertiary}'
    count-badge-filtrada: '{colors.success}'
    radius: '{rounded.DEFAULT}'
  control-button:
    primitive: 'HeroUI Button'
    pausar-text: '{colors.warning}'
    detener-text: '{colors.danger}'
    reanudar-bg: '{colors.success}'
    reanudar-text: '{colors.success-foreground}'
    radius: '{rounded.DEFAULT}'
  flood-notice:
    primitive: 'HeroUI Alert/Chip'
    background: 'oklch(78.19% 0.1593 71.03 / .12)'
    background-dark: 'oklch(82.03% 0.1395 75.04 / .12)'
    border: 'oklch(78.19% 0.1593 71.03 / .5)'
    border-dark: 'oklch(82.03% 0.1395 75.04 / .5)'
    countdown-font: '{typography.data-mono}'
  bottom-nav:
    primitive: 'HeroUI Tabs (bottom placement)'
    active-bg: '{colors.surface-tertiary}'
    live-dot: '{colors.success}'
  field:
    primitive: 'HeroUI Input/Textarea/Select'
    background: '{colors.field-background}'
    placeholder: '{colors.field-placeholder}'
    radius: '{rounded.field}'
---

# cc — Design Spine

> Peer contract: **DESIGN.md owns how it looks; EXPERIENCE.md owns how it works. Spines win over mocks** — `mockups/direction-cabina-refinada.html` is the confirmed visual reference, HeroUI v3 primitives win on implementation.

## Brand & Style

**Cabina de datos, recortada** (trimmed cockpit). The product is an operator's instrument for a paid service: one progress ring, the three numbers that matter (enviadas · en cola, ETA, CC nuevas), and dense monospace data rows — nothing else. No decorative blocks, no filler statistics, "sin info innecesaria o exagerada": every number on screen earns its place by answering an operator question.

The system is **HeroUI v3** end to end. This file specifies only the delta on top of HeroUI defaults; the palette, radius, and font are fixed verbatim by `imports/heroui-theme.css` (user-supplied — do not reinterpret). **Dark mode is the default**; the light theme exists and is fully defined by the same import. The console-density register (monospace, hairline separators, compact rows) is confined to data rows; the chrome around them stays calm HeroUI surfaces.

Explicitly NOT the brand: the existing `static/index.html` UI. Its functionality carries over; its visual patterns do not.

→ Visual reference: `mockups/direction-cabina-refinada.html` (confirmed direction: mobile Envío sending/paused, Historial, desktop Envío). `imports/heroui-theme.css` is the canonical token source.

## Colors

The palette is a near-monochrome blue-grey field with four functional chromatics. Each color has exactly one meaning:

- **Accent blue (`{colors.accent}`, same in both modes)** — the live send. Progress ring while `sending`, primary action (Enviar), active nav, brand mark. Never decorative.
- **Warning amber (`{colors.warning}` / `{colors.warning-dark}`)** — *paused and waiting, not broken*. Ring while `paused`, the Pausar control, the FloodWait notice, ETA-while-paused. FloodWait is informational, so it wears amber, never red.
- **Success green (`{colors.success}`, same in both modes)** — captured value. CC nuevas counter, Filtrada count badge, new-row highlight, ✅ status, live dot, the Reanudar button fill.
- **Danger red (`{colors.danger}` / `{colors.danger-dark}`)** — destructive or failed. Detener, eliminar sesión, ❌ status, real errors. Never used for FloodWait or pause.
- **Surfaces (`{colors.background}` → `{colors.surface}` → `{colors.surface-secondary}` → `{colors.surface-tertiary}`)** — tonal layering ladder; depth is tone, not shadow. `{colors.border}` outlines containers at 1px; `{colors.separator}` divides data rows at lower contrast.
- **Text (`{colors.foreground}` primary, `{colors.muted}` secondary)** — muted carries labels, timestamps, and metadata.

Unlisted tokens (focus, overlay, scrollbar, segment, default) inherit from `imports/heroui-theme.css` unchanged. Avoid: gradients (the only conic-gradient lives inside the progress ring), new chromatics, color-coding prefijos.

## Typography

**Public Sans** is the UI voice (load the font; the theme maps `--font-sans` to it). **Monospace is data-only**: CC rows, timestamps, counters, ETA digits, prefijo chips, session ids — set in `{typography.data-mono}` / `{typography.metric}` / `{typography.metric-lg}`. If it's a sentence, it's Public Sans; if the eye needs to scan or compare it, it's mono.

Ramp: `{typography.heading}` for surface titles and session names; `{typography.body}` for prose and controls; `{typography.label-caps}` for the small tracked-uppercase metric labels ("ENVIADAS · EN COLA", "ETA", "CC NUEVAS") and section headers; `{typography.metric-lg}` only inside the ring. No display sizes beyond the ring percentage.

## Layout & Spacing

Mobile-first single column (~390px reference frame): header strip → ring block → controls → dual-view panel filling remaining height → bottom nav. Side margins `{spacing.margin-mobile}`; gaps between major blocks 10px; inside-card padding `{spacing.gutter}`.

Desktop (~1100px): top header bar (brand mark, batch title, nav Envío | Historial, state pill) over a 3-column grid `300px 1fr 1fr` — ring + metrics + controls left, **Completa and Filtrada panels side by side**. Historial detail reuses the same dual panels.

The data panel is the flexible element: it absorbs leftover vertical space and scrolls internally; the cockpit (ring, controls) never scrolls away on mobile while a batch is live.

## Elevation & Depth

No shadow hierarchy. Containers are `{colors.surface}` on `{colors.background}` with a 1px `{colors.border}` outline; nested emphasis steps up the surface ladder (`{colors.surface-secondary}`, `{colors.surface-tertiary}`). Data rows separate with 1px `{colors.separator}` only. HeroUI overlay/modal shadows stay at HeroUI defaults — add nothing on top.

## Shapes

`{rounded.DEFAULT}` (0.25rem, fixed by the theme import) on everything: cards, buttons, fields, chips, tabs, badges. The single exception is the state pill at `{rounded.full}`. The progress ring is the only circle. Sharp-ish corners read "instrument", which is the point.

## Components

HeroUI v3 components are the primitives; mocks are reference only. Per-component visual deltas:

- **Progress ring** — HeroUI `CircularProgress`, ~128px on mobile. Stroke `{colors.accent}` while sending, `{colors.warning}` while paused; track `{colors.surface-tertiary}`. Center: percentage in `{typography.metric-lg}` + fraction `34 / 120` in `{typography.data-mono}` muted. The ring's flank shows exactly three metrics as `{typography.label-caps}` label + `{typography.metric}` value: enviadas · en cola, ETA, CC nuevas (in `{colors.success}`). **No other stats.**
- **State pill** — HeroUI `Chip`, `{rounded.full}`, uppercase tracked 10px. Enviando = accent-tint bg; En pausa = amber-tint bg (values in `{components.state-pill}`).
- **Prefijo chip** — HeroUI `Chip`, `{typography.data-mono}`, `{colors.surface-secondary}` bg, 1px `{colors.border}`. Shows the active prefijo (e.g. `.zo`) verbatim with its dot.
- **Control buttons** — HeroUI `Button`/`ButtonGroup`, full-width pair under the ring on mobile. Pausar: `{colors.surface-secondary}` bg with `{colors.warning}` text; Detener: `{colors.surface-secondary}` bg with `{colors.danger}` text; Reanudar: solid `{colors.success}` fill with `{colors.success-foreground}` text — the only solid-filled control, because resuming is the moment that matters.
- **Dual-view tabs (Completa | Filtrada)** — HeroUI `Tabs`, segmented; active tab `{colors.surface-tertiary}` bg + 1px `{colors.border}`. Each tab carries a mono count badge — Filtrada's count in `{colors.success}`. The export action (`↓ .txt`) sits in the same strip. On desktop the tabs become two side-by-side panels with `{typography.label-caps}` headers (COMPLETA / FILTRADA) and a footer export link each.
- **Data row** — the only console-density element. `{typography.data-mono}` 11px, 1px `{colors.separator}` dividers, timestamp/index muted at left, content ellipsized, status glyph at right (✅ `{colors.success}` / ❌ `{colors.danger}`). Newly captured rows: `{components.data-row.new-highlight}` background + `{colors.success}` text, with a small "nueva" tag.
- **FloodWait notice** — HeroUI `Alert`-style strip: amber-tint bg, amber 50%-alpha border, 12px `{typography.body}` text with the countdown in mono amber (`{components.flood-notice}`). Informational tone, never danger styling.
- **Bottom nav (mobile)** — two items, **Envío | Historial**. Active item `{colors.surface-tertiary}` bg; Envío carries a 6px live dot (`{colors.success}` while sending, `{colors.warning}` while paused). On desktop the same two items move into the top header as inline nav.
- **Session row (Historial)** — HeroUI `Listbox`/`Card` row: friendly name in `{typography.heading}`, mono sub-line `prefijo · session-id` in `{colors.muted}`, right-aligned badge ("En curso" accent-tint / "Cerrada" `{colors.surface-tertiary}` muted). Selected row: `{colors.accent}` border.
- **Fields (Textarea / Select / Input)** — HeroUI defaults on `{colors.field-background}` with `{colors.field-placeholder}`, `{rounded.field}`. The prefijo selector is a HeroUI `Select` over the catalog — never a free-text input.
- **Modal / Table (admin, confirmations)** — HeroUI `Modal` and `Table` at defaults; admin surfaces reuse the same surface ladder and mono-for-data rule, no separate admin theme. [ASSUMPTION: admin surfaces are not in the confirmed mock; they inherit this system by extension.]

## Do's and Don'ts

| Do | Don't |
|---|---|
| Exactly three metrics beside the ring (enviadas · en cola, ETA, CC nuevas) | Filler stats, vanity counters, "info innecesaria o exagerada" |
| HeroUI v3 primitives with these token deltas | Hand-rolled widgets imitating the mock pixel-for-pixel |
| Palette verbatim from `imports/heroui-theme.css` | New colors, gradients, per-prefijo color coding |
| Mono for data, Public Sans for everything else | Mono body text, sans-serif CC rows |
| Amber for pause/FloodWait, red only for destructive/failed | Styling FloodWait as an error |
| Tonal layering + 1px borders for depth | Shadow stacks, glows, elevation hierarchy |
| Dark mode default, light fully supported | Dark-only hardcoding |
| Carry over the old UI's functionality | Inheriting **any** visual pattern from `static/index.html` ("la ui/ux no me gusta para nada") |
