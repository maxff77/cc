---
title: 'Dual Telegram support contacts (replace single seller handle)'
type: 'feature'
created: '2026-06-17'
status: 'done'
baseline_commit: '037addf83cb40ab82542c734f4cb215997ed992d'
context: ['{project-root}/CLAUDE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The client-facing support contact is a single hardcoded Telegram handle (`@yesterWhite`) in `frontend/config/site.ts`, read by login, the `/expired` lockout, and the in-app "Soporte" link. There are now two real contacts — `@AionRanger` (primary) and `@AionRangerOwner` — and `yesterWhite` is stale.

**Approach:** Replace the single `contact` object with an ordered `contacts` list of bare handles in the same one-source-of-truth config; every surface maps over it. Show **both** handles (no role label) on the surfaces with room — login footer, the shared `ContactPanel` (login-blocked + `/expired`), and the desktop client header. The mobile bottom-nav "Soporte" tab links to the first (primary) handle only, because two truncated handles are indistinguishable in a ~90px tab and the design system has no menu/popover component.

## Boundaries & Constraints

**Always:**
- One source of truth: `siteConfig.contacts` in `frontend/config/site.ts`. No second hardcode of any handle/link anywhere else.
- Store **bare handles only** (`"AionRanger"`); derive the link via one helper `telegramHref(handle) => \`https://t.me/${handle}\``. UI shows `@${handle}`.
- Array **order = priority**; index 0 is the primary support contact.
- External links: new tab, `rel="noopener noreferrer" target="_blank"`. Spanish copy, existing Ranger-X idiom — no restyle of surrounding markup.
- Frontend-only: no backend, no migration, no new env var, no new dependency/component.

**Ask First:**
- If the mobile bottom-nav should show **both** handles too (would need a menu/popover component or accept role labels) instead of primary-only.
- If a third+ contact or a non-Telegram channel is ever added (current scope: exactly Telegram, the two given handles).

**Never:**
- Re-add WhatsApp/email on any surface.
- Touch the per-client `users.contact` admin feature (`admin/users/page.tsx` `t.me/${contact}` — operator→client, a different shipped feature) or legacy (`app.py`/`core.py`/`static/`).
- Make the contacts owner-editable at runtime — decided: hardcoded constant, redeploy to change.
- Commit unrelated dirty files (`admin/plans/page.tsx`, `admin-shell.tsx`, `admin/keys/`, `components/keys/`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Login footer | render `/login` | "Soporte Telegram" + two chips `@AionRanger` `@AionRangerOwner`, each → `t.me/<handle>`, new tab | N/A |
| Blocked account | login returns `account_blocked` | `ContactPanel`: two Telegram buttons labeled `@AionRanger` / `@AionRangerOwner` | N/A |
| Expired / no-plan | render `/expired` (incl. no-plan self-reg) | same `ContactPanel`, both buttons | N/A |
| Desktop client header | logged-in client, `lg+` | two ghost buttons `@AionRanger` / `@AionRangerOwner` | N/A |
| Mobile client nav | logged-in client, `< lg` | single "Soporte" tab → `t.me/AionRanger` (primary, index 0) | N/A |
| Staff (owner/admin) | any surface | no support link shown (unchanged — staff are the seller) | N/A |

</frozen-after-approval>

## Code Map

- `frontend/config/site.ts` -- replace `contact: {telegram, handle}` with `contacts: { handle: string }[]` (the two handles); add exported `telegramHref(handle)` helper. **Single source.**
- `frontend/app/login/page.tsx` (~167-177) -- footer: map `siteConfig.contacts` → one chip per handle (keep chip styling), href via `telegramHref`.
- `frontend/components/contact-panel.tsx` (11-13) -- `CHANNELS` derived from `siteConfig.contacts`; one `Btn` per handle, label `@${handle}`, href via `telegramHref`. Shared by login-blocked + `/expired`.
- `frontend/components/client-nav.tsx` (204-219 desktop, 240-249 mobile) -- desktop: map contacts → one ghost `Btn` per handle labeled `@${handle}`; mobile: single `<a>` → `telegramHref(siteConfig.contacts[0].handle)`, label "Soporte" (unchanged structure).

## Tasks & Acceptance

**Execution:**
- [x] `frontend/config/site.ts` -- `contacts = [{handle:"AionRanger"}, {handle:"AionRangerOwner"}]`, remove `contact`; export `telegramHref(handle: string)` -- one source of truth + link derivation.
- [x] `frontend/components/contact-panel.tsx` -- build `CHANNELS` from `siteConfig.contacts.map(c => ({label:`@${c.handle}`, href:telegramHref(c.handle)}))`; render one button each -- shared lockout panel shows both.
- [x] `frontend/app/login/page.tsx` -- footer maps both handles into chips (preserve existing chip classes), href via `telegramHref` -- login shows both.
- [x] `frontend/components/client-nav.tsx` -- desktop header maps both handles into ghost buttons `@${handle}`; mobile bottom-nav "Soporte" → `telegramHref(contacts[0].handle)` -- in-app access (both on desktop, primary on mobile).

**Acceptance Criteria:**
- Given any visitor on `/login`, when it renders, then two distinct Telegram chips/links (`@AionRanger`, `@AionRangerOwner`) open `t.me/<handle>` in new tabs and no `yesterWhite`/WhatsApp text remains.
- Given a blocked or expired user, when the `ContactPanel` renders, then it shows two buttons, one per handle, each opening the correct `t.me` link.
- Given a logged-in client on `lg+`, when the header renders, then both handles appear as buttons; on `< lg`, the bottom-nav "Soporte" opens `t.me/AionRanger`.
- Given the handles change only in `siteConfig.contacts`, when the app rebuilds, then every surface reflects them and no other file hardcodes a handle/link (verified by grep).
- Given a staff (owner/admin) session, when any surface renders, then no support link is shown (unchanged behavior).

## Design Notes

```ts
// config/site.ts
export const telegramHref = (handle: string) => `https://t.me/${handle}`;
export const siteConfig = {
  // ...
  contacts: [
    { handle: "AionRanger" },      // index 0 = primary (mobile nav uses this)
    { handle: "AionRangerOwner" },
  ],
};
```

Mobile bottom-nav stays single-link by design: clients see 3 tabs (Envío, Historial, Soporte); a 4th tab would force ~90px columns where `@AionRanger` and `@AionRangerOwner` both truncate to `@AionRang…` — indistinguishable. With no menu/popover in the `components/ui/` design system (all custom, no HeroUI), primary-only is the clean choice; full list lives on login + `/expired`. Flip to both-on-mobile only by adding a menu or accepting role labels (Ask First).

## Verification

**Commands:**
- `cd frontend && npm run build` -- expected: compiles, no type errors (shape change from `contact`→`contacts`; build is the real gate, lint misses TS errors).
- `grep -rn "yesterWhite\|wa.me\|whatsapp\|\.contact\.telegram\|\.contact\.handle" frontend/app frontend/components frontend/config` -- expected: no matches (old shape/handle fully removed).
- `grep -rn "t.me\|AionRanger" frontend/app frontend/components` -- expected: only via `telegramHref`/`siteConfig`; no literal `https://t.me/<handle>` hardcode outside `config/site.ts` (the `users.contact` admin link in `admin/users/page.tsx` is the allowed exception).

**Manual checks:**
- `/login`: two chips, both open correct `t.me` links in new tabs.
- Blocked login + `/expired`: panel shows two buttons.
- Logged-in client: desktop header two buttons; mobile bottom-nav "Soporte" → `t.me/AionRanger`.

## Suggested Review Order

**Source of truth**

- Entry point: single list every surface reads; bare handles + one link derivation, no drift. Index 0 = primary.
  [`site.ts:15`](../../frontend/config/site.ts#L15)

- The only link builder — `@handle` → `t.me/handle`.
  [`site.ts:5`](../../frontend/config/site.ts#L5)

**Shared lockout panel (login-blocked + /expired)**

- `CHANNELS` derived from the list → one button per handle; label = `@handle` so the two are distinguishable (no role label).
  [`contact-panel.tsx:13`](../../frontend/components/contact-panel.tsx#L13)

**Auth surface**

- Login footer maps both handles into chips; `flex-wrap` added so they wrap on the narrow card.
  [`login/page.tsx:169`](../../frontend/app/login/page.tsx#L169)

**In-app access (clients only)**

- Desktop header: both handles as ghost buttons (lg+, room exists, full text).
  [`client-nav.tsx:205`](../../frontend/components/client-nav.tsx#L205)

- Mobile bottom-nav: primary-only "Soporte" (two truncated handles would be indistinguishable); guarded against an empty list.
  [`client-nav.tsx:242`](../../frontend/components/client-nav.tsx#L242)
