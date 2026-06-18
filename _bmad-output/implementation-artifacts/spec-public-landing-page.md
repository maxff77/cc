---
title: 'Public sales landing as default entry (about + offerings + live plans + gates), cockpit relocated to /app'
type: 'feature'
created: '2026-06-17'
status: 'done'
baseline_commit: '509695b'
context: ['{project-root}/CLAUDE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A first-time visitor hitting the site root is immediately bounced to `/login` — there is no public face that explains who we are, what we offer, our pricing, or which gates we support. Marketing/conversion is impossible and login/register feel like a dead end.

**Approach:** Make `/` a conversion-oriented public sales landing: about/hero, "what we offer" feature blocks, a **live pricing section driven by the real plan catalog** (premium tier highlighted; unlimited-credit plans show an ∞ infinity glyph), and a live "gates we have" list grouped by category → gate name. Relocate the authenticated client cockpit from `/` to `/app` (sessions to `/app/sessions`). Add no-auth backend endpoints feeding the public plans + gates lists. Login/register stay as their own pages, reached from the landing CTAs. Design to the impeccable bar (brand register), not a generic SaaS pricing page.

## Boundaries & Constraints

**Always:**
- 🔒 The public **gates** endpoint exposes **only `category_name` + gate `name`** — NEVER the real `value` (engine command) nor `display_value`/`credit_cost`. No auth dependency; reuse `gates_repo.list_active` (already eager-loads category).
- 🔒 The public **plans** endpoint exposes only marketing-safe fields: `name, price_usd, duration_days, max_lines_per_batch, credits, credits_unlimited, is_default` for **active plans only** (`is_active`). NEVER `antispam_seconds` (internal pacing) or any per-tenant data. No auth dependency.
- 🔒 **Unlimited credits = display convention, no migration.** `credits_unlimited = credits >= UNLIMITED_CREDITS_THRESHOLD` (a backend constant, e.g. 99_999). The owner sets the $21 tier's `credits` at/above the threshold from `/admin/plans`; the engine is untouched (it still decrements, but the grant is effectively inexhaustible). Frontend renders ∞ for `credits_unlimited`.
- 🔒 `tenant_id`/identity rules unchanged; `/app` and `/app/sessions` keep the exact same auth gating the cockpit has today (no-cookie → `/login`, plan/password gates intact).
- Landing reachable WITHOUT a session; an authenticated visitor at `/` is redirected to `/app`. Backend `_home_path_for("client")` returns `/app`; client recovery redirects (`/expired`, `/change-password` fallback) target `/app`.
- Design to the Ranger-X brand + impeccable bar (see Design Notes): dark control-room, violet→cyan as moments, Spanish copy, reuse `Logo`/`Mark`/`RxBackdrop`/`Btn`. Run `npm run build` (tsc gate) before done.

**Ask First:**
- Adding `antispam_seconds` or a new `unlimited_credits` DB column / engine "skip metering" semantics (this round uses the threshold convention only).
- Any sitemap/SEO/meta or i18n work beyond the dark Spanish page.

**Never:**
- Exposing gate `value`, `antispam_seconds`, or any per-tenant data publicly. (Plan price/credits ARE public now — intentional.)
- Touching Telethon / `core/telegram.py`, the send pipeline, the frozen credits-metering logic, or legacy root `app.py`/`core.py`/`static/`.
- A second instance / changing the auth gating semantics of the cockpit.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Visitor at `/`, no cookie | unauthenticated | Public landing renders: about/hero, feature blocks, live pricing cards, gates grouped by category (name only), CTAs → `/login` `/register` | any data fetch fails → that section shows a quiet fallback; rest of page still renders |
| Visitor at `/`, has cookie | authenticated (any role) | Redirect to `/app` | stale cookie → `/app` re-gates → `/login` (cookie cleared) |
| `GET /api/public/gates` | no auth | `200 {categories:[{name, gates:[name,…]}], total}`, grouped by category, names only, no `value` | empty catalog → `{categories:[], total:0}` |
| `GET /api/public/plans` | no auth | `200 {items:[{name, price_usd, duration_days, max_lines_per_batch, credits, credits_unlimited, is_default}], total}`, active only, no `antispam_seconds` | empty catalog → `{items:[], total:0}` (pricing section hides/falls back) |
| Plan with `credits >= 99_999` | premium tier | `credits_unlimited: true` → card shows ∞ glyph instead of a number | — |
| Client logs in / registers | success | backend `home_path` = `/app` → cockpit | unchanged |
| Client renews plan on `/expired` | plan active again | redirect to `/app` | unchanged |
| Admin/owner | authenticated | `/admin/*` unchanged; client-bounce from `/admin/*` now targets `/app` | unchanged |

</frozen-after-approval>

## Code Map

- `backend/app/api/public.py` -- NEW no-auth router (`/api/public`): routes `/gates` (reuse `gates_repo.list_active`, group by category, names only) and `/plans` (reuse plans repo `list_active`, map to marketing-safe shape + `credits_unlimited`).
- `backend/app/api/health.py` -- pattern reference for a no-`Depends` router.
- `backend/app/main.py:105-110` -- router registration block; add `public_router`.
- `backend/app/api/auth.py:140` -- `_home_path_for`: client `/` → `/app`.
- `backend/app/db/repos/gates.py:19` -- `list_active` (eager-loads `Gate.category`); reuse as-is.
- `backend/app/db/repos/plans.py` -- `list_active` (mirror admin `/plans/active`); reuse.
- `backend/app/config.py` -- add `UNLIMITED_CREDITS_THRESHOLD` constant (or module const in `public.py`).
- `backend/tests/test_admin_gates.py` + `test_*plan*` -- test patterns to mirror.
- `frontend/app/(client)/` -- cockpit route group; move to `frontend/app/app/` (page, layout, sessions, sessions/[id]).
- `frontend/app/page.tsx` -- NEW public landing.
- `frontend/middleware.ts:24,132,184` -- `/` public+authed-redirect handling; client-bounce `/`→`/app`; matcher already matches `/`.
- `frontend/components/client-nav.tsx:34,35,170` -- nav/logo links `/`→`/app`, `/sessions`→`/app/sessions`.
- `frontend/components/ui/admin-shell.tsx:23,24` -- cross-links `/`→`/app`, `/sessions`→`/app/sessions`.
- `frontend/app/expired/page.tsx:41,74` & `frontend/app/change-password/page.tsx:64` -- client recovery `/`→`/app`.
- `frontend/components/landing/*` -- NEW landing sections (hero, features, pricing, gates) to keep `page.tsx` lean.
- `frontend/types/api.ts` -- add `PublicGatesResponse` + `PublicPlansResponse` types.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/api/public.py` -- NEW no-auth router. `GET /api/public/gates`: reuse `gates_repo.list_active`; `{categories:[{name, gates:[name,…]}], total}` ordered by category then gate name; never `value`/`display_value`/`credit_cost`. `GET /api/public/plans`: reuse plans `list_active`; map to `{items:[{name, price_usd, duration_days, max_lines_per_batch, credits, credits_unlimited, is_default}], total}`; `credits_unlimited = credits >= UNLIMITED_CREDITS_THRESHOLD`; never `antispam_seconds`.
- [x] `backend/app/main.py` -- register `public_router` (near `health_router`).
- [x] `backend/app/api/auth.py` -- `_home_path_for`: return `/app` for `client`.
- [x] `backend/app/config.py` -- `UNLIMITED_CREDITS_THRESHOLD` constant (default 99_999).
- [x] `backend/tests/test_public.py` -- unit-test I/O matrix: both endpoints unauth 200; gates names-only (assert no `value`/`display_value`); plans active-only + no `antispam_seconds` + `credits_unlimited` true at/above threshold; empty-catalog shapes.
- [x] `frontend/app/app/**` -- move `(client)` route group to `/app`; fix in-file links (`/`→`/app`, `/sessions`→`/app/sessions`) in the moved `page.tsx`, `sessions/page.tsx`, `sessions/[id]/page.tsx`.
- [x] `frontend/app/page.tsx` + `frontend/components/landing/*` -- NEW sales landing: hero/about, feature blocks ("Envío masivo en vivo", "Captura ✅/❌ atribuida", "Filtrada automática", "Multi-tenant justo"), **live pricing** via `useQuery(["public-plans"], …/api/public/plans)` (premium/`is_default` highlighted, ∞ for `credits_unlimited`, $ price + días + líneas), gates-by-category via `useQuery(["public-gates"], …)`, CTAs → `/login` & `/register`. Reuse brand primitives; follow Design Notes.
- [x] `frontend/middleware.ts` -- at `/`: no cookie → `next()` (public); cookie → redirect `/app`. Client-bounce-from-admin → `/app`.
- [x] `frontend/components/client-nav.tsx`, `frontend/components/ui/admin-shell.tsx` -- repoint client links to `/app`, `/app/sessions`.
- [x] `frontend/app/expired/page.tsx`, `frontend/app/change-password/page.tsx` -- client recovery redirects → `/app`.
- [x] `frontend/types/api.ts` -- `PublicGatesResponse` + `PublicPlansResponse`.

**Acceptance Criteria:** (system-level; I/O scenarios above not repeated)
- Given the relocated cockpit, when a client uses `/app` and `/app/sessions`, then every feature (send, live state, history, exports, admin cross-links) behaves exactly as before the move — no broken links to `/` or `/sessions` remain anywhere.
- Given the landing's live sections, when the plan + gate catalogs are populated, then pricing cards render from the real catalog (premium highlighted, ∞ on unlimited) and gates render grouped by category — and each section degrades to a quiet fallback if its fetch fails, without breaking the page.
- Given the AI-slop bar, when the landing is reviewed, then it reads as the Ranger-X control-room brand (not a generic blue-SaaS pricing page): no per-section eyebrows, no 01/02/03 scaffolding, no new gradient-clipped prose text, no identical-card grid as the only idea.

## Design Notes

Impeccable **brand register**. Anchor: Ranger-X = *confident, sharp, alive — a precision instrument, not a brochure.* Escape the **generic-blue-SaaS** lane the product is rebranding away from; don't drift into editorial-serif either.

- **Scene/voice:** an operator's control room shown off for sale. Dark `--background`, calm surfaces, violet→cyan `--brand-gradient` only in moments (hero mark, primary CTA, featured plan, ∞). Spanish, terse operator copy.
- **Type:** existing stack only — Saira (hero), Public Sans (body), Fira Code (price/∞/gate names). Fluid hero ≤6rem, `text-wrap: balance` on headings.
- **Pricing:** NOT three identical SaaS cards — designed tier row, premium (`is_default`/top price) dominant; price as Fira Code readout; unlimited credits = bold ∞ *icon/SVG* (never `background-clip:text` on prose) + label "Ilimitados". 3–4 real features per card (días, líneas/lote, créditos, gates).
- **Imagery (required):** tool brand → product motifs as imagery (cockpit/readout mock, `RxBackdrop` bloom, canvas send-pulse). Text-only page / colored placeholder blocks are banned.
- **Motion:** one orchestrated page-load reveal (staggered, ease-out-expo) + `prefers-reduced-motion` crossfade. Not fade-on-scroll per section.
- **A11y:** WCAG AA; body ≥4.5:1; state never color-alone; focus rings on CTAs.
- The actual impeccable craft pass (browser screenshot + contrast/responsive testing per breakpoint) happens in step-03 build.

**Impl note (featured plan):** `is_default` is the gift-key *basic* tier, NOT the premium one — so the highlighted/"Recomendado" card is chosen as `credits_unlimited` (the $21 ∞ tier), falling back to the highest `price_usd` when no plan is unlimited. `is_default` is exposed but not used for emphasis.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest tests/test_public.py` -- expected: all pass (both endpoints unauth 200, gates names-only, plans no `antispam_seconds`, ∞ threshold, empty-catalog).
- `cd frontend && npm run build` -- expected: tsc + build succeed (no dangling `/` or `/sessions` type/route refs).

**Manual checks:**
- Logged out: `/` shows landing; pricing cards from real catalog (premium highlighted, ∞ on the $21 unlimited tier); gates grouped by category; CTAs reach login/register.
- Logged in client: `/` redirects to `/app`; `/app` and `/app/sessions` behave as the old cockpit; admin nav cross-links to `/app` work.
- DevTools: `/api/public/plans` has no `antispam_seconds`; `/api/public/gates` shows only category + gate names.
- Visual (step-03 craft): brand-on, AA contrast both themes, responsive at mobile/tablet/desktop, reveal motion + reduced-motion fallback.

## Suggested Review Order

**Routing & auth boundary (start here)**

- The design pivot: `/` is now public; any authed visitor is bounced to `/app`.
  [`middleware.ts:28`](../../frontend/middleware.ts#L28)
- Client post-login/register/recovery now lands on `/app`, not `/`.
  [`auth.py:140`](../../backend/app/api/auth.py#L140)

**Public catalog endpoints (no-auth data)**

- The ∞ convention: unlimited = `credits >= 99_999`, no migration.
  [`public.py:35`](../../backend/app/api/public.py#L35)
- Plans mapped to marketing-safe fields only (no `antispam_seconds`/per-tenant).
  [`public.py:105`](../../backend/app/api/public.py#L105)
- Gates grouped by category, names only (never `value`).
  [`public.py:72`](../../backend/app/api/public.py#L72)
- New public router wired into the app.
  [`main.py:107`](../../backend/app/main.py#L107)

**Landing UI**

- Page composition (server component; children own their fetches).
  [`page.tsx:21`](../../frontend/app/page.tsx#L21)
- Live pricing: single featured tier + ∞ render + graceful fallback.
  [`pricing.tsx:181`](../../frontend/components/landing/pricing.tsx#L181)
- Live gates by category as chips, with empty/error fallback.
  [`gates.tsx:34`](../../frontend/components/landing/gates.tsx#L34)
- ∞ glyph as a gradient-stroke SVG with a per-instance `useId`.
  [`infinity-glyph.tsx:1`](../../frontend/components/landing/infinity-glyph.tsx#L1)

**Route relocation & types (supporting)**

- Cockpit nav links repointed to `/app` / `/app/sessions`.
  [`client-nav.tsx:34`](../../frontend/components/client-nav.tsx#L34)
- Public response types (hand-kept in sync with the endpoints).
  [`api.ts:31`](../../frontend/types/api.ts#L31)
- Endpoint contract tests (public, names-only, ∞ threshold, empty shapes).
  [`test_public.py:106`](../../backend/tests/test_public.py#L106)
