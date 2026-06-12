---
baseline_commit: deea2ce078dcbb6d41a1421f44b55b6bde864d86
---

# Story 4.4: Preparación de lanzamiento: gates, backups y runbooks

Status: review

> **⚠️ TERMINOLOGY:** the product term "gate" = a catalog prefix (Story 2.1 rename).
> This story's "gates" are **launch gates** (pre-launch checks) — an unrelated,
> pre-existing epic term. Every artifact disambiguates explicitly ("gate de
> lanzamiento" in docs; no new code identifier uses bare "gate").

## Story

As the owner,
I want the pre-launch gates executed and recovery procedures documented,
So that real clients onboard onto a validated, recoverable service.

## Acceptance Criteria

1. **Given** the staging/production environment, **when** the load test runs,
   **then** `G_min = 3.0s` is validated or adjusted based on real FloodWait
   behavior before onboarding real clients.
2. **Given** real gate (prefix) commands, **when** the attribution volume test
   runs, **then** the bot-always-replies-with-`reply_to` assumption is
   validated at volume, with unmatched replies ≈ 0.
3. **Given** the VPS, **when** the backup cron is installed, **then** `pg_dump`
   runs daily and produces restorable dumps.
4. **Given** the operations docs, **when** the re-auth runbook is written,
   **then** it covers: detect `AuthKeyError` → global pause → re-authenticate
   ON the VPS → explicit resume.
5. **Given** the launch plan, **when** clients onboard, **then** ramp-up is
   gradual over the first weeks (content-pattern ban mitigation) — documented
   as an operating rule.

## Tasks / Subtasks

- [x] Task 1: `G_min` load-test harness + CLI gate (AC: 1)
  - [x] `backend/scripts/load_test_gmin.py`: `FakeGateway` modeling Telegram
        flood control as a sliding-window rate limit (cap per rolling window,
        cooldown, `FloodWaitSimulated` mirroring Telethon's `FloodWaitError`).
  - [x] `ReferenceScheduler` encoding the architecture contract verbatim:
        `P(n)` 10s→20s linear (n=1..5), `G = max(G_min, P(n)/n)`, stable
        round-robin, owner jumps rotation bounded at 50% of slots, paused
        tenants excluded from `n`, governor raises `G_min` on each FloodWait.
  - [x] Virtual-clock simulation loop: FloodWait → sleep + governor raise +
        retry the SAME line (never lost, never duplicated); event cap so a
        stuck policy fails loudly.
  - [x] CLI (`python -m scripts.load_test_gmin`): defaults 8 clients × 100
        lines at `G_min=3.0`; `--json`; exit 0 = gate passed, 1 = raise
        `G_min`. Verified: defaults PASS; `--g-min 1.0` FAILS with governor
        raises (exit 1).
- [x] Task 2: attribution volume-test harness + CLI gate (AC: 2)
  - [x] `backend/scripts/attribution_volume_test.py`: `FakeBot` (configurable
        missing-`reply_to` rate, ❌→✅ edit revisions), `SendRecord` mirroring
        the Story 2.5 `send_log` contract, `Attributor` matching solely on
        `reply_to_msg_id` with per-reply edit dedup and an unmatched bucket
        (never guesses a tenant).
  - [x] Volume run: 50 tenants × 100 lines interleaved round-robin, replies
        shuffled out of order (edits may precede originals). Ground-truth
        tenant carried on each fake reply → cross-tenant attributions counted
        and required to be 0.
  - [x] CLI (`python -m scripts.attribution_volume_test`): `--json`,
        `--missing-reply-to-rate` (demonstrates a failing gate), seeded and
        deterministic; exit 0/1. Verified: defaults PASS (5000/5000 attributed,
        0 unmatched); 2% missing reply_to FAILS (exit 1).
- [x] Task 3: tests for both harnesses (AC: 1, 2)
  - [x] `backend/tests/test_prelaunch.py` — 14 tests, pure simulation (no DB,
        no ASGI): pacing never below `G_min` + zero FloodWaits at 3.0s,
        round-robin fairness + interleaving, per-client cadence inside the
        10–20s band (n=3 ≈ 15s), owner capped at 50% and not starved, paused
        tenant excluded from `n` (gap 4.375s not 4.0s), formula spot-checks
        (`P(n)`, `G_min` floor), unsafe `G_min=1.0` → FloodWaits + governor
        raises + gate fails, flood-waited lines neither lost nor duplicated,
        gateway window/cooldown/recovery unit test; attribution: 5000-send
        volume run fully attributed, zero cross-tenant errors, missing
        `reply_to` → unmatched + gate fails, edits never double-attribute,
        large out-of-order window changes nothing.
- [x] Task 4: daily Postgres backup on the VPS (AC: 3)
  - [x] `deploy/backup_db.sh`: `pg_dump --format=custom` INSIDE the
        `lohari-postgres` container (local socket — no password in script or
        history) → `/var/backups/cc/cc-<UTC>.dump` (root-only, umask 077,
        `.partial` staging so a failed dump never looks like a backup),
        verification via `pg_restore --list`, 14-day retention pruning.
        Env-overridable (`PG_CONTAINER`, `BACKUP_DIR`, `RETENTION_DAYS`, …).
        `bash -n` clean, executable bit set.
  - [x] `deploy/cc-backup.service` (oneshot, root, `After=docker.service`) +
        `deploy/cc-backup.timer` (`OnCalendar=*-*-* 04:30:00 UTC`,
        `Persistent=true`, `RandomizedDelaySec=10m`) — systemd timer, matching
        the cc-core/cc-web unit layout (this VPS schedules via systemd, not
        crontab).
  - [x] `deploy/deploy.sh`: unit-refresh step now also copies
        `cc-backup.{service,timer}` (copying never enables the timer —
        first-time enable is the runbook's job).
  - [x] `deploy/README.md` step 12: install + immediate first run + verify
        (`list-timers`, dump present mode 600), linking the restore runbook.
- [x] Task 5: re-auth runbook (AC: 4)
  - [x] `docs/runbooks/reauth-telegram.md` — full cycle the AC mandates:
        **detect** (`journalctl` grep for `AuthKeyError`/deauthorization,
        watchdog 4.1 pointer) → **global pause** (watchdog auto-pause when
        deployed; `systemctl stop cc-core` hard fallback) → **re-authenticate
        ON the VPS** (move dead session aside — closes the 1.7-deferred
        corrupt-session gap —, `sudo -u cc … -m scripts.telegram_auth`,
        verify `-rw------- cc cc`) → **explicit owner resume** (never
        automatic). Includes single-owner-rule cause analysis and
        post-incident steps.
- [x] Task 6: ops runbooks + launch plan (AC: 1, 2, 3, 5)
  - [x] `docs/runbooks/backups-y-restauracion.md` — weekly verification,
        monthly restore drill into a scratch DB (with `alembic_version`
        check), disaster restore (stop → drop/create → `pg_restore` → smoke),
        off-site copy note, container-rename caveat.
  - [x] `docs/runbooks/gates-de-lanzamiento.md` — the two-layer model per
        gate: Layer A simulation (repo scripts, automated) / Layer B real
        environment (manual, owner-only, never from a dev machine against the
        production account); pass criteria (0 FloodWaits sustained ≥ 30 min;
        unmatched ≈ 0 over ≥ 500 real commands across every catalog gate),
        adjust-`G_min` failure path via the governor's suggested value,
        sign-off checklist + result tables.
  - [x] `docs/runbooks/plan-de-lanzamiento.md` — AC5 operating rule: phased
        ramp-up (1–2 pilot → ≤5 → ≤10 with admission control → +~5/week),
        advance conditions (0 FloodWait alerts, unmatched ≈ 0, no watchdog
        trips), rollback rule, daily monitoring list. Content-pattern ban
        rationale stated.
  - [x] `docs/runbooks/README.md` — index + the gate-terminology
        disambiguation note.
- [x] Task 7: verification gates
  - [x] Backend: `ruff check app/ tests/ scripts/` clean;
        `pytest tests/test_prelaunch.py` 14/14 green; both CLIs smoke-run
        (pass and fail paths, exit codes verified). Full suite at merge time
        (shared dev Postgres, parallel-dev constraint).
  - [x] `bash -n deploy/backup_db.sh` clean; systemd units visually reviewed
        against cc-core/cc-web conventions (no systemd on macOS).
  - [x] Frontend untouched — no `npm run lint` needed (no UI surface in this
        story's ACs).

## Owner manual actions (explicitly out of automated scope)

These AC parts require a human on the VPS / against real Telegram and are
documented, not executed:

- **AC1 Layer B:** run the real load test on staging/production
  (`gates-de-lanzamiento.md` Gate 1B) once Story 2.4's scheduler is deployed;
  adopt the governor-suggested `G_min` if it fails.
- **AC2 Layer B:** run the real-command attribution volume test
  (Gate 2B) once Stories 2.5 + 3.1 are deployed.
- **AC3 install:** execute `deploy/README.md` step 12 ON the VPS (enable the
  timer, verify the first dump) and run the restore drill once.
- **AC5 execution:** the ramp-up rule is followed by the owner during
  onboarding — the document is the deliverable.

## Dev Notes

### Critical context

- **Parallel-dev reality:** at this story's baseline (`deea2ce`), stories
  2.2–4.3 are NOT merged — there is no production scheduler
  (`app/services/` has only auth/plans/users), no `send_log`, no watchdog.
  The epic's pre-launch gates assume those exist. Resolution: the harnesses
  encode the **architecture contract itself** (the same one 2.4/2.5/3.1 must
  implement) as `ReferenceScheduler`/`Attributor`, so the gate machinery and
  its assertions ship and run NOW; the runbook's Layer B binds them to the
  real components once deployed. **Seam:** when 2.4 lands, point
  `run_load_test` at the real scheduling policy (anything implementing
  `next_sender()`/`global_interval()` over `SimSender` state) and keep the
  assertions.
- **Never execute against production/Telegram from dev.** The scripts are
  pure simulation (virtual clock, fake gateway/bot, no network, no DB). The
  real-environment validation is owner-manual by design — same posture as
  1.7's "manual verification: pending VPS execution".
- **Architecture formula (verbatim source of truth):** `P(n)` linear 10s
  (n=1) → 20s (n≥5); `G = max(G_min, P(n)/n)`; paused tenants excluded from
  `n`; owner jumps rotation, ≤ 50% of slots; governor auto-raises `G_min` on
  FloodWait; FloodWait retries the same line. Fake-gateway flood model:
  22 msgs / rolling 60s — `G_min=3.0` sustains 20/min (passes with margin),
  2.0s pacing fails; the threshold is a CLI knob, not a claim about
  Telegram's real limits (that's what Layer B measures).
- **Backup is a systemd timer, not a crontab line** — deliberate: the VPS
  layout (1.7) manages everything as systemd units (`cc-core`, `cc-web`);
  `Persistent=true` beats cron for missed runs; logs land in journald.
  pg_dump runs inside the `lohari-postgres` container over the local socket
  (the official postgres image trusts local connections) so no password is
  needed — the script never touches `backend/.env`.
- **Custom-format dumps** (`--format=custom`): compressed, TOC-verifiable
  (`pg_restore --list` catches truncation/corruption on every run), selective
  restore. The dump includes `alembic_version`, so a restore is
  schema-consistent; runbook covers the dump-older-than-code case
  (`alembic upgrade head` after restore).
- **Terminology collision** ("gate" = catalog prefix since 2.1): all docs say
  "gate de lanzamiento" explicitly and the index README carries the
  disambiguation note; test file is `test_prelaunch.py` (not "gates") to keep
  grep-distance from `test_admin_gates.py`.

### Existing code/docs this story builds on

| Artifact | State today | This story |
| --- | --- | --- |
| `deploy/` (1.7) | Caddyfile, cc-core/cc-web units, deploy.sh, README runbook | ADD backup script + 2 units, EXTEND deploy.sh unit refresh + README step 12 |
| `backend/scripts/` | bootstrap_owner, seed_user, telegram_auth (run-once CLIs, module docstrings, `python -m scripts.X`) | ADD the two gate harnesses, same idiom |
| `backend/scripts/telegram_auth.py` | interactive re-auth, idempotent, chmod 600 | referenced by the re-auth runbook (its corrupt-session gap — 1.7 deferred finding — is closed by the runbook's move-aside step) |
| `backend/tests/` | pytest + ASGI/DB integration style | ADD `test_prelaunch.py` (pure simulation — deliberately no DB: nothing here has a schema) |
| `docs/` | did not exist | NEW `docs/runbooks/` (architecture defers runbooks to ops; 1.7 pointed "full runbook arrives with Story 4.4" here) |

- Migration chain head observed: `62f6cc07f7b0` (gate name label). **This
  story adds NO migration** (no schema).
- `_bmad-output/project-context.md` documents the legacy single-user app —
  its Spanish-naming/no-deps rules apply to legacy files only (scope rule
  inherited from 1.1–1.7). Hard 🔒 rules apply everywhere: never read
  `respuestas/`, never touch `.env` values or `anon.session`.

### Testing standards

- New tests are plain sync pytest (no `asyncio` marker needed — strict mode
  stays untouched), one behavior per test, docstrings, deterministic seeds.
  They import the harnesses as `scripts.*` (importable because
  `backend/tests/__init__.py` makes pytest insert `backend/` on `sys.path`).
- `ruff check app/ tests/ scripts/` and `pytest tests/test_prelaunch.py` run
  green standalone — no DB requirement, immune to the shared-Postgres churn.

### References

- [Source: planning-artifacts/epics.md#Story-4.4 — ACs verbatim]
- [Source: planning-artifacts/architecture.md#Gap-Analysis-Results — adaptive
  formula, governor, assumption audit (A1 reply_to, G_min=3.0s), pre-launch
  gates + backup cron as named open items]
- [Source: implementation-artifacts/1-7-…md — VPS layout (systemd, Dockerized
  Postgres `lohari-postgres`, `/var/lib/cc/anon.session`), re-auth script,
  deferred corrupt-session finding]
- [Source: implementation-artifacts/2-1-…md — gate rename decision]
- [Source: deploy/README.md — first-deploy runbook this story extends]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Claude Code)

### Debug Log References

- `ruff check app/ tests/ scripts/` → clean.
- `pytest tests/test_prelaunch.py` → 14 passed in 0.04s (no DB needed).
- CLI smoke: `scripts.load_test_gmin` defaults → GATE PASSED exit 0 (800
  sends, min gap 3.000s, 0 FloodWaits); `--g-min 1.0 --clients 10` → exit 1
  (FloodWaits + governor raises). `scripts.attribution_volume_test` defaults
  → GATE PASSED exit 0 (5000 attributed, 698 edit revisions, 0 unmatched);
  `--missing-reply-to-rate 0.02` → exit 1.
- `bash -n deploy/backup_db.sh` → clean; executable bit set.
- Full backend suite NOT run here (shared dev Postgres with a concurrent
  test run — parallel-dev constraint); runs at merge time. The new test
  module touches no DB, so contention cannot affect it.

### Completion Notes List

- Both pre-launch gates ship as runnable, deterministic simulation harnesses
  with CLI exit codes (0 pass / 1 fail) + 14 tests; the real-environment
  Layer B is documented as owner-manual procedure with pass criteria and
  result tables (see "Owner manual actions").
- Backup: systemd timer (04:30 UTC daily, `Persistent=true`) → custom-format
  pg_dump inside the postgres container, TOC-verified, root-only, 14-day
  retention; restore drill + disaster restore documented.
- Re-auth runbook covers the AC's full cycle (detect → pause → re-auth ON
  VPS → explicit resume) and closes 1.7's deferred corrupt-session-file gap
  (move-aside step) at the docs level.
- Launch plan documents the gradual ramp-up operating rule (phases, advance
  conditions, rollback rule) per AC5.
- No migration, no frontend changes, no new backend deps, nothing executed
  against the VPS or Telegram.

### File List

- backend/scripts/load_test_gmin.py (new — G_min load-test harness + CLI)
- backend/scripts/attribution_volume_test.py (new — attribution volume harness + CLI)
- backend/tests/test_prelaunch.py (new — 14 tests)
- deploy/backup_db.sh (new, executable — daily pg_dump + verify + retention)
- deploy/cc-backup.service (new — oneshot backup unit)
- deploy/cc-backup.timer (new — daily 04:30 UTC, Persistent)
- deploy/deploy.sh (modified — refresh backup units too)
- deploy/README.md (modified — step 12: daily backups)
- docs/runbooks/README.md (new — index + terminology note)
- docs/runbooks/reauth-telegram.md (new — AC4 runbook)
- docs/runbooks/backups-y-restauracion.md (new — verification/drill/disaster restore)
- docs/runbooks/gates-de-lanzamiento.md (new — AC1/AC2 procedures + checklist)
- docs/runbooks/plan-de-lanzamiento.md (new — AC5 ramp-up operating rule)
- _bmad-output/implementation-artifacts/4-4-preparacion-de-lanzamiento-gates-backups-y-runbooks.md (new — this story file)
- _bmad-output/implementation-artifacts/sprint-status.yaml (modified — 4-4 → review, epic-4 → in-progress)

## Change Log

- 2026-06-12: Story 4.4 implemented — pre-launch gate harnesses
  (`load_test_gmin`, `attribution_volume_test`) + 14 tests, daily pg_dump
  backup (script + systemd timer + deploy integration), `docs/runbooks/`
  (re-auth, backups/restore, launch gates, ramp-up plan). Owner manual
  actions documented (Layer B real-environment runs, VPS timer install,
  restore drill, ramp-up execution). ruff + new tests green. Status → review.
