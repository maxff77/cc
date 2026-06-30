---
title: 'Completa muestra respuestas sin glifo (✅/❌) como fila neutral'
type: 'bugfix'
created: '2026-06-30'
status: 'done'
context: []
baseline_commit: '03aca37'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Cuando el bot responde a una línea con texto pero SIN ✅ ni ❌ (su respuesta definitiva no trae glifo), hoy no se persiste nada (`status = previous_status` → `None` → ninguna fila) y el cliente nunca ve esa respuesta en Completa: desaparece por completo.

**Approach:** Persistir esas respuestas sin veredicto como una fila **neutral** en `responses` (nuevo `status='neutral'`), visible SOLO en Completa con un glifo neutro. No cuenta como Aprobada ni Rechazada, no debita créditos, no extrae CC, y NO altera "esperando respuesta". Como Completa colapsa a la última revisión por `(chat_id, message_id)` y el reducer live hace upsert por `messageId`, un `⏳→✅/❌` normal se sobrescribe en sitio a su veredicto — la fila neutral solo queda permanente cuando el mensaje NUNCA recibe glifo.

## Boundaries & Constraints

**Always:**
- El cambio de persistencia vive SOLO en la rama `else` final (gate normal: ni cookie-mode ni special-mode) de `process_incoming`.
- `responded_line_count` (denominador de "esperando respuesta") debe seguir contando solo `status IN (ok, rejected)` → una respuesta neutral NO marca la línea como respondida; "esperando" se comporta idéntico a hoy.
- Mantener la guarda no-op (text + status iguales → return) para que un replay reconciliado de una neutral sea idempotente.
- `status` es `String(10)` sin enum/check → `'neutral'` cabe sin migración.

**Ask First:**
- Si surge la necesidad de que la neutral SÍ cuente como respondida en "esperando respuesta", o de mostrarla en Historial/export-filtrada/Datos CC — HALT y preguntar (fuera de alcance acordado).

**Never:**
- No tocar las ramas cookie-mode ni special-mode (siguen escribiendo nada en su no-verdict).
- No contar la neutral en Aprobadas, Datos CC, créditos, live-forward ni Historial (todos ya filtran `status='ok'` — no agregar lógica nueva ahí).
- No crear migración. No colapsar/duplicar filas por revisión.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Reply final sin glifo | gate normal, 1ª reply sin ✅/❌, sin revisión previa | Fila `kind=full, status='neutral'` persistida + emitida; aparece en Completa con glifo neutro; NO en Aprobadas/Datos CC; sin débito; `esperando` sin cambio | N/A |
| ⏳ intermedio → ✅ | `⏳` (sin glifo) y luego edit `✅` (mismo message_id) | Live: una sola fila que pasa neutral→✅ (upsert por messageId); snapshot colapsa a la ✅; `esperando` baja solo con la ✅ | N/A |
| Edit sin glifo tras ✅/❌ | mensaje ya `ok`/`rejected`, llega edit sin glifo | Conserva el status previo (no degrada a neutral); no se crea fila neutral | N/A |
| No-verdict cookie/special | sesión cookie_mode o special_mode, reply sin veredicto, sin previa | Sin cambios: no escribe fila (NO neutral) | N/A |
| Replay de neutral | misma reply neutral reenviada (reconciler/catch_up) | No-op (text+status coinciden) → sin fila duplicada | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/db/repos/responses.py` -- define `STATUS_OK/REJECTED`; añadir `STATUS_NEUTRAL`. `responded_line_count` (denominador esperando) cuenta full sin filtro de status → restringir a ok/rejected. `full_count`/`list_full` sin filtro de status ya incluyen neutral en Completa; con `status=ok` ya la excluyen de Aprobadas/CC.
- `backend/app/core/capture.py` -- `process_incoming`: rama `else` final (líneas ~459-461) asigna `status = previous_status` (None si no hay previa) → asignar neutral cuando no hay previa. El guard `if status is None` (~477) queda solo para cookie/special.
- `frontend/lib/ws.ts` -- uniones de `status` (`ResponseRow`, `SnapshotResponseRow`, `ResponseCapturedData.status`/`previous_status`) deben admitir `'neutral'`; el reducer ya hace upsert por `messageId` y pasa status verbatim.
- `frontend/components/sessions/response-row.tsx` -- `DataRow` solo conoce `ok|rejected|undefined(=Datos CC)`; añadir render de `'neutral'`.
- `frontend/components/sessions/response-views.tsx` -- Aprobadas ya filtra `row.status === "ok"`; Completa muestra todo → sin cambios (verificar).
- `backend/tests/test_attribution.py` -- `test_first_intermediate_revision_without_emoji_is_not_persisted` asume el viejo "sin fila"; actualizar.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/repos/responses.py` -- añadir `STATUS_NEUTRAL = "neutral"`; en `responded_line_count` agregar `.where(Response.status.in_((STATUS_OK, STATUS_REJECTED)))` -- preserva la semántica de "esperando respuesta" (neutral ≠ respondida).
- [x] `backend/app/db/repos/send_log.py` -- **(descubierto en impl)** `_answered_full_exists` es la verdadera compuerta de "esperando respuesta" Y de la work-list del reconciler — restringir a `status IN (ok, rejected)` (import de los constants). Sin esto, el ⏳→neutral bajaba el contador a 0. Bonus: el reconciler conserva la línea solo-neutral, recuperando un ✅/❌ editado que se haya perdido.
- [x] `backend/app/core/capture.py` -- en la rama `else` final: `status = previous_status if previous_status is not None else responses_repo.STATUS_NEUTRAL`; comentario actualizado. No toca cookie/special.
- [x] `frontend/lib/ws.ts` -- `"neutral"` añadido a las 4 uniones de status (ResponseRow, SnapshotResponseRow, ResponseCapturedData.status y .previous_status).
- [x] `frontend/components/sessions/response-row.tsx` -- `status?: "ok" | "rejected" | "neutral"`; `neutral` renderiza glifo muted `·` con `sr-only` "Sin veredicto", texto `text-foreground`. `isData` sigue siendo `status === undefined`.
- [x] `backend/tests/test_attribution.py` -- `test_first_no_verdict_reply_persists_neutral_row`: 1ª reply sin glifo persiste UNA fila `neutral` + emite (previous_status None), no entra al bucket unmatched; el ✅ posterior agrega revisión `ok` (collapse). `test_awaiting_reply.py` y `test_history.py` verdes sin editar.

**Acceptance Criteria:**
- Given un gate normal con una línea enviada, when el bot responde con texto sin ✅/❌ y nunca edita un glifo, then la respuesta aparece en Completa (fila neutral) y permanece, sin contar en Aprobadas/Datos CC/créditos.
- Given una línea en `⏳ procesando`, when llega después el `✅`, then Completa muestra una sola fila que termina en ✅ y "esperando respuesta" baja únicamente al llegar el ✅.
- Given una respuesta neutral ya capturada, when el reconciler la reenvía, then no se inserta fila duplicada (no-op).
- Given una sesión cookie-mode o special-mode con reply sin veredicto, then el comportamiento es idéntico al actual (no se crea fila neutral).

## Spec Change Log

- **Impl-time correction (compuerta de esperando-respuesta):** el bloque frozen citaba `responded_line_count` como denominador de "esperando respuesta", pero esa función ya no tiene callers de producción. La compuerta autoritativa es `send_log._answered_full_exists` (un `EXISTS` correlacionado contra `responses kind=full`), usada por el contador del cockpit (`awaiting_count_for_session`) Y por la work-list del reconciler. Sin parchearla, una fila neutral marcaba la línea como respondida y `⏳→neutral` bajaba el contador a 0 (test_awaiting_reply rojo). Se añadió `status IN (ok, rejected)` ahí (y también en `responded_line_count`, por consistencia). KEEP: dejar neutral fuera de `_answered_full_exists` también es correcto para el reconciler — mantiene la línea en cola y recupera un ✅/❌ editado que se haya perdido.

## Design Notes

Por qué neutral es invisible a "esperando respuesta": al arribo no se puede distinguir un `⏳` intermedio de una respuesta-final-sin-glifo (ambas sin ✅/❌). Si la neutral contara como respondida, un `⏳` bajaría el contador prematuramente. Excluirla de `responded_line_count` deja "esperando" exactamente como hoy (una línea solo-neutral sigue esperando un veredicto que quizá no llegue — mismo efecto que el viejo "sin fila"). El único cambio observable es que Completa ahora pinta la fila.

Colapso sin ruido: `_latest_full_ids` usa `DISTINCT ON (chat_id, message_id) ORDER BY id DESC` (snapshot) y `ws.ts` hace upsert por `messageId` (live) → un `⏳→✅` se ve como una fila única que transiciona, nunca dos filas.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest tests/test_attribution.py tests/test_awaiting_reply.py tests/test_history.py` -- expected: verde (neutral persiste; esperando intacto).
- `cd backend && .venv/bin/pytest` -- expected: suite completa verde.
- `cd frontend && npm run build` -- expected: `tsc` sin errores de tipo en las uniones de status.

**Manual checks (if no CLI):**
- En el cockpit, con un gate cuyo bot responda sin glifo: la respuesta aparece en Completa con glifo neutro, NO suma Aprobadas ni Datos CC, y "esperando respuesta" no baja por esa línea.

## Suggested Review Order

**Lógica de clasificación (empieza aquí)**

- El fix de una línea: primer reply sin ✅/❌ → fila neutral en vez de nada.
  [`capture.py:481`](../../backend/app/core/capture.py#L481)

- El nuevo valor de status (cabe en String(10), sin migración).
  [`responses.py:32`](../../backend/app/db/repos/responses.py#L32)

**Compuerta "esperando respuesta" + reconciler**

- La compuerta autoritativa: neutral NO responde una línea (esperando intacto; el reconciler conserva la línea para recuperar un ✅/❌ perdido).
  [`send_log.py:183`](../../backend/app/db/repos/send_log.py#L183)

- Mismo filtro en el contador de integridad (consistencia).
  [`responses.py:385`](../../backend/app/db/repos/responses.py#L385)

**Frontend**

- Glifo neutro `·` + sr-only "Sin veredicto" (Aprobadas/Datos CC lo excluyen solos).
  [`response-row.tsx:71`](../../frontend/components/sessions/response-row.tsx#L71)

- Uniones de status admiten neutral; el reducer hace upsert por messageId.
  [`ws.ts:52`](../../frontend/lib/ws.ts#L52)

- Interfaz del visor admin alineada (el archivo que el primer pase omitió).
  [`page.tsx:55`](../../frontend/app/admin/tenants/[id]/page.tsx#L55)

**Tests**

- Fija la nueva semántica: neutral persiste + emite, y el ✅ posterior colapsa.
  [`test_attribution.py:272`](../../backend/tests/test_attribution.py#L272)
