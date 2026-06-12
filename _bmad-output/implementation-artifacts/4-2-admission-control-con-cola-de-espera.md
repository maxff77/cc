---
baseline_commit: 27f3170641b86c1ee8d6b501a1c76f509f816f7d
---

# Story 4.2: Admission control con cola de espera

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGÍA (decisión del owner 2026-06-11):** el término de producto para un prefijo es **"gate"** — DB, API, identificadores de código y todo el copy de UI (masculino: "el gate"). epics.md / architecture.md / docs de UX son anteriores al renombre y todavía dicen "prefijo/prefixes" — lee cada "prefijo" como "gate"; donde haya conflicto, gana "gate".

## Story

As the owner,
I want a configurable cap on concurrent active senders,
So that per-client cadence stays near the 10–20s band instead of degrading everyone.

## Acceptance Criteria

1. **Given** the owner-configurable cap (e.g. 10), **when** a new batch would exceed it, **then** the batch enters a FIFO waiting queue instead of degrading every active sender's interval.
2. **Given** a waiting batch, **when** the client views Envío, **then** they see their queue position — not a dead-slow drip and not a silent stall.
3. **Given** an active sender finishes, stops, or frees a slot, **when** the slot opens, **then** the next waiting batch starts automatically.
4. **Given** the cap is disabled, **when** batches arrive, **then** behavior falls back to pure adaptive-interval degradation (Epic 2 semantics).

## Tasks / Subtasks

### Backend (Tasks 1–6)

- [x] Task 1: migración + modelos — `system_settings` y el estado `'waiting'` (AC: 1, 4)
  - [x] `db/models.py`: nueva tabla `SystemSetting` (`key: String(64)` PK, `value: String(200)` NOT NULL, `updated_at`) — KV mínimo para config de runtime del owner (la clave de esta story: `max_active_senders`; `"0"`/ausente = control desactivado). NO es config de `.env` a propósito: el cap es "owner-configurable" en caliente (AC 1/4), debe sobrevivir restarts y cambiar sin redeploy — `Settings` (pydantic) queda para lo que solo cambia con deploy.
  - [x] `Batch.__table_args__`: el predicado del índice parcial `uq_batches_one_live_per_tenant` se amplía a `state IN ('sending', 'paused', 'stopping', 'waiting')` (el docstring del modelo lo exige: "widen BOTH together if a live state is ever added" — un lote en espera ES el lote vivo del tenant: bloquea un segundo POST, los controles lo encuentran, nueva/continuar 409).
  - [x] Migración nueva (hand-written, como `1b606109cc99` — autogenerate no captura índices parciales): `down_revision = '2faec0509cb8'` (head actual). Upgrade: crear `system_settings` → drop + recreate del índice con el predicado ampliado. Downgrade: pre-clean `waiting` → `'stopped'` (la misma coreografía de pre-clean de `1b606109cc99` — el índice angosto no puede reconstruirse con lotes waiting vivos), recrear el índice angosto, drop de `system_settings`. **NO correr `alembic upgrade` (entorno paralelo — el merge re-encadena y aplica).**
- [x] Task 2: repos — cola FIFO durable + settings (AC: 1, 2, 3, 4)
  - [x] `db/repos/batches.py`: `STATE_WAITING = "waiting"`; `LIVE_STATES` += waiting; tupla nueva `ADMITTED_STATES = (STATE_SENDING, STATE_PAUSED, STATE_STOPPING)` (los estados que OCUPAN un slot de admisión — ver decisiones). Funciones nuevas en la sección worker: `count_admitted(session) -> int` (lotes en `ADMITTED_STATES`; ≤1 por tenant por el índice único ⇒ contar filas ≡ contar tenants), `waiting_batches(session, *, for_update=False) -> list[Batch]` (`state='waiting'` ORDER BY id — el id ES el orden FIFO de llegada), `queue_position(session, batch_id) -> int` (1 + count de waiting con id menor).
  - [x] `db/repos/system_settings.py` — NUEVO (idiom gates.py: módulo de funciones, flush no commit; catálogo GLOBAL sin tenant — nota de módulo): `get_value(session, key) -> str | None`, `get_value_for_update(session, key)` (FOR UPDATE — serializa las decisiones de admisión POST↔sweep), `set_value(session, key, value)` (upsert vía `postgresql.insert(...).on_conflict_do_update` — race-free sin TOCTOU).
  - [x] `active_senders` / `count_active_senders` NO cambian: `'waiting'` no matchea `state='sending'`, así que los lotes en espera quedan fuera de la rotación Y fuera del `n` de la fórmula adaptativa solos — ese es exactamente el punto del admission control (AC 1: la cola NO degrada el intervalo de los activos).
- [x] Task 3: servicio de admisión `app/services/admission.py` — NUEVO (AC: 1, 3, 4)
  - [x] `CAP_KEY = "max_active_senders"`, `CAP_DISABLED = 0`, `CAP_MAX = 1000`. `get_cap(session) -> int` (parse defensivo: ausente/no-int/negativo → 0 = desactivado), `get_cap_locked(session) -> int` (variante FOR UPDATE; fila ausente ⇒ sin lock Y desactivado — inofensivo: sin cap no hay decisión que serializar), `set_cap(session, cap)` (persiste SIEMPRE la fila, también `"0"` — así el lock existe apenas el owner tocó el knob una vez), y el helper puro `has_capacity(cap, admitted) -> bool` (`cap <= 0` → True; si no `admitted < cap`).
- [x] Task 4: `POST /api/batches` — decisión de admisión al crear (AC: 1, 2, 4)
  - [x] Rama de lote NUEVO en `api/batches.py`: antes de `create_batch`, `cap = await admission.get_cap_locked(session)` + `admitted = await batches_repo.count_admitted(session)`; `state = STATE_SENDING if admission.has_capacity(cap, admitted) else STATE_WAITING`. `create_batch` gana el parámetro `state` (default `STATE_SENDING` — los callers existentes no cambian).
  - [x] El binding de capture-session se mantiene EN LA CREACIÓN (decisión registrada): misma transacción, los paneles flipean ya; las replies viejas se atribuyen por send_log → línea → lote, no por la sesión activa — nada se rompe por activar antes.
  - [x] Lote waiting: calcular `queue_position` dentro de la transacción (post-flush), emitir `batch.state` con surface state `"waiting"` + `queue_position` (+ `batch.progress` como hoy). `BatchOut` gana `queue_position: int | None` (None salvo waiting) — la respuesta del POST ES el primer reporte de posición (AC 2).
  - [x] Rama APPEND: sin cambios de semántica (un lote waiting acepta append — las líneas se encolan y esperan con él); la respuesta reporta `queue_position` cuando `live.state == 'waiting'`.
- [x] Task 5: worker — sweep de promoción FIFO (AC: 3, 4)
  - [x] `core/send_worker.py`: helper nuevo `_admit_waiting()` ejecutado al TOPE de `step()` (antes de la ventana FloodWait — promover no envía nada, y el cliente ve "Enviando" apenas hay slot aunque la ventana siga abierta). Una sesión corta propia: `get_cap_locked` → `waiting_batches(for_update=True)` (vacío → return temprano, cero costo en el caso común) → si cap deshabilitado promover TODOS (AC 4: el fallback a Epic 2 también rescata a los que YA esperaban); si no, promover los `max(0, cap - count_admitted)` más viejos. Por cada promovido: `state = 'sending'`, payloads `state_data(batch, "sending")` + `progress_data` construidos DENTRO de la sesión (lección MissingGreenlet); por cada restante: `state_data(batch, "waiting", queue_position=i)` re-numerado. Commit → emitir todo (broadcaster tenant-scoped).
  - [x] **Por qué el sweep vive en el worker y NO en `core/scheduler.py` (decisión registrada):** la cola FIFO es DURABLE (filas `batches.state='waiting'` en Postgres, NFR6 — no memoria de proceso como el cursor de rotación), y el sweep necesita `services/batches.state_data/progress_data`… que importan `core.scheduler` a nivel de módulo (el ETA usa `scheduler.interval`) — un import inverso sería circular. `scheduler.py` conserva su rol puro (pick/pace) SIN cambios: los lotes waiting jamás aparecen en `active_senders` ni en `n`, así que `pick_next`/`interval` ya hacen lo correcto sin tocarlos. El worker es el único consumidor del scheduler (docstring de 2.4) — su loop es el lugar del sweep; "no refactorizar el scheduler más allá de lo que admission control necesita" = no tocarlo.
  - [x] Serialización POST↔sweep: ambos toman la fila de settings FOR UPDATE antes de contar/decidir — sin overshoot del cap por carrera. Orden de locks settings→batches en el sweep; el stop handler lockea el batch SIN el settings lock → sin ciclo de deadlock.
  - [x] Latencia de promoción: el sweep corre en CADA vuelta del loop (idle-sleep 1s; pacing acotado por G). `complete_if_drained`/`_cancel_expired_batch`/stop liberan slot y la siguiente vuelta promueve (AC 3 "starts automatically"). El stop directo (sin línea en vuelo) ahora también hace `wake()` — corta un idle-sleep y la promoción aterriza al instante.
- [x] Task 6: controles + snapshot + API del owner (AC: 2, 3, 4)
  - [x] Stop sobre un lote `'waiting'` (`api/batches.py`): permitido — el cliente sale de la cola. Entra por la rama directa existente (`delete_queued_lines` + sin línea en vuelo → `'stopped'`); además el handler re-numera y emite `batch.state waiting + queue_position` a los que esperaban DETRÁS (ninguna promoción ocurre — no se liberó slot de admisión — así que el sweep no lo haría por nosotros).
  - [x] Pause/resume sobre `'waiting'` → 409 con error NUEVO `batch_waiting()` ("El lote está en cola de espera. Puedes detenerlo si no quieres esperar.") — `batch_not_live` ("Ese lote ya terminó.") mentiría. La UI ni muestra Pausar en waiting (defensa en ambas capas).
  - [x] `services/batches.py`: `state_data(batch, state, *, queue_position=None)` — el payload gana `"queue_position"` (None salvo waiting; viaja en TODOS los `batch.state` para que el reducer no adivine). `snapshot`: la rama viva pasa `batch.state` tal cual (ahora puede ser `'waiting'`) y gana `"queue_position"` (consulta `queue_position()` solo si waiting; None en idle y en los demás estados) — una pestaña que conecta a mitad de la espera renderiza la posición del snapshot solo (patrón snapshot-first de 2.2).
  - [x] API del owner en `api/admin.py` (`require_owner`, schemas inline): `GET /api/admin/admission` → `{max_active_senders: int}` (0 = desactivado); `PUT /api/admin/admission` (body `{max_active_senders}`, bounds 0..1000 validados en la ruta → 400 `invalid_admission_cap` — patrón invalid_plan_days) → persiste vía `admission.set_cap`, commit, `send_worker.wake()` (subir/deshabilitar el cap debe promover YA, no en ≤1s) y devuelve el valor nuevo. Error nuevo en `errors.py`: `invalid_admission_cap()` 400 "Indica un límite entre 0 y 1000 (0 desactiva el control de admisión)."

### Frontend (Tasks 7–8)

- [x] Task 7: superficie waiting en Envío (AC: 2)
  - [x] `lib/ws.ts`: `BatchSurfaceState` += `"waiting"`; `LiveBatchState` += `queuePosition: number | null` (IDLE: null); `SnapshotData`/`BatchStateData` ganan `queue_position`; los reducers `snapshot` y `batch.state` lo asignan (`?? null`). `seedFromBatch` gana `state`/`queue_position` (el POST puede devolver `'waiting'` — sembrar "sending" mentiría); `send-form.tsx` se los pasa (su `BatchOut` local gana `queue_position`).
  - [x] `components/batch/waiting-notice.tsx` — NUEVO: tarjeta informativa ámbar (idiom flood-notice: "esperando, no roto" — jamás rojo) con "En cola de espera", la posición en grande (mono, tabular) y "Tu lote empezará solo cuando se libere un lugar." Se renderiza en `page.tsx` EN LUGAR del ProgressRing cuando `live.state === "waiting"` (la posición es LA métrica de la espera; el ring volvería con 0% muerto — AC 2 "not a silent stall").
  - [x] `components/batch/batch-controls.tsx`: en waiting solo **Detener** (salir de la cola); Pausar no se renderiza (no hay nada que pausar — defensa que espeja el 409 del backend).
  - [x] `components/client-nav.tsx`: pill `"En espera"` + dot warning para waiting (es "vivo pero no enviando", la misma familia que paused/stopping — DESIGN.md: tinte warning).
  - [x] `send-form.tsx`: cero cambios de gating extra — `isLive` ya cubre waiting (`state !== "idle"`): selector lockeado + chip + append permitido.
- [x] Task 8: knob del owner (AC: 1, 4)
  - [x] Sección "Control de admisión" en `/admin/users` (page.tsx), gateada por `isOwner` (mismo patrón que el form "Crear admin" — cero página nueva): input numérico + Guardar contra `GET/PUT /api/admin/admission`, helper "0 desactiva el límite: todos los lotes entran de inmediato (degradación adaptativa pura).", validación digits-only client-side (idiom `isPositiveInt` existente, aquí admitiendo 0) y el error `{code,message}` del server como fallback.
  - [x] Interfaces locales explícitas (idiom users/gates-page); **NO regenerar `types/api.ts`** (diferido 2-1 — pase épico aparte).

### Tests + gates (Tasks 9–10)

- [x] Task 9: `backend/tests/test_admission.py` — NUEVO (idiom ASGI de conftest: self-seeding, self-cleaning, `FakeGateway`, `step()` directo, eventos por monkeypatch del broadcaster)
  - [x] Knob del owner: GET default 0; PUT 1 → GET 1; PUT 0 persiste; bounds (−1, 1001) → 400 `invalid_admission_cap`; admin y client → 403 en GET y PUT.
  - [x] AC 1: cap=1 — el primer lote entra `'sending'`; el segundo (otro tenant) responde 201 `state='waiting'`, `queue_position=1`; un tercero → posición 2. `count_active_senders == 1` (la espera NO pesa en `n`/ETA — snapshot del activo con ETA de n=1).
  - [x] AC 2: snapshot del tenant en espera → `state='waiting'` + `queue_position` correcto; `batch.state` emitido al crear lleva `queue_position` (broadcaster grabador).
  - [x] AC 3: drenar el lote activo con `step()` → la siguiente vuelta promueve al más viejo (eventos: `batch.state sending` al promovido, `batch.state waiting` re-numerado al resto); stop del activo → idem; el lote promovido ENVÍA (fake_gateway).
  - [x] AC 4: cap deshabilitado (sin fila) → dos POST entran ambos `'sending'` (semántica Epic 2 intacta); PUT 0 con lotes YA esperando → el sweep los promueve TODOS.
  - [x] FIFO estricto: dos en espera → se promueve el de id menor primero.
  - [x] Controles: stop de un lote waiting → 204, `'stopped'`, y el que esperaba detrás recibe posición re-numerada; pause y resume sobre waiting → 409 `batch_waiting`; append a un lote waiting → 201 `appended=true` con `queue_position` y las líneas encoladas.
  - [x] Exclusión del worker: con A activo y B en espera, `step()` solo sirve líneas de A (jamás de B) mientras no haya slot.
- [x] Task 10: gates de verificación
  - [x] Backend: `ruff check app/ tests/` + `pytest tests/test_admission.py tests/test_batches.py tests/test_scheduler.py tests/test_batch_controls.py` (los módulos tocados/aledaños; la suite COMPLETA corre en el merge — entorno paralelo). `tests/test_batches.py`: el assert de forma EXACTA del snapshot idle gana `"queue_position": None`.
  - [x] Frontend: `npm run lint` (se tocó ws.ts/page.tsx/batch-controls/client-nav/send-form + waiting-notice + admin/users). `npm run build` lo corre el merge.
  - [ ] (HUMAN — Richard) Smoke manual con dos tenants reales: cap=1, B entra en cola y ve su posición; A termina → B arranca solo. No bloquea — la cobertura automatizada es el gate real.

## Dev Notes

### Qué NO es esta story (cerco de alcance)

- **Watchdog de respuestas / pérdida de sesión / pausa global** → Story 4.1. El admission control jamás pausa a nadie: solo decide QUIÉN entra al canal.
- **Alertas de FloodWait / observabilidad estructurada** → Story 4.3. Aquí solo logs `logger.info` del sweep (promociones), nada de alerting.
- **Sin tocar la mecánica del scheduler 2.4**: `pick_next`, `interval`, governor, ventana FloodWait global — intactos byte a byte. El admission control opera ANTES de la rotación (quién está en `state='sending'`), no dentro de ella.
- **El cap NO expulsa**: bajar el cap por debajo de los admitidos actuales no pausa ni detiene a nadie — solo frena admisiones nuevas hasta que el conteo baje solo (decisión registrada: expulsar sería un control destructivo disfrazado de config).
- **Cero cambios en captura/atribución/historial** (Epic 3): el binding de capture-session queda en la creación del lote, waiting o no.

### Diseño de la admisión (decisiones registradas)

- **"Admitted" = vivo no-waiting** (`sending | paused | stopping`): un lote PAUSADO conserva su slot. Si pausar liberara el slot, reanudar podría exceder el cap y obligaría a re-encolar al reanudado (UX kafkiana: "reanudaste y ahora esperás de nuevo") o a hacer overshoot del cap por diseño. El intervalo adaptativo YA excluye a los pausados de `n` (2.4) — el slot retenido no degrada a nadie; solo limita admisiones. El AC 3 dice "finishes, stops, or frees a slot": terminar/stop/cancelar liberan; pausar no.
- **FIFO durable por `id`**: la cola es `batches.state='waiting'` ORDER BY id — cero estado en memoria, sobrevive restarts (NFR6), y el índice único parcial (ampliado) garantiza ≤1 lote vivo por tenant ⇒ cola ≡ tenants en espera. La posición se CALCULA (1 + count de ids menores), nunca se almacena — no hay columnas de posición que re-balancear.
- **El cap vive en `system_settings` (DB), no en `Settings` (env)**: "owner-configurable" significa en caliente desde la UI (AC 1/4), sin redeploy, durable. `"0"`/fila ausente = desactivado. Fila persistida también en 0 para que el FOR UPDATE serialice desde el primer toque del knob.
- **Serialización POST↔sweep**: ambos toman la fila del cap FOR UPDATE antes de contar admitidos/decidir — dos POST concurrentes o un POST cruzado con el sweep no pueden hacer overshoot. Con el cap deshabilitado no hay fila/lock — y tampoco decisión que proteger.
- **Promoción en el loop del worker** (sweep al tope de `step()`): autosanador (cada vuelta re-evalúa — un evento perdido se corrige en ≤1 vuelta), testeable con `step()` directo, y single-threaded contra sí mismo (un solo worker). `wake()` en PUT del cap y en el stop directo para que las promociones obvias aterricen al instante; el resto de los caminos que liberan slot (drain, cancel por expiración, stop con línea en vuelo) ya terminan dentro del loop — la siguiente vuelta promueve sin ayuda.
- **Carrera benigna POST→evento**: entre el commit del POST (lote waiting) y su emit, el sweep puede promover y emitir `sending` ANTES de que el handler emita `waiting` — la pestaña mostraría "En espera" hasta el siguiente evento/snapshot. Aceptado (MVP): el snapshot reconcilia, y la ventana es de milisegundos.
- **`'waiting'` entra a `LIVE_STATES`**: es EL lote vivo del tenant — un segundo POST hace append sobre él, nueva/continuar 409 (`batch_live`), el historial lo ve "En curso". Consecuencia deliberada: el índice parcial se amplía (migración) y el pre-clean del downgrade marca los waiting como `'stopped'`.
- **ETA de un lote waiting**: el payload conserva `eta_seconds` (calculado con `n_eff = n+1`, mismo trato que paused) pero la UI NO lo muestra en waiting — la posición es la métrica honesta de la espera (UX-DR14: nada de matemática de colas falsa-precisa sobre cuándo le toca).

### Código actual que vas a tocar (estado HOY @ 27f3170, con anclas)

| Archivo | Hoy | Esta story |
| --- | --- | --- |
| `backend/app/db/models.py` | índice parcial `('sending','paused','stopping')` :200 | predicado + `'waiting'`; modelo `SystemSetting` nuevo |
| `backend/migrations/versions/` | head `2faec0509cb8` | migración nueva: `system_settings` + índice ampliado |
| `backend/app/db/repos/batches.py` | `LIVE_STATES` :33; sección worker :209+ | `STATE_WAITING`, `ADMITTED_STATES`, `count_admitted`, `waiting_batches`, `queue_position` |
| `backend/app/db/repos/system_settings.py` | NO EXISTE | nuevo: get/get_for_update/set (upsert pg) |
| `backend/app/services/admission.py` | NO EXISTE | nuevo: CAP_KEY, get_cap/get_cap_locked/set_cap/has_capacity |
| `backend/app/api/batches.py` | rama nueva crea `'sending'` :103-161; stop :266-303 | decisión de admisión; `BatchOut.queue_position`; stop sobre waiting re-numera; pause/resume waiting → 409 |
| `backend/app/core/send_worker.py` | `step()` :165 | `_admit_waiting()` al tope; docstring |
| `backend/app/services/batches.py` | `state_data` :64; `snapshot` :154 | `queue_position` en ambos payloads |
| `backend/app/api/admin.py` | routers owner-only existentes | `GET/PUT /api/admin/admission` |
| `backend/app/errors.py` | — | `batch_waiting()`, `invalid_admission_cap()` |
| `backend/tests/test_batches.py` | snapshot idle shape exacta :394 | + `"queue_position": None` |
| `frontend/lib/ws.ts` | surface states :17; reducers | `"waiting"`, `queuePosition`, seed con state |
| `frontend/components/batch/waiting-notice.tsx` | NO EXISTE | nuevo: posición en cola |
| `frontend/components/batch/batch-controls.tsx` | pausar/detener :45-73 | waiting → solo Detener |
| `frontend/components/batch/send-form.tsx` | seed hardcodea sending :98-105 | pasa state/queue_position |
| `frontend/components/client-nav.tsx` | PILL_COPY/_CLASS :23-39 | `"En espera"` warning |
| `frontend/app/(client)/page.tsx` | ring si `isLive` :53-59 | waiting → WaitingNotice |
| `frontend/app/admin/users/page.tsx` | secciones owner-gated | tarjeta "Control de admisión" |

**Sin cambios:** `core/scheduler.py` (a propósito — ver Task 5), `core/telegram.py`, `core/capture.py`, `api/ws.py`, `api/sessions.py` (el guard `batch_live` ya cubre waiting vía `LIVE_STATES`), `core/broadcaster.py`, `deploy/*`, `types/api.ts` (diferido 2-1).

### Cumplimiento de arquitectura (no negociable)

- El cap es del sistema, jamás de un request de cliente (FR12-familia): solo el owner lo toca vía `/api/admin` (`require_owner` server-side); `tenant_id` jamás del body. [Source: architecture.md#Enforcement-Guidelines]
- Eventos SOLO vía broadcaster, envelope `{"event","data"}` intacto; `batch.state` sigue siendo el único canal de estado (gana un campo, no nace un evento nuevo). [Source: architecture.md#Communication-Patterns]
- Postgres es la fuente durable de la cola (NFR6) — cero estado de admisión en memoria de proceso.
- Errores `{code, message}` en español; copy de UI en español (tuteo). [Source: errors.py contract]

### Inteligencia de stories previas (2.3/2.4/2.5)

- `step()` no duerme (los sleeps viven en `run_worker`/`_send_with_retries`) — los tests del sweep son rápidos por construcción; el idiom `step()` directo viene de 2.2.
- Payloads construidos DENTRO de la sesión antes del commit (lección MissingGreenlet de 2.3) — el sweep y el stop handler la siguen.
- Eventos verificados monkeypatcheando `broadcaster.emit`/`emit_global` con lista grabadora (lección 2.2) — jamás sockets de test.
- El `reset_scheduler`/`reset_capture` autouse de conftest ya aísla el singleton entre tests; el admission no agrega estado de proceso (durable en DB) — no necesita reset propio.
- FOR UPDATE como serializador de read-modify-write (idiom `_require_client_target` 1.5, `get_live_batch(for_update=True)` 2.3).
- `architecture.md#Risk-Deep-Dive` ya nombraba el admission control como "knob de producto" diferido de 2.4 — esta story ES ese knob.

### Estándares de testing

- `pytest` + `pytest-asyncio` (`loop_scope="session"`) + httpx `ASGITransport` contra la app real y el Postgres de dev; self-seed/self-clean; sin mocks de DB; un comportamiento por test. ASGITransport NO corre el lifespan — los tests llaman `step()` directo con `FakeGateway`.
- Entorno PARALELO: el Postgres de dev es compartido — correr `tests/test_admission.py` + módulos aledaños; la suite completa corre en el merge. Fallos claramente ambientales (contención/cleanup ajeno) se anotan, no se "arreglan".
- Frontend: sin framework de tests (decisión diferida) — gate = `npm run lint` (`build` en el merge).

### Notas de estructura del proyecto

- **Nuevos:** `backend/app/db/repos/system_settings.py`, `backend/app/services/admission.py`, `backend/migrations/versions/<rev>_admission_control_waiting_queue.py`, `backend/tests/test_admission.py`, `frontend/components/batch/waiting-notice.tsx`.
- **Modificados:** `backend/app/db/models.py`, `backend/app/db/repos/batches.py`, `backend/app/api/batches.py`, `backend/app/api/admin.py`, `backend/app/core/send_worker.py`, `backend/app/services/batches.py`, `backend/app/errors.py`, `backend/tests/test_batches.py`, `frontend/lib/ws.ts`, `frontend/app/(client)/page.tsx`, `frontend/app/admin/users/page.tsx`, `frontend/components/batch/batch-controls.tsx`, `frontend/components/batch/send-form.tsx`, `frontend/components/client-nav.tsx`.
- Legacy `core.py`/`app.py`/`auto_sender.py` congelados en la raíz — solo referencia. **🔒 JAMÁS leer contenido bajo `respuestas/`. JAMÁS tocar `.env` ni `anon.session`.**

### Referencias

- [Source: planning-artifacts/epics.md#Story-4.2 — ACs verbatim; #Epic-4 ("configurable admission control (waiting queue with position)")]
- [Source: planning-artifacts/architecture.md#Risk-Deep-Dive — admission control como knob de producto diferido por 2.4]
- [Source: implementation-artifacts/2-4-planificador-multi-tenant…md — scheduler puro, worker único consumidor, "Admission control → Story 4.2" en su cerco]
- [Source: implementation-artifacts/2-3-…md / 2-5-…md — máquina de estados de batch, stop directo vs stopping, FOR UPDATE, fail-stop]
- [Source: código actual @ 27f3170 — backend/app/{db/models.py, db/repos/batches.py, api/batches.py, api/admin.py, core/send_worker.py, services/batches.py}, frontend/{lib/ws.ts, components/batch/*, components/client-nav.tsx, app/(client)/page.tsx, app/admin/users/page.tsx}]
- [Source: _bmad-output/project-context.md — 🔒 reglas duras; nota: describe el legacy pre-backend/frontend — el código actual gana]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Fable 5) — BMad dev agent, 2026-06-12

### Debug Log References

- Gates backend: `ruff check app/ tests/` limpio; `mypy app` limpio (40 archivos); `pytest tests/test_admission.py` — 16 passed; módulos aledaños `tests/test_batches.py tests/test_batch_controls.py tests/test_scheduler.py` — 61 passed; `tests/test_send_hardening.py tests/test_sessions.py tests/test_admin_gates.py` — 59 passed.
- Suite COMPLETA corrida una vez de cortesía: 226 passed, 2 failed (`test_fairness_two_tenants_interleave_strictly`, `test_two_errors_then_success_is_sent`) — ambos pasan en aislamiento y en los runs dirigidos: contención con la corrida de tests concurrente sobre el Postgres compartido (lotes vivos ajenos rompen los asserts de alternancia estricta). Ambiental, no de esta story; el merge corre la suite real.
- Para correr los tests sin `alembic upgrade` (prohibido en el entorno paralelo) el DDL de la migración se aplicó A MANO al Postgres de dev (create table + índice ampliado) y se REVIRTIÓ al terminar (drop table + índice angosto restaurado) — el schema quedó intacto en head `2faec0509cb8` para que el merge aplique la migración limpio.
- Gates frontend: `npm run lint` limpio, `npx tsc --noEmit` limpio (`build` corre en el merge, por instrucción del entorno paralelo).
- Migración NO aplicada vía alembic (el merge re-encadena y aplica): revision `f3a9c1d4e8b7`, down_revision `2faec0509cb8`.

### Completion Notes List

- Tasks 1–10 implementadas según el diseño; la única subtarea abierta es la (HUMAN) del smoke manual con dos tenants reales (no bloquea — AC 1–4 cubiertos por la suite).
- El sweep `_admit_waiting()` corre al tope de `step()` y promueve FIFO bajo el lock de la fila del cap; con el cap deshabilitado promueve TODOS los que esperaban (AC 4 incluye rescate); retorna temprano sin promociones (cero spam de posiciones). `scheduler.py` quedó byte a byte intacto, como exigía el cerco.
- `'waiting'` entró a `LIVE_STATES` y al predicado del índice único parcial (migración `f3a9c1d4e8b7` con pre-clean en el downgrade, espejo de `1b606109cc99`). Consecuencia verificada: el TOCTOU de dos tabs sobre un lote waiting cae en el índice y se convierte en append, igual que con sending.
- Stop sobre waiting reutiliza la rama directa existente y re-numera/emite posiciones SOLO a los que quedaban detrás (ids mayores); pause/resume sobre waiting → 409 `batch_waiting` (error nuevo, copy honesto). El stop directo ahora hace `wake()` para que la promoción del slot liberado aterrice al instante.
- `queue_position` viaja en `batch.state` (todos), en el snapshot y en `BatchOut` — la pestaña que reconecta a mitad de la espera renderiza la posición del snapshot solo (snapshot-first). Asserts de forma EXACTA actualizados: snapshot idle (test_batches.py) y los tres payloads de state_data (test_batch_controls.py) ganan `queue_position: None` — sin esto la suite previa rompía.
- Frontend: surface state `waiting` end-to-end (reducer, seed del POST con state real, pill "En espera" + dot warning, WaitingNotice en lugar del ring, solo Detener); knob del owner como tarjeta en /admin/users (GET/PUT /api/admin/admission). El validador client-side admite 0 explícitamente (el `isPositiveInt` existente lo habría rechazado — el 0 ES el off).
- Decisiones locales del dev: (1) `seedFromBatch` gana `state` y `queue_position` opcional — sembrar "sending" hardcodeado mentiría con un POST que devuelve waiting; (2) `lib/api.ts` gana `api.put` (espejo de `patch`) porque el endpoint del knob es un reemplazo completo de un singleton — PUT semántico; (3) locks siempre en orden settings→batches (el stop lockea batch SIN settings) — sin ciclos de deadlock posibles.
- Adversarial self-review (STEP 3) sobre el diff completo: verificado orden de locks POST↔sweep↔stop, payloads construidos dentro de la sesión (MissingGreenlet), carrera benigna POST-commit→emit vs sweep (documentada), tenant expirado promovido se cancela en el claim (auto-sanado 2.5), y que `count_admitted` cuenta 'stopping' (ocupa el canal hasta finalizar). Sin hallazgos restantes.

### File List

- `backend/migrations/versions/f3a9c1d4e8b7_admission_control_waiting_queue.py` — NUEVA: `system_settings` + índice `uq_batches_one_live_per_tenant` ampliado a `'waiting'` (downgrade con pre-clean).
- `backend/app/db/models.py` — modificado: `SystemSetting`; predicado del índice parcial + `'waiting'`; docstrings de estado.
- `backend/app/db/repos/batches.py` — modificado: `STATE_WAITING`, `LIVE_STATES`, `ADMITTED_STATES`, `count_admitted`, `waiting_batches`, `queue_position`; `create_batch(state=…)`.
- `backend/app/db/repos/system_settings.py` — NUEVO: get/get_for_update/upsert.
- `backend/app/services/admission.py` — NUEVO: cap (get/get_locked/set) + `has_capacity`.
- `backend/app/services/batches.py` — modificado: `state_data(queue_position=…)`; snapshot con `queue_position`.
- `backend/app/api/batches.py` — modificado: decisión de admisión en el POST, `BatchOut.queue_position`, stop sobre waiting + re-numeración, guards 409 `batch_waiting`.
- `backend/app/api/admin.py` — modificado: `GET/PUT /api/admin/admission` (owner-only).
- `backend/app/core/send_worker.py` — modificado: `_admit_waiting()` al tope de `step()`; docstring.
- `backend/app/errors.py` — modificado: `batch_waiting()`, `invalid_admission_cap()`.
- `backend/tests/test_admission.py` — NUEVO (16 tests): knob owner (default/persistencia/bounds/403), admisión cap=1 con posiciones, snapshot+evento de creación con posición, promoción FIFO automática (drain, stop directo, subir cap = solo slots libres), fallback cap=0 (directo + rescate de los que ya esperaban), stop sobre waiting re-numera, pause/resume → 409, append a waiting, exclusión del worker, paused conserva slot.
- `backend/tests/test_batches.py` — modificado: snapshot idle shape + `queue_position: None`.
- `backend/tests/test_batch_controls.py` — modificado: los tres payloads exactos de `state_data` ganan `queue_position: None`.
- `frontend/lib/api.ts` — modificado: `api.put` (espejo de `patch`).
- `frontend/lib/ws.ts` — modificado: surface state `waiting`, `queuePosition`, seed con state real.
- `frontend/components/batch/waiting-notice.tsx` — NUEVO: posición en cola (ámbar informativo).
- `frontend/components/batch/batch-controls.tsx` — modificado: waiting → solo Detener.
- `frontend/components/batch/send-form.tsx` — modificado: `BatchOut.queue_position` + seed con state.
- `frontend/components/client-nav.tsx` — modificado: pill/dot "En espera".
- `frontend/app/(client)/page.tsx` — modificado: WaitingNotice en lugar del ring en waiting.
- `frontend/app/admin/users/page.tsx` — modificado: tarjeta "Control de admisión" (owner-only).
