---
baseline_commit: 27f3170641b86c1ee8d6b501a1c76f509f816f7d
---

# Story 4.1: Watchdog de respuestas y detección de pérdida de sesión

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGÍA (decisión del owner 2026-06-11):** el término de producto para un prefijo es **"gate"** — DB, API, identificadores de código y todo el copy de UI (masculino: "el gate"). epics.md / architecture.md / docs de UX son anteriores al renombre y todavía dicen "prefijo/prefixes" — lee cada "prefijo" como "gate"; donde haya conflicto, gana "gate". Esta story no toca el catálogo, pero hereda la regla para todo copy nuevo.

## Story

As the owner,
I want automatic global pause when the bot stops replying or the Telegram session dies,
So that silent failures never burn the shared account.

## Acceptance Criteria

1. **Given** active sending, **when** the reply rate collapses over a sliding window (bot silently blocking the account), **then** the watchdog alerts the owner and auto-pauses global sending.
2. **Given** the Telethon client, **when** an `AuthKeyError` or deauthorization is detected, **then** global sending pauses immediately and the owner is alerted — the trigger for the re-auth runbook.
3. **Given** a watchdog-triggered global pause, **when** the owner has resolved the cause, **then** resuming is an explicit owner action — never automatic.
4. **Given** watchdog activity, **when** it fires or recovers, **then** every event is recorded in the structured logs.

## Tasks / Subtasks

### Backend (Tasks 1–7)

- [x] Task 1: migración + modelo — `watchdog_state` (AC: 3)
  - [x] `backend/app/db/models.py`: clase `WatchdogState`, tabla **`watchdog_state`** — UNA fila (id=1, app-enforced, sin tenant: estado GLOBAL de sistema, excepción documentada como `gates`). Columnas: `id` PK; `paused` Boolean `server_default=false()` NOT NULL; `reason` `String(40)` nullable (`'reply_rate_collapse' | 'session_lost'` — Strings sin enum de DB, decisión 2.2); `detail` `Text` nullable; `paused_at`/`resumed_at` timestamptz nullable; `updated_at`. Docstring: el latch DURABLE del pause global — un deploy (push a main reinicia los services) jamás reanuda solo (AC 3); la memoria del singleton es la autoridad operativa, esta fila es lo que sobrevive al restart.
  - [x] **Migración** (`watchdog state global pause latch`, `down_revision = "2faec0509cb8"` — head actual verificado): `op.create_table` con el idiom exacto de `2faec0509cb8` (`op.f('pk_…')`, `sa.text('false')`/`'now()'`). **NO correr `alembic upgrade` en el Postgres de dev** (constraint del entorno paralelo — el merge re-encadena y aplica); reflejar TODO en el modelo para autogenerate-diff vacío.
- [x] Task 2: repo — `backend/app/db/repos/watchdog.py` (AC: 3)
  - [x] NUEVO, estilo "pure ORM, flush not commit", SIN tenant scoping (estado de sistema, lo escribe solo el singleton del watchdog — sección documentada como la worker-section de batches.py): `get_state(session) -> WatchdogState | None` (fila id=1) y `save_state(session, *, paused, reason, detail, paused_at, resumed_at) -> WatchdogState` (get-or-create de la fila 1 + update, flush).
- [x] Task 3: `core/watchdog.py` — ventana deslizante + latch global + persistencia best-effort (AC: 1, 3, 4)
  - [x] NUEVO `backend/app/core/watchdog.py` (sin telethon, sin requests — frontera idéntica a scheduler.py): clase `Watchdog` con reloj inyectable (`time.monotonic`, idiom Scheduler) + singleton module-level `watchdog`. Estado: `_sends: deque[float]`, `_replies: deque[float]` (timestamps podados a `_WINDOW_SECONDS`), latch `_paused/_reason/_detail/_paused_at`.
  - [x] Constantes de módulo (regla 2.5: internals jamás son settings): `_WINDOW_SECONDS = 300.0` (ventana deslizante de 5 min), `_MIN_SENDS_IN_WINDOW = 5` (señal mínima), `_MIN_SILENCE_SPAN_SECONDS = 60.0` (el silencio debe ABARCAR ≥60s de envío: el send más viejo en ventana tiene esa edad). **Condición de colapso (decisión registrada):** `sends_en_ventana >= 5 AND replies_en_ventana == 0 AND span >= 60s` — cero respuestas con envío activo sostenido es la señal inequívoca de "bot silently blocking"; un umbral de ratio inventaría tuning sin datos y un falso positivo pausa a TODOS los tenants (el costo manda conservadurismo); el piso de span evita que la ráfaga inicial de un lote (las replies van DETRÁS de los sends) dispare en falso — y de paso impide que los loops rápidos de la suite (5 steps en milisegundos) latcheen el singleton en tests ajenos.
  - [x] `note_reply()` (sync): registra vida del bot. `async note_sent()`: registra el envío y evalúa el colapso → `trigger(REASON_REPLY_RATE, …)`. `async session_lost(detail)` → `trigger(REASON_SESSION_LOST, …)`. `async trigger(reason, detail)`: idempotente (ya pausado → no-op, sin eventos duplicados); latch en memoria PRIMERO (la guardia real), `logger.warning("event=watchdog_paused reason=… detail=… sends_in_window=… replies_in_window=…")` (AC 4), `_persist()` best-effort, `broadcaster.emit_global("watchdog.paused", {reason, detail, paused_at})` — global a propósito (idiom `flood.wait`): el pause afecta a todos y el tab del owner ES uno de ellos; la UI decide por rol qué mostrar.
  - [x] `async resume() -> bool`: solo lo llama el endpoint del owner (AC 3 — JAMÁS automático: ninguna ruta de código interna lo invoca; `note_reply` con el latch puesto NO despausa). No pausado → False (no-op idempotente, idiom de los controles 2.3). Limpia el latch Y las deques (**decisión registrada:** ventana fresca al reanudar — los timestamps pre-pausa re-dispararían el colapso al instante), `logger.info("event=watchdog_resumed …")` (AC 4), persiste, `emit_global("watchdog.resumed", {resumed_at})`.
  - [x] `_persist()` **best-effort** (decisión registrada): sesión corta propia (`async_session_factory`), commit; excepción → `logger.exception("event=watchdog_persist_failed …")` y seguir — el latch en memoria ES la guardia y con la DB caída el fail-stop de 2.5 ya bloquea todo envío; la fila solo compra durabilidad ante restarts.
  - [x] `async load_persisted()`: lifespan — restaura el latch desde la fila ANTES de que el worker pueda reclamar; fila ausente/no pausada/DB caída → arranca despausado con `event=watchdog_load_failed` si falló. Restaurado → `event=watchdog_restored` (AC 4). `status() -> dict` (`{paused, reason, detail, paused_at}`) para snapshot + GET. `is_paused` property. `reset()` para tests (memoria, no DB) + fixture autouse `reset_watchdog` en conftest (espejo `reset_scheduler`/`reset_capture` — la trampa del estado module-level).
- [x] Task 4: detección de pérdida de sesión en el gateway (AC: 2)
  - [x] `backend/app/core/telegram.py`: `class SessionLostError(Exception)` — excepción de DOMINIO que cruza la frontera (telethon sigue confinado; el worker no importa clases telethon). `_AUTH_LOSS_ERRORS = (UnauthorizedError, AuthKeyError)` de `telethon.errors.rpcbaseerrors` — `UnauthorizedError` es la base de TODOS los 401 (AUTH_KEY_UNREGISTERED, SESSION_REVOKED, SESSION_EXPIRED, USER_DEACTIVATED…) y `AuthKeyError` la base 406 (AUTH_KEY_DUPLICATED) — el "AuthKeyError or deauthorization" del AC literal.
  - [x] En `send()` y `recent_outgoing()`: catch `_AUTH_LOSS_ERRORS` → `self.authorized = False` (los POST /api/batches nuevos 503ean `telegram_unauthorized` solos) + `logger.error("event=session_lost source=… error=…")` + `raise SessionLostError(…) from e`. **Decisión registrada:** el gateway NO dispara el watchdog él mismo — el worker es quien tiene la línea reclamada en la mano y el contexto para soltarla; `recent_outgoing` (boot recovery) cae en su fallback `reconcile_unverified` existente y el primer `send` real dispara el latch.
  - [x] **Cerco (decisión registrada):** unauthorized EN EL BOOT (`connect()` sin sesión) NO dispara el watchdog — no es un fallo silencioso (warning + 503 en todo envío nuevo, semántica 2.2 intacta) y pausaría/alertaría en falso en un deploy fresco pre-auth. El watchdog cubre la pérdida EN CALIENTE, que es la que quema la cuenta. El runbook de re-auth es 4.4.
- [x] Task 5: worker — gate del latch + release de la línea + alimentación de la ventana (AC: 1, 2, 3)
  - [x] `send_worker.py` `step()`, paso 0 (ANTES de la ventana FloodWait): `if watchdog.is_paused: return False` — nadie reclama ni envía con el latch puesto; `run_worker` duerme `_IDLE_SLEEP_SECONDS` y rota. Reanudar = solo el endpoint del owner limpia el latch (+ `send_worker.wake()`).
  - [x] `_send_with_retries`: rama nueva `except SessionLostError` (ANTES del genérico) → return `("session_lost", detail)` — la pérdida de sesión NO es una línea mala: no cuenta para el cap de 3 ni marca `failed`. Firma: `int | tuple[Literal["failed", "session_lost"], str] | Literal["release", "abort"]`.
  - [x] `step()`: rama `session_lost` → `_release_line(…)` (la línea JAMÁS salió: vuelve a 'queued' intacta, mismo helper del pause — su manejo de la carrera con stop incluido) + `await watchdog.session_lost(detail)` + return False. El lote queda 'sending' en DB con el latch global encima — al reanudar, el worker lo retoma donde estaba.
  - [x] `_record_sent`: `await watchdog.note_sent()` tras el commit (junto al contador `_sent_by_tenant`) — solo envíos REALES alimentan la ventana (las confirmaciones de la reconciliación de boot NO: son entregas viejas).
- [x] Task 6: captura — `note_reply` en el arrival (AC: 1)
  - [x] `capture.py` `enqueue()`: `watchdog.note_reply()` — **decisión registrada:** se registra en el ARRIVAL (bridge), no en `process_incoming`: la señal es "el bot está vivo", independiente de la salud de la DB (con la DB caída los replies se acumulan en la cola y el watchdog NO debe disparar por eso — el fail-stop ya detuvo los envíos) y de la atribución (un reply unmatched también prueba vida). Sin ciclo de imports: watchdog no importa capture/telegram/send_worker.
- [x] Task 7: API owner-only + guard de envío + snapshot (AC: 1, 2, 3)
  - [x] NUEVO `backend/app/api/watchdog.py`: `GET /api/watchdog` (response_model `WatchdogStatusOut` `{paused, reason, detail, paused_at}`) + `POST /api/watchdog/resume` (204; idempotente — no pausado → 204 sin evento, idiom de los controles 2.3; reanuda + `send_worker.wake()`). AMBOS `Depends(require_role("owner"))` — el AC 3 literal: "resuming is an explicit owner action". Router en main.py.
  - [x] `errors.py`: `sending_paused()` 503 `{code: "sending_paused", message: "Los envíos están pausados por protección de la cuenta. Intenta más tarde."}`. `api/batches.py` `create_or_append_batch`: tras el check del gateway, `if watchdog.is_paused: raise sending_paused()` — **decisión registrada:** crear Y anexar se rechazan con el latch puesto (encolar líneas que no van a salir invita confusión; el banner del WS explica el estado).
  - [x] `services/batches.py` `snapshot`: slice `"watchdog": watchdog.status()` en AMBAS ramas (snapshot-first: un tab reconectado reconstruye el banner solo). `main.py` lifespan: `await watchdog.load_persisted()` tras `gateway.connect()`, ANTES de crear `worker_task`.

### Frontend (Task 8)

- [x] Task 8: banner global + botón Reanudar del owner (AC: 1, 2, 3)
  - [x] `frontend/lib/ws.ts`: `LiveBatchState` gana `watchdog: {paused, reason, detail, pausedAt}`; `SnapshotData` gana el slice; reducers nuevos `watchdog.paused`/`watchdog.resumed`; el reset a IDLE de `batch.state` idle y `seedFromBatch` PRESERVAN `watchdog` (es estado de sistema, no del lote — mismo trato que los campos de sesión 3.2).
  - [x] NUEVO `frontend/components/batch/watchdog-notice.tsx`: banner **danger** (DESIGN.md: "Danger red — destructive or failed"; esto ES un fallo, al revés que el ámbar informativo del FloodWait) con copy español por `reason` (`reply_rate_collapse` → "El bot dejó de responder…", `session_lost` → "Se perdió la sesión de Telegram…", fallback genérico) + "Solo el owner puede reanudar los envíos." Para el owner (rol vía `/api/auth/me`, idiom admin/users): botón "Reanudar envíos" → `POST /api/watchdog/resume`; el evento `watchdog.resumed` limpia el banner en todos los tabs.
  - [x] `frontend/app/(client)/page.tsx`: montar `<WatchdogNotice />` encima de `<FloodNotice />` (el owner envía desde la misma superficie Envío que un cliente — ahí ve la alerta y el botón). `types/api.ts` regenerado (endpoints nuevos ⇒ el OpenAPI cambia).

### Tests + gates (Tasks 9–10)

- [x] Task 9: `backend/tests/test_watchdog.py` — NUEVO (todos los AC)
  - [x] Idiom de la suite: `FakeClock` + instancias frescas `Watchdog(now=clock)` para la aritmética de ventana (idiom Scheduler); grabador `events` monkeypatcheando `broadcaster.emit`/`emit_global` (lección 2.2); `_persist` no-opeado en tests de memoria (la tabla puede no existir en el dev DB hasta el merge — ver Task 10); integración con `fake_gateway` + lote real vía `POST /api/batches` + `step()`.
  - [x] **Colapso (AC 1):** 4 envíos sin replies → NO pausa (umbral); 5º envío → pausa con `reason=reply_rate_collapse`, evento `watchdog.paused` global, `caplog` con `event=watchdog_paused`; replies en ventana → no pausa; replies VIEJOS (fuera de la ventana, reloj avanzado) no cuentan; trigger idempotente (sin evento duplicado).
  - [x] **Pérdida de sesión (AC 2):** `session_lost()` pausa al instante sin umbral; integración: `fake.errors = [SessionLostError(…)]` → `step()` devuelve False, la línea vuelve a `'queued'` (released, no `failed`), el latch queda puesto, evento global emitido, el siguiente `step()` no llama a `gateway.send`.
  - [x] **Reanudación manual (AC 3):** `resume()` limpia + emite `watchdog.resumed` + ventana fresca (los envíos pre-pausa no re-disparan); `note_reply` con el latch puesto NO despausa (jamás automático); endpoint: owner 204 + latch limpio, segundo 204 idempotente sin evento; client y admin → 403 `forbidden`.
  - [x] **Logs (AC 4):** `caplog` con `event=watchdog_paused` / `event=watchdog_resumed` (substrings, no formatos exactos).
  - [x] **Integración worker/captura:** con el latch puesto `step()` no envía y `POST /api/batches` → 503 `sending_paused`; `_record_sent` alimenta la ventana (con `_MIN_SENDS_IN_WINDOW` monkeypatcheado a 1, un step sin replies pausa y el siguiente no envía); `capture.enqueue` alimenta `note_reply` (5 envíos + 1 reply encolado → sin pausa).
  - [x] **Persistencia (AC 3, durabilidad):** trigger → fila `watchdog_state` con `paused=true`; `reset()` + `load_persisted()` restaura el latch; resume → fila `paused=false`. **Skip-if-missing:** si `to_regclass('watchdog_state')` es NULL (la migración se aplica en el merge, no aquí) el test se salta con mensaje explícito — el resto de la suite no toca la tabla (persistencia best-effort).
  - [x] **Ajustes a la suite existente:** `test_snapshot_idle_shape` (test_batches.py) gana la clave `"watchdog"`; conftest gana `reset_watchdog` autouse.
- [x] Task 10: gates de verificación + housekeeping (todos los AC)
  - [x] Backend: `ruff check app/ tests/`, `mypy app`, `pytest tests/test_watchdog.py tests/test_batches.py` (la suite COMPLETA corre en el merge — Postgres de dev compartido con otra corrida).
  - [x] Frontend: `npm run lint` (build corre en el merge). `types/api.ts` regenerado.
  - [x] sprint-status.yaml: 4-1 → review. Esta story no absorbe diferidos (los abiertos de 2-5 siguen abiertos — cerco abajo).
  - [ ] (HUMAN — necesita credenciales reales) Smoke manual: matar la sesión de Telegram en caliente (cerrar sesión desde otro dispositivo) con un lote enviando → banner rojo + pausa global + `journalctl | grep event=watchdog_paused`; reanudar desde el tab del owner. **No correr contra producción sin el OK de Richard.**

## Dev Notes

### Qué NO es esta story (cerco de alcance)

- **Admission control (cap de senders + cola de espera con posición)** → Story 4.2. **Alerting de FloodWait, métricas queryables, dashboards, alerta del bucket de unmatched** → Story 4.3 (los logs `event=watchdog_*` de aquí son su materia prima). **Runbook de re-auth, backups, load tests** → Story 4.4 — esta story solo DETECTA y PAUSA; recuperar la sesión es operación manual documentada allí.
- **Cero reintentos de reconexión automática**: detectada la pérdida, el gateway queda `authorized=False` y NADA intenta `connect()` de nuevo — la recuperación es el runbook (4.4) + resume explícito del owner (AC 3).
- **Diferidos que SIGUEN diferidos** (deferred-work.md — no arreglar "de paso"): 2-5 MEDIUM telegram.py:111 (match de reconciliación frágil), 2-5 LOW :398 (re-check en `_cancel_expired_batch`), 2-5 LOW :652 (retry en `_release_line`/`_abort_line` — el release del session_lost hereda el gap a sabiendas: si la DB falla justo ahí, `run_worker` loguea y el latch IGUAL queda puesto), 2-2 #2/#4 (el emit_global del watchdog a sockets de tenants vencidos es "honesto e inofensivo" mientras #4 siga abierto), 2-1/1-6 (tipos generados en admin).

### Diseño del watchdog (decisiones registradas)

- **Latch DURABLE en Postgres (`watchdog_state`, una fila):** el CI despliega en cada push a main reiniciando los services — un pause de watchdog que se evapora en el deploy es exactamente el agujero del AC 3 ("never automatic"). La memoria del singleton es la autoridad operativa (cero queries por step); la fila es solo lo que `load_persisted()` lee al boot. Persistencia **best-effort**: si la DB está caída al disparar, el latch en memoria ya bloquea y el fail-stop de 2.5 ya detuvo el pipeline — la fila no es la guardia, es la durabilidad.
- **Colapso = ≥5 envíos en la ventana de 5 min, cero replies en la misma ventana, Y el silencio abarcando ≥60s de envío.** Sin ratio, sin tuning: cero-respuestas-con-envío-sostenido es la única señal inequívoca de "bot silently blocking the account" y un falso positivo pausa a TODOS. El piso de span existe porque las replies van DETRÁS de los sends — la ráfaga inicial de un lote no debe pausar a todos por un blip transitorio (detección a ~60-70s de silencio real: latencia aceptable para un guardarraíl vs. el costo del falso positivo). Replies cuentan en el ARRIVAL (bridge→enqueue), atribuidos o no, ✅ o ⏳ — cualquier mensaje del bot prueba vida. Solo envíos REALES alimentan la otra mitad (`_record_sent`); la evaluación corre exactamente ahí — "Given active sending" del AC literal: sin envíos no hay señal ni falsos disparos en idle.
- **Pausa global = gate en memoria al tope de `step()`** (paso 0, antes de la ventana FloodWait): nadie reclama, nadie envía, los lotes quedan en su estado en DB y el worker rota en idle. NO se tocan estados de lotes (a diferencia del pause por-tenant de 2.3): reanudar retoma todo donde estaba — es un freno de emergencia, no un control de lote.
- **`SessionLostError` es excepción de dominio** (telegram.py la define, el worker la importa de ahí — telethon jamás cruza): conversión en `send()`/`recent_outgoing()` desde `UnauthorizedError` (base de todos los 401: AUTH_KEY_UNREGISTERED, SESSION_REVOKED, USER_DEACTIVATED…) + `AuthKeyError` (406). La línea reclamada se SUELTA (release, no failed — no es una línea mala) y el lote queda recuperable.
- **El resume NO tiene ninguna ruta automática:** `note_reply` con el latch puesto no despausa; el boot restaura el latch, no lo limpia; el único `resume()` del codebase vive en el endpoint owner-only. Ventana fresca al reanudar (deques limpias) — sin ella, los timestamps pre-pausa re-dispararían el colapso en el primer envío.
- **El alert del owner = `emit_global("watchdog.paused")` + log estructurado.** Global a propósito (idiom `flood.wait`): el pause afecta a todos y el copy honesto evita el "silent stall"; el tab del owner es uno más y la UI decide por rol (botón Reanudar solo para owner). Canales push/externos son territorio de 4.3.
- **Boot unauthorized ≠ session lost:** el watchdog cubre la pérdida EN CALIENTE (la que quema la cuenta con envíos activos); un boot sin autorizar ya tiene su semántica desde 2.2 (warning + 503) y dispararlo ahí alertaría en falso en cada deploy fresco.

### Código actual que vas a tocar (estado HOY @ 27f3170, con anclas)

| Archivo | Hoy | Esta story |
| --- | --- | --- |
| `backend/app/db/models.py` | hasta `Response` (:355-413) | + `WatchdogState` |
| `backend/migrations/versions/` | head `2faec0509cb8` (verificado) | + migración `watchdog_state` (NO aplicar aquí — merge) |
| `backend/app/db/repos/watchdog.py` | NO EXISTE | nuevo: `get_state` / `save_state` |
| `backend/app/core/watchdog.py` | NO EXISTE | nuevo: ventana + latch + persistencia + singleton |
| `backend/app/core/telegram.py` | `send` :174-184, `recent_outgoing` :186-201; docstring :11-12 "AuthKeyError detection/watchdog is Story 4.1 — deliberately NOT built here" | + `SessionLostError` + conversión auth-loss; promesa del docstring cumplida |
| `backend/app/core/send_worker.py` | `step()` :165-238 (paso 0 = ventana flood :173-180), `_send_with_retries` :431-488, `_record_sent` :241-314 | + gate del latch (paso 0 nuevo), + rama `session_lost`, + `note_sent` |
| `backend/app/core/capture.py` | `enqueue` :109-111 | + `note_reply` |
| `backend/app/api/watchdog.py` | NO EXISTE | nuevo: GET + POST resume (owner-only) |
| `backend/app/api/batches.py` | guard gateway :79-80 | + guard `sending_paused` |
| `backend/app/errors.py` | hasta 3.4 (:264-288) | + `sending_paused` |
| `backend/app/services/batches.py` | `snapshot` :154-199 | + slice `watchdog` en ambas ramas |
| `backend/app/main.py` | lifespan :37-57 | + router watchdog + `load_persisted()` |
| `backend/tests/conftest.py` | autouse `reset_scheduler`/`reset_capture` :153-177 | + autouse `reset_watchdog` |
| `backend/tests/test_batches.py` | `test_snapshot_idle_shape` :389-410 (dict exacto) | + clave `watchdog` |
| `backend/tests/test_watchdog.py` | NO EXISTE | nuevo (Task 9) |
| `frontend/lib/ws.ts` | store :45-72, IDLE :171-188, reducer :215-461, seed :553-591 | + campo `watchdog` + reducers + preservación |
| `frontend/components/batch/watchdog-notice.tsx` | NO EXISTE | nuevo (banner danger + botón owner) |
| `frontend/app/(client)/page.tsx` | cockpit :52-93 | + `<WatchdogNotice />` |
| `frontend/types/api.ts` | generado | regenerado (endpoints nuevos) |

**Sin cambios:** `core/scheduler.py` (la ventana FloodWait es ortogonal al latch), `core/attribution.py`, `core/cc_extract.py`, `api/ws.py` (el snapshot solo crece de payload), `api/sessions.py`, `api/admin.py`/`api/gates.py`/`api/auth.py`, `app/config.py` (CERO settings nuevos — todo constantes de módulo, regla 2.5), `deploy/*`, el resto de `frontend/`.

### Cumplimiento de arquitectura (no negociable)

- Telethon confinado a `core/telegram.py`: la detección vive ahí y cruza la frontera como `SessionLostError` (dominio) — `watchdog.py` y `send_worker.py` no importan telethon. [Source: architecture.md#Architectural-Boundaries]
- Eventos SOLO vía broadcaster con envelope `{"event","data"}`: `watchdog.paused`/`watchdog.resumed` son `emit_global` (precedente `flood.wait` — el único otro global). Errores REST con el contrato `{code, message}` (`sending_paused`). [Source: architecture.md#Communication-Patterns, #Format-Patterns]
- Migración Alembic a mano + espejo en modelos (idiom `2faec0509cb8`); tabla snake_case, índices `pk_`/`ix_` por convención. `watchdog_state` es global sin `tenant_id` — excepción documentada (misma clase que `gates`: estado de sistema, no de tenant). [Source: architecture.md#Database-Naming-Conventions; #Enforcement-Guidelines]
- `tenant_id` jamás de un request: el watchdog corre fuera de requests; el endpoint owner deriva el rol de `require_role` (deps). Identificadores en inglés, copy de UI en español (tuteo). [Source: architecture.md#Tenant-Scoping, #Code-Naming-Conventions]
- Risk Deep-Dive literal: "reply-rate watchdog with global auto-pause … `AuthKeyError` → pause + alert → manual resume only" — esta story ES esa mitigación; los logs `event=…` key=value siguen el patrón 2.5 (journald greppable, semilla de 4.3). [Source: architecture.md#Risk-Deep-Dive, #Logging]

### Inteligencia de stories previas (3.1 + 2.5 + 2.4)

- **El terreno ya está preparado a propósito — esta story COBRA promesas:** telegram.py :11-12 "“AuthKeyError” detection/watchdog is Story 4.1 — deliberately NOT built here" (se cumple aquí — actualizar el docstring); el contador `_unmatched_total` de capture (3.1) y `_sent_by_tenant` del worker (2.5) son la semilla de observabilidad que 4.3 explota — el watchdog NO los consume (su señal es la ventana propia, más simple y sin acoplarse a la atribución).
- **Lecciones de reviews previos:** eventos verificados monkeypatcheando `broadcaster.emit`/`emit_global` con lista grabadora (2.2 — jamás sockets); estado module-level SIEMPRE con `reset()` + fixture autouse (governor 2.4, cola capture 3.1 — aquí: deques + latch); sesiones cortas propias vía `async_session_factory` fuera de requests (patrón send_worker); atributos capturados antes de cerrar la sesión (MissingGreenlet 2.3 — `load_persisted` copia dentro del bloque).
- **El lazo del worker es de 2.4/2.5 — no deshacerlo:** el gate del latch es OTRO paso-0 componiendo los primitivos existentes (como la ventana FloodWait de 2.5), no un rewrite; `_release_line` se REUSA para el session_lost (su manejo de la carrera con stop ya existe).
- **Postgres de dev COMPARTIDO con una corrida paralela (constraint del entorno):** NO correr `alembic upgrade` (el merge re-encadena y aplica); los tests de persistencia hacen skip-if-missing sobre `to_regclass('watchdog_state')`; correr solo los tests nuevos/ajustados (la suite completa corre en el merge).
- **1.7/CI:** Conventional Commits con scope, rama `story/4-1`; push a main = deploy automático (la migración corre en `deploy.sh` vía `alembic upgrade head`). Sin claves de entorno nuevas.

### Estándares de testing

- `pytest` + `pytest-asyncio` (`loop_scope="session"`) + httpx `ASGITransport` contra la app real y el Postgres de dev; self-seed/self-clean; sin mocks de DB; un comportamiento por test. ASGITransport NO corre el lifespan → los tests llaman `step()`/`process_incoming`/`watchdog.*` directamente.
- Aritmética de ventana en unit con `Watchdog(now=FakeClock())` (idiom Scheduler de test_send_hardening); `caplog` para `event=watchdog_*` (substrings); `_persist` no-opeado fuera del test de persistencia explícito.
- Frontend: sin framework de tests (decisión diferida) — gates = `eslint` (build en el merge). No introducir vitest/jest.

### Notas de estructura del proyecto

- **Nuevos:** `backend/app/core/watchdog.py`, `backend/app/db/repos/watchdog.py`, `backend/app/api/watchdog.py`, `backend/tests/test_watchdog.py`, `frontend/components/batch/watchdog-notice.tsx`, una migración. (El árbol de architecture no nombra módulos para 4.1 — precedente 2.5: `send_log.py`/`test_send_hardening.py` nacieron por story.)
- **Modificados:** `backend/app/db/models.py`, `backend/app/core/{telegram,send_worker,capture}.py`, `backend/app/services/batches.py`, `backend/app/api/batches.py`, `backend/app/errors.py`, `backend/app/main.py`, `backend/tests/{conftest,test_batches}.py`, `frontend/lib/ws.ts`, `frontend/app/(client)/page.tsx`, `frontend/types/api.ts` (regenerado), `sprint-status.yaml`.
- Legacy `core.py`/`app.py`/`auto_sender.py` congelados en la raíz — el watchdog no tiene ancestro legacy (era imposible mono-tenant sin scheduler). **🔒 JAMÁS leer contenido bajo `respuestas/`. JAMÁS tocar `.env` ni `anon.session`.**

### Referencias

- [Source: planning-artifacts/epics.md#Story-4.1 — ACs verbatim; #Epic-4 (intro: "reply-rate watchdog with global auto-pause … `AuthKeyError` re-auth runbook"); #Epic-List ("Ban-guardrail operations")]
- [Source: planning-artifacts/architecture.md#Risk-Deep-Dive (watchdog + auto-pause + manual resume como mitigación del riesgo de baneo); #Communication-Patterns (envelope, flood.wait global); #Architectural-Boundaries (telethon confinado); #Logging (key=value)]
- [Source: implementation-artifacts/3-1-captura-y-atribucion-de-respuestas-del-bot.md — pipeline de captura (enqueue/bridge), `_unmatched_total` como semilla 4.3, lecciones MissingGreenlet/reset autouse]
- [Source: implementation-artifacts/2-5-endurecimiento-del-pipeline-de-envio.md — fail-stop (la DB caída ya bloquea), ventana FloodWait global (el precedente del paso-0 en `step()`), logs estructurados key=value, `_release_line`]
- [Source: _bmad-output/project-context.md — 🔒 reglas: nunca leer respuestas/, nunca tocar .env/anon.session; "no quitar rate-limiting"; counters de proceso never-reset]
- [Source: código actual @ 27f3170 — backend/app/{core/{telegram,send_worker,capture,scheduler}.py, db/{models.py, repos/}, services/batches.py, api/{batches,ws,deps}.py, errors.py, main.py}, backend/tests/{conftest,test_batches,test_send_hardening}.py, frontend/{lib/ws.ts, app/(client)/page.tsx, components/batch/*}]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Fable 5) — BMad dev agent, 2026-06-12.

### Debug Log References

- `ruff check app/ tests/` → All checks passed. `mypy app` → Success: no issues found in 41 source files.
- `pytest tests/test_watchdog.py` → 13 passed, 1 skipped (el de persistencia: `watchdog_state` aún sin migrar en el Postgres de dev compartido — skip por diseño, corre tras el `alembic upgrade` del merge).
- `pytest` (suite completa) → **225 passed, 1 skipped** (baseline 212 + 14 nuevos; el skip es el mismo de arriba). Corrida local pese al entorno compartido — sin choques.
- **NO se corrió `alembic upgrade`** (constraint del entorno paralelo): migración `d7f4b2a91c63` (revisa `2faec0509cb8`) queda para que el merge la re-encadene/aplique. Espejo en models.py escrito a mano con el idiom exacto de `2faec0509cb8`.
- Frontend: `npm run lint` limpio (1 warning de prettier corregido), `npx tsc --noEmit` limpio. `npm run build` NO corrido (lo corre el merge, constraint del entorno).
- `types/api.ts` regenerado vía `app.openapi()` dump + `npx openapi-typescript` — el diff es solo aditivo e incluye, además de `/api/watchdog*`, los paths `/api/sessions*` de 3.3–3.5 que el archivo commiteado no traía (estaba stale).

### Completion Notes List

- Todos los AC 1–4 implementados y cubiertos por tests. La única subtarea NO hecha es la marcada **(HUMAN)** en Task 10: smoke manual matando la sesión real de Telegram en caliente — requiere credenciales/acción del owner. **No se corrió nada contra producción.**
- **Latch global (AC 1/3):** `core/watchdog.py` — singleton con reloj inyectable; paso 0a de `step()` gatea en memoria (cero queries); la pausa NO toca estados de lotes (freno de emergencia, no control de lote): al reanudar todo retoma donde estaba. Durable en `watchdog_state` (1 fila, get-or-create id=1), restaurado en el lifespan ANTES del worker; persistencia best-effort (`event=watchdog_persist_failed` — el latch en memoria es la guardia y el fail-stop 2.5 ya bloquea con la DB caída).
- **Colapso (AC 1):** ≥5 sends en ventana de 300s + cero replies + span ≥60s (ver decisión registrada — el piso de span se agregó durante el dev al detectar que la ráfaga inicial de un lote Y los loops de la suite dispararían en falso). Sends desde `_record_sent` (entregas reales; las confirmaciones de boot-reconciliation NO cuentan); replies en el ARRIVAL (`capture.enqueue` — independiente de DB y atribución).
- **Pérdida de sesión (AC 2):** `SessionLostError` (dominio, definida en telegram.py) convertida desde `UnauthorizedError`/`AuthKeyError` de telethon en `send()`/`recent_outgoing()` + `authorized=False` (los POST nuevos 503ean solos). El worker la atrapa ANTES del genérico: la línea reclamada se SUELTA (`_release_line` — vuelve 'queued' intacta, no 'failed', no cuenta al cap) y `watchdog.session_lost()` latchea. Boot unauthorized NO dispara (decisión registrada — cerco). Flujo auto-corrector: un resume sin re-auth re-latchea al primer send.
- **Resume manual-only (AC 3):** el ÚNICO call-site de `watchdog.resume()` es `POST /api/watchdog/resume` (owner-only vía `require_role("owner")`; admin y client → 403); idempotente (no pausado → 204 sin evento, idiom controles 2.3); `note_reply` con latch jamás despausa; restart restaura el latch; ventana fresca al reanudar. + `GET /api/watchdog` (owner) para la superficie de alerta.
- **Logs (AC 4):** `event=watchdog_paused` (warning, con sends/replies en ventana), `event=watchdog_resumed` (info), `event=watchdog_restored` (warning, boot), `event=watchdog_persist_failed`/`watchdog_load_failed` (exception), `event=session_lost source=send|recent_outgoing` (error, gateway). Key=value greppable — materia prima de 4.3.
- **Alert del owner:** `emit_global("watchdog.paused"/"watchdog.resumed")` (idiom `flood.wait` — el único otro global) + snapshot con slice `watchdog` en ambas ramas (snapshot-first). Nota: `detail` viaja a todos los tabs (texto de excepción telethon / resumen de ventana — operativo, no sensible); el copy visible sale de `reason`.
- **Guard de envío:** `sending_paused` 503 en `POST /api/batches` (crear Y anexar) mientras el latch está puesto — decisión registrada: encolar líneas que no saldrán invita confusión.
- **Frontend:** `ws.ts` gana `watchdog` (estado de SISTEMA: sobrevive el reset a IDLE y `seedFromBatch`; snapshot autoritativo + reducers `watchdog.paused|resumed`); `watchdog-notice.tsx` — banner danger (DESIGN.md: rojo = failed, al revés que el ámbar FloodWait), copy español por `reason`, "Solo el owner puede reanudar" para no-owners y botón "Reanudar envíos" para el owner (rol vía `/api/auth/me` con `enabled` solo si pausado — los tabs sanos no pagan el round-trip); el evento `watchdog.resumed` limpia todos los tabs (cero optimismo, UX-DR12). Montado encima de `<FloodNotice />`.
- **Tests (14 nuevos en `test_watchdog.py`):** colapso (umbral/ventana/span/idempotencia), session-lost unit + integración worker (release + latch + freeze), resume manual-only (unit + endpoint owner/admin/client + idempotencia), logs con caplog, gates (worker + 503 + snapshot), alimentación de ventana (record phase + enqueue), persistencia round-trip (skip-if-missing sobre `to_regclass`). Fixture autouse `reset_watchdog` en conftest; `test_snapshot_idle_shape` gana la clave `watchdog`.
- No se hizo push (instrucción del workflow). Migración SIN aplicar al Postgres de dev (el merge la aplica).

### File List

**Nuevos:**
- `backend/migrations/versions/d7f4b2a91c63_watchdog_state_global_pause_latch.py`
- `backend/app/db/repos/watchdog.py`
- `backend/app/core/watchdog.py`
- `backend/app/api/watchdog.py`
- `backend/tests/test_watchdog.py`
- `frontend/components/batch/watchdog-notice.tsx`

**Modificados:**
- `backend/app/db/models.py` (`WatchdogState` + docstring del módulo)
- `backend/app/core/telegram.py` (`SessionLostError` + conversión auth-loss en `send`/`recent_outgoing` + docstring cumplido)
- `backend/app/core/send_worker.py` (gate 0a del latch, rama `session_lost`, `note_sent` en `_record_sent`, docstrings)
- `backend/app/core/capture.py` (`note_reply` en `enqueue`)
- `backend/app/services/batches.py` (slice `watchdog` en ambas ramas del snapshot)
- `backend/app/api/batches.py` (guard `sending_paused`)
- `backend/app/errors.py` (`sending_paused`)
- `backend/app/main.py` (router watchdog + `load_persisted()` en lifespan)
- `backend/tests/conftest.py` (fixture autouse `reset_watchdog`)
- `backend/tests/test_batches.py` (clave `watchdog` en el snapshot idle)
- `frontend/lib/ws.ts` (`WatchdogInfo` + slice en snapshot + reducers + preservación en idle-reset/seed)
- `frontend/app/(client)/page.tsx` (monta `<WatchdogNotice />`)
- `frontend/types/api.ts` (regenerado — incluye además los paths 3.3–3.5 que faltaban)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (4-1 → review)
- `_bmad-output/implementation-artifacts/4-1-watchdog-de-respuestas-y-deteccion-de-perdida-de-sesion.md` (este archivo)
