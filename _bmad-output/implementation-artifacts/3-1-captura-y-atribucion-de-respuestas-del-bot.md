---
baseline_commit: a9c8df8c1f7c976fc2199280aa3e80982bf3c967
---

# Story 3.1: Captura y atribución de respuestas del bot

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGÍA (decisión del owner 2026-06-11):** el término de producto para un prefijo es **"gate"** — DB, API, identificadores de código y todo el copy de UI (masculino: "el gate"). epics.md / architecture.md / docs de UX son anteriores al renombre y todavía dicen "prefijo/prefixes" — lee cada "prefijo" como "gate"; donde haya conflicto, gana "gate". En esta story el AC 3 del epic dice "tenant+prefix": léelo "tenant+gate"; los snapshots `gate_value`/`gate_name` de `batches` (2.2) son el patrón a copiar en `capture_sessions`.

> **⚠️ HALLAZGOS DIFERIDOS QUE ESTA STORY ABSORBE (deferred-work.md, review de 2-5):** (1) **2-5 LOW `send_worker.py:28`** — la mitad "replies" del AC 5 de 2.5 ("incoming replies buffer in memory … flush on DB recovery") quedó como decisión registrada PARA 3.1: no había productor hasta que existieran los handlers de captura. Esta story la cobra POR DISEÑO: la cola `asyncio.Queue` + consumidor único con retry-forever de la Task 5 ES el buffer en memoria (los items se acumulan mientras la DB está caída y se vuelcan al volver; `catch_up=True` ya cubre las desconexiones de Telegram desde 2.2). (2) **2-5 LOW `send_worker.py:616`** — el confirm de la reconciliación de boot asume que la fila de intent existe: `set_message_id` es un UPDATE pelado que no-opea sin fila, dejando líneas `'sent'` SIN registro en `send_log` (3.1 no podría atribuir sus replies) y con su `message_id` invisible para `used_message_ids`. Fix de una línea (Task 4): `record_intent` (idempotente) antes de `set_message_id` en la rama confirm de `_boot_recovery`. Marcar ambos RESOLVED en deferred-work.md al cerrar.

## Story

As a client,
I want every bot reply captured and saved to MY space automatically,
So that my results are mine and complete.

## Acceptance Criteria

1. **Given** the schema, **when** this story's migration is applied, **then** `capture_sessions` and `responses` tables exist (full revisions + filtered/deduped rows, all tenant-scoped).
2. **Given** the Telethon client, **when** `cc-core` starts, **then** `NewMessage` and `MessageEdited` handlers are registered once and capture bot replies.
3. **Given** a client sends a batch with a prefix, **when** no capture session is active for that tenant+prefix, **then** one is created and bound automatically at batch start (matching legacy `/api/enviar` semantics) and all subsequent attributed responses save to it.
4. **Given** a bot reply with `reply_to_msg_id`, **when** the capture handler processes it, **then** the id resolves against `send_log` to the exact tenant, batch and line, and the response saves to that tenant's active capture session — never to anyone else's.
5. **Given** an already-captured message that the bot edits, **when** the edit arrives, **then** `message_id` is preserved so attribution holds, ❌→✅ transitions move the counters, and duplicate edits are deduped (per-message_id state).
6. **Given** a response containing `CC:` data, **when** extraction runs (port of `extraer_cc`/`RE_CC`, each value truncated at the literal `Status`), **then** only session-new CC lines are added to the filtered rows (per-session dedup persisted in Postgres).
7. **Given** a reply that matches no `send_log` record, **when** it arrives, **then** it is logged to the unmatched-replies monitoring bucket (ban-guardrail observability).
8. **Given** a captured response, **when** it is saved, **then** a tenant-scoped `response.captured` WS event is emitted.
9. **Given** the backend test suite, **when** attribution and isolation tests run, **then** they cover reply mapping, edits, unmatched replies, and cross-tenant access (which must fail) — all passing.

## Tasks / Subtasks

### Backend (Tasks 1–8)

- [x] Task 1: migración + modelos — `capture_sessions`, `responses`, `batches.capture_session_id` (AC: 1, 3)
  - [x] `backend/app/db/models.py`: clase `CaptureSession`, tabla **`capture_sessions`** (nombre literal de architecture.md). Columnas: `id` PK; `tenant_id` FK `tenants.id` ondelete CASCADE `index=True`; `gate_value` `String(20)` + `gate_name` `String(80)` — SNAPSHOTS verbatim del catálogo, mismo idiom y justificación que `Batch` (:181-184: retirar/renombrar un gate jamás reescribe historia; sin FK a `gates`); `name` `String(200)` nullable (nombre amigable; 200 es el cap del rename de 3.3 y del legacy `escribir_nombre`; NULL ⇒ la UI cae a un formato de `created_at`, espejo de `nombre_bonito`); `is_active` Boolean `server_default=false()` NOT NULL; `created_at`/`updated_at` con `func.now()`. Índice parcial **`uq_capture_sessions_one_active_per_tenant`** sobre `(tenant_id)` `WHERE is_active` — mismo idiom que `uq_batches_one_live_per_tenant` (models.py :196-201): UNA sesión activa por tenant, DB-enforced. Docstring: "the legacy active `Sesion` generalized per tenant; activation deactivates the previous one (legacy: sessions are replaced by reassignment, never closed)".
  - [x] `backend/app/db/models.py`: clase `Response`, tabla **`responses`** — guarda AMBOS tipos de fila (architecture :126: "responses (full + filtered/deduped rows)"): `id` PK; `tenant_id` FK CASCADE idx; `capture_session_id` FK `capture_sessions.id` ondelete CASCADE idx; `batch_id` FK `batches.id` ondelete **SET NULL** nullable; `line_id` FK `batch_lines.id` ondelete **SET NULL** nullable (la captura sobrevive a limpiezas de lote; la sesión es la dueña real); `message_id` `BigInteger` NOT NULL + índice `ix_responses_message_id` (la clave del estado per-message_id del AC 5 — mismo ancho que `send_log.message_id`); `kind` `String(10)`: `'full'` (revisión completa) | `'cc'` (dato filtrado); `status` `String(10)` nullable: `'ok'` (✅) | `'rejected'` (❌), NULL en filas `'cc'`; `text` `Text` (la revisión completa, o el VALOR CC extraído en `'cc'`); `created_at`. Índice parcial **`uq_responses_session_cc`** sobre `(capture_session_id, text)` `WHERE kind = 'cc'` — el dedup CC por sesión lo garantiza Postgres, no solo el código (FR17: "continuar" en 3.3 precarga el set desde estas filas).
  - [x] `backend/app/db/models.py` `Batch`: columna nueva `capture_session_id: Mapped[int | None]` FK `capture_sessions.id` ondelete **SET NULL** nullable `index=True` — el binding del AC 3 ("bound automatically at batch start"). Docstring: la atribución resuelve reply → send_log → line → batch → ESTA sesión, aunque el lote ya esté `completed`/`stopped`/`cancelled` (promesa de 2.5: "Story 3.1 attributes their replies even on a cancelled batch").
  - [x] **Migración** (`alembic revision -m "capture sessions and responses"`, `down_revision = "ede0d6d7b7c6"` — head actual, verificado): `op.create_table` × 2 + índices (incl. ambos parciales con `postgresql_where`) + `op.add_column("batches", …)`. Escrita a mano como `ede0d6d7b7c6` (su idiom exacto: FKs nombradas `op.f('fk_…')`, índices `ix_`/`uq_`); reflejar TODO en los modelos para que un autogenerate posterior dé diff vacío. `alembic upgrade head` en el Postgres de dev.
- [x] Task 2: capa de repos — `capture_sessions` + `responses` (AC: 1, 3, 6)
  - [x] NUEVO `backend/app/db/repos/capture_sessions.py` (estilo repos/batches.py: "pure ORM, flush not commit"; TENANT-SCOPED — las funciones toman `tenant_id` explícito; la sección usada por el pipeline de captura corre fuera de requests y queda documentada como la worker-section de batches.py): `get_active(session, tenant_id) -> CaptureSession | None`; `create_active(session, tenant_id, gate_value, gate_name) -> CaptureSession` (UPDATE `is_active=False` a la activa previa + INSERT activa nueva, flush — el índice parcial es el cinturón); `resolve_for_batch(session, tenant_id, gate_value, gate_name) -> CaptureSession` — **la semántica legacy del AC 3**: si la activa existe Y su `gate_value` coincide → reúsala; si no → `create_active` (legacy: "/api/enviar reuses the active Sesion when its slug matches the submitted prefix, otherwise auto-creates one").
  - [x] NUEVO `backend/app/db/repos/responses.py` (mismo estilo): `last_full_revision(session, message_id) -> Response | None` (kind `'full'`, `order_by(id.desc()).limit(1)` vía `ix_responses_message_id` — ES el estado per-message_id durable: reemplaza al dict en memoria del legacy y dedupea solo los replays de `catch_up` tras un restart); `add_full(session, *, tenant_id, capture_session_id, batch_id, line_id, message_id, status, text) -> Response`; `add_new_cc(session, *, tenant_id, capture_session_id, batch_id, line_id, message_id, values: list[str]) -> list[str]` (SELECT de los `text` ya existentes en la sesión con `kind='cc'` → INSERT solo de los nuevos preservando orden → devuelve los insertados; sin carrera: el consumidor de captura es único, Task 5 — el índice único es la red); `cc_count(session, capture_session_id) -> int`.
  - [x] NUEVO en `backend/app/db/repos/send_log.py`: `get_by_message_id(session, message_id) -> SendLog | None` — la búsqueda caliente prometida en el docstring de `SendLog` (models.py :282: "The hot attribution lookup of Story 3.1") sobre `ix_send_log_message_id`. Actualizar el docstring del módulo (:6: "read by Story 3.1's capture/attribution" se cumple aquí).
- [x] Task 3: `core/cc_extract.py` — port EXACTO de `extraer_cc`/`RE_CC` (AC: 6)
  - [x] NUEVO `backend/app/core/cc_extract.py`: `RE_CC = re.compile(r"(?i)\bCC\s*:\s*([^\n]+)")` y `extract_cc(text) -> list[str]` — port línea a línea de legacy core.py :35 y :59-66: por cada match, `value.split("Status")[0].strip()`, descartar vacíos, preservar orden. Docstring con el 🔒 de project-context: el truncado en el substring literal `Status` es INTENCIONAL — no "arreglarlo". Sin más lógica (el dedup vive en el repo; espejo de `apply_gate` en services/batches.py :16-29, que portó `agregar_prefijo` igual de literal).
- [x] Task 4: `core/attribution.py` — `reply_to_msg_id → (tenant, batch, line, sesión)` (AC: 4, 5, 7; absorbe diferido 2-5 `:616`)
  - [x] NUEVO `backend/app/core/attribution.py` (módulo nombrado por architecture; sin telethon, sin requests): dataclass `Attribution(tenant_id, batch_id, line_id, capture_session_id)` + `async resolve(session, *, message_id, reply_to_msg_id) -> Attribution | None`. Orden de resolución: **(1)** fila previa en `responses` para ese `message_id` (`last_full_revision`) → reusar su atribución completa — así las EDICIONES conservan la atribución aunque algo borre la fila de `send_log` (AC 5: "message_id is preserved so attribution holds"); **(2)** `reply_to_msg_id` no nulo → `send_log_repo.get_by_message_id` → cargar el `Batch` → su `capture_session_id`; si es NULL (lote pre-3.1) → `capture_sessions_repo.resolve_for_batch` con los snapshots `gate_value`/`gate_name` del lote + backfill de `batch.capture_session_id` (decisión registrada: replies tardíos a lotes viejos no se pierden); **(3)** nada → `None` (el caller lo manda al bucket de no-atribuidos).
  - [x] `backend/app/core/send_worker.py` `_boot_recovery`, rama confirm (:615-626): agregar `await send_log_repo.record_intent(session, line)` ANTES de `await send_log_repo.set_message_id(session, line_id, match_id)` — el diferido 2-5 `:616`: sin fila de intent (líneas `'sending'` pre-migración-2.5) el UPDATE no-opea y la línea queda `'sent'` sin registro que 3.1 pueda atribuir. `record_intent` ya es get-or-create idempotente (repos/send_log.py :20-40) — cero riesgo de duplicado.
- [x] Task 5: `core/capture.py` — pipeline de captura: cola + consumidor único + state machine del legacy (AC: 2, 4, 5, 6, 7, 8; cobra el buffer diferido 2-5 `:28`)
  - [x] NUEVO `backend/app/core/capture.py`: dataclass `IncomingReply(message_id: int, reply_to_msg_id: int | None, text: str, edited: bool)` (tipos planos — telethon JAMÁS cruza la frontera); `_queue: asyncio.Queue[IncomingReply]` module-level; `def enqueue(reply) -> None` (`put_nowait`; síncrono — lo llama el bridge de telegram.py); `async def run_capture() -> None` — tarea infinita espejo de `run_worker`: `reply = await _queue.get()` → `process_incoming(reply)` con **retry-forever** ante excepciones de DB (`logger.exception("event=db_unreachable phase=capture …")` + `await asyncio.sleep(_RETRY_SECONDS)` y reintentar EL MISMO item; `asyncio.CancelledError` siempre re-lanza). **Esa pareja cola-bloqueada+consumidor ES el buffer en memoria del AC 5 de 2.5** (decisión registrada allí, cobrada aquí): con la DB caída los replies entrantes se acumulan en `_queue` y se vuelcan en orden al volver; `catch_up=True` (telegram.py :59, desde 2.2) ya recupera lo llegado durante desconexiones de Telegram. Borrar/actualizar la nota "NOTE for Story 3.1" del docstring de send_worker.py (:28-31) — se cumple aquí.
  - [x] `async def process_incoming(reply) -> None` — el port de `_manejar_bot` (legacy app.py :333-385), con el estado per-message_id derivado de la DB (no dict en memoria — sobrevive restarts y dedupea los replays de catch_up): **(a)** sesión propia (`async_session_factory`, jamás request-scoped); `attribution.resolve(...)`; `None` → bucket de no-atribuidos: `logger.warning("event=unmatched_reply message_id=%s reply_to=%s", …)` + contador module-level `_unmatched_total` (espejo de `_sent_by_tenant` de send_worker :80-82 — observabilidad de proceso, semilla de 4.3) y RETURN sin guardar nada (AC 7). **(b)** `previous = last_full_revision(message_id)`; si existe y `previous.text == reply.text` → no-op total ("edición sin cambios reales", legacy :335-336). **(c)** status efectivo (legacy :339-344): `"✅" in text` → `'ok'`; `"❌" in text` → `'rejected'`; ninguno → el status previo (edición intermedia ⏳ conserva estado); sin previo y sin emoji → no-op total (decisión registrada, paridad legacy: el primer ⏳ no produce fila; su edición posterior a ✅/❌ llega con `reply_to` intacto y se atribuye igual). **(d)** persistir: `add_full(...)` con el status efectivo — **decisión registrada (desviación consciente del disco legacy):** las revisiones `'rejected'` SÍ se persisten porque Postgres es ahora el store que respalda la vista Completa de 3.2 (su AC pinta filas con glifo ❌; el legacy solo las mostraba en vivo y las perdía al recargar). Si status `'ok'`: `extract_cc(text)` → `add_new_cc(...)` → `new_cc`. **(e)** commit; capturar TODOS los atributos a emitir ANTES de cerrar la sesión (lección MissingGreenlet de 2.3). **(f)** emitir DESPUÉS del commit, paridad de emisión legacy: transición a `'ok'` (previo ≠ ok) → emite; `'ok'` ya-ok con `new_cc` no vacío → emite (el "ok-edit" legacy :362-371); transición a `'rejected'` → emite; ok→ok con texto nuevo pero sin CC nuevos → persiste SIN emitir (paridad legacy :347-348, decisión registrada). Las transiciones ❌→✅ "mueven los contadores" solas: la última revisión por `message_id` define el estado vigente — los counts son derivados, no columnas.
  - [x] Evento tenant-scoped **`response.captured`** (nombre literal de architecture.md, envelope del broadcaster): `{"session_id", "batch_id", "message_id", "status", "previous_status", "edited", "text", "new_cc", "cc_total", "captured_at"}` — `new_cc` lista (puede ser `[]`), `cc_total` = `cc_count` de la sesión tras guardar (la métrica "CC nuevas" de 3.2 y el `cc_new` del snapshot salen del mismo número), `captured_at` ISO-8601 UTC. 3.2 lo consume tal cual: fila a Completa, `new_cc` a Filtrada, ring.
  - [x] `def reset() -> None`: vaciar `_queue` + `_unmatched_total = 0` — y fixture autouse `reset_capture` en conftest.py espejo de `reset_scheduler` (:153-163): el estado module-level contaminaría la suite entera (la misma trampa que el governor en 2.4).
- [x] Task 6: bridge Telethon + wiring de lifespan — handlers registrados UNA vez (AC: 2)
  - [x] `backend/app/core/telegram.py`: atributo `self._capture: Callable[[IncomingReply], None] | None = None` + `def register_capture(self, callback) -> None` (se llama ANTES de `connect()`); en `connect()`, tras autorizar y resolver target, registrar UNA VEZA los handlers `events.NewMessage()` y `events.MessageEdited()` **sin filtro `chats=`** (decisión legacy web, app.py :102-113: el filtrado vive en el cuerpo del handler — sobrevive a un futuro multi-target) que filtran `event.out` y `event.chat_id != self._target_id` (`self._target_id = await self.client.get_peer_id(self._entity)` capturado en `_resolve_target`) y llaman `self._capture(IncomingReply(message_id=event.message.id, reply_to_msg_id=event.message.reply_to_msg_id, text=event.raw_text or "", edited=…))`. telethon sigue confinado a este módulo (frontera de architecture — `capture.py` no lo importa; el import va telegram→capture solo por el dataclass, sin ciclo). Gateway no autorizado al boot ⇒ no se registra nada (tampoco puede llegar nada); el re-auth runbook es 4.4.
  - [x] `backend/app/main.py` lifespan (:32-42): `gateway.register_capture(capture.enqueue)` ANTES de `await gateway.connect()`; `capture_task = asyncio.create_task(capture.run_capture())` junto al worker; en shutdown, cancel + suppress como `worker_task`. ASGITransport no corre el lifespan ⇒ los tests llaman `process_incoming` directo (idiom de `step()`).
- [x] Task 7: binding automático al arrancar lote en `POST /api/batches` (AC: 3)
  - [x] `backend/app/api/batches.py`, rama new-batch (:101-147): dentro de LA MISMA transacción que `create_batch`/`add_lines`, `cs = await capture_sessions_repo.resolve_for_batch(session, tenant_id, gate_value, gate_name)` + `batch.capture_session_id = cs.id` — el commit del lote ES el "bound automatically at batch start". El fallback de `IntegrityError` (:118-127) ya hace rollback de TODO (lote + sesión creada) y cae al append — el índice parcial de sesiones solo puede chocar junto al de lotes, mismo manejo, cero código extra (documentarlo en el comment existente). La rama APPEND no se toca: el lote vivo ya está ligado y el legacy "keeps the active Sesion" sale gratis. `BatchOut` NO cambia (cero OpenAPI nuevo).
- [x] Task 8: `cc_new` real en el snapshot (AC: 8; cobra el "hardcoded until Epic 3")
  - [x] `backend/app/services/batches.py` `snapshot` (:91-134): `cc_new` deja de ser `0` literal — en AMBAS ramas (idle y live) = `cc_count` de la sesión activa del tenant (`get_active`; sin sesión → 0). Decisión registrada: el contador NO se resetea entre lotes (espejo del legacy "counters never reset"); un tab reconectado reconstruye la métrica del snapshot solo (snapshot-first). Actualizar el docstring (:93-95). `api/ws.py` no se toca (el payload solo cambia de valor, no de forma); el frontend ya consume `cc_new` (ws.ts :56/:137) — cero cambios allí.

### Frontend

- [x] (sin tareas) **Esta story no toca `frontend/`** — decisión registrada: `response.captured` lo consume la Story 3.2 (vistas Completa/Filtrada); el reducer de ws.ts ignora eventos desconocidos sin romper (:218-219, verificado) y `cc_new` ya viaja por el snapshot existente. El OpenAPI no cambia (sin endpoints ni campos REST nuevos) ⇒ NO regenerar `types/api.ts`.

### Tests + gates (Tasks 9–10)

- [x] Task 9: `backend/tests/test_attribution.py` — NUEVO (nombre literal del árbol de architecture: "reply mapping, edits, unmatched replies") (AC: todos, gate del AC 9)
  - [x] Setup idiom: fixtures existentes `ctx`/`gate`/`client_user`/`fake_gateway` (no inventar otros); lote real vía `POST /api/batches` + `send_worker.step()` con `FakeGateway` (ids incrementales 1..n pueblan `send_log.message_id` — conftest :57-62); eventos verificados monkeypatcheando `broadcaster.emit` con lista grabadora (lección 2.2 — jamás sockets); las llamadas de captura van DIRECTO a `capture.process_incoming(IncomingReply(...))` (sin telethon, sin lifespan).
  - [x] **Binding (AC 3):** el POST deja `batch.capture_session_id` no nulo y una `capture_sessions` activa con los snapshots del gate; segundo lote del MISMO gate (tras drenar el primero) reúsa LA MISMA sesión activa; lote de un gate distinto → sesión nueva activa, la anterior `is_active=False` (y solo una activa por tenant — el índice parcial).
  - [x] **Reply mapping (AC 4, 6, 8):** `process_incoming` con `reply_to_msg_id` = id registrado y texto `"✅ … CC: 4111 Status aprobada"` → fila `'full'` status `'ok'` con tenant/sesión/batch/line correctos + fila `'cc'` con `"4111"` (truncado en `Status`) + evento `response.captured` al tenant correcto con `new_cc=["4111"]`, `cc_total=1`, `previous_status=None`.
  - [x] **Edits (AC 5):** mismo `message_id`, texto editado con CC nuevo → segunda revisión `'full'`, solo el CC nuevo en `'cc'`, evento con `edited=True`; edición con texto IDÉNTICO → cero filas nuevas, cero eventos; ❌ primero (fila `'rejected'` + evento) y luego edición ✅ → transición con `previous_status="rejected"` y el estado vigente derivado (última revisión) es `'ok'`; ok→ok con texto nuevo sin CC → fila nueva SIN evento (paridad legacy).
  - [x] **Dedup CC (AC 6):** el mismo valor CC en dos respuestas de la sesión → UNA fila `'cc'`; el segundo evento lleva `new_cc=[]`. Y la sesión nueva (otro gate) NO hereda el dedup (es por sesión).
  - [x] **Unmatched (AC 7):** `reply_to_msg_id=None` y `reply_to_msg_id` desconocido → cero filas, cero eventos, `caplog` con `event=unmatched_reply`, `_unmatched_total` incrementado.
  - [x] **Aislamiento cross-tenant (AC 4, 9):** dos tenants con lotes enviados; el reply al mensaje de A → filas SOLO bajo el tenant/sesión de A; el grabador de B no recibe nada; y los repos tenant-scoped (`get_active(B)`) no devuelven jamás la sesión de A — "cross-tenant access must fail".
  - [x] **Buffer DB-down (diferido 2-5 `:28`):** monkeypatch selectivo de `async_session_factory` en capture para fallar N veces + `_RETRY_SECONDS` a 0.0 (idiom `_ERROR_RETRY_SECONDS` de test_batches.py) → con dos items encolados, `run_capture` acotado con `asyncio.wait_for` los persiste AMBOS en orden al volver la DB (el primero reintentado, el segundo desde la cola).
  - [x] **Boot reconciliation + intent (diferido 2-5 `:616`):** línea `'sending'` SIN fila de `send_log` (seed directo, el caso pre-2.5) + `fake.outgoing` con match → `_boot_recovery` → línea `'sent'` Y fila de `send_log` con el `message_id` del match (antes: ninguna fila).
  - [x] **Snapshot (Task 8):** tras una captura con CC, `batches_service.snapshot(...)` del tenant devuelve `cc_new == 1` (y el de otro tenant sigue en 0).
  - [x] **Ajustes a la suite existente (verde COMPLETA, baseline 165):** los asserts `cc_new == 0` (test_batches.py :405/:429, test_batch_controls.py :506) siguen verdes (sus tests no capturan nada) — solo actualizar el comentario "hardcoded until Epic 3"; conftest gana el fixture autouse `reset_capture` (Task 5); `cleanup_users` no necesita cambios (el CASCADE de tenant se lleva `capture_sessions`/`responses` solo — verificarlo con los FKs de la Task 1).
- [x] Task 10: gates de verificación + housekeeping (todos los AC)
  - [x] Backend: `ruff check .`, `mypy app`, `pytest` — todo verde (165 previos + los nuevos).
  - [x] Frontend: NO se toca; correr `npx tsc --noEmit` igualmente como humo de que nada se rompió por accidente.
  - [x] `_bmad-output/implementation-artifacts/deferred-work.md`: marcar `~~…~~ **RESOLVED in Story 3.1 (fecha)**` los hallazgos 2-5 `send_worker.py:28` (buffer de replies) y 2-5 `send_worker.py:616` (intent en el confirm). Los demás de 2-5 SIGUEN diferidos (ver cerco abajo). Patrón de tachado ya usado por 2.3/2.4/2.5.
  - [ ] (HUMAN — necesita credenciales reales) Smoke manual en dev: enviar una línea real, esperar el reply del bot y verificar la fila en `responses` + el evento en un tab. El **volume test de atribución con comandos reales** es un gate PRE-LANZAMIENTO de architecture (assumption A1), no de esta story. **No correr contra producción sin el OK de Richard.**

## Dev Notes

### Qué NO es esta story (cerco de alcance)

- **Vistas Completa/Filtrada en la UI** → Story 3.2 (consume `response.captured` y `cc_new`; toca ws.ts/componentes). **Historial REST + UI (listar/renombrar/eliminar/continuar), evento `session.active`, export `.txt` (`api/sessions.py`, `services/exports.py`)** → Story 3.3. **Watchdog de reply-rate / `AuthKeyError`** → 4.1. **Admission control** → 4.2. **Dashboards** → 4.3 (los logs `event=unmatched_reply` de aquí son su materia prima).
- **Cero endpoints REST nuevos y cero cambios de OpenAPI**: el binding del AC 3 vive dentro del `POST /api/batches` existente sin cambiar su contrato; `api/sessions.py` nace en 3.3.
- **Cero settings nuevos** (config.py intacto): retry seconds y demás son constantes de módulo, regla de 2.5.
- **Diferidos que SIGUEN diferidos** (deferred-work.md — no los arregles "de paso"): 2-5 MEDIUM telegram.py:111 (fragilidad del match de reconciliación: `from_user="me"` + parse_mode markdown — pipeline de ENVÍO, no de captura; la atribución de aquí matchea por `reply_to_msg_id`, no por texto, así que no la afecta), 2-5 LOW send_worker :398 (re-check de estado en `_cancel_expired_batch`) y :652 (retry en `_release_line`/`_abort_line`), 2-5 LOW ws.ts:162 (guard de `batch_id` en el reducer `line_failed` — que lo absorba 3.2, que es quien toca ws.ts), 2-2 #2/#4, 2-3 #2, 2-1/1-6 (tipos generados en admin).

### Diseño de la captura (decisiones registradas)

- **El binding es por LOTE, no por lookup-en-caliente:** `batches.capture_session_id` se fija en la transacción de creación del lote. Lectura fiel del AC 3 ("bound automatically at batch start … all subsequent attributed responses save to it") y MÁS correcta que el legacy: un reply tardío de un lote viejo aterriza en la sesión que estaba activa CUANDO ESE lote arrancó, no en la que esté activa hoy con otro gate. El fallback `resolve_for_batch` + backfill cubre lotes pre-migración.
- **Una sesión activa por tenant** (índice parcial, idiom 2.3): el legacy `Engine` tenía UNA `Sesion` activa que se reemplazaba por reasignación — generalizado por tenant. Reuso si el gate coincide, sesión nueva si no (semántica exacta de `/api/enviar`). No existe "cerrar" una sesión en 3.1 (legacy: "There is no close/finalize") — el badge En curso/Cerrada de 3.3 deriva de `is_active`.
- **Estado per-message_id DURABLE, no dict en memoria:** `last_full_revision(message_id)` reemplaza al `estado_mensajes` del legacy. Gratis: sobrevive restarts y dedupea los replays que `catch_up=True` re-entrega tras una desconexión (el legacy re-contaba). El costo es un SELECT indexado por evento — trivial a esta escala (NFR2).
- **Las revisiones ❌ se persisten** (desviación consciente del disco legacy, donde solo ✅ tocaba `completa.txt`): la vista Completa de 3.2 pinta filas con glifo ❌ y Postgres es ahora el store. La paridad de EMISIÓN sí es exacta al legacy (transición ok / ok-edit con CC nuevos / transición rejected; ok→ok sin CC no emite).
- **Solo se guarda lo atribuible.** El legacy guardaba TODO mensaje del bot en el chat destino; en multi-tenant eso es imposible sin atribución — un mensaje sin `reply_to` o con `reply_to` desconocido va al bucket (`event=unmatched_reply` + contador) y NO se persiste (AC 7; architecture: "replies that match no record are logged for monitoring"). Si el bot dejara de usar `reply_to`, el bucket es el indicador (assumption A1 del Risk Deep-Dive).
- **Cola + consumidor único = orden, dedup sin carreras y el buffer DB-down en un solo mecanismo.** Telethon puede despachar handlers concurrentes; serializar por `asyncio.Queue` da el orden del legacy, hace el dedup CC race-free sin locks y ES el buffer en memoria que 2.5 dejó por escrito para 3.1. El retry-forever del consumidor es el espejo del fail-stop de `_record_sent`: nada se descarta, todo espera a la DB.
- **`responses` única con `kind`** en vez de dos tablas: architecture solo nombra `capture_sessions` y `responses` ("full + filtered/deduped rows") y el índice parcial `WHERE kind='cc'` le da al dedup enforcement de DB sin ensuciar las filas full. FR17 (continuar con dedup precargado, 3.3) lee de aquí.
- **`extract_cc` es un port literal**, truncado en `Status` incluido — regla 🔒 de project-context ("Don't 'fix' this — it's intentional parsing").

### Código actual que vas a tocar (estado HOY @ a9c8df8, con anclas)

| Archivo | Hoy | Esta story |
| --- | --- | --- |
| `backend/app/db/models.py` | `Batch` :178-221 (snapshots de gate, sin sesión), `SendLog` :265-299 ("read by capture/attribution (3.1)", `ix_send_log_message_id` :282-283) | + `CaptureSession` + `Response`; + `Batch.capture_session_id`; promesas de docstring cumplidas |
| `backend/migrations/versions/` | head `ede0d6d7b7c6` (verificado; su idiom es la plantilla) | + migración `capture_sessions`/`responses`/`batches.capture_session_id` |
| `backend/app/db/repos/send_log.py` | `record_intent` :20-40 (idempotente), `set_message_id` :43-55, sin lookup inverso | + `get_by_message_id` |
| `backend/app/db/repos/capture_sessions.py` | NO EXISTE | nuevo: `get_active` / `create_active` / `resolve_for_batch` |
| `backend/app/db/repos/responses.py` | NO EXISTE | nuevo: `last_full_revision` / `add_full` / `add_new_cc` / `cc_count` |
| `backend/app/core/cc_extract.py` | NO EXISTE (legacy: core.py :35 `RE_CC`, :59-66 `extraer_cc`) | nuevo: port literal |
| `backend/app/core/attribution.py` | NO EXISTE | nuevo: `Attribution` + `resolve` |
| `backend/app/core/capture.py` | NO EXISTE (legacy: app.py :333-385 `_manejar_bot`) | nuevo: cola + `run_capture` + `process_incoming` + `reset` |
| `backend/app/core/telegram.py` | `connect` :45-72 (`catch_up=True` :59), `_resolve_target` :74-86, sin handlers | + `register_capture` + bridge NewMessage/MessageEdited + `_target_id` |
| `backend/app/core/send_worker.py` | nota "NOTE for Story 3.1" :28-31; `_boot_recovery` confirm :615-626 (`set_message_id` sin intent) | nota cobrada; + `record_intent` en el confirm (diferido 2-5 :616) |
| `backend/app/main.py` | lifespan :32-42 (gateway + worker) | + `register_capture` antes de `connect` + `capture_task` |
| `backend/app/api/batches.py` | new-batch :101-147 (sin sesión), fallback IntegrityError :118-127 | + `resolve_for_batch` + `batch.capture_session_id` en la misma transacción |
| `backend/app/services/batches.py` | `snapshot` :91-134 (`cc_new` 0 literal, ":93-95 hardcoded until Epic 3") | `cc_new` real (sesión activa) en ambas ramas |
| `backend/tests/conftest.py` | `reset_scheduler` autouse :153-163; fixtures :104-225 | + autouse `reset_capture` |
| `backend/tests/test_attribution.py` | NO EXISTE (nombrado por architecture) | nuevo (Task 9) |
| `backend/tests/test_batches.py` / `test_batch_controls.py` | asserts `cc_new == 0` :405/:429 y :506 | solo comentarios (siguen verdes) |
| `deferred-work.md` | 2-5 :28 y :616 abiertos | housekeeping Task 10 |

**Sin cambios:** `core/scheduler.py`, `core/broadcaster.py` (su `emit` tenant-scoped :34-36 es todo lo que la captura necesita), `api/ws.py` (el snapshot solo cambia de valor), `api/admin.py`/`api/gates.py`/`api/auth.py`, `errors.py` (sin endpoints nuevos), `app/config.py`, `deploy/*`, TODO `frontend/` (ws.ts :218-219 ignora eventos desconocidos — verificado). El índice `uq_batches_one_live_per_tenant` no se toca.

### Cumplimiento de arquitectura (no negociable)

- Tablas `capture_sessions` y `responses` con esos nombres LITERALES; columnas snake_case, FKs `<singular>_id`, índices `ix_`/`uq_`, timestamps `timestamptz`. Migración Alembic a mano + espejo en modelos (idiom `ede0d6d7b7c6`). [Source: architecture.md#Database-Naming-Conventions; #Enforcement-Guidelines]
- El flujo es EXACTAMENTE el data-flow de architecture: "bot reply → capture matches `reply_to_msg_id` → `responses` row + CC extract/dedup → broadcaster emits `response.captured` to that tenant's sockets". Frontera de componentes: capture → attribution (lookup) → repos (save) → broadcaster. Módulos `core/capture.py`, `core/attribution.py`, `core/cc_extract.py` y test `test_attribution.py` nombrados por el árbol del repo. [Source: architecture.md#Data-flow, #Component-Boundaries, #Proposed-Source-Tree]
- Telethon confinado a `core/telegram.py` — los handlers viven ahí y cruzan la frontera como dataclass plano; `capture.py` no importa telethon. [Source: architecture.md#Architectural-Boundaries]
- Evento `response.captured` (nombre literal de la lista de eventos), envelope `{"event","data"}`, tenant-scoped vía broadcaster — jamás un emit global. `tenant_id` jamás de un request: la captura corre fuera de requests y lo deriva de `send_log`; el binding usa `user.tenant_id` de la sesión. [Source: architecture.md#Communication-Patterns, #Tenant-Scoping]
- Identificadores en inglés; "sesión de guardado" → `capture_session` (evita el choque con auth session — traducción mandada por architecture). Copy de UI no aplica (sin UI aquí). [Source: architecture.md#Code-Naming-Conventions]
- Unmatched-reply bucket = log estructurado + contador (observabilidad del ban-guardrail, assumption A1). [Source: architecture.md#Risk-Deep-Dive, #Logging]

### Inteligencia de stories previas (2.5 + 2.4 + 2.2)

- **El terreno ya está preparado a propósito — esta story COBRA promesas:** `ix_send_log_message_id` existe "the hot attribution lookup of Story 3.1" (models.py :282); `send_log` "read by capture/attribution (3.1)" (:268); el docstring del worker (:28-31) reserva el buffer de replies para 3.1; `catch_up=True` puesto en 2.2 (telegram.py :59); `cc_new` hardcodeado "until Epic 3" (services/batches.py :93-95); 2.5 dejó las líneas `'sent'` de lotes cancelados intactas "Story 3.1 attributes their replies even on a cancelled batch" (send_worker :389). Borrar/actualizar esos comentarios al cumplirlos.
- **Lecciones de reviews previos:** eventos verificados monkeypatcheando `broadcaster.emit` con lista grabadora (jamás sockets de test — 2.2); objetos expirados tras commit/rollback → capturar atributos antes de cerrar la sesión (MissingGreenlet, 2.3); estado module-level SIEMPRE con `reset()` + fixture autouse o contamina la suite (governor 2.4, ventana flood 2.5 — aquí: `_queue` y `_unmatched_total`); sesiones cortas propias vía `async_session_factory`, jamás la request-scoped (patrón send_worker).
- **`FakeGateway` (conftest :30-67) ya devuelve message ids incrementales** — un `step()` real puebla `send_log` con ids predecibles para los tests de atribución; no necesita extensión para esta story (la captura no llama al gateway).
- **Semántica legacy — qué se conserva y qué muere a propósito:** se conserva la state machine ✅/❌/⏳ con dedup por texto y la paridad de emisión exacta (incl. "ok→ok sin CC no emite"); se conserva "counters never reset" (cc_new acumula entre lotes); **muere** el guardar-todo-sin-atribución (multi-tenant lo prohíbe — bucket), **muere** el dict en memoria (estado durable en `responses`) y **muere** el filtrado por `destinos_ids` mutable (un solo target fijo de settings, filtrado en el bridge).
- **1.7/CI:** Conventional Commits con scope, rama `story/3.1-captura-atribucion`; push a main = deploy automático al VPS (la migración corre en `deploy.sh` vía `alembic upgrade head`). Sin claves de entorno nuevas.

### Estándares de testing

- `pytest` + `pytest-asyncio` (`loop_scope="session"`) + httpx `ASGITransport` contra la app real y el Postgres de dev; self-seed/self-clean; sin mocks de DB; un comportamiento por test. ASGITransport NO corre el lifespan → los tests llaman `capture.process_incoming` / `send_worker.step()` / `_boot_recovery` directamente.
- `caplog` para `event=unmatched_reply` / `event=db_unreachable phase=capture` — asertar substrings, no formatos exactos. Tests con tiempo: retry seconds a 0.0 + `asyncio.wait_for` como cota (idiom test_batches.py).
- Frontend: sin framework de tests (decisión diferida) y esta story no lo toca — `tsc --noEmit` como humo, nada más.

### Notas de estructura del proyecto

- **Nuevos:** `backend/app/core/{capture,attribution,cc_extract}.py`, `backend/app/db/repos/{capture_sessions,responses}.py`, `backend/tests/test_attribution.py`, una migración — todos nombrados por el árbol de architecture (a diferencia de 2.5, aquí el árbol SÍ los nombra).
- **Modificados:** `backend/app/db/models.py`, `backend/app/db/repos/send_log.py`, `backend/app/core/{telegram,send_worker}.py`, `backend/app/main.py`, `backend/app/api/batches.py`, `backend/app/services/batches.py`, `backend/tests/{conftest,test_batches,test_batch_controls}.py`, `deferred-work.md`.
- Legacy `core.py`/`app.py`/`auto_sender.py` congelados en la raíz — solo referencia de port (las anclas legacy de arriba). **🔒 JAMÁS leer contenido bajo `respuestas/`. JAMÁS tocar `.env` ni `anon.session`.**

### Referencias

- [Source: planning-artifacts/epics.md#Story-3.1 — ACs verbatim; #Epic-3 (intro: atribución `reply_to_msg_id` + send_log, vistas en 3.2, historial en 3.3)]
- [Source: planning-artifacts/architecture.md#Data-flow ("bot reply → capture matches reply_to_msg_id → responses row + CC extract/dedup → response.captured"); #Attribution-pipeline ("send worker records (message_id, tenant, batch, line) at dispatch; capture handler resolves reply_to_msg_id; unmatched logged"); #Database-Naming-Conventions (`capture_sessions`, `responses` literales); #Risk-Deep-Dive (fail-stop + buffer de replies, assumption A1 "Bot always replies with reply_to" → bucket + volume test pre-lanzamiento); empirical note ("edits keep the same id" — verificado 2026-06-10)]
- [Source: implementation-artifacts/2-5-endurecimiento-del-pipeline-de-envio.md — send_log write-ahead (lo que esta story LEE), decisión registrada del buffer de replies "se construye en 3.1 DENTRO del pipeline de captura", líneas sent de lotes cancelados conservadas para 3.1]
- [Source: implementation-artifacts/deferred-work.md#Story-2-5-review — LOW :28 (buffer, heredado aquí) y LOW :616 (intent en confirm) absorbidos; MEDIUM telegram.py:111 y los demás SIGUEN diferidos]
- [Source: _bmad-output/project-context.md — 🔒 reglas: nunca leer respuestas/, nunca tocar .env/anon.session; `extraer_cc` trunca en `Status` a propósito; dedup CC es por sesión; handlers web sin chat-filter; counters never reset]
- [Source: código actual @ a9c8df8 — backend/app/{core/{telegram,send_worker,broadcaster}.py, db/{models.py, repos/{send_log,batches}.py}, services/batches.py, api/{batches,ws}.py, main.py}, backend/tests/{conftest,test_batches,test_batch_controls}.py, frontend/lib/ws.ts; legacy core.py :35/:59-66, app.py :102-113/:333-385]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Fable 5) — BMad dev agent, 2026-06-12

### Debug Log References

- Baseline verde confirmada antes de tocar nada: 165 passed (pytest), head Alembic `ede0d6d7b7c6` en el Postgres de dev.
- Migración `2faec0509cb8` aplicada con `alembic upgrade head`; un `alembic revision --autogenerate` posterior dio diff VACÍO (modelos espejados, revisión de chequeo borrada).
- Único fallo durante el desarrollo: dato de test con dos `CC:` en la MISMA línea — `RE_CC` captura hasta fin de línea, así que el segundo valor queda dentro del primero y se trunca en su `Status` (comportamiento legacy correcto). Se corrigió el dato del test (`\n` entre los dos CC), no el port.
- Gates finales: `pytest` 182 passed (165 + 17 nuevos), `ruff check app/ tests/` limpio, `mypy app` limpio, `npx tsc --noEmit` (frontend, humo — sin cambios) limpio.

### Completion Notes List

- Todos los AC 1–9 implementados y cubiertos por tests. La única subtarea NO hecha es la marcada **(HUMAN)** en Task 10: smoke manual en dev con credenciales reales (enviar línea real, esperar reply del bot, verificar fila en `responses` + evento en un tab) — requiere acción del owner; el volume test de atribución es gate pre-lanzamiento de architecture, no de esta story. **No se corrió nada contra producción.**
- Binding AC 3: `resolve_for_batch` + `batch.capture_session_id = cs.id` dentro de la MISMA transacción del new-batch en `POST /api/batches`; el fallback de `IntegrityError` existente cubre también el índice parcial de sesiones (documentado en el comment). Rama APPEND intacta; `BatchOut` sin cambios (cero OpenAPI nuevo).
- Pipeline de captura: `IncomingReply` (dataclass plano) → `enqueue` (bridge síncrono) → `_queue` → `run_capture` (consumidor único, retry-forever con `event=db_unreachable phase=capture`) → `process_incoming` (port de `_manejar_bot` con estado per-message_id durable vía `last_full_revision`). Paridad de emisión legacy exacta; las revisiones `'rejected'` SÍ se persisten (desviación consciente registrada — store de la vista Completa de 3.2). Emisión SIEMPRE post-commit, atributos capturados antes de cerrar la sesión (lección MissingGreenlet).
- Atribución: orden (1) fila previa en `responses` (las ediciones conservan atribución sin `send_log`, probado con `reply_to_msg_id=None` en el edit), (2) `send_log.get_by_message_id` → batch → sesión ligada, con backfill para lotes pre-3.1 (`capture_session_id` NULL), (3) `None` → bucket `event=unmatched_reply` + `_unmatched_total` (contador de proceso, semilla de 4.3) y NO se guarda nada.
- Telethon sigue confinado a `core/telegram.py`: handlers `NewMessage`/`MessageEdited` registrados UNA vez en `connect()` (solo si autorizado), SIN filtro `chats=` — el filtrado (`event.out`, `chat_id != _target_id`) vive en el cuerpo; `_target_id` se captura en `_resolve_target` vía `get_peer_id`. `register_capture(capture.enqueue)` se instala en el lifespan ANTES de `connect()`; `capture_task` se cancela en shutdown como el worker.
- Diferidos 2-5 absorbidos y marcados RESOLVED en deferred-work.md: `:28` (el buffer ES la cola+consumidor) y `:616` (`record_intent` idempotente antes de `set_message_id` en el confirm de `_boot_recovery`). Los demás diferidos de 2-5 NO se tocaron (cerco respetado).
- `cc_new` real en el snapshot (ambas ramas, helper `_cc_new`): conteo CC de la sesión activa del tenant; no se resetea entre lotes (paridad legacy "counters never reset"). Los asserts existentes `cc_new == 0` siguen verdes (esos tests no capturan); solo se actualizaron comentarios.
- `frontend/` no se tocó (decisión registrada de la story: `response.captured` lo consume 3.2; el reducer ignora eventos desconocidos; `cc_new` ya viaja en el snapshot). `tsc --noEmit` corrido como humo: limpio.
- Fixture autouse `reset_capture` agregado a conftest (espejo de `reset_scheduler`) — `_queue`/`_unmatched_total` no contaminan la suite.
- No se hizo commit ni push (instrucción del workflow).

### File List

**Nuevos:**
- `backend/migrations/versions/2faec0509cb8_capture_sessions_and_responses.py`
- `backend/app/db/repos/capture_sessions.py`
- `backend/app/db/repos/responses.py`
- `backend/app/core/cc_extract.py`
- `backend/app/core/attribution.py`
- `backend/app/core/capture.py`
- `backend/tests/test_attribution.py`

**Modificados:**
- `backend/app/db/models.py` (CaptureSession + Response + `Batch.capture_session_id` + docstrings)
- `backend/app/db/repos/send_log.py` (`get_by_message_id` + docstring)
- `backend/app/core/telegram.py` (`register_capture` + bridge handlers + `_target_id`)
- `backend/app/core/send_worker.py` (nota del buffer cobrada; `record_intent` en el confirm de `_boot_recovery`)
- `backend/app/main.py` (lifespan: register_capture antes de connect + `capture_task`)
- `backend/app/api/batches.py` (binding de sesión en la transacción del new-batch)
- `backend/app/services/batches.py` (`_cc_new` + snapshot real en ambas ramas)
- `backend/tests/conftest.py` (fixture autouse `reset_capture`)
- `backend/tests/test_batches.py` (solo comentario del assert `cc_new`)
- `backend/tests/test_batch_controls.py` (solo comentario del assert `cc_new`)
- `_bmad-output/implementation-artifacts/deferred-work.md` (2-5 `:28` y `:616` → RESOLVED)
- `_bmad-output/implementation-artifacts/3-1-captura-y-atribucion-de-respuestas-del-bot.md` (este archivo)
