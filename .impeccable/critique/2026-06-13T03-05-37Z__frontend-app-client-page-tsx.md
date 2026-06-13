---
target: the cockpit
total_score: 29
p0_count: 1
p1_count: 2
timestamp: 2026-06-13T03-05-37Z
slug: frontend-app-client-page-tsx
---
# Critique — Client Cockpit (`frontend/app/(client)/page.tsx`)

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 4 | Best-in-class: pill + live dot + ring stroke + flank metrics, WS server-truth, never optimistic. |
| 2 | Match System / Real World | 3 | `FILTRADA CON/SIN RESPONSE` engineer labels; mobile tabs say "Con response" — same data, two names. |
| 3 | User Control and Freedom | 3 | Pause/Resume/Stop immediate + reversible; no confirm on destructive Detener (intentional). |
| 4 | Consistency and Standards | 2 | Drift: two caps styles, two card mechanisms, two count badges, rogue `default-*` tokens. |
| 5 | Error Prevention | 3 | Strong — catalog-only gate, whitespace blocked both layers, re-submit guarded, stale-gate self-heals. |
| 6 | Recognition Rather Than Recall | 3 | Active-gate chip persists; 3 near-identically-titled panels demand recall of which holds what. |
| 7 | Flexibility and Efficiency | 3 | Append-while-live + scroll-pin thoughtful; zero keyboard shortcuts. |
| 8 | Aesthetic and Minimalist | 3 | Calm default; up to 9 stacked panels on mobile, no collapse. |
| 9 | Error Recovery | 3 | Per-code Spanish copy anchored to field/Alert; generic fallback thin for a money failure. |
| 10 | Help and Documentation | 2 | None. No tooltips, no gate explanation, no ✅/❌/nueva legend. |
| Total | | 29/40 | Acceptable — status visibility carries it; consistency + help + unbuilt brand drag it. |

## Anti-Patterns Verdict

Not AI-slop. Reads as a competent, disciplined, generic-blue dashboard — a real opinionated system, but a DIFFERENT opinion than DESIGN.md documents.

- LLM: clean on classic tells (no gradient-text, no hero-metric, no identical card grids; the one 2px left-rail is correctly gated to live-state). Real smell = drift: two caps styles (`response-views.tsx:215`), two card mechanisms (flat SectionCard vs HeroUI elevated Card, shadow manually nulled), two count badges (live duplicates dead `ui/count-badge.tsx`).
- Deterministic scan: 0 findings, exit 0, across 23 cockpit TSX files. Verified genuine (synthetic slop file tripped 2 rules + exit 2). BUT markup-only — the P0 lives in `globals.css`, never read by the scan. Clean detector understates, doesn't clear.
- Visual overlays: none — no browser automation. Fallback signal only.

## Overall Impression

Structural layer genuinely well-built (flat plates, engraved legends, total tabular discipline, server-truthful state) — all wearing the wrong color. The Ranger-X rebrand ships on zero surfaces. `globals.css:22,67` still set `--accent: oklch(55% 0.12 243)` (the exact legacy hue-243 blue the rebrand escapes) in both themes. No gradient tokens (grep accent-from/292/195 = 0). Gradient on 0/3 mandated moments. Biggest opportunity: make the brand real, then close drift.

## What's Working

1. Status visibility best-in-class + server-truthful (WS-only state, ring stroke by state, nav dot mirror, pill copy verbatim).
2. Waiting/flood reassurance doctrine-perfect: `waiting-notice.tsx:21` promotes queue-position to hero readout replacing the misleading 0% ring; flood amber + countdown. Amber-vs-red diagnosis held.
3. Tabular-figure discipline total — every live number `font-mono tabular-nums`. The one 100%-built piece of the system.

## Priority Issues

[P0] Brand layer unbuilt; cockpit ships prohibited legacy blue. Why: literal anti-reference the rebrand escapes; every doctrine win wears the wrong color; detector can't catch it (CSS). Fix: `--accent: oklch(60% 0.19 292)` + matching `--focus` both themes; add `--accent-from`/`--accent-to`; gradient on primary Enviar + mark + sending pulse (3 moments). Command: colorize.

[P1] Three near-identical Filtrada panels = comprehension tax. Why: 4-col grid (`page.tsx:60`) of overlapping subsets with engineer labels; operator must model "completa ⊇ con-response" under pressure. Fix: collapse to one panel + filter toggle, or relabel + legend; reconcile stale 3-col comment. Command: distill.

[P1] System-internal inconsistency (caps, cards, badges, tokens). Why: violates One-Tracking + Flat-Plate; HeroUI Card path is a latent shadow that lifts in light theme; reads as assembled not designed. Fix: rogue caps→LabelCaps; panels→flat SectionCard; delete dead badge; `default-*`→`surface-*`/`border`. Command: harden.

[P2] No terminal/success moment. Why: completion returns silently to idle ring; peak-end lost on the successful finish. Fix: "Lote completo · N enviadas · N CC" summary in ring slot before revert — sanctioned success-pulse moment. Command: delight.

[P2] No in-cockpit pre-warning before plan-expiry lockout mid-batch. Why: middleware → full-page `/expired`; client yanked mid-batch into a void. Fix: non-blocking amber "Tu plan vence en N días" strip; let running batch finish/pause gracefully. Command: clarify.

## Persona Red Flags

Alex (power user): zero keyboard shortcuts (click-only Enviar/Pausar/Detener); 3 always-on panels can't collapse/reorder; append-while-live undiscoverable.

Sam (accessibility): focus ring prohibited blue + likely low-contrast on near-white light field; ✅/❌ emoji-only status (`response-row.tsx:40`), no aria-label; "nueva" tag `text-[9px]` below low-vision floor. Good: pulse motion-safe; role=status vs role=alert correct.

Riley (stress tester): pending/failed lists cap + authoritative count (good); BUT 3 response panels render every row, no virtualization → 10k×3 jank-lock; scroll-pin useLayoutEffect fires every frame under capture flood.

## Minor Observations

- Mark still says "CC" (`client-nav.tsx:164`) — rename unbuilt at brand-string level.
- Light theme: `--surface` and `--field-background` both pure white — fields disappear into plates; light mode looks untested.
- `legend="NUEVO LOTE"` all-caps string into LabelCaps which also CSS-uppercases — double-cased, SR spell-out risk.
- `ui/count-badge.tsx` dead code; `active-session-card.tsx:137` hardcodes always-on `rail="accent"`.

## Questions to Consider

1. Gradient is the whole identity, ships on zero surfaces — is Ranger-X any different from the blue SaaS it was funded to escape?
2. Three overlapping views always on screen — did a client ask for "Filtrada con response," or is the backend kind-split leaking into UI?
3. Nails waiting + failure, no success peak — is the missing end-of-run moment costing loyalty?
4. Light theme declared first-class — has anyone run a live batch in it, or is dark the only tested reality?
