# Ranger-X Check — design system conventions

A dark-principal, neon-identity React component library (cyan → violet → magenta).
Components are plain function components exported from `window.RangerX.*`. There is
**no provider to wrap** — styling comes entirely from CSS custom properties, so the
only setup that matters is loading the stylesheet.

## Setup — load the stylesheet, pick the theme

Every component reads `var(--*)` tokens defined in `styles.css`. Without it the
components render with browser defaults (unstyled). `styles.css` `@import`s the
tokens, the brand fonts (Saira / JetBrains Mono / Public Sans, loaded from Google
Fonts at runtime), and the component CSS — load that one file and everything works.

- **Dark is the default** — the dark tokens live on `:root`, so a bare page is already dark.
- **Light theme**: add `class="light"` (or `data-theme="light"`) to an ancestor.
- **Accent presets**: `data-accent="cyan"` or `data-accent="magenta"` on an ancestor
  re-points `--accent`/`--focus` (violet is the default).
- The app surface is dark navy (`--background`); put content on `--surface` panels.

## Styling idiom — tokens, NOT utility classes

There is **no Tailwind / utility-class system**. Components style themselves with
inline styles driven by CSS variables. For your own layout glue, use the same tokens
so it stays on-brand — never invent hex colors:

| Role | Tokens |
|---|---|
| Surfaces | `--background` `--surface` `--surface-secondary` `--surface-tertiary` |
| Lines | `--border` `--border-strong` `--separator` |
| Text | `--foreground` `--muted` `--faint` |
| Brand spectrum | `--cyan` `--blue` `--accent` (violet) `--magenta` |
| Brand fills | `--brand-gradient` `--brand-gradient-soft` `--accent-soft` |
| Semantic | `--success` `--danger` `--warning` (+ `--*-foreground`) |
| Fields | `--field-background` `--field-border` `--field-foreground` |
| Shape | `--radius` `--radius-field` `--radius-sm` |
| Neon dial | `--glow` (0–1.6 multiplier on every glow/shadow) |

Helper classes shipped in the stylesheet: `.font-display` (Saira), `.font-mono`
(JetBrains Mono), `.gradient-text`, `.glow-accent`, `.glow-soft`, `.rx-scroll`,
`.rx-focus`, `.rx-backdrop` (the ambient page grid + corner bloom).

Headings/labels use **Saira**; numbers/codes/console rows use **JetBrains Mono**;
body copy uses **Public Sans**.

## Where the truth lives

- **Tokens & classes**: `styles.css` and its `@import` closure (`_ds_bundle.css`,
  `tokens/`). Read it before styling — it is authoritative.
- **Per-component API**: `components/general/<Name>/<Name>.d.ts` (the `<Name>Props`
  interface) and usage in `<Name>.prompt.md`.

## Idiomatic snippet

```jsx
const { SectionCard, Field, Btn, StatePill } = window.RangerX;

<div style={{ background: "var(--background)", padding: 24, color: "var(--foreground)" }}>
  <SectionCard legend="Envío" legendRight={<StatePill tone="accent" dot="pulse">Activa</StatePill>}>
    <Field label="Correo" icon="user" placeholder="cliente@rangerx.mx" />
    <Btn variant="primary" icon="send" style={{ marginTop: 12 }}>Enviar lote</Btn>
  </SectionCard>
</div>
```

The `primary` Btn wears `--brand-gradient` + a `--glow`-scaled neon shadow — that
gradient-on-a-surface is the one signature moment; keep other surfaces calm.
