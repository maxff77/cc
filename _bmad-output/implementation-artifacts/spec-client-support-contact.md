---
title: 'Client-facing support/seller Telegram contact across surfaces'
type: 'feature'
created: '2026-06-15'
status: 'done'
baseline_commit: '3d229c3da75c850ed3433d3eafc887873fcd06f7'
context: ['{project-root}/CLAUDE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Clients have no easy, consistent way to reach the seller/support. Contact is now **Telegram-only** (`@yesterWhite`), but the app still offers WhatsApp + Telegram from placeholders (`config/site.ts` = `your_handle`/`0000000000`), only exposes contact on login + expired, and a half-finished login edit hardcodes `t.me/yesterWhite` instead of using shared config.

**Approach:** Make `@yesterWhite` the single Telegram contact, sourced from one frontend constant. Drop WhatsApp everywhere. Expose it on the three surfaces the client touches: **login**, the **expired** lockout, and — new — a permanent **"Soporte"** link in the client header (`ClientNav`, desktop + mobile) so support is reachable any time inside the app.

## Boundaries & Constraints

**Always:**
- One source of truth: `siteConfig.contact` in `frontend/config/site.ts`; every surface reads it, no second hardcode.
- Config holds both the bare handle (`yesterWhite`) and full link (`https://t.me/yesterWhite`); UI prepends `@` to display.
- External links: new tab, `rel="noopener noreferrer" target="_blank"`. Spanish copy, existing Ranger-X idiom (no restyle).
- Frontend-only: no backend, migration, or new env var.

**Ask First:**
- If the handle should be owner-editable at runtime (`system_settings` + admin UI) vs a redeploy-to-change constant.
- If seller and support should be two separate handles (current scope: one handle for both).

**Never:**
- Re-add WhatsApp on any surface.
- Touch legacy (`app.py`/`core.py`/`static/`) or the per-client `users.contact` admin feature (operator→client, a different shipped feature).
- Commit the unrelated dirty files (`.gitignore`, `.impeccable/*`, `CLAUDE.md`, `layout.tsx` impeccable-live `<script>`, `backend/scripts/enum_commands.py`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output |
|----------|--------------|-----------------|
| Login footer | render `/login` | "Soporte" link → `https://t.me/yesterWhite`, text `@yesterWhite`, new tab |
| Blocked account | login returns `account_blocked` | `ContactPanel`: Telegram-only button + copy with no "WhatsApp" |
| Expired plan | render `/expired` | `ContactPanel` Telegram-only + message with no "WhatsApp" |
| Logged-in client | render `(client)` chrome | permanent "Soporte" link → `t.me/yesterWhite` in header (desktop) and bottom nav (mobile) |

</frozen-after-approval>

## Code Map

- `frontend/config/site.ts` -- `siteConfig.contact`: replace `{whatsapp, telegram}` placeholders with `{ telegram: "https://t.me/yesterWhite", handle: "yesterWhite" }`.
- `frontend/components/contact-panel.tsx` -- shared danger Notice (login-blocked + expired); reduce `CHANNELS` to Telegram only, href from `siteConfig.contact.telegram`.
- `frontend/app/login/page.tsx` -- footer link reads `siteConfig.contact` (replace WIP hardcode); `COPY.account_blocked` drops "WhatsApp".
- `frontend/app/expired/page.tsx` -- `MESSAGE` drops "WhatsApp".
- `frontend/components/client-nav.tsx` -- add permanent external "Soporte" link in header right cluster + mobile bottom nav.

## Tasks & Acceptance

**Execution:**
- [x] `frontend/config/site.ts` -- `contact` = `{ telegram: "https://t.me/yesterWhite", handle: "yesterWhite" }`, remove `whatsapp` -- one Telegram source of truth.
- [x] `frontend/components/contact-panel.tsx` -- `CHANNELS` = Telegram only (label "Telegram", href `siteConfig.contact.telegram`) -- shared panel goes Telegram-only.
- [x] `frontend/app/login/page.tsx` -- footer uses `siteConfig.contact.telegram` + `@{siteConfig.contact.handle}`; `COPY.account_blocked` = "Tu cuenta está bloqueada. Escríbenos por Telegram para reactivarla." -- route through config, drop WhatsApp.
- [x] `frontend/app/expired/page.tsx` -- `MESSAGE` = "Tu plan venció. Escríbenos por Telegram y lo reactivamos." -- drop WhatsApp.
- [x] `frontend/components/client-nav.tsx` -- "Soporte" external `<a>` (href `siteConfig.contact.telegram`, new tab) in header (with ThemeToggle / Cerrar sesión) and mobile bottom nav -- always-on access in-app.

**Acceptance Criteria:**
- Given any visitor on `/login`, when it renders, then a "Soporte" link → `https://t.me/yesterWhite` opens in a new tab and no WhatsApp link/copy is present.
- Given a logged-in client on any `(client)` page, when chrome renders, then a permanent "Soporte" link → `t.me/yesterWhite` shows on both desktop header and mobile bottom nav.
- Given the handle is changed only in `siteConfig.contact`, when the app rebuilds, then every surface reflects it and no other app file hardcodes the handle/link.

## Design Notes

```ts
contact: {
  telegram: "https://t.me/yesterWhite", // full link for href
  handle: "yesterWhite",                // bare handle; UI shows `@${handle}`
}
```

`ClientNav` link matches the existing secondary-control idiom (sits with `ThemeToggle` + `Cerrar sesión`); shared chrome, so every role sees it (acceptable). The login footer markup already exists from the WIP edit — only swap the hardcoded href/text for `siteConfig`, keep its styling.

## Verification

**Commands:**
- `cd frontend && npm run build` -- expected: compiles, no type errors (build is the real gate; lint misses TS errors).
- `grep -rn "wa.me\|whatsapp\|your_handle\|0000000000" frontend/app frontend/components frontend/config` -- expected: no matches.
- `grep -rn "yesterWhite\|t.me" frontend/app frontend/components` -- expected: only via `siteConfig`; no literal hardcode outside `config/site.ts`.

**Manual checks:**
- `/login` footer → `t.me/yesterWhite` new tab; blocked login → panel Telegram-only.
- `/expired` → panel + message Telegram-only.
- Logged-in client → "Soporte" in header + mobile bottom nav on every client page.

## Suggested Review Order

**Source of truth**

- Entry point: the single Telegram contact every surface reads — change here, all follow.
  [`site.ts:10`](../../frontend/config/site.ts#L10)

**Shared contact surfaces**

- Reusable danger panel goes Telegram-only (login-blocked + expired share it).
  [`contact-panel.tsx:11`](../../frontend/components/contact-panel.tsx#L11)

- Login footer link bound to config (no hardcode); blocked-account copy drops WhatsApp.
  [`login/page.tsx:182`](../../frontend/app/login/page.tsx#L182)

- Expired lockout copy drops WhatsApp.
  [`expired/page.tsx:14`](../../frontend/app/expired/page.tsx#L14)

**Always-on in-app access**

- Client-only gate — staff are the seller, and it keeps the mobile nav from overflowing.
  [`client-nav.tsx:125`](../../frontend/components/client-nav.tsx#L125)

- Desktop header "Soporte" link (clients, `hidden lg:inline-flex`).
  [`client-nav.tsx:194`](../../frontend/components/client-nav.tsx#L194)

- Mobile bottom-nav "Soporte" link (clients).
  [`client-nav.tsx:222`](../../frontend/components/client-nav.tsx#L222)
