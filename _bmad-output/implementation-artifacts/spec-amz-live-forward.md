---
title: 'Reenviar lives del gate Amazon a un canal de Telegram'
type: 'feature'
created: '2026-06-20'
status: 'done'
baseline_commit: '77d56f7c08c38ff54afc40a1d8696978fc9c217c'
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/project-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Toda "live" (veredicto Amazon `Approved`) del gate cookie-mode ya se guarda en `responses` (`kind='full'`, `status='ok'`), pero no hay forma de que el owner las vea todas juntas en un solo lugar. El owner quiere que cada live de cualquier tenant se reenvíe automáticamente a un canal/grupo de Telegram que él controla, para tenerlas todas registradas y a la mano.

**Approach:** Al capturar un veredicto `Approved` nuevo en una sesión cookie-mode, reenviar la **response original verbatim** (el `clean_text` ya redactado — sin el rewrite de tarjeta LIVE/DEAD) al canal global configurado por el owner. El canal se guarda como un setting runtime (`system_settings`, key `live_forward_channel`) editable desde `/admin`, reusando el patrón de "intervalo de envío" / "cap de admisión". El envío sale por un método nuevo `gateway.send_to()` fuera del round-robin, best-effort: si falla, se loggea y la captura no se afecta.

## Boundaries & Constraints

**Always:**
- Reenviar **`clean_text`** (la response ya redactada: "Checked By"/Credits ya scrubeados). **NUNCA** `reply.text` crudo ni `display_transform` (sin rebrand — el owner pidió "la response original sin reescribir").
- Reenviar **una sola vez por live**: solo en la primera transición a `ok` (`status == STATUS_OK and previous_status != STATUS_OK`), nunca en cada edición/revisión.
- Telethon confinado a `core/telegram.py`: el reenvío sale por un método nuevo del `gateway`; ningún otro módulo importa Telethon ni captura sus excepciones. `parse_mode=None`.
- El reenvío es **post-commit** y **best-effort**: cualquier fallo (FloodWait, canal no resuelto, sesión caída) se loggea y se ignora; jamás revierte la transacción ni bloquea la captura.
- El setting de canal es **owner-only** (`require_owner`); reusa `system_settings_repo` (upsert race-free). Vacío/ausente = reenvío desactivado.
- Validar el canal al guardarlo vía `gateway.resolve_one()` (igual que destinos); persistir el marked chat id resuelto.

**Ask First:**
- Añadir cualquier metadato de atribución (tenant/hora) al mensaje reenviado — el owner pidió la response **verbatim**. Si se necesita saber de qué cliente es cada live, renegociar.
- Cualquier cola/retry de reenvíos fallidos (hoy es best-effort sin cola).

**Never:**
- No reenvíos por-tenant ni configuración por cliente (alcance: un único canal global del owner).
- No vista/pantalla nueva de "Amz Lives" (el canal TG + el Historial existente bastan).
- No tocar el pacing del scheduler ni el send_log/atribución: el reenvío no pasa por `send()` ni cuenta como envío de lote.
- No migración de esquema (se reusa `system_settings`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Live nueva, canal configurado | sesión cookie-mode, verdict `Approved`, primera transición a `ok`, `live_forward_channel` set | `clean_text` reenviado verbatim al canal vía `gateway.send_to` | fallo de envío → log `event=live_forward_failed`, captura intacta |
| Live nueva, canal sin configurar | igual, `live_forward_channel` = "" / ausente | no se reenvía nada | N/A |
| Re-edición de mensaje ya-live | verdict `Approved` pero `previous_status` ya `ok` | no reenvía (sin duplicado) | N/A |
| Veredicto no-live | cookie-mode `Declined`/`cookie_dead`/`format_error` | no reenvía | N/A |
| ✅ de gate normal (no cookie) | `cookie_mode=False`, status ok | no reenvía | N/A |
| Owner guarda canal válido | `PUT /api/admin/live-channel` con id/@canal resoluble | 200, persiste marked chat id | N/A |
| Owner guarda canal inválido | id no resoluble (o gateway no autorizado) | 400 `invalid_live_channel`, no persiste | reject |
| Owner limpia canal | `PUT` con string vacío | 200, persiste "", reenvío desactivado | N/A |

</frozen-after-approval>

## Code Map

- `backend/app/core/capture.py` -- `process_incoming`: añadir el hook de reenvío post-commit (después del bloque de verdict-signal, ~L611). `clean_text`/`status`/`previous_status`/`cookie_verdict_kind` ya están en scope.
- `backend/app/core/telegram.py` -- añadir `async def send_to(identifier, text) -> bool`: envío out-of-band best-effort (no round-robin, no pacing), captura toda excepción.
- `backend/app/services/live_forward.py` -- **NUEVO**: `LIVE_FORWARD_KEY`, `get_channel`/`set_channel` (sobre `system_settings_repo`), `forward_live(text)` (lee canal en sesión propia → `gateway.send_to`).
- `backend/app/api/admin.py` -- GET/PUT `/api/admin/live-channel` (`require_owner`), modelos `LiveChannelOut`/`UpdateLiveChannelRequest`, validación vía `gateway.resolve_one`.
- `backend/app/errors.py` -- `invalid_live_channel()` (400, code `invalid_live_channel`, copy en español).
- `frontend/app/admin/users/page.tsx` -- `LiveChannelCard` (clon de `SendIntervalCard`) + render `{isOwner && <LiveChannelCard />}`; tipos locales `LiveChannelOut`.
- `backend/tests/test_live_forward.py` -- **NUEVO**: cubrir la matriz (live→reenvía una vez; no-live/no-cookie/re-edición→no reenvía; canal vacío→no reenvía; fallo de send_to→no rompe).

## Tasks & Acceptance

**Execution:**
- [x] `backend/app/core/telegram.py` -- añadir `send_to(identifier, text) -> bool`: si `client is None or not authorized` → False; `try: send_message(identifier, text, parse_mode=None); return True` `except Exception` → log warning, False. Confina Telethon aquí.
- [x] `backend/app/services/live_forward.py` -- NUEVO. `LIVE_FORWARD_KEY="live_forward_channel"`; `get_channel`/`set_channel` sobre `system_settings_repo`; `forward_live(text)` abre `async_session_factory`, lee canal, si vacío retorna, si no resuelve `int|str` y llama `gateway.send_to`.
- [x] `backend/app/core/capture.py` -- import `live_forward` service (lazy, en el hook); tras el bloque verdict-signal (post-commit) añadir: si `cookie_mode and cookie_verdict_kind == VERDICT_APPROVED and status == STATUS_OK and previous_status != STATUS_OK` → `await live_forward_service.forward_live(clean_text)`.
- [x] `backend/app/errors.py` -- `invalid_live_channel()` 400.
- [x] `backend/app/api/admin.py` -- import `gateway` + `live_forward` service; GET/PUT `/live-channel` owner-only; PUT: vacío→guarda ""; no-vacío→`resolve_one`, None→`invalid_live_channel`, sino guarda `str(resolved)`.
- [x] `frontend/app/admin/users/page.tsx` -- `LiveChannelCard` (patrón `SendIntervalCard`: useQuery GET `/api/admin/live-channel`, useMutation PUT, draft/banner, `SectionCard` legend "Canal de Telegram para lives", `Field` texto, `Btn` Guardar) + render owner-only.
- [x] `backend/tests/test_live_forward.py` -- NUEVO: tests de la matriz I/O.

**Acceptance Criteria:**
- Given una sesión cookie-mode con `live_forward_channel` configurado, when llega un veredicto `Approved` nuevo, then `gateway.send_to(canal, clean_text)` se invoca exactamente una vez con el texto verbatim (sin rebrand LIVE/DEAD, sin "Checked By"/Credits).
- Given una live ya reenviada, when el mismo mensaje se edita y sigue `Approved`, then NO se vuelve a reenviar.
- Given un veredicto `Declined`/`cookie_dead`/`format_error`, o un ✅ de gate no-cookie, when se captura, then NO se reenvía nada.
- Given `gateway.send_to` lanza/retorna fallo, when ocurre durante captura, then la response queda persistida/emitida normalmente y solo se loggea `event=live_forward_failed`.
- Given el owner en `/admin`, when guarda un id/@canal resoluble, then se persiste el marked chat id; un id no resoluble → 400 `invalid_live_channel`; un valor vacío → reenvío desactivado.
- Given un usuario admin (no owner), when llama `PUT /api/admin/live-channel`, then 403.

## Spec Change Log

- **2026-06-21 — review patch (C1, edge-case hunter).** The frozen Boundary/AC
  describe "una vez por live" as the transition `previous_status != STATUS_OK`.
  Review proved that gate double-forwards on a `✅→❌→✅` re-bounce of the same
  message (the exact trap the credits charge already avoids). Implementation
  refined to the **first-✅-ever** predicate `not has_ok_revision(chat_id,
  message_id)` read before `add_full` — same human intent ("once per live"),
  strictly correct. Frozen text left untouched (human-owned); behavior matches
  its INTENT, only the parenthetical mechanism is superseded. Also patched in
  the same pass: safe channel parse (`as_identifier`, no `int()` 500), full
  exception-safety of `forward_live`, a `telegram_unauthorized` 503 guard on the
  admin PUT, and `send_to` demoting `authorized` on auth-loss. KEEP: forward
  reads `clean_text` (verbatim, redacted), stays post-commit + best-effort.

## Verification

**Commands:**
- `cd backend && .venv/bin/pytest tests/test_live_forward.py` -- expected: pasa.
- `cd backend && .venv/bin/ruff check app && .venv/bin/mypy app` -- expected: limpio.
- `cd frontend && npm run build` -- expected: tsc + build OK (gate real, no solo lint).

**Manual checks:**
- Con telegram autorizado: en `/admin` pegar el id/@canal, guardar (debe validar). Disparar un lote del gate amz con una cookie que dé `Approved` → el mensaje verbatim aparece en el canal una sola vez.

## Suggested Review Order

**Forward decision (the core logic — start here)**

- Entry point: the "first ✅ ever" gate (not a transition) so ✅→❌→✅ forwards once
  [`capture.py:498`](../../backend/app/core/capture.py#L498)

- Post-commit, best-effort call site (lazy import breaks the import cycle)
  [`capture.py:635`](../../backend/app/core/capture.py#L635)

**Forward mechanism**

- `forward_live`: reads the channel knob, fully exception-safe, sends verbatim
  [`live_forward.py:53`](../../backend/app/services/live_forward.py#L53)

- `as_identifier`: ASCII signed-int → int else str (no `int()` 500 on `--5`)
  [`live_forward.py:36`](../../backend/app/services/live_forward.py#L36)

**Telegram boundary (Telethon stays here)**

- `send_to`: out-of-band, unpaced, swallows errors, demotes `authorized` on auth-loss
  [`telegram.py:351`](../../backend/app/core/telegram.py#L351)

**Owner config (owner-only, system_settings)**

- PUT `/live-channel`: empty→disable, unauthorized→503, validate via resolve_one
  [`admin.py:950`](../../backend/app/api/admin.py#L950)

- `invalid_live_channel` 400 (Spanish copy)
  [`errors.py:396`](../../backend/app/errors.py#L396)

**UI**

- `LiveChannelCard` (clone of SendIntervalCard), owner-only render at line 350
  [`page.tsx:882`](../../frontend/app/admin/users/page.tsx#L882)

**Tests (peripherals)**

- Verbatim-once forward + the ✅→❌→✅ re-bounce regression
  [`test_live_forward.py:82`](../../backend/tests/test_live_forward.py#L82)
