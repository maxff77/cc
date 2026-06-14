---
title: 'Configurable send interval — owner-editable runtime floor'
type: 'feature'
created: '2026-06-14'
status: 'done'
baseline_commit: '177d110280b4907a217c7f2bd90e2e52919b00c7'
context: ['{project-root}/CLAUDE.md', '{project-root}/_bmad-output/implementation-artifacts/spec-flat-send-interval.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The constant send interval (the scheduler floor `g_min`, default 4.0s) is hard-coded in `settings.scheduler_g_min_seconds`, so changing the cadence of the shared Telegram account needs a code/`.env` edit + redeploy. The owner wants to tune it live from the UI.

**Approach:** Promote the interval to a hot-configurable, durable `system_settings` row (mirroring the `max_active_senders` admission cap): an owner-only `GET/PUT /api/admin/interval`, persisted across restarts, applied live to the scheduler singleton via a new `set_floor()`, with a server-enforced safe range of **2–30s**. The FloodWait governor, decay, global window, round-robin and owner priority are untouched — only the *source* of the floor changes (env constant → runtime field).

## Boundaries & Constraints

**Always:** Owner-only (`require_owner`) — a client/admin can never lower the shared-account cadence. Enforce the **2.0–30.0s** range server-side (`AppError` on out-of-range). Persist in `system_settings` (key `send_interval_seconds`, string seconds); default when absent/garbage = `settings.scheduler_g_min_seconds` (4.0). Apply live via `scheduler.set_floor(v)` with **no worker wake** — pacing stays wake-immune (a control never sends *faster* mid-sleep; the new floor lands on the next `interval()` call). The governor's decay must converge to the **runtime** floor, not the env constant. `note_flood_wait`, `flood_remaining`, `pick_next` and the governor math stay byte-for-byte unchanged. One scheduler singleton.

**Ask First:** Changing the 2s/30s bounds. Letting a non-owner *edit* it (read-only display is fine). Adding a worker wake to apply instantly (breaks wake-immunity).

**Never:** Deriving the interval from a *batch/send request* — FR12 holds (owner admin action only). Reintroducing the `P(n)/n` band. A new env var or Alembic migration (`system_settings` exists — just a new key). Touching legacy `core.py`/`app.py`/`static/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Owner sets valid | `PUT {interval_seconds: 6.0}`, owner | Row persisted; `scheduler.set_floor(6.0)`; subsequent `interval()` → `6.0` | N/A |
| Owner reads, no row | `GET`, empty `system_settings` | `{interval_seconds: 4.0}` (env default) | N/A |
| Below floor | `PUT {interval_seconds: 1.5}` | Rejected, value unchanged | 400 `invalid_send_interval` |
| Above ceiling | `PUT {interval_seconds: 45}` | Rejected, value unchanged | 400 `invalid_send_interval` |
| Non-owner write | `PUT` as admin/client | Rejected, value unchanged | 403 (`require_owner`) |
| Boot with persisted | row `send_interval_seconds=8.0` at startup | `scheduler.set_floor(8.0)` applied **before** the worker starts | falls back to 4.0 on parse failure |
| FloodWait then quiet, floor=2.0 | governor raised, then 600s windows | `g_min` decays ×÷1.5 toward **2.0** (runtime floor), never below | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/core/scheduler.py` -- add `self._floor` (init from `settings.scheduler_g_min_seconds`), a `set_floor(seconds)` setter, and a `floor` property; `_maybe_decay` reads `self._floor` instead of `settings.scheduler_g_min_seconds` (line 123). Governor/`pick_next`/`interval` signature unchanged.
- `backend/app/services/pacing.py` -- **NEW**, mirrors `services/admission.py`: `INTERVAL_KEY="send_interval_seconds"`, `INTERVAL_MIN=2.0`, `INTERVAL_MAX=30.0`, `_parse_interval(raw)->float|None`, `get_interval(session)->float` (parsed-or-default), `set_interval(session, v)` (flush), `apply_persisted(session)` → `scheduler.set_floor(...)` when a valid row exists.
- `backend/app/api/admin.py` -- `IntervalOut{interval_seconds:float}`, `UpdateIntervalRequest{interval_seconds:float}`; `GET/PUT /api/admin/interval` under `require_owner`; PUT bounds-checks → `invalid_send_interval()`, `set_interval` + `commit`, then `scheduler.set_floor(v)` (no `send_worker.wake()`).
- `backend/app/errors.py` -- `invalid_send_interval()` → 400, code `invalid_send_interval`, Spanish message ("Indica un intervalo entre 2 y 30 segundos.").
- `backend/app/main.py` -- in lifespan, after `watchdog.load_persisted()` and **before** `run_worker()`, call `pacing.apply_persisted(boot_db)` (reuse a boot session).
- `frontend/app/admin/users/page.tsx` -- `SendIntervalCard()` sibling of `AdmissionControlCard`, rendered `{isOwner && …}`; `useQuery(["admin-interval"])` → `GET /api/admin/interval`; `useMutation` PUT; `TextField type=number` (step 0.5) labeled "Intervalo de envío (s)", client-validate 2–30, `setQueryData` on success; copy warns lowering raises ban risk.
- `backend/tests/test_scheduler.py` -- `set_floor` tests (see AC).
- `backend/tests/test_admin_interval.py` -- **NEW**, mirrors the admission endpoint tests: parse bounds, GET default, PUT happy/out-of-range, owner-only.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/core/scheduler.py` -- add `_floor`, `set_floor()`, `floor` property; point `_maybe_decay` floor at `self._floor`. Leave governor/window/`pick_next`/`interval` math unchanged.
- [x] `backend/app/services/pacing.py` -- new service per Code Map; defensive `_parse_interval` (None on missing/garbage/out-of-range).
- [x] `backend/app/errors.py` -- `invalid_send_interval()`.
- [x] `backend/app/api/admin.py` -- GET/PUT `/api/admin/interval`, `require_owner`, bounds → error, set+commit+`set_floor`, no wake.
- [x] `backend/app/main.py` -- apply persisted interval at boot before the worker starts.
- [x] `frontend/app/admin/users/page.tsx` -- `SendIntervalCard` owner-only control mirroring `AdmissionControlCard`.
- [x] `backend/tests/test_scheduler.py` + `backend/tests/test_admin_interval.py` -- unit-test the matrix + `set_floor` semantics.

**Acceptance Criteria:**
- Given the owner PUTs a valid interval, when a subsequent send is paced, then `scheduler.interval(1)` returns the new value live (no restart).
- Given `set_floor(F)` with no active FloodWait, when called, then `g_min` snaps to `F` (raise or lower); given an active FloodWait elevation, then `g_min` stays at `max(elevated, F)` and later decays toward `F`, never below it.
- Given a persisted interval row, when the app boots, then the scheduler floor equals the persisted value before the first send.
- Given the full backend suite, when `pytest` runs, then it is green — existing `4.0` default assertions still hold (default unchanged).

## Design Notes

`g_min` is both the floor *and* the live value (governor raises it, decay returns it). `set_floor` re-baselines without fighting an active FloodWait:
```python
def set_floor(self, seconds: float) -> None:
    self._floor = seconds
    if self._last_flood_at is None:        # steady state: snap to the floor
        self._g_min = min(seconds, _G_MIN_CEIL)
    else:                                   # mid-flood: keep any elevation
        self._g_min = min(max(self._g_min, seconds), _G_MIN_CEIL)
```
This renegotiates `spec-flat-send-interval`'s "server-config only (FR12)": the interval is still never derived from a send request — only owner-configurable via a guarded endpoint, with the 2s floor preserving ban protection.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest` -- expected: full suite green (scheduler set_floor, admin interval, plus untouched governor/prelaunch tests).
- `cd backend && .venv/bin/ruff check . && .venv/bin/mypy app` -- expected: clean.
- `cd frontend && npm run lint` -- expected: clean.

**Manual checks:**
- As owner, set the interval to 6s in `/admin/users`; confirm the value persists on reload and the owner dashboard's live `g_min` reflects pacing. As a client, confirm the control is absent.

## Suggested Review Order

**The floor mechanism (design intent)**

- Entry point — the live setter that re-baselines without fighting the FloodWait governor.
  [`scheduler.py:87`](../../backend/app/core/scheduler.py#L87)
- The critical wiring: decay now converges to the runtime floor, not the env constant.
  [`scheduler.py:150`](../../backend/app/core/scheduler.py#L150)

**Persistence & boundaries**

- The service mirroring the admission-cap precedent: parse-or-default, set, boot apply.
  [`pacing.py:29`](../../backend/app/services/pacing.py#L29)
- Owner-only PUT: bounds + isfinite guard, then commit, then live `set_floor` — no worker wake.
  [`admin.py:697`](../../backend/app/api/admin.py#L697)
- Boot restore runs before the worker starts, so a restart preserves the cadence.
  [`main.py:63`](../../backend/app/main.py#L63)
- The Spanish error code for an out-of-range value.
  [`errors.py:377`](../../backend/app/errors.py#L377)

**UI binding**

- Owner-only card mirroring `AdmissionControlCard`; copy warns lowering raises ban risk.
  [`page.tsx:526`](../../frontend/app/admin/users/page.tsx#L526)

**Tests (supporting)**

- Proof the decay returns to the configured floor (2.0), never the env 4.0.
  [`test_scheduler.py:146`](../../backend/tests/test_scheduler.py#L146)
- Live-apply + persistence round-trip, plus the NaN/Inf guard.
  [`test_admin_interval.py:78`](../../backend/tests/test_admin_interval.py#L78)
