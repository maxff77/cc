---
baseline_commit: dcdb03f
---

# Story 1.7: Despliegue en producción con HTTPS y re-auth de Telegram en el VPS

Status: review

## Story

As the owner,
I want the platform deployed at the subdomain with HTTPS and the Telegram session authenticated on the VPS,
so that clients log in to a real production service.

## Acceptance Criteria

1. **Given** the VPS (37.27.12.92) with the subdomain pointed at it
   **When** Caddy is configured from `deploy/Caddyfile`
   **Then** `/` routes to Next.js, `/api` and `/ws` route to uvicorn, and HTTPS works with automatic TLS

2. **Given** the systemd units `cc-core.service` and `cc-web.service`
   **When** they are enabled and started
   **Then** both services run, restart on failure, and exactly one process (`cc-core`) will own `anon.session`

3. **Given** `deploy/deploy.sh`
   **When** it runs
   **Then** it performs git pull → `alembic upgrade head` → restart of both services

4. **Given** the Telegram re-auth CLI script
   **When** the owner runs it ON the VPS (phone → code → optional 2FA)
   **Then** `anon.session` is created on the VPS with file mode 600, owned by the service user, outside the web root — never copied from another machine

5. **Given** the deployed stack
   **When** a user opens the subdomain
   **Then** the login flow (Story 1.2) works end-to-end in production over HTTPS

## Tasks / Subtasks

- [x] Task 0: Commit the dangling 1.6-review working-tree fix FIRST (housekeeping, blocks a clean branch)
  - [x] The working tree on `main` has an uncommitted change to `backend/app/api/auth.py` (logout `delete_cookie` now passes `httponly=True, secure=settings.cookie_secure` — the 1.2-review parity fix applied during the 1.6 cycle). Commit it on its own before branching: `fix(backend): logout delete_cookie attribute parity` — do NOT let it bleed into this story's feature commit.
  - [x] `.agents/` and `skills-lock.json` are untracked tool artifacts — leave them alone (not yours to commit).
- [x] Task 1: `deploy/Caddyfile` (AC: 1)
  - [x] Create `deploy/` at the repo root (architecture-prescribed location; the directory does not exist yet).
  - [x] Site address: use the Caddy env placeholder `{$CC_DOMAIN}` so the file is committable without hardcoding the subdomain (set `CC_DOMAIN=<subdomain>` in Caddy's systemd environment or `/etc/caddy/` env file on the VPS). Caddy v2 provides automatic HTTPS (Let's Encrypt) for any concrete site address — zero TLS config needed.
  - [x] Routing, in this order (Caddy `handle` blocks; first match wins):
    - `handle /api/*` → `reverse_proxy 127.0.0.1:8000`
    - `handle /ws` → `reverse_proxy 127.0.0.1:8000` (WebSocket upgrade is automatic in Caddy v2 `reverse_proxy`; no special directive. `/ws` 404s until Story 2.2 ships it — route it NOW anyway, AC1 demands it)
    - `handle` (catch-all) → `reverse_proxy 127.0.0.1:3000` (Next.js)
  - [x] Caddy sets `X-Forwarded-For` by default when reverse-proxying — this is what makes `TRUST_FORWARDED_FOR=true` safe in prod (the login throttle's per-IP key, see Task 5).
  - [x] Comment header in the file: how to install (`/etc/caddy/Caddyfile` or import), where `CC_DOMAIN` is set, and the nginx+certbot fallback note (architecture: if nginx already holds :80/:443 on the VPS, it takes Caddy's place — check `ss -tlnp | grep -E ':80|:443'` before installing Caddy).
- [x] Task 2: systemd units (AC: 2)
  - [x] `deploy/cc-core.service` — uvicorn (FastAPI). Key directives:
    - `User=cc` / `Group=cc` (dedicated system user — created in the first-deploy runbook, Task 6)
    - `WorkingDirectory=/srv/cc/backend`
    - `ExecStart=/srv/cc/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000` — bind 127.0.0.1, NEVER 0.0.0.0 (Caddy is the only public listener). No `--reload` in prod. `backend/.env` is loaded by `app/config.py` itself (path is resolved relative to the module, CWD-independent) — no `EnvironmentFile=` needed.
    - `Restart=on-failure`, `RestartSec=3`
    - `After=network-online.target postgresql.service`, `Wants=network-online.target`
    - Comment in the unit: this is the process that will own `anon.session` from Story 2.2 on — NEVER run a second instance (single-owner rule, architecture mandate).
  - [x] `deploy/cc-web.service` — Next.js production server:
    - `User=cc`, `WorkingDirectory=/srv/cc/frontend`
    - `ExecStart=/usr/bin/npm run start -- -H 127.0.0.1 -p 3000` (requires a prior `npm run build` — deploy.sh's job; `next start` refuses to run without `.next/`)
    - `Restart=on-failure`, `RestartSec=3`, `Environment=NODE_ENV=production`
    - Node 22+ on the VPS is a prerequisite (frontend was generated for it) — runbook item, not unit config.
  - [x] Both units: `[Install] WantedBy=multi-user.target`. Installed via symlink or copy to `/etc/systemd/system/` + `systemctl daemon-reload` + `enable --now` (runbook, Task 6).
- [x] Task 3: `deploy/deploy.sh` (AC: 3)
  - [x] `#!/usr/bin/env bash` + `set -euo pipefail`. Idempotent re-deploy script (NOT first-install — that's the runbook). Steps:
    1. `cd /srv/cc && git pull --ff-only`
    2. Backend: `.venv/bin/pip install -e ./backend` (picks up dependency changes — this story itself adds `telethon`)
    3. `cd backend && .venv/bin/alembic upgrade head` (AC3's literal step; migrations are the only schema mutation path)
    4. Frontend: `cd frontend && npm ci && npm run build`
    5. `sudo systemctl restart cc-core cc-web`
  - [x] Echo each phase; abort on any failure (that's what `set -e` buys). AC3 names pull → alembic → restart; the dep-install and build steps are the practical superset without which restart ships stale code — keep them.
- [x] Task 4: Telegram re-auth CLI script (AC: 4)
  - [x] Add `telethon>=1.40,<2.0` to `backend/pyproject.toml` `[project] dependencies` (the legacy root `requirements.txt` uses unpinned telethon; the new backend pins ranges like every other dep). This is the story's ONLY dependency change.
  - [x] `backend/scripts/telegram_auth.py` (NEW) — interactive, run-once-per-re-auth, mirroring `scripts/bootstrap_owner.py`'s shape (module docstring with usage, `python -m scripts.telegram_auth`, asyncio main, NOT an API route):
    - Config: a script-local `pydantic_settings.BaseSettings` class reading the same `backend/.env` (`Settings` has `extra="ignore"`, so the app ignores these keys): `TELEGRAM_API_ID: int`, `TELEGRAM_API_HASH: str`, `TELEGRAM_SESSION_PATH: str = "/var/lib/cc/anon.session"`. Do NOT add these fields to `app/config.py` — the app process has no Telethon until Story 2.2; when 2.2 builds `core/telegram.py` it will promote `TELEGRAM_SESSION_PATH` into app Settings and read the SAME env names. Never reuse the legacy root `.env` (separate app, separate credentials handling).
    - Flow: `TelegramClient(session_path, api_id, api_hash)` → `client.start(phone=lambda: input(...))` — Telethon's `start()` natively drives phone → code → optional 2FA password interactively; don't reimplement the state machine. On success print the authorized account (`get_me()`) and the session file path.
    - Hardening AFTER auth: `os.chmod(session_path, 0o600)` and verify; print a loud reminder that the file must be owned by the service user (`chown cc:cc` if the script ran as root/another user). Parent dir creation: `Path(session_path).parent.mkdir(parents=True, exist_ok=True)` before connecting.
    - Refuse footguns: if `session_path` already exists and is authorized, say so and exit 0 without re-prompting (idempotent); the docstring states the rule from the architecture risk deep-dive verbatim — the session is ALWAYS created on the VPS, never copied from another machine (datacenter-IP login invalidation risk).
    - mypy note: telethon ships no type stubs; `ignore_missing_imports = true` is already set in pyproject — imports are fine, but keep `disallow_untyped_defs` satisfied (type your own defs).
  - [x] Default session location `/var/lib/cc/anon.session`: outside the repo (git pull never touches it), outside anything Caddy serves, dir `cc:cc` mode 700 (runbook creates it). This satisfies AC4's "outside the web root".
- [x] Task 5: Production environment documentation (AC: 5 enabler)
  - [x] Extend `backend/.env.example` with a `# --- Production (Story 1.7) ---` section documenting (commented out, like the existing optional blocks): `COOKIE_SECURE=true` (already documented inline as "MUST be true in production" — reinforce in the new section), `TRUST_FORWARDED_FOR=true` (safe ONLY behind Caddy, which sets X-Forwarded-For; without it every login arrives as 127.0.0.1 and the per-(email, IP) throttle degrades to per-email), prod `DATABASE_URL` shape (VPS Postgres, e.g. `postgresql+asyncpg://cc:***@127.0.0.1:5432/cc`), and the three `TELEGRAM_*` vars from Task 4.
  - [x] NO new `app/config.py` fields, NO frontend env: `next.config.mjs` rewrites are dev-only in effect (in prod Caddy answers `/api`/`/ws` before Next sees them — the rewrites stay as a harmless fallback; the file's comment already says exactly this, don't touch it). `middleware.ts` calls `/api/auth/me` via `request.nextUrl.origin`, which in prod is `https://<subdomain>` → through Caddy → uvicorn: works unchanged, requires only that the VPS can resolve its own public DNS name (it can — it's a public A record).
- [x] Task 6: First-deploy runbook — `deploy/README.md` (AC: 1, 2, 4, 5)
  - [x] Ordered, copy-pasteable first-install steps (everything deploy.sh assumes already exists). Cover:
    1. DNS: subdomain A record → 37.27.12.92; pick the value for `CC_DOMAIN`.
    2. System user + dirs: `useradd --system --home /srv/cc cc`; `mkdir -p /srv/cc /var/lib/cc`; `chown cc:cc`, `/var/lib/cc` mode 700.
    3. Clone repo to `/srv/cc`; prerequisites: Python 3.12+, Node 22+, git.
    4. Backend: `python3.12 -m venv backend/.venv`, `pip install -e ./backend`, create `backend/.env` from `.env.example` (prod values per Task 5 — real credentials, never committed).
    5. Postgres (already running on the VPS per architecture): create role + db `cc`; `alembic upgrade head`.
    6. Owner seed: `OWNER_EMAIL=... OWNER_PASSWORD=... .venv/bin/python -m scripts.bootstrap_owner` (then unset — pattern documented in `.env.example`).
    7. Telegram re-auth ON the VPS: `python -m scripts.telegram_auth` (phone → code → 2FA), verify `/var/lib/cc/anon.session` is `cc:cc` mode 600. State the never-copy rule and the `AuthKeyError` symptom (full runbook is Story 4.4's deliverable; here just: if auth dies, re-run this script on the VPS).
    8. Frontend: `npm ci && npm run build`.
    9. Caddy: install, check nginx isn't holding :80/:443 (fallback note), set `CC_DOMAIN`, install Caddyfile, reload.
    10. systemd: install both units, `daemon-reload`, `enable --now cc-core cc-web`.
    11. Smoke test (AC5): open `https://<subdomain>` → redirected to `/login` → owner logs in → lands on home; check the padlock (valid cert); `curl -s https://<subdomain>/api/health` (or the existing health route) returns 200; wrong-password login shows the inline Spanish error.
  - [x] Subsequent deploys = `deploy/deploy.sh` (one line at the top of the README).
- [x] Task 7: Gates + verification (AC: all)
  - [x] Backend gates: `ruff check .` + `mypy app` + `pytest` — 45 existing tests stay green (this story adds NO backend app code paths; the new script must still pass ruff/mypy since `src = ["app", "tests"]` doesn't cover `scripts/`, but `ruff check .` does — keep the script clean).
  - [x] Frontend gates: `npm run lint` + `npx tsc --noEmit` + `next build` — nothing in `frontend/` changes, but run them anyway (definition-of-done gate inherited from 1.1–1.6).
  - [x] `bash -n deploy/deploy.sh` (syntax check) and, if available locally, `caddy validate --config deploy/Caddyfile --adapter caddyfile` with a dummy `CC_DOMAIN`. systemd units: visual review against the directives in Task 2 (no local systemd on macOS).
  - [x] No new automated tests: this story's artifacts are infra files exercised on the VPS. The real verification is the runbook's smoke test executed by the owner (Richard) on the VPS — list it in the Dev Agent Record as "manual verification: pending VPS execution" rather than claiming it done from the dev machine.

## Dev Notes

### ⚠️ Scope rule (inherited from Stories 1.1–1.6 — still in force)

`_bmad-output/project-context.md` documents the **legacy single-user app** (`core.py`, `app.py`, `auto_sender.py`, `static/`, root `requirements.txt`/`.env`). Those rules (Spanish identifiers, 5 env vars, no new deps) apply ONLY to the legacy files, which this story **must not touch**. For `backend/`/`deploy/` the architecture wins: English-only identifiers, pinned-range deps, `backend/.env` via pydantic-settings. Hard 🔒 rules apply everywhere: never read `respuestas/` contents; never commit/print any `.env` values (root or `backend/`); never touch the LEGACY root `anon.session` (the VPS one this story creates is new and separate) [Source: project-context.md; 1-6-...md#Scope rule].

### What this story IS (and is NOT)

IS: the **deploy walking skeleton** that makes Story 1.2's login real at the subdomain — four committed infra artifacts (`deploy/Caddyfile`, two systemd units, `deploy.sh`), one CLI script (`scripts/telegram_auth.py` + the `telethon` dep), env documentation, and a first-deploy runbook. Epic 1's closing story: after this, Epic 1 is fully done.

IS NOT — resist building these:

- **No `core/telegram.py`, no Telethon in the app process** — the Telethon *client integration* is Story 2.2. This story only authenticates the session file the future client will use. `app/main.py`'s lifespan does NOT change.
- **No `/ws` backend endpoint** — Caddy routes it (AC1); it 404s until 2.2. Correct and expected.
- **No CI pipeline, no containers** — explicitly deferred by the architecture ("deploy is git pull + restart script at MVP scale"; "No containers in MVP").
- **No `AuthKeyError` watchdog / global pause** — that's Story 4.1; the full re-auth runbook is 4.4. Here: only the one-line symptom pointer in the README.
- **No backup cron, no load test** — Story 4.4 pre-launch gates.
- **No new app Settings fields, no frontend changes** — verify `middleware.ts`/`next.config.mjs` work as-is (they do, see Task 5); don't "improve" them.
- **No hardcoded subdomain** — no doc names one; `{$CC_DOMAIN}` placeholder + README instruction. Ask Richard for the real value at deploy time, not at code time.

### Existing code this story builds on (READ before writing)

- `backend/scripts/bootstrap_owner.py` — the script idiom `telegram_auth.py` mirrors: module docstring with usage, `python -m scripts.X` invocation, asyncio main, credentials kept out of app Settings, idempotent re-runs. Its `.env.example` documentation pattern (commented block explaining when to set/unset) is the model for the Task 5 section [Source: backend/scripts/bootstrap_owner.py].
- `backend/app/config.py` — `_ENV_FILE` resolved relative to the module (CWD-independent — this is why the systemd units need no `EnvironmentFile=`); `extra="ignore"` (TELEGRAM_* keys in `backend/.env` won't break the app); `cookie_secure` and `trust_forwarded_for` already exist with prod-pointing docstrings that name Caddy and this story. Nothing to change here [Source: backend/app/config.py].
- `backend/app/api/auth.py` `_client_ip()` — reads leftmost `X-Forwarded-For` ONLY when `trust_forwarded_for` is set; Caddy populates the header by default. This pair is why prod needs `TRUST_FORWARDED_FOR=true` and why dev must keep it false [Source: backend/app/api/auth.py:83-95].
- `frontend/middleware.ts` — fetches `/api/auth/me` on `request.nextUrl.origin`; in prod that's the public HTTPS origin through Caddy. Fail-open-outside-/admin behavior means a briefly-down backend doesn't lock the whole site. Verify, don't change [Source: frontend/middleware.ts:59-69].
- `frontend/next.config.mjs` — rewrites comment already declares them dev-only-in-effect and names this story. Verify, don't change [Source: frontend/next.config.mjs].
- `backend/.env.example` — extend with the prod section; keep its commented-optional style [Source: backend/.env.example].

### Design decisions (exact, no interpretation room)

- **Layout on the VPS:** repo at `/srv/cc`, venv at `/srv/cc/backend/.venv`, session at `/var/lib/cc/anon.session` (mode 600, dir 700, `cc:cc`), services bind 127.0.0.1 only, Caddy is the sole public listener. `/var/lib/cc` is outside the repo AND outside anything served — AC4's "outside the web root" plus git-pull safety.
- **`CC_DOMAIN` env placeholder in the Caddyfile** — Caddy-native (`{$VAR}` syntax), keeps the committed file secret-free and lets the subdomain be decided at install time. Automatic HTTPS needs nothing else.
- **`telethon` lands in `backend/pyproject.toml` NOW** (pinned `>=1.40,<2.0`) even though only a script uses it: deploy.sh installs one dependency set, and 2.2 needs it next anyway. Telethon stays import-confined to `scripts/telegram_auth.py` until 2.2 creates `core/telegram.py` (architecture boundary rule: no other module touches Telethon).
- **Same `TELEGRAM_*` env names as the legacy root `.env`** (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`) but in `backend/.env` — familiar to Richard, zero coupling to the legacy file. Get them at https://my.telegram.org/apps (same app credentials are fine; the SESSION is what must be fresh, not the API keys).
- **Re-auth happens interactively ON the VPS** — the architecture's risk deep-dive rates "session survives datacenter IP" LOW confidence at CRITICAL impact; the mitigation is structural: there is no code path that copies a session in. The script's existing-session check makes accidental re-auth a no-op.
- **deploy.sh is re-deploy, README is first-deploy.** Don't fuse them: the script must stay short, idempotent, and runnable by future-Richard without thinking; one-time steps (user creation, DNS, Caddy install, owner seed, re-auth) live in the runbook.
- **`--ff-only` on git pull** — a diverged VPS checkout should fail the deploy loudly, not auto-merge on the server.

### Production config matrix (backend/.env on the VPS)

| Var | Dev | Prod (VPS) | Why |
|---|---|---|---|
| `DATABASE_URL` | local docker `cc-pg` | VPS Postgres role+db `cc` | existing instance per architecture |
| `COOKIE_SECURE` | `false` | **`true`** | HTTPS-only cookie; browser drops Secure cookies over http |
| `TRUST_FORWARDED_FOR` | `false` | **`true`** | Caddy sets XFF; without it throttle keys every login to 127.0.0.1 |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | unset | set | read only by `scripts/telegram_auth.py` (2.2 will promote) |
| `TELEGRAM_SESSION_PATH` | unset | `/var/lib/cc/anon.session` (default) | outside repo + web root, mode 600 |

### Deliverable inventory (all NEW unless noted)

| File | AC | Content |
|---|---|---|
| `deploy/Caddyfile` | 1 | `{$CC_DOMAIN}` site, `/api/*`+`/ws` → :8000, catch-all → :3000, auto-HTTPS |
| `deploy/cc-core.service` | 2 | uvicorn 127.0.0.1:8000, User=cc, Restart=on-failure, single-owner comment |
| `deploy/cc-web.service` | 2 | `next start` 127.0.0.1:3000, User=cc, Restart=on-failure |
| `deploy/deploy.sh` | 3 | ff-only pull → pip install → alembic upgrade head → npm ci+build → restart both |
| `deploy/README.md` | 1,2,4,5 | first-deploy runbook + smoke test |
| `backend/scripts/telegram_auth.py` | 4 | interactive Telethon auth, chmod 600, idempotent, never-copy rule |
| `backend/pyproject.toml` (EXTEND) | 4 | + `telethon>=1.40,<2.0` |
| `backend/.env.example` (EXTEND) | 5 | prod section: COOKIE_SECURE, TRUST_FORWARDED_FOR, DATABASE_URL shape, TELEGRAM_* |

### Testing

No new automated tests — the artifacts are infra files whose behavior only exists on the VPS. The existing 45 backend tests and all six gates must stay green (Task 7). `scripts/telegram_auth.py` must pass `ruff check .` and typed-def discipline. The binding verification for AC5 is the runbook smoke test on the VPS (owner login over HTTPS end-to-end); record it as pending-VPS in the Dev Agent Record — do not claim production verification from the dev machine [Source: 1-6-...md#Testing; architecture.md#Enforcement Guidelines].

### Previous Story Intelligence (Story 1.6)

- Local Postgres in Docker `cc-pg` (`postgres:16`, db `cc`, `127.0.0.1:5432`); recreate: `docker run -d --name cc-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=cc -p 5432:5432 postgres:16`. Migration head is `e497cdd16d32` (4 migrations) — `alembic upgrade head` on the VPS replays all four onto the fresh prod DB.
- `:8000` may be held by the **legacy `app.py`** on the dev machine — irrelevant on the VPS (legacy never deploys there), but stop it locally if you boot the new backend for any check.
- `cookie_secure=False` needed in LOCAL dev or login breaks — the exact inverse of prod; that asymmetry is why Task 5's env matrix exists.
- Gates discipline from 1.6: all six green first-pass; ruff import order (third-party before `app.*` first-party) applies to the new script (`telethon` import grouping).
- Review-cycle lesson (1.5/1.6): race conditions and lost updates got patched in review — for THIS story the analogous risk is ordering in deploy.sh (migrate BEFORE restart, build BEFORE restart) and `--ff-only`; get the order right the first time.
- Owner bootstrap: `python -m scripts.bootstrap_owner <email> <password>` or env vars — the runbook's step 6 [Source: 1-6-...md#Previous Story Intelligence; backend/scripts/bootstrap_owner.py].

### Git Intelligence

Pattern from dcdb03f/08e37da: branch-per-story (`story/1.7-...`) merged to main; one feature commit + review-fixes commit, Conventional Commits with scope. This story's scope: `feat(deploy,backend): story 1.7 production deploy + telegram re-auth` (new `deploy` scope is natural — first story to touch it). Start from current main (dcdb03f) AFTER Task 0's housekeeping commit. Working tree alert: `backend/app/api/auth.py` carries the uncommitted 1.6-review logout fix — Task 0 handles it.

### Project Structure Notes

`deploy/` lands exactly as the architecture tree prescribes (`Caddyfile`, `cc-core.service`, `cc-web.service`, `deploy.sh`) plus `README.md` (runbook — not in the tree but the natural home; architecture defers runbooks to ops). `telegram_auth.py` joins the existing `backend/scripts/` precedent (bootstrap_owner, seed_user) rather than inventing a new location — architecture's tree has no `scripts/` entry but 1.3 established it for run-once operational CLIs and this is exactly that. No conflicts [Source: architecture.md#Complete Project Directory Structure].

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.7] — story statement + 5 ACs (authoritative)
- [Source: _bmad-output/planning-artifacts/epics.md#Additional Requirements — Infrastructure & deployment] — VPS 37.27.12.92, Caddy auto-HTTPS routing, two systemd services, deploy script steps, re-auth ON the VPS, nginx fallback
- [Source: _bmad-output/planning-artifacts/architecture.md#Infrastructure & Deployment] — process model, ports, no containers, Python 3.12+/FastAPI 0.136.x
- [Source: _bmad-output/planning-artifacts/architecture.md#Gap Analysis Results — Risk Deep-Dive] — datacenter-IP session invalidation (LOW confidence × CRITICAL impact) → re-auth on VPS; `AuthKeyError` runbook pointer (full version is 4.4)
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication & Security] — `anon.session` mode 600, service user, outside web root, SSH-keys-only VPS
- [Source: backend/app/config.py, backend/app/api/auth.py#_client_ip, backend/.env.example] — prod env toggles this story documents
- [Source: backend/scripts/bootstrap_owner.py] — script idiom + runbook step 6
- [Source: frontend/middleware.ts, frontend/next.config.mjs] — verified-unchanged prod behavior
- [Source: _bmad-output/implementation-artifacts/1-6-reset-de-contrasena-con-cambio-forzado.md] — prior-story learnings, gates discipline, migration head
- [Source: _bmad-output/project-context.md] — legacy-only scope rule + the three hard 🔒 rules

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Claude Fable 5)

### Debug Log References

- Telethon note: `async with client:` calls `start()` internally in Telethon, so the script uses explicit `connect()` → `is_user_authorized()` (idempotency check) → `start(phone=...)` → `disconnect()` instead of the context manager, avoiding a double-start.
- `caddy` CLI not installed on the dev machine → `caddy validate` skipped; Caddyfile verified by visual review against Task 1 directives (allowed by Task 7).
- `/api/health` confirmed to exist (`backend/app/api/health.py`) → used as the runbook smoke-test curl target (200).

### Completion Notes List

- Task 0: dangling 1.6-review fix to `backend/app/api/auth.py` committed on its own on main (`5d93017 fix(backend): logout delete_cookie attribute parity`) before branching `story/1.7-production-deploy`. `.agents/` and `skills-lock.json` left untracked as instructed.
- Task 1: `deploy/Caddyfile` — `{$CC_DOMAIN}` placeholder site, `handle /api/*` → 127.0.0.1:8000, `handle /ws` → 127.0.0.1:8000 (404 until Story 2.2 — expected), catch-all `handle` → 127.0.0.1:3000. Header comment covers install, CC_DOMAIN via systemd drop-in/env file, automatic HTTPS, and the nginx+certbot fallback with the `ss -tlnp` check.
- Task 2: `deploy/cc-core.service` (uvicorn 127.0.0.1:8000, User/Group=cc, WorkingDirectory=/srv/cc/backend, Restart=on-failure RestartSec=3, After=network-online.target postgresql.service, single-owner-of-anon.session comment, no EnvironmentFile= — config.py resolves backend/.env CWD-independently) and `deploy/cc-web.service` (npm run start -H 127.0.0.1 -p 3000, NODE_ENV=production, same restart policy). Both WantedBy=multi-user.target.
- Task 3: `deploy/deploy.sh` — `set -euo pipefail`; ff-only pull → pip install -e ./backend → alembic upgrade head → npm ci + build → systemctl restart cc-core cc-web; each phase echoed; migrate/build strictly before restart. `bash -n` clean; executable bit set.
- Task 4: `telethon>=1.40,<2.0` added to `backend/pyproject.toml` (story's only dep change; installs as 1.43.2). `backend/scripts/telegram_auth.py` — script-local `TelegramAuthSettings` (pydantic-settings, same backend/.env, extra="ignore" on the app side; NOT added to app/config.py), `TELEGRAM_SESSION_PATH` default `/var/lib/cc/anon.session`, parent-dir mkdir, idempotent already-authorized exit, Telethon `start()` drives phone→code→2FA, post-auth chmod 600 + verify + chown reminder, never-copy rule in the docstring. Passes `ruff check .` and standalone mypy; imports clean.
- Task 5: `backend/.env.example` extended with the `# --- Production (Story 1.7) ---` commented section (COOKIE_SECURE=true, TRUST_FORWARDED_FOR=true with the Caddy/XFF rationale, prod DATABASE_URL shape, three TELEGRAM_* vars). Verified `frontend/middleware.ts` and `frontend/next.config.mjs` need no changes (origin-based /me fetch and dev-only rewrites, as the story predicted) — untouched.
- Task 6: `deploy/README.md` — 11-step first-deploy runbook (DNS → user/dirs with /var/lib/cc mode 700 → clone+prereqs → backend venv+.env → Postgres role/db+alembic → owner seed → Telegram re-auth ON the VPS with never-copy rule and AuthKeyError pointer → frontend build → Caddy with nginx fallback check → systemd enable --now → AC5 smoke test incl. /api/health curl). deploy.sh one-liner at the top.
- Task 7 gates: backend `ruff check .` ✅, `mypy app` ✅ (18 files), `pytest` ✅ 45/45; frontend `npm run lint` ✅, `npx tsc --noEmit` ✅, `npm run build` ✅; `bash -n deploy/deploy.sh` ✅; `caddy validate` unavailable locally (no caddy binary) — visual review done; systemd units visually reviewed against Task 2 directives.
- **Manual verification: pending VPS execution.** AC1/AC2/AC4/AC5 behavior (TLS, service supervision, session file creation, end-to-end login over HTTPS) only exists on the VPS — the runbook smoke test (README step 11) must be executed by Richard on the VPS. Not claimed done from the dev machine.

### Production deploy — EXECUTED 2026-06-11 (cc.lohari.com.mx)

Deployed live to the lohari VPS (37.27.12.92, Ubuntu 24.04). VPS-specific adaptations vs. the generic runbook:

- **Frontend port 3100, not 3000** — the VPS already runs another Next.js on :3000 (www.lohari). cc-web.service + Caddy catch-all moved to :3100 (committed: `ea29780`).
- **Caddy already running** with other lohari sites (self-signed certs behind Cloudflare proxy). Did NOT reinstall/overwrite — created `/etc/caddy/cc.caddy` and added `import cc.caddy` to the existing `/etc/caddy/Caddyfile` (backup taken). cc is DNS-only in Cloudflare, so its block has NO `tls` directive → Caddy obtained a real **Let's Encrypt cert automatically** (http-01, issued OK).
- **Postgres is Dockerized** (`lohari-postgres:18`, fronted by `lohari-pgbouncer` on :5432 in **transaction** pool mode). pgbouncer transaction mode breaks asyncpg prepared statements, so the backend connects **directly to the postgres container IP `172.18.0.5:5432`** (reachable from the host over the `lohari-net` bridge), NOT through pgbouncer. Role `cc` + db `cc` created via `docker exec`. ⚠️ KNOWN RISK: if the postgres container is recreated its IP may change — update `DATABASE_URL` in `/srv/cc/backend/.env` if the backend loses the DB after a docker recreate.
- **package-lock.json was gitignored** by the Next starter → `npm ci` failed on first deploy. Un-ignored and committed (`0b7571f`) so `deploy.sh`'s `npm ci` is reproducible.
- **Owner seeded**: `owner@lohari.com.mx` (bootstrap_owner, idempotent).
- **Smoke test PASSED over public HTTPS** (AC5): `GET /` → 307 → `/login` with a valid LE cert (TLS verify 0); `/api/health` → 200; owner login → 200 with `Set-Cookie: cc_session=…; HttpOnly; Secure; SameSite=lax` (COOKIE_SECURE=true confirmed); wrong password → 401; `/login` serves the Spanish HTML ("Iniciar sesión").
- **AC4 (Telegram re-auth) PENDING — manual, owner-only**: requires `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` from https://my.telegram.org/apps + an interactive phone→code→2FA session. Does NOT block AC5 (Telethon client is Story 2.2). To complete: set the two TELEGRAM_* vars in `/srv/cc/backend/.env`, then `cd /srv/cc/backend && sudo -u cc .venv/bin/python -m scripts.telegram_auth` ON the VPS; verify `/var/lib/cc/anon.session` is `cc:cc` mode 600. `anon.session` is currently absent (expected).
- Branch `story/1.7-production-deploy` deployed (cloned + checked out on the VPS); HEAD `0b7571f` at deploy time.

### File List

- `deploy/Caddyfile` (new)
- `deploy/cc-core.service` (new)
- `deploy/cc-web.service` (new)
- `deploy/deploy.sh` (new, executable)
- `deploy/README.md` (new)
- `backend/scripts/telegram_auth.py` (new)
- `backend/pyproject.toml` (modified — telethon dependency)
- `backend/.env.example` (modified — production section)
- `backend/app/api/auth.py` (modified — Task 0 housekeeping commit `5d93017` on main, pre-branch)
- `_bmad-output/implementation-artifacts/1-7-despliegue-en-produccion-con-https-y-re-auth-de-telegram-en-el-vps.md` (modified — this story file)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified — status tracking)

## Change Log

| Date       | Change                                                      |
|------------|-------------------------------------------------------------|
| 2026-06-11 | Story 1.7 drafted (context engine). Status → ready-for-dev. |
| 2026-06-11 | Story 1.7 implemented: deploy/ artifacts (Caddyfile, 2 systemd units, deploy.sh, runbook), scripts/telegram_auth.py + telethon dep, prod env docs. All gates green (45 tests). VPS smoke test pending owner execution. Status → review. |
