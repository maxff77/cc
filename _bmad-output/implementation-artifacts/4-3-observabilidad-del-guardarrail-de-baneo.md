---
baseline_commit: 024962a532fe15aea3e9acd891a60d4ad9185da3
---

# Story 4.3: Observabilidad del guardarraíl de baneo

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGÍA (decisión del owner 2026-06-11):** el término de producto para un prefijo es **"gate"** — DB, API, identificadores de código y todo el copy de UI (masculino: "el gate"). epics.md / architecture.md / docs de UX son anteriores al renombre y todavía dicen "prefijo/prefixes" — lee cada "prefijo" como "gate"; donde haya conflicto, gana "gate". Esta story no toca el catálogo, pero hereda la regla para todo copy nuevo.

## Story

As the owner,
I want FloodWait alerting and send-pattern visibility,
So that the ~0-bans counter-metric is operable, not aspirational.

## Acceptance Criteria

1. **Given** FloodWait events, **when** they exceed a threshold within a window, **then** the owner is alerted (FloodWait is the leading ban indicator).
2. **Given** the structured logs, **when** the owner inspects them, **then** per-tenant send counts, FloodWait events, governor `G_min` raises, and unmatched replies are all queryable.
3. **Given** the unmatched-replies bucket, **when** it grows abnormally, **then** an alert fires (attribution health is part of the ban guardrail).

## Tasks / Subtasks

### Backend (Tasks 1–6)

- [x] Task 1: `core/alerts.py` — NUEVO: latches de alerta por ventana deslizante (AC: 1, 3)
  - [x] Clase `SlidingAlert(kind, threshold, window_seconds, now=time.monotonic)` — reloj inyectable (idiom Watchdog/Scheduler): deque de timestamps podada a la ventana, `async note(detail)` que dispara al CRUZAR el umbral, `count_in_window()`, `is_alerting()`, `reset()`.
  - [x] **Anti-spam (decisión registrada, espejo del latch idempotente del watchdog):** la alerta dispara una sola vez al cruzar el umbral; saturación sostenida NO re-dispara; cuando la ventana drena por debajo del umbral el latch se re-arma solo. A diferencia del watchdog, NO requiere acción del owner: es alerta informativa, jamás pausa nada (la pausa es 4.1 — cerco).
  - [x] Al disparar: `logger.warning("event=guardrail_alert kind=… count=… window_seconds=… detail=…")` (AC 2: alertas greppables) + `broadcaster.emit_global("guardrail.alert", {kind, count, window_seconds, detail, at})` — global a propósito (idiom `watchdog.paused`/`flood.wait`: el tab del owner es uno de todos; la UI decide por rol qué mostrar).
  - [x] Singletons module-level + funciones `note_flood_wait()` / `note_unmatched()` / `reset()`. Umbrales como CONSTANTES de módulo (regla 2.5 — internals del pipeline jamás son settings): `flood_alert` = 3 FloodWaits en 600s (la ventana de decay del governor: si el governor no calmó la cuenta dentro de su propia ventana, el owner debe verlo); `unmatched_alert` = 5 unmatched en 600s (en operación sana el bucket es ~0 — crecimiento sostenido es anómalo).
- [x] Task 2: contadores del governor en `core/scheduler.py` (AC: 1, 2)
  - [x] `reset()` gana `_flood_events_total` y `_governor_raises`; `note_flood_wait` incrementa ambos — raises SOLO cuando `g_min` realmente subió (en el techo de 30s no hay raise). Properties `flood_events_total` / `governor_raises` (espejo de `g_min`). Los contadores viven donde nace el evento; el endpoint solo lee.
- [x] Task 3: wiring worker + capture (AC: 1, 2, 3)
  - [x] `core/send_worker.py`, rama `except FloodWaitError`: el log `event=flood_wait` gana `flood_total=` y `raises_total=` (los raises del governor quedan explícitamente greppables aunque g_min sature en el techo) + `await alerts.note_flood_wait()` tras el `flood.wait` global. Accessor nuevo `sent_by_tenant() -> dict[int, int]` (copia del Counter process-lifetime — los logs `event=line_sent … tenant_total=` ya cubrían el AC 2).
  - [x] `core/capture.py`, rama unmatched FINAL (la que ya loggea `event=unmatched_reply … total=`): `await alerts.note_unmatched()` — los reintentos de atribución (carrera send→record) NO alimentan la alerta, solo el intento final que de verdad bucketiza. Accessor nuevo `unmatched_total() -> int`.
- [x] Task 4: profundidad de la cola de admisión — `db/repos/batches.py` (AC: 2)
  - [x] `count_waiting(session) -> int` en la sección de admission control (espejo de `count_admitted`) — la profundidad de la cola FIFO para el endpoint; cero cambios en la mecánica 4.2.
- [x] Task 5: `api/observability.py` — NUEVO: `GET /api/observability` owner-only (AC: 2)
  - [x] `require_role("owner")` (idiom api/watchdog.py — estado GLOBAL de sistema, sin tenant scoping, misma clase de excepción documentada que el catálogo de gates). Estrictamente READ-ONLY: el endpoint lee singletons y cuenta filas, jamás escribe.
  - [x] Response `ObservabilityOut`: `sent_by_tenant` + `sent_total` (Counter del worker), `flood` (`events_total`, `governor_raises`, `g_min` actual, `events_in_window`, `alert_active`), `unmatched` (`total`, `events_in_window`, `alert_active`), `watchdog` (slice exacto de `watchdog.status()`) y `admission` (`max_active_senders`, `admitted`, `waiting`).
  - [x] Router registrado en `main.py` (tras watchdog_router).
- [x] Task 6: tests + gates de verificación (AC: 1, 2, 3)
  - [x] `backend/tests/test_observability.py` — NUEVO (idiom test_watchdog/test_admission: ASGI real, FakeClock para la aritmética de ventana, eventos por monkeypatch del broadcaster, `step()` directo con FakeGateway): umbral del flood alert (2 no, 3 sí), anti-spam en saturación, re-arme al drenar la ventana, wiring worker (3 FloodWaits reales → contadores + alerta + logs), wiring capture (5 unmatched finales → bucket + alerta; el reintento NO cuenta), raises no contados en el techo del governor, endpoint owner-only (admin/client 403), endpoint reporta todos los slices, profundidad de admisión con cola viva.
  - [x] conftest: fixture autouse `reset_alerts` (la trampa del estado module-level — espejo `reset_watchdog`).
  - [x] Gates: `cd backend && .venv/bin/python -m pytest -q && .venv/bin/ruff check app/ tests/`.

### Frontend (en paralelo — OTRO agente; fuera de este dev record)

- [ ] Task 7: superficie del owner para `guardrail.alert` + panel de observabilidad contra `GET /api/observability`. Contrato que este backend deja listo: evento global `{"event": "guardrail.alert", "data": {kind: "flood_wait" | "unmatched_replies", count, window_seconds, detail, at}}`; el GET responde los slices del Task 5 (las claves de `sent_by_tenant` viajan como strings en JSON).

### Smoke (HUMAN)

- [ ] (HUMAN — Richard) En producción: `journalctl -u cc-core | grep -E "event=(flood_wait|line_sent|unmatched_reply|guardrail_alert|watchdog_)"` responde las cuatro preguntas del AC 2 sin tooling extra. No bloquea — la cobertura automatizada es el gate real.

## Dev Notes

### Qué NO es esta story (cerco de alcance)

- **Pausar/reanudar envíos** → Story 4.1. Las alertas de 4.3 son informativas: jamás latchean nada, jamás tocan el worker. El watchdog ya alerta Y pausa; el guardrail alert solo alerta.
- **Cambiar la mecánica del governor/scheduler 2.4** — `note_flood_wait` solo GANA contadores; la fórmula, el factor ×1.5, el techo y el decay quedan byte a byte.
- **Tocar la cola de admisión 4.2** — solo se cuenta su profundidad (`count_waiting`).
- **Persistencia de contadores**: process-memory A PROPÓSITO (espejo de `_sent_by_tenant` 2.2 y `_unmatched_total` 3.1 — "counters never reset" del legacy; un restart los pone a cero y los logs estructurados en journald son la serie histórica). Nada de tablas nuevas, cero migraciones.
- **Dashboards/retención de logs** → 4.4 (runbooks). Aquí: logs greppables + un GET.

### Diseño (decisiones registradas)

- **Los contadores viven donde nace el evento, el endpoint solo lee:** flood/raises en el `Scheduler` (todo FloodWait ya pasa por `note_flood_wait`), unmatched en `capture` (el bucket ya existía desde 3.1), sends en el worker (`_sent_by_tenant` ya existía desde 2.2). `core/alerts.py` NO duplica totales: solo ventanas deslizantes + latch de disparo.
- **Un solo evento `guardrail.alert` discriminado por `kind`** en vez de dos eventos: el reducer del frontend trata ambas alertas igual (banner del owner); `kind` extiende sin tocar el envelope.
- **Umbrales = constantes de módulo** (regla 2.5): 3 FloodWaits / 600s (ventana = `_GOVERNOR_DECAY_SECONDS` — coherencia deliberada con el governor) y 5 unmatched / 600s. Sin knobs: tuning sin datos sería inventado; el owner ve el evento y los contadores crudos en el GET.
- **`g_min` se lee con decay lazy**: el property refleja el último valor evaluado; puede leerse alto hasta el próximo turno del worker (que llama `interval()`). Aceptado — el GET es observabilidad, no control.
- **Owner-only estricto**: el GET expone `tenant_id`s y volúmenes cross-tenant — exactamente la clase de dato que el aislamiento multi-tenant prohíbe a clientes/admins. `require_role("owner")`, mismo patrón server-side que api/watchdog.py.
- **AC 2 ya estaba medio cubierto por logs existentes** (decisión: reutilizar, no duplicar): `event=line_sent … tenant=… tenant_total=…` (2.2/2.5), `event=flood_wait … g_min=…` (2.4/2.5), `event=unmatched_reply … total=…` (3.1), `event=watchdog_…` (4.1). Esta story agrega `flood_total=`/`raises_total=` al log de flood_wait (raises explícitos aunque g_min sature) y el GET como superficie viva.

### Código actual que vas a tocar (estado HOY @ 024962a, con anclas)

| Archivo | Hoy | Esta story |
| --- | --- | --- |
| `backend/app/core/alerts.py` | NO EXISTE | nuevo: `SlidingAlert` + singletons flood/unmatched |
| `backend/app/core/scheduler.py` | `note_flood_wait` :79 (governor ×1.5) | + `flood_events_total` / `governor_raises` |
| `backend/app/core/send_worker.py` | rama FloodWait :572-587; `_sent_by_tenant` :99 | log ampliado + `alerts.note_flood_wait()`; accessor `sent_by_tenant()` |
| `backend/app/core/capture.py` | bucket unmatched final :263-269 | + `alerts.note_unmatched()`; accessor `unmatched_total()` |
| `backend/app/db/repos/batches.py` | sección admission :314+ | + `count_waiting` |
| `backend/app/api/observability.py` | NO EXISTE | nuevo: `GET /api/observability` owner-only |
| `backend/app/main.py` | routers :80-87 | + observability_router |
| `backend/tests/conftest.py` | resets autouse :154-192 | + `reset_alerts` |
| `backend/tests/test_observability.py` | NO EXISTE | nuevo |

**Sin cambios:** `core/watchdog.py` (su `status()` se reutiliza tal cual), `services/admission.py` (`get_cap` se reutiliza), `api/watchdog.py`, `api/admin.py`, `errors.py` (sin errores nuevos — el GET no falla por dominio), migraciones (cero DDL), frontend (agente paralelo).

### Cumplimiento de arquitectura (no negociable)

- Aislamiento multi-tenant sagrado: la superficie cross-tenant es owner-only server-side (`require_role` — la UI solo lo espeja). `tenant_id` jamás del request. [Source: architecture.md#Enforcement-Guidelines]
- Eventos SOLO vía broadcaster, envelope `{"event","data"}` intacto; `guardrail.alert` nace global (idiom `flood.wait`/`watchdog.paused`). [Source: architecture.md#Communication-Patterns]
- Logs estructurados `event=… key=value` (idiom 2.5/4.1) — journald-greppables, sin stack de observabilidad nuevo (NFR/assumption A1: el guardarraíl es operable con journalctl).
- Sin configuración nueva (regla 2.5: internals jamás son settings) y sin estado durable nuevo (los contadores son process memory documentada).

### Inteligencia de stories previas (2.4/2.5/3.1/4.1/4.2)

- El reloj inyectable (`now=time.monotonic`) hace determinística la aritmética de ventanas — idiom `Scheduler`/`Watchdog`; los tests usan `FakeClock` local (test_watchdog).
- Estado module-level ⇒ fixture autouse de reset en conftest (la trampa descubierta en 2.4 con el governor y repetida en 3.1/4.1).
- Eventos verificados monkeypatcheando `broadcaster.emit`/`emit_global` con lista grabadora (lección 2.2) — jamás sockets de test.
- `FloodWaitError(request=None, capture=0)` en el FakeGateway dispara la rama completa del worker con espera 0 (idiom test_send_hardening) — 3 en `errors` producen 3 eventos de flood en un solo `step()`.
- El reintento de atribución (carrera send→record, review 3-1) NO bucketiza hasta el intento final — la alerta debe colgarse de la MISMA rama final, o contaría falsos positivos.
- `_sent_by_tenant` no se resetea entre tests (process-lifetime a propósito): los asserts del endpoint van contra el tenant FRESCO del fixture (== exacto) y `sent_total` se trata como acumulado.

### Estándares de testing

- `pytest` + `pytest-asyncio` (`loop_scope="session"`) + httpx `ASGITransport` contra la app real y el Postgres de dev; self-seed/self-clean; sin mocks de DB; un comportamiento por test. ASGITransport NO corre el lifespan — el worker se ejercita con `step()` directo + `FakeGateway`.
- Entorno PARALELO: el Postgres de dev es compartido — fallos claramente ambientales (contención/cleanup ajeno) se anotan, no se "arreglan"; la suite completa corre en el merge.

### Notas de estructura del proyecto

- **Nuevos:** `backend/app/core/alerts.py`, `backend/app/api/observability.py`, `backend/tests/test_observability.py`.
- **Modificados:** `backend/app/core/scheduler.py`, `backend/app/core/send_worker.py`, `backend/app/core/capture.py`, `backend/app/db/repos/batches.py`, `backend/app/main.py`, `backend/tests/conftest.py`.
- Legacy `core.py`/`app.py`/`auto_sender.py` congelados en la raíz — solo referencia. **🔒 JAMÁS leer contenido bajo `respuestas/`. JAMÁS tocar `.env` ni `anon.session`.**

### Referencias

- [Source: planning-artifacts/epics.md#Story-4.3 — ACs verbatim; #Epic-4 ("structured logs + FloodWait alerting")]
- [Source: implementation-artifacts/4-1-watchdog…md — idiom de alerta global + log estructurado + reloj inyectable; el watchdog pausa, 4.3 solo observa]
- [Source: implementation-artifacts/4-2-admission…md — cola FIFO durable; `count_admitted`/cap reutilizados; "Alertas de FloodWait / observabilidad estructurada → Story 4.3" en su cerco]
- [Source: implementation-artifacts/2-4…md / 2-5…md — governor ×1.5/techo/decay; logs estructurados del pipeline; regla "internals jamás son settings"]
- [Source: implementation-artifacts/3-1…md — bucket unmatched (`_unmatched_total`), carrera send→record y el intento final]
- [Source: código actual @ 024962a — backend/app/{core/scheduler.py, core/send_worker.py, core/capture.py, core/watchdog.py, api/watchdog.py, api/admin.py, db/repos/batches.py, services/admission.py}]

## Dev Agent Record

### Agent Model Used

claude-fable-5 (Fable 5) — BMad dev agent (backend), 2026-06-12

### Debug Log References

- Gates backend: `ruff check app/ tests/` limpio; `mypy app` limpio (46 archivos); `pytest tests/test_observability.py` — 10 passed; suite COMPLETA `pytest -q` — **275 passed, 0 failed** (42s).
- Cero migraciones, cero DDL: la story no toca el schema (contadores process-memory documentados).
- Iteración de tests: dos fixes locales al módulo de tests (el FakeGateway recibe el texto CON el prefijo del gate — assert por `endswith`; el slice test debe alimentar la ventana de alerts además del scheduler, espejo del wiring real del worker). Sin cambios de producción derivados.

### Completion Notes List

- Tasks 1–6 implementadas según el diseño; AC 1 (umbral de FloodWaits → alerta al owner), AC 2 (logs estructurados + GET owner-only con los cuatro contadores del epic más watchdog y admisión) y AC 3 (alerta por crecimiento del bucket unmatched) cubiertos por la suite. **Ningún AC saltado**; el único ítem humano es el smoke con journalctl en producción (no bloquea).
- `core/alerts.py`: `SlidingAlert` con reloj inyectable + latch anti-spam que se re-arma solo al drenar la ventana (a diferencia del watchdog: informativo, jamás pausa). Evento global único `guardrail.alert` discriminado por `kind` (`flood_wait` | `unmatched_replies`) — contrato listo para el agente de frontend.
- Contadores donde nacen los eventos: `Scheduler.flood_events_total`/`governor_raises` (raise contado SOLO si `g_min` subió de verdad — verificado en el techo de 30s), `capture.unmatched_total()`, `send_worker.sent_by_tenant()`. El endpoint solo lee; mecánica de governor/admisión/captura byte a byte intacta.
- El log `event=flood_wait` ganó `flood_total=`/`raises_total=` — los raises quedan greppables aunque `g_min` sature; ningún test previo dependía del formato anterior (verificado por grep antes de tocar).
- La alerta de unmatched se cuelga de la rama FINAL de atribución (la que de verdad bucketiza) — los reintentos de la carrera send→record no producen falsos positivos (test dedicado).
- `GET /api/observability` owner-only server-side (`require_role("owner")`): expone tenant_ids y volúmenes cross-tenant — admin y client reciben 403 (test dedicado). Slices: sends por tenant + total, flood (totales, raises, g_min, ventana, alert_active), unmatched (total, ventana, alert_active), watchdog.status() verbatim, admisión (cap/admitted/waiting con `count_waiting` nuevo).
- Decisión local del dev: `is_alerting()` re-arma el latch al LEER cuando la ventana ya drenó — el GET reporta `alert_active` honesto sin esperar un evento nuevo que pode la deque.
- Frontend intencionalmente fuera (agente paralelo): el contrato del evento y del GET queda documentado en Task 7.

### File List

- `backend/app/core/alerts.py` — NUEVO: `SlidingAlert` + singletons `flood_alert` (3/600s) y `unmatched_alert` (5/600s), evento `guardrail.alert`, log `event=guardrail_alert`.
- `backend/app/core/scheduler.py` — modificado: contadores `flood_events_total`/`governor_raises` en `reset()`/`note_flood_wait` + properties.
- `backend/app/core/send_worker.py` — modificado: log flood_wait con `flood_total=`/`raises_total=`, `alerts.note_flood_wait()` en la rama FloodWait, accessor `sent_by_tenant()`.
- `backend/app/core/capture.py` — modificado: `alerts.note_unmatched()` en el bucket final, accessor `unmatched_total()`.
- `backend/app/db/repos/batches.py` — modificado: `count_waiting()` (sección admission).
- `backend/app/api/observability.py` — NUEVO: `GET /api/observability` owner-only (slices flood/unmatched/watchdog/admission/sends).
- `backend/app/main.py` — modificado: registro de `observability_router`.
- `backend/tests/conftest.py` — modificado: fixture autouse `reset_alerts`.
- `backend/tests/test_observability.py` — NUEVO (10 tests): umbral/anti-spam/re-arme del SlidingAlert, contadores del governor hasta el techo, wiring worker (3 FloodWaits → contadores + logs + 1 alerta), wiring capture (5 unmatched → bucket + 1 alerta; reintento no cuenta), endpoint owner-only, endpoint con todos los slices, profundidad de admisión con cola viva.
