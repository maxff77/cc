---
baseline_commit: af96a4e593fb0bbcff12fd0e9072a0680445046e
---

# Story 2.3: Pausar, reanudar y detener con ETA honesto

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGÍA (decisión del owner 2026-06-11):** el término de producto para un prefijo es **"gate"** — DB, API, identificadores de código y todo el copy de UI (masculino: "el gate"). epics.md / architecture.md / docs de UX son anteriores al renombre y todavía dicen "prefijo/prefixes" — lee cada "prefijo" como "gate"; donde haya conflicto, gana "gate". Establecido en 2.1 (`gates`, `/api/gates`) y extendido en 2.2 (`gate_categories`, snapshots `gate_value`/`gate_name` en `batches`).

> **⚠️ HALLAZGOS DIFERIDOS DE 2.2 QUE ESTA STORY ABSORBE (deferred-work.md, sección "Story 2-2 review"):** esta story resuelve TRES de los hallazgos diferidos porque tocan exactamente las piezas que 2.3 rediseña: (1) el índice único parcial "un lote vivo por tenant" — la nota del review dice literalmente "Note for 2.3: widen the index predicate when 'paused'/'stopping' arrive"; (2) `register` antes del snapshot en `/ws` — sin esto el frame terminal `batch.state idle` puede perderse y la UI queda en un estado inventado, lo que viola el AC 1 de esta story; (3) `batch.state` con `batch_id`/`gate_name`/`gate_value` — 2.3 reescribe ese evento de todos modos y el ejemplo de architecture.md ya trae `batch_id`. Los hallazgos de WS-auth-continua y del retry de la transacción de registro NO se tocan aquí (ver Dev Notes → "Qué NO es esta story").

## Story

As a client,
I want to pause, resume or stop my own batch with an honest ETA,
So that I control my send without affecting anyone else.

## Acceptance Criteria

1. **Given** a live batch, **when** the client calls `POST /api/batches/{id}/pause|resume|stop`, **then** only that client's batch is affected and the resulting `batch.state` event (`idle | sending | paused | stopping`) is the single source of truth — the UI never invents a state and makes no optimistic jumps.
2. **Given** the state machine, **when** state changes arrive, **then** the pill mirrors it verbatim (Enviando / En pausa / Deteniendo, hidden at idle), controls follow (`sending`→Pausar+Detener, `paused`→Reanudar+Detener, `stopping`→disabled, `idle`→hidden), and the ring switches accent↔warning.
3. **Given** a pause request mid-interval, **when** the worker is sleeping, **then** the sleep is interrupted instantly (cancelable wait).
4. **Given** a stop request, **when** it executes, **then** the remaining queue clears and Detener acts instantly — no confirmation modal.
5. **Given** a live batch, **when** `batch.progress` events arrive, **then** the ETA shows an honest estimate ("~12 min") recomputed each event; while paused it relabels to "ETA al reanudar"; never a fake-precise countdown.
6. **Given** a `flood.wait` event, **when** it fires, **then** an amber informational notice appears with live countdown and copy "Telegram pidió esperar N s — reanudamos solos.", self-dismisses on resume, and is never styled as an error, **and** the nav live dot shows success while sending, warning while paused.

## Tasks / Subtasks

### Backend (Tasks 1–8)

- [x] Task 1: estados nuevos + invariante "un lote vivo" en DB (AC: 1, 4; resuelve diferido 2-2 #1)
  - [x] En `backend/app/db/repos/batches.py` (constantes en líneas 20–28): agregar `STATE_PAUSED = "paused"`, `STATE_STOPPING = "stopping"`, `STATE_STOPPED = "stopped"` y `LIVE_STATES = (STATE_SENDING, STATE_PAUSED, STATE_STOPPING)`. `state` es `String(20)` sin enum de DB (models.py:198) — **no hay ALTER de columna**; los valores nuevos no necesitan migración de datos.
  - [x] `get_live_batch` (repos/batches.py:31): el predicado pasa de `state == STATE_SENDING` a `state.in_(LIVE_STATES)`, y agregar `.order_by(Batch.id)` (determinismo si el invariante se rompiera — fix del review 2.2). Mantener el parámetro `for_update`.
  - [x] **Migración** (`alembic revision -m "one live batch per tenant"`, `down_revision = "b8e52d0cf1a4"` — head actual): índice único parcial `uq_batches_one_live_per_tenant ON batches (tenant_id) WHERE state IN ('sending','paused','stopping')`. Autogenerate NO captura índices parciales de forma fiable — escribir el `op.create_index(..., postgresql_where=...)` a mano y reflejarlo en `Batch.__table_args__` (models.py:177+) con `Index(..., unique=True, postgresql_where=text("state IN ('sending','paused','stopping')"))` para que un autogenerate posterior dé diff vacío. `alembic upgrade head` en el Postgres de dev. **Producción tiene filas vivas potencialmente**: si al aplicar existieran duplicados (dos lotes vivos del mismo tenant por el bug de concurrencia), la migración fallaría — agregar en el upgrade un saneo previo (los lotes vivos duplicados más viejos por tenant pasan a `'stopped'`; conservar el de `id` mayor).
  - [x] En `POST /api/batches` (`backend/app/api/batches.py`, rama "New batch" líneas 86–116): envolver el `commit()` de creación en `try/except IntegrityError` → `rollback()` → re-leer `get_live_batch(for_update=True)` → si ahora hay lote vivo, ejecutar la rama de append (dos tabs creando a la vez: el segundo se convierte en append, nunca 500). Importar `IntegrityError` de `sqlalchemy.exc`.
- [x] Task 2: helpers de repo para los controles (AC: 1, 4)
  - [x] `get_batch(session, tenant_id, batch_id, *, for_update=False) -> Batch | None` — **tenant-scoped** (el id de otro tenant devuelve None → 404; AC 1 "only that client's batch is affected").
  - [x] `delete_queued_lines(session, batch_id) -> int` — `DELETE FROM batch_lines WHERE batch_id=… AND state='queued'` (el stop "clears the remaining queue"). Ejecutar SIEMPRE antes del chequeo de línea en vuelo (ver Task 4 — el orden importa para la carrera con el claim del worker).
  - [x] `has_sending_line(session, batch_id) -> bool` — ¿hay una línea en estado `'sending'` (en manos del worker)?
  - [x] `mark_queued(session, line) -> None` — devolver una línea reclamada a `'queued'` (el "release" de pausa, Task 6).
  - [x] `delete_line(session, line_id) -> None` — descartar la línea en vuelo abandonada por un stop (nunca se envió; la cola ya se borró).
  - [x] `get_batch_state(session, batch_id) -> str | None` — lectura corta del estado para el re-chequeo del worker (sesión propia, sin lock).
- [x] Task 3: errores nuevos en `backend/app/errors.py` (AC: 1)
  - [x] `batch_not_found()` → 404 `batch_not_found` "Ese lote no existe." (id desconocido, id de otro tenant, id > int32 — no filtrar existencia).
  - [x] `batch_not_live()` → 409 `batch_not_live` "Ese lote ya terminó." (acción sobre `completed`/`stopped`).
  - [x] `batch_stopping()` → 409 `batch_stopping` "El lote se está deteniendo. Espera un momento." (pause/resume sobre `stopping`; también el append durante `stopping`, Task 4).
- [x] Task 4: endpoints `POST /api/batches/{id}/pause|resume|stop` → 204 (AC: 1, 3, 4)
  - [x] En `backend/app/api/batches.py` (extender el router existente; architecture.md: acción no-CRUD = sufijo verbal POST, respuesta 204 sin body). Dependencias `get_current_user` + `get_session` como el POST actual. Guard de id: reutilizar `_PG_INT_MAX` (api/batches.py:28) → fuera de rango = `batch_not_found()`.
  - [x] Flujo común: `get_batch(session, user.tenant_id, batch_id, for_update=True)` → None → 404. Matriz de transiciones (decisión registrada — idempotencia para el caso dos-tabs):
    - **pause**: `sending`→`paused` (commit + evento `paused` + `send_worker.wake()` — el wake corta el sleep del intervalo al instante, AC 3); `paused`→204 no-op sin evento; `stopping`→409 `batch_stopping`; `completed`/`stopped`→409 `batch_not_live`.
    - **resume**: `paused`→`sending` (commit + evento `sending` + `wake()` — si el worker estaba esperando, reintenta ya: semántica legacy "pause→resume puede reintentar antes de que venza la ventana"); `sending`→204 no-op; `stopping`→409 `batch_stopping`; terminal→409 `batch_not_live`.
    - **stop**: `sending`|`paused`→ dentro de la MISMA transacción y EN ESTE ORDEN: `delete_queued_lines()` primero (un DELETE concurrente con el claim del worker bloquea sobre la fila en disputa y la salta si quedó `'sending'`), después `has_sending_line()`: si **no** hay línea en vuelo → `state='stopped'` + commit + evento `idle`; si **sí** hay → `state='stopping'` + commit + evento `stopping` + `wake()` (el worker abandona la línea y finaliza, Task 6). `stopping`→204 no-op; terminal→409 `batch_not_live`. **Sin modal de confirmación — Detener actúa al instante (AC 4).**
  - [x] Guard de append: en el POST existente, si `live.state == STATE_STOPPING` → `batch_stopping()` 409 (la cola se acaba de borrar; líneas anexadas quedarían huérfanas en un lote `stopped`). Append con `live.state == 'paused'` SÍ se permite (legacy `_lote_vivo` incluía pausado) — emite `batch.progress` como hoy y NO cambia el estado.
- [x] Task 5: `batch.state` con contexto completo (AC: 1, 2; resuelve diferido 2-2 #6)
  - [x] Helper `state_data(batch, state) -> dict` en `backend/app/services/batches.py`: `{"batch_id": batch.id, "state": state, "gate_name": batch.gate_name, "gate_value": batch.gate_value}` — el ejemplo de architecture.md ya trae `batch_id`: `{"event": "batch.state", "data": {"batch_id": 7, "state": "paused"}}`. Los estados emitidos son los de superficie: `idle | sending | paused | stopping` (en DB los terminales son `completed`/`stopped`; al emitir, ambos viajan como `"idle"` — patrón ya establecido en 2.2).
  - [x] Actualizar los DOS emisores existentes: lote nuevo en `api/batches.py:101-103` (hoy emite `{"state": "sending"}` pelado — la segunda pestaña nunca aprende el gate, bug del review) y drenado en `send_worker.py:105` (`{"state": "idle"}` → con contexto del batch). Los emisores nuevos de pause/resume/stop (Task 4) y de la finalización del worker (Task 6) usan el mismo helper.
- [x] Task 6: worker consciente de pause/stop (AC: 1, 3, 4)
  - [x] `_send_with_retries` (`backend/app/core/send_worker.py:109`) cambia de firma a `(tenant_id, batch_id, text) -> Literal["sent", "release", "abort"]`. Al TOPE de cada iteración del bucle de reintentos (incluida la primera, y tras CADA `sleep_cancelable` interrumpido por `wake()`): leer `get_batch_state(batch_id)` en una sesión corta —
    - `'sending'` → intentar el envío (FloodWait y errores genéricos siguen igual: emitir `flood.wait` global / `error` tenant-scoped, dormir cancelable, volver al tope del bucle — donde el re-chequeo decide).
    - `'paused'` → return `"release"` (NO retener la línea: con un solo worker y sin scheduler hasta 2.4, retenerla bloquearía a los demás tenants).
    - `'stopping'` / `'stopped'` / None → return `"abort"`.
  - [x] `step()` (send_worker.py:61) maneja los tres resultados:
    - `"sent"` → camino actual de registro (paso 3), con UNA rama nueva: re-leer el batch; si `state == 'stopping'` (el stop aterrizó mientras `gateway.send` estaba en vuelo) → `mark_sent`, `state='stopped'`, commit, emitir `batch.line_sent` + `batch.state idle` — NO pasar por `complete_if_drained` (marcaría `'completed'`, repos/batches.py:193). El resto del camino actual queda igual.
    - `"release"` → sesión nueva: `mark_queued(line)` (la línea vuelve a la cola intacta; al reanudar se reclama de nuevo — mismo efecto neto que el legacy), commit, return False (el bucle exterior duerme el idle-sleep de 1s, no el intervalo).
    - `"abort"` → sesión nueva: `delete_line(line_id)` (nunca se envió; la cola ya fue borrada por el handler), re-leer batch: si `state == 'stopping'` → `state='stopped'`, commit, emitir `batch.state idle`. Return False.
  - [x] El primitivo ya existe completo: `_wake` (send_worker.py:41), `wake()` (:44), `sleep_cancelable` (:49) — 2.2 lo construyó EXACTAMENTE para esto; no reescribirlo. `next_queued_line` (repos/batches.py:139) ya filtra `Batch.state == 'sending'`, así que los lotes pausados/deteniéndose no entregan líneas sin cambio alguno.
  - [x] Comentario en el código: la pareja release/abort es la versión 2.3; Story 2.5 introduce `cancelled` + send_log y Story 2.4 reemplaza la selección FIFO — no construir nada de eso.
- [x] Task 7: `/ws` registra antes del snapshot (AC: 1; resuelve diferido 2-2 #3)
  - [x] En `backend/app/api/ws.py:66-70`: mover `broadcaster.register(tenant_id, websocket)` ANTES de construir/enviar el snapshot (el `unregister` queda en el `finally` actual). Hoy un evento emitido en el hueco snapshot→register se pierde; si ese frame era el `batch.state idle` terminal, la pestaña queda "Enviando" para siempre — la antítesis del AC 1. Orden seguro: register → construir snapshot (lectura DB) → `send_json` — los frames WS son FIFO por socket y el snapshot, construido después, siempre es igual o más fresco que cualquier evento que se cuele antes; el reducer del frontend REEMPLAZA el store con cada snapshot, así que el interleaving es inocuo.
- [x] Task 8: snapshot con los estados nuevos (AC: 1, 2, 5)
  - [x] `services/batches.snapshot` (services/batches.py:52): la rama viva hoy devuelve `"state": "sending"` hardcodeado — debe devolver `batch.state` tal cual (`sending`/`paused`/`stopping` son passthrough; `get_live_batch` ya ampliado los encuentra). Una pestaña abierta con el lote pausado debe renderizar pill "En pausa" + ring warning + "ETA al reanudar" SOLO desde el snapshot. La forma del snapshot no cambia en nada más (`eta_seconds` = `queued × intervalo`, honesto, igual que hoy).

### Frontend (Tasks 9–14)

- [x] Task 9: store WS `frontend/lib/ws.ts` (AC: 1, 2, 5, 6)
  - [x] Ampliar `LiveBatchState.state` y `SnapshotData.state` a `"idle" | "sending" | "paused" | "stopping"`. Agregar `floodUntil: number | null` al store (epoch ms en que termina la espera de FloodWait; `null` = sin aviso). `IDLE` lo incluye en `null`.
  - [x] `BatchStateData` (ws.ts:47) pasa a `{ state: "idle"|"sending"|"paused"|"stopping"; batch_id: number | null; gate_name: string | null; gate_value: string | null }` (payload nuevo del Task 5). Reducer `batch.state`: `idle` → `setStore(IDLE)` (igual que hoy); cualquier otro → setear `state` + `batchId` + `gateName` + `gateValue` (arregla el chip vacío en la segunda pestaña) y, si `state === "sending"`, limpiar `floodUntil` (el envío se reanudó → el aviso se autodescarta, AC 6).
  - [x] **Bug a corregir en `batch.progress` (ws.ts:98-107):** hoy fuerza `state: "sending"`. Con pausa, un append durante `paused` emite `batch.progress` y la UI "inventaría" un estado (violación directa del AC 1). El reducer de `batch.progress` NO debe tocar `state` — solo counts/eta/batchId. También limpia `floodUntil` (llegó progreso ⇒ el envío fluye de nuevo).
  - [x] Reducer `flood.wait` (evento ya emitido por el backend desde 2.2, send_worker.py:117): `floodUntil = Date.now() + seconds * 1000`.
  - [x] `batch.line_sent` (hoy no-op, ws.ts:120): además de tolerarse, limpia `floodUntil` (una línea salió ⇒ resumed).
  - [x] `snapshot` REEMPLAZA el store completo como hoy; el snapshot no trae flood info → `floodUntil: null` (un reconnect descarta el aviso: honesto, el countdown ya no es verificable).
- [x] Task 10: controles `frontend/components/batch/batch-controls.tsx` (nuevo) (AC: 1, 2, 4)
  - [x] Conjunto visible según la máquina de estados, SIN saltos optimistas: `sending`→Pausar+Detener; `paused`→Reanudar+Detener; `stopping`→Pausar+Detener renderizados pero `isDisabled` (decisión registrada: se congela el par visible); `idle`→no renderizar nada (el slot ya existe vacío en `app/(client)/page.tsx:42` con el comentario "Controls slot — EMPTY until Story 2.3").
  - [x] Acciones: `useMutation` → `api.post<void>(`/api/batches/${live.batchId}/pause`)` (ídem `resume`/`stop`; `lib/api.ts` ya tolera 204 sin body — el logout lo usa así). Deshabilitar los botones mientras `isPending` (lección 2.1: guard de re-submit). **El estado de la UI SOLO cambia cuando llega el `batch.state` resultante** — el onSuccess no toca el store. **Detener dispara directo, sin modal ni confirm inline** (AC 4; EXPERIENCE.md: confirm solo en Eliminar, nunca en Detener).
  - [x] onError: `err instanceof ApiError` → mostrar `err.message` (los 409/404 traen español del servidor) en un texto inline bajo los botones; fallback de red "No pudimos conectar. Intenta de nuevo." El siguiente `batch.state`/`snapshot` reconcilia solo.
  - [x] Estilo (DESIGN.md `components.control-button`): par a lo ancho bajo el ring en mobile; Pausar = bg `surface-secondary` + texto `warning`; Detener = bg `surface-secondary` + texto `danger`; Reanudar = relleno sólido `success` + texto `success-foreground` (el único control sólido). Radius por defecto del tema.
- [x] Task 11: state pill (AC: 2)
  - [x] En el header de `frontend/components/client-nav.tsx` (EXPERIENCE.md la ubica en el header de Envío; el header desktop de DESIGN.md lista "brand, nav, state pill" — `ClientNav` ya lee `useLiveBatch`, ws.ts:185). HeroUI `Chip`, `rounded-full` (la ÚNICA pieza full-round del sistema), uppercase tracked 10px. Copy verbatim del estado: `sending`→"Enviando", `paused`→"En pausa", `stopping`→"Deteniendo", `idle`→oculta (no renderizar).
  - [x] Colores (`components.state-pill`): Enviando = bg accent-tint `oklch(55% 0.12 243 / .22)`; En pausa = bg amber-tint `oklch(78.19% 0.1593 71.03 / .18)` (dark: `oklch(82.03% 0.1395 75.04 / .18)`). Deteniendo: token NO definido en DESIGN.md — decisión registrada: tint de `danger` al mismo ~18% de alpha (Detener viste danger); es un estado de sub-segundos en la práctica.
- [x] Task 12: ring, ETA y formulario en los estados nuevos (AC: 2, 5)
  - [x] `frontend/app/(client)/page.tsx:34`: el ring se muestra cuando `live.state !== "idle"` (hoy solo con `"sending"` — un lote pausado haría desaparecer el ring). Insertar `<BatchControls live={live} />` y `<FloodNotice />` en el slot de controles (orden mobile DESIGN.md: ring → controles → data-panel).
  - [x] `frontend/components/batch/progress-ring.tsx:19-23`: `ProgressCircle` cambia `color` según estado — `sending`→`accent`, `paused`/`stopping`→`warning` (AC 2 "the ring switches accent↔warning"; DESIGN.md `color-paused: {colors.warning}`; para `stopping` no hay token — warning como "vivo pero no enviando", decisión registrada). Track sin cambio.
  - [x] Etiqueta del ETA: el `Metric` de ETA (progress-ring.tsx:44) usa label `"ETA al reanudar"` cuando `live.state === "paused"`, `"ETA"` en el resto. El VALOR no cambia (`formatEta` de metric.tsx:33 queda igual — estimado "~12 min", nunca countdown preciso).
  - [x] `frontend/components/batch/send-form.tsx:66`: `isLive` pasa de `live.state === "sending"` a `live.state !== "idle"` (con lote pausado el selector sigue bloqueado, el chip visible y el append permitido). Botón Enviar: `isDisabled={mutation.isPending || live.state === "stopping"}` (el backend responde 409 `batch_stopping` — defensa en ambas capas; el código cae al banner por el camino default del onError existente, sin trabajo extra).
- [x] Task 13: aviso FloodWait `frontend/components/batch/flood-notice.tsx` (nuevo) (AC: 6)
  - [x] Lee `floodUntil` del store (`useLiveBatch`). `null` o vencido → no renderiza nada. Activo → tira estilo `Alert` ámbar (DESIGN.md `components.flood-notice`): bg `oklch(78.19% 0.1593 71.03 / .12)`, borde `oklch(... / .5)` (variantes dark en el token), texto 12px, countdown en **mono ámbar**.
  - [x] Copy EXACTO con countdown vivo: "Telegram pidió esperar {N} s — reanudamos solos." — `N` decrece cada segundo (un `setInterval` local de 1s recalcula `Math.ceil((floodUntil - Date.now()) / 1000)`; limpiar el interval al desmontar). Se autodescarta al llegar a 0 o cuando el store limpia `floodUntil` (progress/line_sent/state sending — Task 9).
  - [x] **NUNCA styling de error** (rojo prohibido para FloodWait — DESIGN.md: "Amber for pause/FloodWait, red only for destructive/failed"). Tono informativo, el sistema se encarga solo.
- [x] Task 14: live dot del nav (AC: 6)
  - [x] `frontend/components/client-nav.tsx:43-48,73`: el dot de Envío hoy solo existe verde con `sending`. Pasa a: `sending`→`bg-success`; `paused`→`bg-warning`; `stopping`→`bg-warning` (decisión registrada: vivo-pero-no-enviando viste warning); `idle`→sin dot. El comentario de la línea 4-5 ("warning-while-paused arrives with 2.3") se cumple aquí — actualizarlo.
- [x] Task 15: regenerar tipos API (AC: 1)
  - [x] Los tres endpoints nuevos aparecen en OpenAPI → regenerar `frontend/types/api.ts` (`npm run generate:api` con el backend corriendo, o el camino offline de 2.2: `app.openapi()` → JSON → `npx openapi-typescript`). GENERATED — no editar a mano. Los payloads WS siguen tipados a mano en `lib/ws.ts` (excepción documentada).

### Tests + gates (Tasks 16–17)

- [x] Task 16: tests backend (AC: 1–5)
  - [x] **Promover a `backend/tests/conftest.py`** las fixtures hoy locales de `test_batches.py` que 2.3 necesita compartir: `gate` (cataloga un gate+categoría), `client_user`, `fake_gateway` (la clase `FakeGateway` YA vive en conftest.py:25; las fixtures que la montan no), `authorized_gateway` (autouse, monkeypatch de `gateway.authorized`/`target_ok`). Ajustar los imports de `test_batches.py` — sus 107+ tests actuales deben seguir verdes.
  - [x] Nuevo `backend/tests/test_batch_controls.py` (idiom ASGI de conftest: self-seeding, self-cleaning):
    - pause feliz: crear lote → `POST /api/batches/{id}/pause` → 204; en DB `state='paused'`; `send_worker.step()` → False y `fake_gateway.sent` vacío (el worker no toca lotes pausados).
    - resume feliz: → 204, `state='sending'`, `step()` envía la siguiente línea.
    - stop sin línea en vuelo: → 204, las líneas `queued` borradas, `state='stopped'`; un POST /api/batches posterior crea un lote NUEVO (id distinto, `state='sending'`).
    - stop con línea en vuelo: reclamar a mano (`batches_repo.mark_sending` + commit) → stop → 204 y `state='stopping'`; luego `_send_with_retries(...)` devuelve `"abort"` y el camino abort de `step()`/finalización deja la línea borrada y el lote `'stopped'`.
    - release por pausa: reclamar a mano, poner el lote en `paused`, `_send_with_retries` → `"release"` sin enviar nada; la línea vuelve a `'queued'`.
    - idempotencia: pause sobre `paused` → 204; resume sobre `sending` → 204; stop sobre `stopping` → 204 (sin eventos duplicados).
    - transiciones inválidas: pause/resume/stop sobre `completed` y `stopped` → 409 `batch_not_live`; pause/resume sobre `stopping` → 409 `batch_stopping`.
    - scoping: anónimo → 401; tenant B sobre el lote de A → 404 `batch_not_found`; id > int32 → 404.
    - append en pausa: POST /api/batches con lote `paused` → `appended=True` y el estado en DB sigue `paused`; append en `stopping` → 409 `batch_stopping`.
    - eventos: fixture que monkeypatchea `broadcaster.emit`/`emit_global` con una lista grabadora → pause emite `batch.state` con `{batch_id, state:"paused", gate_name, gate_value}` (Task 5); stop sin línea en vuelo emite `idle`; el POST de lote nuevo ahora emite `sending` CON los campos del gate.
    - índice único parcial: insertar vía repo un segundo batch `'sending'` (o `'paused'`) del mismo tenant → `pytest.raises(IntegrityError)`; y `get_live_batch` encuentra lotes `paused`/`stopping`.
    - snapshot: con lote pausado → `state == "paused"` (resto de la forma intacta); con `stopping` → `"stopping"`.
    - `sleep_cancelable` + `wake()`: un sleep de 10s retorna en <1s tras `wake()` (acotar con `asyncio.wait_for`).
- [x] Task 17: gates de verificación (todos los AC)
  - [x] Backend: `ruff check .`, `mypy app`, `pytest` — todo verde (la suite previa completa incluida).
  - [x] Frontend: `npm run lint`, `npx tsc --noEmit`, `next build` — verdes.
  - [ ] (HUMAN — necesita credenciales reales de Richard; envía mensajes reales — PENDIENTE, acción manual del owner) Smoke manual en dev: lote de ~6 líneas → Pausar a mitad (pill "En pausa", ring warning, "ETA al reanudar", dot ámbar; segunda pestaña idéntica vía snapshot) → Reanudar (reintento inmediato) → Detener (cola vacía al instante, sin modal, superficie idle). FloodWait real es difícil de provocar — el aviso se valida con los tests del reducer/manualmente emitiendo el evento si hace falta. **No correr contra producción sin el OK de Richard.**

## Dev Notes

### Qué NO es esta story (cerco de alcance)

- **Scheduler round-robin multi-tenant, prioridad owner, intervalo adaptativo `G = max(G_min, P(n)/n)`, governor de FloodWait** → Story 2.4. Aquí el intervalo sigue siendo `settings.send_interval_seconds` fijo y la selección FIFO global (`next_queued_line`). La exclusión de tenants pausados del cálculo de `n` también es 2.4 — aquí "pausado" solo significa que sus líneas no se reclaman.
- **`send_log`, retry cap = 3 + estado `failed`, estado `cancelled`, fail-stop sin DB, reconciliación post-restart** → Story 2.5. El retry-forever de errores genéricos NO se toca (los comentarios `# Story 2.5` de send_worker.py:37,98,125 se conservan).
- **Captura de respuestas, CC, paneles Completa/Filtrada** → Epic 3 ("CC NUEVAS" sigue mostrando el 0 hardcodeado del snapshot).
- **Diferidos de 2.2 que SIGUEN diferidos** (deferred-work.md): re-validación de auth en sockets ya abiertos (#4 — un usuario bloqueado mantiene el stream hasta que el socket muere; arquitectura de cierre por sesión, no es de esta story) y retry acotado de la transacción de registro post-send (#5 — territorio de 2.5). No los arregles "de paso".
- **Sin endpoints GET/list de batches** — el snapshot WS sigue siendo el único read path.

### Máquina de estados (diseño registrado)

| Estado DB (`batches.state`) | Estado de superficie (WS/UI) | Quién lo pone | Sale por |
| --- | --- | --- | --- |
| `sending` | `sending` | POST lote nuevo; resume | pause / stop / drenado |
| `paused` | `paused` | pause | resume / stop |
| `stopping` | `stopping` | stop CON línea en vuelo | el worker finaliza → `stopped` |
| `stopped` | `idle` (terminal) | stop sin línea en vuelo; finalización del worker | — |
| `completed` | `idle` (terminal) | `complete_if_drained` (2.2) | — |

- `LIVE_STATES = (sending, paused, stopping)` — es el conjunto de `get_live_batch`, del predicado del índice único parcial y de la noción "lote vivo" del POST (append).
- `stopping` existe SOLO porque el worker puede tener una línea reclamada (mid-`gateway.send` o mid-FloodWait). Si no la hay, stop va directo a `stopped` y la UI nunca ve "Deteniendo". La ventana típica es sub-segundo (el `wake()` corta cualquier sleep al instante); el peor caso es la duración de un `gateway.send` en vuelo.
- `stopped` ≠ `completed` a propósito (el docstring del modelo de 2.2 ya lo anunciaba): Epic 3 distinguirá lotes drenados de lotes detenidos en el historial.
- **Pausa = release, no hold:** cuando el worker despierta con el lote pausado, devuelve la línea reclamada a `queued` y sigue (con un único worker global, retenerla bloquearía a los demás tenants hasta 2.4). Reanudar la reclama de nuevo de inmediato — mismo efecto neto que el legacy "pause→resume puede reintentar antes de que venza la ventana de FloodWait".
- **Stop con send en vuelo que SÍ salió:** la línea se registra como `sent` (honesto — salió a Telegram) y el lote finaliza `stopped`. No pasar por `complete_if_drained` en ese caso (marcaría `completed`).

### Código actual que vas a tocar (estado HOY, con anclas)

| Archivo | Hoy | Esta story |
| --- | --- | --- |
| `backend/app/db/repos/batches.py` | constantes :20-28; `get_live_batch` solo `sending` :46-51; `next_queued_line` filtra `Batch.state=='sending'` :139-148 (NO cambia); `complete_if_drained` con FOR UPDATE :164 | constantes nuevas + `LIVE_STATES`; ampliar `get_live_batch` + `order_by(Batch.id)`; helpers Task 2 |
| `backend/app/api/batches.py` | solo `POST ""` ; `_PG_INT_MAX` :28; 503 gate :66; `get_live_batch(for_update=True)` :81; emite `batch.state {state:"sending"}` pelado :101-103 | + 3 endpoints de control; guard `batch_stopping` en append; IntegrityError→append; emisores con `state_data` |
| `backend/app/core/send_worker.py` | `wake`/`sleep_cancelable` :41-58 (LISTOS, no tocar); `step` :61; `_send_with_retries(tenant_id, text)` retry infinito sin re-chequeo :109-126; emite idle pelado :105 | re-chequeo de estado por iteración; `sent/release/abort`; finalización `stopping→stopped` |
| `backend/app/services/batches.py` | `eta_seconds` :32; `progress_data` :40; `snapshot` con `"sending"` hardcodeado :73 | + `state_data`; snapshot passthrough del estado |
| `backend/app/errors.py` | hasta `telegram_unauthorized` (2.2) | + `batch_not_found`, `batch_not_live`, `batch_stopping` |
| `backend/app/api/ws.py` | snapshot se envía :68 ANTES de `register` :70 | register primero (Task 7) |
| `backend/app/db/models.py` | `Batch` :177, `state String(20)` :198 sin enum | `__table_args__` con el índice parcial |
| `backend/tests/test_batches.py` | fixtures locales `authorized_gateway`/`fake_gateway`/`gate`/`client_user` :35-96 | promover a conftest, imports |
| `frontend/lib/ws.ts` | `state` solo `idle\|sending`; `batch.progress` fuerza `"sending"` :98-107 (**bug con pausa**); `batch.state` shape `{state}` :47,109-119 | Task 9 completo |
| `frontend/app/(client)/page.tsx` | ring solo si `sending` :34; slot de controles vacío :42 | ring si `!== "idle"`; montar controles + flood notice |
| `frontend/components/batch/send-form.tsx` | `isLive = state === "sending"` :66 | `!== "idle"`; Enviar disabled en `stopping` |
| `frontend/components/batch/progress-ring.tsx` | `color="accent"` fijo :21 | accent↔warning por estado; label ETA condicional |
| `frontend/components/client-nav.tsx` | dot solo success con `sending` :43-48,73 | success/warning por estado |
| `frontend/types/api.ts` | GENERATED | regenerar |

Head de migraciones: `b8e52d0cf1a4` (batches and batch_lines). Esta story agrega UNA revisión (índice único parcial + saneo de duplicados vivos).

`deploy/Caddyfile`, `middleware.ts`, `.env`: **cero cambios** — los endpoints nuevos viven bajo `/api/*` (ya ruteado y excluido del matcher) y no hay claves de entorno nuevas. Mergear a main auto-despliega (CI de 1.7).

### Cumplimiento de arquitectura (no negociable)

- Acciones no-CRUD = `POST /api/batches/{id}/pause|resume|stop` → `204`; errores `{code, message}` (snake_case + español); `tenant_id` JAMÁS del body. [Source: architecture.md#API-Naming-Conventions, #Format-Patterns]
- WS server→client only, envelope `{"event","data"}`, `batch.state` con `batch_id` (ejemplo literal de architecture.md#Pattern-Examples); todos los eventos tenant-scoped salvo `flood.wait` global. [Source: architecture.md#Communication-Patterns]
- Máquina de estados del lote: `idle | sending | paused | stopping` — fuente única `batch.state`; el frontend tiene UN reducer por evento en el store único; los componentes nunca hacen fetch crudo. [Source: architecture.md#Process-Patterns, #State-Management]
- Telethon confinado a `core/telegram.py` (esta story NO lo toca); eventos solo vía broadcaster; toda alteración de esquema = migración Alembic. [Source: architecture.md#Enforcement-Guidelines]

### Requisitos UX (DESIGN.md / EXPERIENCE.md — lee "prefijo" como "gate")

- **Controles (EXPERIENCE.md#Component-rules):** actúan SOLO sobre el lote propio (FR15); presionar dispara REST y la UI cambia únicamente con el `batch.state` resultante — cero saltos optimistas (UX-DR12/UX-DR5). Single-tap, server-confirmed. Detener sin confirmación (instantáneo); el confirm queda reservado a Eliminar (Epic 3).
- **Pill (DESIGN.md `components.state-pill`):** Chip `rounded.full` (única pieza full-round), uppercase tracked 10px; Enviando = accent-tint, En pausa = amber-tint; oculta en idle. Copy verbatim: Enviando / En pausa / Deteniendo.
- **Ring (`components.progress-ring`):** stroke `accent` enviando, `warning` pausado; track `surface-tertiary`. El flanco sigue siendo EXACTAMENTE 3 métricas (UX-DR3) — pausar NO agrega stats.
- **ETA (UX-DR14):** estimado honesto "~12 min" recomputado por evento; en pausa la ETIQUETA cambia a "ETA al reanudar" (el valor congela el último estimado); nunca countdown con precisión falsa. El countdown del FloodWait notice es la excepción explícita (es una espera impuesta con duración conocida).
- **FloodWait (`components.flood-notice`):** ámbar SIEMPRE (informational, "paused and waiting, not broken"); copy exacto "Telegram pidió esperar N s — reanudamos solos."; countdown en `data-mono`; se autodescarta al reanudar. Rojo prohibido para FloodWait (DESIGN.md anti-pattern explícito).
- **Nav dot (UX-DR10 / `components.bottom-nav`):** 6px, success enviando, warning pausado; mismo dot en bottom-nav mobile y header desktop.
- **Microcopy (tuteo, exacto):** botones "Pausar" / "Reanudar" / "Detener"; pill "Enviando" / "En pausa" / "Deteniendo"; "ETA al reanudar". Mono SOLO para datos (countdown, ETA, contadores); Public Sans para frases.

### Inteligencia de stories previas (2.2 + 2.1)

- **2.2 dejó los cimientos a propósito:** `wake()`/`sleep_cancelable` (send_worker.py:41-58) se construyeron en 2.2 "para que 2.3 no reescriba el worker" —ConsÚMELOS, no los dupliques. El evento `flood.wait {seconds}` ya se emite global (send_worker.py:117); esta story solo agrega el consumidor UI. `FakeGateway` (conftest.py:25) es programable con `errors` (lista de excepciones que `send` lanza en orden) — diseñado para que 2.3/2.4/2.5 lo reúsen.
- **Lecciones del review 2.1/2.2 que aplican:** ids fuera de int4 → 404 (no 500 de asyncpg) — usa `_PG_INT_MAX`; guard de re-submit con `isPending`; atrapar `IntegrityError` además del chequeo aplicativo (TOCTOU); los cuerpos 422 no traen `{code,message}` (lib/api.ts ya normaliza).
- **Semántica legacy que se preserva** (project-context.md / CLAUDE.md raíz): sleeps cancelables — pausa/stop interrumpen al instante; stop limpia la cola y sale de un FloodWait sin esperar; pause→resume puede reintentar antes de que venza la ventana. Lo ÚNICO que cambia vs legacy: el estado vive en Postgres (NFR6), no en un `Engine` en memoria.
- **1.7/CI:** Conventional Commits con scope, rama `story/2.3-pausar-reanudar-y-detener-con-eta-honesto`; push a main = deploy automático al VPS. Producción puede seguir 503 `telegram_unauthorized` hasta que Richard complete el AC4 de 1.7 (re-auth de `anon.session` en el VPS) — no bloquea esta story.

### Estándares de testing

- `pytest` + `pytest-asyncio` (`loop_scope="session"`) + httpx `ASGITransport` contra la app real y el Postgres de dev; self-seed/self-clean; sin mocks de DB; un comportamiento por test; assert de status + forma del body. ASGITransport NO corre el lifespan → ni Telethon ni worker de fondo: los tests del worker llaman `step()`/`_send_with_retries` directamente con `FakeGateway` y `send_interval_seconds=0` (idiom ya establecido en test_batches.py:375+).
- Los eventos se verifican monkeypatcheando `broadcaster.emit`/`emit_global` con una lista grabadora — no montes sockets para esto (lección 2.2: "don't burn hours on socket test plumbing").
- Frontend: sin framework de tests (decisión diferida) — gates = `eslint` + `tsc` + `next build`. No introducir vitest/jest.

### Notas de estructura del proyecto

- **Nuevos:** `backend/migrations/versions/<rev>_one_live_batch_per_tenant.py`, `backend/tests/test_batch_controls.py`, `frontend/components/batch/batch-controls.tsx`, `frontend/components/batch/flood-notice.tsx`.
- **Modificados:** `backend/app/db/repos/batches.py`, `backend/app/api/batches.py`, `backend/app/core/send_worker.py`, `backend/app/services/batches.py`, `backend/app/errors.py`, `backend/app/api/ws.py`, `backend/app/db/models.py`, `backend/tests/conftest.py`, `backend/tests/test_batches.py` (solo imports de fixtures), `frontend/lib/ws.ts`, `frontend/app/(client)/page.tsx`, `frontend/components/batch/send-form.tsx`, `frontend/components/batch/progress-ring.tsx`, `frontend/components/client-nav.tsx`, `frontend/types/api.ts` (regenerado), `_bmad-output/implementation-artifacts/deferred-work.md` (marcar resueltos los diferidos #1, #3 y #6 del review 2-2).
- El árbol de architecture.md ya contemplaba esto: "`api/batches.py` — /api/batches CRUD + pause|resume|stop" y "`components/batch/` — progress/eta, pause-resume-stop". Sin variaciones nuevas.
- Legacy `core.py`/`app.py`/`auto_sender.py` congelados en la raíz — solo referencia. **🔒 JAMÁS leer contenido bajo `respuestas/`. JAMÁS tocar `.env` ni `anon.session`.**

### Referencias

- [Source: planning-artifacts/epics.md#Story-2.3 — ACs verbatim; #Epic-2 (reparto del pipeline 2.2→2.5)]
- [Source: planning-artifacts/architecture.md#API-Naming-Conventions (pause|resume|stop → 204), #Communication-Patterns (envelope, batch.state con batch_id), #Process-Patterns (máquina de estados idle|sending|paused|stopping), #Pattern-Examples]
- [Source: planning-artifacts/ux-designs/ux-cc-2026-06-10/DESIGN.md — tokens state-pill / control-button / flood-notice / progress-ring.color-paused; ámbar = pausa/FloodWait, rojo solo destructivo]
- [Source: planning-artifacts/ux-designs/ux-cc-2026-06-10/EXPERIENCE.md — reglas de Control buttons / State pill / FloodWait notice / ETA display; Flow 1 pasos 7-8; confirm solo en Eliminar, no en Detener]
- [Source: implementation-artifacts/2-2-enviar-un-lote-un-cliente-con-progreso-en-vivo.md — wake/sleep_cancelable construidos para 2.3; evento flood.wait ya emitido; estados de superficie vs DB; FakeGateway; cerco de alcance]
- [Source: implementation-artifacts/deferred-work.md#Story-2-2-review — hallazgos #1 (índice parcial, "Note for 2.3"), #3 (register antes del snapshot), #6 (batch.state con gate) absorbidos aquí; #4 y #5 siguen diferidos]
- [Source: _bmad-output/project-context.md — 🔒 reglas: nunca leer respuestas/, nunca imprimir .env, sleeps cancelables / semántica FloodWait y stop del legacy]
- [Source: código actual @ af96a4e — backend/app/{core/send_worker.py, api/batches.py, api/ws.py, services/batches.py, db/repos/batches.py}, frontend/{lib/ws.ts, app/(client)/page.tsx, components/batch/*, components/client-nav.tsx}]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Fable 5) — BMad dev agent, 2026-06-12

### Debug Log References

- `MissingGreenlet` en el camino IntegrityError→append del POST: el `session.rollback()` expira el objeto `user` cargado por `get_current_user` y el acceso posterior a `user.tenant_id` dispara un refresh síncrono. Fix: capturar `tenant_id`/`is_owner` en locales ANTES de cualquier rollback (api/batches.py, comentado en el código). Detectado por `test_create_race_integrity_error_converts_to_append`.
- Tints del DESIGN.md (`accent/.22`, `warning/.18`, `danger/.18`, `warning/.12`, `warning/.50`) implementados como modificadores de opacidad Tailwind v4 sobre los tokens del tema (`bg-accent/22`, `bg-warning/18`, …): compilan a `color-mix(in oklab, var(--token) N%, transparent)` y siguen el dark mode solos (verificado en el CSS del build).

### Completion Notes List

- Tasks 1–17 completos. Gates verdes: backend `pytest` (125 passed — 12 nuevos en `test_batch_controls.py`), `ruff check app/ tests/`, `mypy app`; frontend `npm run lint`, `npx tsc --noEmit`, `next build`.
- Migración `1b606109cc99_one_live_batch_per_tenant` aplicada al Postgres de dev (`alembic upgrade head`); `alembic check` da diff vacío (índice reflejado en `Batch.__table_args__`). Incluye el saneo previo de duplicados vivos (conserva el id mayor por tenant; los demás → 'stopped').
- Resueltos los TRES diferidos absorbidos del review 2.2 (marcados en deferred-work.md): #1 índice único parcial + `order_by(Batch.id)` + IntegrityError→append; #3 `/ws` registra antes del snapshot; #6 `batch.state` con `{batch_id, state, gate_name, gate_value}` vía `state_data` en TODOS los emisores. #4 (WS-auth continua) y #5 (retry del registro post-send) siguen diferidos como manda la story.
- Worker: `_send_with_retries(tenant_id, batch_id, text) -> "sent"|"release"|"abort"` con re-chequeo del estado al tope de cada iteración; `step()` maneja los tres resultados con helpers `_release_line`/`_abort_line` (llamables desde tests). Decisiones de robustez más allá de la letra de la story, comentadas en el código: (a) el camino "sent" relee el batch con FOR UPDATE antes de elegir entre finalizar 'stopped' y `complete_if_drained` (sin el lock, un stop concurrente podía quedar pisado por 'completed'); (b) `_release_line` también lockea el batch y, si un stop aterrizó 'stopping' durante la ventana del release, aborta la línea y finaliza 'stopped' en vez de re-encolarla (evitaba un batch atascado en 'stopping' para siempre, que además bloquearía lotes nuevos vía el índice único).
- Pill "Deteniendo" con tint de danger ~18% y ring/dot en warning para 'stopping' (decisiones registradas en la story, sin token DESIGN.md).
- `types/api.ts` regenerado por el camino offline (`app.openapi()` → JSON → `npx openapi-typescript`); los tres endpoints nuevos presentes.
- PENDIENTE HUMANO (único item no automatizable): smoke manual en dev con credenciales reales de Richard (Task 17, tercer bullet) — envía mensajes reales por Telegram; no correr contra producción sin su OK.
- Sin commits ni push (instrucción del orquestador).

### File List

**Nuevos:**
- `backend/migrations/versions/1b606109cc99_one_live_batch_per_tenant.py`
- `backend/tests/test_batch_controls.py`
- `frontend/components/batch/batch-controls.tsx`
- `frontend/components/batch/flood-notice.tsx`

**Modificados:**
- `backend/app/db/repos/batches.py` — estados nuevos + `LIVE_STATES`; `get_live_batch` ampliado + `order_by(Batch.id)`; `get_batch`, `get_batch_state`, `delete_queued_lines`, `has_sending_line`, `mark_queued`, `delete_line`
- `backend/app/db/models.py` — `Batch.__table_args__` con el índice único parcial
- `backend/app/errors.py` — `batch_not_found`, `batch_not_live`, `batch_stopping`
- `backend/app/services/batches.py` — `state_data`; snapshot passthrough del estado
- `backend/app/api/batches.py` — IntegrityError→append; guard `batch_stopping` en append; endpoints pause/resume/stop (204); emisores con `state_data`; captura de `tenant_id` pre-rollback
- `backend/app/core/send_worker.py` — re-chequeo de estado por iteración; `sent/release/abort`; finalización `stopping→stopped`; `_release_line`/`_abort_line`
- `backend/app/api/ws.py` — register antes del snapshot
- `backend/tests/conftest.py` — fixtures promovidas: `authorized_gateway` (autouse), `fake_gateway`, `gate`, `client_user`
- `backend/tests/test_batches.py` — solo remoción de fixtures locales + imports
- `frontend/lib/ws.ts` — estados nuevos + `floodUntil`; `BatchStateData` con contexto; fix `batch.progress` no toca `state`; reducer `flood.wait`; `line_sent` limpia el aviso
- `frontend/app/(client)/page.tsx` — ring si `state !== "idle"`; monta `BatchControls` + `FloodNotice`
- `frontend/components/batch/progress-ring.tsx` — color accent↔warning; label "ETA al reanudar"
- `frontend/components/batch/send-form.tsx` — `isLive = state !== "idle"`; Enviar disabled en 'stopping'
- `frontend/components/client-nav.tsx` — state pill (Chip full-round) + dot success/warning
- `frontend/types/api.ts` — regenerado (GENERATED)
- `_bmad-output/implementation-artifacts/deferred-work.md` — diferidos #1/#3/#6 del review 2.2 marcados resueltos
