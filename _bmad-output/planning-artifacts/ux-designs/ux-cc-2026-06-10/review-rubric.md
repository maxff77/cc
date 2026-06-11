# Spine Pair Review — cc

## Overall verdict

The pair is a clean, extractable contract: every PRD journey lands in a named Key Flow, every token reference in both spines resolves to a defined DESIGN.md frontmatter token, routes / WS events / state machine / error contract are verbatim from architecture, and the confirmed mock plus theme import are linked inline with spines-win-on-conflict stated in both files. No critical or high findings. The two medium findings (light-mode gap in the amber alpha tints; no login-error treatment) are the only places a downstream builder would have to invent something user-facing; everything else is low-severity polish.

## 1. Flow coverage — strong

Extracted the five verbatim PRD journeys (FR9, FR10, FR15, FR17, FR5) plus the implied admin lifecycle (FR1/FR4/FR6/FR7) and FR20 support journey. Mapped against EXPERIENCE.md Key Flows 1–5: FR9+FR15 → Flow 1; FR17 → Flow 2; FR1/FR4/FR6/FR7 → Flow 3; FR5 → Flow 4; FR20 → Flow 5. FR10 (round-robin) is backend behavior, correctly surfaced only through the honest-ETA rule. All five flows have a named protagonist (Marcos, Laura, "the owner"), numbered steps, and an explicit **Climax** beat; Flows 1–3 have failure paths. The user's verbatim climax ("hands-off, zero manual work") is Flow 1's climax, as the decision log requires.

### Findings
- **low** FR17's "renombrar" is never exercised in a flow — it exists only as a behavioral rule in the Session row pattern (EXPERIENCE.md Component Patterns). The rule is complete ("inline, persisted via REST"), so this is coverage style, not a gap. *Fix:* optionally add a rename beat to Flow 2 step 2.
- **low** Flows 4 and 5 have no failure path (EXPERIENCE.md Key Flows). Flow 4 is itself a failure scenario, defensible; Flow 5 leaves "client has no sessions / session missing" unhandled. *Fix:* one line in Flow 5 (empty tenant view → same empty-Historial copy).

## 2. Token completeness — strong

Extracted all frontmatter tokens (24 color pairs, 6 typography roles, 3 radius, 6 spacing, 10 component objects) and every `{path.to.token}` reference in DESIGN.md prose and component objects, plus the 8 token references in EXPERIENCE.md. **All resolve.** Color tokens carry oklch values with light/`-dark` pairs throughout (accent/success intentionally identical across modes, matching the import). Values verified digit-for-digit against `imports/heroui-theme.css` (independent spot-check of accent, background, border, danger, warning, success-foreground — matches; consistent with `.working/reconcile-heroui-theme.md` PASS). Typography is fully literal-valued; nothing left semantic that shouldn't be. Contrast targets are absent — judged against the decision log's explicit MVP cut (a11y = HeroUI defaults only), so flagged but not critical.

### Findings
- **medium** The amber alpha tints hardcode the **dark-mode** warning literal with no light pair: `components.state-pill.paused-bg` and `components.flood-notice.background/border` use `oklch(82.03% 0.1395 75.04 / …)` (= `warning-dark`), while light-mode warning is `oklch(78.19% 0.1593 71.03)` (DESIGN.md frontmatter lines ~109, 136–137). DESIGN.md promises "light fully supported" and bans "dark-only hardcoding" in its own Do's and Don'ts. A light-mode builder gets the wrong hue or invents one. *Fix:* add `-dark` pairs for the three tint values, or express them as `{colors.warning}` at alpha so they track the mode.
- **low** No contrast targets stated for load-bearing combinations (muted on surface, success text on `new-highlight` tint, amber countdown on amber tint). Consistent with the explicit user decision to skip a11y extras for MVP — flagged honestly, not critical. *Fix:* none for MVP; record in the post-MVP revisit list.
- **low** `{spacing.margin-desktop}` is defined but never referenced, and Layout & Spacing introduces a 10px inter-block gap that sits outside the spacing scale (DESIGN.md Layout & Spacing). *Fix:* reference the token in the desktop paragraph; promote 10px to a spacing token or round to `{spacing.3}`.

## 3. Component coverage — strong

Extracted every component name used anywhere in either spine. DESIGN.md Components: Progress ring, State pill, Prefijo chip, Control buttons, Dual-view tabs, Data row, FloodWait notice, Bottom nav, Session row, Fields, Modal/Table (admin). EXPERIENCE.md Component Patterns: Lote textarea, Prefijo selector, Progress ring, Control buttons, State pill, Dual-view tabs/panels, Export button, FloodWait notice, Session row, ETA display, Admin user table, Prefijo catalog table. All shared names carry real rules on both sides (visual deltas with token refs in DESIGN; state-machine-driven behavior, REST/WS bindings in EXPERIENCE) — none are one-word descriptions.

### Findings
- **low** Five EXPERIENCE-only components have no identically-named DESIGN.md row: Lote textarea / Prefijo selector → "Fields", Export button → inside "Dual-view tabs", ETA display → inside "Progress ring" flank, Admin user table / Prefijo catalog table → "Modal / Table (admin)". Every mapping exists but is implicit. *Fix:* a parenthetical in each EXPERIENCE row naming its DESIGN.md anchor (e.g. "visuals: DESIGN.md Fields").
- **low** Bottom nav and Prefijo chip appear in DESIGN.md Components but have no behavioral row in EXPERIENCE.md — the live-dot semantics (success while sending, warning while paused) live only in the visual spec (DESIGN.md Bottom nav). Both are near-passive, so impact is small. *Fix:* one Component Patterns row for Bottom nav stating the dot is driven by `batch.state`.

## 4. State coverage — adequate

Walked all eight IA surfaces against the State Patterns table. Covered: Envío idle/cold/FloodWait/send-error, Historial empty/cold, Empty Filtrada, forced password change, plan expirado, permission denied (middleware redirect, no blocked screen), live-follow detach. Offline is explicitly cut per the decision log (WS auto-reconnect + snapshot only) and the spine states this — not a miss. Generic REST loading/error conventions (TanStack `isPending`/`isError` + `{code, message}` mapping) cover the long tail.

### Findings
- **medium** Login has no error treatment: wrong credentials, blocked account (Bloquear in Flow 3 step 4 produces "immediate lockout" with no stated login-time message). The generic code→Spanish mapping exists but the copy is load-bearing and user-facing; a builder will invent it (EXPERIENCE.md State Patterns / IA Login row). *Fix:* two State Patterns rows — invalid credentials and blocked-account copy at `/login`.
- **low** Admin surfaces' empty states are uncovered: `/admin/users` with no clients, `/admin/prefixes` empty catalog, `/admin/tenants/[id]` with no sessions; the Cold load row names only Envío/Historial. The decision log itself triaged this ("admin empty/cold states generic"). *Fix:* one row "Empty admin table → HeroUI Table empty slot + one Spanish sentence + primary action".
- **low** Empty Completa is uncovered — only Empty Filtrada has copy (lote running, no responses captured yet). *Fix:* mirror the Filtrada row ("Aún no hay respuestas.").

## 5. Visual reference coverage — strong

Inventory: `imports/heroui-theme.css`; `.working/` visual artifacts = `direction-cabina-refinada.html` (confirmed) plus four rejected explorations (`direction-consola-operador.html`, `direction-cabina-datos.html`, `direction-mensajeria.html`, `direction-tarjetas-saas.html`). The theme import is linked in DESIGN.md frontmatter sources, Brand & Style, and Colors, named as "canonical token source". The confirmed mock is linked inline in DESIGN.md Brand & Style (naming exactly what it illustrates: mobile Envío sending/paused, Historial, desktop Envío) and in EXPERIENCE.md IA ("composition reference"). Spines-win-on-conflict is stated in both files' header blockquotes. No references to the nonexistent `mockups/` or `wireframes/` dirs. The decision log records which surfaces are mock-covered vs spine-only (user's explicit "Ninguna más").

### Findings
- **low** The four rejected direction mocks sit in `.working/` unmarked — only the decision log says they're dead. A consumer scanning the folder cannot tell `direction-cabina-datos.html` (superseded by `-refinada`) from the confirmed file without it. *Fix:* nothing required; optionally a one-line "superseded — see .decision-log.md" comment or a `rejected/` subfolder.

## 6. Bloat & overspecification — strong

Both files are lean. DESIGN.md carries editorial voice where allowed (Brand & Style) and tokens elsewhere; EXPERIENCE.md is table-first with no decorative narrative — flow prose is tied to decisions and the user's verbatim climax. Foundation restates just enough stack to anchor the contract; the ETA `G×n` restatement is load-bearing (drives the honest-ETA rule), not bloat. No persona/FR restatement beyond inline FR citations.

### Findings
- **low** FloodWait notice spec self-conflicts: "12px `{typography.body}` text" but `typography.body` is 14px (DESIGN.md Components, FloodWait notice). A builder must pick one. *Fix:* drop "12px" or define/point to a smaller role (`data-mono` is already 11px).
- **low** A few magic pixel numbers outside the token scale: 10px block gap, 6px live dot, ~128px ring (DESIGN.md Layout & Spacing / Components). The ring and dot are reasonable approximations; the gap belongs in spacing. *Fix:* tokenize or mark as approximate.

## 7. Inheritance discipline — strong

All `sources` frontmatter entries resolve on disk (verified). FR names cited verbatim and correctly (FR9, FR12, FR15, FR17, FR18, FR19, FR20 — each checked against prd.md). Routes table, WS event names (`batch.progress`, `batch.line_sent`, `batch.state`, `response.captured`, `flood.wait`, `session.active`, `auth.state`, `error`, snapshot-first), batch state machine (`idle | sending | paused | stopping`), action endpoints (`/api/batches/{id}/pause|resume|stop`), and error contract (`{code, message}` Spanish) are all verbatim from architecture.md. Glossary terms (cliente, prefijo, sesión, lote, Completa/Filtrada) identical across PRD, decision log, and both spines. Every EXPERIENCE.md token reference resolves by name to DESIGN.md frontmatter, including dotted paths (`{components.progress-ring.color-sending}`/`.color-paused`).

### Findings
- **low** Source path conventions differ between the spines: DESIGN.md sources are workspace-relative (`imports/…`, `.working/…`), EXPERIENCE.md mixes repo-root-relative (`_bmad-output/…`) with workspace-relative (`.decision-log.md`). All resolve, but a mechanical extractor needs two base paths. *Fix:* normalize to one convention.
- **low** Frontmatter component keys are singular where prose is plural (`control-button` vs "Control buttons"); `dual-view-tabs` vs "Dual-view tabs / panels". Resolvable by inspection, trivial drift. *Fix:* align key names with section headings.

## 8. Shape fit — strong

DESIGN.md body sections are in canonical order with all eight present: Brand & Style → Colors → Typography → Layout & Spacing → Elevation & Depth → Shapes → Components → Do's and Don'ts. Frontmatter uses the spec's keys with correct types (flat kebab-case colors, nested typography, components with `{path}` refs). EXPERIENCE.md has all required defaults (Foundation, IA, Voice and Tone, Component Patterns, State Patterns, Interaction Primitives, Accessibility Floor, Key Flows) plus the triggered Responsive & Platform section — correctly triggered by the mobile-first user override, which it names explicitly. Inspiration & Anti-patterns is dropped; defensible (rejected directions documented in the decision log; banned-pattern list lives in Interaction Primitives). The Accessibility Floor honestly states the MVP cut rather than silently omitting the section — exactly right. No invented sections.

### Findings
- **low** Key Flows precedes Responsive & Platform; both shape examples place Key Flows last. The order lock is spec'd for DESIGN.md only, so this is a convention deviation, not a violation. *Fix:* move Responsive & Platform above Key Flows.
- **low** DESIGN.md frontmatter adds `status`/`updated`/`sources` beyond the spec's seven keys (the spec's example DESIGN files don't carry them). Harmless and aids traceability; a strict spec-conformant parser should tolerate unknown keys. *Fix:* none, or note them as workspace extensions.

## Mechanical notes

- **Cross-refs:** every `{path.to.token}` in both files resolves against DESIGN.md frontmatter — zero broken references found in a full extraction pass (colors ×15 distinct, typography ×6, rounded ×3, spacing ×2, components ×5 incl. dotted sub-paths).
- **Theme reconciliation independently confirmed:** spot-checked accent, background, border, danger, warning, success-foreground light+dark against `imports/heroui-theme.css` — digit-for-digit matches; the "inherit verbatim" clause correctly enumerates the unlisted tokens (focus, overlay, scrollbar, segment, default, field-border/foreground, surface-*-foreground).
- **Name inconsistencies:** singular frontmatter keys vs plural headings (`control-button`); EXPERIENCE-only component names without same-named DESIGN rows (§3); "Dual-view tabs" vs "Dual-view tabs / panels". All resolvable by inspection.
- **Internal value conflict:** FloodWait notice "12px" vs `{typography.body}` 14px (§6) — the only place the spines contradict themselves.
- **Frontmatter completeness:** both spines carry `name`/`status: draft`/`updated`/`sources`; all source paths resolve on disk (two base-path conventions, §7). `status: draft` is accurate pre-review.
- **ASSUMPTION hygiene:** 7 `[ASSUMPTION:]` tags found across the spines, matching the decision log's triage list exactly (admin surfaces extend the system; Detener instant / confirm only Eliminar; continuar-while-live guard; /expired contact targets; no owner priority controls; no tablet layout; admin mobile = responsive tables). All marked non-blocking; none hides a load-bearing decision that the consumer can't proceed without.
- **No dangling artifact paths:** spines reference only `.working/direction-cabina-refinada.html` and `imports/heroui-theme.css`; `mockups/`/`wireframes/` are never cited (they don't exist).
