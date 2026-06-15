---
title: 'Auto-recuperación del cliente al renovar un plan vencido'
type: 'feature'
created: '2026-06-15'
status: 'done'
context: []
baseline_commit: '66f2baa64b685c2a768874c5de6e82083adf5c22'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Cuando un admin renueva el plan de un cliente vencido, el cliente queda atascado en `/expired` para siempre. Al vencer, el backend **revoca la sesión y borra la cookie** (`deps.py`, `middleware.ts`), y `/expired` es un callejón anónimo que sondea `/me` una sola vez al montar — sin botón de logout ni link a login. La renovación es correcta en el servidor pero nunca llega a la pestaña abierta del cliente.

**Approach:** Dejar de revocar la sesión al vencer (mantenerla viva; la verja de plan devuelve un 403 **repetible**, igual que la verja de cambio-de-contraseña), conservar la cookie, y hacer que `/expired` haga *polling* a `/me` para que una renovación voltee el 403→200 y la página re-ingrese al cliente sola. Agregar un botón "Iniciar sesión" como respaldo manual por si la sesión misma expiró.

## Boundaries & Constraints

**Always:**
- La expiración sigue siendo una verja **por request** en `get_current_user`/`_resolve_session_user`; identidad y `tenant_id` siguen viniendo SOLO de la sesión.
- El 403 `plan_expired` pasa a ser **repetible** (la sesión sobrevive) — replicar EXACTO el patrón existente de `password_change_required`: sin revocar, sin borrar cookie.
- El bloqueo (`is_blocked`) conserva su revocación dura — solo cambia la rama de expiración.
- `/expired` debe seguir siendo accesible SIN sesión (queda fuera del matcher del middleware).

**Ask First:**
- Si al mantener viva la sesión cualquier endpoint protegido queda accesible (respuesta NO-403) a un cliente vencido, HALT y reportar antes de seguir.

**Never:**
- NO tocar `compute_renewed_expiry` ni el endpoint `/users/{id}/renew` — la renovación ya es correcta.
- NO agregar push en tiempo real (WebSocket) para esto — el polling basta; sin eventos WS nuevos.
- NO debilitar la revocación del cliente bloqueado.
- NO sondear más rápido que ~5s (carga sobre el backend compartido).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Expiración mid-sesión | sesión activa, `expires_at` pasa al pasado | primer Y siguientes `/api/auth/me` → 403 `plan_expired`; sesión NO revocada, cookie conservada | N/A |
| Renovación con cliente en /expired | cliente en /expired con sesión válida (vencida), admin renueva | el siguiente poll a `/me` → 200; redirige cliente→`/`, staff→`/admin/users` | N/A |
| Sesión vencida por TTL | cookie presente pero `auth_session` lapsada/desconocida | `/me` → 401; /expired redirige a `/login` | N/A |
| Login como cliente vencido | cliente vencido intenta loguear | 403 `plan_expired`, sin cookie (sin cambios) | sin cambios |
| Cliente bloqueado | `is_blocked` true | sesión revocada, 401 (sin cambios) | sin cambios |

</frozen-after-approval>

## Code Map

- `backend/app/api/deps.py` -- `_resolve_session_user`: rama de expiración (líneas 52-54). Quitar la revocación; dejar la sesión viva.
- `frontend/middleware.ts` -- rama 403 `plan_expired` (línea ~91): dejar de borrar la cookie; conservar el redirect a `/expired`.
- `frontend/app/expired/page.tsx` -- sondeo único en `useEffect` → reemplazar por polling + redirects + botón de respaldo.
- `frontend/lib/api.ts` -- `toApiError` ya salta el redirect cuando `pathname === "/expired"` (línea ~53): verificar que el poll no haga loop; probablemente sin cambios.
- `backend/tests/test_plan_expiry.py` -- `test_mid_session_expiry_cuts_access_and_revokes` afirma la revocación (línea 71): actualizar + agregar test de recuperación por renovación.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/api/deps.py` -- En `_resolve_session_user`, eliminar `await _revoke_own_session(token)` de la rama `is_plan_expired` para que la sesión sobreviva; solo `raise plan_expired()`. Actualizar comentarios: la expiración ahora es un 403 repetible (espeja `password_change_required`), ya no "one-shot". La rama `is_blocked` sigue revocando.
- [x] `frontend/middleware.ts` -- En la rama 403 `plan_expired`, quitar `redirect.cookies.delete(SESSION_COOKIE)` (conservar la cookie para que `/expired` pueda sondear). Actualizar los comentarios de "one-shot".
- [x] `frontend/app/expired/page.tsx` -- Reemplazar el sondeo único por un poll de `/api/auth/me` (~10s + chequeo inmediato), limpiando el intervalo al desmontar. En 200 → `window.location.replace(role==="client"?"/":"/admin/users")`. En 401 → redirigir a `/login`. En 403 `plan_expired` → seguir esperando. Agregar un botón visible "Iniciar sesión" → `/login` como respaldo manual.
- [x] `backend/tests/test_plan_expiry.py` -- Reescribir `test_mid_session_expiry_cuts_access_and_revokes`: el segundo `/me` sigue 403 `plan_expired` (sesión NO revocada). Agregar `test_renewal_restores_access_on_same_session`: tras expirar → 403, luego poner `expires_at` futuro → misma cookie en `/me` → 200.

**Acceptance Criteria:**
- Given un cliente activo cuyo plan vence mid-sesión, when pega cualquier endpoint, then recibe 403 `plan_expired` y su sesión NO se revoca (cookie sigue válida).
- Given un cliente vencido en `/expired`, when un admin renueva el plan, then dentro de un intervalo de poll la página detecta el 200 de `/me` y lo re-ingresa a la app — sin login manual.
- Given un cliente vencido cuya `auth_session` ya lapsó por TTL, when `/expired` sondea, then recibe 401 y lo manda a `/login`.
- Given un cliente bloqueado, when hace un request, then la sesión sigue revocada (401) — sin cambios.

## Design Notes

- **Por qué mantener la sesión en vez de push WS:** la pestaña bloqueada no tiene conexión WS y la recuperación más simple es la que la página YA hace (un probe a `/me`) → solo hacerla repetir. Espeja la verja `password_change_required`, que ya es un 403 repetible y no-revocante — estamos volviendo `plan_expired` consistente con ella.
- **Sin loop en api.ts:** `toApiError` ya omite el redirect a `/expired` cuando `pathname === "/expired"`, así que `api.get("/api/auth/me")` en `/expired` lanza un `ApiError(403)` atrapable sin navegar — el poll ramifica sobre `err.status`/`err.code`.
- **Intervalo ~10s:** carga ligera, pocos lockouts concurrentes; detener al desmontar.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest tests/test_plan_expiry.py` -- expected: tests actualizados + el nuevo de recuperación pasan.
- `cd frontend && npm run build` -- expected: tsc + build pasan (CLAUDE.md: build es el gate, no solo lint).

**Manual checks:**
- Loguear como cliente, mover `expires_at` al pasado en la DB, observar el redirect a `/expired`; renovar desde `/admin/users`; en ~10s la pestaña redirige a `/` sin re-login.

## Suggested Review Order

**El pivote: la verja de expiración deja de revocar**

- Antes revocaba la sesión al vencer; ahora 403 repetible (espeja la verja de password). Es todo el diseño.
  [`deps.py:59`](../../backend/app/api/deps.py#L59)

**Recuperación en el frontend**

- El poll a `/me` que detecta la renovación y re-ingresa al cliente; punto de entrada de la UX.
  [`page.tsx:33`](../../frontend/app/expired/page.tsx#L33)

- Maneja el caso post-renovación `password_change_required` (patch del review).
  [`page.tsx:48`](../../frontend/app/expired/page.tsx#L48)

- Conservar la cookie en el 403 `plan_expired` — sin ella el poll no tiene identidad.
  [`middleware.ts:94`](../../frontend/middleware.ts#L94)

- La guarda por `pathname === "/expired"` que evita el loop de redirect del poll.
  [`api.ts:54`](../../frontend/lib/api.ts#L54)

**Consistencia (solo comentarios, comportamiento intacto)**

- El WS ya gatea expiración por su cuenta cada 60s — la ventana de exposición no cambia.
  [`ws.py:59`](../../backend/app/api/ws.py#L59)

**Tests**

- Contrato invertido: el 2º `/me` sigue 403, la sesión NO se revoca.
  [`test_plan_expiry.py:71`](../../backend/tests/test_plan_expiry.py#L71)

- Auto-recuperación: la renovación revive la MISMA sesión (200).
  [`test_plan_expiry.py:100`](../../backend/tests/test_plan_expiry.py#L100)
