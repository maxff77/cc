---
baseline_commit: ac61983ee6c543533af3d6c10e73e57efc2be60f
---

# Story 3.3: Historial: listar, ver detalle, renombrar y eliminar sesiones

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGÍA (decisión del owner 2026-06-11):** el término de producto para un prefijo es **"gate"** — DB, API, identificadores de código y todo el copy de UI (masculino: "el gate"). epics.md / architecture.md / docs de UX son anteriores al renombre y todavía dicen "prefijo/prefixes" — lee cada "prefijo" como "gate"; donde haya conflicto, gana "gate". En esta story: el AC 1 del epic dice "grouped by prefix" → agrupado por **gate**; la sub-línea mono de UX-DR11 `prefijo · session-id` → **`gate_value · id`** (el id de DB es el id estable de sesión — el timestamp-folder del legacy murió con el disco). "Completa", "Filtrada", "En curso", "Cerrada", "Renombrar", "Eliminar" son términos de producto verbatim y se quedan tal cual.

> **⚠️ HALLAZGO DIFERIDO QUE ESTA STORY ABSORBE (deferred-work.md, review de 2-3):** **2-3 MEDIUM `client-nav.tsx:34`** — la pill 'En pausa' es ilegible: `PILL_CLASS.paused` usa `bg-warning/18 text-warning-foreground`, pero `--warning-foreground` es casi-negro (color de contraste para fills SÓLIDOS warning) y la app es dark-mode fija → texto 10px casi-negro sobre tint ámbar casi-negro. 3.2 lo dejó diferido con la razón explícita "es de OTRO componente" — pero ESTA story sí toca `client-nav.tsx` (el highlight de ruta activa para `/sessions/[id]`, Task 6), así que lo absorbe por la regla de siempre: quien toca el archivo, cobra el diferido. Fix de una palabra: `text-warning-foreground` → `text-warning` (espejo de sus hermanos `text-accent`/`text-danger`). Marcar RESOLVED en deferred-work.md al cerrar.

## Story

As a client,
I want to browse my sessions, rename them and delete the ones I don't need,
So that my history stays organized.

## Acceptance Criteria

1. **Given** `/(client)/sessions`, **when** the client opens it, **then** their sessions list grouped by prefix, newest first, each row showing the friendly name, a mono sub-line `prefijo · session-id`, and a right badge "En curso" (accent-tint) or "Cerrada" (muted).
2. **Given** a session row, **when** tapped, **then** `/(client)/sessions/[id]` opens with the same dual Completa/Filtrada views.
3. **Given** the detail view of a session that is currently live, **when** `response.captured` events arrive, **then** the view live-follows; navigating to another session stops the follow.
4. **Given** a session row, **when** the client renames it inline, **then** the new name persists via REST and shows immediately, **and** names are capped at 200 characters.
5. **Given** a session row, **when** the client deletes it, **then** a confirm modal asks "¿Eliminar esta sesión? No se puede deshacer." and on confirm the session and its rows are gone — content editing does not exist anywhere.
6. **Given** the session bound to a live batch, **when** the client tries to delete it, **then** the request is rejected with an error code and the UI shows "Detén el lote antes de eliminar esta sesión."
7. **Given** a client with no sessions, **when** Historial renders, **then** it shows "Todavía no tienes sesiones. Tu primer lote crea una." with a link to Envío.
8. **Given** any session request, **when** it resolves, **then** only the requesting tenant's sessions are reachable (isolation).

## Tasks / Subtasks

### Backend (Tareas 1–3)

- [x] Task 1: capa de repos — lecturas/escrituras de Historial (AC: 1, 2, 5, 8)
  - [x] `backend/app/db/repos/capture_sessions.py` (estilo del módulo intacto: pure ORM, flush not commit, tenant-scoped con `tenant_id` explícito): `list_for_tenant(session, tenant_id) -> list[CaptureSession]` — todas las sesiones del tenant, `order_by(CaptureSession.id.desc())` (newest first; sin paginación — escala MVP, NFR2); `get_for_tenant(session, tenant_id, session_id) -> CaptureSession | None` — lookup tenant-scoped, otro tenant ⇒ `None` (espejo exacto de `batches_repo.get_batch` :75-88: "existence is never leaked"); `delete(session, capture_session) -> None` — `await session.delete(...)` + flush; las filas de `responses` las borra el CASCADE de DB (FK `responses.capture_session_id` ondelete CASCADE, models.py :393-395) y los lotes sobreviven con `capture_session_id` NULL (FK SET NULL, models.py :222-224) — cero borrado manual.
  - [x] `backend/app/db/repos/responses.py`: ampliar `_list_last`/`list_full`/`list_cc` (:169-205) a `limit: int | None` — `None` ⇒ sin `LIMIT` (y sin el reverse-dance: con `None`, `order_by(id.asc())` directo). El snapshot sigue pasando `_SNAPSHOT_ROWS` (services/batches.py :135-146, sin cambios); el detalle de Historial pasa `None` — **el dato completo vive aquí**, promesa registrada de 3.2 ("el dato completo es de Historial (3.3) y export (3.5)"); el cap de 200 es solo del snapshot.
- [x] Task 2: `backend/app/errors.py` — códigos nuevos (AC: 6, 8)
  - [x] Bloque `--- Codes this story (3.3) defines ---` (idiom de los bloques 2.3): `session_not_found()` → 404, code `session_not_found`, message `"Esa sesión no existe."` (cubre id desconocido, id de otro tenant e id fuera de int4 por igual — idiom `batch_not_found` :214-220); `session_in_use()` → 409, code `session_in_use`, message `"Detén el lote antes de eliminar esta sesión."` — el message ES el copy del AC 6 verbatim: la UI renderiza `err.message` tal cual (contrato `{code, message}` + EXPERIENCE "falls back to the server's message verbatim").
  - [x] El cap de nombre NO es un error helper: va como `field_validator` de pydantic (ValueError ⇒ 422), idiom exacto de `_validate_gate_name` (api/admin.py :356-367) — el frontend espeja la validación antes de enviar (patrón `validateCategoryName` de admin/gates).
- [x] Task 3: NUEVO `backend/app/api/sessions.py` — el router de Historial (AC: 1, 2, 4, 5, 6, 8)
  - [x] `router = APIRouter(prefix="/api/sessions", tags=["sessions"])` — módulo nombrado por el árbol de architecture ("sessions.py — /api/sessions (capture sessions, export .txt)"; la mitad export es 3.5, NO la fabriques). Registrarlo en `app/main.py` (:74-79, entre `batches_router` y `ws_router`). Tenant scoping: `user.tenant_id` de `get_current_user` SIEMPRE — jamás del body/path (mandato de deps.py); cualquier rol autenticado (el owner navega su historial igual que un cliente, mismo criterio que `POST /api/batches`).
  - [x] Schemas inline (convención del codebase, api/batches.py :43-62): `SessionOut {id, name: str | None, gate_value, gate_name, is_active: bool, created_at}`; `SessionListResponse {items: list[SessionOut], total: int}`; filas del detalle ESPEJO de las del snapshot para que los mappers del frontend 3.2 sirvan tal cual — `SessionResponseRow {id, message_id, status, text, created_at}` / `SessionCcRow {id, text}` (services/batches.py :127-146); `SessionDetailOut {**SessionOut, responses, cc, responses_total: int, cc_total: int}`; `RenameSessionRequest {name: str}` con `field_validator` (trim, no vacío, sin caracteres no imprimibles, ≤ 200 — el cap del AC 4 = `String(200)` de models.py :343, espejo del legacy `escribir_nombre`).
  - [x] Helper `_require_session(session, tenant_id, session_id) -> CaptureSession`: guard `0 < session_id <= _PG_INT_MAX` (constante local, idiom api/batches.py :40 — ids fuera de int4 desbordan asyncpg) + `get_for_tenant` + `session_not_found()` si `None` (idiom `_controlled_batch` :201-217).
  - [x] `GET ""` → `SessionListResponse` con `list_for_tenant` (newest first; el AGRUPADO por gate es del cliente, Task 5 — la API entrega lista plana).
  - [x] `GET /{session_id}` → `SessionDetailOut`: `_require_session` + `list_full(session, id, None)` / `list_cc(session, id, None)` (SIN cap — Task 1) + `responses_total`/`cc_total` = `len()` de las listas (sin cap son los totales reales; los repos `full_count`/`cc_count` quedan para el snapshot). `created_at` de fila en ISO-8601 (pydantic serializa `datetime` solo — misma forma on-wire que el snapshot).
  - [x] `PATCH /{session_id}` → `SessionOut`: `_require_session` + `target.name = body.name` + commit (idiom `update_gate` :491-514, sin la danza de duplicados — los nombres NO son únicos). **SIN guard de lote vivo** — paridad legacy registrada: "renombrar is unguarded" (project-context). `updated_at` se actualiza solo (`onupdate=func.now()`).
  - [x] `DELETE /{session_id}` → 204: `_require_session`; guard del AC 6 — `live = await batches_repo.get_live_batch(session, tenant_id)` y si `live is not None and live.capture_session_id == target.id` ⇒ `raise session_in_use()`; si no, `capture_sessions_repo.delete` + commit. Decisiones registradas: (a) borrar la sesión ACTIVA con el surface idle SÍ se permite — el AC solo protege la ligada a un lote VIVO; queda el tenant sin sesión activa hasta que el próximo lote cree una (`resolve_for_batch`); (b) borrado DURO con CASCADE (las filas "are gone", AC 5) — no hay soft-delete de sesiones; (c) la ventana TOCTOU contra un `POST /api/batches` concurrente que liga la misma sesión se CIERRA con un row lock (corrección post-review 3-3; la aceptación original se apoyaba en una premisa falsa: "el FK violado haría 500 en el POST" solo vale en un orden — el FK es `SET NULL`, así que en el otro orden el DELETE des-ligaba en silencio el lote ya VIVO, sin 500 ni rechazo, y las capturas posteriores caían en una sesión backfill INACTIVE oculta). El lookup del delete toma `FOR UPDATE` (`get_for_tenant(..., for_update=True)`) ANTES del guard de lote vivo: el FK check del INSERT del batch toma `FOR KEY SHARE` sobre la fila de la sesión, que conflictúa con `FOR UPDATE` — o el POST bloquea y falla sobre la fila borrada (el 500 documentado), o comitea primero y el guard ve el lote vivo ⇒ 409.

### Frontend (Tareas 4–6)

- [x] Task 4: `frontend/lib/ws.ts` — acción `clearSession` (AC: 5; honestidad del mismo tab)
  - [x] Exportar `clearSession(sessionId: number)`: si `store.sessionId === sessionId`, resetear SOLO los campos de sesión (`sessionId: null, responses: [], cc: [], ccNew: 0, responsesTotal: 0`) preservando todo lo demás; si no coincide, no-op. La llama la mutación de Eliminar (Task 5) cuando el borrado tocó la sesión que el store tiene ligada: sin esto, el operador borra la sesión en Historial, vuelve a Envío y los paneles siguen mostrando filas que YA NO EXISTEN en el servidor hasta la próxima reconexión. Precedente exacto del patrón: `seedFromBatch` (:484-522) — seed local confirmado por REST, el WS sigue siendo la fuente de verdad después. Decisión registrada: otros tabs abiertos reconcilian en su próximo snapshot (aceptado a escala MVP — el dato ya no existe server-side, solo se ve stale).
  - [x] CERO eventos WS nuevos y CERO cambios de reducers: `session.active` nace en 3.4 con "continuar" (cerco de 3.2, se respeta) — no lo adelantes para el delete.
- [x] Task 5: `frontend/app/(client)/sessions/page.tsx` — la lista real reemplaza el stub (AC: 1, 4, 5, 6, 7, 8)
  - [x] `"use client"` + `useQuery({queryKey: ["sessions"], queryFn: () => api.get<SessionListResponse>("/api/sessions")})`. Interfaces locales espejo del backend (snake_case end-to-end) — idiom explícito de admin/gates/page.tsx :22-46 y de `GateListResponse` en page.tsx :24-27; NO regenerar `types/api.ts` (el diferido 2-1 "migrate all admin pages in one epic-wide pass" SIGUE diferido). Cold load: `Spinner` centrado (idiom page.tsx :70-74 — el codebase ya resolvió "skeletons" de UX-DR16 como Spinner; seguir el código); error: `Alert status="danger"`.
  - [x] Vacío (AC 7): copy VERBATIM `Todavía no tienes sesiones. Tu primer lote crea una.` + Link `Ir a Envío` a `/` — el JSX del stub actual (:5-16) sobrevive como rama vacía (el copy ya es exacto).
  - [x] Agrupado por gate (AC 1): del array plano newest-first, agrupar por `gate_value` preservando orden de primera aparición (⇒ grupos ordenados por su sesión más reciente). Header de grupo: `gate_name` en label-caps (`text-[10px] font-medium uppercase tracking-[0.12em] text-muted`, idiom metric.tsx :16) + `gate_value` en mono.
  - [x] Fila de sesión (UX-DR11 / DESIGN :214): `Link` al detalle `/sessions/{id}` envolviendo nombre+sub-línea (las acciones viven FUERA del Link — un tap en Renombrar/Eliminar NO navega); heading = `name ?? fallback` donde el fallback formatea `created_at` como `YYYY-MM-DD HH:MM` (espejo del legacy `nombre_bonito` — models.py :322 lo documenta: "NULL ⇒ the UI falls back to a created_at format"); sub-línea mono muted `{gate_value} · {id}`; badge derecha: `is_active` ⇒ `En curso` con `bg-accent/22 text-accent` (accent-tint, mismas clases arbitrarias que client-nav :33), si no `Cerrada` con `bg-surface-tertiary text-muted`. El badge deriva de `is_active` — decisión registrada de 3.1 ("el badge En curso/Cerrada de 3.3 deriva de is_active"); a lo sumo UNA "En curso" por tenant (índice parcial).
  - [x] Renombrar inline (AC 4): botón `Renombrar` que cambia la fila a un `Input` (valor inicial = nombre actual o vacío) + Guardar/Cancelar; `useMutation` → `api.patch<SessionOut>(\`/api/sessions/\${id}\`, {name})`; validación cliente espejo del backend (trim no vacío, ≤ 200 — `maxLength={200}` + chequeo, patrón `validateCategoryName` de admin/gates :55-60); `onSuccess` invalida `["sessions"]` (y `["session", id]` si existe) — "shows immediately". Errores: `err.message` inline (idiom `DeleteGateAction` :836-846).
  - [x] Eliminar (AC 5, 6): botón `Eliminar` (danger) → **modal de confirmación** (el AC lo pide modal, a diferencia del confirm inline de admin/gates) con copy VERBATIM `¿Eliminar esta sesión? No se puede deshacer.` y botones Eliminar (danger) / Cancelar; máximo UN nivel de modal (UX-DR10). HeroUI v3 trae `Modal` y `AlertDialog` (verificados en `@heroui/react` 3.1.0 `dist/components/` — modal y alert-dialog existen): verificar la API compound contra los typings INSTALADOS antes de escribir JSX — el mismo ejercicio que 3.2 hizo con `Tabs` y 2.2 con `Select`. `useMutation` → `api.delete<void>(\`/api/sessions/\${id}\`)`; `onSuccess`: cerrar modal, invalidar `["sessions"]`, `clearSession(id)` (Task 4); `onError`: si `err.code === "session_in_use"` mostrar `err.message` (ES el copy del AC 6) dentro del modal sin cerrarlo; `session_not_found` ⇒ ya borrada en otro tab: tratar como éxito (idiom `DeleteGateAction` :835-841).
  - [x] SIN botón `Continuar` (Story 3.4) y SIN export `↓ .txt` (Story 3.5) — no fabricar UI muerta (mismo cerco que 3.2 aplicó al export). SIN edición de contenido en ninguna parte (AC 5 / FR19) — las filas de respuestas no tienen acciones.
- [x] Task 6: NUEVO `frontend/app/(client)/sessions/[id]/page.tsx` + retoque de nav (AC: 2, 3)
  - [x] Página cliente (`"use client"` — ruta nombrada por architecture y UX-DR17 verbatim): `useParams<{id: string}>()` de `next/navigation`; id no numérico/fuera de rango ⇒ render directo del estado no-encontrada. `useQuery({queryKey: ["session", id], queryFn: () => api.get<SessionDetailOut>(\`/api/sessions/\${id}\`)})`; en `ApiError` 404 (`session_not_found`) render `Esa sesión no existe.` + Link de vuelta a `/sessions` — nunca un dead-end (UX-DR16).
  - [x] Cabecera: heading = `name ?? fallback created_at` (mismo fallback que la lista), sub-línea mono `{gate_value} · {id}`, badge En curso/Cerrada (mismos estilos), y Link de regreso a Historial.
  - [x] Vistas duales (AC 2 — "the same dual Completa/Filtrada views"): REUSAR VERBATIM `CompletaPanel`/`FiltradaPanel` (desktop, `hidden lg:flex`, dos columnas `lg:grid lg:grid-cols-2 lg:gap-6`) y `ResponseTabs` (`lg:hidden`) de `components/sessions/response-views.tsx` — son props-driven A PROPÓSITO (su comentario :7-9: "Story 3.3's Historial detail reuses these panels verbatim"; DESIGN :190 "Historial detail reuses the same dual panels"). Mapear las filas REST a `ResponseRow`/`CcRow` (tipos exportados de ws.ts :27-42): keys `s-${id}`, `capturedAt = created_at`, `nueva: false` (el highlight "nueva" es del live de Envío; el detalle es lectura). Totales = `responses_total`/`cc_total` del detalle. Listas con scroll interno (`lg:max-h-[calc(100vh-12rem)]` desktop; móvil hereda el `max-h-72` interno de `ResponseTabs` — ver Completion Notes: el reuso verbatim ganó al `max-h-[60vh]` sugerido).
  - [x] Live-follow (AC 3): `const live = useLiveBatch()` + `useEffect` que observa `[live.responsesTotal, live.ccNew]` y, SOLO si `live.sessionId === id`, hace `queryClient.invalidateQueries({queryKey: ["session", id]})` — es el port del idiom legacy LITERAL ("el history browser live-follows la sesión activa: debounced refresh en cada evento respuesta"); sin timer extra: los eventos llegan al paso del bot (≥ intervalo de envío) y react-query dedupea fetches en vuelo. Navegar a otra sesión cambia `id` ⇒ el efecto deja de coincidir — el follow se detiene POR CONSTRUCCIÓN (segunda mitad del AC 3, gratis). El pinning de auto-scroll ya vive en `PanelList` (response-views.tsx :80-127) — "stays pinned" sale gratis del reuso.
  - [x] `frontend/components/client-nav.tsx`: el item Historial debe quedar activo también en `/sessions/[id]` — hoy `active={pathname === item.href}` (:115-117) lo apaga en el detalle. Cambiar el cálculo a `pathname === href || (href !== "/" && pathname.startsWith(href + "/"))`. **Y absorber el diferido 2-3 #2** (:34): `PILL_CLASS.paused` → `bg-warning/18 text-warning`.

### Tests + gates (Tareas 7–8)

- [x] Task 7: NUEVO `backend/tests/test_sessions.py` (AC: todos los del lado servidor)
  - [x] Setup idiom (test_attribution.py): fixtures existentes `ctx`/`gate`/`client_user`/`fake_gateway` — no inventar otros; sesiones reales vía `POST /api/batches` (crea/liga la activa) + capturas vía `capture.process_incoming(IncomingReply(...))` directo (ASGITransport no corre el lifespan); eventos jamás por sockets.
  - [x] **Lista (AC 1):** dos lotes de gates distintos (drenar el primero con `send_worker.step()` antes del segundo) ⇒ `GET /api/sessions` devuelve ambas newest-first, la segunda `is_active=True`, la primera `False`, `name` null, snapshots `gate_value`/`gate_name` correctos; `total` == 2.
  - [x] **Detalle (AC 2):** tras una captura ✅ con CC, `GET /api/sessions/{id}` trae la fila `'full'` (`{id, message_id, status: "ok", text, created_at}`) + la `'cc'` (`{id, text}` truncado en `Status`) + `responses_total == 1`/`cc_total == 1` — dicts de forma exacta (lección: asserts completos, no `in`). Detalle SIN cap: sembrar > `_SNAPSHOT_ROWS`… no — innecesariamente caro; basta monkeypatchear `batches_service._SNAPSHOT_ROWS` a 1 con dos capturas y verificar que el DETALLE trae las 2 mientras el snapshot trae 1 (el contraste prueba el `limit=None`).
  - [x] **Renombrar (AC 4):** PATCH persiste (`GET` lo refleja) y devuelve `SessionOut` con el nombre; 200 chars exactos OK; 201 ⇒ 422; vacío/whitespace ⇒ 422; renombrar CON lote vivo ⇒ 200 (paridad legacy: unguarded).
  - [x] **Eliminar (AC 5, 6):** con lote vivo ligado ⇒ 409 body exacto `{"code": "session_in_use", "message": "Detén el lote antes de eliminar esta sesión."}`; tras `POST /{id}/stop` (o drenado) el DELETE de la MISMA sesión ⇒ 204 aunque siga `is_active=True` (el guard es por lote vivo, no por is_active); tras el 204: fuera de la lista, sus filas de `responses` ya no existen (SELECT directo), y el `batches.capture_session_id` del lote quedó NULL con el lote intacto.
  - [x] **404 nunca filtra existencia (AC 8):** id desconocido, id de OTRO tenant (GET, PATCH y DELETE — los tres) e id > int4 ⇒ 404 `session_not_found` idénticos; la lista del tenant B jamás contiene sesiones de A (idiom cross-tenant de test_attribution.py).
  - [x] Suite COMPLETA verde (baseline 196 + los nuevos) — esta story no toca módulos que otros tests asserten, pero corre todo igual. (202 passed.)
- [x] Task 8: gates de verificación + housekeeping (todos los AC)
  - [x] Backend: `ruff check .`, `mypy app`, `pytest` — verde completo.
  - [x] Frontend: `npx tsc --noEmit` + `npm run lint` + `npm run build` — sin framework de tests (decisión diferida del proyecto; NO inventar jest/vitest). Correr lint ANTES de declarar verde (import-order y unused-imports muerden).
  - [x] `_bmad-output/implementation-artifacts/deferred-work.md`: marcar `~~…~~ **RESOLVED in Story 3.3 (fecha)**` el hallazgo **2-3 MEDIUM client-nav.tsx:34** (pill 'En pausa'). Los demás diferidos NO se tocan (cerco abajo).
  - [ ] (HUMAN — necesita credenciales reales) Smoke manual en dev: lote real → Historial muestra la sesión "En curso"; abrir el detalle a mitad de lote y ver el live-follow; renombrar y verificar persistencia tras recarga; borrar una sesión vieja y verificar que desaparece con sus filas; intentar borrar la ligada al lote vivo y leer el error. **No correr contra producción sin el OK de Richard.**

## Dev Notes

### Qué NO es esta story (cerco de alcance)

- **Continuar sesión + evento `session.active`** → **Story 3.4** (reabrir como activa, dedup precargado, guard "Termina o detén el lote actual…"). NO renderizar botón Continuar ni emitir `session.active` — el cerco de 3.2 lo reservó para 3.4 y se respeta.
- **Export `.txt`** (`↓ .txt` por vista, `services/exports.py`, la mitad export de api/sessions.py) → **Story 3.5**. Sin botones muertos.
- **Vista de soporte cross-tenant `/admin/tenants/[id]`** (`for_tenant(id)` + audit log) → **Story 3.6**. Los endpoints de aquí son SIEMPRE del propio tenant.
- **Edición de contenido NO existe en ninguna parte** (AC 5 / FR19): las filas de respuestas son de solo lectura en lista, detalle y para siempre — no diseñar "por si acaso".
- **Cero eventos WS nuevos, cero cambios de snapshot**: todo lo nuevo de esta story viaja por REST; `services/batches.py` y `api/ws.py` no se tocan.
- **NO regenerar `types/api.ts`**: interfaces locales espejo (idiom vigente); el diferido 2-1 "migrate to generated types in one epic-wide pass" SIGUE diferido.
- **Diferidos que SIGUEN diferidos** (no arreglar "de paso"): 2-2 #2 (`batches.py:121` append race) y #4 (`ws.py:54` auth de socket abierto); 2-5 MEDIUM `telegram.py:111` y LOW `send_worker.py:398`/`:652`; 2-1 LOW `admin.py:466` (mapeo IntegrityError) y el copy del delete de /admin/gates (decisión del owner); 1-6 (tipos de error en OpenAPI).

### Diseño (decisiones registradas)

- **Historial lee por REST, no por WS:** architecture nombra `/api/sessions/{id}` en su lista REST y el WS es server→client solo para eventos vivos. El live-follow del detalle NO mete las filas del evento en la página: es un **refetch disparado por el store** (`live.sessionId === id` + cambios en los totales) — el port literal del legacy ("debounced refresh on each `respuesta` event"); REST es la fuente del detalle, el WS solo avisa que hay algo nuevo. Sin debounce explícito: el ritmo lo marca el bot (≥ intervalo de envío) y react-query dedupea.
- **El detalle entrega el dato COMPLETO (sin `_SNAPSHOT_ROWS`):** promesa registrada de 3.2 — el snapshot viaja recortado a 200 porque es por-reconexión, pero "el dato completo vive en Historial (3.3) y export (3.5)". `limit=None` en los listados; a escala MVP (NFR2) el JSON es trivial; el bulk real es del export 3.5.
- **Badge "En curso" = `is_active`** (decisión registrada de 3.1, models.py :322), NO "ligada a lote vivo": la sesión activa sigue "En curso" entre lotes (la captura queda armada — paridad legacy). A lo sumo una por tenant (índice parcial `uq_capture_sessions_one_active_per_tenant`).
- **El guard de borrado es por LOTE VIVO, no por is_active:** el AC 6 protege exactamente "the session bound to a live batch" — `get_live_batch(...).capture_session_id == id`. Borrar la sesión ACTIVA con el surface idle se permite (no hay lote que detener); el tenant queda sin activa hasta el próximo lote. Reply tardío a un lote cuya sesión murió: la atribución (camino 2) encuentra el lote con sesión NULL y `resolve_for_backfill` crea un fallback INACTIVO (o reúsa la activa si el gate coincide) — el dato borrado no "revive", solo las capturas nuevas aterrizan en la sesión nueva. Registrado y aceptado.
- **Borrado duro con CASCADE:** "the session and its rows are gone" (AC 5) — `session.delete()` y el FK CASCADE de `responses` hace el resto; `batches.capture_session_id` es SET NULL: la historia de lotes NO se borra (los contadores de lote son del lote). No hay soft-delete de sesiones (a diferencia de gates).
- **`clearSession` es un seed local confirmado por REST**, no un evento: mismo patrón que `seedFromBatch`. El tab que borró limpia sus paneles al instante; otros tabs reconcilian en su próximo snapshot (stale visual aceptado a MVP — el dato ya no existe en el server). Emitir un evento de sesión aquí adelantaría el diseño de `session.active` que pertenece a 3.4.
- **Renombrar sin guard de lote** (legacy verbatim: "renombrar is unguarded", project-context) y sin unicidad de nombres — dos sesiones pueden llamarse igual; el id estable es el de DB.
- **Modal de confirmación real para Eliminar** (el AC lo exige — a diferencia del confirm inline que 2.1 usó en admin/gates por UX-DR21): un solo nivel, copy verbatim, el error `session_in_use` se muestra DENTRO del modal sin cerrarlo (el operador decide: detener el lote o cancelar).
- **Agrupado client-side:** la API entrega lista plana newest-first; el agrupado por `gate_value` (orden de primera aparición ⇒ grupos por su sesión más nueva) es presentación pura. Sin paginación a escala MVP.
- **Fallback de nombre `YYYY-MM-DD HH:MM`** desde `created_at` (espejo de `nombre_bonito`, que embellecía el timestamp-folder legacy) — formateado en el cliente con padStart (idiom `formatTime` de response-views.tsx :30-36), determinista, sin locale.

### Código actual que vas a tocar (estado HOY @ ac61983, con anclas)

| Archivo | Hoy | Esta story |
| --- | --- | --- |
| `backend/app/db/repos/capture_sessions.py` | `get_active` :19, `create_active` :34, `resolve_for_batch` :59, `resolve_for_backfill` :74 — sin list/get-by-id/delete | + `list_for_tenant` / `get_for_tenant` / `delete` |
| `backend/app/db/repos/responses.py` | `_list_last`/`list_full`/`list_cc` :169-205 con `limit: int` obligatorio | `limit: int | None` (None ⇒ sin LIMIT) |
| `backend/app/errors.py` | termina en `batch_stopping` :232-238 | + bloque 3.3: `session_not_found`, `session_in_use` |
| `backend/app/api/sessions.py` | NO EXISTE (nombrado por el árbol de architecture) | nuevo: list/detail/rename/delete |
| `backend/app/main.py` | `include_router` :74-79 (health, auth, admin, gates, batches, ws) | + `sessions_router` |
| `backend/app/api/batches.py` | `_PG_INT_MAX` :40, `_controlled_batch` :201-217 (idioms a copiar) | SIN CAMBIOS |
| `backend/app/db/repos/batches.py` | `get_live_batch` :48-73 (LIVE_STATES, tenant-scoped), `get_batch` :75-88 (espejo del lookup tenant-scoped) | SIN CAMBIOS (solo se llama) |
| `backend/app/db/models.py` | `CaptureSession` :313-352 (`name` String(200) :343, docstring del badge :322), `Response` FKs CASCADE :393-395, `Batch.capture_session_id` SET NULL :222-224 | SIN CAMBIOS (cero migraciones — los FKs ya hacen el trabajo) |
| `backend/app/api/admin.py` | `_validate_gate_name` :356-367, `UpdateGateRequest` :386-398, `update_gate` :491-514, `delete_gate` :517-525 | SIN CAMBIOS (plantillas de validator/PATCH/DELETE) |
| `frontend/lib/ws.ts` | campos de sesión :59-67, `IDLE` :156-173, `seedFromBatch` :484-522 (el precedente del seed REST) | + `clearSession(sessionId)` |
| `frontend/app/(client)/sessions/page.tsx` | STUB :1-16 ("Story 3.3 builds the real session list") — su copy vacío ya es el del AC 7 | reescrito: lista agrupada + renombrar + eliminar |
| `frontend/app/(client)/sessions/[id]/page.tsx` | NO EXISTE (ruta nombrada por architecture/UX-DR17) | nuevo: detalle con vistas duales + live-follow |
| `frontend/components/sessions/response-views.tsx` | `CompletaPanel` :168, `FiltradaPanel` :193, `ResponseTabs` :222, props-driven a propósito :7-9, `PanelList` pinning :80-127, `formatTime` :30-36 | SIN CAMBIOS (reuso verbatim — esa reusabilidad fue requisito de diseño de 3.2) |
| `frontend/components/client-nav.tsx` | `ITEMS` :17-20, `active={pathname === item.href}` :115-117, `PILL_CLASS.paused` :34 (diferido 2-3 #2) | match por prefijo de ruta + fix `text-warning` |
| `frontend/lib/api.ts` | `api.get/post/patch/delete` :85-98 (ApiError con `code`) | SIN CAMBIOS (solo se consume) |
| `frontend/app/admin/gates/page.tsx` | interfaces locales :22-46, `validateCategoryName` :55-60, `DeleteGateAction` :818-884 (mutación + `err.message` + not_found⇒éxito) | SIN CAMBIOS (solo patrones a copiar) |
| `backend/tests/test_sessions.py` | NO EXISTE | nuevo (Task 7) |
| `backend/tests/conftest.py` | `ctx` :104, `reset_capture` :166, `fake_gateway` :191, `gate` :199, `client_user` :224 | SIN CAMBIOS (fixtures suficientes) |
| `_bmad-output/implementation-artifacts/deferred-work.md` | 2-3 client-nav.tsx:34 abierto | housekeeping Task 8 |

**Sin cambios:** `core/{capture,attribution,cc_extract,scheduler,send_worker,telegram,broadcaster}.py`, `services/batches.py` (el snapshot NO cambia de forma), `api/{ws,batches,admin,gates,auth,health}.py`, `db/models.py` (CERO migraciones), `config.py` (cero settings nuevos — regla 2.5), `deploy/*`, `frontend/types/api.ts` (NO regenerar), `frontend/app/(client)/page.tsx`, `components/batch/*`, `components/sessions/response-row.tsx`, legacy `core.py`/`app.py`/`static/`.

### Cumplimiento de arquitectura (no negociable)

- **REST plural, sin kebab:** `/api/sessions`, `/api/sessions/{id}` — ruta LITERAL de architecture ("REST: plural nouns — `/api/batches`, `/api/sessions/{id}`"); acciones CRUD puras (GET/PATCH/DELETE), sin verbos de sufijo. Errores SIEMPRE `{code, message}` con status con sentido (404/409/422). [Source: architecture.md#API-&-Communication-Patterns; #Error-contract]
- **Tenant scoping:** `tenant_id` SOLO de `user.tenant_id` (deps.py — "handlers never read tenant_id from request bodies"); todo lookup por id es tenant-scoped y el 404 jamás filtra existencia (idiom 2.3). El AC 8 es exactamente esto. [Source: architecture.md#Tenant-Scoping; #Enforcement-Guidelines]
- **Identificadores en inglés, copy en español tuteo** con términos de producto verbatim: "sesión de guardado" → `capture_session` en código (choque con auth session, traducción mandada); copy exacto: "En curso", "Cerrada", "Renombrar", "Eliminar", "¿Eliminar esta sesión? No se puede deshacer.", "Detén el lote antes de eliminar esta sesión.", "Todavía no tienes sesiones. Tu primer lote crea una.", "Esa sesión no existe." [Source: architecture.md#Code-Naming-Conventions; epics.md#Story-3.3; EXPERIENCE.md#Per-surface-states]
- **Árbol del repo:** `backend/app/api/sessions.py`, `frontend/app/(client)/sessions/page.tsx`, `frontend/app/(client)/sessions/[id]/page.tsx`, reuso de `frontend/components/sessions/` — todos nombrados por el Proposed Source Tree. [Source: architecture.md#Proposed-Source-Tree]
- **UX spines ganan a mocks:** fila de sesión = HeroUI row con heading + sub-línea mono muted + badge derecha (DESIGN :214); danger SOLO para Eliminar/destructivo (DESIGN :174); modal stack máx 1 (UX-DR10); nav exactamente Envío | Historial. Las vistas duales del detalle son las MISMAS de 3.2 — mono 11px data-row, pinning, empty states verbatim, todo heredado del reuso. [Source: DESIGN.md#Components, #Brand-&-Style; epics.md UX-DR10/DR11/DR16/DR17]

### Inteligencia de stories previas (3.2 + 3.1 + 2.x)

- **Esta story COBRA promesas dejadas a propósito:** los paneles de 3.2 son props-driven "para que 3.3 los reuse verbatim en el detalle de sesión — esa reusabilidad es requisito de diseño" (response-views.tsx :7-9); el stub de sessions/page.tsx dice "Story 3.3 builds the real session list" (:1-2) — borrar el comentario al cumplirlo; `CaptureSession.name` y su cap 200 existen desde 3.1 "Story 3.3 rename" (models.py :321); el dedup-precargado de las filas `'cc'` que el docstring de `Response` promete (:367) es de 3.4, no de aquí.
- **Lecciones de frontend (2.x/3.2):** copy de UI EXACTO al spec — los reviews comparan string a string; HeroUI v3 usa API compound — verificar contra los typings instalados de `@heroui/react` 3.1.0, no contra docs v2 (el ejercicio de Tabs/Select); UN solo store WS (`useSyncExternalStore`) — no crear un segundo socket; `npm run lint` antes de declarar verde; las mutaciones usan `err.message` del ApiError y tratan el not_found post-borrado como éxito (DeleteGateAction).
- **Lecciones de backend:** dicts exactos en asserts y actualizar tests COMPLETOS; atributos capturados antes de cerrar la sesión (MissingGreenlet 2.3 — aquí: materializar el detalle dentro de la sesión de request, que pydantic serializa después); los tests llaman `capture.process_incoming`/`send_worker.step()` directo (sin lifespan); `_PG_INT_MAX` en todo path-id (lección 2.1: ids fuera de int4 = 500 de asyncpg).
- **Semántica legacy que esta story traduce:** el "history browser" del SPA legacy (lista de prefijos → sesiones → completa/filtrada, live-follow con detach) se convierte en `/sessions` + `/sessions/[id]` + refetch-on-event; `renombrar` (max 200, unguarded) ya era REST en legacy (`/api/sesion/renombrar`); lo que muere a propósito: el botón "↻ Ver sesión actual" (el follow aquí es por id de ruta, no por modo attach/detach) y el warning "Verificá el prefijo" de sesiones sin meta.json (todas las sesiones DB tienen snapshots de gate — el caso legacy-sin-meta no existe en Postgres).
- **1.7/CI:** Conventional Commits con scope (`feat(backend,frontend): …`), rama `story/3.3-historial-sesiones`; push a main = deploy automático al VPS. Sin migraciones, sin claves de entorno nuevas.

### Estándares de testing

- Backend: `pytest` + `pytest-asyncio` (`loop_scope="session"`) + httpx `ASGITransport` contra la app real y el Postgres de dev; self-seed/self-clean (el CASCADE de tenant en `cleanup_users` ya se lleva `capture_sessions`/`responses` — verificado en 3.1); sin mocks de DB; un comportamiento por test; fixtures existentes (`ctx`/`gate`/`client_user`/`fake_gateway`) — no inventar otros. Capturas DIRECTO a `capture.process_incoming(IncomingReply(...))` (idiom test_attribution.py); lotes reales vía `POST /api/batches` + `send_worker.step()`.
- Los 404 se asertan con body exacto `{"code": "session_not_found", "message": "Esa sesión no existe."}` — los tres verbos, tres ids malos (desconocido / otro tenant / > int4).
- Frontend: SIN framework de tests (decisión diferida del proyecto — no instalar nada). Gates: `npx tsc --noEmit` + `npm run lint` + `npm run build`. La verificación de comportamiento (agrupado, modal, live-follow) es el smoke manual de Task 8.

### Notas de estructura del proyecto

- **Nuevos:** `backend/app/api/sessions.py`, `backend/tests/test_sessions.py`, `frontend/app/(client)/sessions/[id]/page.tsx` — cero migraciones, cero módulos core nuevos.
- **Modificados:** `backend/app/db/repos/{capture_sessions,responses}.py`, `backend/app/errors.py`, `backend/app/main.py`, `frontend/lib/ws.ts`, `frontend/app/(client)/sessions/page.tsx`, `frontend/components/client-nav.tsx`, `_bmad-output/implementation-artifacts/deferred-work.md`.
- Legacy `core.py`/`app.py`/`static/` congelados en la raíz — solo referencia de comportamiento. **🔒 JAMÁS leer contenido bajo `respuestas/`. JAMÁS tocar `.env` ni `anon.session`.**

### Referencias

- [Source: planning-artifacts/epics.md#Story-3.3 — ACs verbatim; UX-DR10/DR11 (nav y fila de sesión con acciones), UX-DR16 (empty states), UX-DR17 (route map verbatim); #Story-3.4/#Story-3.5/#Story-3.6 (fronteras: continuar, export, soporte cross-tenant NO son de aquí)]
- [Source: planning-artifacts/architecture.md#API-&-Communication-Patterns (REST `/api/sessions/{id}` literal, error contract); #Tenant-Scoping (404 sin filtrar existencia, tenant_id solo de la sesión); #Proposed-Source-Tree (`api/sessions.py`, `(client)/sessions/page.tsx`, `sessions/[id]/page.tsx`, `components/sessions/`); #Code-Naming-Conventions (capture_session)]
- [Source: ux-designs/ux-cc-2026-06-10/DESIGN.md :214 (session row: heading + sub-línea mono + badge accent/muted), :174 (danger solo destructivo), :190 (desktop: "Historial detail reuses the same dual panels"); EXPERIENCE.md :30-31/:70 (Historial y acciones de fila), :110 (empty Historial verbatim), :119 (live-follow del detalle: "detaching/browsing another session stops following"), :152-158 (flujo Historial→detalle)]
- [Source: implementation-artifacts/3-2-vistas-completa-filtrada-en-vivo-en-envio.md — paneles props-driven prometidos a 3.3; cap `_SNAPSHOT_ROWS` con "el dato completo vive en Historial 3.3 / export 3.5"; cerco que mantuvo `session.active` en 3.4]
- [Source: implementation-artifacts/3-1-captura-y-atribucion-de-respuestas-del-bot.md — badge En curso/Cerrada deriva de `is_active`; `name` String(200) puesto para el rename de 3.3; FKs CASCADE/SET NULL diseñados para que "la sesión es la dueña real"]
- [Source: implementation-artifacts/deferred-work.md#Story-2-3-review — MEDIUM client-nav.tsx:34 (pill 'En pausa') absorbido aquí; el resto SIGUE diferido]
- [Source: _bmad-output/project-context.md — 🔒 reglas: nunca leer respuestas/, nunca tocar .env/anon.session; "renombrar is unguarded"; history paths tenant-guarded; legacy meta.json/nombre_bonito (el fallback de nombre)]
- [Source: código actual @ ac61983 — backend/app/{api/{batches,admin,gates,deps}.py, db/{models.py, repos/{capture_sessions,responses,batches}.py}, services/batches.py, errors.py, main.py}, backend/tests/{conftest,test_attribution}.py, frontend/{lib/{ws,api}.ts, app/(client)/{page,sessions/page}.tsx, components/{client-nav.tsx, sessions/response-views.tsx}, app/admin/gates/page.tsx}]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Claude Code dev agent)

### Debug Log References

- Baseline @ ac61983 verificado: 196 passed antes de tocar nada.
- Gates finales: backend `pytest` → **202 passed** (196 baseline + 6 nuevos), `ruff check app/ tests/` → All checks passed, `mypy app` → Success (37 files). Frontend `npx tsc --noEmit` limpio, `npm run lint` 0 errors/0 warnings (tras `lint:fix` de 5 prettier wraps), `npm run build` OK (`/sessions` static, `/sessions/[id]` dynamic).

### Completion Notes List

- Backend exactamente según spec: repos (`list_for_tenant`/`get_for_tenant`/`delete` en capture_sessions; `limit: int | None` en responses con `order_by(id.asc())` directo cuando `None`), errores 3.3 (`session_not_found` 404 / `session_in_use` 409 con el copy del AC 6 verbatim), router `api/sessions.py` (GET lista plana newest-first, GET detalle sin cap con totales = `len()`, PATCH unguarded con `field_validator` ≤200, DELETE 204 con guard por LOTE VIVO — no por `is_active` — y CASCADE/SET NULL de los FKs). Registrado en `main.py` entre batches y ws. Cero migraciones (los FKs de 3.1 ya hacen el trabajo).
- `SessionResponseRow.status` se tipa `str | None` (espejo honesto de `Response.status`, que es nullable para filas 'cc') — en filas 'full' siempre viaja "ok"/"rejected", igual que el snapshot; el frontend lo estrecha a `"ok" | "rejected"` en su interface local (mismo trato que `SnapshotResponseRow`).
- HeroUI v3.1.0: API compound verificada contra los typings instalados ANTES del JSX (ejercicio 3.2/2.2). Se eligió `AlertDialog` (no `Modal`) para el confirm de Eliminar: su Backdrop default es NO dismissable + Esc deshabilitado — un destructivo exige acción explícita; controlado vía `isOpen`/`onOpenChange` (Root extiende `DialogTrigger` de react-aria).
- `fallbackName`/`SessionBadge` están duplicados en `sessions/page.tsx` y `sessions/[id]/page.tsx` (≈15 líneas): App Router NO permite exports extra en archivos `page.tsx` (rompe el type-check del build) y la story no autoriza módulos compartidos nuevos. Comentado en ambos lados.
- Detalle móvil: `ResponseTabs` se reusa VERBATIM (mandato del file-table "SIN CAMBIOS") y trae su `max-h-72` interno hardcodeado — ganó sobre el `max-h-[60vh]` sugerido en Task 6 (tocarlo habría modificado response-views.tsx). Desktop sí lleva `lg:max-h-[calc(100vh-12rem)]` vía prop `listClassName`.
- Live-follow implementado como refetch-by-invalidation observando `[live.responsesTotal, live.ccNew]` con guard `live.sessionId === id`; deps extra (`idParam`, `queryClient`, etc.) exigidas por exhaustive-deps — inofensivas, la invalidación se dedupea. Navegar fuera detiene el follow por construcción (cambia `id`).
- `clearSession` añadido a ws.ts como seed-local-confirmado-por-REST (patrón `seedFromBatch`); cero eventos WS nuevos, cero reducers tocados. También se llama en el camino `session_not_found`-como-éxito del delete (borrada en otro tab — limpiar es igual de honesto).
- client-nav: match por prefijo de ruta sólo para items ≠ "/" (Historial queda activo en `/sessions/[id]`; Envío sigue exact-only) + diferido 2-3 #2 absorbido (`text-warning-foreground` → `text-warning`), marcado RESOLVED en deferred-work.md.
- Copy de validación cliente en tuteo ("Ingresa un nombre." / "Máximo 200 caracteres.") — las páginas admin usan voseo pero el surface cliente es tuteo (architecture).
- Queda HUMANO: smoke manual en dev con credenciales reales (último sub-item de Task 8). No correr contra producción sin el OK de Richard.

### File List

**Nuevos:**
- `backend/app/api/sessions.py`
- `backend/tests/test_sessions.py`
- `frontend/app/(client)/sessions/[id]/page.tsx`

**Modificados:**
- `backend/app/db/repos/capture_sessions.py`
- `backend/app/db/repos/responses.py`
- `backend/app/errors.py`
- `backend/app/main.py`
- `frontend/lib/ws.ts`
- `frontend/app/(client)/sessions/page.tsx`
- `frontend/components/client-nav.tsx`
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/3-3-historial-listar-ver-detalle-renombrar-y-eliminar-sesiones.md` (esta story)
