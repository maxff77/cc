# Reconcile — imports/heroui-theme.css vs DESIGN.md

**Verdict: PASS — 0 mismatches.**

- All 30 CSS tokens per mode accounted for: explicitly restated in DESIGN.md frontmatter (light + `-dark` pairs) or covered by the "inherit verbatim" clause (default, default-foreground, field-border, field-foreground, focus, overlay, overlay-foreground, scrollbar, segment, segment-foreground, surface-*-foreground).
- oklch values match digit-for-digit (spot-checked accent, background, danger, danger-dark, among others).
- Radius: `--radius`/`--field-radius` 0.25rem = `rounded.DEFAULT`/`rounded.field`. ✓
- Font: Public Sans requirement carried over (load note included). Monospace stack is a DESIGN.md addition for data rows — an extension, not a reinterpretation.
- No qualitative content from the import dropped.
