# Product

## Register

product

## Users

Two roles, weighted equally — design both surfaces to the same bar:

- **Clientes (operators on a paid plan):** paste lines, pick a category→gate, fire a batch, and watch it drain. They live in the cockpit daily and care about one thing: *is it sending, and what came back?* They need speed, unambiguous state (sending / paused / flood / done), and instant attribution of ✅/❌ replies. Friction or ambiguity costs them money.
- **Owner / admins (operators running the platform):** manage tenants, gates, plans, the admission cap, and watch the global watchdog/ban-guardrail. Their job is keeping the single shared Telegram account alive and tenants unblocked. They need confident control surfaces and at-a-glance system health.

Context for both: focused, repetitive, real-time work — often long sessions, sometimes under time pressure. The interface is a control room, not a brochure.

## Product Purpose

Ranger-X Check is a multi-tenant Telegram forwarder (SaaS). Clients submit lines; the platform sends them through one shared Telegram user account to a checker bot, paced and round-robined fairly across tenants, then captures the ✅/❌ replies and attributes each back to its originating line and tenant. Two derived views — **Completa** (every captured reply revision) and **Filtrada** (deduplicated `CC:` data). Success = batches send reliably, state is never ambiguous, replies are correctly attributed, and the shared account is protected from bans. The product is currently live at cc.lohari.com.mx as "cc"; it is being rebranded to **Ranger-X Check**.

## Brand Personality

**Confident. Sharp. Alive.** A precision instrument, not a toy and not a brochure. Voice is direct, operator-to-operator Spanish — terse, no marketing fluff, no hand-holding (mirrors the existing product copy). The identity carries a vivid purple→cyan gradient as its signature, but it earns attention in *moments* (the mark, the primary action, live state) rather than shouting on every surface. Calm and reliable by default; energetic where energy means something.

## Anti-references

Avoid all four — confirmed by the user:

- **Crypto / pump aesthetic** — exchange neons, "moon" hype, garish glowing financial dashboards.
- **Generic blue SaaS** — the soulless gray+corporate-blue template that looks identical to a thousand other tools. The product ships with exactly this today (single hue-243 blue accent); the rebrand must escape it.
- **Loud gamer / esports** — the friend's reference logo: chrome bevels, 3D extrude, circuit-trace overlays, Twitch-overlay energy. Keep the gradient *concept*, drop the chrome.
- **Childish / playful** — emoji confetti, pastels, bubbly rounded kid-app look.

## Design Principles

1. **State is the product.** This is a real-time sending tool; sending/paused/flood/done, progress, and ✅/❌ attribution must be legible at a glance and never ambiguous. Clarity of state beats every decorative consideration.
2. **Energy in moments, not pages.** The purple→cyan gradient is punctuation — the mark, the primary action, a live pulse — surrounded by calm restrained surfaces. Never wallpaper.
3. **Earned familiarity.** Standard affordances done impeccably; the tool disappears into the task. No invented controls for standard jobs. Operators trust calm, reliable surfaces.
4. **Two surfaces, one bar.** Client cockpit and admin panel are designed to the same level of craft — neither is a second-class citizen.
5. **Light and dark are both first-class.** Every token, state, and the gradient identity must hold up in both themes with a real toggle — not dark-only with a broken light mode.

## Accessibility & Inclusion

WCAG AA. Body text ≥4.5:1, large text ≥3:1, placeholders held to body contrast (not muted gray). Visible focus rings on every interactive element. State conveyed by more than color alone (icon/label/shape alongside the ✅/❌/status hue) — matters doubly given the gradient identity and color-blind operators. Respect `prefers-reduced-motion` with a crossfade/instant fallback for every animation. Both themes must pass contrast independently.
