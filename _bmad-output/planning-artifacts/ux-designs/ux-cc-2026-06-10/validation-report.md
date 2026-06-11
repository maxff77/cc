# Validation Report — cc

- **DESIGN.md:** `/Users/pedro/Documents/git/Prueba/cc/_bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/DESIGN.md`
- **EXPERIENCE.md:** `/Users/pedro/Documents/git/Prueba/cc/_bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/EXPERIENCE.md`
- **Run at:** 2026-06-10

## Overall verdict

The pair is a clean, extractable contract: every PRD journey lands in a named Key Flow, every token reference in both spines resolves to a defined DESIGN.md frontmatter token, routes / WS events / state machine / error contract are verbatim from architecture, and the confirmed mock plus theme import are linked inline with spines-win-on-conflict stated in both files. No critical or high findings. The two medium findings (light-mode gap in the amber alpha tints; no login-error treatment) are the only places a downstream builder would have to invent something user-facing; everything else is low-severity polish.

## Category verdicts

| Category | Verdict |
|---|---|
| Flow coverage | strong |
| Token completeness | strong |
| Component coverage | strong |
| State coverage | adequate |
| Visual reference coverage | strong |
| Bloat & overspecification | strong |
| Inheritance discipline | strong |
| Shape fit | strong |

## Findings by severity

### Critical (0)

None.

### High (0)

None.

### Medium (2)

- **[Token completeness]** — Amber alpha tints hardcode the dark-mode warning literal with no light pair (DESIGN.md frontmatter lines ~109, 136–137). `components.state-pill.paused-bg` and `components.flood-notice.background/border` use `oklch(82.03% 0.1395 75.04 / …)` (= `warning-dark`), while light-mode warning is `oklch(78.19% 0.1593 71.03)`. DESIGN.md promises "light fully supported" and bans "dark-only hardcoding" in its own Do's and Don'ts; a light-mode builder gets the wrong hue or invents one. Fix: add `-dark` pairs for the three tint values, or express them as `{colors.warning}` at alpha so they track the mode.
- **[State coverage]** — Login has no error treatment (EXPERIENCE.md State Patterns / IA Login row). Wrong credentials, blocked account (Bloquear in Flow 3 step 4 produces "immediate lockout" with no stated login-time message). The generic code→Spanish mapping exists but the copy is load-bearing and user-facing; a builder will invent it. Fix: two State Patterns rows — invalid credentials and blocked-account copy at `/login`.

### Low (15)

- **[Flow coverage]** — FR17's "renombrar" is never exercised in a flow (EXPERIENCE.md Component Patterns). It exists only as a behavioral rule in the Session row pattern; the rule is complete ("inline, persisted via REST"), so this is coverage style, not a gap. Fix: optionally add a rename beat to Flow 2 step 2.
- **[Flow coverage]** — Flows 4 and 5 have no failure path (EXPERIENCE.md Key Flows). Flow 4 is itself a failure scenario, defensible; Flow 5 leaves "client has no sessions / session missing" unhandled. Fix: one line in Flow 5 (empty tenant view → same empty-Historial copy).
- **[Token completeness]** — No contrast targets stated for load-bearing combinations (DESIGN.md Colors). Muted on surface, success text on `new-highlight` tint, amber countdown on amber tint. Consistent with the explicit user decision to skip a11y extras for MVP. Fix: none for MVP; record in the post-MVP revisit list.
- **[Token completeness]** — Unreferenced token and off-scale gap (DESIGN.md Layout & Spacing). `{spacing.margin-desktop}` is defined but never referenced, and a 10px inter-block gap sits outside the spacing scale. Fix: reference the token in the desktop paragraph; promote 10px to a spacing token or round to `{spacing.3}`.
- **[Component coverage]** — Five EXPERIENCE-only components have no identically-named DESIGN.md row (EXPERIENCE.md Component Patterns). Lote textarea / Prefijo selector → "Fields", Export button → inside "Dual-view tabs", ETA display → inside "Progress ring" flank, Admin user table / Prefijo catalog table → "Modal / Table (admin)". Every mapping exists but is implicit. Fix: a parenthetical in each EXPERIENCE row naming its DESIGN.md anchor (e.g. "visuals: DESIGN.md Fields").
- **[Component coverage]** — Bottom nav and Prefijo chip have no behavioral row in EXPERIENCE.md (DESIGN.md Bottom nav). The live-dot semantics (success while sending, warning while paused) live only in the visual spec; both are near-passive, so impact is small. Fix: one Component Patterns row for Bottom nav stating the dot is driven by `batch.state`.
- **[State coverage]** — Admin surfaces' empty states are uncovered (EXPERIENCE.md State Patterns). `/admin/users` with no clients, `/admin/prefixes` empty catalog, `/admin/tenants/[id]` with no sessions; the Cold load row names only Envío/Historial. The decision log itself triaged this. Fix: one row "Empty admin table → HeroUI Table empty slot + one Spanish sentence + primary action".
- **[State coverage]** — Empty Completa is uncovered (EXPERIENCE.md State Patterns). Only Empty Filtrada has copy (lote running, no responses captured yet). Fix: mirror the Filtrada row ("Aún no hay respuestas.").
- **[Visual reference coverage]** — The four rejected direction mocks sit in `.working/` unmarked (`.working/`). Only the decision log says they're dead; a consumer scanning the folder cannot tell `direction-cabina-datos.html` (superseded by `-refinada`) from the confirmed file without it. Fix: nothing required; optionally a one-line "superseded — see .decision-log.md" comment or a `rejected/` subfolder.
- **[Bloat & overspecification]** — FloodWait notice spec self-conflicts (DESIGN.md Components, FloodWait notice). "12px `{typography.body}` text" but `typography.body` is 14px; a builder must pick one. Fix: drop "12px" or define/point to a smaller role (`data-mono` is already 11px).
- **[Bloat & overspecification]** — A few magic pixel numbers outside the token scale (DESIGN.md Layout & Spacing / Components). 10px block gap, 6px live dot, ~128px ring; the ring and dot are reasonable approximations, the gap belongs in spacing. Fix: tokenize or mark as approximate.
- **[Inheritance discipline]** — Source path conventions differ between the spines (DESIGN.md / EXPERIENCE.md frontmatter sources). DESIGN.md sources are workspace-relative (`imports/…`, `.working/…`), EXPERIENCE.md mixes repo-root-relative (`_bmad-output/…`) with workspace-relative (`.decision-log.md`). All resolve, but a mechanical extractor needs two base paths. Fix: normalize to one convention.
- **[Inheritance discipline]** — Frontmatter component keys are singular where prose is plural (DESIGN.md frontmatter / Components). `control-button` vs "Control buttons"; `dual-view-tabs` vs "Dual-view tabs / panels". Resolvable by inspection, trivial drift. Fix: align key names with section headings.
- **[Shape fit]** — Key Flows precedes Responsive & Platform (EXPERIENCE.md section order). Both shape examples place Key Flows last; the order lock is spec'd for DESIGN.md only, so this is a convention deviation, not a violation. Fix: move Responsive & Platform above Key Flows.
- **[Shape fit]** — DESIGN.md frontmatter adds keys beyond the spec's seven (DESIGN.md frontmatter). `status`/`updated`/`sources` are extras; harmless and aids traceability, a strict spec-conformant parser should tolerate unknown keys. Fix: none, or note them as workspace extensions.

## Reviewer files

- `review-rubric.md`
