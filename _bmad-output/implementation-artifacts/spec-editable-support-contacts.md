---
status: in-review
slug: editable-support-contacts
route: plan-code-review
created: 2026-06-28
---

# Spec: Editable Support Contacts

## Goal

The owner can add, remove, and edit the Telegram **support contacts** (the
handles clients see on `/login`, `/expired`, and the in-app "Soporte" link)
from the admin UI — no code edit, no redeploy. Today they are hardcoded in
`frontend/config/site.ts` (`siteConfig.contacts`, 2 fixed handles).

## Approach (mirror existing patterns)

- **Storage:** one `system_settings` row, key `support_contacts`, value = JSON
  array of canonical handles (the repo doc already states values are short
  strings; parsing lives in the owning service). **No migration.**
- **Default = current behavior:** when the row is unset, the service returns
  `["AionRanger", "AionRangerOwner"]` — identical to today. Zero change until
  the owner edits.
- **Public read:** `/api/public/support-contacts` (the existing no-auth router
  that already feeds `/login`). Handles are already public (rendered in login
  HTML today) → marketing-safe.
- **Owner write:** `/api/admin/support-contacts` (owner-only), reusing
  `_normalize_contact` (the single source of truth for handle format) and the
  `invalid_contact` error.
- **Frontend:** a `useSupportContacts()` hook fetches the public endpoint and
  falls back to `siteConfig.contacts` while loading / on error, so the support
  channel never disappears. `siteConfig.contacts` stays as the fallback.

## Tasks

### Backend

1. **`backend/app/services/support_contacts.py` (NEW)** — `KEY="support_contacts"`,
   `DEFAULT_HANDLES=["AionRanger","AionRangerOwner"]`, `MAX_SUPPORT_CONTACTS=8`.
   - `get_handles(session) -> list[str]`: read `system_settings_repo.get_value`;
     `None` / bad JSON / empty list → `DEFAULT_HANDLES`.
   - `set_handles(session, handles: list[str]) -> None`: `set_value(KEY, json.dumps(handles))`.
2. **`backend/app/errors.py`** — add `support_contacts_empty()` (400) and
   `too_many_support_contacts()` (400), mirroring the `telegram_target_*` block.
3. **`backend/app/api/public.py`** — `SupportContactOut{handle}`,
   `SupportContactsResponse{contacts}`; `GET /support-contacts` → `get_handles`.
4. **`backend/app/api/admin.py`** — `UpdateSupportContactsRequest{handles: list[str]}`;
   `GET /support-contacts` (owner) → `get_handles`; `PUT /support-contacts` (owner):
   normalize each via `_normalize_contact` (skip blanks, raises `invalid_contact`
   on malformed), dedupe case-insensitive (first wins); empty result →
   `support_contacts_empty`; `>MAX` → `too_many_support_contacts`; persist, commit,
   return normalized list. Reuse `SupportContactsResponse` from public.

### Frontend

5. **`frontend/hooks/use-support-contacts.ts` (NEW)** — client hook: `useEffect` +
   `api.get("/api/public/support-contacts")`, default state = `siteConfig.contacts`,
   keep fallback on error/empty. Returns `{handle}[]`.
6. **`frontend/app/login/page.tsx`** — replace `siteConfig.contacts.map` with
   `useSupportContacts()`.
7. **`frontend/components/contact-panel.tsx`** — move module-level `CHANNELS`
   into the component, driven by `useSupportContacts()`.
8. **`frontend/components/client-nav.tsx`** — replace `siteConfig.contacts[0]`
   with `useSupportContacts()[0]`.
9. **`frontend/app/admin/contactos/page.tsx` (NEW)** — owner page (mirror the
   destinos page idiom: `AdminShell gatesVisible`, `SectionCard`, `Field`, `Btn`,
   `Notice`, React Query). Editable list of handle inputs; first row tagged
   "Principal"; "Agregar contacto" appends a row; per-row remove (disabled when
   one left); "Guardar" → `PUT /api/admin/support-contacts`. Maps `invalid_contact`
   / `support_contacts_empty` / `too_many_support_contacts` to Spanish notices.
10. **`frontend/config/nav.ts`** — add an owner-only "Contactos" nav link to
    `/admin/contactos`.

### Test

11. **`backend/tests/test_support_contacts.py` (NEW)** — assert: unset → default
    handles; `PUT` normalizes (`@AionRanger`, `t.me/Foo` → `AionRanger`, `Foo`),
    dedupes, rejects empty (`support_contacts_empty`) and `>8`
    (`too_many_support_contacts`); non-owner → 403; public GET returns the set list.

## Acceptance Criteria

- **AC1 — Default unchanged.** Given the `support_contacts` setting is unset,
  When a logged-out user opens `/login`, Then the Soporte links show
  `@AionRanger` and `@AionRangerOwner` (exactly as today).
- **AC2 — Add.** Given the owner is on `/admin/contactos`, When they add a row
  `@NuevoSoporte` and Guardar, Then `/login`, `/expired`, and the in-app Soporte
  link all show the new handle without a redeploy.
- **AC3 — Remove.** Given two contacts exist, When the owner removes one and
  Guardar, Then only the remaining handle is shown across all three surfaces.
- **AC4 — Edit.** Given a contact `@AionRanger`, When the owner edits it to
  `@AionRangerMX` and Guardar, Then every surface links to `t.me/AionRangerMX`.
- **AC5 — Validation.** Given the owner submits `@x` (too short), When they
  Guardar, Then the request is rejected with `invalid_contact` and a Spanish
  notice; nothing is persisted.
- **AC6 — Empty guard.** Given the owner removes every contact, When they
  Guardar, Then `support_contacts_empty` is returned and the previous list
  stays intact.
- **AC7 — Owner-only.** Given a non-owner calls `PUT /api/admin/support-contacts`,
  Then it returns 403.
- **AC8 — Resilience.** Given the public endpoint errors, When `/login` renders,
  Then it falls back to the static `siteConfig.contacts` defaults (no empty UI).

## Out of scope

Reordering UI (first row is primary; owner re-points by editing the field).
Per-contact role labels (decision: handle-only, kept).
