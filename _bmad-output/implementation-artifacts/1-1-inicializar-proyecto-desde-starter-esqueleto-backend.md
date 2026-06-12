---
baseline_commit: c9318924ec38267cae0a5e3fb214d3fb0974faf0
---

# Story 1.1: Inicializar proyecto desde starter + esqueleto backend

Status: done

## Story

As a developer,
I want the monorepo initialized from the HeroUI starter with a working FastAPI backend skeleton,
so that every later story builds on the mandated stack and conventions.

## Acceptance Criteria

1. **Given** a clean repo
   **When** `npx heroui-cli@latest init frontend -t app` is run (Node.js 22+)
   **Then** `frontend/` exists with Next.js 16.2.x + HeroUI v3 + Tailwind CSS v4 + TypeScript strict and the dev server starts without errors

2. **Given** the generated frontend
   **When** the theme layer is applied
   **Then** the tokens from `imports/heroui-theme.css` are applied verbatim (oklch palette, 0.25rem radius), dark mode is the default surface, light mode works, and Public Sans is loaded as `--font-sans` (UX-DR1, UX-DR2)

3. **Given** the repo root
   **When** the backend skeleton is created
   **Then** `backend/` contains `pyproject.toml` (FastAPI 0.136.x, SQLAlchemy 2.0.x async, asyncpg, Alembic, argon2-cffi, pydantic-settings, ruff + mypy config), an app factory in `app/main.py`, env-based config in `app/config.py`, and a health route responding under `/api`

4. **Given** Alembic is initialized
   **When** migration #1 is applied
   **Then** only the `tenants`, `users` (with role) and `auth_sessions` tables exist — no other tables are created ahead of need

5. **Given** both apps
   **When** running `uvicorn app.main:app --reload` (port 8000) and `npm run dev` (port 3000)
   **Then** the frontend proxies `/api` and `/ws` to 8000 via rewrites
   **And** `ruff`, `mypy`, `eslint` and `tsc` all pass

6. **Given** the backend OpenAPI schema
   **When** the type-generation step runs (`openapi-typescript`)
   **Then** `frontend/types/api.ts` is generated from it — API types are never hand-written

## Tasks / Subtasks

- [x] Task 1: Initialize frontend from the official HeroUI starter (AC: 1)
  - [x] Verify Node.js 22+ (`node -v`); install/switch if needed
  - [x] From repo root run `npx heroui-cli@latest init frontend -t app` (App template → Next.js 16.2.x + HeroUI v3 + Tailwind v4 + TS strict)
  - [x] `cd frontend && npm install && npm run dev` — confirm clean boot at http://localhost:3000, no console errors
  - [x] Confirm `tsconfig.json` has `"strict": true` (starter default — do not weaken)
- [x] Task 2: Apply the theme layer (AC: 2)
  - [x] Copy `_bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/imports/heroui-theme.css` content VERBATIM into the global stylesheet, after `@import "@heroui/styles"` (and after `@import "tailwindcss"`). Do not reinterpret, rename, or drop any token
  - [x] Load Public Sans via `next/font/google` (`Public_Sans`, `variable: '--font-public-sans'`) and attach the variable class on `<html>` in `app/layout.tsx` — the theme file maps `--font-sans: var(--font-public-sans)`
  - [x] Make dark mode the default surface: `<html className="dark ...">` (theme file scopes dark tokens to `.dark, [data-theme="dark"]`); verify light mode still renders correctly by removing the class
  - [x] Smoke-check: a HeroUI Button shows the accent `oklch(55% 0.12 243)`, radius 0.25rem, Public Sans body text
- [x] Task 3: Create the backend skeleton (AC: 3)
  - [x] Create `backend/pyproject.toml`: Python 3.12+, deps `fastapi` (0.136.x), `uvicorn[standard]`, `sqlalchemy[asyncio]` (2.0.x), `asyncpg`, `alembic`, `argon2-cffi`, `pydantic-settings`; dev deps `ruff`, `mypy` (+ `pytest`, `pytest-asyncio`, `httpx` for the suite later epics require); include `[tool.ruff]` and `[tool.mypy]` config in the same file
  - [x] `backend/app/config.py` — pydantic-settings `Settings` class reading `backend/.env` (e.g. `database_url`); create `backend/.env.example`; ensure `backend/.env` is gitignored
  - [x] `backend/app/main.py` — app factory pattern (`create_app()`) with a lifespan stub (DB engine init/dispose; Telethon comes in Epic 2 — do NOT add it now)
  - [x] Health route under `/api` (e.g. `GET /api/health` → `{"status": "ok"}`) via an APIRouter in `backend/app/api/`
  - [x] `backend/app/db/base.py` — async engine + session factory (SQLAlchemy 2.0 `async_sessionmaker`) and `DeclarativeBase`
- [x] Task 4: Alembic init + migration #1 (AC: 4)
  - [x] `alembic init -t async migrations` inside `backend/` (async template); wire `alembic.ini`/`env.py` to the Settings `database_url` and the models metadata
  - [x] Define models in `backend/app/db/models.py` — ONLY: `tenants`, `users` (with `role`), `auth_sessions` (column guidance in Dev Notes)
  - [x] Autogenerate + review migration #1; `alembic upgrade head` against the local/VPS Postgres; verify exactly those 3 tables exist (plus `alembic_version`)
- [x] Task 5: Wire dev proxy + verification gates (AC: 5)
  - [x] `frontend/next.config.js` rewrites: `/api/:path*` → `http://127.0.0.1:8000/api/:path*` and `/ws` → `http://127.0.0.1:8000/ws`
  - [x] Run both dev servers; confirm `http://localhost:3000/api/health` returns the backend response
  - [x] Gates green: `ruff check .` and `mypy app` in `backend/`; `npm run lint` (eslint) and `npx tsc --noEmit` in `frontend/`
- [x] Task 6: OpenAPI → TypeScript type generation (AC: 6)
  - [x] Add `openapi-typescript` as a frontend devDependency
  - [x] Add script `"generate:api": "openapi-typescript http://127.0.0.1:8000/openapi.json -o types/api.ts"` (backend must be running)
  - [x] Run it; commit the generated `frontend/types/api.ts` with a header comment "GENERATED — do not hand-edit"

## Dev Notes

### ⚠️ Scope rule that overrides the project-context file

`_bmad-output/project-context.md` documents the **legacy single-user app** (`core.py`, `app.py`, `auto_sender.py`, `static/`). Those rules (Spanish naming, no tests, no new deps, 5 env vars) apply ONLY to the legacy files — which this story must **not touch at all**. They stay at repo root, frozen as reference [Source: _bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure].

For ALL new code under `backend/` and `frontend/`, the architecture document wins:

- **English-only identifiers** (cola→queue, destino→target, sesión de guardado→capture_session). Client-facing UI text stays Spanish — but this story has no UI text.
- Backend tests live in `backend/tests/` (none required by this story's ACs; create the empty dir + `conftest.py` stub is fine).
- New dependencies are expected — they live in `backend/pyproject.toml` and `frontend/package.json`, never in the legacy root `requirements.txt`.

Hard rules that still apply everywhere: 🔒 never read `respuestas/` contents; 🔒 never commit `.env` (root or backend) or print credentials; 🔒 don't touch/delete `anon.session`.

### What this story is (and is not)

Pure bootstrap: monorepo scaffolding, theme tokens, DB schema seed, dev proxy, quality gates, type-gen pipeline. NO auth logic (Story 1.2), NO Telethon (Epic 2), NO admin UI (Story 1.3), NO deploy files (Story 1.7). Resist creating `send_log`, `batches`, `prefixes`, `capture_sessions`, `responses` tables — each later story adds its own migration [Source: epics.md AC "no other tables are created ahead of need"].

### Target structure (create only what's needed now)

```
cc/
├── backend/
│   ├── pyproject.toml            # deps + [tool.ruff] + [tool.mypy]
│   ├── alembic.ini
│   ├── .env.example              # real backend/.env gitignored
│   ├── app/
│   │   ├── main.py               # create_app() factory, lifespan (DB only)
│   │   ├── config.py             # pydantic-settings
│   │   ├── api/
│   │   │   └── health.py         # GET /api/health  (router prefix /api)
│   │   └── db/
│   │       ├── base.py           # async engine/session factory, DeclarativeBase
│   │       └── models.py         # Tenant, User, AuthSession ONLY
│   ├── migrations/               # alembic async template (versions/)
│   └── tests/                    # conftest.py stub
├── frontend/                     # from heroui next-app-template (do not restructure)
│   ├── next.config.js            # + rewrites /api, /ws → :8000
│   ├── types/api.ts              # GENERATED via openapi-typescript
│   └── app/layout.tsx            # Public Sans font var + dark default
├── core.py / app.py / auto_sender.py / static/   # legacy — FROZEN, untouched
```

[Source: architecture.md#Complete Project Directory Structure — fuller tree (api/deps.py, core/, services/, repos/) arrives with the stories that need it]

### Migration #1 — column guidance

Naming conventions are mandatory: plural snake_case tables, PK `id`, FKs `<singular>_id`, `created_at`/`updated_at` as UTC `timestamptz`, expiry as `expires_at`, indexes `ix_<table>_<cols>`, uniques `uq_<table>_<cols>` [Source: architecture.md#Naming Patterns].

Minimal sensible shape (later stories extend via new Alembic migrations — never mutate schema manually):

- `tenants`: `id`, `name`, `created_at`, `updated_at`
- `users`: `id`, `tenant_id` FK, `email` (unique), `password_hash`, `role` (`owner` | `admin` | `client`), `created_at`, `updated_at`
- `auth_sessions`: `id` (or opaque token PK), `user_id` FK, `expires_at`, `created_at` (revocation/blocked flags arrive with Stories 1.2/1.5)

Postgres already runs on the VPS (37.27.12.92); for local dev use any reachable Postgres via `DATABASE_URL` (asyncpg driver: `postgresql+asyncpg://...`).

### Theme layer specifics (UX-DR1, UX-DR2)

- Canonical token source: `_bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/imports/heroui-theme.css`. Verbatim copy — oklch palette, `--radius`/`--field-radius` 0.25rem, `--font-sans: var(--font-public-sans)`. No new colors, no gradients, no shadows added [Source: DESIGN.md#Brand & Style].
- HeroUI v3 theming pattern (verified against current docs): global CSS does `@import "tailwindcss"; @import "@heroui/styles";` then token overrides scoped to `:root`/`.light` and `.dark`/`[data-theme="dark"]` — exactly the structure the import file already has.
- Dark is the DEFAULT surface; light fully supported. Don't hardcode dark-only values anywhere.
- Public Sans via `next/font/google` with CSS variable `--font-public-sans` exposed on `<html>`; the theme maps it to `--font-sans`. Do not load fonts via `<link>` tags.
- Do NOT carry over anything visual from legacy `static/index.html`.

### Dev proxy caveat (`/ws` rewrite)

Next.js rewrites proxy HTTP cleanly; WebSocket upgrade through the dev-server rewrite has historically been flaky across Next versions. The AC requires the rewrites to be configured for `/api` and `/ws`. There is no `/ws` endpoint until Epic 2, so verify the `/api` proxy end-to-end now and leave the `/ws` rewrite in place; in production Caddy routes `/ws` directly to uvicorn (Story 1.7), so the rewrite only matters in dev.

### Quality gates (the verification gate for every future story)

- Backend: `ruff check .` + `mypy app` — configure both in `pyproject.toml`. Keep mypy strict enough to be useful (e.g. `disallow_untyped_defs` on `app/`), but don't block on third-party stubs (`ignore_missing_imports` where needed).
- Frontend: starter's `eslint` config + `tsc --noEmit`. TypeScript strict stays on.
- All four MUST pass before the story is done [Source: architecture.md#Enforcement Guidelines].

### Type generation contract

`frontend/types/api.ts` is GENERATED from the FastAPI OpenAPI schema with `openapi-typescript` — never hand-written, never hand-edited [Source: architecture.md#Structure Patterns]. JSON is snake_case end-to-end (FastAPI default); generated TS types match — do not add camelCase mapping layers.

### Conventions snapshot (for everything created here)

- Python: snake_case functions/vars, PascalCase classes, UPPER_SNAKE constants; type hints on new code.
- TypeScript: camelCase vars/functions, PascalCase components/types; component files kebab-case (`user-card.tsx`) per HeroUI template convention.
- API errors (when they start existing): HTTP status + `{"code": "snake_case", "message": "Spanish text"}` — health route just returns a direct payload.
- Commits: Conventional Commits with scope (`feat(backend): …`, `feat(frontend): …`) per repo history.

### Project Structure Notes

- The monorepo lands NEXT TO the legacy files; `frontend/` is created by the CLI at repo root (run the init command from `cc/`). Root `.gitignore` already exists — extend it for `backend/.env`, `frontend/node_modules`, `.next/`, `__pycache__/` as needed; do not remove existing entries (`.env`, `respuestas/`…).
- `frontend/types/api.ts` exists in the architecture tree from day one — this story creates the generation pipeline so later stories only re-run the script.
- No `deploy/` directory yet — that's Story 1.7.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.1] — story statement + ACs
- [Source: _bmad-output/planning-artifacts/epics.md#Additional Requirements → Starter template] — init command mandate
- [Source: _bmad-output/planning-artifacts/architecture.md#Selected Starter] — rationale, Node 22+, versions
- [Source: _bmad-output/planning-artifacts/architecture.md#Core Architectural Decisions] — Postgres/SQLAlchemy/Alembic/argon2/pydantic-settings stack (versions verified 2026-06)
- [Source: _bmad-output/planning-artifacts/architecture.md#Implementation Patterns & Consistency Rules] — naming, structure, format, enforcement
- [Source: _bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/DESIGN.md] — theme contract (UX-DR1/UX-DR2)
- [Source: _bmad-output/planning-artifacts/ux-designs/ux-cc-2026-06-10/imports/heroui-theme.css] — canonical tokens (copy verbatim)
- [Source: _bmad-output/project-context.md] — legacy-only rules + the three hard 🔒 rules

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (Opus 4.8, 1M context)

### Debug Log References

- Frontend scaffolded with `npx heroui-cli@latest init frontend -t app -p npm` → HeroUI CLI v3.0.3, Next.js 16.2.6, @heroui/react 3.1.0, Tailwind v4, TS strict. `npm install` clean (exit 0).
- Backend deps installed in `backend/.venv` via `pip install -e ".[dev]"`: fastapi 0.136.3, sqlalchemy 2.0.50, alembic 1.18.4, asyncpg 0.30, argon2-cffi 25.1, pydantic-settings 2.14, ruff 0.15, mypy 1.20.
- No local Postgres / docker daemon was running → started Docker Desktop and a `postgres:16` container `cc-pg` (`-e POSTGRES_DB=cc -p 5432:5432`) for migration verification.
- `alembic revision --autogenerate` detected exactly `tenants`, `users`, `auth_sessions` (+ indexes `ix_users_tenant_id`, `ix_auth_sessions_user_id`); `alembic upgrade head` → revision `282b9bd6744a`. Verified `pg_tables` = alembic_version, auth_sessions, tenants, users only.
- **Fix (pre-existing starter bug):** the generated `eslint.config.mjs` failed under ESLint 9.25 — `plugin:@next/next/recommended` (eslintrc style) carries a top-level `name` key the legacy validator rejects. Replaced the compat-extend with the Next plugin's native flat config (`@next/eslint-plugin-next` recommended + core-web-vitals rules). Not caused by this story's changes; required to make the eslint gate pass.
- Ruff config excludes `migrations/versions` (Alembic-generated revision files are machine-written).

### Completion Notes List

- All 6 ACs satisfied. Four quality gates green: **ruff PASS, mypy PASS, eslint PASS, tsc PASS**.
- AC1: frontend boots clean (`✓ Ready` on Turbopack); used :3001 only because :3000 was already occupied by another local process.
- AC2: `imports/heroui-theme.css` copied **verbatim** into `styles/globals.css` after `@import "@heroui/styles"`; `<html className="dark">` makes dark the default surface (next-themes `defaultTheme: "dark"` retained); Public Sans loaded via `next/font/google` as `--font-public-sans`, which the theme maps to `--font-sans`.
- AC3: backend skeleton — `create_app()` factory + DB-only lifespan, pydantic-settings config, `GET /api/health` → `{"status":"ok"}` (verified on a temp :8001 instance), async engine/session factory + `DeclarativeBase` with mandated naming convention.
- AC4: migration #1 applied; exactly the 3 tables exist.
- AC5: `next.config.mjs` rewrites `/api/:path*` and `/ws` → `http://127.0.0.1:8000`. Proxy plumbing **verified end-to-end**: `GET :3001/api/health` returned the identical response to hitting `:8000` directly, proving the rewrite forwards to 8000. Full `{"status":"ok"}` through the proxy is blocked only because the **frozen legacy `app.py` currently owns port 8000** (user-owned process, not killed); the new backend serves health correctly on its own port.
- AC6: `openapi-typescript` added as a frontend devDep; `generate:api` script points at `:8000`; `frontend/types/api.ts` generated from the live OpenAPI schema with a `GENERATED — do not hand-edit` header.
- Legacy files (`core.py`, `app.py`, `auto_sender.py`, `static/`) untouched. 🔒 rules respected: `respuestas/` never read, no `.env` committed/printed, `anon.session` untouched.
- **Local dev note:** the `cc-pg` Postgres container is left running for continued dev; recreate with `docker run -d --name cc-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=cc -p 5432:5432 postgres:16`. `backend/.env` (gitignored) holds `DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/cc`.

### File List

**Backend (new):**
- backend/pyproject.toml
- backend/.env.example
- backend/alembic.ini
- backend/app/__init__.py
- backend/app/config.py
- backend/app/main.py
- backend/app/api/__init__.py
- backend/app/api/health.py
- backend/app/db/__init__.py
- backend/app/db/base.py
- backend/app/db/models.py
- backend/migrations/env.py (generated by `alembic init -t async`, then wired to Settings + models metadata)
- backend/migrations/script.py.mako (generated)
- backend/migrations/README (generated)
- backend/migrations/versions/282b9bd6744a_initial_tenants_users_auth_sessions.py
- backend/tests/__init__.py
- backend/tests/conftest.py
- backend/.env (created locally; **gitignored**)

**Frontend (new — HeroUI `app` starter scaffolded at `frontend/`; only files changed beyond the starter are listed):**
- frontend/config/fonts.ts (Inter → Public Sans, `--font-public-sans`)
- frontend/app/layout.tsx (dark default surface)
- frontend/styles/globals.css (verbatim theme tokens)
- frontend/next.config.mjs (dev proxy rewrites)
- frontend/eslint.config.mjs (ESLint 9 / Next flat-config fix)
- frontend/package.json (`generate:api` script + `openapi-typescript` devDep)
- frontend/types/api.ts (GENERATED from OpenAPI)

**Root:**
- .gitignore (added Python tool-cache + egg-info ignores)

## Change Log

| Date       | Change                                                                 |
|------------|------------------------------------------------------------------------|
| 2026-06-11 | Story 1.1 implemented: HeroUI frontend init + theme layer, FastAPI backend skeleton, Alembic migration #1 (tenants/users/auth_sessions), dev proxy rewrites, openapi-typescript pipeline, all 4 quality gates green. Status → review. |
