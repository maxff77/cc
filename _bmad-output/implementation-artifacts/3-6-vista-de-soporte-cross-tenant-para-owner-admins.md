---
baseline_commit: 27f3170641b86c1ee8d6b501a1c76f509f816f7d
---

# Story 3.6: Vista de soporte cross-tenant para owner/admins

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGÍA (decisión del owner 2026-06-11):** el término de producto para un prefijo es **"gate"** — DB, API, identificadores de código y todo el copy de UI (masculino: "el gate"). epics.md / architecture.md / docs de UX son anteriores al renombre y todavía dicen "prefijo/prefixes" — lee cada "prefijo" como "gate"; donde haya conflicto, gana "gate". En esta story: el "cross-tenant session viewer" de UX-DR18 y el Flow 5 de EXPERIENCE se leen contra el modelo ACTUAL — las sesiones son `capture_sessions` con snapshots `gate_value`/`gate_name` y las filas son `responses` (kind `full`/`cc`). "Completa", "Filtrada", "Sesiones", "En curso", "Cerrada" son términos de producto verbatim y se quedan tal cual. La ruta de UX `/admin/prefixes` ya existe como `/admin/gates`.

> **⚠️ DIFERIDOS: esta story NO absorbe ninguno.** Regla de siempre revisada contra deferred-work.md: el ÚNICO hallazgo abierto que vive en un archivo tocado es **2-1 LOW `admin.py:466` (y `:509`)** — mapeo IntegrityError→gate_exists en el CRUD de gates. **NO se absorbe, decisión registrada:** esta story solo APPENDEA una sección nueva al final de `admin.py` (la vista de soporte, lectura pura + un INSERT de auditoría — sin IntegrityError que mapear) y jamás toca las líneas del CRUD de gates; el fix exige inspección del nombre de constraint en `exc.orig` + tests de carrera que no comparten nada con este alcance. Siguen diferidos: 2-2 MEDIUM `api/batches.py:121` (append race), 2-3 MEDIUM `ws.py:54` (auth de socket abierto), 2-5 MEDIUM `telegram.py:111` y LOW `send_worker.py:398`/`:652`, 1-6/2-1 pase epic-wide de generated types (esta story sigue el idiom de interfaces espejo a mano de admin/users — el pase es de OTRA story). No arreglar nada "de paso".

## Story

As an admin or owner,
I want to view any client's sessions read-only,
So that I can support clients from their own data view.

## Acceptance Criteria

1. **Given** `/admin/tenants/[id]`, **when** an admin or owner opens it, **then** the target client's sessions list and detail render read-only, reusing the same dual-view component.
2. **Given** a cross-tenant read, **when** it executes, **then** it goes through the explicit `for_tenant(id)` support path and is audit-logged — the only place tenant isolation is intentionally crossed.
3. **Given** a client, **when** they request `/admin/tenants/[id]`, **then** middleware redirects them away.
4. **Given** a client with no sessions, **when** the support view renders, **then** it shows "Este cliente no tiene sesiones."

## Tasks / Subtasks

### Backend (Tareas 1–4)

- [x] Task 1: `backend/app/db/models.py` — modelo `AuditLog` + migración (AC: 2)
  - [x] La tabla la nombra architecture LITERALMENTE en el listing de models.py (:309 — "...responses, auth_sessions, **audit_log**"). Esta story la estrena: hasta hoy NO existe nada de auditoría en el modelo (verificado @ baseline — `grep audit` en `app/` y `migrations/` devuelve cero filas de DB).
  - [x] `class AuditLog(Base)` al final de models.py (`__tablename__ = "audit_log"`): `id` PK; `actor_user_id: Mapped[int | None]` FK `users.id` **ondelete SET NULL** nullable + index (el registro sobrevive a la baja del admin que miró); `tenant_id: Mapped[int]` FK `tenants.id` **ondelete CASCADE** + index (el tenant OBJETIVO del cruce — decisión registrada: el trail es de soporte, muere con el tenant; esto además mantiene el `cleanup_users` de los tests sin tocar); `action: Mapped[str]` String(40) (snake_case, p. ej. `support_sessions_list`); `capture_session_id: Mapped[int | None]` **SIN FK, nullable** (decisión registrada: es una referencia histórica — el registro de auditoría NO debe morir ni anularse cuando el cliente borra su sesión; hard-delete de 3.3 existe); `created_at` tz-aware `server_default=func.now()` (idiom de todas las tablas). Docstring del modelo registrando las tres decisiones (SET NULL / CASCADE / sin FK).
  - [x] Migración Alembic NUEVA (`alembic revision -m "audit log"` desde `backend/`, venv activo): hand-written espejada en models.py "so later autogenerates diff empty" (idiom literal de `2faec0509cb8`). `down_revision` = la head ACTUAL — hoy `2faec0509cb8` (capture_sessions and responses); confírmalo con `alembic heads` antes de escribir. `upgrade()` crea la tabla + índices; `downgrade()` la tira. Aplicar con `alembic upgrade head` contra el Postgres de dev.
- [x] Task 2: repos — `backend/app/db/repos/audit.py` NUEVO + `users.py` ampliado (AC: 2)
  - [x] `db/repos/audit.py`: module note declarando que el ÚNICO escritor es la ruta de soporte de `api/admin.py` (architecture :248 — "Owner/admin cross-tenant access goes through explicit `for_tenant(id)` support paths, audit-logged"); ORM puro, **flush not commit** — el caller es dueño de la transacción (idiom de todos los repos). Una sola función: `async def record(session, *, actor_user_id: int, tenant_id: int, action: str, capture_session_id: int | None = None) -> AuditLog` — construye, `session.add`, `await session.flush()`, return.
  - [x] `db/repos/users.py` — `async def get_user_by_tenant(session: AsyncSession, tenant_id: int) -> User | None`: el user del tenant (tenant-per-user — `create_account` crea "a fresh tenant named after the email (one tenant per user)", services/users.py :36-46; `seed_user` de los tests igual, conftest :81-93). `select(User).where(User.tenant_id == tenant_id).order_by(User.id)` + `.first()` (el order_by es cinturón por si algún día un tenant tuviera más de un user). GLOBAL/no tenant-scoped — mismo carril documentado del módulo ("an admin manages all clients"); colócala junto a `get_user_by_id` (:86).
- [x] Task 3: `backend/app/errors.py` — `tenant_not_found()` (AC: 1, 2)
  - [x] Sección nueva `# --- Codes this story (3.6) defines ---` al final (:288): `tenant_not_found()` → 404, code `tenant_not_found`, message **"Ese cliente no existe."** (idiom `session_not_found` :244-251: id desconocido, tenant cuyo user NO es client e id fuera de int4 responden IDÉNTICO — la existencia del tenant del owner/admins jamás se filtra a quien sondee ids).
- [x] Task 4: `backend/app/api/admin.py` — sección "Cross-tenant support view (Story 3.6)" al FINAL del archivo (AC: 1, 2, 4)
  - [x] Comentario de sección (idiom de las secciones 1.5/2.1/2.2 del archivo) declarando: este es EL ÚNICO lugar del sistema donde un handler pasa a los repos un `tenant_id` que NO sale de `user.tenant_id` sino del PATH — el cruce intencional de architecture (:248, :365); cada lectura queda auditada en `audit_log` ANTES de servir datos. Actualizar también el docstring del módulo (:1-10) mencionando la vista de soporte.
  - [x] Imports nuevos: `from app.api.sessions import SessionDetailOut, SessionListResponse, SessionOut, session_to_out` (ver subtarea de rename), `from app.db.repos import audit as audit_repo`, `from app.db.repos import capture_sessions as capture_sessions_repo`, `from app.db.repos import responses as responses_repo`, `from app.errors import tenant_not_found` (sumar al import existente), `import logging` + `logger = logging.getLogger(__name__)` (idiom send_worker).
  - [x] **En `api/sessions.py`: renombrar `_session_out` → `session_to_out`** (público, :109-117) — espejo EXACTO del precedente `gate_to_out` ("Shared Gate → GateOut mapper (also used by the public gates router)", admin.py :415-420): el mapper compartido vive donde viven los schemas y el otro router lo importa. Actualizar los 4 call sites internos de sessions.py y el docstring del módulo (:14-15 — "the cross-tenant support view is Story 3.6, not here" → apuntar a `api/admin.py`; promesa cobrada).
  - [x] Helper `async def _require_client_tenant(session, tenant_id) -> User`: (1) `if not 0 < tenant_id <= _PG_INT_MAX: raise tenant_not_found()` (reusa el `_PG_INT_MAX` :339 del módulo); (2) `target = await users_repo.get_user_by_tenant(session, tenant_id)`; (3) `if target is None or target.role != "client": raise tenant_not_found()` — el objetivo es un CLIENTE (AC: "any client's sessions"; el copy del vacío dice "Este cliente"); sondear el tenant del owner o de un admin responde IGUAL que uno inexistente. Devuelve el User target (su `email` alimenta el header de la UI).
  - [x] `GET /tenants/{tenant_id}/sessions` → response model NUEVO inline `SupportSessionsResponse(BaseModel)`: `tenant_id: int`, `email: str`, `items: list[SessionOut]`, `total: int`. Cuerpo: `actor: User = Depends(require_admin_or_owner)` (el module-level singleton :46 — admin Y owner, AC 1; un client recibe 403 `forbidden` del gate, la frontera de seguridad REAL detrás del redirect de middleware); `target = await _require_client_tenant(...)`; `sessions = await capture_sessions_repo.list_for_tenant(session, target.tenant_id)` — **el support path `for_tenant(id)` LITERAL de architecture: la función ya se llama así** (repos/capture_sessions.py :34-47) y aquí recibe el tenant del path, no el del actor; `await audit_repo.record(session, actor_user_id=actor.id, tenant_id=target.tenant_id, action="support_sessions_list")`; `await session.commit()` — **fail-closed registrado:** el commit del audit va ANTES del return; si la auditoría no puede escribirse, la lectura NO se sirve (el AC dice "is audit-logged", sin registro no hay datos); `logger.info("event=support_view action=sessions_list actor=%s role=%s tenant=%s total=%s", ...)` (idiom estructurado `event=` de send_worker :298). Return con `items=[session_to_out(s) for s in sessions]`, newest first (el repo ya ordena `id.desc()`).
  - [x] `GET /tenants/{tenant_id}/sessions/{session_id}` → `response_model=SessionDetailOut` (el shape de 3.3 VERBATIM — los mappers del frontend 3.2/3.3 sirven tal cual). Cuerpo: mismo gate + `_require_client_tenant`; luego `if not 0 < session_id <= _PG_INT_MAX: raise session_not_found()` y `target_session = await capture_sessions_repo.get_for_tenant(session, target.tenant_id, session_id)` (el SEGUNDO support path nombrado; `None` ⇒ `session_not_found()` — una sesión de OTRO tenant distinto al target 404ea idéntica, trío intacto); filas con `responses_repo.list_full(session, target_session.id, None)` + `list_cc(..., None)` — espejo línea a línea de `get_session_detail` (api/sessions.py :161-191, `limit=None` = datos COMPLETOS ascendentes); `audit_repo.record(..., action="support_session_detail", capture_session_id=target_session.id)` + commit fail-closed + logger. Construir el `SessionDetailOut` exactamente como el detail de 3.3 (`**session_to_out(...).model_dump()`, `SessionResponseRow`/`SessionCcRow`).
  - [x] **Read-only por AUSENCIA de rutas (AC 1):** bajo `/api/admin/tenants/...` existen SOLO estos dos GET — sin rename, sin continue, sin delete, sin export. FastAPI responde 405 a cualquier otro verbo sobre esas rutas: la garantía es estructural, no un flag.
  - [x] `main.py` SIN CAMBIOS (admin_router ya registrado :77).

### Frontend (Tareas 5–6)

- [x] Task 5: `frontend/app/admin/tenants/[id]/page.tsx` — página NUEVA (AC: 1, 4)
  - [x] `"use client"`; parseo del param idéntico a `sessions/[id]/page.tsx` (:66, :108-112): `/^\d{1,10}$/` + rango int4 (`PG_INT_MAX = 2147483647`) ⇒ id imposible renderiza el estado "no existe" sin round-trip.
  - [x] Interfaces espejo locales (idiom admin/users :20-34, diferido 2-1 vigente — NO regenerar types): `SessionOut`, `SupportSessionsResponse {tenant_id, email, items, total}`, `SessionDetailOut` (+`SessionResponseRow`/`SessionCcRow`) — copiar los shapes de `sessions/[id]/page.tsx` :28-62.
  - [x] Query de lista: `useQuery({ queryKey: ["admin-tenant-sessions", String(tenantId)], queryFn: () => api.get<SupportSessionsResponse>(`/api/admin/tenants/${tenantId}/sessions`) })`. Estados: `isLoading` ⇒ `<Spinner />` centrado; `ApiError` con `code === "tenant_not_found"` ⇒ estado "Ese cliente no existe." + Link "← Usuarios" a `/admin/users` (jamás dead-end, UX-DR16; idiom `NotFound` de sessions/[id] :96-105); otros errores ⇒ `<Alert status="danger">No pudimos cargar las sesiones. Recarga la página.</Alert>`.
  - [x] Header: Link "← Usuarios" a `/admin/users` + `<h1>` "Sesiones de {data.email}" (el email ES la identidad visible del cliente — la tabla de /admin/users lo muestra igual).
  - [x] Lista como HeroUI `Table` (UX-DR18 + DESIGN :216: superficies admin = `Table` a defaults, mismo theme; idiom EXACTO de admin/users :135-147): columnas **Nombre** (name ?? `fallbackName(created_at)` — duplicar el helper, precedente 3.3 aceptado: las pages del App Router no exportan helpers), **Gate** (mono `gate_value`), **Estado** (badge "En curso"/"Cerrada" — duplicar `SessionBadge`), **Acciones** (botón "Ver" `size="sm" variant="secondary"`). `renderEmptyState={() => "Este cliente no tiene sesiones."}` — **copy VERBATIM del AC 4** (y de EXPERIENCE :113, slot vacío de tabla admin; sin acción primaria: la superficie es de solo lectura).
  - [x] Detalle por SELECCIÓN local (`const [selectedId, setSelectedId] = useState<number | null>(null)`) — NO hay sub-ruta: la tabla de superficies de UX nombra SOLO `/admin/tenants/[id]` (EXPERIENCE :34) y el flujo es "opens the one in question" dentro de la misma vista (Flow 5 paso 2). Query de detalle `enabled: selectedId !== null`, `queryKey: ["admin-tenant-session", String(tenantId), String(selectedId)]` → `/api/admin/tenants/${tenantId}/sessions/${selectedId}`. Header del detalle: nombre + `gate_value · id` mono + badge + botón "← Sesiones" que vuelve a la lista (`setSelectedId(null)`); `session_not_found` (borrada por el cliente entre lista y click) ⇒ volver a la lista + invalidate de la query de lista.
  - [x] **Duales reutilizados read-only (AC 1):** mapear filas EXACTAMENTE como `sessions/[id]/page.tsx` :223-235 (`key: \`s-${row.id}\``, `nueva: false` — superficie de lectura) y montar `CompletaPanel`/`FiltradaPanel` desktop (`hidden lg:flex`, grid 2 cols) + `ResponseTabs` mobile (`lg:hidden`) — los componentes de `components/sessions/response-views.tsx` tal cual, **SIN `exportPath`** ⇒ no se renderiza footer alguno (cero botones muertos por construcción; el endpoint de export de 3.5 es tenant-scoped al dueño y NO existe en superficie admin — frontera registrada en la story 3.5). `components/sessions/*` NO se toca.
  - [x] **SIN live-follow, decisión registrada:** el broadcaster es tenant-scoped al tenant del ACTOR (architecture: "Scope every... WS broadcast by tenant") — el socket del admin jamás trae eventos del tenant objetivo. La vista de soporte es REST puro: foto al cargar/seleccionar; re-seleccionar refresca. NO tocar `lib/ws.ts`, NO inventar un socket admin.
- [x] Task 6: `frontend/app/admin/users/page.tsx` — punto de entrada (AC: 1)
  - [x] En la celda Acciones de las filas con `u.role === "client"` (:164-173), añadir un `Link` "Sesiones" (`className="text-sm text-default-500 underline"`, idiom del link "Gates" del header :92-98) a `/admin/tenants/${u.tenant_id}` JUNTO a `ClientLifecycleActions` — el Flow 5 arranca aquí ("The owner opens /admin/tenants/[id] for that client"; el `tenant_id` ya viaja en `UserOut`). Filas admin (vista owner) NO ganan link: el objetivo del soporte es un cliente.

### Middleware, tests y gates (Tareas 7–9)

- [x] Task 7: `frontend/middleware.ts` — VERIFICAR, sin cambios (AC: 3)
  - [x] El AC 3 ya está cubierto @ baseline: `isAdminPath = pathname.startsWith("/admin")` (:38) atrapa `/admin/tenants/[id]` y un `me.role === "client"` redirige a `/` (:127-131, "Middleware redirect; no 'blocked' screen rendered" — EXPERIENCE per-surface states). El gate owner-only de `/admin/gates` (:136-141) NO aplica aquí: la vista es admin+owner. Si el backend está caído, /admin/* falla cerrado a /login (:54-57) — también cubierto. Documentar la verificación en el File List como "sin cambios"; la frontera de SEGURIDAD es el 403 del API (Task 4) y se testea allí — middleware es UX.
- [x] Task 8: `backend/tests/test_support_view.py` — módulo NUEVO (AC: 1, 2, 3-API, 4)
  - [x] Mismo idiom que test_sessions.py: app ASGI real + Postgres de dev, self-seed/self-clean, `FakeGateway`, capturas DIRECTO a `capture.process_incoming(IncomingReply(...))`, lotes vía `POST /api/batches` + `send_worker.step()`. Fixtures de conftest: `ctx` (owner_client + admin_client logueados), `client_user`, `gate`, `fake_gateway`. Replicar localmente los helpers chicos (`_post_batch`, `_drain`, `_capture_ok`, `_bound_session_id` — test_sessions.py :71-119; los test modules no se importan entre sí). Bodies esperados como constantes: `TENANT_NOT_FOUND_BODY = {"code": "tenant_not_found", "message": "Ese cliente no existe."}`, `SESSION_NOT_FOUND_BODY` (= el de sessions), `FORBIDDEN_BODY = {"code": "forbidden", "message": "No tienes permiso para acceder a esto."}` — asserts de body EXACTOS (lección 3.3).
  - [x] **Lista cross-tenant (AC 1):** lote del `client_user` + 2 capturas ✅ ⇒ owner `GET /api/admin/tenants/{tenant_id}/sessions` ⇒ 200, `email` == email del cliente, `tenant_id` correcto, `total` == 1, item con `gate_value`/`is_active`/`name` correctos (shape `SessionOut` exacto).
  - [x] **Detalle cross-tenant (AC 1):** admin `GET .../sessions/{session_id}` ⇒ 200, shape `SessionDetailOut` con `responses`/`cc` EXACTOS (mismos asserts que el detail de 3.3 — comparar contra `_response_rows`), `responses_total`/`cc_total` correctos.
  - [x] **Auditoría (AC 2):** tras lista (owner) + detalle (admin), SELECT directo a `audit_log` (async_session_factory) filtrado por `tenant_id` del cliente ⇒ 2 filas: `(actor=owner.id, action="support_sessions_list", capture_session_id=None)` y `(actor=admin.id, action="support_session_detail", capture_session_id=<id>)`. Self-clean: las filas CASCADE con el tenant del `client_user` en su teardown — cero limpieza manual.
  - [x] **Cliente bloqueado en el API (AC 3, lado servidor):** el `client_user` llama ambos GET ⇒ 403 `FORBIDDEN_BODY` exacto (require_admin_or_owner — la frontera real; el redirect de middleware es UX y queda para el smoke).
  - [x] **Trío 404 de tenant (AC 2 — sin filtrar existencia):** tenant id desconocido, `ctx["owner"].tenant_id` (tenant cuyo user NO es client) e id > int4 ⇒ 404 `TENANT_NOT_FOUND_BODY` IDÉNTICOS los tres (para owner Y admin como actores).
  - [x] **Trío 404 de sesión en el detalle:** session id desconocido, sesión de OTRO tenant distinto al target (seed segundo cliente con `seed_user`+`login` propios, lote+sesión suyos; pedirla bajo el tenant del PRIMER cliente) e id > int4 ⇒ 404 `SESSION_NOT_FOUND_BODY` idénticos.
  - [x] **Vacío (AC 4):** cliente recién seedeado sin lotes ⇒ lista 200 con `items == []`, `total == 0` (el copy "Este cliente no tiene sesiones." lo pinta la Table del frontend — el API entrega el vacío honesto).
  - [x] **Read-only estructural (AC 1):** `PATCH`/`DELETE` sobre `.../sessions/{id}` y `POST .../sessions/{id}/continue` bajo el prefijo admin ⇒ **405** (los verbos NO existen — basta el status, es el Method Not Allowed de FastAPI, mismo trato que los 422 de validación).
  - [x] Suite COMPLETA verde (baseline al cierre de 3.5: **212 passed** — verificar ANTES de tocar nada).
- [x] Task 9: gates de verificación (todos los AC)
  - [x] `alembic upgrade head` aplicado en dev ANTES de pytest (la tabla nueva debe existir).
  - [x] Backend: `ruff check app/ tests/`, `mypy app`, `pytest` — verde completo.
  - [x] Frontend: `npx tsc --noEmit` + `npm run lint` + `npm run build` — los tres verdes; SIN framework de tests (decisión diferida del proyecto; NO inventar jest/vitest).
  - [x] deferred-work.md NO se toca (nada absorbido, nada nuevo salvo que el review lo diga).
  - [ ] (HUMAN — necesita credenciales reales) Smoke manual en dev: como owner abrir `/admin/users` → "Sesiones" en un cliente con capturas → la lista carga con el email correcto → "Ver" una sesión → duales Completa/Filtrada idénticas a las del cliente, SIN botones de export/renombrar/continuar/eliminar → cliente sin sesiones muestra "Este cliente no tiene sesiones." → logueado como CLIENTE, navegar a `/admin/tenants/1` redirige fuera (AC 3) → verificar en Postgres que `audit_log` tiene las filas de las vistas. **No correr contra producción sin el OK de Richard.**

## Dev Notes

### Qué NO es esta story (cerco de alcance)

- **SIN mutaciones cross-tenant:** ni rename, ni continue, ni delete, ni export sobre datos ajenos — "read-only" se garantiza por AUSENCIA de rutas (los dos únicos verbos son GET). El export de 3.5 queda fuera de superficies admin (frontera ya registrada en la story 3.5: "el export NO se monta en superficies admin"); su endpoint además es tenant-scoped al dueño y 404earía.
- **SIN live-follow ni WS admin:** el broadcaster es tenant-scoped al actor — la vista de soporte es REST puro, foto bajo demanda. NO tocar `lib/ws.ts`, `core/broadcaster.py` ni `api/ws.py`.
- **SIN auditoría retroactiva ni genérica:** `audit_log` nace aquí y lo escriben SOLO los dos GET de soporte. No instrumentar otros endpoints "de paso" (las rutas admin de users/gates ya son globales POR DISEÑO — module note de `db.repos.users` — y no cruzan datos de sesiones; auditarlas no es de esta story).
- **SIN paginación, SIN filtros, SIN búsqueda** en la lista de soporte (MVP/NFR2 — espejo de `list_for_tenant` que tampoco pagina).
- **SIN cambios en middleware.ts** (AC 3 ya cubierto @ baseline — Task 7 solo verifica), **SIN eventos WS nuevos, SIN settings nuevos** (regla 2.5), **`main.py` SIN CAMBIOS**, **`components/sessions/*` SIN CAMBIOS** (los duales se consumen, no se modifican — eran props-driven justo para esto).
- 🔒 La regla "jamás leer `respuestas/`" sigue intacta — el modelo nuevo vive en Postgres; esta story ni se acerca al legacy.

### Diseño (decisiones registradas)

- **Rutas `GET /api/admin/tenants/{tenant_id}/sessions[/{session_id}]`:** prefijo `/api/admin` porque la autorización es de rol, no de tenant (router existente, `require_admin_or_owner` :46 — admin Y owner por AC 1; el gate owner-only es solo para gates/categorías). El `tenant_id` viaja en el PATH — la ÚNICA excepción al mandato "handlers never read tenant_id from request bodies": no es body, es un recurso admin explícito, y es exactamente el "explicit `for_tenant(id)` support path" de architecture :248. Documentado en el comentario de sección.
- **Los support paths YA EXISTEN con el nombre de architecture:** `capture_sessions_repo.list_for_tenant` (:34) y `get_for_tenant` (:50) son las funciones `for_tenant(id)` literales — 3.3 las estrenó tenant-scoped al actor; 3.6 las reutiliza pasando el tenant del path. CERO código nuevo en ese repo: el cruce es del handler, auditado, no del repo.
- **Objetivo = tenant de un CLIENTE:** tenant-per-user (un tenant por usuario, nombrado con su email — services/users.py); `get_user_by_tenant` resuelve el user único y `role != "client"` ⇒ 404 idéntico al inexistente. Sondear el tenant del owner o de un admin no filtra NADA (idiom `session_not_found`/`batch_not_found`: existence is never leaked). El `email` del target alimenta el header "Sesiones de {email}".
- **Auditoría en DB + log estructurado, fail-closed:** una fila `audit_log` por lectura de soporte (acción `support_sessions_list` / `support_session_detail`, esta última con `capture_session_id`), commit ANTES de devolver datos — si el registro falla, la respuesta falla (500): "is audit-logged" es condición de servicio, no best-effort. Además `logger.info("event=support_view ...")` (idiom `event=` de send_worker) para el ojo operativo de Epic 4. Un GET que escribe es deliberado y queda documentado en el docstring del handler.
- **FKs del audit:** `actor_user_id` SET NULL (el trail sobrevive a la baja del admin), `tenant_id` CASCADE (el trail es de soporte y muere con el tenant objetivo — pragmático a escala MVP y mantiene limpio el teardown de tests), `capture_session_id` SIN FK (referencia histórica: borrar la sesión no borra ni anula el registro de que alguien la miró).
- **Shapes reutilizados VERBATIM:** la lista entrega `SessionOut` y el detalle `SessionDetailOut` — los schemas de 3.3 importados desde `api/sessions.py`, con `_session_out` promovido a `session_to_out` público (espejo del precedente `gate_to_out` que `api/gates.py` ya importa de `admin.py`). Así los mappers del frontend (3.2/3.3) sirven tal cual y los duales renderizan idéntico a lo que ve el cliente — el corazón del AC 1 ("their own data view").
- **Detalle por selección local, sin sub-ruta:** UX nombra SOLO `/admin/tenants/[id]` (EXPERIENCE :34); lista→detalle es estado de página (`selectedId`), como el "list + detail" desktop del Historial. Menos superficie, mismo flujo del Flow 5.
- **`nueva: false` en todas las filas:** el highlight "nueva" pertenece al aterrizaje en vivo de Envío; el soporte es superficie de lectura (mismo criterio que el detalle 3.3).
- **Sin botón muerto de export:** los paneles SIN `exportPath` no renderizan footer (response-views.tsx :210-214) — read-only visual por construcción, cero CSS nuevo.
- **El 403 del API es la frontera; el redirect de middleware es UX:** AC 3 lo cumple el middleware existente (isAdminPath :38 + role gate :127-131) y el servidor lo respalda con `require_admin_or_owner` (403 `forbidden`) — "Authorization is enforced SERVER-SIDE here (the security boundary — the UI only mirrors it)", docstring de admin.py. El test de AC 3 es contra el API; el redirect se verifica en el smoke.

### Código actual que vas a tocar (estado HOY @ 27f3170, con anclas)

| Archivo | Hoy | Esta story |
| --- | --- | --- |
| `backend/app/db/models.py` | termina en `Response` :355-413; sin tabla de auditoría | + `AuditLog` al final (audit_log — architecture :309) |
| `backend/migrations/versions/` | head = `2faec0509cb8` (capture_sessions and responses) — confirmar con `alembic heads` | + migración `audit log` hand-written espejada |
| `backend/app/db/repos/audit.py` | NO EXISTE | NUEVO: `record(...)` (ORM puro, flush not commit) |
| `backend/app/db/repos/users.py` | `get_user_by_id` :86-95, `create_tenant` :43, module note "GLOBAL/cross-tenant by design" | + `get_user_by_tenant` |
| `backend/app/db/repos/capture_sessions.py` | `list_for_tenant` :34-47, `get_for_tenant` :50-74 — los support paths nombrados | SIN CAMBIOS (solo se llaman con el tenant del path) |
| `backend/app/db/repos/responses.py` | `list_full` :193, `list_cc` :201 (`limit=None` = todo, ascendente) | SIN CAMBIOS |
| `backend/app/errors.py` | termina en `session_conflict` :279-287 | + sección 3.6: `tenant_not_found()` |
| `backend/app/api/admin.py` | docstring :1-10, `require_admin_or_owner` :46, `_PG_INT_MAX` :339, `gate_to_out` :415 (precedente de mapper compartido), CRUD gates :441-527 (NO tocar — diferido 2-1 :466/:509 vive ahí), termina :664 | + sección "Cross-tenant support view": `_require_client_tenant`, `SupportSessionsResponse`, 2 GET con audit; docstring actualizado |
| `backend/app/api/sessions.py` | `_session_out` :109-117 (privado), docstring :14-15 ("the cross-tenant support view is Story 3.6, not here"), `SessionOut` :51, `SessionDetailOut` :83, detail :161-191 (el espejo a seguir) | rename `_session_out` → `session_to_out` + docstring actualizado (promesa cobrada); NADA más |
| `backend/app/main.py` | routers :75-81 (admin ya registrado) | SIN CAMBIOS |
| `frontend/middleware.ts` | `isAdminPath` :38, client→`/` :127-131, gates owner-only :136-141, matcher :156-158 | SIN CAMBIOS (Task 7 = verificación AC 3) |
| `frontend/app/admin/tenants/[id]/page.tsx` | NO EXISTE | NUEVO: lista Table + detalle por selección + duales read-only |
| `frontend/app/admin/users/page.tsx` | header con link "Gates" :92-98 (idiom de link), celda Acciones :164-186, `UserOut.tenant_id` :27 | + Link "Sesiones" en filas client |
| `frontend/components/sessions/response-views.tsx` | `CompletaPanel` :219, `FiltradaPanel` :247, `ResponseTabs` :279 — props-driven, footer solo con `exportPath` :210-214 | SIN CAMBIOS (solo se consumen) |
| `frontend/app/(client)/sessions/[id]/page.tsx` | parseo id :66/:108-112, `fallbackName` :70, `SessionBadge` :80, mapeo de filas :223-235 — los idioms a duplicar | SIN CAMBIOS (referencia) |
| `frontend/lib/api.ts` | `api.get` + `ApiError` (branch por `code`) | SIN CAMBIOS (solo se consume) |
| `backend/tests/test_support_view.py` | NO EXISTE | NUEVO: Task 8 completa |
| `backend/tests/conftest.py` | `ctx` :104 (owner+admin logueados), `client_user` :224, `gate` :199, `seed_user` :75, `cleanup_users` :136, `fake_gateway` :191 | SIN CAMBIOS (solo se usa) |

**Sin cambios además:** `core/*` completo, `services/*` completo, `api/{auth,batches,gates,health,ws}.py`, `db/base.py`, `config.py`, `deploy/*`, `frontend/lib/ws.ts`, `frontend/components/{batch/*,sessions/response-row.tsx,client-nav.tsx}`, `frontend/app/(client)/*`, `frontend/types/api.ts` (NO regenerar — diferido 2-1 vigente), legacy `core.py`/`app.py`/`static/`.

### Cumplimiento de arquitectura (no negociable)

- **"Owner/admin cross-tenant access goes through explicit `for_tenant(id)` support paths, audit-logged"** (:248) y **"admin cross-tenant reads via explicit audited support paths"** (:365) — esta story ES esa línea: repos `*_for_tenant` reutilizados con el tenant del path + `audit_log` (tabla nombrada en el listing de models.py :309). Es "the only place tenant isolation is intentionally crossed" (AC 2 / FR20, epics :71). [Source: architecture.md#Tenant-Scoping; #Data-Boundaries; :309]
- **Migración Alembic para todo cambio de schema** — la tabla nueva llega por migración hand-written espejada (idiom 2faec0509cb8), jamás mutación manual. [Source: architecture.md#Enforcement-Guidelines]
- **Errores `{code, message}` + status con sentido:** `tenant_not_found` 404 (sin filtrar existencia), `forbidden` 403 reusado del gate de rol; 405 estructural de FastAPI para verbos inexistentes (misma excepción aceptada que los 422). [Source: architecture.md#Error-Handling]
- **REST sobre recursos:** dos GET de lectura, anidados bajo el recurso admin (`/tenants/{id}/sessions`); la escritura de auditoría es efecto deliberado y documentado del soporte, no una acción del cliente. [Source: architecture.md#API-&-Communication-Patterns]
- **Identificadores en inglés, copy en español tuteo:** `AuditLog`/`get_user_by_tenant`/`support_sessions_list`/`SupportSessionsResponse` en código; copy verbatim: **"Sesiones de {email}"**, **"Este cliente no tiene sesiones."**, **"Ese cliente no existe."**, **"Ver"**, **"← Usuarios"**. [Source: architecture.md#Code-Naming-Conventions]
- **UX:** superficie `/admin/tenants/[id]` admin+owner (EXPERIENCE :34); "same dual-view component, cross-tenant by explicit admin route" (Flow 5, :177-183); empty state de tabla admin con el copy del AC (:113); UX-DR18 "cross-tenant session viewer reusing the dual-view component read-only; admin tables responsive" (epics :140); admin surfaces = HeroUI `Table` a defaults, mismo theme (DESIGN :216). [Source: EXPERIENCE.md; DESIGN.md; epics.md :140]

### Inteligencia de stories previas (3.5 + 3.4 + 3.3)

- **Esta story COBRA la promesa del docstring de `api/sessions.py`** (:14-15 — "the cross-tenant support view is Story 3.6, not here"): actualízala al cumplirla (mismo ritual que 3.4/3.5 con las suyas). No hay más promesas pendientes con el nombre 3.6 en el código (verificado por grep @ baseline).
- **Lecciones 3.3/3.4/3.5:** los 404 se asertan con body EXACTO y el trío de ids malos se extiende a CADA verbo nuevo (aquí: trío de tenant en ambos GET + trío de sesión en el detalle); asserts de contenido exactos, no `in`; copy de UI string-a-string contra el spec (los reviews comparan verbatim); HeroUI v3 se verifica contra los typings INSTALADOS antes de usar una variante nueva (la página nueva solo usa Table/Button/Alert/Spinner/Link — todos ya usados en admin/users y sessions); `npm run lint` antes de declarar verde.
- **Lecciones 3.1/3.2:** los paneles duales son props-driven a propósito (cero lecturas de store dentro) — la página admin les pasa filas REST y jamás toca el store WS; el detail de 3.3 ya pinta datos COMPLETOS por REST (`limit=None`) — el detalle de soporte reusa esos MISMOS SELECTs vía el mismo par list_full/list_cc.
- **Precedente de mapper compartido entre routers:** `gate_to_out` vive en admin.py y `api/gates.py` lo importa — `session_to_out` replica el patrón en la dirección sessions→admin. No duplicar schemas pydantic entre routers.
- **Duplicación aceptada en pages:** `fallbackName`/`SessionBadge` se duplican en la página nueva (las pages del App Router no exportan helpers — precedente registrado en 3.3/3.4 y repetido en ambos archivos de sessions).
- **1.7/CI:** Conventional Commits con scope (`feat(backend,frontend): …`), rama `story/3.6-soporte-cross-tenant`; push a main = deploy automático al VPS (deploy.sh corre `alembic upgrade head` — la migración viaja sola). Sin claves de entorno nuevas.

### Estándares de testing

- Backend: `pytest` + `pytest-asyncio` (`loop_scope="session"`) + httpx `ASGITransport` contra la app real y el Postgres de dev; self-seed/self-clean (el CASCADE del tenant en `cleanup_users` se lleva `capture_sessions`/`responses` Y las filas nuevas de `audit_log` del target); sin mocks de DB; un comportamiento por test. Capturas DIRECTO a `capture.process_incoming(IncomingReply(...))`; lotes reales vía `POST /api/batches` + `send_worker.step()`.
- Actores: `ctx` da owner_client Y admin_client — cubrir AMBOS roles en los happy paths y en el trío 404 (el gate es el mismo pero el matrix de roles se asserta, no se asume). El client va por `client_user`.
- La verificación del redirect de middleware (AC 3 lado UX) y del render visual de los duales es el smoke manual de Task 9 — SIN framework de tests frontend (decisión diferida; no instalar nada). Gates frontend: `npx tsc --noEmit` + `npm run lint` + `npm run build`.

### Notas de estructura del proyecto

- **Nuevos:** `backend/app/db/repos/audit.py`, `backend/migrations/versions/<hash>_audit_log.py`, `backend/tests/test_support_view.py`, `frontend/app/admin/tenants/[id]/page.tsx`.
- **Modificados:** `backend/app/db/models.py`, `backend/app/db/repos/users.py`, `backend/app/errors.py`, `backend/app/api/admin.py`, `backend/app/api/sessions.py` (solo rename `session_to_out` + docstring), `frontend/app/admin/users/page.tsx`.
- Legacy `core.py`/`app.py`/`static/` congelados en la raíz — solo referencia de comportamiento. **🔒 JAMÁS leer contenido bajo `respuestas/`. JAMÁS tocar `.env` ni `anon.session`.**

### Referencias

- [Source: planning-artifacts/epics.md#Story-3.6 (:734-755) — los 4 ACs verbatim; FR20 (:166); :71 (tenant isolation: "admin cross-tenant access via explicit audited `for_tenant(id)` support paths"); UX-DR18 (:140)]
- [Source: planning-artifacts/architecture.md :248 (support paths audit-logged), :254 (scope every query by tenant), :309 (audit_log nombrado en models.py), :365 (data boundaries), #Error-Handling, #Enforcement-Guidelines, #Code-Naming-Conventions]
- [Source: ux-designs/ux-cc-2026-06-10/EXPERIENCE.md :34 (superficie /admin/tenants/[id] admin+owner), :113 (empty admin table — "Este cliente no tiene sesiones."), :177-183 (Flow 5 completo); DESIGN.md :216 (admin = HeroUI Table a defaults, mismo theme)]
- [Source: implementation-artifacts/3-5-exportar-...md — formato de story, frontera "el export NO se monta en superficies admin", ritual de promesas, baseline 212 passed]
- [Source: implementation-artifacts/3-3-historial-...md / 3-4-continuar-...md — `_require_session`/trío 404 por verbo, detail `limit=None`, duplicación aceptada en pages, asserts exactos]
- [Source: implementation-artifacts/deferred-work.md — revisado: único abierto en archivos tocados = 2-1 LOW admin.py:466/:509, NO absorbido (justificación en la cabecera); 2-2 batches.py:121, 2-3 ws.py:54, 2-5 telegram.py:111 / send_worker.py:398/:652 y el pase de generated types SIGUEN diferidos]
- [Source: _bmad-output/project-context.md — 🔒 reglas respuestas//.env/anon.session; "History paths guarded… keep this on any new history endpoint" (equivalente moderno: lookup scoped + trío 404 — aquí scoped AL TENANT DEL PATH, auditado)]
- [Source: código actual @ 27f3170 — backend/app/{api/{admin,sessions,deps}.py, db/{models.py, repos/{users,capture_sessions,responses}.py}, services/users.py, errors.py, main.py}, backend/tests/{conftest.py, test_sessions.py}, backend/migrations/versions/2faec0509cb8_*.py, frontend/{middleware.ts, lib/api.ts, components/sessions/response-views.tsx, app/admin/users/page.tsx, app/(client)/sessions/[id]/page.tsx}]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Fable 5)

### Debug Log References

- Baseline verificado @ 27f3170 (HEAD == baseline_commit): `pytest` → **212 passed**; `alembic heads`/`alembic current` → `2faec0509cb8 (head)`; `grep audit` en `app/`+`migrations/` → cero filas de DB. Las anclas de la story coinciden con el código actual.
- Migración `2d9609ffa4d4_audit_log.py` aplicada con `alembic upgrade head`; `alembic check` → "No new upgrade operations detected" (espejo models.py↔migración confirmado).
- Cierre: `pytest` → **220 passed** (212 + 8 nuevos), `ruff check app/ tests/` limpio, `mypy app` limpio; frontend `npx tsc --noEmit` + `npm run lint` (0 errors/0 warnings tras `--fix` de 2 warnings prettier) + `npm run build` verdes — la ruta `ƒ /admin/tenants/[id]` aparece en el build output.

### Completion Notes List

- **Desviación registrada (Task 8):** `POST .../sessions/{id}/continue` bajo el prefijo admin responde **404**, no 405 — el sub-path `/continue` no existe EN ABSOLUTO bajo `/api/admin/tenants/...` (ningún route hace match parcial), mientras PATCH/DELETE sí dan 405 (la ruta GET existe, el verbo no). El test asserta la realidad (404) con comentario; la garantía del AC ("los verbos no existen") queda igual de probada — 404 es "aún más ausente" que 405.
- **Import de schemas ajustado (Task 4):** la lista de imports de la story incluía `SessionListResponse`, pero el endpoint de lista usa el nuevo `SupportSessionsResponse` — importarlo sin uso rompería ruff F401. Se importan `SessionCcRow, SessionDetailOut, SessionOut, SessionResponseRow, session_to_out` (los que el detalle/lista realmente construyen).
- Auditoría fail-closed implementada como dicta la story: `audit_repo.record(...)` + `session.commit()` ANTES del return en ambos GET; logger `event=support_view` (idiom send_worker). El test de 403 además asserta que un client NO deja fila de auditoría.
- Trío 404 de tenant cubierto para owner Y admin como actores en ambas rutas; trío de sesión con segundo cliente seedeado (y verificación positiva de que la sesión de B resuelve bajo SU tenant).
- Frontend: detalle por selección local (sin sub-ruta), duales `CompletaPanel`/`FiltradaPanel`/`ResponseTabs` consumidos SIN `exportPath` (cero footer por construcción), REST puro sin WS; `session_not_found` en el detalle vuelve a la lista + invalidate vía `useEffect`. `fallbackName`/`SessionBadge` duplicados (precedente 3.3). Link "Sesiones" solo en filas client de /admin/users.
- Task 7 (middleware): verificado SIN cambios — `isAdminPath` (:38) atrapa `/admin/tenants/[id]`, client→`/` (:129-131), backend caído fail-closed a /login (:54-57), el gate owner-only de gates (:136-141) no aplica. La frontera de seguridad es el 403 del API (testeado).
- deferred-work.md NO tocado; diferido 2-1 (admin.py CRUD gates) intacto — la sección nueva solo appendea al final del archivo.
- **(HUMAN pendiente)** Smoke manual de Task 9 (owner en dev: /admin/users → Sesiones → Ver → duales sin acciones; cliente redirigido fuera de /admin/tenants/1; filas en audit_log). No corrido: requiere credenciales reales/navegador. No correr contra producción sin el OK de Richard.

### File List

**Nuevos:**
- `backend/migrations/versions/2d9609ffa4d4_audit_log.py` — tabla `audit_log` (hand-written, espejo de models.py)
- `backend/app/db/repos/audit.py` — `record(...)` (ORM puro, flush not commit; único escritor = vista de soporte)
- `backend/tests/test_support_view.py` — 8 tests (lista/detalle cross-tenant, auditoría, 403 client, tríos 404 tenant/sesión, vacío, read-only estructural)
- `frontend/app/admin/tenants/[id]/page.tsx` — lista Table + detalle por selección + duales read-only

**Modificados:**
- `backend/app/db/models.py` — `AuditLog` al final + docstring del módulo (audit_log 3.6)
- `backend/app/db/repos/users.py` — `get_user_by_tenant` (junto a `get_user_by_id`)
- `backend/app/errors.py` — sección 3.6: `tenant_not_found()`
- `backend/app/api/admin.py` — sección "Cross-tenant support view (Story 3.6)": `_require_client_tenant`, `SupportSessionsResponse`, 2 GET auditados; docstring + imports + logger
- `backend/app/api/sessions.py` — rename `_session_out` → `session_to_out` (4 call sites) + docstring (promesa 3.6 cobrada)
- `frontend/app/admin/users/page.tsx` — Link "Sesiones" en filas client → `/admin/tenants/{tenant_id}`

**Verificado sin cambios:** `frontend/middleware.ts` (AC 3 cubierto @ baseline), `backend/app/main.py`, `backend/app/db/repos/capture_sessions.py`, `backend/app/db/repos/responses.py`, `frontend/components/sessions/*`, `frontend/lib/{api,ws}.ts`, `_bmad-output/implementation-artifacts/deferred-work.md`.
