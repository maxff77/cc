---
baseline_commit: 283e89aff3af62d5bf7bc58503d3e7f163e74454
---

# Story 3.2: Vistas Completa/Filtrada en vivo en Envío

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGÍA (decisión del owner 2026-06-11):** el término de producto para un prefijo es **"gate"** — DB, API, identificadores de código y todo el copy de UI (masculino: "el gate"). epics.md / architecture.md / docs de UX son anteriores al renombre y todavía dicen "prefijo/prefixes" — lee cada "prefijo" como "gate"; donde haya conflicto, gana "gate". **"Completa" y "Filtrada" NO cambian**: son términos de producto verbatim (UX-DR15) y se quedan tal cual en tabs, headers y empty states.

> **⚠️ HALLAZGOS DIFERIDOS QUE ESTA STORY ABSORBE (deferred-work.md):** (1) **2-5 LOW `ws.ts:162`** — el reducer `batch.line_failed` ignora el `batch_id` del payload (un frame stale que cruce un `seedFromBatch` de otro lote puede colgar una fila ajena y su dedup por `position` suprimir un fallo legítimo). La story 3.1 lo nominó EXPLÍCITAMENTE para 3.2 ("que lo absorba 3.2, que es quien toca ws.ts"). Fix de una línea (Task 4): guard `if (store.batchId !== null && store.batchId !== d.batch_id) break;` — espejo de los demás reducers scoped. El case sigue HOY en ws.ts :162-176. (2) **2-2 LOW `ws.ts:197`** — `seedFromBatch` puede REGRESAR estado WS más fresco: el backend emite `batch.progress` (y hasta la línea 1) antes de que vuelva el POST, y el `onSuccess` siembra `sent=0`, retrocediendo el anillo hasta un intervalo entero. Fix registrado en el hallazgo: early-return cuando `store.batchId === batch.id` (la función vive HOY en :292-318 — el ancla `:197` es de la revisión de 2.2). Mismo motivo de absorción: esta story es quien toca `seedFromBatch` de todos modos (las filas de sesión deben sobrevivir la siembra). Marcar ambos RESOLVED en deferred-work.md al cerrar.

## Story

As a client,
I want live Completa and Filtrada views while my batch runs,
So that I watch data land without manual work.

## Acceptance Criteria

1. **Given** the Envío surface, **when** rendered on mobile, **then** Completa | Filtrada are segmented tabs; on desktop (≥lg) they are two side-by-side panels with COMPLETA / FILTRADA headers — same components, recomposed.
2. **Given** the dual views, **when** responses exist, **then** rows render console-density (mono 11px, 1px separators, muted timestamp/index left, ellipsized content, ✅ success / ❌ danger glyph right) and each tab/panel shows a live mono count badge — Filtrada's in success green.
3. **Given** a `response.captured` event, **when** it arrives, **then** the row appends to Completa (and to Filtrada if it carries new deduped CC data) with the "nueva" success-tint highlight, and the ring's CC nuevas metric increments.
4. **Given** a pane scrolled away from the bottom, **when** new rows arrive, **then** the view stays pinned — auto-scroll only happens if the pane was already at the bottom.
5. **Given** no responses yet, **when** the views render, **then** Completa shows "Aún no hay respuestas." and Filtrada shows "Aún no hay datos CC: capturados." with counters at 0 — no fake rows.

## Tasks / Subtasks

### Backend (Tareas 1–3) — el snapshot debe poder RECONSTRUIR las vistas

- [x] Task 1: `backend/app/db/repos/responses.py` — lecturas para el snapshot (AC: 2, 3, 5)
  - [x] `full_count(session, capture_session_id) -> int` — conteo de filas `kind='full'` de la sesión (el badge de Completa; espejo exacto de `cc_count` :139-151).
  - [x] `list_full(session, capture_session_id, limit) -> list[Response]` — las ÚLTIMAS `limit` revisiones `'full'` de la sesión, devueltas en orden ASCENDENTE de `id` (SELECT `order_by(id.desc()).limit(limit)` + reverse en Python — el panel pinta viejo→nuevo y el scroll ancla abajo).
  - [x] `list_cc(session, capture_session_id, limit) -> list[Response]` — mismo idiom con `kind='cc'` (los valores deduplicados de Filtrada, orden de inserción).
  - [x] Estilo del módulo intacto: pure ORM, flush not commit, docstrings con el porqué. Cero escrituras nuevas.
- [x] Task 2: `backend/app/services/batches.py` — snapshot con los datos de la sesión activa + `session_id` en `state_data` (AC: 2, 3, 5)
  - [x] Constante de módulo `_SNAPSHOT_ROWS = 200` (regla de 2.5: interna del pipeline = constante, JAMÁS un setting en config.py). Es el tope de filas que viajan en el snapshot por lista; los TOTALES siguen siendo los reales (badges honestos aunque la lista esté recortada — el dato completo vive en Historial 3.3 / export 3.5).
  - [x] Reemplazar `_cc_new` (:93-101) por `_active_session_data(session, tenant_id) -> dict`: UNA llamada a `capture_sessions_repo.get_active` que devuelve `{"session_id": id | None, "cc_new": cc_count, "responses_total": full_count, "responses": [...], "cc": [...]}`; sin sesión activa → `{"session_id": None, "cc_new": 0, "responses_total": 0, "responses": [], "cc": []}`. Forma de fila `responses`: `{"id", "message_id", "status", "text", "created_at"}` (ISO-8601 — `Response.created_at.isoformat()`); forma de fila `cc`: `{"id", "text"}` (Filtrada no muestra timestamp — paridad con `filtrada.txt`: un valor por línea).
  - [x] `snapshot` (:104-147): AMBAS ramas (idle y live) hacen merge de `_active_session_data(...)` — un tab reconectado reconstruye Completa/Filtrada/badges del snapshot SOLO (contrato snapshot-first de 2.2; el precedente exacto es `failed_lines`, que ya viaja en el snapshot por esta misma razón, :138-143). Actualizar el docstring.
  - [x] `state_data` (:58-71): añadir `"session_id": batch.capture_session_id` — es función única para TODAS las emisiones `batch.state` (api/batches.py :143-147/:236-238/:258-260/:291-295/:300-302 y send_worker.py :277/:284/:346/:353/:406/:509/:531), así que un solo cambio propaga el binding de sesión a cada tab en cuanto el lote arranca. Sin esto el reducer no puede distinguir "sesión nueva (limpiar paneles)" de "reply tardío de una sesión vieja (ignorar)".
  - [x] `api/ws.py` NO se toca (el snapshot solo cambia de forma en el service); `BatchOut` NO cambia (cero OpenAPI nuevo — el `session_id` viaja por WS, no por REST).
- [x] Task 3: tests backend (AC: 2, 3, 5 — la mitad servidor)
  - [x] Actualizar los asserts de forma EXACTA que ganan claves: `test_batches.py::test_snapshot_idle_shape` (:390-406 — dict exacto: + `"session_id": None, "responses": [], "cc": [], "responses_total": 0`) y `::test_snapshot_live_shape_and_eta_math` (:410-429 — tras el POST la sesión activa EXISTE: `session_id` no nulo, listas vacías, totales 0); `test_batch_controls.py::test_batch_state_events_carry_batch_and_gate_context` (:361-399 — los tres dicts exactos ganan `"session_id"`, no nulo).
  - [x] Nuevos (en `test_attribution.py`, que ya fabrica capturas con `process_incoming` + `FakeGateway`): (a) tras una captura ✅ con CC, `batches_service.snapshot` devuelve `session_id` == sesión activa, `responses` con la revisión (`status="ok"`, texto, `message_id`), `cc` con el valor truncado, `responses_total == 1` y `cc_new == 1`; (b) una revisión ❌ viaja con `status="rejected"` (el glifo ❌ de AC 2 sale de aquí); (c) cap: monkeypatch `batches_service._SNAPSHOT_ROWS` a 1 con dos capturas → la lista trae SOLO la última revisión pero `responses_total == 2` (badges honestos); (d) aislamiento: el snapshot del otro tenant sigue con `session_id` nulo y listas vacías.
  - [x] El resto de la suite (baseline 182) queda verde — `test_send_hardening.py` solo asserta `data["state"]` de los eventos idle (:284/:487), no la forma completa: no se toca.

### Frontend (Tareas 4–6)

- [x] Task 4: `frontend/lib/ws.ts` — store + reducers de sesión (AC: 3; absorbe los dos diferidos)
  - [x] Tipos nuevos exportados: `ResponseRow {key: string; messageId: number; status: "ok" | "rejected"; text: string; capturedAt: string; nueva: boolean}` y `CcRow {key: string; text: string; nueva: boolean}`. `LiveBatchState` (+`IDLE`) gana `sessionId: number | null`, `responses: ResponseRow[]`, `cc: CcRow[]`, `responsesTotal: number`. Keys: filas del snapshot `s-${id}` (id de DB), filas vivas `l-${n}` con un contador monotónico de módulo (el evento no trae id de fila).
  - [x] Contratos hand-typed (el ÚNICO contrato legítimo fuera de types/api.ts — comentario :4-6): `SnapshotData` gana `session_id`, `responses`, `cc`, `responses_total`; `BatchStateData` gana `session_id: number | null`; interface nueva `ResponseCapturedData` espejo VERBATIM del emit de capture.py :327-342: `{session_id, batch_id, message_id, status, previous_status, edited, text, new_cc, cc_total, captured_at}`.
  - [x] Reducer `snapshot`: mapear los campos nuevos (filas con `nueva: false` — el highlight es solo para lo que aterriza en vivo; el snapshot es reconciliación, no novedad).
  - [x] Reducer nuevo `response.captured`: (a) guard de sesión — si `store.sessionId !== null && d.session_id !== store.sessionId` → `break` (reply tardío de una sesión VIEJA: persiste en DB y se verá en Historial 3.3; el panel de Envío es la sesión activa); si `store.sessionId === null` → adoptar `d.session_id`. (b) append a Completa: `{messageId: d.message_id, status: d.status, text: d.text, capturedAt: d.captured_at, nueva: true}` + `responsesTotal + 1`. (c) `ccNew = d.cc_total` (autoritativo del servidor — ES el incremento del anillo del AC 3, el `Metric "CC nuevas"` ya lee `live.ccNew` en progress-ring.tsx :52: cero cambios allí). (d) cada valor de `d.new_cc` (puede ser `[]`) se appendea a Filtrada con `nueva: true`. NO toca `state` (mismo contrato que `batch.progress` :148-150 — la captura sigue armada entre lotes y los replies tardíos siguen aterrizando con el surface en idle, paridad legacy "capture stays armed between batches").
  - [x] Reducer `batch.state`: rama NO-idle — si `d.session_id !== null && store.sessionId !== null && d.session_id !== store.sessionId` → sesión NUEVA reemplazó a la vieja (cambio de gate): limpiar `responses: []`, `cc: []`, `ccNew: 0`, `responsesTotal: 0`; en todo caso adoptar `sessionId: d.session_id ?? store.sessionId`. Rama idle (:180-191) — el reset `{...IDLE, failed, failedLines}` PRESERVA ADEMÁS `sessionId`, `responses`, `cc`, `ccNew`, `responsesTotal`: la sesión sobrevive al lote (legacy: "counters never reset" + el clímax del journey: el lote termina y los datos siguen en pantalla). OJO: hoy ese reset pisa `ccNew` a 0 — eso era correcto sin paneles y deja de serlo aquí.
  - [x] `seedFromBatch` (:292-318): (a) diferido 2-2 — `if (store.batchId === batch.id) return;` primero (no regresar estado WS más fresco del mismo lote). (b) al sembrar otro lote, PRESERVAR los campos de sesión (`sessionId`, `responses`, `cc`, `ccNew`, `responsesTotal`) — pertenecen a la SESIÓN, no al lote; eliminar el reset actual de `ccNew` (:314). Si el lote nuevo activó OTRA sesión (gate distinto), el `batch.state` que el POST emite inmediatamente después (api/batches.py :143-147) trae el `session_id` nuevo y el reducer limpia — el server manda, la siembra no adivina.
  - [x] Diferido 2-5 — reducer `batch.line_failed` (:162-176): añadir el guard de `batch_id` ANTES del dedup por posición.
- [x] Task 5: NUEVO `frontend/components/sessions/` — las vistas duales (AC: 1, 2, 3, 4, 5)
  - [x] `response-row.tsx` — la fila console-density (token `data-row` de DESIGN.md, el ÚNICO elemento console-density del sistema): `font-mono text-[11px] leading-[1.4]`, separador `border-b border-separator` (1px), izquierda índice/timestamp en `text-muted` (Completa: hora `HH:MM:SS` de `capturedAt`/`created_at`; Filtrada: índice `001`-style), contenido con `truncate` (una línea, ellipsis), glifo de status a la derecha (✅ `text-success` / ❌ `text-danger`; las filas de Filtrada no llevan glifo — son datos, no estados). Highlight "nueva": `bg-success/12 text-success` + tag pequeño `nueva` (token `new-highlight` = success al 12%; mismo patrón de clases arbitrarias que client-nav `bg-accent/22`).
  - [x] `response-views.tsx` — la recomposición (AC 1, "same components, recomposed"): UN componente de panel (header/badge + lista scrolleable + empty state) instanciado dos veces. Móvil (`lg:hidden`): HeroUI **Tabs** segmentadas `Completa | Filtrada`, cada tab con badge mono de conteo — Filtrada's en `text-success` (token `dual-view-tabs`; verificar la API compound del Tabs instalado — `@heroui/react` 3.1.0 exporta `Tabs`/`TabList`/`Tab`/`TabPanel`; mismo ejercicio que hizo send-form.tsx :174-230 con `Select.Trigger`/`Select.Popover`). Desktop (`hidden lg:flex` ×2): los dos paneles lado a lado con headers label-caps `COMPLETA` / `FILTRADA` (`text-[10px] font-medium uppercase tracking-[0.12em] text-muted`, idiom exacto de metric.tsx :16) + el mismo badge. Contenedor: `border border-border rounded-md bg-surface` (DESIGN: superficies con outline 1px, sin sombras).
  - [x] Badges: Completa = `responsesTotal`, Filtrada = `ccNew` — mono `tabular-nums`; AMBOS visibles también con 0 (AC 5 "counters at 0").
  - [x] Auto-scroll pinning (AC 4): por panel, `ref` al contenedor scrolleable + flag "estaba abajo" (`scrollHeight - scrollTop - clientHeight < 24`, capturado en `onScroll`); en un `useLayoutEffect` que dependa de la longitud de filas, si estaba abajo → `scrollTop = scrollHeight`; si no → no tocar (la vista queda clavada donde el operador la dejó).
  - [x] Empty states (AC 5, copy VERBATIM de EXPERIENCE.md :111-112): Completa → `Aún no hay respuestas.`; Filtrada → `Aún no hay datos CC: capturados.` — en `text-muted`, sin filas falsas, badges en 0.
  - [x] SIN botón de export (`↓ .txt`): UX-DR6 lo menciona pero es la Story 3.5 — no fabricar un botón muerto (cerco abajo).
- [x] Task 6: `frontend/app/(client)/page.tsx` — montar las vistas (AC: 1, 5)
  - [x] Desktop: reemplazar los dos `<div aria-hidden className="hidden lg:block" />` (:66-68) por los paneles Completa y Filtrada (grid `300px 1fr 1fr` ya existente, :35 — DESIGN: "Completa and Filtrada panels side by side"). Altura: el panel es el elemento flexible que scrollea internamente (p.ej. `lg:max-h-[calc(100vh-8rem)] overflow-y-auto` en la lista) — la cabina queda `sticky` como hoy.
  - [x] Móvil: las Tabs van en el slot que 2.2 dejó comentado (":46 Mobile order per DESIGN.md: ring → controls → (data panels 3.2)"): tras `FailedLines` y antes del bloque del catálogo/SendForm, con altura acotada y scroll interno (p.ej. `max-h-72 overflow-y-auto` en la lista) para que el form siga alcanzable — DESIGN: "the data panel… scrolls internally; the cockpit never scrolls away".
  - [x] Los paneles se renderizan SIEMPRE (no condicionados a `isLive`): en idle muestran los empty states del AC 5 o las filas de la sesión que sigue activa (los datos sobreviven al lote); pásales `live` (ya viene de `useLiveBatch()` :25).

### Tests + gates (Tareas 7–8)

- [x] Task 7: gates de verificación (todos los AC)
  - [x] Backend: `ruff check .`, `mypy app`, `pytest` — verde completo (196 passed = baseline real 192 + los 4 nuevos de Task 3; el "182" del spec quedó corto — la suite ya había crecido tras escribirse la story. ruff "All checks passed!", mypy "no issues found in 36 source files").
  - [x] Frontend: `npx tsc --noEmit` y `npm run lint` (limpios a la primera) + `npm run build` (OK) — sin framework de tests de frontend (decisión diferida del proyecto; NO inventar jest/vitest).
- [x] Task 8: housekeeping + smoke (AC: 3, 4)
  - [x] `_bmad-output/implementation-artifacts/deferred-work.md`: marcar `~~…~~ **RESOLVED in Story 3.2 (fecha)**` los hallazgos **2-5 LOW ws.ts:162** y **2-2 LOW ws.ts:197** (patrón de tachado ya usado por 2.3/2.4/2.5/3.1). Los demás diferidos NO se tocan (cerco abajo).
  - [ ] (HUMAN — necesita credenciales reales) Smoke manual en dev: lote real → el reply del bot pinta la fila en Completa con tag "nueva", el CC aterriza en Filtrada, el anillo sube CC nuevas; scrollear arriba y verificar que filas nuevas NO arrastran la vista; recargar el tab a mitad de lote y verificar que ambos paneles se reconstruyen del snapshot. **No correr contra producción sin el OK de Richard.** ← PENDIENTE: acción manual del owner, fuera del alcance del agente.

## Dev Notes

### Qué NO es esta story (cerco de alcance)

- **Export `.txt`** (botón `↓ .txt` por vista, `services/exports.py`) → **Story 3.5**. UX-DR6/DESIGN dibujan el botón en la misma franja de tabs; aquí NO se renderiza (ni deshabilitado — no fabricar UI muerta).
- **Historial** (lista de sesiones, `/(client)/sessions/[id]`, renombrar, eliminar, REST `api/sessions.py`) → **Story 3.3**. El stub de `/(client)/sessions/page.tsx` NO se toca. Los componentes de Task 5 se diseñan props-driven (filas + conteos por props, no leyendo el store adentro del panel) para que 3.3 los reuse verbatim en el detalle de sesión — esa reusabilidad es requisito de diseño, no especulación (EXPERIENCE: "Historial detail reuses the same dual panels").
- **Continuar sesión + evento `session.active`** → **Story 3.4** (el reducer ignora eventos desconocidos — :218-219 — así que no hay que hacer nada hoy).
- **Cero endpoints REST nuevos, cero cambios de OpenAPI** (`BatchOut` intacto, NO regenerar `types/api.ts`): todo lo nuevo viaja por WS (snapshot + `response.captured` + `batch.state`), que es el contrato hand-typed legítimo de ws.ts.
- **Cero cambios en `core/capture.py`**: el evento `response.captured` ya emite TODO lo que la UI necesita (3.1 lo diseñó así: "3.2 lo consume tal cual"). Si algo parece faltar, el bug está en el consumo, no en la emisión.
- **Diferidos que SIGUEN diferidos** (no arreglar "de paso"): 2-2 #2 (`batches.py:121` append race) y #4 (`ws.py:54` auth de socket abierto) — backend; 2-5 MEDIUM `telegram.py:111` (match de reconciliación) y LOW `send_worker.py:398`/`:652`; 2-3 #2 (pill 'En pausa' ilegible en client-nav — es de OTRO componente); 2-1/1-6 (tipos generados en admin).

### Diseño (decisiones registradas)

- **El snapshot reconstruye las vistas (snapshot-first, no event-sourcing puro):** los AC de la epic solo piden append por evento, pero el contrato de reconexión del proyecto ("every fresh `snapshot` REPLACES the whole store — that's the silent reconciliation", ws.ts :8-10) haría que cada reconexión VACIARA los paneles mientras el badge `cc_new` del snapshot dice otra cosa. Decisión: el snapshot carga `session_id` + filas (capped) + totales — precedente exacto: `failed_lines`, añadido al snapshot en 2.5 por la misma razón. Postgres es el store que respalda la vista (decisión ya registrada en 3.1: las revisiones ❌ se persisten "porque Postgres es ahora el store que respalda la vista Completa de 3.2").
- **Cap `_SNAPSHOT_ROWS = 200` con totales honestos:** las listas del snapshot viajan recortadas a las últimas 200 filas (un snapshot es por reconexión y no debe pesar megas), pero `responses_total`/`cc_new` son los conteos REALES — los badges jamás mienten (UX "cabina de datos": cada número responde una pregunta del operador). El dato completo es de Historial (3.3) y export (3.5).
- **`session_id` viaja en `batch.state` (vía `state_data`), no en un evento nuevo:** el reducer necesita distinguir "el lote nuevo activó OTRA sesión → limpiar paneles" (cambio de gate; legacy: sesión nueva = carpeta nueva) de "reply tardío de una sesión vieja → ignorar en Envío". `batches.capture_session_id` existe desde 3.1 y `state_data` es la única fuente de TODAS las emisiones `batch.state` — un solo cambio, cero eventos nuevos. El evento `session.active` formal nace en 3.4 con "continuar"; no adelantarlo.
- **La sesión sobrevive al lote (y al reset idle):** la captura queda armada entre lotes (legacy parity) y los replies tardíos siguen emitiendo `response.captured` con el surface en idle — los paneles siguen creciendo. Por eso el reset idle de `batch.state` preserva los campos de sesión (igual que ya preserva `failedLines`) y `seedFromBatch` jamás los limpia: solo un `batch.state`/`snapshot` con OTRA `session_id` limpia. Nota: hoy ese reset pisa `ccNew` a 0 sin que se note (el anillo se oculta en idle); con badges persistentes deja de ser inocuo.
- **"nueva" = solo filas que aterrizan en vivo.** Las filas del snapshot llegan con `nueva: false` (reconciliación, no novedad); las del evento, `nueva: true` y lo conservan hasta el siguiente snapshot/limpieza — EXPERIENCE no pide fade-out, no inventarlo.
- **Completa en vivo ≠ Completa persistida (consciente, heredado de 3.1):** la paridad de emisión legacy hace que una edición ok→ok sin CC nuevos se PERSISTA sin emitir — esa revisión no se appendea en vivo pero SÍ aparece tras una reconexión (el snapshot lee la DB). Diferencia documentada en 3.1; el snapshot manda. No "arreglarlo" emitiendo más eventos.
- **`ccNew = cc_total` del evento (autoritativo), no `ccNew + new_cc.length`:** el servidor ya calcula `cc_total` tras guardar (capture.py :339) — sumas en el cliente derivan con frames perdidos; asignar reconcilia gratis. Es la misma métrica que `cc_new` del snapshot (3.1: "salen del mismo número").
- **El guard de sesión descarta en Envío los replies de sesiones viejas:** no se pierden — están en Postgres y el Historial (3.3) los muestra; el panel de Envío representa LA sesión activa (FR17). Con `sessionId === null` (tab fresco sin sesión previa) el primer evento adopta su `session_id`.

### Código actual que vas a tocar (estado HOY @ 283e89a, con anclas)

| Archivo | Hoy | Esta story |
| --- | --- | --- |
| `backend/app/db/repos/responses.py` | `cc_count` :139-151; constantes `KIND_FULL`/`KIND_CC`/`STATUS_*` :19-24 | + `full_count` / `list_full` / `list_cc` (solo lecturas) |
| `backend/app/services/batches.py` | `state_data` :58-71 (sin sesión); `_cc_new` :93-101; `snapshot` :104-147 (`failed_lines` ya viaja :138-143) | `state_data` + `session_id`; `_cc_new` → `_active_session_data`; snapshot + 4 claves en ambas ramas; `_SNAPSHOT_ROWS` |
| `backend/app/api/ws.py` | handshake snapshot-first :67-78 | SIN CAMBIOS (el service cambia la forma) |
| `backend/app/api/batches.py` | emite `batch.state` del new-batch :143-147 (vía `state_data`) | SIN CAMBIOS (hereda `session_id` gratis) |
| `backend/app/core/capture.py` | emit `response.captured` :327-342 — payload `{session_id, batch_id, message_id, status, previous_status, edited, text, new_cc, cc_total, captured_at}` | SIN CAMBIOS (contrato a consumir VERBATIM) |
| `frontend/lib/ws.ts` | `LiveBatchState` :23-41, `IDLE` :90-103, `snapshot` :121-143, `line_failed` :162-176 (diferido), `batch.state` :177-204 (reset idle :180-191), `batch.line_sent` :211-217 (su comentario ":213 Other consumers arrive in 3.2" NO aplica — el consumo de 3.2 es `response.captured`), `seedFromBatch` :292-318 (diferido; resetea `ccNew` :314) | store de sesión + reducer `response.captured` + guards + preservación idle + fixes diferidos |
| `frontend/components/sessions/` | NO EXISTE (nombrado por el árbol de architecture: "session list, response columns") | nuevo: `response-views.tsx` + `response-row.tsx` |
| `frontend/app/(client)/page.tsx` | grid `300px 1fr 1fr` :35; columnas vacías :66-68 ("EMPTY this story… Completa/Filtrada is 3.2"); slot móvil comentado :46 | montar paneles desktop + tabs móvil; borrar los comentarios-promesa |
| `frontend/components/batch/progress-ring.tsx` | `Metric "CC nuevas"` lee `live.ccNew` :52 | SIN CAMBIOS (el anillo sube solo al asignar `ccNew`) |
| `frontend/components/batch/metric.tsx` / `failed-lines.tsx` / `client-nav.tsx` | idiom label-caps :16 / panel inline :25-45 / clases tint arbitrarias `bg-accent/22` :33 | SIN CAMBIOS (solo patrones a copiar) |
| `backend/tests/test_batches.py` | dicts exactos de snapshot :394-406 y :420-429 | + 4 claves nuevas |
| `backend/tests/test_batch_controls.py` | dicts exactos de `batch.state` :369-399; `cc_new` :506 | + `session_id` en los tres dicts; :506 sigue verde |
| `backend/tests/test_attribution.py` | fabrica capturas reales (`process_incoming` + `FakeGateway` + recorder de `broadcaster.emit`) | + tests de snapshot/cap/aislamiento de Task 3 |
| `_bmad-output/implementation-artifacts/deferred-work.md` | 2-5 ws.ts:162 y 2-2 ws.ts:197 abiertos | housekeeping Task 8 |

**Sin cambios:** `core/{capture,attribution,cc_extract,scheduler,send_worker,telegram,broadcaster}.py`, `db/models.py` (cero migraciones — `Response.created_at` :409 y `Batch.capture_session_id` ya existen), `repos/{capture_sessions,send_log,batches}.py`, `api/{admin,gates,auth,batches,ws}.py`, `errors.py`, `config.py`, `main.py`, `deploy/*`, `frontend/types/api.ts` (NO regenerar), `frontend/app/(client)/sessions/page.tsx` (stub de 3.3), legacy `core.py`/`app.py`/`static/`.

### Cumplimiento de arquitectura (no negociable)

- **WS server→client only**: cero comandos por `/ws`; esta story no añade REST. El payload WS es el único contrato hand-typed permitido y vive junto al reducer (ws.ts :4-6) — espejo exacto de lo que emite el backend, snake_case en el wire, camelCase en el store. [Source: architecture.md#API-&-Communication-Patterns]
- **Evento `response.captured` y `session_id`**: nombres literales de la lista de eventos de architecture (`response.captured` ya existe; `session.active` es de 3.4 — NO emitirlo aquí). Todo tenant-scoped vía broadcaster; el snapshot se construye con `user.tenant_id` del handshake. [Source: architecture.md#Communication-Patterns, #Tenant-Scoping]
- **Identificadores en inglés, copy en español tuteo** con términos de producto verbatim: `Completa`/`Filtrada` (tabs/headers), "Aún no hay respuestas.", "Aún no hay datos CC: capturados.", tag "nueva". "sesión de guardado" → `capture_session`/`sessionId` en código. [Source: architecture.md#Code-Naming-Conventions; EXPERIENCE.md#Microcopy]
- **Árbol del repo**: los componentes van en `frontend/components/sessions/` — el directorio que architecture nombra para "session list, response columns" (3.3 le añadirá la lista). [Source: architecture.md#Proposed-Source-Tree]
- **UX spines ganan a mocks** (DESIGN.md es contrato): data-row es el ÚNICO elemento console-density; mono SOLO para datos; superficies con border 1px sin sombras; `rounded-md` (0.25rem) en tabs/badges/cards; exactamente tres métricas junto al anillo (los badges de los paneles NO son métricas del anillo — viven en la franja de tabs/headers). Tokens: `data-row.new-highlight` = success/.12, `dual-view-tabs.count-badge-filtrada` = success, `divider` = separator. [Source: DESIGN.md#Components, #Brand-&-Style]

### Inteligencia de stories previas (3.1 + 2.5 + 2.3 + 2.2)

- **Esta story COBRA promesas dejadas a propósito:** el payload de `response.captured` se diseñó en 3.1 "3.2 lo consume tal cual: fila a Completa, `new_cc` a Filtrada, ring"; `cc_new` real en el snapshot desde 3.1; los comentarios-promesa en page.tsx (":66 EMPTY this story", ":46 data panels 3.2") y ws.ts (":213 Other consumers arrive in Stories 2.5/3.2") se cumplen aquí — borrarlos/actualizarlos al cumplirlos (housekeeping idiom de 3.1).
- **Lecciones de frontend (2.2/2.3/2.5):** el store WS es un singleton `useSyncExternalStore` con UN reducer por evento — no crear un segundo socket ni un segundo store; eventos desconocidos se ignoran (:218-219); copy de UI EXACTO al spec (los reviews comparan string a string); HeroUI v3 usa API compound — verificar contra los typings instalados de `@heroui/react` 3.1.0 (exporta `Tabs`/`TabList`/`Tab`/`TabPanel`), no contra docs de v2 (NextUI) — el ejercicio que send-form.tsx ya hizo con `Select.Trigger`/`Select.Popover`; correr `npm run lint` ANTES de declarar verde (import-order y unused-imports muerden).
- **Lecciones de backend:** asserts de eventos vía recorder monkeypatcheando `broadcaster.emit` (jamás sockets — 2.2); los tests llaman `capture.process_incoming`/`send_worker.step()` directo porque ASGITransport no corre el lifespan; atributos capturados antes de cerrar la sesión (MissingGreenlet 2.3) — `_active_session_data` debe materializar TODO el dict dentro de la sesión; tests con dicts exactos se actualizan COMPLETOS, no con `in`.
- **Semántica legacy que esta story traduce:** las columnas side-by-side Completa/Filtrada y el live-append vienen del SPA legacy (`static/index.html`: "Live responses are split into side-by-side Completa/Filtrada columns"); el auto-scroll-solo-si-estaba-abajo es regla legacy literal ("auto-scroll only if the pane was already at the bottom"); "counters never reset" → badges y filas sobreviven al fin del lote. Lo que muere a propósito: el "history browser live-follow" del legacy se reparte — el live de Envío es esta story, el detalle/follow de Historial es 3.3.
- **1.7/CI:** Conventional Commits con scope (`feat(backend,frontend): …`), rama `story/3.2-vistas-completa-filtrada`; push a main = deploy automático al VPS. Sin migraciones ni claves de entorno nuevas.

### Estándares de testing

- Backend: `pytest` + `pytest-asyncio` (`loop_scope="session"`) + httpx `ASGITransport` contra la app real y el Postgres de dev; self-seed/self-clean; sin mocks de DB; un comportamiento por test; fixtures existentes (`ctx`/`gate`/`client_user`/`fake_gateway`/`events`) — no inventar otros. Las capturas de los tests nuevos van DIRECTO a `capture.process_incoming(IncomingReply(...))` (idiom de test_attribution.py).
- Frontend: SIN framework de tests (decisión diferida del proyecto — no instalar nada). Gates: `npx tsc --noEmit` + `npm run lint`. La verificación de comportamiento (tabs, auto-scroll, highlight) es el smoke manual de Task 8.
- El cap del snapshot se testea monkeypatcheando `batches_service._SNAPSHOT_ROWS` (idiom de constantes de módulo: `_ERROR_RETRY_SECONDS`/`_RETRY_SECONDS` en suites previas).

### Notas de estructura del proyecto

- **Nuevos:** `frontend/components/sessions/response-views.tsx`, `frontend/components/sessions/response-row.tsx` — únicos archivos nuevos; cero migraciones, cero módulos backend nuevos.
- **Modificados:** `backend/app/db/repos/responses.py`, `backend/app/services/batches.py`, `backend/tests/{test_batches,test_batch_controls,test_attribution}.py`, `frontend/lib/ws.ts`, `frontend/app/(client)/page.tsx`, `_bmad-output/implementation-artifacts/deferred-work.md`.
- Legacy `core.py`/`app.py`/`static/` congelados en la raíz — solo referencia de comportamiento. **🔒 JAMÁS leer contenido bajo `respuestas/`. JAMÁS tocar `.env` ni `anon.session`.**

### Referencias

- [Source: planning-artifacts/epics.md#Story-3.2 — ACs verbatim; #Epic-3 (intro: "live Completa/Filtrada views, CC dedup"); #Story-3.3/#Story-3.5 (fronteras: detalle/rename/delete y export NO son de aquí)]
- [Source: planning-artifacts/architecture.md#Proposed-Source-Tree (`components/sessions/` — "session list, response columns"); #Communication-Patterns (eventos literales `response.captured`/`session.active`, envelope `{"event","data"}`); #Data-flow ("…broadcaster emits response.captured to that tenant's sockets → UI updates"); #Code-Naming-Conventions]
- [Source: ux-designs/ux-cc-2026-06-10/DESIGN.md#Components (tokens `data-row` — mono 11px, divider separator, new-highlight success/.12, glifos ✅/❌ — y `dual-view-tabs` — segmented, badge Filtrada success); #Layout-&-Spacing (móvil: panel dual llenando alto restante con scroll interno; desktop: grid 300px 1fr 1fr con paneles lado a lado); #Typography (data-mono 11px, label-caps)]
- [Source: ux-designs/ux-cc-2026-06-10/EXPERIENCE.md :67 (comportamiento del dual view), :92 (mapeo `response.captured`), :111-112 (empty states verbatim), :125 (regla de auto-scroll), :190-192 (responsive); UX-DR6/DR15/DR16 en epics.md :128/:137-138]
- [Source: implementation-artifacts/3-1-captura-y-atribucion-de-respuestas-del-bot.md — payload `response.captured` diseñado para 3.2; ❌ persistido para la vista Completa; paridad de emisión (ok→ok sin CC no emite); `cc_new`/`cc_total` "salen del mismo número"; cerco que nominó los diferidos ws.ts a 3.2]
- [Source: implementation-artifacts/deferred-work.md#Story-2-5-review (LOW ws.ts:162) y #Story-2-2-review (LOW ws.ts:197) — absorbidos aquí; el resto SIGUE diferido]
- [Source: _bmad-output/project-context.md — 🔒 reglas: nunca leer respuestas/, nunca tocar .env/anon.session; counters never reset; CC dedup por sesión]
- [Source: código actual @ 283e89a — backend/app/{services/batches.py, db/repos/responses.py, core/capture.py :327-342, api/{batches,ws}.py}, backend/tests/{test_batches,test_batch_controls,test_attribution}.py, frontend/{lib/ws.ts, app/(client)/page.tsx, components/batch/{progress-ring,metric,failed-lines,send-form}.tsx, components/client-nav.tsx}]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Fable 5) — BMad dev agent, 2026-06-12.

### Debug Log References

- Backend gates: `pytest` → 196 passed (baseline real 192 + 4 nuevos); `ruff check app/ tests/` → All checks passed; `mypy app` → no issues in 36 source files.
- Frontend gates: `npx tsc --noEmit` limpio, `npm run lint` limpio (sin necesidad de `--fix`), `npm run build` OK (todas las rutas prerenderizadas).

### Completion Notes List

- **Verificación de supuestos contra el código actual:** todas las anclas del spec (responses.py :139-151, batches.py :58-71/:93-101/:104-147, capture.py :327-342, ws.ts :162-176/:177-204/:292-318, page.tsx :35/:46/:66-68) seguían vigentes @ HEAD — cero deriva respecto al baseline 283e89a en lo que esta story toca.
- **Task 1:** `full_count`/`list_full`/`list_cc` añadidos a `repos/responses.py`; los dos listados comparten un helper privado `_list_last` (SELECT desc + reverse en Python, como pide el spec) — solo lecturas, pure ORM.
- **Task 2:** `_SNAPSHOT_ROWS = 200` constante de módulo; `_cc_new` reemplazado por `_active_session_data` (una llamada a `get_active`, todo materializado dentro de la sesión — lección MissingGreenlet 2.3); ambas ramas de `snapshot` hacen `**merge`; `state_data` gana `"session_id": batch.capture_session_id` (propaga a las 12 emisiones `batch.state` sin tocar api/batches.py ni send_worker.py). `api/ws.py` y `BatchOut` intactos.
- **Task 3:** asserts exactos actualizados (idle/live shape en test_batches.py; los tres dicts de test_batch_controls.py ganan `session_id` leído de la DB); 4 tests nuevos en test_attribution.py (filas+totales, ❌→`rejected`, cap con totales honestos vía monkeypatch de `_SNAPSHOT_ROWS`, aislamiento multi-tenant). Nota: el "baseline 182" del spec ya era 192 al implementar (la suite creció tras escribirse la story) — 196 verde total.
- **Task 4 (ws.ts):** tipos `ResponseRow`/`CcRow` exportados; `LiveBatchState`+`IDLE` con `sessionId`/`responses`/`cc`/`responsesTotal`; contratos `SnapshotData`/`BatchStateData` extendidos + `ResponseCapturedData` espejo verbatim de capture.py; reducer `snapshot` mapea filas con `nueva: false` (keys `s-${id}`); reducer nuevo `response.captured` con guard de sesión + adopción + `ccNew = d.cc_total` autoritativo (keys vivas `l-${n}` con contador de módulo); `batch.state` idle preserva los campos de sesión (incl. el `ccNew` que antes se pisaba) y la rama no-idle limpia paneles SOLO en cambio de `session_id`; `seedFromBatch` con early-return mismo lote (diferido 2-2) y preservación de campos de sesión; guard de `batch_id` en `batch.line_failed` (diferido 2-5). El comentario-promesa de :213 eliminado al cumplirse.
- **Task 5:** `components/sessions/response-row.tsx` exporta `DataRow` (nombrado por el token `data-row` de DESIGN.md; evita colisión con el tipo `ResponseRow` de ws.ts) — mono 11px, separador 1px, izquierda muted, truncate, glifo ✅/❌ solo Completa, highlight `bg-success/12 text-success` + tag "nueva". `response-views.tsx`: UN `ResponsePanel` (header label-caps opcional + `CountBadge` mono + `PanelList` con pinning de scroll) recompuesto en `CompletaPanel`/`FiltradaPanel` (desktop, props-driven para reuso 3.3) y `ResponseTabs` (móvil, HeroUI Tabs compound `Tabs.ListContainer`/`Tabs.List`/`Tabs.Tab`/`Tabs.Indicator`/`Tabs.Panel`, verificado contra los typings instalados de @heroui/react 3.1.0 — variante default = segmented). En las tabs móviles el label+badge vive en la tab y el panel se renderiza sin header (recomposición, no duplicación). Sin botón export (3.5).
- **Task 6:** page.tsx monta `ResponseTabs` (`lg:hidden`, lista `max-h-72`) en el slot móvil tras `FailedLines`, y `CompletaPanel`/`FiltradaPanel` (`hidden lg:flex`, listas `lg:max-h-[calc(100vh-8rem)]`) en las dos columnas del grid; siempre renderizados (no condicionados a `isLive`); comentarios-promesa de 2.2 borrados/actualizados.
- **Decisión menor:** el componente de panel acepta `header?: boolean` (las tabs móviles ya llevan label+badge — un header duplicado dentro del panel sería ruido); el badge usa `bg-surface-secondary` para legibilidad sobre `bg-surface`, y el tag "nueva" `bg-success/20` para destacar sobre el fondo `success/12` de la fila.
- **Pendiente HUMAN:** el smoke manual de Task 8 (lote real contra dev con credenciales de Telegram) queda para el owner — no se corrió contra producción.

### File List

- `backend/app/db/repos/responses.py` — modificado (Task 1: `full_count`, `_list_last`, `list_full`, `list_cc`)
- `backend/app/services/batches.py` — modificado (Task 2: `_SNAPSHOT_ROWS`, `_active_session_data`, snapshot merge ×2, `state_data.session_id`)
- `backend/tests/test_batches.py` — modificado (Task 3: shapes idle/live)
- `backend/tests/test_batch_controls.py` — modificado (Task 3: `session_id` en los tres dicts de batch.state)
- `backend/tests/test_attribution.py` — modificado (Task 3: 4 tests nuevos de snapshot 3.2)
- `frontend/lib/ws.ts` — modificado (Task 4: store de sesión, `response.captured`, guards, preservación idle/seed, diferidos 2-2/2-5)
- `frontend/components/sessions/response-row.tsx` — NUEVO (Task 5: `DataRow`)
- `frontend/components/sessions/response-views.tsx` — NUEVO (Task 5: `ResponsePanel`/`CompletaPanel`/`FiltradaPanel`/`ResponseTabs`)
- `frontend/app/(client)/page.tsx` — modificado (Task 6: montaje móvil + desktop)
- `_bmad-output/implementation-artifacts/deferred-work.md` — modificado (Task 8: 2-5 ws.ts:162 y 2-2 ws.ts:197 → RESOLVED)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — modificado (3-2 → review)
- `_bmad-output/implementation-artifacts/3-2-vistas-completa-filtrada-en-vivo-en-envio.md` — este archivo (tasks, dev record, status)
