---
baseline_commit: 84cf26a5eb977e927fd8e875abb1fad7515fedbb
---

# Story 2.4: Planificador multi-tenant: round-robin, prioridad owner, intervalo adaptativo

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGÍA (decisión del owner 2026-06-11):** el término de producto para un prefijo es **"gate"** — DB, API, identificadores de código y todo el copy de UI (masculino: "el gate"). epics.md / architecture.md / docs de UX son anteriores al renombre y todavía dicen "prefijo/prefixes" — lee cada "prefijo" como "gate"; donde haya conflicto, gana "gate". Establecido en 2.1 (`gates`, `/api/gates`), extendido en 2.2 (`gate_categories`, snapshots `gate_value`/`gate_name` en `batches`) y 2.3 (`state_data` con `gate_name`/`gate_value`).

> **⚠️ HALLAZGOS DIFERIDOS DE 2.3 QUE ESTA STORY ABSORBE (deferred-work.md, sección "Story 2-3 review"):** esta story resuelve DOS de los tres hallazgos diferidos porque tocan exactamente las piezas que 2.4 rediseña: (1) **el colateral cross-tenant de `wake()`** — el control de un tenant corta el sleep de FloodWait de OTRO tenant y el reintento temprano sobre la cuenta compartida escala el FloodWait; el mismo wake corta el sleep del intervalo global y alternar pause/resume rompe el ritmo del sistema (bypass de FR12). El fix es el bucle con deadline + re-sleep del remanente — exactamente el lazo del worker que esta story reescribe para el intervalo adaptativo. (2) **el interval leak de `flood-notice.tsx`** — `flood.wait` es GLOBAL pero las señales que lo limpian son tenant-scoped: el tenant ocioso queda con un `floodUntil` viejo y un `setInterval` re-renderizando por segundo para siempre. Con el scheduler multi-tenant real, que TODOS los tenants reciban `flood.wait` pasa a ser lo normal — el leak deja de ser teórico. El hallazgo #2 (contraste de la pill "En pausa") NO se toca aquí: es cosmética sin relación con el scheduler (ver Dev Notes → "Qué NO es esta story").

## Story

As the owner,
I want all tenants' sends scheduled fairly over the shared channel at a safe adaptive pace,
So that no client monopolizes the account and the account stays safe.

## Acceptance Criteria

1. **Given** multiple clients with live batches, **when** the scheduler assigns send slots, **then** the channel rotates round-robin across active clients and all in-flight batches advance interleaved — no client monopolizes.
2. **Given** `n` active (non-paused) senders, **when** the interval is computed, **then** `G = max(G_min, P(n)/n)` with `G_min = 3.0s` (configurable, to be load-tested) and `P(n)` linear from 10s (n=1) to 20s (n≥5); each client gets a turn every `G×n`, **and** paused tenants are excluded from `n`.
3. **Given** the owner sends while clients are active, **when** owner lines enter the rotation, **then** they jump ahead of the client rotation but take at most 50% of send slots.
4. **Given** repeated FloodWait events, **when** the governor detects them, **then** `G_min` auto-raises (self-tuning toward the safe band) and every FloodWait broadcasts a global `flood.wait` event so stalled ETAs are explained.
5. **Given** the backend test suite, **when** scheduler tests run, **then** they cover fairness (round-robin), bounded owner priority, the adaptive formula, and paused-tenant exclusion — all passing.

## Tasks / Subtasks

### Backend (Tasks 1–6)

- [x] Task 1: config — el intervalo fijo muere, nace el piso del governor (AC: 2)
  - [x] `backend/app/config.py:56-59`: ELIMINAR `send_interval_seconds` (su propio comentario lo anuncia: "Story 2.4 replaces the constant with the adaptive formula"). Agregar `scheduler_g_min_seconds: float = 3.0` — piso global configurable del intervalo (AC 2 "configurable, to be load-tested"); comentario: server config ONLY, jamás de un request; es el PISO del governor de FloodWait, no el intervalo efectivo. `extra="ignore"` (config.py:18) hace inofensivo un `SEND_INTERVAL_SECONDS` residual en el `.env` del VPS — **cero cambios de deploy** (el default 3.0 aplica solo).
  - [x] El único uso restante de `settings.send_interval_seconds` fuera del worker/servicios es `backend/tests/test_batches.py:432` (test del ETA) — se rehace en Task 9.
- [x] Task 2: queries de repo para el scheduler (AC: 1, 2, 3)
  - [x] En `backend/app/db/repos/batches.py`, sección worker (:189-206): REEMPLAZAR `next_queued_line` (FIFO global — su comentario de bloque dice "Story 2.4 replaces this selection"; sin usos en tests, verificado por grep) por:
    - `ActiveSender` (`NamedTuple`: `tenant_id`, `batch_id`, `is_owner_priority`) — el índice único parcial `uq_batches_one_live_per_tenant` garantiza ≤1 lote vivo por tenant, así que tenant ≡ lote en la rotación.
    - `active_senders(session) -> list[ActiveSender]` — lotes `state='sending'` con ≥1 línea `'queued'` (EXISTS sobre `batch_lines`), `ORDER BY Batch.tenant_id` (rotación cíclica estable). Los pausados quedan fuera SOLOS (`state='paused'` no matchea) — la exclusión del AC 2 no necesita código extra, solo el test.
    - `next_queued_line_for_tenant(session, tenant_id) -> BatchLine | None` — la línea `'queued'` más vieja por `(batch_id, position)` del lote `'sending'` de ESE tenant (el join actual de `next_queued_line` + filtro `Batch.tenant_id`).
    - `count_active_senders(session) -> int` — el `n` de la fórmula: `COUNT(DISTINCT tenant_id)` de lotes `'sending'` con ≥1 línea en `_PENDING_STATES` (:36). **Nota la diferencia deliberada:** la selección exige líneas `'queued'` (servibles); `n` cuenta también al tenant cuya única línea está `'sending'` en vuelo — sigue ocupando el canal.
  - [x] Actualizar el comentario de bloque de la sección worker (la promesa "Story 2.4 replaces this selection" se cumple aquí).
- [x] Task 3: `backend/app/core/scheduler.py` — NUEVO (AC: 1, 2, 3, 4)
  - [x] El árbol de architecture.md ya lo nombra: "`scheduler.py` — round-robin, owner priority, adaptive interval". Clase `Scheduler` + singleton módulo-level `scheduler = Scheduler()` (mismo idiom que `settings`/`gateway`/`broadcaster`). Constructor con reloj inyectable `now: Callable[[], float] = time.monotonic` (los tests del governor inyectan un reloj falso) y método `reset()` (los tests del singleton lo limpian entre casos).
  - [x] **Fórmula (AC 2):** `_target_per_client(n) = min(20.0, 10.0 + 2.5 * (n - 1))` (lineal 10s→20s, saturada en n≥5) e `interval(n) -> float` = `max(g_min_efectivo, _target_per_client(n) / max(1, n))`. Constantes de módulo: `_P_BASE = 10.0`, `_P_CAP = 20.0` (la banda 10–20 es producto FR13 — NO configurable; solo `G_min` lo es, AC 2). Tabla de verdad en Dev Notes — los tests la asertan tal cual.
  - [x] **Rotación + prioridad owner (AC 1, 3 — decisión registrada):** `pick_next(active: list[ActiveSender]) -> ActiveSender | None`. Estado en memoria: `_last_client_tenant_id`, `_last_owner_tenant_id`, `_last_was_owner`. Regla: partir `active` en `owners` (`is_owner_priority=True`) y `clients`; si hay owners y (`not _last_was_owner` o no hay clients) → servir al siguiente owner en orden cíclico por `tenant_id` y marcar `_last_was_owner=True`; si no → siguiente client en orden cíclico y `_last_was_owner=False`; lista vacía → `None`. La ALTERNANCIA estricta implementa las dos mitades del AC 3 a la vez: el owner "salta" la rotación (recibe el siguiente slot disponible sin esperar su turno de client) y queda acotado a exactamente ≤50% mientras haya clients activos; solo → 100% (architecture: "may send at G_min directly"). Varios lotes owner rotan dentro de su clase.
  - [x] **Governor de FloodWait (AC 4 — decisión registrada):** estado `_g_min` (inicia en `settings.scheduler_g_min_seconds`) y `_last_flood_at`. `note_flood_wait()`: `_g_min = min(_g_min * 1.5, 30.0)` y sella `_last_flood_at`. Decay perezoso dentro de `interval()`: si pasaron ≥600s desde el último FloodWait y `_g_min > piso` → `_g_min = max(piso, _g_min / 1.5)` y re-sellar (un paso por ventana de 600s — "self-tuning toward the safe band" en ambas direcciones; el AC solo exige la subida, el decay evita que UN FloodWait frene todo para siempre). Constantes: `_GOVERNOR_FACTOR = 1.5`, `_G_MIN_CEIL = 30.0`, `_GOVERNOR_DECAY_SECONDS = 600.0`.
  - [x] **El estado del scheduler es memoria de proceso a propósito** (decisión registrada): un solo worker en un solo asyncio loop (architecture: "single asyncio loop in cc-core"); el cursor de rotación y el governor NO son estado durable — un restart los resetea y la equidad se restablece en la primera vuelta. Postgres guarda cola/lotes (NFR6); el cursor no. Documentarlo en el docstring del módulo.
- [x] Task 4: el worker selecciona vía scheduler (AC: 1, 3)
  - [x] `backend/app/core/send_worker.py` `step()` (:83), fase de claim (:89-100): reemplazar `next_queued_line(session)` por: `active = await batches_repo.active_senders(session)` → `pick = scheduler.pick_next(active)` → `None` → return False; `line = await batches_repo.next_queued_line_for_tenant(session, pick.tenant_id)`; si `None` (carrera: un stop vació la cola entre el listado y el claim) → return False (idle-sleep de 1s; la siguiente vuelta rota — decisión registrada: no reintentar otro tenant dentro del mismo step, simplicidad sobre latencia marginal). El resto del claim (mark_sending + commit + capturar locales) queda igual.
  - [x] El resto de `step()` (registro "sent", release/abort, finalización stopping→stopped) NO cambia — es la maquinaria de 2.3, el scheduler solo decide DE QUIÉN es la próxima línea.
- [x] Task 5: pacing con deadline — intervalo adaptativo + fix del diferido 2-3 #1 (AC: 2, 4)
  - [x] Helper nuevo en send_worker: `_wait_respecting_state(batch_id, seconds) -> Literal["elapsed", "release", "abort"]` — bucle con deadline (`time.monotonic()`): mientras quede remanente, `sleep_cancelable(remanente)` y al despertar re-leer `get_batch_state`; `'paused'` → `"release"`; ≠`'sending'` → `"abort"`; `'sending'` → **re-dormir el remanente** (el wake era de otro tenant — NO reintentar temprano sobre la cuenta compartida; este es el fix del diferido #1, parte 1). Deadline cumplido con estado `'sending'` → `"elapsed"`.
  - [x] `_send_with_retries` (:148-180): la rama FloodWait (:169-172) pasa a: `scheduler.note_flood_wait()` + `emit_global("flood.wait", {"seconds": e.seconds})` (el emit YA existe — AC 4 lo exige intacto) + `_wait_respecting_state(batch_id, float(e.seconds))`; `"release"`/`"abort"` se devuelven tal cual, `"elapsed"` vuelve al tope del bucle (re-chequeo + envío). La rama de error genérico (:175-180) usa el mismo helper con `_ERROR_RETRY_SECONDS` (retry-forever se conserva — 2.5 pone el cap). La semántica legacy "pause→resume puede reintentar antes de que venza la ventana" sobrevive vía release/re-claim (igual que en 2.3): el pause del PROPIO tenant corta el sleep al instante → release; el resume re-reclama ya.
  - [x] `run_worker` (:261): el sleep post-envío (:279) pasa de `sleep_cancelable(settings.send_interval_seconds)` a: sesión corta → `n = await batches_repo.count_active_senders(session)` → `await sleep_paced(scheduler.interval(max(1, n)))`. `sleep_paced(seconds)` es OTRO helper nuevo: bucle con deadline INMUNE a `wake()` (re-duerme el remanente incondicionalmente) — el ritmo global jamás se salta por un control (fix del diferido #1, parte 2; FR12). Es seguro: durante este sleep el worker no tiene línea reclamada, así que ningún pause/stop necesita cortarlo (un lote pausado simplemente no será elegido en la próxima vuelta; `stopping` solo existe con línea en vuelo). El idle-sleep de 1s (:281) sigue siendo `sleep_cancelable` simple.
  - [x] Actualizar el docstring del módulo (:1-24): la selección ya no es FIFO global, el intervalo ya no es constante; conservar las notas "Story 2.5" de retry-forever (:13, :46-47).
- [x] Task 6: ETA honesto derivado de `G×n` (AC: 2)
  - [x] `backend/app/services/batches.py` `eta_seconds` (:32-37): nueva firma `eta_seconds(queued: int, n_eff: int) -> float` = `queued * n_eff * scheduler.interval(n_eff)` — el turno de un cliente llega cada `G×n`, así que drenar `queued` líneas toma ≈ `queued × G × n` (architecture: "UI must show honest ETA derived from G×n so degradation is visible, not mysterious").
  - [x] `progress_data` (:56) y la rama viva de `snapshot` (:68): calcular `n = await batches_repo.count_active_senders(session)`; `n_eff = n if batch.state == STATE_SENDING else n + 1` (un lote pausado está excluido de `n` — para SU "ETA al reanudar" se re-incluye como si reanudara ya); `n_eff = max(1, n_eff)`. La rama idle del snapshot conserva `eta_seconds: 0`. La forma de ambos payloads NO cambia — el frontend ya muestra `eta_seconds` tal cual (`formatEta`, redondeo "~12 min"); **cero cambios de UI para el ETA**.
  - [x] Decisión registrada: la prioridad del owner NO ajusta el ETA de los clientes (con owner activo el turno real de un client puede llegar hasta 2× más lento que `G×n`); la aproximación `G×n` es honesta, se recalcula en cada evento y evita matemática de colas falsa-precisa (UX-DR14).

### Frontend (Task 7)

- [x] Task 7: fix del interval leak en `frontend/components/batch/flood-notice.tsx` (AC: 4; resuelve diferido 2-3 #3)
  - [x] En el efecto (:17-23): capturar `const until = live.floodUntil` en el closure y, dentro del callback del `setInterval`, detener el timer al expirar: `if (Date.now() >= until) window.clearInterval(id)` (además del `setNow` actual). Hoy el interval de 1s NUNCA para mientras `floodUntil` siga non-null — y como `flood.wait` es global pero las señales que lo limpian (`batch.progress`, `batch.line_sent`, `batch.state sending`) son tenant-scoped del que envía, el tenant OCIOSO re-renderiza cada segundo hasta un reconnect. Con 2.4 el evento global a todos es lo normal, no la excepción.
  - [x] El render no cambia (`seconds <= 0` ya devuelve null); copy y estilo ámbar intactos (DESIGN.md: rojo prohibido para FloodWait).

### Tests + gates (Tasks 8–10)

- [x] Task 8: `backend/tests/test_scheduler.py` — NUEVO (AC: 5; architecture lo nombra: "test_scheduler.py — fairness, owner priority, adaptive interval")
  - [x] **Unit (sin DB — `Scheduler()` y `ActiveSender` construidos a mano):**
    - fórmula: asertar la tabla de Dev Notes exacta — `interval(1)=10.0`, `interval(2)=6.25`, `interval(3)=5.0`, `interval(5)=4.0`, `interval(7)=3.0` (piso), y los turnos `G×n` (10.0/12.5/15.0/20.0/21.0). `_target_per_client` saturado en 20.0 para n≥5.
    - governor: con reloj falso inyectado — `note_flood_wait()` ×1 → `g_min` 3.0→4.5; ×k → ×1.5^k con techo 30.0; `interval(7)` refleja el piso subido; avanzar el reloj 600s sin floods → un `interval()` decae un paso (÷1.5, nunca bajo el piso configurado); dos ventanas → dos pasos.
    - round-robin puro: 3 clients activos → `pick_next` cicla A,B,C,A,B,C; quitar B de la lista (pausado) → cicla A,C,A,C.
    - prioridad owner acotada: owner + 2 clients → la secuencia de picks da owner, client, owner, client… (owner exactamente 50%, nunca más); owner solo en la lista → todos los slots; "jump ahead": con cursor a mitad de la rotación de clients, aparecer el owner en `active` → el SIGUIENTE pick es el owner.
    - lista vacía → `None`.
  - [x] **Integración (idiom ASGI de conftest — self-seeding, self-cleaning, `FakeGateway` vía fixture `fake_gateway`, fixtures `gate`/`client_user`/`ctx` ya promovidas en 2.3; fixture autouse local que haga `scheduler.reset()` por test — el singleton acumula cursor/governor):**
    - fairness end-to-end: dos clients (segundo vía `seed_user`+`login`, patrón de conftest) con lotes de 3 líneas de textos distintos → `step()` ×6 → `fake_gateway.sent` alterna estrictamente entre los dos tenants y ambos lotes terminan `completed`.
    - exclusión de pausados: dos lotes vivos, pausar B (`POST /api/batches/{id}/pause`) → `step()` solo sirve líneas de A y `count_active_senders() == 1`; reanudar B → vuelve a la rotación.
    - prioridad owner E2E: lote del owner (vía `ctx["owner_client"]` — `is_owner_priority=True` lo pone el POST solo, api/batches.py:83+111) + lote de un client → la secuencia de envíos alterna owner/client.
    - carrera selección↔stop: vaciar la cola del tenant elegido entre listado y claim (borrar líneas a mano con el repo) → `step()` devuelve False sin excepción.
    - re-sleep del remanente (diferido #1): lote A en FloodWait (FakeGateway con `FloodWaitError` y `capture=N` pequeño) → disparar `send_worker.wake()` ajeno a mitad del sleep → el reintento NO ocurre antes del deadline (acotar con tiempos cortos + `asyncio.wait_for`, mismo estilo que el test de `sleep_cancelable` de 2.3); `sleep_paced` ignora `wake()` (duración completa).
    - governor E2E: un `FloodWaitError` en `fake_gateway.errors` → tras el step, `scheduler` quedó con `g_min` subido Y el broadcaster grabador (monkeypatch de `emit_global`, lección 2.2) recibió `flood.wait {seconds}` — las dos mitades del AC 4 juntas.
    - ETA: snapshot con 1 sender activo y 3 en cola → `eta_seconds == 30.0` (3 × 1 × 10.0); con 2 senders activos → `3 × 2 × 6.25 == 37.5`; lote pausado (n excluido) → usa `n+1`.
- [x] Task 9: ajustar la suite existente — debe seguir verde COMPLETA
  - [x] `backend/tests/test_batches.py:425-443` (`test_snapshot_live_shape_and_eta_math`): muere el monkeypatch de `send_interval_seconds` (:432); con un solo sender el assert pasa a `eta_seconds == 30.0` (3 × 1 × interval(1)=10.0). Nada más referencia el setting (verificado por grep).
  - [x] Los tests de worker existentes (`test_batches.py:312+`, `test_batch_controls.py`) son mono-tenant: round-robin con un tenant ≡ FIFO, así que `step()` se comporta igual — no deberían necesitar cambios, solo correr. Si alguno acumula estado del singleton, el `scheduler.reset()` autouse de test_scheduler.py NO los cubre — agregar el reset a `conftest.py` como fixture autouse global si aparece flakiness entre módulos (decisión local del dev).
- [x] Task 10: gates de verificación (todos los AC)
  - [x] Backend: `ruff check .`, `mypy app`, `pytest` — todo verde (los 125 previos + los nuevos).
  - [x] Frontend: `npm run lint`, `npx tsc --noEmit`, `next build` — verdes (se tocó flood-notice.tsx). **NO regenerar `types/api.ts`**: cero endpoints/esquemas REST nuevos — el OpenAPI no cambia.
  - [x] `_bmad-output/implementation-artifacts/deferred-work.md`: marcar resueltos los diferidos #1 (wake cross-tenant) y #3 (flood-notice interval) del review 2-3, con el patrón `~~…~~ **RESOLVED in Story 2.4 (fecha)**` ya usado por 2.2/2.3. El #2 (contraste pill) queda diferido.
  - [ ] (HUMAN — necesita credenciales reales de Richard y DOS cuentas/tenants; envía mensajes reales) Smoke manual en dev: dos tenants con lotes vivos → los envíos se intercalan y el ETA de cada uno refleja `G×n`; pausar uno → el otro acelera (n baja). **No correr contra producción sin el OK de Richard.** No bloquea la story — la cobertura automatizada del AC 5 es el gate real.

## Dev Notes

### Qué NO es esta story (cerco de alcance)

- **`send_log` write-ahead, retry cap = 3 + estado `failed`, estado `cancelled`, fail-stop sin DB, reconciliación post-restart** → Story 2.5. El retry-forever de errores genéricos NO se toca (los comentarios `# Story 2.5` de send_worker.py:13,46-47,179 se conservan). El saneo de arranque (`_boot_recovery`, send_worker.py:229) tampoco se toca.
- **Admission control (cap de senders concurrentes + cola de espera)** → Story 4.2. **Watchdog de respuestas / detección de pérdida de sesión** → Story 4.1. El governor de aquí solo ajusta `G_min`; no pausa el envío global.
- **Cancelación de cola por expiración de plan mid-batch** → territorio de 2.5 (necesita `cancelled`).
- **Captura de respuestas, CC, paneles** → Epic 3 (`cc_new` sigue hardcodeado 0).
- **Cero UI nueva**: no hay indicador de "n senders activos" ni de `G` — el ETA que ya se muestra ES la superficie del scheduler (architecture: la degradación se ve en el ETA, no en un dashboard). El único cambio frontend es el fix del interval leak (Task 7).
- **Diferidos que SIGUEN diferidos** (deferred-work.md): 2-2 #4 (re-validación de auth en sockets abiertos), 2-2 #5 (retry del registro post-send — 2.5), 2-3 #2 (contraste de la pill "En pausa" — cosmética, `bg-warning/18 text-warning`), 2-1/1-6 (tipos generados). No los arregles "de paso".
- **Sin migración Alembic**: cero cambios de esquema. `is_owner_priority` existe desde 2.2 (models.py:210-214 — "CONSUMED by Story 2.4's scheduler — only written here"); esta story por fin lo LEE.

### Diseño del planificador (decisiones registradas)

**Tabla de verdad de la fórmula** (AC 2; `P(n) = min(20, 10 + 2.5·(n−1))`, `G = max(G_min, P(n)/n)`, piso default 3.0) — los tests la asertan literal:

| n activos | P(n) | G | turno por cliente (G×n) |
| --- | --- | --- | --- |
| 1 | 10.0 | 10.0 | 10.0s |
| 2 | 12.5 | 6.25 | 12.5s |
| 3 | 15.0 | 5.0 | 15.0s |
| 4 | 17.5 | 4.375 | 17.5s |
| 5 | 20.0 | 4.0 | 20.0s |
| 6 | 20.0 | 3.3̄ | 20.0s |
| 7 | 20.0 | **3.0 (piso)** | 21.0s — empieza la degradación |
| 50 | 20.0 | 3.0 | 150.0s — "slower, never down" (NFR4) |

- Con n=1 el sistema se comporta EXACTO como hoy (G=10.0 = el viejo `send_interval_seconds` default) — ningún cambio observable para un solo tenant.
- **Alternancia owner/client = la cota del 50%** (AC 3): no hay contador de slots ni ventanas — `_last_was_owner` alterna estrictamente cuando ambas clases tienen lotes activos. Owner solo → todos los slots a `G` (con n=1, G=10; architecture dice "may send at G_min directly" — eso requeriría n especial para el owner y NO se hace: el owner es un sender más en `n`, decisión registrada por simplicidad y porque con clients activos n≥2 baja G de todos modos).
- **`n` excluye pausados gratis**: `active_senders`/`count_active_senders` filtran `state='sending'`; un lote `'paused'` no aparece. El AC 2 se prueba, no se programa aparte.
- **Governor**: subida ×1.5 por evento (techo 30s), decay ÷1.5 por ventana de 600s sin FloodWaits (piso = `scheduler_g_min_seconds`). El AC solo pide la subida; el decay es la mitad "self-tuning" que evita castigo permanente. Reloj inyectable para tests.
- **Estado en memoria de proceso**: cursor de rotación + governor viven en el singleton `scheduler` — un solo worker en un solo loop (architecture), restart = reset inocuo. La cola y los lotes (lo durable) ya están en Postgres (NFR6).
- **Carrera selección↔stop**: entre `active_senders()` y el claim, un stop puede vaciar la cola del elegido → claim devuelve None → `step()` devuelve False (idle 1s). No se reintenta otro tenant en el mismo step.
- **Pacing inmune a wake** (diferido 2-3 #1): `sleep_paced` (intervalo global post-envío) re-duerme el remanente SIEMPRE; `_wait_respecting_state` (FloodWait/error con línea reclamada) re-duerme el remanente solo si el estado sigue `'sending'` — pause/stop del propio tenant siguen aterrizando al instante (release/abort), los controles ajenos ya no provocan reintentos tempranos ni rompen FR12.

### Código actual que vas a tocar (estado HOY @ 84cf26a, con anclas)

| Archivo | Hoy | Esta story |
| --- | --- | --- |
| `backend/app/config.py` | `send_interval_seconds: float = 10.0` :56-59 ("Story 2.4 replaces the constant") | eliminarlo; + `scheduler_g_min_seconds: float = 3.0` |
| `backend/app/db/repos/batches.py` | `next_queued_line` FIFO global :197-206; comentario de bloque :189-194 ("Story 2.4 replaces this selection"); `_PENDING_STATES` :36 | reemplazar por `ActiveSender` + `active_senders` + `next_queued_line_for_tenant` + `count_active_senders` |
| `backend/app/core/scheduler.py` | NO EXISTE | nuevo: `Scheduler` (pick_next / interval / note_flood_wait / reset) + singleton |
| `backend/app/core/send_worker.py` | claim FIFO en `step()` :89-100; FloodWait sleep :169-172; error sleep :175-180; intervalo fijo :279; `wake`/`sleep_cancelable` :50-67 (quedan) | selección vía scheduler; `_wait_respecting_state` + `sleep_paced` (deadline); `note_flood_wait()`; docstring |
| `backend/app/services/batches.py` | `eta_seconds(queued) = queued × send_interval_seconds` :32-37 ("The adaptive G×n version is Story 2.4"); usos :64,:99 | `eta_seconds(queued, n_eff)` = `queued × n_eff × interval(n_eff)`; `progress_data`/`snapshot` calculan `n_eff` |
| `backend/tests/test_batches.py` | monkeypatch `send_interval_seconds` :432, assert 6.0 :443 | assert 30.0 sin monkeypatch |
| `frontend/components/batch/flood-notice.tsx` | `setInterval` sin freno al expirar :17-23 | clearInterval al llegar a 0 (diferido 2-3 #3) |
| `_bmad-output/implementation-artifacts/deferred-work.md` | 2-3 #1 y #3 abiertos | marcarlos resueltos |

**Sin cambios:** `api/batches.py` (los controles y sus `wake()` quedan igual — :222,:246,:279), `api/ws.py`, `db/models.py` (cero migración), `core/broadcaster.py` (`emit_global` :38 ya existe), `core/telegram.py` (Telethon confinado — esta story no lo toca), `frontend/lib/ws.ts` (el reducer `flood.wait` y el ETA ya consumen lo que el backend manda), `deploy/*`, `middleware.ts`, `types/api.ts` (OpenAPI intacto). Head de migraciones sigue `1b606109cc99`.

### Cumplimiento de arquitectura (no negociable)

- El scheduler vive en `core/scheduler.py` y el worker lo drena — frontera "scheduler ↔ send_worker (queue protocol)" del architecture.md; los handlers REST jamás llaman al scheduler. [Source: architecture.md#Component-Boundaries, #Requirements-to-Structure (F2 → core/scheduler.py)]
- Eventos SOLO vía broadcaster; `flood.wait` es EL evento global, todo lo demás tenant-scoped. El envelope `{"event","data"}` no cambia. [Source: architecture.md#Communication-Patterns]
- El intervalo es del sistema, jamás de un request (FR12): `scheduler_g_min_seconds` es server config; nada del scheduler entra al OpenAPI. [Source: architecture.md#Format-Patterns; config.py idiom]
- Telethon confinado a `core/telegram.py`; cero esquema nuevo = cero migración; tenant_id jamás del body (sin cambios de API aquí de todos modos). [Source: architecture.md#Enforcement-Guidelines]

### Inteligencia de stories previas (2.3 + 2.2)

- **2.3 dejó el terreno listo:** `get_batch_state` (repos :80) es el re-chequeo barato que `_wait_respecting_state` necesita; `wake()`/`sleep_cancelable` (:50-67) son los primitivos — NO reescribirlos, componerlos en los bucles con deadline. El par release/abort y la finalización `stopping→stopped` quedan intactos: el scheduler decide QUIÉN, no QUÉ pasa con la línea.
- **`FakeGateway` es programable** (conftest.py:28-56): `errors` es una lista de excepciones que `send` lanza en orden — `FloodWaitError(request=None, capture=N)` da `seconds=N`. Diseñado en 2.2 "para que 2.3/2.4/2.5 lo reúsen".
- **Fixtures ya promovidas en 2.3** (conftest.py:141-200): `authorized_gateway` (autouse), `fake_gateway`, `gate`, `client_user` + helpers `seed_user`/`login`/`cleanup_users` — el segundo tenant de los tests de fairness se arma con esos helpers, no inventes otros.
- **Lecciones de reviews previos:** eventos verificados monkeypatcheando `broadcaster.emit`/`emit_global` con lista grabadora (jamás sockets de test); cuidado con los objetos expirados tras rollback (lección MissingGreenlet de 2.3 — los helpers nuevos usan sesiones cortas propias como todo send_worker); `step()` no duerme (los sleeps viven en `run_worker` y en `_send_with_retries`) — los tests de selección son rápidos por construcción.
- **Semántica legacy que se preserva** (project-context.md / CLAUDE.md raíz): FloodWait duerme lo pedido y reintenta LA MISMA línea; stop sale del FloodWait sin esperar (abort); pause→resume puede reintentar antes de la ventana (release/re-claim). Lo que CAMBIA vs legacy: el intervalo deja de ser constante (`TELEGRAM_INTERVALO` era del mundo viejo) y la selección deja de ser FIFO — ambas cosas son el corazón de esta story.
- **1.7/CI:** Conventional Commits con scope, rama `story/2.4-planificador-multi-tenant`; push a main = deploy automático al VPS. Sin claves de entorno nuevas obligatorias (default 3.0 aplica).

### Estándares de testing

- `pytest` + `pytest-asyncio` (`loop_scope="session"`) + httpx `ASGITransport` contra la app real y el Postgres de dev; self-seed/self-clean; sin mocks de DB; un comportamiento por test. ASGITransport NO corre el lifespan → ni Telethon ni worker de fondo: los tests llaman `step()`/`_send_with_retries`/`sleep_paced` directamente con `FakeGateway` (idiom de test_batches.py:312+ y test_batch_controls.py).
- La lógica pura del scheduler (fórmula, rotación, cota, governor) se testea SIN DB construyendo `Scheduler()` y `ActiveSender` a mano — el reloj inyectable hace determinista el governor. La capa de integración prueba que el worker realmente la consume.
- Tests con tiempo real (re-sleep del remanente): duraciones ≤0.5s y `asyncio.wait_for` como cota superior — mismo estilo que el test de `sleep_cancelable`+`wake()` de 2.3 (test_batch_controls.py:542).
- Frontend: sin framework de tests (decisión diferida) — gates = `eslint` + `tsc` + `next build`. No introducir vitest/jest.

### Notas de estructura del proyecto

- **Nuevos:** `backend/app/core/scheduler.py`, `backend/tests/test_scheduler.py` — ambos nombrados literalmente en el árbol de architecture.md ("scheduler.py — round-robin, owner priority, adaptive interval"; "test_scheduler.py — fairness, owner priority, adaptive interval"). Sin variaciones.
- **Modificados:** `backend/app/config.py`, `backend/app/db/repos/batches.py`, `backend/app/core/send_worker.py`, `backend/app/services/batches.py`, `backend/tests/test_batches.py` (solo el test del ETA), `frontend/components/batch/flood-notice.tsx`, `_bmad-output/implementation-artifacts/deferred-work.md` (marcar 2-3 #1 y #3 resueltos). `backend/tests/conftest.py` solo si hace falta el `scheduler.reset()` autouse global (Task 9).
- Legacy `core.py`/`app.py`/`auto_sender.py` congelados en la raíz — solo referencia. **🔒 JAMÁS leer contenido bajo `respuestas/`. JAMÁS tocar `.env` ni `anon.session`.**

### Referencias

- [Source: planning-artifacts/epics.md#Story-2.4 — ACs verbatim; #Epic-2 (reparto del pipeline: 2.4 = scheduler; send_log/retry-cap/fail-stop/reconciliación = 2.5)]
- [Source: planning-artifacts/architecture.md#Gap-Analysis (fórmula `G = max(G_min, P(n)/n)`, banda 10→20s, n=50→150s/turn, "UI must show honest ETA derived from G×n"); #Risk-Deep-Dive (governor de FloodWait, exclusión de pausados de n, cota owner 50%, admission control → knob de producto NO de esta story); #Proposed-Source-Tree (core/scheduler.py, tests/test_scheduler.py); #Component-Boundaries]
- [Source: implementation-artifacts/2-3-pausar-reanudar-y-detener-con-eta-honesto.md — máquina de estados, release/abort, wake/sleep_cancelable, get_batch_state, fixtures promovidas, "el cerco de 2.3 dice literalmente: scheduler/prioridad/intervalo adaptativo/governor → Story 2.4"]
- [Source: implementation-artifacts/deferred-work.md#Story-2-3-review — #1 (wake cross-tenant + bypass de pacing, fix de deadline/remanente) y #3 (flood-notice interval leak) absorbidos aquí; #2 (pill contrast) sigue diferido]
- [Source: _bmad-output/project-context.md — 🔒 reglas: nunca leer respuestas/, nunca tocar .env; sleeps cancelables / semántica FloodWait y stop del legacy; "no quitar rate-limiting"]
- [Source: código actual @ 84cf26a — backend/app/{config.py, core/send_worker.py, db/repos/batches.py, services/batches.py, db/models.py (is_owner_priority :210-214), core/broadcaster.py (emit_global :38)}, backend/tests/{conftest.py, test_batches.py, test_batch_controls.py}, frontend/components/batch/flood-notice.tsx]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Fable 5) — BMad dev agent, 2026-06-12

### Debug Log References

- Baseline `pytest`: 126 passed @ working tree (la story decía 125 — el código actual gana).
- Gates finales backend: `ruff check .` limpio, `mypy app` limpio (30 archivos), `pytest` 148 passed (126 previos ajustados + 22 nuevos de `test_scheduler.py`).
- Gates frontend: `npm run lint` limpio, `npx tsc --noEmit` limpio, `next build` OK. `types/api.ts` NO regenerado (OpenAPI intacto).

### Completion Notes List

- Tasks 1–10 implementadas tal como estaban diseñadas; cero desviaciones de fondo. La única subtarea abierta es la marcada (HUMAN) en Task 10: smoke manual con dos tenants y credenciales reales de Richard — no bloquea (AC 5 cubierto por la suite).
- Task 9, decisión local del dev tomada: el `scheduler.reset()` autouse se agregó GLOBAL en `conftest.py` (fixture `reset_scheduler`, reset antes y después de cada test) en vez de local a test_scheduler.py — un FloodWait en un test dejaba `g_min` subido y contaminaba la matemática del ETA entre módulos (p.ej. `test_worker_floodwait_retries_same_line_once` corre antes del test de ETA en test_batches.py). Determinista por construcción, no "si aparece flakiness".
- `pick_next`: alternancia estricta owner/client con cursores cíclicos por `tenant_id` dentro de cada clase, exactamente la regla registrada; verificada en unit (50% exacto, jump-ahead, owner solo → 100%, multi-owner rota en su clase) e integración (FakeGateway).
- `_wait_respecting_state` y `sleep_paced` componen `sleep_cancelable`/`wake()` (no los reescriben) en bucles con deadline `time.monotonic()` — diferido 2-3 #1 resuelto en sus dos partes; pause/stop propios siguen aterrizando al instante (tests con tiempos ≤0.5s + `asyncio.wait_for`).
- ETA: `eta_seconds(queued, n_eff)` con helper interno `_n_effective` (lote pausado → `n+1`, piso 1). La forma de los payloads no cambió — cero cambios de UI para el ETA.
- Governor: subida ×1.5 (techo 30.0) + decay perezoso ÷1.5 por ventana de 600s, reloj inyectable; integración verifica las dos mitades del AC 4 juntas (g_min 3.0→4.5 + `flood.wait` global grabado).
- Referencias en docstrings/comentarios a `next_queued_line` actualizadas al reemplazarlo (repos, send_worker y UN comentario en `api/batches.py:97` — solo el comentario; los controles y sus `wake()` quedaron byte a byte igual, como exige la story).
- Sin migración Alembic (cero esquema nuevo); head sigue `1b606109cc99`. `is_owner_priority` ahora se LEE por primera vez (active_senders → scheduler).
- deferred-work.md: 2-3 #1 y #3 marcados RESOLVED in Story 2.4 (2026-06-12); #2 (contraste pill) sigue diferido a propósito.

### File List

- `backend/app/config.py` — modificado: muere `send_interval_seconds`, nace `scheduler_g_min_seconds: float = 3.0`.
- `backend/app/core/scheduler.py` — NUEVO: `Scheduler` (interval/pick_next/note_flood_wait/reset, reloj inyectable) + singleton `scheduler`.
- `backend/app/db/repos/batches.py` — modificado: `next_queued_line` reemplazado por `ActiveSender` + `active_senders` + `next_queued_line_for_tenant` + `count_active_senders`; comentario de bloque y docstrings actualizados.
- `backend/app/core/send_worker.py` — modificado: claim vía scheduler; `sleep_paced` + `_wait_respecting_state` (deadline); `note_flood_wait()` en la rama FloodWait; intervalo adaptativo post-envío; docstring del módulo.
- `backend/app/services/batches.py` — modificado: `eta_seconds(queued, n_eff)` = `queued × n_eff × interval(n_eff)`; `_n_effective`; `progress_data`/`snapshot` lo usan.
- `backend/tests/test_scheduler.py` — NUEVO: 13 unit (fórmula/turnos/governor/decay/rotación/prioridad acotada) + 9 integración (fairness E2E, exclusión de pausados, owner E2E, carrera selección↔stop, re-sleep del remanente, pause instantáneo, sleep_paced inmune a wake, governor+flood.wait global, ETA G×n).
- `backend/tests/test_batches.py` — modificado: test del ETA sin monkeypatch, assert 30.0.
- `backend/tests/conftest.py` — modificado: fixture autouse global `reset_scheduler`.
- `frontend/components/batch/flood-notice.tsx` — modificado: el interval se auto-detiene al expirar el countdown (diferido 2-3 #3).
- `_bmad-output/implementation-artifacts/deferred-work.md` — modificado: 2-3 #1 y #3 resueltos.
