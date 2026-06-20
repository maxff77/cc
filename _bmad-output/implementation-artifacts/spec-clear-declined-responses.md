---
title: 'Limpiar oculta de forma persistente las respuestas declinadas (❌) de Completa'
type: 'feature'
created: '2026-06-19'
status: 'done'
baseline_commit: '2f00aa8499f3788862803701210c809ccd3d667c'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** El botón "Limpiar" del cockpit solo vacía la vista Completa **visualmente y por pestaña**; al recargar reaparecen todas las revisiones (✅ y ❌). El cliente quiere quitar de forma **permanente** el ruido de las **declinadas (❌)** de Completa, conservando **Aprobadas (✅)** y **Datos CC** intactas.

**Approach:** "Limpiar" pasa a **ocultar de forma persistente** (soft-hide: marca `hidden_at`) las revisiones `kind='full' AND status='rejected'` de la sesión activa, con confirmación. Las lecturas de **display/export** de Completa excluyen las ocultas; **toda query de integridad** sigue viéndolas (la fila se retiene solo para atribución). DELETE físico **descartado** — ver Design Notes.

## Boundaries & Constraints

**Always:**
- Solo afecta filas `kind='full' AND status='rejected'` de la sesión objetivo. `status='ok'` (Aprobadas) y `kind='cc'` (Datos CC) **nunca** se tocan.
- `hidden_at` es **solo presentación**: filtra las lecturas de Completa (`list_full`/`full_count` → display, export, Historial). Las queries de integridad (`responded_line_count`, `awaiting_sent_keys`/`_answered_full_exists`, `last_full_revision`, `has_ok_revision`) **deben seguir contando las ocultas** — si no, salta "esperando respuesta", resucita el reconciliador o se duplica el cargo de créditos.
- `tenant_id` **siempre** de la sesión (cookie), nunca de body/path. Sesión desconocida/ajena/oversize → 404 idéntico (`_require_session`).
- Confirmación obligatoria antes de ocultar (`ConfirmDialog`, variante danger).
- Respuestas **nuevas** capturadas tras Limpiar siguen apareciendo en Completa (ocultar ≠ pausa: solo marca las existentes al instante del clic).
- Tras ocultar, re-emitir `session.active` (`active_session_data`) al tenant para que todas las pestañas reflejen el estado (patrón verbatim de `continue_session`).

**Ask First:**
- Purga **física** real (DELETE de `responses` + neutralizar `send_log`): rompe atribución y la resurrección del reconciliador; solo si el humano lo renegocia explícitamente.
- Ocultar también ✅ / Datos CC, o agregar un "des-ocultar"/undo.

**Never:**
- No DELETE de `responses` ni de `send_log`. No tocar Telethon/captura/scheduler/watchdog.
- No filtrar `hidden_at` en **ninguna** query de integridad/atribución/reconciliador/créditos.
- No leer ni escribir el contenido de `respuestas/` (legacy 🔒).
- No conservar el clear como estado local de pestaña: se **elimina** la lógica previa `clearedKeys` (el server es la única fuente de verdad).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Limpiar con N declinadas | sesión activa, N filas ❌ visibles | confirmar → oculta N; Completa baja a solo ✅; Aprobadas y Datos CC sin cambios; badge Completa = conteo ✅; "esperando respuesta" sin cambios; persiste al recargar | N/A |
| Sin declinadas | Completa solo ✅ / vacía | botón "Limpiar" deshabilitado / no-op | N/A |
| Sin sesión activa | `sessionId === null` | botón deshabilitado; endpoint responde 404 si se invoca | `session_not_found` |
| Nueva ❌ tras Limpiar | llega `response.captured` ❌ | aparece en Completa (no estaba marcada al clic) | N/A |
| Recargar / pasada del reconciliador (45 s) | `snapshot` / `reconcile_once` | Completa sigue sin las ocultas; el reconciliador **no** reinserta (la fila oculta existe) | N/A |
| Sesión ajena/desconocida | `session_id` de otro tenant | 404 idéntico (sin fuga de existencia) | `session_not_found` |

</frozen-after-approval>

## Code Map

- `backend/migrations/versions/f6a2d9c4e1b7_responses_hidden_at.py` — migración (down_revision `c4e7a2f9b1d6`): `responses.hidden_at TIMESTAMPTZ NULL`.
- `backend/app/db/models.py` (`Response`, ~716) — columna `hidden_at`.
- `backend/app/db/repos/responses.py` — capa que distingue oculto: `list_full`/`full_count` filtran; nueva `hide_rejected`. Integrity fns **intactas**.
- `backend/app/services/batches.py` — `clear_declined()` orquesta; reusa `active_session_data` para el re-emit.
- `backend/app/api/sessions.py` — `POST /{session_id}/clear-declined` (tenant vía `_require_session`) + `broadcaster.emit`.
- `frontend/app/app/page.tsx` — botón → confirm + mutación; quita el clear local.
- `frontend/components/sessions/response-views.tsx` — `ClearButton` (icono/disabled); props muertas fuera.
- `frontend/lib/api.ts`, `components/ui/confirm-dialog.tsx`, `components/ui/notice.tsx` — reusar.

## Tasks & Acceptance

**Execution:**
- [x] `backend/migrations/versions/f6a2d9c4e1b7_responses_hidden_at.py` — columna nullable `hidden_at` en `responses` (down_revision `c4e7a2f9b1d6`); upgrade `add_column`, downgrade `drop_column`.
- [x] `backend/app/db/models.py` — `Response.hidden_at` (`DateTime(timezone=True)`, nullable).
- [x] `backend/app/db/repos/responses.py` — `_list_last`/`list_full`/`full_count`: param `include_hidden=False` → `Response.hidden_at.is_(None)`; nueva `hide_rejected` (`UPDATE … SET hidden_at=func.now() WHERE kind='full' AND status='rejected' AND hidden_at IS NULL`, retorna `rowcount`). Integrity fns intactas.
- [x] `backend/app/api/sessions.py` — `POST /{session_id}/clear-declined`: `_require_session(tenant)` → `responses_repo.hide_rejected` → commit → `emit("session.active", active_session_data)` → `{hidden}`. **Sin service fn aparte:** la orquestación vive en el router (mismo idiom que `delete_session`); `batches_service.active_session_data` se reusa para el re-emit.
- [x] `frontend/app/app/page.tsx` — fuera el clear local (`clearedKeys`/`useMemo`); estado `confirmOpen`/`clearError`; `useMutation(api.post(...clear-declined))`; `onSuccess` cierra el diálogo (el re-emit actualiza el store); `ConfirmDialog` danger; disabled por `declinedCount = responsesTotal − responsesOkTotal ≤ 0`.
- [x] `frontend/components/sessions/response-views.tsx` — `ClearButton` icono `trash` + `clearDisabled`; props muertas (`completaResponses`/`completaTotal`) fuera; Completa usa `responses`/`responsesTotal`.
- [x] `backend/tests/test_clear_declined.py` — `hide_rejected` solo marca ❌ de esa sesión, idempotente; `list_full`/`full_count` excluyen ocultas (e `include_hidden=True` las trae); `responded_line_count` + `awaiting_reply_count` + `awaiting_sent_keys` **inalterados** tras ocultar (sin spike / sin resurrección); endpoint 404 unknown/oversize/foreign-tenant; `{hidden:2}` + re-emit `session.active`.

**Acceptance Criteria:**
- Given Completa con ✅ y ❌, when confirmo "Limpiar", then Completa queda solo con ✅, Aprobadas y Datos CC intactas, y "esperando respuesta" no cambia.
- Given oculté las declinadas, when recargo o pasa el reconciliador (>45 s), then las ❌ **no** reaparecen.
- Given oculté declinadas de una sesión, when exporto Completa `.txt`, then no incluye las ocultas; el `.txt` de Aprobadas no cambia.
- Given un `session_id` de otro tenant, when invoco el endpoint, then 404 `session_not_found`.
- Given no hay declinadas, when renderiza el cockpit, then "Limpiar" está deshabilitado.

## Design Notes

- **Por qué soft-hide y no DELETE:** `core/reconciler.py` (45 s / ventana 72 h) y `send_log.awaiting_sent_keys` consideran "respondida" una línea por `EXISTS(full response)`. Borrar una ❌ deja la línea como no-respondida → el reconciliador re-baja la respuesta de Telegram y reinserta la ❌ (resucitación), y `responded_line_count` baja → "esperando respuesta" salta. `hidden_at` se filtra **solo** en `list_full`/`full_count`; las integrity fns filtran por `kind='full'` sin mirar `hidden_at`, así que la fila oculta sigue contando para reconciliación, awaiting e idempotencia de créditos (`has_ok_revision`).
- **Re-emit, no evento nuevo:** `active_session_data` sobre `session.active`; el reducer ya aplica `responses`/totales de ese frame (igual que Continuar) — sin acción imperativa en el front.
- **Sustituye** el clear visual-por-pestaña (`spec-clear-completa-view`): se elimina `clearedKeys`.

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` — expected: aplica la migración sin error.
- `cd backend && .venv/bin/pytest` — expected: verde (incl. tests nuevos).
- `cd frontend && npm run build` — expected: tsc sin errores (gate antes de push a main).
- `cd frontend && npm run lint` — expected: sin errores nuevos.

**Manual checks:**
- Enviar lote, capturar ✅ y ❌. Limpiar → confirmar → Completa solo ✅; Aprobadas/Datos CC iguales; "esperando respuesta" no salta. Recargar y esperar >45 s → las ❌ no vuelven.

## Suggested Review Order

**Diseño: soft-hide, no DELETE (entry point)**

- Entry point — el endpoint: tenant-scope → ocultar → commit → re-emit `session.active`.
  [`sessions.py:384`](../../backend/app/api/sessions.py#L384)
- El corazón: marca `hidden_at` solo en ❌ vivas; idempotente; integrity fns intactas.
  [`responses.py:256`](../../backend/app/db/repos/responses.py#L256)
- La columna + el invariante: por qué se retiene la fila (atribución/reconciliador).
  [`models.py:727`](../../backend/app/db/models.py#L727)

**Display vs integridad (la separación load-bearing)**

- El filtro de display: `include_hidden=False` oculta del badge/Completa.
  [`responses.py:251`](../../backend/app/db/repos/responses.py#L251)
- Mismo filtro en la lista (snapshot/export/Historial); integrity fns NO pasan por aquí.
  [`responses.py:343`](../../backend/app/db/repos/responses.py#L343)

**Cockpit: confirmación + mutación (sin clear local)**

- `declinedCount` habilita el botón; mutación POST al endpoint.
  [`page.tsx:117`](../../frontend/app/app/page.tsx#L117)
- Guard: cancela la confirmación si la sesión cambia con el diálogo abierto.
  [`page.tsx:140`](../../frontend/app/app/page.tsx#L140)
- El diálogo danger irreversible.
  [`page.tsx:256`](../../frontend/app/app/page.tsx#L256)

**Botón**

- `ClearButton` ahora con icono `trash` (sí borra) + `clearDisabled`.
  [`response-views.tsx:175`](../../frontend/components/sessions/response-views.tsx#L175)

**Periféricos**

- Migración aditiva-nullable (head off `c4e7a2f9b1d6`).
  [`f6a2d9c4e1b7:36`](../../backend/migrations/versions/f6a2d9c4e1b7_responses_hidden_at.py#L36)
- Tests: solo-❌/idempotente, display-excluye/integrity-cuenta, sin spike/resurrección, 404 tenant.
  [`test_clear_declined.py:168`](../../backend/tests/test_clear_declined.py#L168)
