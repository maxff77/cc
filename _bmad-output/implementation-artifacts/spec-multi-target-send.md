---
title: 'Destinos de envío múltiples y configurables (round-robin)'
type: 'feature'
created: '2026-06-13'
status: 'done'
baseline_commit: '6a9925164d911bc2302aa89b7c893815d758ae38'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** El gateway envía a un único destino fijo (`TELEGRAM_TARGET` del `.env`). El owner necesita mandar a varios chats donde vive el bot (el bot directo + grupos CC1..CC6) rotando entre ellos para repartir la carga por-chat, y poder agregar/quitar destinos sin redeploy.

**Approach:** Lista de destinos en una tabla nueva `send_targets`, gestionada por endpoints **owner-only** (mirror del CRUD de gates) con descubrimiento de chats vía Telethon. El gateway resuelve el conjunto de destinos habilitados, hace **round-robin por mensaje** en `send()`, y captura respuestas de **todos** los chats del conjunto. La atribución (por `message_id` global vía `send_log`) y el pacing global (`scheduler.interval`) quedan intactos.

## Boundaries & Constraints

**Always:**
- Telethon SOLO en `core/telegram.py` (`get_dialogs` y resolución de entities ahí); el gateway es agnóstico de la DB — el service le pasa `[(chat_id, label)]`.
- Atribución/captura **sin cambios**: `message_id` es global de la cuenta; capturar de varios chats no rompe atribución ni hay fuga cross-tenant. `parse_mode=None` intacto.
- Round-robin elige solo *a qué chat* va el mensaje; `scheduler.interval(n)` se respeta igual (pacing global no se toca).
- Destinos = recurso **global** (sin `tenant_id`), solo owner. Repos flush-not-commit + `FOR UPDATE` en read-modify-write.
- ≥1 destino resuelto para enviar; con 0, `POST /api/batches` → 503 (igual que hoy). Editar destinos recarga el gateway **en vivo** (sin reiniciar).

**Ask First:**
- Si parece requerir tocar `scheduler.py`/el intervalo → HALT (objetivo B diferido, fuera de alcance).
- Si surge referenciar `send_targets` desde `send_log`/`responses` → HALT (acopla histórico a config mutable).

**Never:** Telethon fuera de `core/telegram.py`. Leer `respuestas/`. Persistir estado de resolución (transitorio). Destinos tenant-scoped. Dos `cc-core`. Round-robin en el scheduler. Re-autenticar a otra cuenta.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Owner abre Destinos | `GET /api/admin/targets` + `/discover`, cuenta autorizada | lista de destinos guardados (cada uno con flag `resolved`) + chats elegibles | gateway no autorizado → `/discover` 503 `telegram_unauthorized` |
| Owner agrega un chat descubierto | `POST {chat_id,label}` | fila creada, gateway recarga, destino activo para el próximo envío | `chat_id` duplicado → 409 `telegram_target_exists`; irresoluble → 422 `telegram_target_unresolvable` |
| Worker envía K líneas con M destinos habilitados+resueltos | K sends | cada línea va al siguiente destino en round-robin; respuestas de los M chats capturadas y atribuidas | 0 resueltos → `POST /batches` 503 (sin cambio) |
| Owner deshabilita/elimina un destino durante un lote | `PATCH /{id}` / `DELETE /{id}` | gateway recarga el set habilitado; los envíos siguientes lo saltan | id inexistente → 404 `telegram_target_not_found` |
| Boot, tabla vacía, `TELEGRAM_TARGET` seteado | lifespan | siembra una fila desde el env, la resuelve, envío funciona como antes | target del env irresoluble → se saltea, se loguea (sin crash) |

</frozen-after-approval>

## Code Map

- `backend/app/db/models.py` -- agregar modelo `SendTarget` (mirror del patrón global `Gate`).
- `backend/migrations/versions/<rev>_send_targets.py` -- crear tabla `send_targets`.
- `backend/app/db/repos/targets.py` -- repo CRUD (mirror `repos/gates.py`).
- `backend/app/core/telegram.py` -- multi-entity: `_entities`/`_target_ids`/`_send_index`, round-robin en `send()`, filtro por pertenencia en `_bridge()`, agregación en `recent_outgoing()`, `ready`, + `reload_targets(...)` y `list_dialogs()`.
- `backend/app/services/targets.py` -- orquesta DB↔gateway: add/remove/toggle/list/discover/`ensure_seeded`.
- `backend/app/api/targets.py` -- endpoints owner-only (mirror gate CRUD en `admin.py`).
- `backend/app/errors.py` -- errores `telegram_target_*`.
- `backend/app/main.py` -- registrar router; en lifespan post-connect: `ensure_seeded` + `gateway.reload_targets`.
- `frontend/app/admin/destinos/page.tsx` -- página owner-only (mirror `admin/gates/page.tsx`).
- `frontend/components/ui/admin-shell.tsx` -- agregar nav "Destinos" (`ownerOnly: true`).
- `frontend/middleware.ts` -- gate owner-only para `/admin/destinos`.

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/db/models.py` -- modelo `SendTarget`: `id` PK, `chat_id` BigInteger UNIQUE (peer id marcado, account-global), `label` String(80), `enabled` Boolean default true, `created_at`/`updated_at` -- estado de destinos.
- [x] `backend/migrations/versions/<rev>_send_targets.py` -- `op.create_table` con constraints vía `op.f()` (naming convention), índice único en `chat_id` -- esquema.
- [x] `backend/app/db/repos/targets.py` -- `list_all`/`list_enabled`/`get_by_id(for_update)`/`create`/`delete`/`set_enabled`, flush-not-commit -- acceso a datos.
- [x] `backend/app/core/telegram.py` -- `_entity`/`_target_id` → `_entities: list` + `_target_ids: set[int]` + `_send_index`; `send()` round-robin (cursor en memoria); `_bridge()` filtra `event.chat_id in _target_ids` (set vacío = boot gap → unfiltered, preservar); `recent_outgoing()` agrega todas las entities, dedup por message_id, newest-first; `ready` = autorizado y `_entities` no vacío; `reload_targets(list[(int,str)])` resuelve cada `chat_id` (saltea fallas, devuelve resueltos/fallidos); `list_dialogs()` vía `client.get_dialogs()` -- multi-destino.
- [x] `backend/app/services/targets.py` -- orquesta DB↔gateway: `add` (valida resoluble→persiste→recarga), `remove`/`toggle` (persiste→recarga), `list_with_status` (anota `resolved` cruzando con `_target_ids` vivos), `discover` (gateway.list_dialogs), `ensure_seeded` (boot: count==0 y env seteado → resuelve+inserta) -- lógica.
- [x] `backend/app/api/targets.py` -- `GET /api/admin/targets`, `POST`, `PATCH /{id}`, `DELETE /{id}`, `GET /api/admin/targets/discover`, todos `Depends(require_role("owner"))` -- API.
- [x] `backend/app/errors.py` -- `telegram_target_not_found` (404), `telegram_target_exists` (409), `telegram_target_unresolvable` (422), mensajes en español -- contrato de error.
- [x] `backend/app/main.py` -- `include_router(targets_router)`; en lifespan tras `gateway.connect()`: `ensure_seeded` luego `gateway.reload_targets(enabled)` -- wiring.
- [x] `frontend/app/admin/destinos/page.tsx` -- mirror de gates: listar (con badge resuelto/no resuelto + toggle habilitar), agregar desde lista de `/discover`, eliminar; TanStack Query + invalidación; tipos `TargetOut` locales -- UI.
- [x] `frontend/components/ui/admin-shell.tsx` + `frontend/middleware.ts` -- nav "Destinos" `ownerOnly` y redirect owner-only en el edge -- acceso.
- [x] `backend/tests/test_targets.py` -- cubrir la I/O Matrix: round-robin reparte K líneas entre M destinos; `_bridge` filtra por set; `reload_targets` saltea irresolubles y deja `ready` correcto; repo CRUD; seed desde env -- tests.

**Acceptance Criteria:**
- Given ≥2 destinos habilitados+resueltos, when el worker envía K líneas, then los destinos se usan en orden round-robin (reparto parejo ±1).
- Given una respuesta llega en cualquier chat-destino habilitado, when se captura, then se atribuye por `message_id` idéntico al caso de destino único (sin regresión en `test_attribution.py`).
- Given el owner agrega/deshabilita/elimina un destino, when la mutación commitea, then el set activo del gateway se actualiza sin reiniciar el proceso.
- Given el gateway no autorizado, when el owner abre `/discover`, then 503 `telegram_unauthorized` sin crash.
- Given un usuario admin o client, when pega `GET /api/admin/targets` o navega `/admin/destinos`, then 403 / redirect.
- Given el intervalo global de envío, when corre el round-robin, then `scheduler.interval(n)` no cambia (pacing no se bypassa).
- Given tabla vacía + `TELEGRAM_TARGET` seteado, when arranca la app, then existe una fila sembrada y el envío funciona como antes.

## Spec Change Log

- **2026-06-13 — review patches (no loopback; all patch/reject).** 3-reviewer pass (blind/edge/acceptance). Auditor confirmed all 7 AC met + no boundary violations. Applied patches: (1) `reload_targets` warms telethon's entity cache via `get_dialogs` once on the first resolution failure and retries — a bare supergroup/channel id (`-100…`) can't resolve on a cold session, so a seeded numeric target would 503 after every restart; also dedups `_entities` by peer id. (2) `create_target` raises 503 `telegram_unauthorized` (not 422) when the gateway is unauthorized. (3) `ensure_seeded` wraps the seed insert in `try/except IntegrityError` (idempotent under overlapping boots). (4) `send()`/`recent_outgoing()` snapshot `_entities` into a local vs a concurrent `reload_targets` rebind. Rejected: expire-on-commit "CRITICAL" (false positive — `expire_on_commit=False`); per-chat `recent_outgoing` window (safe superset). Added `test_reload_warms_cache_for_cold_numeric_id`.

## Design Notes

- **Cursor round-robin** en memoria (como `scheduler`): se reinicia en restart/reload, la equidad vuelve sola. Swap de `_entities` atómico bajo GIL → sin lock; un reload a mitad de send a lo sumo usa lista vieja/nueva (cosmético).
- **Capas:** gateway NO importa repos; el service lee DB y pasa `[(chat_id, label)]` a `reload_targets`. Resolución es transitoria (no se persiste); `resolved` se deriva intersectando `chat_id` guardados con `_target_ids` vivos.
- `chat_id` es **BigInteger** (peer ids de supergrupos/canales `-100...` exceden int32).

## Verification

**Commands:**
- `cd backend && .venv/bin/alembic upgrade head` -- expected: migración aplica, tabla `send_targets` creada.
- `cd backend && .venv/bin/pytest tests/test_targets.py` -- expected: round-robin/filtro/reload/repo/seed verdes.
- `cd backend && .venv/bin/pytest` -- expected: suite completa verde (sin regresión en captura/atribución).
- `cd frontend && npm run lint && npm run build` -- expected: sin errores.

**Manual checks:**
- Como owner: `/admin/destinos` lista destinos y `/discover` muestra chats; agregar uno; correr un lote; confirmar que las respuestas del chat nuevo se capturan y atribuyen.

## Suggested Review Order

**Engine — el corazón del cambio (gateway multi-destino)**

- Entry point: resuelve la lista, warmea caché de entities y rota — el diseño entero vive acá.
  [`telegram.py:127`](../../backend/app/core/telegram.py#L127)
- Round-robin por mensaje; solo elige chat, no toca el intervalo (pacing intacto).
  [`telegram.py:307`](../../backend/app/core/telegram.py#L307)
- Filtro de captura generalizado a pertenencia en el set (vacío = boot gap unfiltered).
  [`telegram.py:278`](../../backend/app/core/telegram.py#L278)
- `recent_outgoing` agrega salientes de todos los chats (reconciliación de boot).
  [`telegram.py:331`](../../backend/app/core/telegram.py#L331)
- Descubrimiento de chats para la UI (boundary: Telethon solo acá).
  [`telegram.py:219`](../../backend/app/core/telegram.py#L219)

**Orquestación — DB ↔ gateway (gateway agnóstico de DB)**

- `reload_gateway`/`list_with_status`/`ensure_seeded`: el único puente con la DB.
  [`services/targets.py:23`](../../backend/app/services/targets.py#L23)
- Seed desde `TELEGRAM_TARGET` en boot + reload, antes de arrancar el worker.
  [`main.py:58`](../../backend/app/main.py#L58)

**API owner-only (espejo del CRUD de gates)**

- `create_target`: 503 si no autorizado, 409 duplicado, 422 irresoluble.
  [`targets.py:140`](../../backend/app/api/targets.py#L140)
- `require_owner` + discover; toda mutación recarga el gateway en vivo.
  [`targets.py:34`](../../backend/app/api/targets.py#L34)

**Esquema (recurso global, sin tenant)**

- Modelo `SendTarget` (chat_id BigInteger único, enabled, sin tenant_id).
  [`models.py:183`](../../backend/app/db/models.py#L183)
- Migración: tabla `send_targets`.
  [`e2f5a7c9d1b4_send_targets.py:21`](../../backend/migrations/versions/e2f5a7c9d1b4_send_targets.py#L21)
- Repo CRUD flush-not-commit.
  [`repos/targets.py:24`](../../backend/app/db/repos/targets.py#L24)

**Frontend (página owner + gating)**

- Página Destinos: descubrir→elegir→agregar, badge resuelto, pausar/eliminar.
  [`destinos/page.tsx:63`](../../frontend/app/admin/destinos/page.tsx#L63)
- Nav owner-only + gate en el edge.
  [`middleware.ts:147`](../../frontend/middleware.ts#L147)

**Periféricos**

- Tests: round-robin, filtro, reload+warm, repo, seed.
  [`test_targets.py:1`](../../backend/tests/test_targets.py#L1)
