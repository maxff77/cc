---
title: 'Revocar una key canjeada cancela el plan del cliente'
type: 'feature'
created: '2026-06-26'
status: 'done'
baseline_commit: '9888e41d0a68310b75d0717acd7fd9b032f5c2d6'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Un admin/owner mintea una gift-key y se la da a alguien para que trabaje; el cliente la canjea y obtiene días de plan. Si después no cumple, hoy NO hay forma de revertirlo: revocar una key **ya canjeada** devuelve `409 key_already_claimed` (los días ya se otorgaron). El operador puede bloquear al cliente manualmente, pero eso es un flujo aparte y no parte de la key que originó el acceso.

**Approach:** Convertir "Revocar" de la vista de keys en un kill-switch que cascada: revocar una key canjeada marca la key como `revoked` Y cancela el plan del cliente que la canjeó (expira su plan ahora + cierra sus sesiones vivas), igual de inmediato que "Bloquear". El log de keys ya muestra `claimed_by_email`, así que el operador ve a quién está cortando en el mismo lugar.

## Boundaries & Constraints

**Always:**
- Expirar = `user.expires_at = now(UTC)` (el gate `is_plan_expired` usa `<=`, así que lockout inmediato vía `get_current_user`) + revocar todas las sesiones del cliente (mismo mecanismo que `set_blocked`).
- Lockear la fila del usuario con `FOR UPDATE` antes de tocar `expires_at` (read-modify-write).
- La cascada de expiración SOLO corre si la key otorgó días (`key.days > 0`). Una key solo-créditos no creó un plan, así que revocarla no debe expirar nada.
- Revocar sigue siendo idempotente: una key ya `revoked` no re-expira al cliente (early-return existente).

**Ask First:** (ninguno)

**Never:**
- NO setear `is_blocked` (esto cancela el plan, no bloquea — el cliente expirado puede recuperarse canjeando otra key, flujo `allow_expired` existente).
- NO clawback de créditos otorgados (fuera de alcance; una key solo-créditos revocada queda `revoked` sin tocar saldo).
- NO restar los días exactos de la key del `expires_at`; expira a `now` directo ("se cancela el plan", no "se descuentan días").
- NO tocar `responses` / `send_log` / lotes del cliente.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Revocar key activa (sin canjear) | key `status=active` | key → `revoked`; nadie afectado | N/A |
| Revocar key canjeada con días | key `status=claimed`, `days>0`, claimer existe | key → `revoked`; `claimer.expires_at=now`; sesiones del claimer revocadas | N/A |
| Revocar key canjeada solo-créditos | key `status=claimed`, `days=0` | key → `revoked`; plan NO tocado | N/A |
| Revocar key ya revocada | key `status=revoked` | no-op idempotente; cliente NO re-expirado | N/A |
| Claimer borrado | `claimed_by_user_id` ya no existe | key → `revoked`; sin crash | defensivo: skip expiración |
| key_id inexistente | id desconocido | 404 | `key_not_found` |

</frozen-after-approval>

## Code Map

- `backend/app/services/gift_keys.py` -- `revoke()`: hoy lanza `key_already_claimed` en estado `claimed`; aquí va la nueva cascada de expiración.
- `backend/app/services/plans.py` -- `set_blocked()` (patrón de revoke-all-sessions a reusar) y `is_plan_expired()` (boundary `<=`).
- `backend/app/db/repos/users.py` -- `get_user_by_id(for_update=True)` y `revoke_all_sessions_for_user`.
- `backend/app/api/keys.py` -- `revoke_key` route (docstring + ya no es solo "unclaimed").
- `frontend/app/admin/keys/page.tsx` -- `RevokeKeyAction` (línea ~188 gatea a `status==="active"`; copy del diálogo; manejo de `key_already_claimed`).
- `backend/tests/test_gift_keys.py` -- tests de revoke a extender.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/services/gift_keys.py` -- en `revoke()`, cuando `key.status == "claimed"`: lockear y cargar el claimer (`get_user_by_id(claimed_by_user_id, for_update=True)`); si existe y `key.days > 0`, set `expires_at = now(UTC)` + `revoke_all_sessions_for_user`; luego marcar la key `revoked`. Quitar el `raise key_already_claimed()`.
- [x] `backend/app/api/keys.py` -- actualizar docstring de `revoke_key` (ya no rechaza claimed; ahora cancela el plan del claimer).
- [x] `frontend/app/admin/keys/page.tsx` -- mostrar `RevokeKeyAction` también para `status === "claimed"`; en el diálogo, cuando la key está canjeada, advertir que cancelará el plan de `{claimed_by_email}`; quitar el branch que trata `key_already_claimed` como "ya pasó, refresca".
- [x] `backend/tests/test_gift_keys.py` -- cubrir la matriz: revoke de claimed con días expira al claimer + revoca sus sesiones; solo-créditos no toca plan; doble-revoke idempotente no re-expira. (claimer-borrado: guard defensivo `claimer is not None`, no testeado — forzarlo requiere cirugía de FK que no compensa.)

**Acceptance Criteria:**
- Given una key canjeada con días por el cliente X, when un admin la revoca, then la key queda `revoked`, `X.expires_at <= now` (X cae en `plan_expired` en su próximo request) y sus sesiones quedan invalidadas.
- Given una key solo-créditos canjeada, when se revoca, then la key queda `revoked` y el `expires_at` del claimer NO cambia.
- Given una key ya revocada cuyo claimer fue renovado después, when se revoca de nuevo, then es no-op y el plan renovado NO se re-expira.

## Spec Change Log

- **Review 1 (sin loopback).** 3 reviewers (blind / edge-case / acceptance). Acceptance: PASS total. Patches: (a) guard `claimer.role == "client"` antes de expirar/patear — evita pisar `expires_at` y desloguear a un claimer promovido a staff; (b) comment `ponytail:` sobre la inversión de lock KEY→USER vs claim USER→KEY (deadlock ABBA narrow, Postgres aborta sin corrupción; upgrade path documentado). Rechazado (intent confirmado por el user): "expira TODO el plan, no solo los días de la key" es el kill-switch total pedido ("se cancela el plan, igual que zephyr"), no over-revocación accidental.

## Design Notes

El gate de expiración es per-request en `deps.py:65` (`is_plan_expired` con boundary `<=`), así que `expires_at = now` ya lockea sin tocar la sesión. Revocamos sesiones igual para el kick inmediato y paridad con "Bloquear" (`set_blocked`), que el usuario tomó como referencia. La expiración es la semántica correcta sobre bloqueo: el cliente expirado puede recuperarse si el operador le da otra key (flujo `claim` con `allow_expired`), mientras que un bloqueo es un estado distinto y manual.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest tests/test_gift_keys.py -q` -- expected: pasa, incluidos los casos nuevos de cascada.
- `cd frontend && npm run build` -- expected: compila (tsc incluido) sin errores de tipo.

## Suggested Review Order

**La cascada (corazón del cambio)**

- Entry point: `revoke()` ahora expira el plan del claimer + patea sesiones en vez de lanzar 409.
  [`gift_keys.py:139`](../../backend/app/services/gift_keys.py#L139)
- El set de expiración + guard `role == "client"` (no pisar staff).
  [`gift_keys.py:173`](../../backend/app/services/gift_keys.py#L173)
- Comment del lock-order KEY→USER (deadlock ABBA narrow, Postgres aborta).
  [`gift_keys.py:156`](../../backend/app/services/gift_keys.py#L156)

**Ruta admin**

- Docstring: revocar una key canjeada cancela el plan del claimer.
  [`keys.py:168`](../../backend/app/api/keys.py#L168)

**UI**

- Botón "Revocar" ahora también para keys `claimed`.
  [`page.tsx:189`](../../frontend/app/admin/keys/page.tsx#L189)
- Diálogo: copy que advierte a quién se le cancela el plan.
  [`page.tsx:421`](../../frontend/app/admin/keys/page.tsx#L421)

**Tests**

- Matriz: cascada con días, solo-créditos no toca plan, doble-revoke idempotente.
  [`test_gift_keys.py:325`](../../backend/tests/test_gift_keys.py#L325)
