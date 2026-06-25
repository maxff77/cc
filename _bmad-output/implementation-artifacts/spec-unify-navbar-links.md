---
title: 'Unify navbar links across cockpit and admin chrome'
type: 'bugfix'
created: '2026-06-24'
status: 'done'
context: []
baseline_commit: '723d42b400c2d8a6e28bd91f6230310083ad5c41'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The app has two separate nav components — `ClientNav` (cockpit `/app*`) and `AdminShell` (`/admin/*`) — each with its OWN hardcoded link list. They have drifted: an owner on the cockpit sees `Envío, Historial, Usuarios, Gateways, Destinos`, but on an admin page sees `Envío, Usuarios, Keys, Planes, Gateways, Destinos, Monitoreo`. Same role, different links depending on the page — confusing and clearly unintended.

**Approach:** Introduce ONE source of truth: a `navLinks(role)` helper that returns the ordered link set per role (full union). Both `ClientNav` and `AdminShell` render from it, so a given role sees the identical set everywhere. No more divergent lists to drift.

## Boundaries & Constraints

**Always:**
- Canonical set per role (this exact order):
  - client → `Envío (/app)`, `Historial (/app/historial)`
  - admin → client set + `Usuarios (/admin/users)`, `Keys (/admin/keys)`
  - owner → admin set + `Planes (/admin/plans)`, `Gateways (/admin/gates)`, `Destinos (/admin/destinos)`, `Monitoreo (/admin/monitor)`
- Clients NEVER see any `/admin/*` link (role-gated, unchanged).
- Keep each component's existing active-tab logic, brand-gradient underline, theme toggle, logout, and cockpit-only extras (PlanBadge, Canjear key, Soporte, StatePill, live dot) exactly as-is. Only the link list is unified.

**Ask First:**
- Any change to the canonical link set, labels, or hrefs above.

**Never:**
- Do NOT merge the two components into one or restructure either shell's layout/styling beyond the nav link list + the mobile-overflow fix for staff.
- Do NOT touch backend, routes, middleware, or role gating.
- Do NOT add admin links to the client experience.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Client on cockpit | role=client | Nav shows exactly `Envío, Historial` | N/A |
| Admin anywhere | role=admin, on `/app` or `/admin/*` | Same set both places: `Envío, Historial, Usuarios, Keys` | N/A |
| Owner anywhere | role=owner, on `/app` or `/admin/*` | Same 8-link set both places | N/A |
| `me` query loading | role undefined | `navLinks` falls back to client set (no admin flash) | N/A |
| Owner cockpit on mobile | role=owner, <lg viewport | 8 links do not crush the fixed bottom bar | render staff links as a scrollable strip, not the bottom bar |

</frozen-after-approval>

## Code Map

- `frontend/config/nav.ts` -- NEW. `navLinks(role)` single source of truth (ordered union by role).
- `frontend/components/client-nav.tsx` -- replace local `ITEMS`/`ADMIN_ITEMS`/`OWNER_ITEMS` + role ternary with `navLinks(role)`; fix mobile so staff's expanded set doesn't break the fixed bottom bar.
- `frontend/components/ui/admin-shell.tsx` -- replace local `ITEMS` + `ownerOnly` filter with `navLinks(gatesVisible ? "owner" : "admin")` (clients never reach `/admin/*`, so `gatesVisible` already == "is owner").

## Tasks & Acceptance

**Execution:**
- [x] `frontend/config/nav.ts` -- create `export type NavLink = { href: string; label: string }` and `export function navLinks(role: string | undefined): NavLink[]` returning the canonical per-role union (client base, admin += Usuarios/Keys, owner += Planes/Gateways/Destinos/Monitoreo); unknown/undefined role → client base only.
- [x] `frontend/components/client-nav.tsx` -- derive `navItems` from `navLinks(role)`; delete the three local `*_ITEMS` consts and the ternary. Keep the desktop inline `<nav>` and all cockpit extras unchanged. Mobile: keep the fixed bottom bar for clients (`Envío, Historial` + Key); for staff make the same fixed bottom bar horizontally-scrollable (`overflow-x-auto rx-scroll`, `shrink-0` items) instead of cramming 8 links into an even split.
- [x] `frontend/components/ui/admin-shell.tsx` -- replace `ITEMS`/`navItems` with `navLinks(gatesVisible ? "owner" : "admin")`; drop the now-unused `ownerOnly` field. Keep `gatesVisible` prop signature and both `<nav>` strips (desktop inline + mobile scroll) and all call sites unchanged.

**Acceptance Criteria:**
- Given an owner, when they switch between `/app`, `/app/historial`, and any `/admin/*` page, then the navbar shows the identical 8-link set in the same order on every page.
- Given an admin, when on the cockpit vs an admin page, then both show `Envío, Historial, Usuarios, Keys` (Historial now also present on admin pages; Keys now also present on the cockpit).
- Given a client, when anywhere they can reach, then no `/admin/*` link ever appears.
- Given an owner on a <lg viewport cockpit, when the nav renders, then all staff links stay reachable without the bottom bar overflowing/wrapping.

## Design Notes

`gatesVisible` is passed as `gatesVisible` (true) on owner-only pages and `gatesVisible={isOwner}` on `keys`/`users`; since middleware blocks clients from `/admin/*`, `gatesVisible` is exactly "viewer is owner". So `AdminShell` can derive role as `gatesVisible ? "owner" : "admin"` with zero page-file edits.

Order chosen keeps `Historial` adjacent to `Envío` (client grouping) then admin links in `AdminShell`'s existing order, so neither shell's current ordering visibly jumps for the links it already had.

## Verification

**Commands:**
- `cd frontend && npm run build` -- expected: passes (tsc clean — the real gate per project rules; lint alone misses type errors).
- `cd frontend && npm run lint` -- expected: no new warnings.

**Manual checks:**
- Log in as owner: confirm identical link set on `/app` and `/admin/users`; resize to mobile and confirm the cockpit staff links scroll rather than wrap.
- Log in as a client (or simulate role=client): confirm only `Envío, Historial`.

## Suggested Review Order

**The fix — one source of truth**

- Start here: the whole change exists to replace two drifting lists with this one function.
  [`nav.ts:23`](../../frontend/config/nav.ts#L23)

- The canonical per-role union (order is load-bearing — it's the spec's "Always" list).
  [`nav.ts:8`](../../frontend/config/nav.ts#L8)

**Both shells now consume it**

- AdminShell derives role from the existing `gatesVisible` prop — the trick that needs zero page-file edits.
  [`admin-shell.tsx:46`](../../frontend/components/ui/admin-shell.tsx#L46)

- ClientNav feeds the live session role straight in — clients still never get admin links.
  [`client-nav.tsx:124`](../../frontend/components/client-nav.tsx#L124)

**Mobile overflow (the one non-trivial UI bit)**

- Staff's expanded set would crush the fixed bottom bar, so it scrolls horizontally for them; clients keep the even split.
  [`client-nav.tsx:282`](../../frontend/components/client-nav.tsx#L282)
