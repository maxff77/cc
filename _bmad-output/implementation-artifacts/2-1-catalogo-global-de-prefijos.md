---
baseline_commit: 1349da11d2bdfdc11a2265f59b441812543fdaa3
---

# Story 2.1: Catálogo global de gates (prefijos)

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGY DECISION (owner, 2026-06-11):** the product term for a prefix is now **"gate"** — everywhere: DB, API, code identifiers, and all UI copy. The epics/UX/architecture docs predate this rename and still say "prefijo/prefixes"; where they conflict with this story, **this story wins**. Future stories (2.2 selector, 3.x sessions, UX-DR9 chips) must use "gate". The story key `2-1-catalogo-global-de-prefijos` is kept for sprint-status linkage only.

## Story

As the owner,
I want to manage the global gate catalog (full CRUD),
So that clients only ever pick from approved gates.

## Acceptance Criteria

1. **Given** the schema, **when** the migration for this story is applied, **then** the `gates` table exists (global catalog, gate value stored verbatim with its dot, e.g. `.zo`).
2. **Given** the owner on `/admin/gates`, **when** they create, edit or delete a catalog entry, **then** the change persists and is visible in the table, **and** the empty state shows "El catálogo está vacío."
3. **Given** an admin or client, **when** they request `/admin/gates`, **then** middleware redirects them away — the catalog surface is owner-only.
4. **Given** an authenticated client, **when** the frontend requests the gate catalog API, **then** it returns the catalog entries for use in the gate selector (read-only for clients).
5. **Given** a gate referenced by existing batches or sessions, **when** the owner deletes it from the catalog, **then** it is retired (soft-delete): it disappears from the client selector, but historical batches and sessions keep displaying their gate verbatim.

## Tasks / Subtasks

- [x] Task 1: `Gate` model + Alembic migration (AC: 1, 5)
  - [x] Add `Gate` model to `backend/app/db/models.py`: `id` (int PK), `value` (String(20), verbatim with dot), `deleted_at` (DateTime(timezone=True), nullable — NULL = active), `created_at`, `updated_at` (same `server_default=func.now()` / `onupdate=func.now()` pattern as existing models). **NO `tenant_id` — the catalog is global by design.**
  - [x] Partial unique index on `value` where `deleted_at IS NULL` (uniqueness among active entries only; a retired value can be re-created as a new active row). Name per convention: `uq_gates_value_active`.
  - [x] `alembic revision --autogenerate -m "gates table global catalog"`, review the generated file, confirm `down_revision = "e497cdd16d32"` (current head), verify the partial index is in the migration (autogenerate may miss `postgresql_where` — add manually if needed), `alembic upgrade head`.
- [x] Task 2: repository `backend/app/db/repos/gates.py` (AC: 2, 4, 5)
  - [x] Follow `repos/users.py` idiom: pure ORM, flush not commit, module functions. Mark clearly in module docstring: global catalog, intentionally NOT tenant-scoped.
  - [x] `list_active(session) -> list[Gate]` — `WHERE deleted_at IS NULL ORDER BY value`.
  - [x] `get_by_id(session, gate_id, *, for_update=False) -> Gate | None`.
  - [x] `get_active_by_value(session, value) -> Gate | None` — exact (case-sensitive) match among active rows.
  - [x] `create(session, *, value) -> Gate`.
  - [x] `soft_delete(session, gate) -> None` — set `deleted_at = now(UTC)`; idempotent.
- [x] Task 3: owner-only CRUD API in `backend/app/api/admin.py` (AC: 2, 5)
  - [x] Extend the existing admin router (do NOT create a new admin router). Gate every endpoint with the existing `require_owner` dependency.
  - [x] Inline Pydantic v2 schemas (codebase convention — no separate schemas module): `CreateGateRequest {value}`, `UpdateGateRequest {value}`, `GateOut {id, value, created_at}`, `GateListResponse {items: list[GateOut], total: int}`.
  - [x] Validation (field_validator, shared helper): trimmed, non-empty, no whitespace inside, max 20 chars. Stored verbatim — do NOT strip or require the leading dot; `.zo` is stored as `.zo`.
  - [x] `GET /api/admin/gates` → 200 `GateListResponse` (active entries only).
  - [x] `POST /api/admin/gates` → 201 `GateOut`; duplicate active value → 409 `gate_exists`.
  - [x] `PATCH /api/admin/gates/{gate_id}` → 200 `GateOut`; edits `value`; 404 `gate_not_found` if missing or retired; 409 `gate_exists` on duplicate. Fetch target with `for_update=True` (same lock pattern as `_require_client_target`).
  - [x] `DELETE /api/admin/gates/{gate_id}` → 204; soft-delete only (set `deleted_at`); 404 `gate_not_found` if missing or already retired.
  - [x] Add `gate_exists` and `gate_not_found` factories to `backend/app/errors.py` with Spanish messages ("Ya existe ese gate en el catálogo.", "Ese gate no existe.").
- [x] Task 4: read-only catalog endpoint for authenticated users (AC: 4)
  - [x] New router `backend/app/api/gates.py`: `APIRouter(prefix="/api/gates", tags=["gates"])`, registered in `main.py` next to the other routers.
  - [x] `GET /api/gates` → 200 `GateListResponse` (active only), gated by `get_current_user` (any authenticated, non-expired, non-blocked role — client, admin, owner). This feeds the gate Select of Story 2.2.
- [x] Task 5: middleware owner-only gate (AC: 3)
  - [x] In `frontend/middleware.ts`, after the existing client/admin gate (~line 118-122): if path starts with `/admin/gates` and `me.role !== "owner"` → redirect. Client → `/` (existing behavior); admin → `/admin/users`. No "blocked" screen is ever rendered.
- [x] Task 6: `/admin/gates` page (AC: 2)
  - [x] New `frontend/app/admin/gates/page.tsx`, mirroring the `app/admin/users/page.tsx` pattern: self-contained page, header `<h1>Catálogo de gates</h1>` + "Cerrar sesión" button, HeroUI v3 `Table` (`Table.Content/Header/Column/Body/Row/Cell`), TanStack Query.
  - [x] Cache key `const GATES_KEY = ["admin-gates"] as const;` `useQuery` → `api.get<GateListResponse>("/api/admin/gates")`; mutations via `useMutation` + `queryClient.invalidateQueries({ queryKey: GATES_KEY })`.
  - [x] Table columns: **Gate** (value verbatim with dot, `font-mono text-sm`) · **Creado** (muted, `text-default-500`) · **Acciones** (Editar / Eliminar).
  - [x] Empty state: `renderEmptyState={() => "El catálogo está vacío."}` — exact copy.
  - [x] Create form: `Form` + `TextField` (label "Gate", placeholder ".ej"), button "Crear gate" → "Creando…" while pending. Inline field error for `gate_exists` (use `err.message`), banner `Alert` for other errors, network fallback "No pudimos conectar. Intenta de nuevo." — same pattern as users page.
  - [x] Edit: inline per-row (local `useState` open/close — codebase has NO dedicated Modal component; users page uses inline confirm/forms). Pre-filled value, "Guardar" / "Cancelar".
  - [x] Delete: confirm step "¿Eliminar este gate? ({value})" with danger-styled "Eliminar" — same inline-confirm pattern as `DeleteAdminAction`. Max one confirm layer (UX-DR21: never stack modals).
  - [x] Cross-link nav: add an owner-only "Gates" link in the users page header and a "Usuarios" link in the gates page header (no admin layout exists yet; pages are self-contained).
- [x] Task 7: regenerate API types (AC: 2, 4)
  - [x] With backend running: `npm run generate:api` → updates `frontend/types/api.ts` (GENERATED — never hand-edit).
- [x] Task 8: tests `backend/tests/test_admin_gates.py` (AC: 1-5)
  - [x] Use `ctx` fixture from `conftest.py` (`loop_scope="session"`, self-seeding, self-cleaning). Clean up created gates in teardown (delete rows directly, like `cleanup_users`).
  - [x] Owner creates gate → 201, value verbatim with dot.
  - [x] Duplicate active value → 409 `gate_exists`.
  - [x] Owner edits value → 200, persisted.
  - [x] Owner deletes → 204; entry no longer in `GET /api/admin/gates` nor `GET /api/gates`; row still exists in DB with `deleted_at` set (soft-delete, AC 5).
  - [x] Re-creating a retired value → 201 (partial unique index allows it).
  - [x] Admin → any `/api/admin/gates` endpoint → 403. Client → 403.
  - [x] Client `GET /api/gates` → 200 with active entries only.
  - [x] Validation rejects: empty, whitespace-only, internal whitespace, >20 chars.
- [x] Task 9: gates (all ACs)
  - [x] Backend: `ruff check .`, `mypy app`, `pytest` — all green.
  - [x] Frontend: `npm run lint`, `npx tsc --noEmit`, `next build` — all green.

### Review Findings

- [ ] [Review][Decision] Delete-confirm copy conflicts within the spec — Task 6 mandates "¿Eliminar este gate? ({value})" (what's implemented) but the UX microcopy block marks "¿Eliminar este gate? No se puede deshacer." as exact copy (EXPERIENCE.md). "No se puede deshacer" is also debatable with soft-delete. Owner must pick the final copy.
- [x] [Review][Patch] Concurrent duplicate POST hits `uq_gates_value_active` → unhandled `IntegrityError` → 500 instead of 409 `gate_exists` (check-then-insert TOCTOU; `services/users.py` catches `IntegrityError`, this path doesn't) [backend/app/api/admin.py:399]
- [x] [Review][Patch] Same TOCTOU on PATCH: duplicate check reads without lock; concurrent PATCH/POST toward same value → `IntegrityError` → 500 [backend/app/api/admin.py:427]
- [x] [Review][Patch] 422 responses (`HTTPValidationError`, no `{code,message}`) yield empty `ApiError.message` → falsy → no Alert/FieldError rendered; user gets zero feedback. Add fallback message + basic client-side validation (maxLength, no inner spaces) [frontend/app/admin/gates/page.tsx:151, frontend/lib/api.ts:39]
- [x] [Review][Patch] `_validate_gate_value` accepts invisible chars (U+200B/U+FEFF — not `isspace()`) → visually identical duplicate gates; NUL byte passes validation → asyncpg error → 500 [backend/app/api/admin.py:335]
- [x] [Review][Patch] `gate_id` beyond int32 (e.g. 99999999999999999999) overflows asyncpg bind → `DBAPIError` → 500 instead of 404 [backend/app/api/admin.py:420,436]
- [x] [Review][Patch] `startsWith("/admin/gates")` over-matches siblings (`/admin/gatesfoo` redirects instead of 404); match `/admin/gates` exact or `/admin/gates/` [frontend/middleware.ts:127]
- [x] [Review][Patch] PATCH/DELETE returning 404 `gate_not_found` (deleted in another tab) shows message but doesn't invalidate `GATES_KEY` → ghost row + open editor persist [frontend/app/admin/gates/page.tsx:230,313]
- [x] [Review][Patch] Enter re-submits create form while mutation pending (`isDisabled` only blocks the button) → double POST, spurious `gate_exists` error; guard `onSubmit` with `mutation.isPending` [frontend/app/admin/gates/page.tsx:163]
- [x] [Review][Patch] `api/gates.py` imports private `_gate_to_out` from `api/admin.py` — public router coupled to a `_`-prefixed symbol across modules; make it public or move schema+mapper to a neutral spot [backend/app/api/gates.py]
- [x] [Review][Patch] Delete network fallback says "No pudimos eliminar. Intenta de nuevo." — spec copy is "No pudimos conectar. Intenta de nuevo." (create/edit use the correct one) [frontend/app/admin/gates/page.tsx:317]
- [x] [Review][Defer] Hand-written `GateOut`/`GateListResponse` interfaces in page.tsx vs architecture "never hand-write API types" — same idiom as users page (1.3); fix epic-wide, not per-story [frontend/app/admin/gates/page.tsx] — deferred, pre-existing

## Dev Notes

### Critical context — read before coding

- **Terminology: "gate" everywhere.** DB table `gates`, model `Gate`, endpoints `/api/gates` + `/api/admin/gates`, page `/admin/gates`, UI copy "gate" (masculine: "el gate", "este gate"). Where epics.md/architecture.md/UX docs say "prefijo/prefixes", read "gate" — owner decision 2026-06-11, recorded at the top of this story. Do NOT use "prefix"/"prefijo" in any new identifier or UI string.
- **The catalog is GLOBAL.** No `tenant_id` column, no tenant scoping in the repo. This is the deliberate exception (like admin user management in `repos/users.py`) — owner curates one shared catalog for all tenants. Say so explicitly in the repo docstring, mirroring the "do NOT inject tenant_id" comments from Story 1.3.
- **Soft-delete is new to this codebase** — no `deleted_at`/`is_active` pattern exists yet; this story establishes it. Chosen design: nullable `deleted_at timestamptz` + partial unique index on active rows. Rationale for AC 5: future `batches`/`capture_sessions` (Stories 2.2/3.1) will store the gate **string verbatim** at creation (denormalized), so retiring a catalog entry never rewrites history; the retired row is kept for referential sanity, not joined for display.
- **Editing a gate does not rewrite history** for the same reason — batches will snapshot the string. No cascade concerns in this story (no referencing tables exist yet).
- **Do not enforce a leading dot.** Epic mandates "stored verbatim with its dot, e.g. `.zo`" — verbatim storage, minimal validation (non-empty, no whitespace, ≤20 chars). The dot is data, not format law.
- **No new dependencies.** Everything needed (SQLAlchemy 2.0 async, Alembic, Pydantic v2, HeroUI v3.1.0, TanStack Query v5) is already installed.
- **Naming collision warning:** the codebase already uses "gate" informally for auth role gates (`require_role` deps, middleware "role gate", verification "gates" = ruff/mypy/etc.). Those are unrelated concepts — keep the domain model unambiguous: `Gate` model / `gates` table refer ONLY to the catalog entity. Don't rename auth deps.

### Existing code you will touch (current state)

| File | State today | This story |
| --- | --- | --- |
| `backend/app/db/models.py` | `Tenant`, `User`, `AuthSession`; `Base(DeclarativeBase)` with naming-convention `MetaData`; timestamps via `server_default=func.now()` | ADD `Gate` model |
| `backend/app/api/admin.py` | `/api/admin/users` CRUD; `require_admin_or_owner` / `require_owner` module gates; inline schemas; `AppError` raises; `for_update` lock helper `_require_client_target` (line ~199) | ADD gates endpoints, owner-gated |
| `backend/app/errors.py` | `AppError` factories (`email_taken`, `user_not_found`, …) | ADD `gate_exists`, `gate_not_found` |
| `backend/app/main.py` | registers auth/admin/health routers (~line 44) | ADD gates router |
| `backend/app/api/deps.py` | `get_current_user` (blocks expired/blocked/must-change), `require_role(*roles)` factory | use as-is — no changes |
| `frontend/middleware.ts` | cookie check → `/api/auth/me` → client blocked from `/admin/*` (lines 118-122); expired → `/expired`; forced pw → `/change-password` | ADD owner-only gate for `/admin/gates` |
| `frontend/app/admin/users/page.tsx` | the canonical admin table page: HeroUI Table, TanStack Query, inline forms/confirms, Spanish copy | ADD "Gates" nav link (owner-only, via existing `isOwner`) |
| `frontend/types/api.ts` | GENERATED by `openapi-typescript` | regenerate via `npm run generate:api` |

Migration chain head: `e497cdd16d32_user_must_change_password_flag.py`. New migration's `down_revision` must be `e497cdd16d32`.

### Architecture compliance (non-negotiable)

- English snake_case identifiers backend; generated OpenAPI types frontend — **never hand-write API types**. [Source: planning-artifacts/architecture.md#Enforcement-Guidelines]
- REST: plural nouns — `/api/gates`, `/api/admin/gates`; errors `{code, message}` (snake_case machine code, Spanish user message); success = direct payload, lists = `{items, total}`; **no `{"data": ..., "success": true}` wrappers**. [Source: architecture.md#Format-Patterns]
- Every schema change = an Alembic migration; never mutate schema manually. [Source: architecture.md#Enforcement-Guidelines]
- DB naming: plural snake_case table `gates`; `created_at`/`updated_at` timestamptz UTC; index naming via the metadata convention already in `db/base.py`. [Source: architecture.md#Naming-Patterns]
- Routers never query directly — repo functions only; services layer only if orchestration is needed (this story likely needs none; CRUD goes router → repo, same as the thin paths in admin.py).
- No Telethon, no WS events in this story. Nothing touches `core/telegram` (doesn't exist yet) or `anon.session`.

### UX requirements (from DESIGN.md / EXPERIENCE.md / epics UX-DRs — read "prefijo" as "gate")

- **UX-DR18**: admin surfaces reuse the same theme — HeroUI `Table`, no separate admin styling; responsive = usable on phone, no special mobile design.
- **UX-DR9** (consumed by 2.2, served by this API): gate selector is a HeroUI `Select` over `GET /api/gates` — never free text; chip shows gate verbatim with its dot in mono.
- Gate value is DATA → monospace (`font-mono`); sentences → Public Sans. Existing mono usage example: temp-password display in users page (`className="font-mono text-sm"`).
- **UX-DR21 bans**: free-text gate on client surfaces (admin create form here is the one legitimate text input — it curates the catalog itself), modal stacks >1, per-gate color coding, filler stats.
- Microcopy (Spanish tuteo, exact): empty state **"El catálogo está vacío."** [Source: EXPERIENCE.md line 113]; delete confirm pattern **"¿Eliminar este gate? No se puede deshacer."**; product term is **gate** in all UI text (code identifiers stay English).
- Error copy comes from backend `err.message`; frontend maps known `code`s; network fallback "No pudimos conectar. Intenta de nuevo."

### Previous story intelligence (1.3 — the closest pattern — and 1.7)

- Story 1.3 built `/admin/users` end-to-end; **mirror it file-for-file**: router additions in `api/admin.py`, repo module under `db/repos/`, errors in `errors.py`, page under `app/admin/`, middleware gate, regenerated types, ASGI tests. Its File List is the template for yours.
- Authorization lesson from 1.3: **server-side role deps are the boundary**; frontend role checks (`isOwner`) are cosmetic UX only. Request bodies never carry role/tenant decisions.
- HeroUI v3 is react-aria-components-based: `Table.Content/Header/Column/Body/Row/Cell`, `Form`/`TextField`/`Label`/`Input`/`FieldError`, `Alert`, `Button`. No dedicated Modal in use — inline `useState` confirm/edit rows are the established idiom. Don't introduce a Modal component now.
- TanStack Query conventions from 1.3: array cache keys (`["admin-gates"]`), `invalidateQueries` on mutation success, `staleTime: 30_000` default already configured in `lib/query-client.ts`.
- From 1.7 review: commit conventions are Conventional Commits with scope (`feat(backend,frontend): story 2.1 …` + separate review-fix commit); branch `story/2.1-catalogo-global-de-prefijos`; generated types currently omit error-response schemas (known, deferred — don't fix here).
- Deferred-work check: nothing in `deferred-work.md` touches gates or this story. The Caddy `/ws` exact-match widening belongs to Story 2.2, not here.

### Testing standards

- `pytest` + `pytest-asyncio` (`loop_scope="session"`) + `httpx` `ASGITransport` against the real app and dev Postgres. Tests self-seed (via `seed_user`/`ctx`) and self-clean. No mocking of the DB.
- Follow `test_admin_users.py` shape: one behavior per test, assert status code + response body shape, role-denial tests included.
- Frontend: no test framework exists (decision deferred) — gates are `eslint` + `tsc` + `next build` only. Do not introduce vitest/jest here.

### Project Structure Notes

- New files: `backend/app/db/repos/gates.py`, `backend/app/api/gates.py`, `backend/migrations/versions/<rev>_gates_table_global_catalog.py`, `backend/tests/test_admin_gates.py`, `frontend/app/admin/gates/page.tsx`.
- Modified: `backend/app/db/models.py`, `backend/app/api/admin.py`, `backend/app/errors.py`, `backend/app/main.py`, `frontend/middleware.ts`, `frontend/app/admin/users/page.tsx` (nav link), `frontend/types/api.ts` (regenerated).
- Variance vs architecture tree: architecture lists `api/prefixes.py` and `/admin/prefixes` — superseded by the gate rename (this story's header note). Same shape, new name. Route map UX-DR17's `/admin/prefixes` becomes `/admin/gates`.
- Legacy `core.py`/`app.py`/`auto_sender.py` at repo root are frozen reference — do not modify. **Never read anything under `respuestas/`.**

### References

- [Source: planning-artifacts/epics.md#Story-2.1 — ACs verbatim, with prefijo→gate rename applied per owner decision 2026-06-11]
- [Source: planning-artifacts/epics.md#FR9 — pick from global catalog, never free-text]
- [Source: planning-artifacts/architecture.md#Naming-Patterns, #Format-Patterns, #Structure-Patterns, #Enforcement-Guidelines]
- [Source: planning-artifacts/ux-designs/ux-cc-2026-06-10/DESIGN.md — mono/data typography, chip tokens, admin table theming]
- [Source: planning-artifacts/ux-designs/ux-cc-2026-06-10/EXPERIENCE.md — "El catálogo está vacío.", tuteo voice, confirm-on-delete]
- [Source: implementation-artifacts/1-3-alta-manual-de-clientes-y-gestion-de-roles.md — admin CRUD pattern + File List template]
- [Source: implementation-artifacts/1-7-…md + deferred-work.md — commit/branch conventions, deferred items checked]
- [Source: _bmad-output/project-context.md — 🔒 rules: never read respuestas/, never touch .env values]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Claude Code)

### Debug Log References

- Backend gates: `ruff check .` + `mypy app` clean; `pytest -q` → 61 passed (45 pre-existing + 16 new in `test_admin_gates.py`).
- Frontend gates: `npm run lint` clean (0 errors), `npx tsc --noEmit` clean, `next build` green — `/admin/gates` route present in the build output.
- Migration `64cfd2bc35ff` applied to dev Postgres (`alembic upgrade head`); autogenerate DID emit the partial index (`postgresql_where=sa.text('deleted_at IS NULL')`) — no manual fix needed.

### Completion Notes List

- Story creation: ultimate context engine analysis completed — comprehensive developer guide created (epics, architecture, UX specs, stories 1.3/1.7 intelligence, deferred-work, live codebase state).
- 2026-06-11: owner decision — product term renamed "prefijo" → "gate" across DB/API/code/UI; story rewritten accordingly before dev start.
- Implemented full vertical slice: `Gate` model (soft-delete via nullable `deleted_at` + partial unique index `uq_gates_value_active`), repo `db/repos/gates.py` (global, NOT tenant-scoped — documented), owner-only CRUD on `/api/admin/gates` (GET/POST/PATCH/DELETE, errors `gate_exists` 409 / `gate_not_found` 404), read-only `GET /api/gates` for any authenticated role, middleware owner-gate for `/admin/gates` (admin → `/admin/users`, client → `/` via existing gate), and the `/admin/gates` HeroUI page (create form, mono gate column, inline edit, inline delete confirm, empty state "El catálogo está vacío.").
- PATCH allows a no-op edit (saving the unchanged value): the duplicate check excludes the target row itself.
- 404 is returned for BOTH missing and retired gates — retired entries are invisible to the API, indistinguishable from never-existing.
- Added `api.patch` to `frontend/lib/api.ts` (wrapper had only get/post/delete; first PATCH consumer).
- Cross-links: owner-only "Gates" link in users page header; "Usuarios" link in gates page header (no admin layout exists yet).
- Tests (16): create verbatim/trim, duplicate 409, edit + edit-to-duplicate, soft-delete (row kept with `deleted_at`, hidden from both lists, second delete 404), re-create retired value 201, unknown id 404, admin+client 403 on all admin endpoints, client reads active-only via `/api/gates`, anonymous 401, validation rejects empty/whitespace-only/inner-space/inner-tab/>20 chars (422).

### File List

- backend/app/db/models.py (modified — `Gate` model)
- backend/migrations/versions/64cfd2bc35ff_gates_table_global_catalog.py (new)
- backend/app/db/repos/gates.py (new)
- backend/app/api/admin.py (modified — gate CRUD endpoints + schemas + `_validate_gate_value`)
- backend/app/api/gates.py (new — read-only `/api/gates` router)
- backend/app/errors.py (modified — `gate_exists`, `gate_not_found`)
- backend/app/main.py (modified — register gates router)
- backend/tests/test_admin_gates.py (new — 16 tests)
- frontend/middleware.ts (modified — owner-only gate for `/admin/gates`)
- frontend/app/admin/gates/page.tsx (new)
- frontend/app/admin/users/page.tsx (modified — owner-only "Gates" nav link)
- frontend/lib/api.ts (modified — `api.patch`)
- frontend/types/api.ts (regenerated via `npm run generate:api`)

## Change Log

- 2026-06-11: Story 2.1 implemented — global gate catalog: `gates` table (soft-delete, partial unique index), owner-only CRUD API, read-only catalog API, `/admin/gates` page, middleware owner gate, 16 integration tests. All backend (ruff/mypy/pytest 61) and frontend (eslint/tsc/next build) gates green. Status → review.
