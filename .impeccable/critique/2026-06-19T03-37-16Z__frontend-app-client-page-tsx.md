---
target: app page (cockpit /app)
total_score: 27
p0_count: 0
p1_count: 2
timestamp: 2026-06-19T03-37-16Z
slug: frontend-app-client-page-tsx
---
## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 4 | Ring + state pills + live nav dot + server-truth notices + completion moment; status is never ambiguous. |
| 2 | Match System / Real World | 3 | "Filtrada sin respuesta" is semantically backwards (it IS the CC data extracted from ✅ responses); domain jargon (gate, lote, CC) is fine for the audience. |
| 3 | User Control and Freedom | 3 | Pause/Resume/Stop, Cancel, Rename, Nueva sesión, confirm on destructive. No undo of a send (inherent), but Stop covers it. |
| 4 | Consistency and Standards | 2 | The three result views are named differently on desktop vs mobile vs legend; notice strips split between the Notice component, SectionCard, and ad-hoc bordered divs. |
| 5 | Error Prevention | 3 | Pre-submit guards (empty, line cap, credits), locked selects mid-batch, confirm dialog, disabled states. Strong. |
| 6 | Recognition Rather Than Recall | 3 | Active-gate chip, two-step category→gate selector, labeled nav (no icon-only). |
| 7 | Flexibility and Efficiency | 2 | Paste-first + append + Enter-to-submit, but no keyboard shortcuts for Pausar/Detener and no bulk ops for a daily power tool. |
| 8 | Aesthetic and Minimalist Design | 2 | Always-on circuit-grid backdrop + 3 neon corner blooms + glow on every state pill + neon ring shadow push energy onto the page, not into moments; 3 equal live-scroll columns add noise. |
| 9 | Error Recovery | 3 | Spanish, code-mapped, field-anchored, non-blocking, preserves the textarea. |
| 10 | Help and Documentation | 2 | One inline legend explains the views; no contextual help/tooltips; assumes domain fluency. |
| **Total** | | **27/40** | **Acceptable (top of band, just under Good)** |

## Anti-Patterns Verdict

**Does this look AI-generated? No.** This is a hand-built, opinionated control room, not a template. State handling (server-truth, no optimistic jumps), the engraved-legend rack plates, tabular-nums everywhere, real empty/skeleton/loading states, and sr-only verdict text on the ✅/❌ glyphs are all above the AI-slop bar.

**LLM assessment:** The slop risk here is the opposite of flatness — it's *over-energy*. The documented system (DESIGN.md) is "control-room calm, energy only in ≤3 moments per screen, flat plates, near-zero-chroma true gray." The built product drifted: every page carries an ambient circuit-grid + 3 neon corner blooms (`rx-backdrop`), every StatePill carries a neon `box-shadow` glow, the progress ring carries a neon drop-shadow, neutrals carry ~10× the documented chroma (violet-navy, not true gray), the system radius grew from 4px to 7–12px, and a condensed display font (Saira) now rides buttons + nav. Circuit-trace overlays + glow are precisely the "loud gamer/esports — Twitch-overlay energy" the PRODUCT.md anti-references say to avoid. None of this is *slop*; it's a deliberate lean toward "Alive." But it has crossed from punctuation into wallpaper.

**Deterministic scan:** `detect.mjs --json` over `app/app` + `components/batch` + `components/sessions` + `components/ui` + `client-nav.tsx` returned `[]` (exit 0 — clean). No mechanical tells: no eyebrow scaffolding, no decorative side-stripe borders (the 2px rail is the sanctioned state instrument), no identical-card grids. The `.gradient-text` utility exists in `globals.css` but is **unused** in the cockpit (the wordmark is a raster PNG) — dead CSS, not a live violation. `backdrop-blur` appears only on the sticky header + modal backdrop (purposeful, not decorative glass).

**Visual overlays:** Not available this run. No Ranger-X dev server was up (`:3100` down; `:3000` is another site), the cockpit is an auth-gated SPA that redirects to `/login` without a session, and no browser-automation tool was available. Findings are from source review + the deterministic scan only.

## Overall Impression

A genuinely competent operator's console — the engineering discipline (server-truth state, write-ahead semantics surfaced honestly, accessibility baked in) shows in the UI. What's working against it: the visual identity has drifted louder than its own spec, the same three data views wear three different names across surfaces, and the daily primary action (Enviar) can get pushed below a tall stack of transient notices. None are rewrites; all three are tractable. The single biggest opportunity: decide, deliberately, where this lives on the calm↔alive axis and apply that decision consistently — right now the backdrop says "crypto terminal" and the data rows say "control room."

## What's Working

1. **State is legible and honest.** The ring (gradient while sending → solid warning while paused), the header/nav pills, the live nav dot, and the completion moment all read at a glance, and nothing moves until the server confirms it. This is the product's core promise and it's delivered.
2. **Accessibility is not an afterthought.** sr-only "Aprobada/Rechazada" behind every ✅/❌ glyph, `role="alert"` vs `role="status"` split by severity, `tap-44` coarse-pointer targets, visible 2px focus rings, and a real `prefers-reduced-motion` block. Persona Sam survives most of the flow.
3. **Empty / loading / error states exist everywhere.** IdleRing with an invitation, shape-faithful PanelSkeleton, per-panel empty copy, field-anchored Spanish errors that preserve the paste. No "nothing here," no centered spinners.

## Priority Issues

- **[P1] Tonal drift: energy is on the page, not in moments.** The `rx-backdrop` circuit grid + 3 neon corner blooms render behind *every* screen, every StatePill carries a `box-shadow: 0 0 12px currentColor` glow, the ring carries a neon drop-shadow, and `--glow` multiplies it all. DESIGN.md's "Moments Rule" caps brand energy at ≤3 elements per screen; the build blew past that. The circuit overlay specifically matches the "loud gamer/esports / Twitch-overlay" anti-reference the brand rejects.
  - **Why it matters:** Operators run long, repetitive, money-on-the-line sessions. Ambient glow competes with the *functional* glow (the live ring, the sending pill) for the eye, eroding exactly the "state is the product" legibility the rest of the app earns. It also undercuts the "sobria" brand intent.
  - **Fix:** Pull `--glow` toward ~0.3–0.5 globally; drop the circuit grid to a near-invisible texture or kill it on the cockpit; remove the always-on glow from StatePills and let only the *sending* pill + the active ring glow. Reserve neon for the live moment.
  - **Suggested command:** `/impeccable quieter`

- **[P1] The three result views have four different names.** Desktop columns: *Completa · Filtrada con respuesta · Filtrada sin respuesta*. Mobile tabs: *Completa · Con respuesta · Sin respuesta*. The legend: *Todas las respuestas · Aprobadas · Datos CC*. Domain docs: *Completa · Filtrada*. And "Filtrada **sin** respuesta" is backwards — it's the CC data **extracted from** the ✅ responses, yet labeled "without response."
  - **Why it matters:** An operator builds a mental model on desktop, opens their phone, and the tabs don't match the columns. The backwards "sin respuesta" label actively misleads. This is the consistency + match-real-world failure that drags both heuristic scores down.
  - **Fix:** Pick one vocabulary for all three surfaces. Suggest: *Completa · Aprobadas · Datos CC* (matches the legend, which is already the clearest). Kill "con/sin respuesta" entirely.
  - **Suggested command:** `/impeccable clarify`

- **[P2] The primary action sinks under transient notices.** The 320px cockpit column stacks, in order: ring → ActiveSession → PlanExpiry → Controls → Watchdog → Flood → FailedLines → PendingLines → AwaitingReply → SendForm → ClaimKey. When several conditional strips fire at once (plan expiring + watchdog paused + flood + failed lines), the textarea and **Enviar** get pushed below the fold and require scrolling — on the surface the user visits to *send*.
  - **Why it matters:** The daily primary action loses its position to alerts that, while important, are not the task. Persona Casey (mobile, interrupted) and Alex (efficiency) both feel this.
  - **Fix:** Keep SendForm anchored near the top (right under the ring), or pin Enviar; collapse the stacked notices into a single dism/expandable region; cap the simultaneous-notice height.
  - **Suggested command:** `/impeccable layout`

- **[P2] Three equal-weight live-scrolling columns raise cognitive load.** Desktop renders Completa, Filtrada-con, and Filtrada-sin side by side, each streaming and auto-pinning, at identical visual weight. The middle column is a *filtered subset* of the left. The eye has three moving targets and no primary.
  - **Why it matters:** "Aesthetic and minimalist" + working-memory load. Density is permitted in a tool, but three synchronized motion sources with no hierarchy is noise, not density.
  - **Fix:** Either establish a primary view (size/emphasis) with the others secondary, or make the middle subset a toggle/filter on Completa rather than a third permanent column.
  - **Suggested command:** `/impeccable distill`

- **[P2] Notice components are inconsistent.** `failed-lines`, `watchdog-notice`, `waiting-notice` hand-roll `rounded border border-danger/50 bg-danger/10` divs; `flood`/`plan-expiry`/banner paths use the `Notice` component; the form sits in a `SectionCard`. Three different containers for "the system is telling you something."
  - **Why it matters:** Consistency. The same semantic event (a warning, a failure) should wear the same shell everywhere or the surface reads as stitched together.
  - **Fix:** Route every status strip through `Notice` (or a shared rack-plate notice), with tone the only variable. Confirm each strip's tone matches the doctrine (amber = waiting/fine, red = failure).
  - **Suggested command:** `/impeccable polish`

## Persona Red Flags

**Alex (Power User):** No keyboard shortcuts for Pausar/Reanudar/Detener — every control is click-only on a surface he'll drive hundreds of times. No bulk/queue management beyond paste. The primary action can sit below the fold under notices, so even "paste and send" isn't a fixed muscle-memory target.

**Sam (Accessibility):** Mostly strong — sr-only verdicts, focus rings, reduced-motion fallback, severity-split roles. Two gaps: the nav live-dot conveys send-state by color alone (it's redundant with the pill, but on mobile the pill is hidden < sm, leaving a color-only signal), and the small `text-accent` export link (11.5px violet on `--surface`) is borderline for the 4.5:1 body-text floor — worth a measured check in both themes.

**Cliente operator (project persona — time-pressured, daily, revenue on the line):** Opens the cockpit to fire a batch fast. Hits the four-names problem the moment they move between desktop and phone. In a bad-state moment (watchdog + flood at once) they scroll past red strips to find Enviar. The always-on neon backdrop adds visual fatigue across a long session. None block the task, but each adds friction to the exact "is it sending, what came back?" loop the product is built around.

## Minor Observations

- `--font-display: Saira` and a 7–12px radius scale both contradict DESIGN.md ("no display font," "restrained 4px"). The spec has drifted from the build — either is defensible, but DESIGN.md should be re-synced (`/impeccable document`) so future work doesn't fight a stale spec.
- The nav Wordmark is a raster PNG forced onto a light rounded plate inside the dark header — it reads as a light "patch/sticker" rather than the gradient wordmark moment DESIGN.md envisioned.
- Credits strip uses the ✅ emoji as a unit ("N créd./✅") — fine in-domain, slightly informal for a billing figure.
- Lots of 10–11px text (data rows, caps legends, credits). Acceptable as console density, but it's near the floor across the whole surface; verify it holds at 200% zoom.

## Questions to Consider

- Where does Ranger-X actually want to sit on calm↔alive — and would the neon survive a 3-hour shift, or is it a screenshot-day feature?
- Does the operator need all three result views visible at once, or is one the view they live in and the other two on-demand?
- If a batch is mid-send and the watchdog fires, what's the *one* thing the operator must see first — and is it winning the layout right now?
