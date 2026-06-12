---
baseline_commit: a486ab2155dfac48a6cbe682bebd2676aa45b5ab
---

# Story 3.4: Continuar una sesión con dedup preservado

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGÍA (decisión del owner 2026-06-11):** el término de producto para un prefijo es **"gate"** — DB, API, identificadores de código y todo el copy de UI (masculino: "el gate"). epics.md / architecture.md / docs de UX son anteriores al renombre y todavía dicen "prefijo/prefixes" — lee cada "prefijo" como "gate"; donde haya conflicto, gana "gate". En esta story: el "dedup set preloaded from the session's existing `responses` rows" del AC 2 se lee contra el modelo ACTUAL — el dedup de CC ya es DB-backed por `capture_session_id` (no un set en memoria); ver Dev Notes. "Continuar", "En curso", "Cerrada", "Completa", "Filtrada" son términos de producto verbatim y se quedan tal cual.

> **⚠️ DIFERIDOS: esta story NO absorbe ninguno.** Regla de siempre revisada contra deferred-work.md: ningún hallazgo ABIERTO vive en los archivos que esta story toca. En particular, 2-2 MEDIUM `api/batches.py:121` (append race) sigue diferido — esta story NO toca `api/batches.py` (solo LLAMA a `batches_repo.get_live_batch` desde el router de sesiones, igual que hizo 3.3); 2-3 MEDIUM `ws.py:54` (auth de socket abierto), 2-5 MEDIUM `telegram.py:111` y LOW `send_worker.py:398`/`:652`, 2-1 LOW `admin.py:466` y el pase epic-wide de generated types SIGUEN diferidos. No arreglar nada "de paso".

## Story

As a client,
I want to continue a previous session,
So that re-sent data doesn't duplicate my filtered results.

## Acceptance Criteria

1. **Given** a closed session in Historial, **when** the client taps Continuar, **then** the session reopens as the active capture session, a `session.active` event fires, and Envío binds to it.
2. **Given** the reopened session, **when** new batches send and replies arrive, **then** the dedup set was preloaded from the session's existing `responses` rows — previously captured CC lines do NOT reappear in Filtrada; only genuinely new data lands, highlighted, **and** new sends append to the same session.
3. **Given** a live batch in progress, **when** the client tries to continue another session, **then** the request is rejected with an error code and the UI shows "Termina o detén el lote actual antes de continuar otra sesión."

## Tasks / Subtasks

### Backend (Tareas 1–4)

- [x] Task 1: `backend/app/db/repos/capture_sessions.py` — `activate` (AC: 1)
  - [x] Nueva función `activate(session, capture_session) -> None` (estilo del módulo intacto: pure ORM, flush not commit, docstring con la decisión): espejo del patrón UPDATE-first de `create_active` (:99-103 — "the UPDATE runs first so the partial unique index never trips on the honest path") pero reactivando una fila EXISTENTE: `update(CaptureSession).where(tenant_id == cs.tenant_id, CaptureSession.is_active, CaptureSession.id != cs.id).values(is_active=False)` y después `capture_session.is_active = True` + `flush()`.
  - [x] **El `id != cs.id` en el UPDATE es load-bearing** (pitfall documentado): si el UPDATE core incluyera al target, en el caso "continuar la sesión YA activa" pondría `is_active=False` en DB mientras la instancia ORM cargada sigue `True` en memoria — la asignación `True → True` no registra cambio, el flush no emite UPDATE y la fila quedaría INACTIVA. Con la exclusión, el camino ya-activa es un no-op limpio (idempotente) y el camino cerrada→activa registra `False → True` y flushea.
- [x] Task 2: `backend/app/errors.py` — códigos nuevos (AC: 3)
  - [x] Bloque `--- Codes this story (3.4) defines ---` (el archivo termina hoy en `session_in_use` :254-261): `batch_live()` → 409, code `batch_live`, message `"Termina o detén el lote actual antes de continuar otra sesión."` — el message ES el copy del AC 3 verbatim (mismo trato que `session_in_use` le dio al AC 6 de 3.3: la UI renderiza `err.message` tal cual; EXPERIENCE.md registra el ASSUMPTION "error by `code`, exact code from backend" — este es el code).
  - [x] `session_conflict()` → 409, code `session_conflict`, message `"No pudimos continuar la sesión. Intenta de nuevo."` (tuteo) — mapeo del IntegrityError de `uq_capture_sessions_one_active_per_tenant` cuando dos continues (o un continue y un arranque de lote) se cruzan; ver Dev Notes "Razas registradas".
- [x] Task 3: `backend/app/services/batches.py` — promover `_active_session_data` a público (AC: 1)
  - [x] Renombrar `_active_session_data` (:103-147) → `active_session_data` y actualizar sus DOS call sites en `snapshot` (:171 y :194). CERO cambios de forma: `{session_id, cc_new, responses_total, responses: [...], cc: [...]}` con filas cap `_SNAPSHOT_ROWS` y totales reales. El handler de continue lo reusa VERBATIM como payload de `session.active` — el evento es exactamente el slice de sesión que el snapshot ya entrega (un tab que se pierde el evento reconcilia con su próximo snapshot sin diferencia de forma).
  - [x] `_SNAPSHOT_ROWS` se queda privado y donde está (los tests existentes lo monkeypatchean — test_attribution.py y test_sessions.py).
- [x] Task 4: `backend/app/api/sessions.py` — `POST /{session_id}/continue` (AC: 1, 2, 3)
  - [x] Acción no-CRUD con verbo sufijo (architecture :196 — idiom `/api/batches/{id}/pause|resume|stop`): `@router.post("/{session_id}/continue", response_model=SessionOut)` → 200 con la sesión reactivada (la UI quiere el flip de `is_active` sin otra ida; `continue` es statement reservado de Python — nombra la función `continue_session`). Import nuevo: `from sqlalchemy.exc import IntegrityError` (idiom api/batches.py :16) y `batch_live`, `session_conflict` de errors.
  - [x] Cuerpo, EN ESTE ORDEN: (1) `target = await _require_session(session, user.tenant_id, session_id, for_update=True)` — el lock del target serializa contra el DELETE de 3.3 (que también toma `for_update=True`, :224-226) y contra otro continue del MISMO target; 404 idéntico para id desconocido / de otro tenant / fuera de int4 (helper :110-130, sin cambios). (2) Guard del AC 3: `live = await batches_repo.get_live_batch(session, user.tenant_id)`; si `live is not None` ⇒ `raise batch_live()` — el guard es por CUALQUIER lote vivo (`LIVE_STATES` = sending|paused|stopping): paridad legacy registrada "nueva/continuar return HTTP 409 while a batch is live or paused (`_lote_vivo`)" (project-context :48). A diferencia del delete de 3.3, NO importa a qué sesión esté ligado el lote — continuar OTRA sesión bajo un lote vivo es exactamente lo que el AC prohíbe (y continuar la del lote vivo es un no-op que no amerita carril especial: 409 igual, el copy aplica).
  - [x] (3) `await capture_sessions_repo.activate(session, target)` + `await session.commit()` envuelto en `try/except IntegrityError` → `rollback()` + `raise session_conflict()` (la red del índice parcial; ver Razas registradas). (4) POST-commit: `payload = await batches_service.active_session_data(session, user.tenant_id)` — SELECTs frescos en la misma sesión de request ven el estado comiteado y el payload ya sale materializado (dicts planos, `created_at.isoformat()` — la lección MissingGreenlet 2.3 ya está resuelta dentro del helper); `await broadcaster.emit(user.tenant_id, "session.active", payload)` (import `broadcaster` — primero de este router). (5) `return _session_out(target)` — `expire_on_commit=False` (db/base.py :37) mantiene los atributos válidos post-commit.
  - [x] Idempotencia registrada: continuar la sesión YA activa (sin lote vivo) ⇒ 200 + emit — `activate` es no-op limpio (Task 1) y el `session.active` extra es reconciliación barata multi-tab. El botón de la UI no se ofrece en "En curso" (Task 6), pero el server lo tolera (otro tab pudo activarla en medio).

### Frontend (Tareas 5–6)

- [x] Task 5: `frontend/lib/ws.ts` — reducer `session.active` (AC: 1)
  - [x] Interface local `SessionActiveData` espejo EXACTO del payload (`{session_id: number; cc_new: number; responses_total: number; responses: SnapshotResponseRow[]; cc: SnapshotCcRow[]}` — reusa `SnapshotResponseRow`/`SnapshotCcRow` :75-86, son la misma forma on-wire).
  - [x] Nuevo `case "session.active"` en `reduce` (:200): REEMPLAZA solo los campos de sesión — `sessionId: d.session_id`, `responses`/`cc` mapeados con el MISMO mapper del snapshot (keys `s-${row.id}`, `capturedAt: row.created_at`, `nueva: false` — el evento es reconciliación, no novedad; el highlight "nueva" queda reservado a `response.captured`), `ccNew: d.cc_new`, `responsesTotal: d.responses_total`. JAMÁS toca `state`/batch/flood (mismo contrato que `response.captured`: la sesión es de la sesión, el lote es del lote). Reemplazo incondicional — el server dice cuál es la sesión activa AHORA; esto es lo que hace que "Envío binds to it" funcione en TODOS los tabs del tenant, incluido el que disparó el continue.
  - [x] Actualizar el comentario de `clearSession` (:487 — "NO new WS event — `session.active` belongs to Story 3.4"): el evento ya existe; `clearSession` se queda como está (el delete sigue siendo seed local, no evento).
  - [x] CERO cambios en `snapshot`/`seedFromBatch`/`IDLE` y cero eventos más: `session.active` solo lo emite el continue — el arranque de lote sigue propagando su binding vía `batch.state.session_id` (3.2, intacto).
- [x] Task 6: Historial — botón Continuar en lista y detalle (AC: 1, 3)
  - [x] `frontend/app/(client)/sessions/page.tsx`, `SessionRow`: botón `Continuar` (variant `secondary`, size `sm` — NO danger: no es destructivo) PRIMERO en el grupo de acciones (:304-352, junto a Renombrar/Eliminar), renderizado SOLO cuando `!session.is_active` (el AC dice "a closed session"; la activa ya es el destino de captura — no ofrecer un no-op). `useMutation` → `api.post<SessionOut>(\`/api/sessions/\${session.id}/continue\`)` (api.post acepta llamadas sin body, lib/api.ts). Pending: `"Continuando…"`. `onSuccess`: `invalidate()` (el helper :199-206 ya invalida `["sessions"]` + `["session", id]`) **más** `queryClient.invalidateQueries({queryKey: ["session"]})` — la sesión que ERA activa también cambió de badge y su detalle cacheado quedaría stale; la invalidación por prefijo cubre ambas. NO hay seed local: el `session.active` llega por el socket propio del tab — el WS es quien rebinda Envío (decisión registrada). `onError`: `session_not_found` ⇒ borrada en otro tab — `invalidate()` y listo (idiom del delete :243-251); cualquier otro `ApiError` ⇒ `err.message` inline (para `batch_live` ES el copy del AC 3 verbatim) en un estado `continueError` propio, mismo `<span className="text-sm text-danger">` que `renameError` (:355-357); fallback no-ApiError: `"No pudimos conectar. Intenta de nuevo."`.
  - [x] Actualizar el comentario de cabecera (:7): "Continuar is Story 3.4" ya no es futuro — déjalo descrito como hecho (el export `↓ .txt` sigue siendo 3.5).
  - [x] `frontend/app/(client)/sessions/[id]/page.tsx`: el MISMO botón en el header (:192-202, junto al badge — Flow 2 de EXPERIENCE lo tapea desde el detalle), solo cuando `!data.is_active`; misma mutación; `onSuccess`: invalidar `["session", String(sessionId)]` y `["sessions"]`; error en `<span className="text-sm text-danger">` bajo el header (o `Alert status="danger"` — elige UNO, sin modal: no es destructivo). Sinergia gratis a verificar: al refetchear, `is_active` flipea el badge a "En curso", y como `session.active` puso `live.sessionId === id`, el efecto de live-follow (:124-130) empieza a seguir la sesión continuada POR CONSTRUCCIÓN — no toques el efecto.
  - [x] La duplicación de la mutación entre lista y detalle (≈15 líneas) se acepta y comenta en ambos lados — mismo precedente registrado de 3.3 con `fallbackName`/`SessionBadge`: App Router no permite exports extra en `page.tsx` y la story no autoriza módulos compartidos nuevos.
  - [x] SIN preselección del gate en Envío y SIN tocar `send-form.tsx` / `(client)/page.tsx` (decisión registrada — ver Dev Notes). SIN export `↓ .txt` (3.5). SIN edición de contenido (FR19).

### Tests + gates (Tareas 7–8)

- [x] Task 7: ampliar `backend/tests/test_sessions.py` (AC: todos los del lado servidor)
  - [x] Reusar los helpers locales existentes (`_post_batch` :39, `_drain` :46, `_get_session_row` :59, `_response_rows` :66, `_bound_session_id` :76, `_capture_ok` :82, `_create_other_gate` :90) y las fixtures de conftest (`ctx`/`gate`/`client_user`/`fake_gateway`) — no inventar otros. Añadir la fixture local `events` (recorder que monkeypatchea `broadcaster.emit`/`emit_global` — idiom EXACTO de test_batch_controls.py :35-46; no existe en este archivo todavía). Constante local `LIVE_BODY = {"code": "batch_live", "message": "Termina o detén el lote actual antes de continuar otra sesión."}` junto a `NOT_FOUND_BODY` (:30).
  - [x] **Continuar reactiva y emite (AC 1):** lote gate A (drenar) + captura ✅ con un `CC:` → sesión SA; lote gate B (`_create_other_gate`, drenar) → SB activa, SA inactiva. `POST /api/sessions/{SA}/continue` ⇒ 200 con `is_active: True`; `GET /api/sessions` ⇒ SA `True`, SB `False` (activación por reemplazo — exactamente UNA activa); la última emisión es `(tenant_id, "session.active", payload)` con `payload["session_id"] == SA`, `cc_new == 1`, y las filas previas presentes con forma exacta (dicts completos — lección: asserts exactos, no `in`).
  - [x] **Dedup preservado + append a la misma sesión (AC 2 — EL test de la story):** tras continuar SA, `POST /api/batches` con gate A ⇒ `_bound_session_id(batch) == SA` (`resolve_for_batch` reusó la activa: "new sends append to the same session"); drenar; reply ✅ que repite el MISMO valor `CC:` de la primera captura ⇒ `GET /api/sessions/{SA}` con `cc_total` AÚN 1 y una sola fila 'cc' (el dedup vino de las filas existentes — `add_new_cc` + `uq_responses_session_cc`); reply ✅ con un valor NUEVO ⇒ `cc_total` 2, orden de inserción preservado, y la fila 'full' nueva sí presente en `responses` (Completa crece, Filtrada no repite).
  - [x] **Guard de lote vivo (AC 3):** con un lote SIN drenar (sending), `POST /{otra}/continue` ⇒ 409 body exacto `LIVE_BODY`, la sesión activa NO cambió y NO se emitió `session.active`; pausar el lote (`POST /api/batches/{id}/pause`) ⇒ continue sigue 409 (paridad `_lote_vivo`: live O paused); tras `POST /{id}/stop` ⇒ el MISMO continue ⇒ 200.
  - [x] **404 nunca filtra existencia:** id desconocido, id de OTRO tenant e id > int4 ⇒ 404 `NOT_FOUND_BODY` idénticos para el verbo nuevo (extiende el trío de 3.3 al POST).
  - [x] **Idempotencia:** continuar la sesión YA activa (superficie idle) ⇒ 200, sigue `is_active: True`, la lista mantiene exactamente UNA activa, y el `session.active` se emitió igual (reconcile barato — decisión registrada).
  - [x] Suite COMPLETA verde (baseline al cierre de 3.3: **202 passed** — verificado contra el baseline real antes de tocar nada; al cierre: **206 passed**).
- [x] Task 8: gates de verificación (todos los AC)
  - [x] Backend: `ruff check .`, `mypy app`, `pytest` — verde completo (ruff: All checks passed; mypy: no issues in 37 files; pytest: 206 passed).
  - [x] Frontend: `npx tsc --noEmit` + `npm run lint` + `npm run build` — los tres verdes; sin framework de tests (decisión diferida del proyecto; NO inventar jest/vitest).
  - [x] deferred-work.md NO se toca (nada absorbido, nada nuevo salvo que el review lo diga).
  - [ ] (HUMAN — necesita credenciales reales) Smoke manual en dev: lote real con gate A + capturas → segundo lote con gate B → en Historial, Continuar la sesión de A; ver el badge moverse y Envío mostrar las filas viejas de A; reenviar datos repetidos del MISMO gate y verificar que Filtrada NO los repite y lo nuevo aterriza resaltado; intentar Continuar con un lote vivo y leer el error verbatim. **No correr contra producción sin el OK de Richard.**

## Dev Notes

### Qué NO es esta story (cerco de alcance)

- **Export `.txt`** (`↓ .txt`, `services/exports.py`, la mitad export de api/sessions.py) → **Story 3.5**. Sin botones muertos.
- **Vista de soporte cross-tenant** (`/admin/tenants/[id]`, `for_tenant(id)`, audit log) → **Story 3.6**. Todo aquí es del propio tenant.
- **SIN preselección del gate en Envío** (decisión registrada): "Envío binds to it" = el store WS rebinda los paneles vía `session.active` — el selector de gate NO se auto-rellena. El dedup es server-side y gate-keyed: un envío del MISMO gate reúsa la sesión continuada (`resolve_for_batch` :115-127, SIN CAMBIOS — exactamente la semántica que el AC necesita), uno de OTRO gate rota la sesión activa como siempre lo hizo. El legacy sí restauraba el prefijo en el input (con el warning "Verificá el prefijo"), pero en el modelo nuevo el selector es de catálogo dos-pasos y la sesión la resuelve el servidor — auto-tocar `categoryKey`/`gateKey` de `send-form.tsx` desde el store es acoplamiento que ninguna AC pide. Si el operador elige otro gate, no hay colisión de dedup posible (el dedup es por sesión y la sesión es por gate).
- **CERO migraciones y cero código de dedup nuevo:** el modelo de 3.1 ya dejó esto pagado — ver la decisión siguiente.
- **CERO settings nuevos** (regla 2.5), **cero cambios de snapshot** (la forma del slice no cambia, solo se hace público el builder), `main.py` SIN CAMBIOS (el router de sesiones ya está registrado desde 3.3).

### Diseño (decisiones registradas)

- **El "dedup set preloaded" del AC ya es un hecho del modelo de datos, no código nuevo.** El AC (y EXPERIENCE Flow 2) describe el mecanismo del legacy (`continuar=True` → `cargar_cc_existentes()` precargaba un set en memoria desde `filtrada.txt`). En el modelo actual el dedup de CC es **DB-backed por `capture_session_id`**: `responses_repo.add_new_cc` (:81-136) SELECTea los 'cc' existentes de la sesión antes de insertar, con `uq_responses_session_cc` (models.py :380-386) como red — el docstring de `Response` (:366-368) registra la promesa literal: "Story 3.3's 'continuar' preloads the dedup set from these rows" (la numeración era pre-split; la story es esta). Por lo tanto "continuar" = **reactivar la sesión** para que el próximo lote del mismo gate se ligue a ella (`resolve_for_batch` reúsa la activa con gate igual) — y el dedup contra TODO lo ya capturado cae solo. El test del AC 2 prueba la cadena completa, no un helper.
- **Ruta `POST /api/sessions/{id}/continue`:** acción no-CRUD con verbo sufijo — idiom literal de architecture (":196 Actions that aren't CRUD: POST verb suffix"). Devuelve 200 + `SessionOut` (no 204 como pause/stop: la UI consume el flip de `is_active`). Función `continue_session` (`continue` es palabra reservada).
- **Guard por LOTE VIVO del tenant, sin importar a qué sesión esté ligado:** `get_live_batch` (LIVE_STATES = sending|paused|stopping) — paridad legacy "nueva/continuar 409 while live or paused" (project-context :48). Nota la diferencia con el delete de 3.3, cuyo guard exigía `live.capture_session_id == target.id`: aquí el AC prohíbe continuar BAJO un lote vivo, punto.
- **Activación por reemplazo, UPDATE-first, con exclusión del target:** espejo de `create_active` (la danza que nunca dispara `uq_capture_sessions_one_active_per_tenant` en el camino honesto) + el pitfall de idempotencia documentado en Task 1. No hay "cerrar" sesiones — siguen reemplazándose por reasignación (legacy).
- **`session.active` = el slice del snapshot, emitido tras el commit:** payload EXACTO de `active_session_data` (`{session_id, cc_new, responses_total, responses, cc}`, filas cap `_SNAPSHOT_ROWS`, totales reales). Un tab que se pierda el evento reconcilia con su próximo snapshot sin diferencia de forma; el reducer lo trata como reconciliación (`nueva: false`) — el highlight del AC 2 ("only genuinely new data lands, highlighted") lo sigue poniendo `response.captured`, que ya viaja con `new_cc` y el guard de sesión del store deja pasar las filas de la sesión continuada porque `store.sessionId` ES la continuada.
- **Sin seed local en el tab que continúa:** el broadcaster emite a TODOS los sockets del tenant, incluido el propio — para cuando el operador navega de Historial a Envío, el store ya rebindó. `seedFromBatch` existió porque el ring debía aparecer sin esperar al WS *estando ya en Envío*; aquí no aplica. Historial se actualiza por invalidación react-query (`["sessions"]` + prefijo `["session"]` — la ex-activa también cambió de badge).
- **`ccNew` tras continuar = el total histórico de la sesión** (`cc_count`): los contadores son de la SESIÓN y nunca se resetean (decisión 3.1/3.2, "counters never reset"). El badge FILTRADA mostrará p.ej. 50 al continuar una sesión con 50 CC previos — honesto: es la misma `filtrada.txt` extendida del legacy (Flow 2, paso 5).
- **Razas registradas:** (a) dos continues concurrentes (dos tabs, targets distintos) o un continue cruzado con el `create_active` de un `POST /api/batches` pueden disparar `uq_capture_sessions_one_active_per_tenant` en el commit → `IntegrityError` → rollback → **409 `session_conflict`** ("Intenta de nuevo.") — la red es el índice, el mapeo evita el 500 que rompería el contrato `{code, message}`; (b) la ventana continue↔arranque-de-lote donde el POST comitea ENTRE el guard y el commit del continue deja un lote vivo ligado a una sesión recién desactivada — **aceptada a escala MVP**: la atribución es por binding del lote (no por flag activa), nada se pierde, las filas quedan visibles en Historial y el próximo snapshot reconcilia los paneles (no hay fila común que lockear entre "no existe lote vivo" y el INSERT del lote — cerrarla exigiría un lock a nivel tenant que ninguna otra ruta paga); (c) continue↔delete del MISMO target serializa por el `FOR UPDATE` que ambos toman (`_require_session(for_update=True)`), en cualquier orden el resultado es consistente (404 o sesión activa).
- **Botón Continuar solo en sesiones "Cerrada"** (`!is_active`): el AC dice "a closed session"; ofrecer continuar la activa es un no-op. El server igual lo tolera idempotente (multi-tab). El botón vive en la fila del Historial Y en el header del detalle (EXPERIENCE :70 lo lista como acción de fila; Flow 2 :154 lo tapea desde el detalle) — misma mutación duplicada con comentario, precedente 3.3.
- **La UI no pre-deshabilita Continuar con lote vivo:** la lista del Historial es superficie REST-only (no consume el store WS — 3.3 lo dejó así a propósito); el rechazo 409 con el copy verbatim ES la UX especificada (EXPERIENCE :158, failure path). No meter `useLiveBatch` en la lista solo para esto.

### Código actual que vas a tocar (estado HOY @ a486ab2, con anclas)

| Archivo | Hoy | Esta story |
| --- | --- | --- |
| `backend/app/db/repos/capture_sessions.py` | `get_active` :19, `get_for_tenant` :50 (knob `for_update` :72), `create_active` :90 (UPDATE-first :99-103), `resolve_for_batch` :115 (reúsa activa si el gate coincide) | + `activate(session, capture_session)` |
| `backend/app/errors.py` | termina en `session_in_use` :254-261 | + bloque 3.4: `batch_live`, `session_conflict` |
| `backend/app/services/batches.py` | `_active_session_data` :103-147 (privado), call sites en `snapshot` :171/:194, `_SNAPSHOT_ROWS` :21 | rename a público `active_session_data` (misma forma) |
| `backend/app/api/sessions.py` | router :29, `_require_session` :110-130, `_session_out` :99, delete con `for_update=True` :224-226 (el precedente de lock), import de `batches_repo` ya presente :24 | + `POST /{session_id}/continue` (+ imports `IntegrityError`, `broadcaster`, errors nuevos) |
| `backend/app/db/repos/batches.py` | `get_live_batch` :48-73 (LIVE_STATES, tenant-scoped) | SIN CAMBIOS (solo se llama) |
| `backend/app/db/models.py` | `CaptureSession` :313-352 (índice parcial :329-334), `Response` :355-411 (`uq_responses_session_cc` :380-386, promesa "continuar" :366-368) | SIN CAMBIOS (CERO migraciones) |
| `backend/app/db/repos/responses.py` | `add_new_cc` :81-136 (EL dedup), `cc_count` :139 | SIN CAMBIOS (el dedup ya está pagado) |
| `backend/app/core/capture.py` | `process_incoming` :222 (add_new_cc :298-306, emit `response.captured` :327-342) | SIN CAMBIOS |
| `backend/app/api/batches.py` | binding `resolve_for_batch` :122-126, fallback IntegrityError :127-141 | SIN CAMBIOS (el diferido 2-2 :121 sigue diferido) |
| `frontend/lib/ws.ts` | `reduce` switch :200, mapper del snapshot :223-236, guard de `response.captured` :337, `clearSession` :488 (comentario :487 a actualizar), `SnapshotResponseRow`/`SnapshotCcRow` :75-86 | + `SessionActiveData` + case `session.active` |
| `frontend/app/(client)/sessions/page.tsx` | comentario :7 ("Continuar is Story 3.4"), `SessionRow` :191, `invalidate` :199-206, mutaciones rename/remove :208-261, grupo de acciones :304-352, span de error :355-357 | + botón Continuar + mutación + `continueError` |
| `frontend/app/(client)/sessions/[id]/page.tsx` | header :192-202 (badge :201), live-follow :124-130 (NO tocar), query key `["session", String(sessionId)]` :109 | + botón Continuar en header + mutación |
| `frontend/lib/api.ts` | `api.post` acepta body opcional :86-90 | SIN CAMBIOS (solo se consume) |
| `backend/tests/test_sessions.py` | helpers :39-104, `NOT_FOUND_BODY` :30, 6 tests 3.3 | + fixture `events` + tests 3.4 (Task 7) |
| `backend/tests/test_batch_controls.py` | fixture `events` :35-46 (recorder de emisiones — el idiom a copiar) | SIN CAMBIOS (solo plantilla) |

**Sin cambios:** `core/{capture,attribution,cc_extract,scheduler,send_worker,telegram,broadcaster}.py`, `api/{ws,batches,admin,gates,auth,health}.py`, `db/models.py`, `main.py`, `config.py`, `deploy/*`, `frontend/components/batch/*` (incl. `send-form.tsx`), `frontend/components/sessions/*`, `frontend/components/client-nav.tsx`, `frontend/app/(client)/page.tsx`, `frontend/types/api.ts` (NO regenerar — diferido 2-1 vigente), legacy `core.py`/`app.py`/`static/`.

### Cumplimiento de arquitectura (no negociable)

- **Evento `session.active`:** nombrado LITERAL en la lista de eventos WS de architecture ("`batch.progress`, …, `session.active`, …"); envelope estándar `{"event", "data"}`, emisión SOLO vía broadcaster, tenant-scoped, server→client. Era el único evento de la lista aún sin implementar — esta story lo estrena (el cerco de 3.2 y 3.3 lo reservó aquí explícitamente). [Source: architecture.md#Communication-Patterns]
- **REST:** acción no-CRUD = POST con verbo sufijo (`/api/sessions/{id}/continue`); errores SIEMPRE `{code, message}` con status con sentido (404/409); jamás un raw 500 — de ahí el mapeo del IntegrityError. [Source: architecture.md#API-&-Communication-Patterns; #Error-contract]
- **Tenant scoping:** `tenant_id` SOLO de `user.tenant_id`; lookup tenant-scoped con 404 que no filtra existencia (el trío de ids malos también para el verbo nuevo). [Source: architecture.md#Tenant-Scoping; #Enforcement-Guidelines]
- **Identificadores en inglés, copy en español tuteo:** `continue_session`/`activate`/`batch_live` en código; copy verbatim: "Continuar", "Continuando…", "Termina o detén el lote actual antes de continuar otra sesión.", "No pudimos continuar la sesión. Intenta de nuevo." [Source: architecture.md#Code-Naming-Conventions; epics.md#Story-3.4]
- **UX spines:** Continuar NO es destructivo — variant secondary, sin modal, sin confirmación (Detener-instant/confirm-solo-Eliminar es decisión triada del UX); el error 409 se muestra por `err.message` verbatim (ASSUMPTION registrado: "error by code, exact code from backend" — el code es `batch_live`). [Source: EXPERIENCE.md :70, :150-158; .decision-log.md]

### Inteligencia de stories previas (3.3 + 3.2 + 3.1)

- **Esta story COBRA tres promesas explícitas:** el comentario de `clearSession` ("`session.active` belongs to Story 3.4", ws.ts :487), el comentario del Historial ("Continuar is Story 3.4", sessions/page.tsx :7), y el docstring de `Response` (la precarga del dedup "from these rows", models.py :366-368). Bórralas/actualízalas al cumplirlas.
- **Lecciones 3.3:** `for_update=True` en `_require_session` para serializar con escrituras concurrentes; los 404 se asertan con body exacto y el trío de ids malos; HeroUI v3 API compound se verifica contra los typings INSTALADOS (aquí solo `Button` — ya usado en la página, sin riesgo); las mutaciones tratan `session_not_found` post-borrado como éxito/refresh; copy de UI string-a-string contra el spec (los reviews comparan verbatim).
- **Lecciones 3.1/3.2:** emisiones DESPUÉS del commit con todo materializado (MissingGreenlet); el guard de sesión del store (`response.captured` :337) es quien hace que tras `session.active` las réplicas nuevas aterricen — no lo dupliques; los totales del store vienen del server (`cc_total`/`cc_new`), nunca sumas client-side.
- **Lecciones 2.x:** `wake()`/controles no se tocan aquí; el patrón "catch IntegrityError → rollback → camino honesto" viene de api/batches.py :127-141 (aquí el camino honesto es un 409, no un append); `npm run lint` antes de declarar verde.
- **Semántica legacy que esta story traduce:** `/api/sesion/continuar` (tomaba el slug, reconstruía `Sesion(..., continuar=True)` que precargaba el dedup de `filtrada.txt`, 409 bajo `_lote_vivo`) → `POST /api/sessions/{id}/continue` + reactivación por flag + dedup DB-backed que ya estaba. Lo que muere a propósito: el warning "Verificá el prefijo" (toda sesión DB tiene snapshots de gate — el caso sin-meta.json no existe en Postgres) y el evento legacy `sesion_activa` del snapshot (su equivalente moderno ya viaja DENTRO del snapshot desde 3.2; el evento puntual nace aquí).
- **1.7/CI:** Conventional Commits con scope (`feat(backend,frontend): …`), rama `story/3.4-continuar-sesion`; push a main = deploy automático al VPS. Sin migraciones, sin claves de entorno nuevas.

### Estándares de testing

- Backend: `pytest` + `pytest-asyncio` (`loop_scope="session"`) + httpx `ASGITransport` contra la app real y el Postgres de dev; self-seed/self-clean (el CASCADE de tenant en `cleanup_users` se lleva `capture_sessions`/`responses`); sin mocks de DB; un comportamiento por test. Capturas DIRECTO a `capture.process_incoming(IncomingReply(...))`; lotes reales vía `POST /api/batches` + `send_worker.step()`; emisiones por la fixture `events` (recorder sobre `broadcaster.emit` — jamás sockets).
- Los 409 y 404 se asertan con body exacto (`LIVE_BODY`, `NOT_FOUND_BODY`); las emisiones con dicts completos.
- Frontend: SIN framework de tests (decisión diferida — no instalar nada). Gates: `npx tsc --noEmit` + `npm run lint` + `npm run build`. La verificación de comportamiento (rebinding de Envío, badges, error verbatim) es el smoke manual de Task 8.

### Notas de estructura del proyecto

- **Nuevos:** nada — cero archivos nuevos (el router, la página de lista y la de detalle ya existen; los tests amplían `test_sessions.py`).
- **Modificados:** `backend/app/db/repos/capture_sessions.py`, `backend/app/errors.py`, `backend/app/services/batches.py`, `backend/app/api/sessions.py`, `backend/tests/test_sessions.py`, `frontend/lib/ws.ts`, `frontend/app/(client)/sessions/page.tsx`, `frontend/app/(client)/sessions/[id]/page.tsx`.
- Legacy `core.py`/`app.py`/`static/` congelados en la raíz — solo referencia de comportamiento. **🔒 JAMÁS leer contenido bajo `respuestas/`. JAMÁS tocar `.env` ni `anon.session`.**

### Referencias

- [Source: planning-artifacts/epics.md#Story-3.4 — los 3 ACs verbatim (incl. el copy del 409); #Story-3.3/#Story-3.5/#Story-3.6 (fronteras: historial ya hecho, export y soporte cross-tenant NO son de aquí)]
- [Source: planning-artifacts/architecture.md#Communication-Patterns (`session.active` en la lista literal de eventos); #API-&-Communication-Patterns (POST verbo-sufijo :196, error contract); #Tenant-Scoping; #Code-Naming-Conventions]
- [Source: ux-designs/ux-cc-2026-06-10/EXPERIENCE.md :70 (Session row: "Continuar (reopens session — dedup set preserved for new sends)"), :150-158 (Flow 2 completo: tap desde el detalle, `session.active`, dedup, failure path con copy verbatim y ASSUMPTION "exact code from backend")]
- [Source: implementation-artifacts/3-3-historial-...md — `_require_session`/`for_update`, trío de 404, idiom de mutaciones e invalidaciones, duplicación aceptada en pages, "Continuar (Story 3.4)" como cerco que esta story cierra]
- [Source: implementation-artifacts/3-2-vistas-...md — el cerco que reservó `session.active` para 3.4; el slice de sesión del snapshot y sus mappers (que el evento reusa)]
- [Source: implementation-artifacts/3-1-captura-...md — dedup DB-backed (`add_new_cc` + `uq_responses_session_cc`), `resolve_for_batch` (reúsa activa por gate), activación por reemplazo, "counters never reset"]
- [Source: implementation-artifacts/deferred-work.md — revisado: NINGÚN hallazgo abierto en los archivos de esta story; 2-2 batches.py:121, 2-3 ws.py:54, 2-5 telegram.py:111 / send_worker.py:398/:652, 2-1 admin.py:466 SIGUEN diferidos]
- [Source: _bmad-output/project-context.md — :48 "nueva/continuar return HTTP 409 while a batch is live or paused (`_lote_vivo`)"; :71 "CC dedup is session-scoped … `continuar=True` preloads the set"; 🔒 reglas respuestas//.env/anon.session]
- [Source: código actual @ a486ab2 — backend/app/{api/{sessions,batches}.py, db/{models.py, base.py, repos/{capture_sessions,responses,batches}.py}, services/batches.py, core/{capture,broadcaster}.py, errors.py}, backend/tests/{test_sessions,test_batch_controls,conftest}.py, frontend/{lib/{ws,api}.ts, app/(client)/{page,sessions/page,sessions/[id]/page}.tsx, components/batch/send-form.tsx}]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Claude Code, BMad dev agent)

### Debug Log References

- Baseline verificado en HEAD = a486ab2 (el commit de la story): `pytest` ⇒ **202 passed** antes de tocar nada.
- Cierre: `pytest` ⇒ **206 passed** (202 + 4 tests nuevos; el trío de 404 se extendió dentro del test existente, no suma archivo ni test).
- `ruff check app/ tests/` ⇒ All checks passed. `mypy app` ⇒ Success: no issues found in 37 source files.
- Frontend: `npx tsc --noEmit` limpio, `npm run lint` limpio, `npm run build` ⇒ build OK (rutas `/sessions` y `/sessions/[id]` compilan).

### Completion Notes List

- Backend implementado EXACTAMENTE como especifica la story: `activate` UPDATE-first con exclusión del target (pitfall de idempotencia documentado en el docstring), códigos `batch_live`/`session_conflict` con copy verbatim, `_active_session_data` → `active_session_data` (forma intacta, `_SNAPSHOT_ROWS` sigue privado), `POST /api/sessions/{session_id}/continue` con el orden lock → guard → activate/commit (IntegrityError → rollback → 409 `session_conflict`) → emit post-commit → `_session_out`.
- El "dedup set preloaded" del AC 2 no generó código nuevo (decisión registrada): el dedup es DB-backed (`add_new_cc` + `uq_responses_session_cc`); el test de AC 2 prueba la cadena completa (continue → nuevo lote del mismo gate ligado a SA → CC repetido NO reaparece → CC nuevo sí, orden preservado).
- Frontend: `SessionActiveData` + `case "session.active"` en el reducer (reemplazo incondicional de SOLO los campos de sesión, `nueva: false`); botón Continuar en fila del Historial (primero del grupo, solo `!is_active`, pending "Continuando…") y en el header del detalle (junto al badge); errores inline `text-sm text-danger` con `err.message` verbatim (`batch_live` ES el copy del AC 3); `session_not_found` ⇒ invalidación silenciosa (idiom delete). Mutación duplicada lista/detalle comentada en ambos lados (precedente 3.3).
- Las tres promesas cobradas: comentario de `clearSession` (ws.ts), cabecera del Historial (sessions/page.tsx :7) y el docstring de `Response` (models.py — solo texto del docstring, CERO cambios de schema/migraciones).
- Sin cambios en: `resolve_for_batch`, `add_new_cc`, `api/batches.py` (diferido 2-2 sigue diferido), `send-form.tsx`, `(client)/page.tsx`, `main.py`, deferred-work.md. Cero migraciones, cero settings, cero archivos nuevos.
- Idempotencia verificada por test: continuar la sesión YA activa ⇒ 200 + la fila SIGUE activa en DB (la exclusión del target en el UPDATE es la que lo garantiza) + `session.active` emitido igual.
- PENDIENTE (HUMAN): smoke manual en dev con credenciales reales (Task 8, último ítem) — no se corre contra producción sin OK de Richard.

### File List

- `backend/app/db/repos/capture_sessions.py` — + `activate()` (UPDATE-first, exclusión del target)
- `backend/app/errors.py` — + bloque 3.4: `batch_live()`, `session_conflict()`
- `backend/app/services/batches.py` — `_active_session_data` → `active_session_data` (público; docstring ampliado; dos call sites en `snapshot`)
- `backend/app/api/sessions.py` — + `POST /{session_id}/continue` (`continue_session`); imports `IntegrityError`/`broadcaster`/errors nuevos/`batches_service`; docstring del módulo actualizado
- `backend/app/db/models.py` — SOLO docstring de `Response` (promesa "continuar" cobrada; sin cambios de schema, sin migración)
- `backend/tests/test_sessions.py` — + fixture `events`, `LIVE_BODY`, 4 tests 3.4, trío de 404 extendido al POST, docstring del módulo
- `frontend/lib/ws.ts` — + `SessionActiveData`, `case "session.active"`, comentario `clearSession` actualizado, cabecera
- `frontend/app/(client)/sessions/page.tsx` — + mutación `continuar`, botón Continuar (solo "Cerrada"), `continueError`, cabecera actualizada
- `frontend/app/(client)/sessions/[id]/page.tsx` — + mutación `continuar`, botón Continuar en header, `continueError`, interface `SessionOut`
- `_bmad-output/implementation-artifacts/3-4-continuar-una-sesion-con-dedup-preservado.md` — tasks/status/dev record
