---
title: 'Permitir espacios internos en el valor de un gate'
type: 'bugfix'
created: '2026-06-16'
status: 'done'
route: 'one-shot'
---

# Permitir espacios internos en el valor de un gate

## Intent

**Problem:** Adding a gate whose value is a space-separated checker command (e.g. `/xx x`) was impossible — `_validate_gate_value` rejected **any** whitespace (`ch.isspace()`), even though the friendly gate *name* already allowed spaces. The owner hit a hard validation error with no way through.

**Approach:** Drop the blanket `ch.isspace()` rejection and lean on `str.isprintable()`, which keeps the plain ASCII space (0x20) but still flags every tab/newline/NBSP/zero-width/separator/control char. Collapse internal space-runs to one (a stored double space would desync `apply_gate`'s `startswith(gate_value + " ")` dedup and silently double-prefix re-pasted lines). The frontend validator is widened to a true superset-reject of the backend so valid `/xx x` passes inline while invisibles still get a client-side message.

## Suggested Review Order

1. [`backend/app/api/admin.py` — `_validate_gate_value`](../../backend/app/api/admin.py) — the heart: `isprintable()` policy + space-run collapse. Confirm only 0x20 survives among whitespace.
2. [`backend/app/services/batches.py` — `apply_gate`](../../backend/app/services/batches.py) — unchanged, but the reason collapse matters: `startswith(gate_value + " ")` dedup must stay reliable with single-space values.
3. [`frontend/app/admin/gates/page.tsx` — `validateGateValue`](../../frontend/app/admin/gates/page.tsx) — client mirror: collapse + widened invisible-char class (superset of backend; no `/u` flag — sub-es6 tsc target).
4. [`backend/tests/test_admin_gates.py`](../../backend/tests/test_admin_gates.py) — reject-set gains NBSP/em-space/bidi-override/soft-hyphen; accept inner space; collapse double space.

<!-- Ctrl+click (Cmd+click on macOS) the links above to jump to each stop. -->
