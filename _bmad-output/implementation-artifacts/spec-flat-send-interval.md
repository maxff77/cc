---
title: 'Flat send interval — constant 4s global, round-robin turns'
type: 'refactor'
created: '2026-06-13'
status: 'done'
baseline_commit: 'c1ebd4ba7d3d333b678e55d41413d31c22293755'
context: ['{project-root}/CLAUDE.md', '{project-root}/_bmad-output/project-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Owner wants the shared account to fire at a **constant 4s** cadence and let round-robin rotate turns across active clients ("el bot constantemente cada 4s, turnándose entre clientes"). Today `scheduler.interval()` uses the adaptive band `G = max(g_min, P(n)/n)` (P(n) 10→20s), so with few clients the system is SLOW (n=1 → 10s) and only floors near the configured `g_min` when busy — the opposite feel of what's wanted.

**Approach:** Drop the per-client band. Make `interval()` return the governed floor `g_min` flat (ignoring `n`), and raise the default `g_min` 3.0 → **4.0s** (the hard floor the owner specified). The account then sends one line every 4s constant; "more clients = each one slower" falls out of round-robin for free (each client's turn = G×n). The FloodWait governor, global flood window, decay, round-robin and owner priority are unchanged — they remain the real ban protection.

## Boundaries & Constraints

**Always:** Keep the FloodWait governor (`note_flood_wait` ×1.5, ceil 30, decay 1 step / 600s), the global flood window (`flood_remaining`), and `pick_next` (round-robin + bounded owner priority) **byte-for-byte unchanged**. `interval()` keeps its `(n)` signature for caller compat (n simply no longer affects the result). The interval stays server-config only (FR12) — never request-derived. `eta_seconds` math (`queued × n_eff × interval`) stays correct as-is (turn = n×G).

**Ask First:** Changing the 4.0s floor to any other value; touching `pick_next`/priority logic; making the interval re-adapt to `n` in any form (that would reintroduce the band this spec removes).

**Never:** Reintroducing the 10–20s band or `P(n)`. Adding a new env var. Editing `epics.md` (frozen historical artifact — superseded by this spec). Touching the legacy root `core.py`/`app.py`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Single client | `interval(1)`, no FloodWait, default config | `4.0` | N/A |
| Many clients | `interval(5)` (or any n), no FloodWait | `4.0` (flat — n ignored) | N/A |
| Defensive n | `interval(0)` | `4.0` (no div-by-zero, no special-case) | N/A |
| Post-FloodWait | `note_flood_wait(s)` then `interval(7)` | `6.0` (4.0 ×1.5), decays 1 step/600s back toward 4.0, never below | N/A |
| Per-client cadence | n clients, round-robin, steady state | global gap ≈4s; each client served ≈ every 4×n s | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/core/scheduler.py` -- `interval()` returns governed `g_min` flat; remove `_P_BASE/_P_CAP/_P_SLOPE/_target_per_client`; update module + `interval` docstrings. Governor/decay/window/`pick_next` untouched.
- `backend/app/config.py:65` -- `scheduler_g_min_seconds` default `3.0` → `4.0`; comment now says the floor IS the effective interval.
- `backend/.env.example:53` -- comment update (constant 4s floor, no band).
- `backend/app/core/send_worker.py:7,~848` -- docstring + inline comment: interval is the constant governed floor, no longer `P(n)/n` "recomputed every turn".
- `backend/app/services/batches.py:42` -- `eta_seconds` logic unchanged; tighten docstring (turn = n×G, G now flat).
- `backend/tests/test_scheduler.py` -- flat truth table, governor base 4.0, drop `_target_per_client` import+test.
- `backend/tests/test_observability.py:171,212,318` -- governor g_min math rebased 3.0 → 4.0.
- `backend/scripts/load_test_gmin.py` -- `ReferenceScheduler.global_interval` flat (`= g_min`); retire `per_client_target` band.
- `backend/tests/test_prelaunch.py` -- flat cadence/avg_gap/global_interval expectations (band tests rewritten).
- `_bmad-output/planning-artifacts/architecture.md:408` -- amend FR13 capacity math to the flat model (band superseded).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/core/scheduler.py` -- `interval(n)` → `self._maybe_decay(); return self._g_min`; delete band constants + `_target_per_client`; rewrite module/`interval` docstrings to "constant governed floor". Leave governor, `flood_remaining`, decay, `pick_next` exactly as-is.
- [x] `backend/app/config.py` -- default `4.0`; comment: floor is now the effective constant interval (still self-tunes up on FloodWait).
- [x] `backend/.env.example` -- comment reflects constant 4s, no adaptive band.
- [x] `backend/app/core/send_worker.py` -- fix the two docstring/comment spots that describe `G = max(g_min, P(n)/n)` adaptive-per-turn.
- [x] `backend/app/services/batches.py` -- docstring tweak only; verify ETA still reads `queued × n_eff × interval(n_eff)`.
- [x] `backend/tests/test_scheduler.py` -- truth table all `4.0`; `interval(n)*n == 4.0*n`; governor `g_min` 4.0→6.0→9.0…ceil 30, decay back to 4.0; remove `_target_per_client`.
- [x] `backend/tests/test_observability.py` -- rebase the three g_min assertions to a 4.0 base.
- [x] `backend/scripts/load_test_gmin.py` -- reference scheduler global interval flat; drop/neutralize band target so the harness mirrors production.
- [x] `backend/tests/test_prelaunch.py` -- rewrite the band assertions (`per_client_cadence`, `adaptive_formula_matches_architecture`, paused-client `avg_gap`) to flat: cadence ≈ G×n, global interval = g_min; keep the FloodWait/governor/fairness/attribution tests.
- [x] `_bmad-output/planning-artifacts/architecture.md` -- amend the FR13↔NFR1 block to the flat 4s model; note the band is superseded.

**Acceptance Criteria:**
- Given default config and any `n ≥ 0`, when `interval(n)` is called with no FloodWait active, then it returns exactly `4.0`.
- Given a FloodWait, when the governor raises, then `interval()` returns the raised `g_min` and decays one ×1.5 step per 600s quiet window, never below `4.0`.
- Given n active clients with queued lines, when the worker runs steady-state, then the global send gap is ≈4s and round-robin fairness/owner-priority bounds are unchanged (each client's turn ≈ 4×n s).
- Given the full backend suite, when `pytest` runs, then it is green with no surviving 10–20s band assertions.

## Spec Change Log

- **Impl deviation (2026-06-13):** in `send_worker.py` the pacing block previously ran a per-send `count_active_senders` DB query only to feed `interval(n)`. Since the flat interval ignores `n`, that query is now dead weight (one DB round-trip + failure surface per send), so it was removed and the pacing call is `scheduler.interval(1)`. ETA's `count_active_senders` use in `services/batches.py` is unaffected. Not in the original Code Map but a direct consequence of the flat interval; flagged for review.

## Design Notes

`interval()` returns `self._g_min` (NOT `settings.scheduler_g_min_seconds`) so a live FloodWait still pushes the constant up ×1.5 and decays back — that's why it keeps calling `_maybe_decay()`:
```python
def interval(self, n: int) -> float:
    """Constant governed floor between sends; n no longer affects pacing."""
    self._maybe_decay()
    return self._g_min
```
`eta_seconds` (`queued × n × interval`) stays exact with flat interval (turn = n×G), so ETA still degrades honestly as clients join.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest` -- expected: full suite green (scheduler, observability, prelaunch, send-hardening all pass).
- `cd backend && .venv/bin/ruff check . && .venv/bin/mypy app` -- expected: clean (no unused-import warnings from the removed band constants).
- `cd backend && .venv/bin/python -m scripts.load_test_gmin --clients 5 --lines 20` -- expected: no FloodWaits at g_min=4.0, min_gap ≥ 4.0, reference matches production cadence.

## Suggested Review Order

**The interval change (design intent)**

- Entry point: `interval()` now unconditionally returns the governed floor — n is ignored.
  [`scheduler.py:87`](../../backend/app/core/scheduler.py#L87)
- The new constant value (and the comment explaining the floor IS the interval now).
  [`config.py:66`](../../backend/app/config.py#L66)
- Worker pacing: the dead per-send active-sender count was dropped; pacing is `interval(1)`.
  [`send_worker.py:852`](../../backend/app/core/send_worker.py#L852)

**Knock-on correctness (unchanged-on-purpose paths)**

- ETA stays exact with a flat interval — n_eff enters only via the ×n factor.
  [`batches.py:42`](../../backend/app/services/batches.py#L42)

**Docs & config**

- The FR13 capacity math, amended to the flat model with a historical note.
  [`architecture.md:408`](../planning-artifacts/architecture.md#L408)
- `.env.example` now documents the constant 4s floor under the real var name.
  [`.env.example:57`](../../backend/.env.example#L57)

**Tests (supporting)**

- Flat truth table + governor rebased to a 4.0 base.
  [`test_scheduler.py:69`](../../backend/tests/test_scheduler.py#L69)
- Load-test reference made flat so the launch gate mirrors production.
  [`load_test_gmin.py:162`](../../backend/scripts/load_test_gmin.py#L162)
