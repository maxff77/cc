# PRD Quality Review — cc (Plataforma SaaS de envío y captura por Telegram)

## Overall verdict

This is a tight, honest, well-scoped MVP PRD that correctly leads with its central risk (Tenancy B = single point of ban) and keeps the "how" in the addendum without leaving the "what" thin. It holds up on strategic coherence and scope honesty. What's at risk is **done-ness**: several FRs and most NFRs lack testable thresholds (FR10 round-robin fairness, FR13 adaptive interval, NFR1's "ritmo seguro", NFR4 "más lento, no se cae"), and the primary success metric has no baseline or target. A downstream architect inherits the hard problems cleanly via the addendum, but a story author and a QA engineer would hit ambiguity on what "done" and "passing" mean. PASS-WITH-FIXES.

## Decision-readiness — adequate

The PRD makes its governing decision explicit and up front: Tenancy model B is named, its two consequences (punto único de baneo, presupuesto de envío global) are stated as hard constraints that "gobiernan todo el producto", and the counter-metric (tasa de baneo ≈ 0) is wired directly to it. That is the right spine for this product and it is not smoothed to neutral — the PRD repeatedly subordinates growth to account safety (Visión, Riesgo central, Contra-métrica, NFR1). Trade-offs are named with what was given up: no per-client interval (FR12 trades client control for account safety), no batch-size cap (FR14, explicitly chosen), owner priority over fairness (FR11 vs FR10).

The weakness is the **Preguntas abiertas** section: `(ninguna — todas resueltas.)`. For an MVP about to be greenlit, zero open questions is itself a red flag — it reads as closure-by-fiat rather than genuine resolution. At least two real tensions are unresolved in the PRD body and merely deferred to the addendum (attribution mechanism feasibility, adaptive-interval formula). Those are legitimately architecture's to solve, but the PRD should flag them as `[NOTE FOR PM]` risks the architect must close before build, not declare everything resolved.

### Findings
- **medium** Zero open questions overstates closure (§ Preguntas abiertas) — Declaring "ninguna — todas resueltas" on a launch-stakes MVP whose core mechanism (response attribution on a shared session) is admittedly still "a evaluar" in the addendum is not credible. *Fix:* Add 1–2 `[NOTE FOR PM]` items naming the attribution-feasibility and interval-tuning dependencies as open architecture risks gating build.
- **low** FR11/FR10 tension not surfaced as a trade-off (§ F2) — Owner priority (FR11) directly cuts against the fairness guarantee (FR10/NFR4). The PRD states both as facts without acknowledging that owner traffic degrades client fairness. *Fix:* Note the bound — e.g., owner priority must not starve any client beyond X.

## Substance over theater — strong

Little furniture here. There are no invented personas (only Richard/owner and "cliente", both load-bearing), no differentiation/innovation section written for template's sake, and the Visión is specific to this product — "una única cuenta de Telegram compartida" and "el volumen de un cliente nunca degrada el servicio del resto" could not swap into a generic SaaS PRD. The risk section earns its place. The NFRs avoid pure boilerplate in that each ties to the shared-account thesis — but several still lack thresholds (see Done-ness). The roles (FR2) are concrete and each does work in later FRs (FR1, FR4, FR6, FR20). This dimension is genuinely strong; the PRD resists padding.

## Strategic coherence — strong

The PRD has a clear thesis: *take a working single-operator forwarder and sell time-boxed access to many clients over one shared Telegram account, where protecting that account is the existential constraint.* Every feature serves it. F1 (tenancy/access) gates who can consume the shared resource; F2 (controlled batch send) rations the shared resource fairly and safely; F3 (per-client capture) delivers the value while preserving isolation. Prioritization follows the thesis rather than ease — the hard, central problems (global rate budget, adaptive interval, fairness) are in scope for MVP, not deferred.

Success metrics mostly validate the thesis: primary = paying active clients (the business bet), counter-metric = ban rate ≈ 0 (the existential guardrail), secondary = retention/churn. The counter-metric is present and correctly chosen — this is exactly where the rubric wants one. MVP scope kind is coherent (revenue/platform hybrid with a problem-solving core), and Non-Goals match the scope logic (multi-número deferred as the explicit scaling direction). The one gap is measurability of the primary metric, covered under Strategic coherence's metric lens below and in Done-ness.

### Findings
- **medium** Primary metric has no target or baseline (§ Objetivo y métricas) — "número de clientes pagando activos" is a countable metric but no target, timeframe, or success threshold is given (e.g., N clients by month 3). Without it, "success" is undefined. *Fix:* Add a target and horizon; define "activo" precisely (logged in? sent a batch? plan vigente?).
- **low** Counter-metric "≈ 0" needs an operational definition (§ Contra-métrica) — "tasa de baneo ≈ 0" is directionally right but unmeasurable as stated (rate over what window? a single ban is catastrophic, so is the target literally 0 bans, or 0 sustained FloodWait?). *Fix:* State it as "0 account bans during MVP" plus a leading indicator (e.g., 0 sustained FloodWait events/week).

## Done-ness clarity — thin

This is the weakest dimension and the one downstream story/QA work will lean on hardest. Several FRs are testable as written: FR1 (manual creation, no self-registration), FR3 (access cuts at expiry date), FR7 (forced password change after reset), FR14 (no batch cap), FR18 (.txt export of both views), FR19 (delete only, no edit). Good. But a cluster of the most important FRs — precisely the ones implementing the thesis — have no verifiable acceptance condition:

- **FR10 (round-robin fairness)** — "ningún cliente puede monopolizar" / "avanzan de forma intercalada" is a property with no test. What's the fairness bound? Over what window must each active client get a turn?
- **FR13 (adaptive interval)** — "a más clientes, mayor el intervalo" gives direction but no function, bounds, or thresholds in the PRD. The 10–20 s band lives only in the addendum (tuning), but the *acceptance shape* (interval stays within band; monotonic in concurrency) belongs to a testable consequence. As written an engineer cannot tell when FR13 is "done."
- **FR15 (progreso en vivo y ETA)** — no accuracy or latency bound on ETA or "en vivo."
- **FR16 (atribución al cliente correcto)** — correctly defers the *mechanism* to the addendum, but the PRD states no acceptance criterion for the *guarantee* (e.g., "0 cross-client misattributions in test"). Given this is the single highest-risk correctness property in a shared-account design, the absence of a stated pass/fail condition is a real gap, separate from the (legitimately deferred) implementation.

The NFRs are largely adjectives where they need bounds:
- **NFR1** — "dentro de límites seguros de Telegram", "sin FloodWait sostenido": "sostenido" and "seguros" are undefined. NFR1 is marked crítico but is not measurable.
- **NFR4** — "el servicio se vuelve más lento, no se cae" — no bound on acceptable slowdown at 50 clients (max interval? max queue wait?).
- **NFR5** — "Contraseñas con hash" (which algorithm class? bcrypt/argon2 vs unsalted is a security cliff) and "protección de la sesión `anon.session`" (protection how — encryption at rest? file perms?) are under-specified for a security NFR.
- **NFR6** — "persisten y sobreviven a reinicios" is testable; good.

NFR2 (50 concurrent) is a clean, testable bound and is reused consistently. The PRD has **no Acceptance Criteria section**, and for this product type the FR consequences do not carry done-ness on the load-bearing FRs — so an explicit acceptance pass is warranted at least for FR10, FR13, FR16, NFR1, and NFR4.

### Findings
- **high** Load-bearing FRs lack testable acceptance conditions (§ F2/F3: FR10, FR13, FR15, FR16) — The features that implement the core thesis (fairness, adaptive pacing, correct attribution, live ETA) are stated as properties without verifiable bounds. Story authors cannot derive acceptance tests. *Fix:* Add a testable consequence to each (fairness window for FR10; "interval monotonic in concurrency and within band" for FR13; ETA/latency bound for FR15; "0 cross-tenant misattributions under concurrent load" for FR16).
- **high** Critical NFRs are adjectives, not bounds (§ NFR1, NFR4, NFR5) — "límites seguros", "FloodWait sostenido", "más lento no se cae", "contraseñas con hash" cannot be verified. NFR1 is the #1 requirement yet has no number. *Fix:* Define a sends/min ceiling (or reference the addendum band) and a FloodWait threshold for NFR1; a max-degradation bound for NFR4; name the hashing algorithm class and the `anon.session` protection mechanism for NFR5.
- **medium** No Acceptance Criteria section for a launch-stakes MVP (§ whole doc) — Given the thin done-ness on the highest-risk FRs/NFRs, the implied criteria are insufficient. *Fix:* Add a short Acceptance section covering FR10/FR13/FR16/NFR1/NFR4, even if thresholds reference the addendum.

## Scope honesty — strong

Omissions are explicit and do real work. The **Fuera de alcance (MVP)** section names five concrete exclusions (multi-número, session-content editing, self-registration, automated email, volume/consumption plans), and each is echoed at the FR that would otherwise silently imply it (FR1 ↔ no self-registration, FR6 ↔ no auto-email, FR14/Non-Goals ↔ pricing-by-time-only, FR19 ↔ delete-not-edit). Multi-número is correctly framed as the deferred scaling direction, consistent with the addendum. De-scoping is done out loud, not silently.

The PRD does not use inline `[ASSUMPTION]` / `[NON-GOAL]` / `[NOTE FOR PM]` tags or an Assumptions Index. For a PRD this small and with an explicit Non-Goals section that already covers the silent-assumption risks, the absence of the tag machinery is acceptable rather than a defect — but note the one place it bites: the "todas resueltas" claim (see Decision-readiness) is exactly where a `[NOTE FOR PM]` would have been honest. Open-items density is effectively zero, which on a green-light MVP is suspiciously low rather than reassuring.

### Findings
- **low** No assumptions are tagged despite inferences in play (§ whole doc) — e.g., "50 clientes activos" as the MVP ceiling and the role model appear to be inferred/agreed defaults but aren't marked as assumptions to confirm. *Fix:* Tag the load-bearing inferences (concurrency ceiling, "activo" definition) so the architect knows what's settled vs assumed.

## Downstream usability — adequate

IDs are clean: FR1–FR20 contiguous and unique, NFR1–NFR6 contiguous, features F1–F3 group them sensibly. Cross-references to the addendum resolve (FR13→tuning, FR16→attribution, NFR all coherent). Sections are mostly self-contained. This PRD is chain-top (it explicitly feeds arquitectura via the addendum and will feed stories), so downstream usability matters.

The main gap is the **absence of a Glossary**. Several domain nouns carry weight and need single definitions used identically everywhere: **sesión**, **prefijo** (catálogo global vs slug), **lote**, **cliente activo** (used in FR10, NFR1, NFR2 — and "activo" is overloaded: "paying active" in metrics vs "concurrently sending" in the scheduler), **espacio** (tenant boundary). Notably "cliente activo" means *paying* in the success metric but *concurrently sending* in FR10/FR13/NFR1/NFR2 — a real glossary drift that affects how NFR2's "50 activos" is measured. There are no UJs (see Shape fit — defensible), so there are no floating-protagonist issues.

### Findings
- **medium** "cliente activo" is overloaded (§ Objetivo vs FR10/NFR2) — "activos pagando" (business) vs "activos simultáneos" (concurrency) are different populations; conflating them makes the central capacity number (NFR2: 50) ambiguous. *Fix:* Disambiguate the terms (e.g., "clientes pagantes" vs "clientes enviando concurrentemente") and define each in a short Glossary.
- **low** No Glossary on a chain-top PRD (§ whole doc) — sesión, prefijo, lote, espacio recur across FRs/NFRs without canonical definitions. *Fix:* Add a 5–6 term Glossary so architecture/stories source-extract cleanly.

## Shape fit — strong

The PRD is correctly shaped as a **capability spec for a single-operator-turned-multi-tenant tool**, not forced into consumer-product formalism. The decision to omit User Journeys is the right call here: the client-facing surface is narrow and operational (paste batch → choose prefix → watch progress → view/export sessions), the operator (Richard) is a single named role, and the value is in the scheduling/safety machinery, not in a multi-stakeholder experience narrative. Forcing named-protagonist UJs onto this would be overhead. Metrics are appropriately business/operational (paying clients, ban rate) rather than engagement-quality vanity metrics. The brownfield reality (existing core.py/app.py Telethon code) is acknowledged in the addendum as the evolution base, which is accurate.

One mild caveat: F2/F3 do have enough client-facing interaction (pause/resume/stop, live ETA, session rename/continue/delete, export) that a single lightweight UJ for the client's batch-and-capture loop would help UX source-extract the screen flow — but its absence is a minor optimization, not a shape error.

### Findings
- **low** Optional: one client-loop UJ would aid UX hand-off (§ F2/F3) — The batch→prefix→progress→capture→export flow is the client's whole experience; a single named UJ would let UX work from the PRD directly. *Fix:* Optional — add one UJ for "cliente envía un lote y recupera resultados".

## Mechanical notes

- **Glossary drift:** "cliente activo" overloaded (paying vs concurrent) — flagged above (medium). "prefijo" appears as both catalog choice (FR9) and the existing slug concept (codebase) — define which the PRD means. Otherwise terminology is consistent.
- **ID continuity:** FR1–FR20 and NFR1–NFR6 contiguous, unique, no gaps or duplicates. Feature grouping F1–F3 consistent. No broken cross-references; all addendum pointers resolve.
- **Assumptions Index roundtrip:** N/A — no inline `[ASSUMPTION]` tags used. Acceptable for size, but the concurrency ceiling and "activo" definition would benefit from being tagged.
- **UJ protagonist naming:** N/A — no UJs (defensible for this shape, see Shape fit).
- **Required sections:** Visión, Riesgo central, Objetivo/Métricas (with counter-metric), Características/FRs, NFRs, Fuera de alcance, Preguntas abiertas all present. Missing for this stakes level: a Glossary and an Acceptance Criteria section (both flagged above).
