---
baseline_commit: da0b7d7eba40cfc28ffde4c74ef28937f07bb9ea
---

# Story 3.5: Exportar resultados como .txt

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGÍA (decisión del owner 2026-06-11):** el término de producto para un prefijo es **"gate"** — DB, API, identificadores de código y todo el copy de UI (masculino: "el gate"). epics.md / architecture.md / docs de UX son anteriores al renombre y todavía dicen "prefijo/prefixes" — lee cada "prefijo" como "gate"; donde haya conflicto, gana "gate". En esta story: el "export reads rows → `.txt`" de architecture se lee contra el modelo ACTUAL — las filas son `responses` (kind `full`/`cc`) de una `capture_session` con snapshots `gate_value`/`gate_name`. "Completa", "Filtrada", "↓ .txt", "Envío", "Historial" son términos de producto verbatim y se quedan tal cual.

> **⚠️ DIFERIDOS: esta story NO absorbe ninguno.** Regla de siempre revisada contra deferred-work.md: ningún hallazgo ABIERTO vive en los archivos que esta story toca. Siguen diferidos: 2-2 MEDIUM `api/batches.py:121` (append race), 2-3 MEDIUM `ws.py:54` (auth de socket abierto), 2-5 MEDIUM `telegram.py:111` y LOW `send_worker.py:398`/`:652`, 2-1 LOW `admin.py:466`, 1-6/2-1 pase epic-wide de generated types (esta story NO introduce interfaces de API nuevas a mano — el helper de descarga no tipa payloads JSON). No arreglar nada "de paso".

## Story

As a client,
I want to download my complete and filtered views as .txt files,
So that I use my data outside the platform.

## Acceptance Criteria

1. **Given** a session with responses, **when** the client taps `↓ .txt` on a view, **then** the backend generates the file on the fly from rows (no cache) and the browser downloads it — one button per view (completa / filtrada).
2. **Given** the export buttons, **when** shown in Envío or Historial detail, **then** they work both during a live batch and on closed sessions.
3. **Given** any export request, **when** it resolves, **then** only the requesting tenant's own sessions are exportable.

## Tasks / Subtasks

### Backend (Tareas 1–2)

- [x] Task 1: `backend/app/services/exports.py` — módulo NUEVO (AC: 1)
  - [x] El archivo lo nombra architecture LITERALMENTE (`services/exports.py # .txt generation from rows`, :318; mapping F3 :371). Módulo PURO: construcción de strings, sin DB, sin FastAPI, sin I/O — los SELECTs los hace el router y le pasa las filas (más fácil de testear, mismo espíritu "core.py stays pure" del legacy).
  - [x] `completa_txt(rows: list[Response]) -> str` — paridad legacy VERBATIM con `guardar_respuesta` (core.py :144-147): cada revisión 'full' como `[YYYY-MM-DD HH:MM:SS] {text}\n\n` (bloque + línea en blanco, append en orden ascendente de `id` — el orden en que `list_full(limit=None)` ya entrega). Timestamp: `row.created_at.strftime("%Y-%m-%d %H:%M:%S")` — ver decisión "UTC tal cual" en Dev Notes. Cero filas ⇒ `""`. NO filtrar por status: la vista Completa muestra TODAS las revisiones (✅ y ❌, el texto ya trae sus glifos — capture deriva `status` DE los glifos del texto, capture.py :271-277) y el AC exporta "the complete view".
  - [x] `filtrada_txt(rows: list[Response]) -> str` — paridad legacy VERBATIM con la escritura de `filtrada.txt` (core.py :153-156): `"\n".join(textos) + "\n"` (un dato CC por línea, newline final); cero filas ⇒ `""`.
  - [x] `export_filename(capture_session: CaptureSession, view: str) -> str` → `f"{slug}-{capture_session.id}-{view}.txt"` (ej. gate `.zo`, sesión 42, filtrada ⇒ `zo-42-filtrada.txt`). Slug: port de `prefijo_slug` (core.py :38-40 — `lstrip(".")`, espacios→`_`) endurecido a ASCII: `re.sub(r"[^A-Za-z0-9_-]+", "_", gate_value.lstrip("."))` + `strip("_")`, fallback `"gate"` si queda vacío. **El endurecimiento es load-bearing:** el header `Content-Disposition` debe ser latin-1 (límite HTTP de starlette) y `_validate_gate_value` (api/admin.py :342-353) permite cualquier char imprimible no-espacio — un gate con ñ/emoji rompería la respuesta sin el regex.
  - [x] Docstring de módulo registrando las tres decisiones (paridad de formato legacy, filas = la vista, filename ASCII-safe).
- [x] Task 2: `backend/app/api/sessions.py` — `GET /{session_id}/export` (AC: 1, 2, 3)
  - [x] `@router.get("/{session_id}/export", response_class=PlainTextResponse)` con `view: Literal["completa", "filtrada"]` como query param — mapa del legacy `/api/respuesta/{prefijo}/{sesion}?tipo=completa|filtrada` (`tipo`→`view`, identificadores en inglés). FastAPI valida el Literal ⇒ 422 en cualquier otro valor (mismo trato que los 422 de `RenameSessionRequest`: la UI jamás construye un view inválido; api.ts normaliza el body no-contrato). Imports nuevos: `from typing import Literal`, `from fastapi.responses import PlainTextResponse`, `from app.services import exports`. OJO: NO importar `app.db.models.Response` (no hace falta y colisiona mentalmente con `fastapi.Response`).
  - [x] Cuerpo: (1) `target = await _require_session(session, user.tenant_id, session_id)` — SIN `for_update` (lectura pura); el trío 404 (id desconocido / de otro tenant / fuera de int4) cubre el AC 3 sin código nuevo. (2) `rows = await responses_repo.list_full(session, target.id, None)` o `list_cc(..., None)` según `view` — `limit=None` = los datos COMPLETOS ascendentes, mismo idiom del detail (:170-171); el docstring de `_list_last` (responses.py :178) ya prometía "the full data belongs to Historial and export" — esta story cobra la mitad export. (3) `content = exports.completa_txt(rows) | exports.filtrada_txt(rows)`. (4) `return PlainTextResponse(content, headers={"Content-Disposition": f'attachment; filename="{exports.export_filename(target, view)}"'})` — `PlainTextResponse` ya pone `text/plain; charset=utf-8`.
  - [x] **SIN guard de lote vivo** (AC 2: "during a live batch and on closed sessions" — ambos funcionan): mismo trato que renombrar ("renombrar is unguarded", paridad legacy registrada). SIN cache, SIN archivos en disco: SELECT + string por request — architecture :131 "generated on the fly from rows. No caching layer at MVP scale".
  - [x] Sin riesgo de colisión de rutas: `GET /{session_id}/export` tiene un segmento extra sobre `GET /{session_id}` — FastAPI no los confunde; declara la ruta junto al detail por legibilidad.
  - [x] Actualizar el docstring del módulo (:5 — "the export half of this module (`.txt`) is Story 3.5"): la promesa se cobra aquí.
  - [x] `errors.py` NO se toca: el único error propio es el 404 existente (`session_not_found`); view inválido es 422 de FastAPI.

### Frontend (Tareas 3–6)

- [x] Task 3: `frontend/lib/api.ts` — helper de descarga (AC: 1)
  - [x] Extraer el bloque `!res.ok` de `request` (:35-77 — normalización `{code,message}` con fallback `unknown_error`/"Ocurrió un error inesperado.", redirect `plan_expired` → `/expired`, redirect `password_change_required` → `/change-password`) a un helper interno `async function toApiError(res: Response): Promise<ApiError>` que `request` pasa a usar (`throw await toApiError(res)`) — comportamiento byte-a-byte, CERO cambios de semántica. La extracción existe para que la descarga comparta el contrato de error y los redirects de lockout sin duplicarlos.
  - [x] Nuevo export `downloadFile(path: string): Promise<void>`: `fetch(path, { credentials: "include" })` (GET sin body — NO mandar `Content-Type`); `!res.ok` ⇒ `throw await toApiError(res)`; ok ⇒ filename desde `Content-Disposition` (fetch same-origin: el header ES legible — las restricciones de exposición solo aplican en CORS; regex `/filename="([^"]+)"/`, fallback `"export.txt"`), `res.blob()` → `URL.createObjectURL` → `<a>` temporal con `download={filename}` + `click()` → `URL.revokeObjectURL` en `finally`. El backend es la ÚNICA autoridad del nombre — el cliente no lo deriva.
  - [x] Decisión registrada (ver Dev Notes): descarga por fetch+blob, NO `<a href>` directo — un 401/403/404 en un anchor navegaría a JSON crudo (dead-end, UX-DR16) y se saltaría los redirects de plan_expired/password_change.
- [x] Task 4: `frontend/components/sessions/response-views.tsx` — `↓ .txt` por panel (AC: 1, 2)
  - [x] Nuevo componente interno `ExportLink({ path }: { path: string })`: estado local pending/error; onClick → `downloadFile(path)`; label verbatim **`↓ .txt`**, pending **"Descargando…"**; error inline `err instanceof ApiError ? err.message : "No pudimos conectar. Intenta de nuevo."` en `<span className="text-[11px] text-danger">`. Botón plano consola-density (`<button type="button" className="font-mono text-[11px] text-accent disabled:opacity-50">`) — NO HeroUI `Button` aquí: el DESIGN lo llama "footer export link" y el botón plano evita verificar variantes de typings (lección 3.3); si el dev prefiere HeroUI, verificar la variante contra los typings INSTALADOS primero.
  - [x] `ResponsePanel` (:131-166): prop opcional `exportPath?: string`; cuando viene, render de un `<footer className="flex items-center justify-between border-t border-border px-3 py-2">` con el `ExportLink` (y el error a su lado). Sin prop ⇒ sin footer (CERO botones muertos).
  - [x] `CompletaPanel` (:168-191) y `FiltradaPanel` (:193-217): prop opcional `exportPath` reenviada a `ResponsePanel`. `ResponseTabs` (:222-271): props opcionales `exportPathCompleta`/`exportPathFiltrada` reenviadas a los paneles internos.
  - [x] **Decisión registrada (desviación de DESIGN :210):** en mobile el link va en el footer del panel DENTRO de cada `Tabs.Panel`, no en el strip de tabs — meterlo en el strip exige Tabs controlado (selectedKey) solo para saber qué vista exportar. El spine (EXPERIENCE :68) pide "one per view, both sections, live y cerrada" — cumplido con el footer en ambos breakpoints; "Spine wins on conflict" (EXPERIENCE cabecera).
  - [x] Actualizar el comentario de cabecera (:9 — "Export (`↓ .txt`) is Story 3.5 — no dead button here"): la promesa se cobra aquí.
- [x] Task 5: Envío — `frontend/app/(client)/page.tsx` (AC: 1, 2)
  - [x] `live.sessionId` (ws.ts :63) no-null ⇒ `const exportBase = `/api/sessions/${live.sessionId}/export``; pasar `exportPath={`${exportBase}?view=completa`}` al `CompletaPanel` (:85-90), `?view=filtrada` al `FiltradaPanel` (:91-96) y ambos al `ResponseTabs` mobile (:62-68). `sessionId === null` ⇒ `undefined` (sin sesión aún no hay nada que exportar — el botón no existe, no se deshabilita).
  - [x] NO condicionar a `isLive`: el export funciona DURANTE el lote (AC 2) y después (la sesión y `sessionId` sobreviven al lote — "capture stays armed", decisión 3.2). Cero cambios en el store/ws.ts: `sessionId` ya viaja en snapshot, `batch.state` y `session.active`.
- [x] Task 6: Historial detalle — `frontend/app/(client)/sessions/[id]/page.tsx` (AC: 1, 2)
  - [x] Mismos `exportPath` construidos con `data.id` en los dos paneles desktop (:273-286) y el `ResponseTabs` mobile (:289-295) — aquí SIEMPRE presentes (la sesión existe; cerrada o en curso, ambas exportan — AC 2; la "Cerrada" es el caso Flow 1 paso 9 / Flow 2 paso 5).
  - [x] Actualizar el comentario de cabecera (:8-9 — "Export `↓ .txt` is Story 3.5 — no dead button"): promesa cobrada.
  - [x] `frontend/app/(client)/sessions/page.tsx`: SOLO el comentario :9 ("Export `↓ .txt` is Story 3.5 — no dead buttons") — la LISTA no gana botones: las acciones de fila del spine son Renombrar/Continuar/Eliminar (EXPERIENCE :70); el export vive en las vistas duales.

### Tests + gates (Tareas 7–8)

- [x] Task 7: ampliar `backend/tests/test_sessions.py` (AC: todos los del lado servidor)
  - [x] Reusar los helpers locales existentes (`_post_batch` :65, `_drain` :72, `_capture_ok` :104, `_create_other_gate` :116) y las fixtures de conftest (`ctx`/`gate`/`client_user`/`fake_gateway`) — no inventar otros. `NOT_FOUND_BODY` :36 sirve tal cual para el verbo nuevo.
  - [x] **Filtrada exacta (AC 1):** lote + 2 capturas ✅ con valores CC distintos ⇒ `GET /api/sessions/{id}/export?view=filtrada` ⇒ 200, `content-type` empieza con `text/plain`, `content-disposition == f'attachment; filename="{slug}-{id}-filtrada.txt"'` (derivar el slug esperado del `value` del gate de la fixture), body EXACTO `"{cc1}\n{cc2}\n"` en orden de inserción (asserts exactos, no `in` — lección 3.3).
  - [x] **Completa exacta con revisiones (AC 1):** captura ✅ + edición del MISMO message_id (`edited=True` vía `capture.process_incoming`) ⇒ `?view=completa` trae AMBAS revisiones como bloques `[YYYY-MM-DD HH:MM:SS] {texto}\n\n` ascendentes (regex del timestamp o strftime de las filas reales leídas con `_response_rows` :91 — el formato es paridad legacy, no aproximarlo).
  - [x] **On-the-fly, sin cache (AC 1):** exportar ⇒ capturar un CC nuevo ⇒ re-exportar ⇒ el body contiene la fila nueva (dos GETs, el segundo más largo).
  - [x] **Vivo Y cerrada (AC 2):** con un lote SIN drenar (sending) el export responde 200 (sin guard); tras un lote de OTRO gate (`_create_other_gate`) la sesión vieja queda inactiva — su export sigue 200 con el MISMO contenido (cerrada exporta igual).
  - [x] **Sesión sin respuestas:** lote drenado sin capturas ⇒ ambos views 200 con body `""` (archivo vacío honesto — decisión registrada).
  - [x] **404 nunca filtra existencia (AC 3):** extender `test_not_found_is_identical_for_unknown_foreign_and_overflow_ids` (:543) al GET export — id desconocido, id de OTRO tenant e id > int4 ⇒ 404 `NOT_FOUND_BODY` idénticos.
  - [x] **View inválido ⇒ 422** (`?view=otracosa`) — FastAPI Literal; basta el status (el body es `{detail}` de FastAPI, no el contrato — decisión registrada). También `view` ausente ⇒ 422 (mismo validador).
  - [x] Suite COMPLETA verde (baseline al cierre de 3.4: **206 passed** — verificado ANTES de tocar nada; al cierre: **212 passed**).
- [x] Task 8: gates de verificación (todos los AC)
  - [x] Backend: `ruff check app/ tests/`, `mypy app`, `pytest` — verde completo (212 passed).
  - [x] Frontend: `npx tsc --noEmit` + `npm run lint` + `npm run build` — los tres verdes; sin framework de tests (decisión diferida del proyecto; NO inventar jest/vitest).
  - [x] deferred-work.md NO se toca (nada absorbido, nada nuevo salvo que el review lo diga).
  - [ ] (HUMAN — necesita credenciales reales) Smoke manual en dev: lote real con capturas → `↓ .txt` en Filtrada DURANTE el lote (el archivo baja con nombre `{slug}-{id}-filtrada.txt` y una línea por dato) → al terminar, `↓ .txt` en Completa → en Historial abrir una sesión Cerrada y exportar ambas vistas → verificar en el navegador que el contenido coincide con los paneles. **No correr contra producción sin el OK de Richard.**

## Dev Notes

### Qué NO es esta story (cerco de alcance)

- **Vista de soporte cross-tenant** (`/admin/tenants/[id]`, `for_tenant(id)`, audit log) → **Story 3.6**. Todo aquí es del propio tenant; el export NO se monta en superficies admin.
- **SIN export en la lista del Historial:** las acciones de fila del spine son Renombrar/Continuar/Eliminar (EXPERIENCE :70) — el export vive en las vistas duales (Envío + detalle). La lista solo actualiza un comentario.
- **SIN edición de contenido** (FR19), **SIN formatos extra** (CSV/zip/JSON — el AC dice `.txt`, punto), **SIN endpoint de export masivo**.
- **CERO migraciones, CERO settings nuevos** (regla 2.5 — nada de "timezone de export" configurable), **CERO eventos WS nuevos** (la descarga es REST puro), **CERO códigos de error nuevos** (`session_not_found` reusado; view inválido es 422), `main.py` SIN CAMBIOS (el router de sesiones ya está registrado desde 3.3).
- **CERO archivos en disco en el backend:** el legacy escribía `completa.txt`/`filtrada.txt` en `respuestas/`; el modelo nuevo genera el contenido al vuelo desde Postgres y NO persiste nada. 🔒 La regla "jamás leer `respuestas/`" sigue intacta — esta story ni se acerca.

### Diseño (decisiones registradas)

- **Ruta `GET /api/sessions/{session_id}/export?view=completa|filtrada`:** GET porque es una lectura segura e idempotente (el verbo-sufijo POST de architecture :196 es para ACCIONES; descargar no muta nada). `view` como query param = mapa directo del legacy `/api/respuesta/{prefijo}/{sesion}?tipo=completa|filtrada` (`tipo`→`view`). `Literal["completa","filtrada"]` valida en el borde — los valores son términos de producto y viajan tal cual en la URL.
- **Formatos = paridad legacy VERBATIM.** `filtrada`: un dato por línea + newline final (core.py :153-156 — exactamente lo que `cargar_cc_existentes` sabía releer). `completa`: bloques `[YYYY-MM-DD HH:MM:SS] {texto}\n\n` (core.py :144-147). El texto de cada revisión ya trae sus glifos ✅/❌ (capture deriva `status` DE los glifos, capture.py :271-277) — no inyectar marcadores extra. Filtrada NO lleva timestamps (paridad `filtrada.txt`; mismo motivo por el que `SessionCcRow` no los tiene).
- **Las filas SON la vista:** `list_full(limit=None)` / `list_cc(limit=None)` ascendentes — exactamente lo que pinta el detalle del Historial (:170-171). Completa exporta TODAS las revisiones (incl. ❌ y ediciones re-capturadas) porque eso ES la vista Completa del modelo nuevo; el legacy solo guardaba ✅ en disco, pero el AC exporta "the complete view", y la vista muestra todo. Decisión consciente, no un descuido.
- **Timestamps en UTC tal cual están almacenados** (`created_at` es `DateTime(timezone=True)`, server_default `now()` — models.py :347-349): `strftime("%Y-%m-%d %H:%M:%S")` sin sufijo de zona. El legacy escribía hora local del server; la UI pinta hora local del navegador — pueden diferir y se acepta a escala MVP: no existe setting de timezone y crear uno viola la regla "CERO settings" (2.5). Documentado en el docstring de `completa_txt`.
- **Archivo vacío para sesión sin filas** (200, body `""`): honesto y simple — en el legacy el archivo no existía, pero un 404/409 aquí confundiría "sesión sin datos" con "sesión inexistente" y rompería el trío 404 del AC 3.
- **Filename `{slug}-{session_id}-{view}.txt`, autoridad ÚNICA el backend** (Content-Disposition): el id de sesión desambigua (los nombres amistosos se repiten y pueden tener cualquier char); el slug es el port ASCII-endurecido de `prefijo_slug`. El frontend LEE el header (fetch same-origin expone todos los headers) con fallback `export.txt` — jamás deriva el nombre por su cuenta.
- **Descarga por fetch+blob, NO anchor directo:** un `<a href>` con 401/403/404 navegaría al JSON de error (dead-end, prohibido por UX-DR16) y NO pasaría por los redirects de `plan_expired`/`password_change_required` que `lib/api.ts` garantiza en cada llamada. `downloadFile` comparte `toApiError` con `request` — el contrato `{code, message}` y los lockouts aplican también a la descarga. El cookie httpOnly viaja solo (`credentials: "include"`, mismo request() idiom); en dev el rewrite de next.config (:12-22) proxya `/api` a uvicorn y en prod Caddy rutea `/api` directo — same-origin en ambos, y el middleware de Next EXCLUYE `/api` (matcher), así que la descarga nunca quema un round-trip `/me`.
- **Sin guard de lote vivo:** AC 2 lo exige explícitamente ("work both during a live batch and on closed sessions"). Es el mismo carril que renombrar (unguarded, paridad legacy registrada) — solo continuar/eliminar guardan.
- **Botones sin estado muerto:** en Envío los `exportPath` solo existen con `live.sessionId` no-null (antes de la primera sesión no hay nada que exportar — el botón no se renderiza); en el detalle siempre existen. Nunca un botón deshabilitado-para-siempre.
- **El 422 de view inválido NO cumple el contrato `{code,message}` y se acepta:** es el mismo trato que TODOS los 422 de validación del proyecto (`RenameSessionRequest`, gates) — la UI jamás construye un view inválido y `api.ts` normaliza el body no-contrato a "Ocurrió un error inesperado." si algún día pasa.

### Código actual que vas a tocar (estado HOY @ da0b7d7, con anclas)

| Archivo | Hoy | Esta story |
| --- | --- | --- |
| `backend/app/services/exports.py` | NO EXISTE | NUEVO: `completa_txt`, `filtrada_txt`, `export_filename` (módulo puro) |
| `backend/app/api/sessions.py` | docstring :5 (promesa export), router :35, `_PG_INT_MAX` :37, `_require_session` :116-136, detail :157-187 (`limit=None` :170-171), rename :190, continue :206, delete :261 | + `GET /{session_id}/export` (+ imports `Literal`, `PlainTextResponse`, `exports`); docstring :5 actualizado |
| `backend/app/db/repos/responses.py` | `_list_last` :169-190 (docstring :178 promete "export"), `list_full` :193, `list_cc` :201, KIND_FULL/KIND_CC :19-20 | SIN CAMBIOS (solo se llama) |
| `backend/app/db/models.py` | `CaptureSession` :313-352 (`gate_value` String(20) :341, `name` :343, `created_at` tz-aware :347-349), `Response` :355-411 | SIN CAMBIOS (CERO migraciones) |
| `backend/app/errors.py` | termina en `session_conflict` :279-287 | SIN CAMBIOS (404 reusado, view inválido = 422) |
| `frontend/lib/api.ts` | `request` :24-83 (normalización+redirects :35-77), objeto `api` :85-98 | + helper interno `toApiError` (extracción sin cambio de semántica) + export `downloadFile` |
| `frontend/components/sessions/response-views.tsx` | cabecera :9 (promesa export), `PanelList` :80-127, `ResponsePanel` :131-166, `CompletaPanel` :168-191, `FiltradaPanel` :193-217, `ResponseTabs` :222-271 | + `ExportLink` + prop `exportPath` (+ `exportPathCompleta`/`exportPathFiltrada` en Tabs); cabecera actualizada |
| `frontend/app/(client)/page.tsx` | `useLiveBatch` :30, `ResponseTabs` mobile :62-68, paneles desktop :85-96 | + `exportPath` desde `live.sessionId` (oculto si null) |
| `frontend/app/(client)/sessions/[id]/page.tsx` | cabecera :8-9 (promesa export), paneles :273-286, Tabs :289-295, `data.id` disponible :214 | + `exportPath` con `data.id`; cabecera actualizada |
| `frontend/app/(client)/sessions/page.tsx` | cabecera :9 ("Export `↓ .txt` is Story 3.5") | SOLO el comentario (la lista NO gana botones) |
| `frontend/lib/ws.ts` | `sessionId` :63 en `LiveBatchState` :45 | SIN CAMBIOS (solo se consume) |
| `backend/tests/test_sessions.py` | `NOT_FOUND_BODY` :36, `events` :48, `_post_batch` :65, `_drain` :72, `_response_rows` :91, `_capture_ok` :104, `_create_other_gate` :116, trío 404 :543 | + tests export (Task 7), trío extendido al GET export, docstring del módulo |
| `backend/app/api/admin.py` | `_validate_gate_value` :342-353 (referencia: qué chars puede traer `gate_value`) | SIN CAMBIOS |

**Sin cambios:** `core/{capture,attribution,cc_extract,scheduler,send_worker,telegram,broadcaster}.py`, `services/{batches,auth,plans,users}.py`, `api/{ws,batches,admin,gates,auth,health}.py`, `db/*` (cero migraciones), `main.py`, `config.py`, `deploy/*`, `frontend/components/batch/*`, `frontend/components/sessions/response-row.tsx`, `frontend/components/client-nav.tsx`, `frontend/types/api.ts` (NO regenerar — diferido 2-1 vigente), legacy `core.py`/`app.py`/`static/`.

### Cumplimiento de arquitectura (no negociable)

- **`services/exports.py` y la mitad export de `api/sessions.py` son nombrados LITERALES de architecture** (:293 "`sessions.py # /api/sessions (capture sessions, export .txt)`", :318 "`exports.py # .txt generation from rows`", :337 frontend detail "…, export", :371 mapping F3). Esta story los estrena. [Source: architecture.md#Complete-Project-Structure]
- **"Exports: `.txt` generated on the fly from rows (FR18). No caching layer at MVP scale"** — literal; nada de archivos temporales ni memoización. [Source: architecture.md :131]
- **Tenant scoping con exports nombrados explícitamente:** "every read/write path must be tenant-scoped (data, sessions, progress events, **exports**)" (:57). `tenant_id` SOLO de `user.tenant_id`; lookup tenant-scoped con el trío de 404 que no filtra existencia. [Source: architecture.md#Tenant-Scoping; #Enforcement-Guidelines]
- **REST:** lectura = GET sobre el recurso (`/api/sessions/{id}/export`); errores `{code, message}` con status con sentido (404) — el 422 de validación de FastAPI es la excepción aceptada en TODO el proyecto. [Source: architecture.md#API-&-Communication-Patterns]
- **Identificadores en inglés, copy en español tuteo:** `export_session`/`downloadFile`/`exportPath`/`view` en código; copy verbatim: **"↓ .txt"**, **"Descargando…"**, fallback "No pudimos conectar. Intenta de nuevo.". [Source: architecture.md#Code-Naming-Conventions]
- **UX spines:** export disponible en ambas secciones, un botón por vista, vivo y cerrada (EXPERIENCE :68); Flow 1 paso 9 (`↓ .txt` en Filtrada al terminar el lote) y Flow 2 paso 5 (exportar la `filtrada.txt` extendida tras continuar) son los caminos a smoke-testear. La colocación exacta (footer vs strip) es DESIGN-level y la desviación queda registrada — "Spine wins on conflict". [Source: EXPERIENCE.md :31, :68, :146, :156; DESIGN.md :210]

### Inteligencia de stories previas (3.4 + 3.3 + 3.2)

- **Esta story COBRA cuatro promesas explícitas:** el docstring de `api/sessions.py` (:5 "the export half of this module (`.txt`) is Story 3.5"), la cabecera de `response-views.tsx` (:9 "no dead button here"), la del detalle (`sessions/[id]/page.tsx` :8-9) y la de la lista (`sessions/page.tsx` :9). Actualízalas al cumplirlas — mismo ritual que 3.4 con las suyas.
- **Lecciones 3.3/3.4:** los 404 se asertan con body exacto y el trío de ids malos se extiende a CADA verbo nuevo (aquí el GET export); asserts de contenido exactos, no `in`; copy de UI string-a-string contra el spec (los reviews comparan verbatim); HeroUI v3 se verifica contra los typings INSTALADOS antes de usar una variante nueva (por eso el ExportLink es un `<button>` plano); `npm run lint` antes de declarar verde.
- **Lecciones 3.1/3.2:** los paneles son props-driven a propósito (cero lecturas de store dentro) — el `exportPath` entra por props exactamente igual que rows/totales; `sessionId` del store es la llave de Envío y sobrevive al lote ("capture stays armed", "counters never reset"); el detalle ya pinta los datos COMPLETOS por REST (`limit=None`) — el export reusa esos mismos SELECTs.
- **Semántica legacy que esta story traduce:** `/api/respuesta/{prefijo}/{sesion}?tipo=completa|filtrada` (leía el archivo del disco para MOSTRARLO; descargar no existía — el operador copiaba) → `GET /api/sessions/{id}/export?view=` con `Content-Disposition: attachment` (la descarga del navegador ES la mejora de FR18). Los formatos de archivo, en cambio, son paridad estricta: lo que el operador recibía en `completa.txt`/`filtrada.txt` es lo que baja.
- **1.7/CI:** Conventional Commits con scope (`feat(backend,frontend): …`), rama `story/3.5-exportar-txt`; push a main = deploy automático al VPS. Sin migraciones, sin claves de entorno nuevas.

### Estándares de testing

- Backend: `pytest` + `pytest-asyncio` (`loop_scope="session"`) + httpx `ASGITransport` contra la app real y el Postgres de dev; self-seed/self-clean (el CASCADE de tenant en `cleanup_users` se lleva `capture_sessions`/`responses`); sin mocks de DB; un comportamiento por test. Capturas DIRECTO a `capture.process_incoming(IncomingReply(...))`; lotes reales vía `POST /api/batches` + `send_worker.step()`.
- Los bodies de export se asertan EXACTOS (string completo, headers incluidos); los 404 con `NOT_FOUND_BODY`; el filename esperado se construye con la misma regla del slug a partir del gate de la fixture (no hardcodear un slug que la fixture no garantiza — el value es `f".h{uuid}..."` en `_create_other_gate` y el del fixture `gate` el que sea: derivarlo).
- Frontend: SIN framework de tests (decisión diferida — no instalar nada). Gates: `npx tsc --noEmit` + `npm run lint` + `npm run build`. La verificación de comportamiento (descarga real del navegador, nombre del archivo, contenido vs paneles) es el smoke manual de Task 8.

### Notas de estructura del proyecto

- **Nuevos:** `backend/app/services/exports.py` (único archivo nuevo — lo nombra architecture).
- **Modificados:** `backend/app/api/sessions.py`, `backend/tests/test_sessions.py`, `frontend/lib/api.ts`, `frontend/components/sessions/response-views.tsx`, `frontend/app/(client)/page.tsx`, `frontend/app/(client)/sessions/[id]/page.tsx`, `frontend/app/(client)/sessions/page.tsx` (solo comentario).
- Legacy `core.py`/`app.py`/`static/` congelados en la raíz — solo referencia de comportamiento. **🔒 JAMÁS leer contenido bajo `respuestas/`. JAMÁS tocar `.env` ni `anon.session`.**

### Referencias

- [Source: planning-artifacts/epics.md#Story-3.5 (:714-733) — los 3 ACs verbatim; FR18 (:47, :164); #Story-3.6 (frontera: soporte cross-tenant NO es de aquí)]
- [Source: planning-artifacts/architecture.md :131 (export on the fly, no cache), :57 (tenant isolation incluye exports), :195-196 (convenciones REST), :293/:318/:337/:371 (archivos nombrados literales), #Code-Naming-Conventions]
- [Source: ux-designs/ux-cc-2026-06-10/EXPERIENCE.md :31 (detail incluye export), :68 (Export button: one per view, both sections, live y cerrada), :146 (Flow 1 paso 9), :156 (Flow 2 paso 5); DESIGN.md :210 (export en el strip / footer link — desviación registrada)]
- [Source: implementation-artifacts/3-4-continuar-...md — formato de story, ritual de promesas, trío 404 por verbo, asserts exactos, fixture `events`, baseline 206 passed]
- [Source: implementation-artifacts/3-3-historial-...md — `_require_session`, detail `limit=None`, "the full data belongs to Historial and export", duplicación aceptada en pages]
- [Source: implementation-artifacts/3-2-vistas-...md — paneles props-driven reutilizables, `sessionId` en el store, capture armada entre lotes]
- [Source: implementation-artifacts/deferred-work.md — revisado: NINGÚN hallazgo abierto en los archivos de esta story; 2-2 batches.py:121, 2-3 ws.py:54, 2-5 telegram.py:111 / send_worker.py:398/:652, 2-1 admin.py:466 y el pase de generated types SIGUEN diferidos]
- [Source: _bmad-output/project-context.md — :49 "History paths guarded… keep this on any new history endpoint" (el equivalente moderno es el lookup tenant-scoped + trío 404); 🔒 reglas respuestas//.env/anon.session]
- [Source: código actual @ da0b7d7 — backend/app/{api/{sessions,admin}.py, db/{models.py, repos/responses.py}, services/batches.py, errors.py}, backend/tests/test_sessions.py, frontend/{lib/{api,ws}.ts, components/sessions/{response-views,response-row}.tsx, app/(client)/{page,sessions/page,sessions/[id]/page}.tsx, middleware.ts, next.config.mjs}; legacy core.py :38-40/:137-157]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Fable 5)

### Debug Log References

- Baseline verificado antes de tocar nada: `pytest` ⇒ 206 passed (coincide con el cierre de 3.4).
- Gates al cierre: backend `ruff check app/ tests/` limpio, `mypy app` limpio (38 archivos), `pytest` ⇒ **212 passed** (206 baseline + 6 tests nuevos de export). Frontend: `npx tsc --noEmit`, `npm run lint` y `npm run build` los tres verdes (el warning "middleware → proxy" de Next preexiste, no es de esta story).

### Completion Notes List

- **Backend tal cual la spec:** `services/exports.py` nuevo (módulo puro: `completa_txt`, `filtrada_txt`, `export_filename`; docstring registra las tres decisiones — paridad legacy verbatim, filas = la vista, filename ASCII-safe con fallback `"gate"`). `GET /api/sessions/{id}/export?view=completa|filtrada` declarado junto al detail, `Literal` ⇒ 422, `_require_session` sin `for_update` (el trío 404 cubre AC 3 sin código nuevo), `list_full/list_cc(limit=None)`, `PlainTextResponse` + `Content-Disposition: attachment`. Sin guard de lote vivo (AC 2), sin cache, sin archivos en disco, `errors.py`/`models.py`/`responses.py`/`main.py` intactos, cero migraciones.
- **Frontend tal cual la spec:** `toApiError` extraído de `request` byte-a-byte (normalización + redirects `plan_expired`/`password_change_required` compartidos); `downloadFile` por fetch+blob con filename desde `Content-Disposition` (fallback `export.txt`, `revokeObjectURL` en `finally`). `ExportLink` como `<button>` plano (consola-density, sin HeroUI — lección 3.3) con copy verbatim `↓ .txt` / "Descargando…" / fallback "No pudimos conectar. Intenta de nuevo."; footer opcional en `ResponsePanel` (sin `exportPath` ⇒ sin footer, cero botones muertos); props reenviadas por `CompletaPanel`/`FiltradaPanel`/`ResponseTabs` (mobile: footer dentro de cada `Tabs.Panel` — desviación de DESIGN registrada en la story). Envío: paths solo con `live.sessionId` no-null, NO condicionados a `isLive`; detalle: siempre presentes con `data.id`; la lista solo actualizó su comentario.
- **Tests (6 nuevos + trío extendido):** filtrada exacta con headers exactos (slug derivado del value del fixture, nunca hardcodeado), completa con revisión editada (`edited=True`, formato construido con `strftime` de las filas reales), on-the-fly sin cache (dos GETs), vivo y cerrada (lote sending sin guard + sesión desactivada por otro gate exporta el MISMO contenido), sesión sin filas ⇒ `""` en ambos views, view inválido/ausente ⇒ 422, y el trío 404 (`unknown`/foreign/overflow) extendido al GET export dentro del test existente. Asserts exactos, no `in`.
- **Promesas cobradas (las cuatro):** docstring de `api/sessions.py`, cabecera de `response-views.tsx`, cabecera del detalle y comentario de la lista — todas actualizadas al cumplirse.
- **Diferidos:** nada absorbido, nada nuevo; `deferred-work.md` sin tocar. El smoke manual de Task 8 queda para humano (necesita credenciales Telegram reales; no correr contra producción sin el OK de Richard).

### File List

- `backend/app/services/exports.py` (NUEVO — .txt generation from rows, módulo puro)
- `backend/app/api/sessions.py` (endpoint `GET /{session_id}/export` + imports + docstring actualizado)
- `backend/tests/test_sessions.py` (6 tests de export + trío 404 extendido + docstring del módulo)
- `frontend/lib/api.ts` (`toApiError` extraído + export `downloadFile`)
- `frontend/components/sessions/response-views.tsx` (`ExportLink` + `exportPath`/`exportPathCompleta`/`exportPathFiltrada` + cabecera actualizada)
- `frontend/app/(client)/page.tsx` (exportPaths desde `live.sessionId`, ocultos si null)
- `frontend/app/(client)/sessions/[id]/page.tsx` (exportPaths con `data.id` + cabecera actualizada)
- `frontend/app/(client)/sessions/page.tsx` (solo comentario de cabecera)
- `_bmad-output/implementation-artifacts/3-5-exportar-resultados-como-txt.md` (esta story: tasks, dev record, status review)
