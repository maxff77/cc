---
name: cc
status: final
updated: 2026-06-10
sources:
  - _bmad-output/planning-artifacts/prds/prd-cc-2026-06-10/prd.md
  - _bmad-output/planning-artifacts/prds/prd-cc-2026-06-10/addendum.md
  - _bmad-output/planning-artifacts/architecture.md
  - .decision-log.md
---

# cc — Experience Spine

> Peer contract: **EXPERIENCE.md owns how it works; DESIGN.md owns how it looks. Spines win over mocks** (`mockups/direction-cabina-refinada.html` is reference composition only). Document language: English. All user-facing microcopy: Spanish, tuteo.

## Foundation

Responsive web, **mobile-first priority** ("principalmente celular"; see Responsive & Platform for the override); desktop fully supported. UI system: **HeroUI v3** on **Next.js 16.2 app router + Tailwind CSS v4** (HeroUI `next-app-template` base), TypeScript strict. REST state via TanStack Query v5; live state via a single native auto-reconnecting WebSocket (`/ws`, tenant-scoped by cookie). `DESIGN.md` is the visual identity reference — this spine never restates visual specs, it cross-references tokens by name.

Multi-tenant SaaS: one shared Telegram account behind the scenes; every API call and WS event is tenant-scoped. Three roles — **owner / admin / client**. Admins manage clients (alta, renewal, block, password reset) but never other admins; the owner additionally manages admins, owns the global prefijo catalog, and has send priority. Auth is httpOnly cookie; Next.js middleware redirects unauthenticated users to `/login`, gates pages by role, routes expired plans to `/expired`, and a forced password change blocks everything except the change-password action.

## Information Architecture

Routes verbatim from the architecture:

| Surface | Route | Role | Purpose |
|---|---|---|---|
| Login | `/login` | all | Email + password entry; forced-password-change step when flagged |
| Envío | `/(client)/` (`page.tsx`) | client | Active session live: paste lote, prefijo selector, ring, queue, controls, live Completa/Filtrada |
| Historial | `/(client)/sessions` | client | Session list grouped by prefijo: rename, continue, delete |
| Detalle de sesión | `/(client)/sessions/[id]` | client | Completa/Filtrada views, live follow, export `.txt` |
| Gestión de usuarios | `/admin/users` | admin + owner | Create clients, renew plans, block, reset password (owner also manages admins here) |
| Catálogo de prefijos | `/admin/prefixes` | owner only | Create/edit the global prefijo catalog |
| Soporte cross-tenant | `/admin/tenants/[id]` | admin + owner | View any client's sessions (FR20) |
| Plan expirado | `/expired` | expired client | Lockout message + external contact channel |

Client navigation is exactly **two sections: Envío | Historial** — bottom nav on mobile, header nav on desktop ({components.bottom-nav}). **Both sections expose the dual Completa/Filtrada views**: Envío shows them live for the active session; Historial shows them per selected session (also live-following if that session is in progress). Modal stacks one level deep, never two.

→ Composition reference: `mockups/direction-cabina-refinada.html`. Spine wins on conflict.

## Voice and Tone

Spanish, **tuteo** (neutral Latin American), concise and operational — an instrument, not a cheerleader. Product terms verbatim: **cliente, prefijo, sesión, lote, Completa/Filtrada, pausar/reanudar/detener**. Code identifiers stay English.

| Do | Don't |
|---|---|
| "Pega tus líneas" | "Pegue sus líneas aquí por favor" |
| "Elige un prefijo" | "Seleccione el prefijo deseado" |
| "34 enviadas · 86 en cola" | "¡Ya llevas 34 mensajes! 🎉" |
| "Telegram pidió esperar 45 s — reanudamos solos." | "Error: FloodWaitError 420" |
| "Tu plan venció. Escríbenos por WhatsApp o Telegram y lo reactivamos." | "Acceso denegado." (dead-end) |
| "¿Eliminar esta sesión? No se puede deshacer." | "ADVERTENCIA: acción destructiva irreversible" |

FloodWait is always reassuring (the system handles it alone); expiry always redirects to the external sales channel, never dead-ends.

## Component Patterns

Behavioral. Visual specs live in `DESIGN.md.Components`.

| Component | Use | Behavioral rules |
|---|---|---|
| Lote textarea | Envío | Paste-first input ("Pega tus líneas"). One line = one message. Submitting starts the lote; submitting during a live lote appends new lines to the queue. |
| Prefijo selector | Envío | HeroUI `Select` over the global catalog fetched by API. **No free text** (FR9). Required before sending. |
| Progress ring | Envío | Driven exclusively by `batch.progress`. Center = % + fraction; flank = enviadas · en cola, ETA, CC nuevas — nothing else ({components.progress-ring}). |
| Control buttons | Envío | Pausar/Reanudar/Detener act on the **client's own lote only** (FR15). Visible set follows the state machine: `sending` → Pausar+Detener; `paused` → Reanudar+Detener; `stopping` → disabled; `idle` → hidden. Button presses fire REST actions (`/api/batches/{id}/pause|resume|stop`); UI state flips only on the resulting `batch.state` event — no optimistic state-machine jumps. |
| State pill | Envío header | Mirrors `batch.state` verbatim: Enviando / En pausa / Deteniendo; hidden at `idle`. |
| Dual-view tabs / panels | Envío + Historial detail | Completa = every captured response; Filtrada = deduped `CC:` data. Tab badges show live counts. New `response.captured` rows append with the "nueva" highlight; auto-scroll only if the pane was already at the bottom. |
| Export button | Both views, both sections | Triggers backend-generated `.txt` download (FR18) — one per view (completa / filtrada). Available during a live lote and on closed sessions. |
| FloodWait notice | Envío | Appears on `flood.wait` with a live countdown; dismisses itself when sending resumes. Informational ({components.flood-notice}) — never rendered as an error. |
| Session row | Historial | Tap → `/(client)/sessions/[id]`. Actions: Renombrar (inline, persisted via REST), Continuar (reopens session — dedup set preserved for new sends), Eliminar (confirm modal, then gone; no content editing in MVP, FR19). "En curso" badge on the live session. |
| ETA display | Envío | Honest adaptive math: per-client interval degrades with concurrency (~10–20 s band, `G×n`). Display as an estimate ("~12 min"), recomputed on each `batch.progress`; while paused, label it "ETA al reanudar". Never show a fake-precise countdown. |
| Admin user table | `/admin/users` | HeroUI `Table`. Row actions: renovar plan, bloquear, resetear contraseña (generates temp password shown once, delivered out-of-band — no email in MVP), crear cliente. Reset flags the account for forced password change at next login. |
| Prefijo catalog table | `/admin/prefixes` | Owner-only CRUD on catalog entries; clients only ever consume it through the selector. |

## State Patterns

**Batch state machine** — single source of truth is the WS `batch.state` event:

```
idle | sending | paused | stopping
```

The UI never invents a state; every control and pill derives from the last received `batch.state`.

**WebSocket events** (single `/ws` endpoint, envelope `{"event": "<name>", "data": {...}}`):

| Event | UI reaction |
|---|---|
| `batch.progress` | Update ring %, fraction, enviadas/en cola, recompute ETA |
| `batch.line_sent` | Mark line confirmed in queue view |
| `batch.state` | Drive the state machine: pill, controls, ring color ({components.progress-ring.color-sending}/{components.progress-ring.color-paused}) |
| `response.captured` | Append to Completa; if new deduped CC, append to Filtrada + bump CC nuevas |
| `flood.wait` | Show FloodWait notice with countdown — informational, NOT an error |
| `session.active` | Bind Envío surfaces to the active sesión |
| `auth.state` | Re-check auth; redirect via middleware rules if needed |
| `error` | Surface per error contract below |

**Connection:** every new WS connection receives a full snapshot first — the UI renders entirely from it (a tab opened mid-lote shows correct ring, counts, and rows immediately). The socket auto-reconnects; on reconnect the fresh snapshot reconciles all state silently. Per explicit decision, no offline UX beyond this (no banners, no queued offline actions).

**REST:** TanStack Query conventions — `isPending` → HeroUI skeletons/spinners matching the target layout; `isError` → error treatment below. Errors arrive as HTTP status + `{"code": "snake_case", "message": "texto en español"}`; the UI maps known `code`s to Spanish copy and falls back to the server's `message` verbatim.

**Per-surface states:**

| State | Surface | Treatment |
|---|---|---|
| Idle (no lote) | Envío | Ring hidden; textarea + prefijo selector prominent: "Pega tus líneas y elige un prefijo." |
| Cold load | Envío / Historial | Skeletons until snapshot + first query resolve. |
| Credenciales inválidas | `/login` | Inline field-level error: "Correo o contraseña incorrectos." Form stays filled (email kept), no redirect. |
| Cuenta bloqueada | `/login` | Blocking notice on submit: "Tu cuenta está bloqueada. Escríbenos por WhatsApp o Telegram para reactivarla." Same external-channel buttons as `/expired` — never a dead-end. |
| Empty Historial | Historial | "Todavía no tienes sesiones. Tu primer lote crea una." Link to Envío. |
| Empty Completa | Dual view | "Aún no hay respuestas." Counter at 0 — no fake rows. |
| Empty Filtrada | Dual view | "Aún no hay datos CC: capturados." Counter at 0 — no fake rows. |
| Empty admin table | `/admin/users` · `/admin/prefixes` · `/admin/tenants/[id]` | HeroUI Table empty slot + one sentence ("Todavía no hay clientes." / "El catálogo está vacío." / "Este cliente no tiene sesiones.") + the surface's primary action where one exists. |
| FloodWait | Envío | Amber notice, countdown, ring keeps its current state. NOT an error. |
| Send error | Envío | `error` event → inline notice by `code`; the lote continues per backend retry policy. |
| Forced password change | Post-login | All routes blocked except the change-password action; single screen: "Elige una contraseña nueva para continuar." |
| Plan expirado | Any route | Hard lockout → `/expired`. Message + external contact buttons (WhatsApp / Telegram). No partial access. |
| Permission denied | Admin routes for clients | Middleware redirect; no "blocked" screen rendered. |
| Sesión "En curso" elsewhere | Historial detail | Live-follows via `response.captured`; detaching/browsing another session stops following. |

## Interaction Primitives

- Tap/click to act; paste is the primary input gesture. No keyboard-first surface — operators are on phones.
- Controls are single-tap with server-confirmed state (per the Control buttons rule); destructive actions (Detener mid-lote [ASSUMPTION: confirm only on Eliminar, not Detener — Detener must stay instant], Eliminar sesión) follow DESIGN.md danger styling.
- Live rows auto-scroll only when the pane is already at the bottom; scrolling up pins the view.
- **Banned:** free-text prefijo entry, user-editable send interval (FR12 — system-controlled, display-only), filler stats and vanity counters, modal stacks > 1, celebratory animations, push notifications (declined for MVP), hover-only affordances on touch viewports.

## Accessibility Floor

**Minimal by explicit user decision** ("de momento ninguna, no quiero retrasar el MVP"): the floor is **HeroUI v3 component defaults only** — whatever focus management, ARIA roles, and keyboard operability HeroUI ships, unmodified. No additional audits, no custom screen-reader work, no reduced-motion handling, no contrast verification beyond the fixed theme.

Stated honestly: this is below a typical paid-consumer-product floor and is a deliberate MVP scope cut, not an oversight. Revisit post-MVP.

## Key Flows

### Flow 1 — Lote nocturno, manos libres (Marcos, cliente, on his phone at night)

1. Marcos opens the app on his phone; `/login` → email + password.
2. Lands on **Envío** (idle): "Pega tus líneas y elige un prefijo."
3. Pastes his lote (120 lines) into the textarea.
4. Picks `.zo` from the prefijo selector — catalog dropdown, no typing.
5. Taps **Enviar**. `batch.state: sending` — pill "Enviando", ring fills in {colors.accent}, ETA shows "~16 min" (honest adaptive estimate, ~10–20 s per line under current concurrency).
6. He pockets the phone. **Climax — hands-off:** the queue drains itself; each bot response lands in Completa and every new `CC:` line appears live in Filtrada with the "nueva" highlight, CC nuevas ticking up — zero manual work.
7. Mid-lote, `flood.wait` fires: amber notice "Telegram pidió esperar 45 s — reanudamos solos." The countdown runs; sending resumes alone. Marcos does nothing.
8. He needs to take a call: taps **Pausar** → `batch.state: paused`, ring turns {colors.warning}, ETA relabels "ETA al reanudar". Later, **Reanudar** → sending continues where it left off.
9. Lote finishes (`idle`). He taps **↓ .txt** on Filtrada and downloads the deduped CC data.

Failure path: a send error arrives as `error {code, message}` → inline Spanish notice; the lote keeps going per backend retry policy. If the WS drops, it auto-reconnects and the snapshot restores the exact picture — no user action.

### Flow 2 — Continuar una sesión (Marcos, two days later)

1. Marcos opens **Historial**; sessions listed grouped by prefijo, newest first.
2. Finds "Reposición semanal" (`.zo`), taps it → `/(client)/sessions/[id]`, reviews Completa/Filtrada.
3. Taps **Continuar** → the session reopens as the active sesión; Envío binds to it (`session.active`).
4. Pastes a fresh lote, sends. **Climax:** previously captured CC lines do NOT reappear in Filtrada — the dedup set was preserved from the original session; only genuinely new data lands, highlighted.
5. Exports the now-extended `filtrada.txt`.

Failure path: continuing while a lote is live is rejected — error by `code`, "Termina o detén el lote actual antes de continuar otra sesión." [ASSUMPTION: guard exists per single-active-session model; exact code from backend.]

### Flow 3 — Alta y ciclo de vida de un cliente (Laura, admin)

1. Laura logs in → `/admin/users`.
2. **Crear cliente:** email + plan dates; the system generates a temp password shown once. She delivers it out-of-band (WhatsApp) — no email in MVP.
3. Weeks later she **renueva el plan** of an expiring client from the same table.
4. A problematic client: **Bloquear** — immediate lockout.
5. Marcos forgot his password: **Resetear contraseña** → new temp password, delivered out-of-band. **Climax:** at Marcos's next login the middleware forces the change — "Elige una contraseña nueva para continuar" — nothing else is reachable until he does; then he lands on Envío as usual.

Failure path: creating a duplicate email → `error {code}` → "Ya existe un cliente con ese email."

### Flow 4 — Plan vencido (Marcos, plan expired overnight)

1. Marcos opens the app to send a lote; auth check detects the expired plan.
2. Hard lockout — every route resolves to `/expired`. No partial access, no degraded mode.
3. **Climax:** the page never dead-ends him: "Tu plan venció. Escríbenos por WhatsApp o Telegram y lo reactivamos." with direct external contact buttons. [ASSUMPTION: actual WhatsApp/Telegram links/numbers supplied by Richard at implementation.]
4. He messages the channel; an admin renews (Flow 3 step 3); next login works normally.

### Flow 5 — Soporte cross-tenant (owner, FR20)

1. A client reports "no veo mis CC de ayer". The owner opens `/admin/tenants/[id]` for that client.
2. Sees the client's sessions read-only, opens the one in question, switches Completa/Filtrada.
3. **Climax:** the owner diagnoses from the client's own data view — same dual-view component, cross-tenant by explicit admin route, the only place tenant isolation is intentionally crossed.
4. Owner-only extras live nearby: `/admin/prefixes` for the catalog, and admin management in `/admin/users`. The owner's send priority is backend behavior; the client UI never advertises it. [ASSUMPTION: no owner-facing priority controls in MVP — PRD leaves the owner-interval question open.]

## Responsive & Platform

Triggered: **mobile-first by user override** (PRD/architecture assumed desktop-primary; the decision log wins).

| Breakpoint | Behavior |
|---|---|
| `< md` (phones — primary) | Single column. Bottom nav Envío \| Historial ({components.bottom-nav}). Completa/Filtrada as segmented tabs ({components.dual-view-tabs}). Cockpit (ring + controls) stays visible; data panel scrolls internally. |
| `md` (tablets) | Same single-column layout with wider gutters. [ASSUMPTION: no dedicated tablet layout in MVP.] |
| `≥ lg` (desktop) | Nav moves into the top header. Envío becomes the 3-column grid per `DESIGN.md.Layout & Spacing`: cockpit left, **Completa and Filtrada side by side**. Historial: list + detail. |

Design and build phone-first; the desktop layout is a recomposition of the same components, never a separate feature set. Touch targets follow HeroUI defaults (Accessibility Floor). Admin surfaces are table-heavy and used mostly on desktop, but must remain operable on a phone. [ASSUMPTION: admin mobile = responsive tables, no special admin mobile design.]
