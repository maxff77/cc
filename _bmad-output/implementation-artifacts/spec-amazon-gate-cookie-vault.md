---
title: 'Amazon gate — cookie vault (Phase 1)'
type: 'feature'
created: '2026-06-19'
status: 'done'
baseline_commit: 'b11a6b9a7770df3951b6209499a50cb9c5232ff8'
context: ['{project-root}/CLAUDE.md', '{project-root}/_bmad-output/implementation-artifacts/spec-gate-category-special-mode.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Amazon gates will need per-account cookies sent before each line (Phase 2), but there is nowhere for a client to store them. Phase 1 builds only the vault: a place to keep credentials and a way for the owner to mark a gate-category as cookie-mode.

**Approach:** Add a tenant-scoped `gate_cookies` table (a client stores/lists/deletes their own cookies per gate) and a `cookie_mode` boolean on `gate_categories` (+ a snapshot on `capture_sessions`), cloning the existing `special_mode` idiom exactly. No send/rotation/capture-reader changes; the schema leaves a Phase-2 per-cookie `status` column (defaults `'active'`, unread) but no rotation logic exists.

## Boundaries & Constraints

**Always:**
- `tenant_id` comes from the session only, never from body/path.
- Cookie values are SENSITIVE credentials, treated like captured CC data: **stored PLAINTEXT in Postgres** (the locked CC precedent — access-control + TLS); real encryption is explicitly deferred to Phase 2. Plaintext-at-rest never becomes plaintext-on-the-wire.
- The stored value is NEVER echoed to any client; the list returns only `{id, label, masked_value, status, created_at}`. Masking is length-safe: `len ≤ 8` → fixed `••••` (reveal nothing, leak no length); else `value[:2] + '••••' + value[-2:]` with a fixed dot count.
- Value validation (empty / oversized / unprintable) is raised as `AppError invalid_cookie` (400) **inside the router body — NEVER via a pydantic validator on the value field**, so the rejected value can never surface in a default 422 body or access log (no `RequestValidationError` handler exists in `main.py`).
- Cookie values are never logged — not on the happy path, not in the `IntegrityError` dedup mapping, not in any 500. Catch `IntegrityError` narrowly and re-query by `(tenant_id, gate_id, value_hash)` without interpolating the value.
- Cookie read endpoints carry `Cache-Control: no-store`.
- Canonicalization: `value = value.strip()` ONCE, before both the empty/length check AND persistence, so the dedup index keys on the same bytes the validator saw.
- Dedup is DB-enforced (never in code) by a unique index on `(tenant_id, gate_id, value_hash)` where `value_hash = sha256(canonical value)` (a stored generated/derived column). The full value lives in a Text column; the hash, not the value, sits in the btree (the value can exceed the ~2704-byte btree row limit). Store-first / catch-`IntegrityError`-second is the only dedup arbiter — never SELECT-then-INSERT.
- On a unique violation: `await session.rollback()` FIRST, THEN re-fetch the existing `(tenant_id, gate_id, value_hash)` row in a clean transaction and return it 200 (the txn is aborted after the violation — a re-query without rollback would itself 500).
- Gate resolution on POST: `0 < gate_id ≤ _PG_INT_MAX` then `gate is None OR gate.deleted_at is not None` → 404 `gate_not_found` (the batches.py L141-145 guard). Resolve/authorize the gate FIRST (unknown/foreign/retired/oversized → identical 404), then evaluate `cookie_mode` → 409 only for a gate this tenant can already see.
- GET and DELETE are tenant-scoped by cookie/gate ownership and do NOT re-gate on `cookie_mode` (only POST does) — a client can always list and delete cookies they own even after the category flag flips off or the gate retires (no orphaned, undeletable credentials).
- Repos use flush-not-commit; the router/request owns the commit.
- New NOT NULL columns ship `server_default=sa.text('false')` (backfill in one step); `alembic upgrade head` runs before service restart.
- `cookie_mode` clones the `special_mode` idiom exactly: category column + capture-session snapshot, threaded through `create_active` / `resolve_for_batch` (refresh on same-gate reuse) / `resolve_for_backfill` and the `sessions.py new_session` fork — the SAME files special_mode touched. The snapshot WRITE path ships now; the READER stays Phase 2.
- `cookie_mode` is exposed to clients ONLY as a plain UX boolean on the public gate payload (so the cockpit knows when to show the manager) — sourced from `gate.category.cookie_mode`. `gate.value` is never exposed.

**Ask First:** (carried as open forks)
- The per-`(tenant, gate)` cookie cap value (proposed 50) before it is hardcoded.
- Confirm Phase-1 plaintext-at-rest (encryption deferred to Phase 2) is acceptable for credentials of this sensitivity.

**Never:**
- No `send_worker` / `scheduler` / `capture` reader / `attribution` / rotation changes (all Phase 2). No capture-pipeline code reads `gate_cookies` or `cookie_mode`.
- No Telethon imports or send-flow touches.
- No edits to legacy `app.py` / `core.py` / `static/`.
- Never expose `gate.value` to clients; never move cookie dedup into code; never validate the value via a pydantic field validator.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Store cookie | `POST /api/cookies` `{gate_id, value, label?}`, session tenant, cookie-mode gate | 201 masked `CookieOut` (no value); row tied to `(session.tenant_id, gate_id)`; value stripped before persist | empty/whitespace-only/oversized(>~2600 canonical chars)/unprintable → 400 `invalid_cookie` raised in handler (value never in body) |
| Duplicate value | Same canonical `(tenant, gate, value)` posted twice (incl. `"abc"` then `"abc\n"`) | Idempotent: rollback → re-fetch existing row → 200 with SAME id (DB unique index on `value_hash`, never code) | `IntegrityError` mapped to existing row, never 500, value never logged |
| Gate not cookie-mode | gate visible to tenant, `category.cookie_mode = false` | reject store | 409 `gate_not_cookie_mode` (only AFTER gate is confirmed visible) |
| Unknown/foreign/retired/oversized gate | `gate_id` unknown, another tenant's, `deleted_at` set, or out of int4 | reject identically, BEFORE the cookie-mode check | 404 `gate_not_found` (no existence leak, id never logged) |
| List cookies | `GET /api/cookies?gate_id=` | 200 masked list, `Cache-Control: no-store`, bounded LIMIT (newest first); value never present; works even if gate now non-cookie-mode/retired | tenant-scoped; foreign/unknown/oversized `gate_id` → 200 empty list (identical to "no cookies" — no existence leak) |
| Delete cookie | `DELETE /api/cookies/{id}`, owned by tenant | 204, hard delete; works even if gate now non-cookie-mode/retired | — |
| Delete bad id | unknown / foreign-tenant / oversized id | 404 identically | no existence leak, id never logged |
| Cap reached | Nth+1 distinct value for `(tenant, gate)` | reject store | 409 `cookie_limit_reached` |
| Owner toggle | `PATCH /admin/gate-categories/{id}` `{cookie_mode}` | persists flag; `None` leaves untouched; takes effect on the tenant's NEXT batch via the capture-session snapshot | owner role required → 403 otherwise |

</frozen-after-approval>

## Code Map

- `backend/app/db/models.py` -- add `cookie_mode` (Boolean, `server_default=false()`, not null) to `GateCategory` (after `special_mode`) and `CaptureSession` (after its `special_mode`); add `GateCookie` (tenant_id, gate_id, value Text, value_hash, label nullable, status default `'active'`, created_at) with a unique index on `(tenant_id, gate_id, value_hash)`.
- `backend/migrations/versions/<new>_gate_cookies_vault.py` -- NEW; `down_revision='e1c7a4b9d2f0'` (current head). Cloned from `e1c7a4b9d2f0_category_special_mode.py`: two `add_column` (`cookie_mode`, server_default false) + `create_table` gate_cookies + unique index on `value_hash`.
- `backend/app/db/repos/gate_cookies.py` -- NEW; flush-not-commit CRUD: `create` (store-first, raises on unique), `get_by_hash(tenant_id, gate_id, value_hash)` for the idempotent re-fetch, `count_for(tenant_id, gate_id)`, `list_by_tenant_gate` (bounded LIMIT), `delete_by_id` (tenant-scoped). Mirrors `repos/gate_categories.py`.
- `backend/app/db/repos/capture_sessions.py` -- thread `cookie_mode: bool = False` through `create_active`, `resolve_for_batch` (same-gate-reuse refresh block, like `special_mode` L184-186), `resolve_for_backfill`.
- `backend/app/api/batches.py` -- after the gate/category resolve (~L159), read `gate_category.cookie_mode` (default False) and pass it into `resolve_for_batch`.
- `backend/app/api/sessions.py` -- `new_session` passes `cookie_mode=active.cookie_mode` to `create_active` (~L372).
- `backend/app/api/gates.py` -- add `cookie_mode: bool` to `PublicGateOut`, set in `gate_to_public_out` from `gate.category.cookie_mode` (category already `selectinload`-ed in `list_active`).
- `backend/app/api/cookies.py` -- NEW client router: POST/GET/DELETE; tenant from `deps.get_current_user`; masks value; `no-store`; in-handler validation.
- `backend/app/api/admin.py` -- add `cookie_mode` to `CreateCategoryRequest`/`UpdateCategoryRequest`/`CategoryOut`/`_category_to_out`/`update_gate_category` (mirror `special_mode`, ~L941-1034).
- `backend/app/errors.py` -- add `invalid_cookie` (400), `gate_not_cookie_mode` (409), `cookie_not_found` (404), `cookie_limit_reached` (409) — Spanish copy, `AppError(status_code, code, message)` signature.
- `backend/app/main.py` -- register the cookies router (~L102-122).
- `frontend/types/api.ts` + `frontend/lib/cookies.ts` -- NEW `CookieOut` type + `cookie_mode` on `GateOut`; hooks `useListCookies`/`useAddCookie`/`useDeleteCookie`.
- `frontend/components/batch/cookie-manager.tsx` -- NEW add/list/delete UI (HeroUI, Spanish, `type="password"` input, masked rows).
- `frontend/components/batch/send-form.tsx` -- render `<CookieManager>` when the selected gate's `cookie_mode` is true and no batch is live.
- `frontend/app/admin/gates/page.tsx` -- `cookie_mode` toggle on the category row + create form, mirroring `special_mode`.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/models.py` -- `cookie_mode` on `GateCategory` + `CaptureSession`; `GateCookie` model with the `(tenant_id, gate_id, value_hash)` unique index and a `status` default `'active'` -- the vault schema + owner flag.
- [x] `backend/migrations/versions/<new>_gate_cookies_vault.py` -- columns + table + index, `down_revision='e1c7a4b9d2f0'` -- ships before restart.
- [x] `backend/app/db/repos/gate_cookies.py` -- flush-not-commit CRUD: store-first create, `get_by_hash`, `count_for`, bounded `list_by_tenant_gate`, tenant-scoped `delete_by_id` -- isolates DB access; the hash, not the value, is the dedup key.
- [x] `backend/app/db/repos/capture_sessions.py` -- thread `cookie_mode` through `create_active`/`resolve_for_batch` (refresh on reuse)/`resolve_for_backfill` -- the snapshot write path (mirror `special_mode`).
- [x] `backend/app/api/batches.py` + `backend/app/api/sessions.py` -- pass `cookie_mode` into the resolve/new-session calls -- without it the snapshot stays `false` forever.
- [x] `backend/app/api/gates.py` -- `cookie_mode` on `PublicGateOut` from the eager-loaded category -- the client UX signal's only data source.
- [x] `backend/app/errors.py` -- four `AppError` factories (Spanish) -- machine codes.
- [x] `backend/app/api/cookies.py` -- client router; tenant from session; canonicalize+validate in-handler (no pydantic value validator); masked output; `no-store`; store-first dedup with rollback→re-fetch→200; cap guard; check gate visibility before cookie_mode -- the vault API.
- [x] `backend/app/api/admin.py` -- `cookie_mode` on category create/update/out (`None` leaves untouched) -- owner marks cookie-mode.
- [x] `backend/app/main.py` -- register the cookies router -- wire it in.
- [x] `backend/tests/test_cookies.py` -- NEW: store→list (value masked, never raw); a ~3000-char value round-trips without 500; `"abc"`/`"abc\n"` dedup to the same id (200); second identical POST returns 200 + SAME id; too-long value → 400 and body excludes the value; foreign-tenant `gate_id` on GET → empty list (== no existence leak); foreign-tenant cookie-mode gate POST → 404 (not 409); non-cookie-mode gate → 409; delete-by-foreign-id → 404; 1-char and 8-char masks reveal no full secret and don't throw; cap reached → 409 -- locks the security invariants.
- [x] `frontend/types/api.ts` + `frontend/lib/cookies.ts` -- `CookieOut` type + `GateOut.cookie_mode` + TanStack hooks -- client data layer.
- [x] `frontend/components/batch/cookie-manager.tsx` + `frontend/components/batch/send-form.tsx` -- manager UI, rendered only for cookie-mode gates when idle -- client cookie management.
- [x] `frontend/app/admin/gates/page.tsx` -- category `cookie_mode` toggle -- owner control.
- [x] frontend build -- `npm run build` (runs `tsc`) -- catches type errors lint misses.

**Acceptance Criteria:**
- Given the migration is head, when `alembic upgrade head` runs on a populated DB, then existing `gate_categories`/`capture_sessions` rows get `cookie_mode = false` and no behavior changes.
- Given an owner toggles `cookie_mode` while a tenant's capture-session is active, then the live session is NOT rewritten and the new value applies only when the tenant's next batch resolves the session (same refresh-on-reuse path as `special_mode`).
- Given the column stores the value plaintext by design (CC precedent), then a reviewer must read this as an intentional Phase-1 decision (encryption deferred), not a missing-encryption bug; no client-facing endpoint (cookies, gates, sessions, snapshot, WS) ever serializes the raw value or `gate.value`. The only new client shape is `CookieOut = {id, label, masked_value, status, created_at}`.
- Given a client stores cookies for a gate, when they later DELETE one and re-list (even after the gate leaves cookie-mode or retires), then the deleted cookie is absent, the rest remain deletable, and no response ever contains the raw value.
- Given Phase 2 is unimplemented, when the vault is in use, then no `send_worker`/`scheduler`/`capture`/Telethon path references `gate_cookies` or `cookie_mode`, and `status` stays `'active'` on every row.

## Spec Change Log

### 2026-06-19 — review loopback (2 bad_spec amended, 3 patches applied)

- **Finding (blind+edge+acceptance, CRITICAL):** the list endpoint shape was underspecified — backend `GET /api/cookies` returned a bare `list[CookieOut]` while the frontend (and the tests) assumed different shapes; the cockpit cookie pane rendered empty forever and the count stuck at 0/50. **Amended:** the list now uses the codebase-universal `{items, total}` envelope (`CookieListResponse`), matching `PublicGateListResponse`/`SessionListResponse`. Backend returns the envelope; tests read `["items"]`; the frontend already expected it. **Known-bad avoided:** a silently non-functional manager that type-checks on both sides yet never shows a stored cookie. **KEEP:** `CookieOut = {id,label,masked_value,status,created_at}` (no raw value), `no-store`, the length-safe masking, bad/foreign `gate_id` → empty list.
- **Finding (blind, MEDIUM):** the per-(tenant,gate) cap ran BEFORE the dedup path, so a client at the 50-cap could not idempotently re-POST an existing cookie (409 instead of the frozen-matrix 200). **Amended:** the cap is enforced AFTER the store-first insert (count the flushed row; `> cap` → rollback + 409) — a duplicate raises `IntegrityError` on flush BEFORE the cap is consulted, so it dedups to 200 even at the cap. This keeps the frozen "store-first / never SELECT-then-INSERT" boundary (no pre-check SELECT). Added a regression test: re-POST an existing value at the cap → 200 + same id. **Known-bad avoided:** the cap silently blocking idempotent re-stores, contradicting the frozen I/O matrix ("cap on the Nth+1 *distinct* value"). **KEEP:** cap = 50; the `value_hash` unique index as dedup arbiter; the `IntegrityError` → rollback → re-fetch → 200 fallback.
- **Patches (applied):** (1) the `IntegrityError`→`existing is None` race branch raises a mapped `cookie_conflict_retry` (409) instead of a bare re-raise → no unmapped 500 (only `AppError` has a handler in `main.py`). (2) `gate_cookies.count_for` uses SQL `func.count()` not `len(scalars().all())`. (3) the two dedup tests' stale NOTE comments (claiming a 201 bug already fixed) were rewritten to the actual 200 behavior.
- **Deferred (not this change):** soft-cap TOCTOU under concurrency (no `FOR UPDATE`; cap scoped as best-effort) and `isprintable()` rejecting internal whitespace (spec-sanctioned for single-line Amazon; revisit for a Phase-2 multi-line format) → `deferred-work.md`.

## Design Notes

The cookie list uses the codebase-standard `{items, total}` envelope (`CookieListResponse`), like every other list endpoint. The per-(tenant,gate) cap (50) is enforced AFTER the store-first insert: a duplicate raises on flush before the cap, so an idempotent re-POST dedups to 200 even at the cap, while a genuinely-new distinct value past the cap is rolled back and 409'd — preserving the store-first / never-SELECT-then-INSERT dedup boundary.

`cookie_mode` is a deliberate clone of `special_mode`: an owner-toggled category boolean, snapshotted onto `CaptureSession` at batch start, refreshed on same-gate reuse, read (later, Phase 2) only from the snapshot. The snapshot is dead weight unless the SAME three files special_mode threaded (`capture_sessions.py`, `batches.py`, `sessions.py`) thread `cookie_mode` too — adding the column alone leaves it pinned at `server_default false` and makes the "applies on next batch" AC untestable.

Unlike `special_mode` (purely server-side), `cookie_mode` is a NEW, deliberate client exposure via `PublicGateOut` — safe because it is a plain UX boolean (when to show the manager), never `gate.value`. Without it `send-form.tsx`'s `GateOut.cookie_mode` is always `undefined` and the manager never renders.

Dedup follows the `uq_responses_session_cc` precedent but corrects its constraint: the CC index truncates to 600 chars precisely because btree rows cap at ~2704 bytes. A cookie can exceed that, so the unique key is `sha256(canonical value)`, with the full value in a Text column. The app rejects > ~2600 canonical chars with 400 `invalid_cookie`, but the hash index — not a length guard — is the dedup source of truth. Canonical = `value.strip()`, applied once before validation and persistence so a pasted trailing newline dedups instead of duplicating.

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` -- expected: applies cleanly, head = new revision.
- `cd backend && .venv/bin/pytest tests/test_cookies.py` -- expected: all pass (masking, dedup, hash round-trip, 404/409 parity, cap).
- `cd frontend && npm run build` -- expected: `tsc` + build succeed with `CookieOut` and the manager.

**Manual checks:**
- `GET /api/cookies` carries `Cache-Control: no-store` and no `value` field.
- After a store, a duplicate store, a too-long store, and a bad-id delete, grep the server/access logs: no cookie value and no rejected value appear anywhere.

## Suggested Review Order

**Vault API — the heart (store path: order is load-bearing)**

- Entry point — store_cookie: validate → gate 404 → cookie-mode 409 → store-first → cap → dedup-200.
  [`cookies.py:164`](../../backend/app/api/cookies.py#L164)
- The load-bearing fix: cap is checked AFTER the store-first insert, so a duplicate dedups even at the cap.
  [`cookies.py:209`](../../backend/app/api/cookies.py#L209)
- List uses the codebase-standard `{items,total}` envelope + `no-store`; bad/foreign gate_id → empty.
  [`cookies.py:243`](../../backend/app/api/cookies.py#L243)
- Length-safe masking — the only window onto the credential; raw value never serialized.
  [`cookies.py:112`](../../backend/app/api/cookies.py#L112)
- Spanish `AppError` codes (incl. the mapped `cookie_conflict_retry` for the race).
  [`errors.py:528`](../../backend/app/errors.py#L528)

**Schema & dedup**

- `GateCookie` + the `(tenant_id, gate_id, value_hash)` unique index (hash, not value, in the btree).
  [`models.py:240`](../../backend/app/db/models.py#L240)
- The migration: two `cookie_mode` columns + `gate_cookies` + index; `down_revision` = real head.
  [`f2b6c9e4a1d8:46`](../../backend/migrations/versions/f2b6c9e4a1d8_gate_cookies_vault.py#L46)
- `count_for` as SQL `COUNT` (runs on every store).
  [`gate_cookies.py:71`](../../backend/app/db/repos/gate_cookies.py#L71)

**cookie_mode snapshot plumbing (clones special_mode)**

- Threaded through create + refresh-on-reuse, mirroring special_mode exactly.
  [`capture_sessions.py:193`](../../backend/app/db/repos/capture_sessions.py#L193)
- Batch start derives the flag from the gate's category and threads it in.
  [`batches.py:165`](../../backend/app/api/batches.py#L165)
- The one deliberate client exposure: a UX boolean on `PublicGateOut`, never `gate.value`.
  [`gates.py:43`](../../backend/app/api/gates.py#L43)

**Frontend (cockpit + owner toggle)**

- The cookie manager — masked rows, add/delete, count vs cap; never renders a raw value.
  [`cookie-manager.tsx:32`](../../frontend/components/batch/cookie-manager.tsx#L32)
- Render guard: only for the selected cookie-mode gate when idle.
  [`send-form.tsx:409`](../../frontend/components/batch/send-form.tsx#L409)
- Owner `cookie_mode` toggle, exact mirror of the special_mode switch.
  [`gates/page.tsx:590`](../../frontend/app/admin/gates/page.tsx#L590)

**Tests (peripheral)**

- Security contract + the cap-after-dedup regression (re-POST existing at cap → 200).
  [`test_cookies.py:476`](../../backend/tests/test_cookies.py#L476)
