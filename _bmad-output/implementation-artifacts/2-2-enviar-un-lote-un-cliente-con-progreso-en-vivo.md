---
baseline_commit: deea2ce078dcbb6d41a1421f44b55b6bde864d86
---

# Story 2.2: Enviar un lote (un cliente) con progreso en vivo

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> **⚠️ TERMINOLOGY (owner decision 2026-06-11):** the product term for a prefix is **"gate"** — DB, API, code identifiers, and all UI copy (masculine: "el gate"). epics.md / architecture.md / UX docs predate the rename and still say "prefijo/prefixes" — read every "prefijo" as "gate"; where they conflict, gate wins. Established in Story 2.1 (`gates` table, `/api/gates`, `/api/admin/gates`, `/admin/gates`).

> **⚠️ OWNER ADDITIONS (2026-06-11, this story):** (1) gates get a **category** — new `gate_categories` table with owner CRUD, every gate belongs to one; (2) clients now see **name + category + value** (SUPERSEDES the 2.1 post-review "selector shows the name only" decision — the value/prefix is client-visible again); (3) gate selection is **two-step: pick category first, then gate**. ACs 12–13 and Task 0 below carry this scope; it is NOT in epics.md.

> **⚠️ SIZE NOTE (from epics.md):** largest story in the document. If the dev agent runs short on context, split backend (gateway + tables + API + worker + WS) from the Envío UI — the task groups below are ordered so the backend half (Tasks 0–9) is independently completable and testable before the frontend half (Tasks 10–14).

## Story

As a client,
I want to paste my lines, pick a gate and send the batch watching live progress,
So that my batch goes out hands-free.

## Acceptance Criteria

1. **Given** the backend service `cc-core`, **when** it starts, **then** the Telethon client connects in the FastAPI lifespan, lives only in `core/telegram.py`, and is the single owner of `anon.session`.
2. **Given** the schema, **when** this story's migration is applied, **then** `batches` and `batch_lines` tables exist with `tenant_id`, state and ordering columns.
3. **Given** a client with a valid plan on Envío, **when** they paste lines, pick a gate from the HeroUI Select (catalog-fed, never free text) and tap Enviar, **then** `POST /api/batches` validates the plan and the gate against the catalog, applies the gate with in-batch dedup, and persists the queued lines (no batch size cap).
4. **Given** an empty or whitespace-only paste, **when** the client taps Enviar, **then** the request is rejected with an error code and no batch is created.
5. **Given** the owner, **when** they open the send surface, **then** they can paste, pick a gate and send exactly like a client — owner batches enter the scheduler flagged for owner priority (route gating admits the owner role to Envío).
6. **Given** a queued batch, **when** the send worker drains it, **then** lines go out at the system-controlled interval (not editable by the client) and each line's state updates in Postgres.
7. **Given** Telegram responds with FloodWait, **when** the worker hits it, **then** it waits the requested duration and retries the same line (no line lost).
8. **Given** the WebSocket endpoint `/ws`, **when** a client connects, **then** the cookie handshake authenticates the tenant, a full snapshot arrives first, and subsequent `batch.progress` / `batch.line_sent` events are tenant-scoped — a tab opened mid-batch renders correct state immediately.
9. **Given** the Envío surface, **when** a batch is live, **then** the progress ring shows % + fraction and the flank shows exactly three metrics (enviadas · en cola, ETA, CC nuevas) — no other stats, **and** navigation is exactly Envío | Historial (bottom nav mobile, header nav desktop), **and** at idle the surface shows "Pega tus líneas y elige un gate."
10. **Given** a live batch, **when** the client submits more lines, **then** new lines append to the existing queue (no second batch).
11. **Given** a dropped WebSocket, **when** it auto-reconnects, **then** the fresh snapshot reconciles all state silently — no banners, no offline UX.
12. _(owner addition 2026-06-11)_ **Given** the schema, **when** this story's migrations are applied, **then** `gate_categories` exists (owner-managed catalog), every gate references exactly one category, and pre-existing gates are backfilled into a seed category the owner can rename; the owner manages categories (create, rename, delete) and assigns a category on gate create/edit from `/admin/gates`; deleting a category that still has gates is rejected with a clear Spanish error.
13. _(owner addition 2026-06-11)_ **Given** an authenticated client on Envío, **when** they pick a gate, **then** selection is two-step — first a category Select, then a gate Select filtered to that category — and the gate options show **name, category and value** (the value/prefix is client-visible; supersedes 2.1's name-only decision).

## Tasks / Subtasks

### Catalog extension (Task 0 — vertical slice, mirrors Story 2.1 file-for-file)

- [x] Task 0: gate categories (AC: 12, 13-backend)
  - [x] **Model + migration:** `GateCategory` in `db/models.py`: `id` PK, `name: String(80)` (unique — plain unique constraint `uq_gate_categories_name`; categories have NO soft-delete), `created_at`/`updated_at` (existing `func.now()` pattern). Add `category_id` to `Gate`: FK → `gate_categories.id` `ondelete="RESTRICT"`, indexed, NOT NULL after backfill.
  - [x] Migration (separate revision BEFORE the batches one; `down_revision = "62f6cc07f7b0"`): create `gate_categories` → seed one row `name="General"` → add `gates.category_id` nullable → backfill all existing gates to the seed row → ALTER to NOT NULL. Same backfill choreography as the `62f6cc07f7b0` name-label migration. Production has live gate rows — the backfill is not optional. Owner renames "General" later from the UI if he wants.
  - [x] **Repo** `db/repos/gate_categories.py` (gates.py idiom — global catalog, NOT tenant-scoped, module docstring says so): `list_all(session)` ordered by name, `get_by_id`, `get_by_name`, `create`, `has_gates(session, category_id) -> bool` (active gates only — retired gates don't block deletion), `delete`.
  - [x] **Errors:** `category_exists()` → 409 "Ya existe esa categoría." · `category_not_found()` → 404 "Esa categoría no existe." · `category_in_use()` → 409 "No puedes eliminar una categoría con gates. Reasigna sus gates primero."
  - [x] **Owner CRUD API** in `api/admin.py` (extend the existing router, `require_owner`, inline schemas, same `_validate_gate_name`-style validation — trimmed, non-empty, ≤80, no invisible chars): `GET /api/admin/gate-categories` → `{items, total}` · `POST` → 201; duplicate → 409 `category_exists` (catch `IntegrityError` too — TOCTOU lesson from 2.1 review) · `PATCH /{id}` rename · `DELETE /{id}` → 204; gates still assigned (non-retired) → 409 `category_in_use`. Guard ids > int32 → 404 (2.1 review lesson).
  - [x] **Gates API changes:** `CreateGateRequest`/`UpdateGateRequest` gain required `category_id` (validated to exist → 404 `category_not_found`); `GateOut` gains `category_id` + `category_name`. Applies to BOTH `/api/admin/gates` and the client `GET /api/gates` (eager-load the relationship — no N+1, no async lazy-load surprises: `selectinload`).
  - [x] **`/admin/gates` page:** add a **Categorías** management block above the gates table (same inline-form idiom: create input + list with inline rename/delete, delete shows the `category_in_use` error when rejected); gates table gains a **Categoría** column; gate create/edit forms gain a category `Select` (required). Max one inline confirm layer (UX-DR21).
  - [x] **Client category source:** NO new client endpoint — the client UI derives the category list by grouping `GET /api/gates` items by `category_name` (only categories that actually have active gates matter to clients).
  - [x] **Tests** `backend/tests/test_gate_categories.py` (+ touch `test_admin_gates.py`: existing create/edit tests now send `category_id` — seed a category in fixtures): owner CRUD happy paths; duplicate name 409; rename persists; delete empty category 204; delete with active gates 409 `category_in_use`; delete after retiring its gates 204; gate create without/with bad `category_id` → 422/404; admin+client 403 on all `/api/admin/gate-categories`; client `GET /api/gates` items carry `category_id`+`category_name`; validation rejects empty/whitespace/invisible/>80.

### Backend (Tasks 1–9 — independently completable + testable)

- [x] Task 1: promote Telegram settings into `app/config.py` (AC: 1, 6)
  - [x] Add to `Settings`: `telegram_api_id: int = 0`, `telegram_api_hash: str = ""`, `telegram_session_path: str = "/var/lib/cc/anon.session"`, `telegram_target: str = ""` (destination username, `@` optional — strip it), `send_interval_seconds: float = 10.0`. The first three already exist in `backend/.env` (read today only by `scripts/telegram_auth.py` — its docstring says "Story 2.2 promotes them"; do NOT change the script). `telegram_target` and `send_interval_seconds` are new keys. **Defaults are deliberately permissive** (unlike `database_url`): a machine without Telegram keys must still import the app and run the full test suite — the gateway treats missing/zero credentials as "not authorized" and sending stays down (503), nothing crashes.
  - [x] `send_interval_seconds` is the system-controlled interval (FR12): server config only, never accepted from any request. Default 10.0 (= architecture `P(1)`); Story 2.4 replaces the constant with the adaptive formula.
  - [x] Update `backend/.env.example`: uncomment/move the Telegram block out of "script-only" wording, add `TELEGRAM_TARGET=` and `SEND_INTERVAL_SECONDS=` with comments. NEVER print or commit real values from `backend/.env`.
  - [ ] (HUMAN — Richard) For the manual smoke (Task 16) the dev `backend/.env` needs real values for the three credentials + `TELEGRAM_TARGET` (ask Richard for the target; do not invent values). Settings stays import-time (`settings = get_settings()`), same as today.
- [x] Task 2: Telethon gateway `backend/app/core/telegram.py` (AC: 1, 7)
  - [x] Create `backend/app/core/__init__.py` + `telegram.py` — the ONLY module importing telethon anywhere in `app/` (architecture boundary; enforce by review, telethon imports nowhere else).
  - [x] `TelegramGateway` class: `connect()` → `TelegramClient(settings.telegram_session_path, api_id, api_hash, catch_up=True)`, `await client.connect()`, `self.authorized = await client.is_user_authorized()`; resolve `settings.telegram_target` once via `get_input_entity` (store the input entity; on resolution failure log and mark `self.target_ok = False` — do not crash the app). `disconnect()` for shutdown.
  - [x] `async send(text: str) -> int`: `client.send_message(entity, text)`, return `message.id` (Story 2.5's `send_log` will consume it — return it now so the worker signature doesn't change). Let `FloodWaitError` propagate to the worker (the worker owns retry policy).
  - [x] If `anon.session` is missing/unauthorized: the app must still BOOT (login/admin keep working); `authorized=False`, worker idles, `POST /api/batches` → 503 `telegram_unauthorized`. Re-auth is operational (run `scripts/telegram_auth.py` on the VPS); `AuthKeyError` detection/watchdog is Story 4.1 — do NOT build it.
  - [x] Module-level singleton `gateway = TelegramGateway()` (same idiom as `settings`), wired in lifespan (Task 8).
- [x] Task 3: migration — `batches` + `batch_lines` (AC: 2)
  - [x] `Batch` model in `db/models.py`: `id` PK, `tenant_id` FK→tenants ondelete CASCADE indexed, `gate_value: String(20)` + `gate_name: String(80)` (SNAPSHOT verbatim from the catalog row at creation — denormalized on purpose: retiring/renaming a gate never rewrites history, per Story 2.1 design), `state: String(20)` (`'sending' | 'completed'` in this story; 2.3 adds `paused`/`stopping`/`stopped`, 2.5 adds `cancelled` — String, NOT a DB enum, so later stories don't need ALTER TYPE), `is_owner_priority: Boolean server_default=false()` (set when creator role == owner; CONSUMED by Story 2.4, only written here), `created_at`/`updated_at` (same `func.now()` pattern as existing models).
  - [x] `BatchLine` model: `id` PK, `batch_id` FK→batches ondelete CASCADE indexed, `tenant_id` FK→tenants ondelete CASCADE indexed (denormalized for isolation queries and 2.5's send_log), `position: int` (ordering; unique `uq_batch_lines_batch_id_position` on `(batch_id, position)`), `text: Text` (the FULL message with gate applied), `state: String(20)` (`'queued' | 'sending' | 'sent'`; 2.5 adds `failed`/`cancelled`), `sent_at: timestamptz nullable`, `created_at`. Composite index `ix_batch_lines_batch_id_state` on `(batch_id, state)` (the worker's hot query).
  - [x] `alembic revision --autogenerate -m "batches and batch_lines"`, review, `down_revision` = Task 0's gate-categories revision (which itself chains off `62f6cc07f7b0`). `alembic upgrade head` on dev Postgres.
- [x] Task 4: repo `backend/app/db/repos/batches.py` (AC: 2, 3, 6, 10)
  - [x] Follow `repos/gates.py` idiom: pure ORM, flush not commit, module functions. EVERY function takes `tenant_id` explicitly (tenant-scoped — this is NOT the gates global exception).
  - [x] `get_live_batch(session, tenant_id) -> Batch | None` — state == 'sending' (one live batch per tenant is the invariant this story establishes).
  - [x] `create_batch(session, *, tenant_id, gate_value, gate_name, is_owner_priority) -> Batch`.
  - [x] `add_lines(session, *, batch, texts: list[str], start_position: int) -> list[BatchLine]`.
  - [x] `pending_texts(session, batch_id) -> set[str]` — texts of lines in state `queued`/`sending` (the append-dedup set; SENT lines may be re-queued, legacy semantics).
  - [x] `counts(session, batch_id) -> tuple[sent, queued]` (drive progress/snapshot).
  - [x] Worker queries (used by Task 7, not by handlers): `next_queued_line(session) -> BatchLine | None` (oldest `queued` by `(batch_id, position)` FIFO across all batches — Story 2.4 replaces this selection with the round-robin scheduler), `mark_sending/mark_sent(session, line)`, `complete_if_drained(session, batch) -> bool`, `requeue_stuck_sending(session) -> int` (boot recovery, Task 7).
- [x] Task 5: errors (AC: 3, 4)
  - [x] Add to `backend/app/errors.py`: `empty_batch()` → 400 `empty_batch` "No hay líneas para enviar." · `telegram_unauthorized()` → 503 `telegram_unauthorized` "Telegram no está autorizado todavía. Contacta al administrador."
  - [x] Reuse the existing `gate_not_found()` (404) for an unknown/retired `gate_id` on batch creation.
- [x] Task 6: batch service + `POST /api/batches` (AC: 3, 4, 5, 10)
  - [x] `backend/app/services/batches.py`: `apply_gate(text: str, gate_value: str) -> list[str]` — English port of legacy `core.agregar_prefijo` (core.py:43): split lines, strip, skip blanks, prepend `f"{gate_value} "` unless the line already starts with it, dedup preserving order. Port the behavior EXACTLY (in-batch dedup is an AC).
  - [x] New router `backend/app/api/batches.py`: `APIRouter(prefix="/api/batches", tags=["batches"])`, registered in `main.py`. Inline Pydantic schemas (codebase convention): `CreateBatchRequest {text: str, gate_id: int}`, `BatchOut {id, gate_name, gate_value, state, sent, queued, total, appended: bool, added: int}` (shape consumed by the UI to flip into live mode without waiting for WS).
  - [x] `POST /api/batches`, dependency `get_current_user` (any role — owner sends exactly like a client, AC 5; plan expiry/block already enforced by the dep). Flow:
    1. `gateway.authorized` false → raise `telegram_unauthorized()` (503).
    2. Resolve gate: `gates_repo.get_by_id` active (deleted_at IS NULL) else `gate_not_found()`.
    3. `get_live_batch(tenant_id)`:
       - **None → new batch:** `apply_gate(text, gate.value)`; empty result → `empty_batch()`. Create batch (`state='sending'`, `is_owner_priority = user.role == "owner"`, snapshot `gate.value` + `gate.name`), add lines positions 0..n-1. Emit `batch.state {state:"sending"}` + initial `batch.progress` (Task 8 broadcaster).
       - **Live → APPEND (AC 10):** apply the LIVE batch's `gate_value` (the submitted `gate_id` is validated for existence but its value is IGNORED — one lote = one gate; the UI locks the selector during a live lote, Task 11). Dedup against `pending_texts()` (queued+sending only — already-sent lines may be re-queued, legacy `/api/enviar` semantics). Append at `max(position)+1...`; zero lines after dedup is NOT an error (returns `added: 0`). Emit `batch.progress`.
  - [x] `tenant_id` comes ONLY from `user.tenant_id` (never from the body — architecture mandate).
  - [x] No GET/list endpoints — the WS snapshot is the read path; don't invent REST reads this story doesn't need.
- [x] Task 7: send worker `backend/app/core/send_worker.py` (AC: 6, 7)
  - [x] Single background `asyncio.Task` created in lifespan (Task 8). Loop: `next_queued_line()` → none: idle-sleep ~1s, repeat. Found: mark `sending` (commit), `gateway.send(line.text)`, mark `sent` + `sent_at` (commit), emit `batch.line_sent {batch_id, position, text}` + `batch.progress {batch_id, sent, queued, total, eta_seconds}`; if batch drained → batch `state='completed'`, emit `batch.state {state:"idle"}`. Then cancelable-sleep `settings.send_interval_seconds`.
  - [x] Each loop iteration opens its own `async_session_factory()` session (the worker NEVER uses the request-scoped session).
  - [x] **Cancelable sleep now:** port legacy `_sleep_cancelable` (app.py:246 — `asyncio.wait_for(wake_event.wait(), timeout)`), with a module-level wake `asyncio.Event`. 2.2 itself only needs plain sleeps, but Story 2.3's pause/stop must interrupt sleeps instantly — building the primitive now avoids a worker rewrite next story.
  - [x] **FloodWait (AC 7):** catch `FloodWaitError` → broadcast GLOBAL `flood.wait {seconds}` (all tenants — architecture: every FloodWait is explained to everyone; the UI notice is Story 2.3, only the event ships now), cancelable-sleep `e.seconds`, retry the SAME line (leave it `sending`, loop back to it — `next_queued_line` must therefore prefer a line already in `sending` state, or simply retry in-place without re-querying; in-place retry is simpler and correct).
  - [x] **Other send errors:** emit tenant-scoped `error {code:"send_error", message:str(e)}`, sleep 2s, retry the same line FOREVER — legacy semantics, kept deliberately; the retry-cap=3 + `failed` state is Story 2.5's AC. Leave a `# Story 2.5 replaces retry-forever with cap=3` comment.
  - [x] **Boot recovery (NFR6):** on worker start, `requeue_stuck_sending()` (lines left in `sending` by a crash → back to `queued`) and resume draining any batch in state `sending`. Small double-send window accepted until Story 2.5's reconciliation — say so in a comment. DB-unreachable fail-stop buffering is also 2.5 — a plain try/log/sleep-retry around the loop body is enough here.
- [x] Task 8: broadcaster + lifespan wiring (AC: 1, 8)
  - [x] `backend/app/core/broadcaster.py`: tenant-scoped fan-out — `register(tenant_id, ws)`, `unregister(tenant_id, ws)`, `async emit(tenant_id, event: str, data: dict)`, `async emit_global(event, data)` (flood.wait). Envelope EXACTLY `{"event": "<name>", "data": {...}}`. Dead sockets discarded on send failure (legacy Broadcaster pattern, app.py:38). Module singleton.
  - [x] `app/main.py` lifespan: startup → `await gateway.connect()`, start worker task; shutdown → cancel worker task (await with `contextlib.suppress(asyncio.CancelledError)`), `await gateway.disconnect()`, then the existing `engine.dispose()`. Update the module docstring (it currently says "Telethon arrives in Epic 2 — do NOT add it here" — that gate is now open). Guard: tests use `ASGITransport`, which does NOT run lifespan — the app object must stay importable and testable with no Telegram/worker running (it will, since both start only inside lifespan).
- [x] Task 9: WebSocket `/ws` `backend/app/api/ws.py` (AC: 8, 11)
  - [x] `@router.websocket("/ws")` registered in `main.py`. Handshake: read `settings.session_cookie_name` from `websocket.cookies`; validate with the SAME chain as `deps._resolve_session_user` (valid session → not blocked → plan not expired → not must_change_password) but via a small WS-local helper (HTTP deps raise AppError, which a WS route can't render) — on any failure `await websocket.close(code=4401)` after accept. On success the socket is bound to `user.tenant_id` for its lifetime.
  - [x] Snapshot FIRST, always (AC 8/11): `{"event":"snapshot","data":{...}}` with: `state` (`"sending"` if a live batch else `"idle"`), `batch_id`, `gate_name`, `gate_value`, `sent`, `queued`, `total`, `eta_seconds`, `cc_new: 0` (hardcoded 0 until Epic 3 — the metric slot must exist for the UI). Build it from a helper (e.g. `services/batches.snapshot(session, tenant_id) -> dict`) so it's unit-testable without a socket.
  - [x] After snapshot: `broadcaster.register(tenant_id, ws)`; keep-alive `receive_text()` loop; `finally: unregister` (legacy /ws pattern, app.py:585). WS is server→client ONLY — ignore/discard any client payload, never act on it.
  - [x] `eta_seconds = queued * settings.send_interval_seconds` (honest, recomputed per event/snapshot; UX-DR14 — the adaptive `G×n` version is Story 2.4). Same formula in `batch.progress` emissions.

### Frontend (Tasks 10–14)

- [x] Task 10: WS client store `frontend/lib/ws.ts` (AC: 8, 9, 11)
  - [x] Singleton auto-reconnecting native WebSocket to `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws` (dev: next.config.mjs already rewrites `/ws` → 8000; prod: Caddy routes it). Reconnect with capped backoff (e.g. 1s doubling to 10s), forever; NO offline banners or queued actions (AC 11 — explicit UX decision).
  - [x] One reducer-style handler per event name updating a single store (architecture state pattern); expose a `useLiveBatch()` hook via `useSyncExternalStore`. Store shape mirrors the snapshot: `{state: "idle"|"sending", batchId, gateName, gateValue, sent, queued, total, etaSeconds, ccNew}`.
  - [x] Every `snapshot` REPLACES the whole store (that's the silent reconnect reconciliation — AC 11). `batch.progress` updates counts/eta; `batch.state` updates `state`; `batch.line_sent` is received and tolerated (consumers arrive in 2.5/3.2); unknown events ignored without crashing (forward-compat: 2.3+ adds events).
- [x] Task 11: Envío surface `frontend/app/(client)/page.tsx` + `frontend/components/batch/` (AC: 3, 4, 9, 10)
  - [x] Replace the stub page. Compose from `components/batch/` (architecture tree): suggested `send-form.tsx`, `progress-ring.tsx`, `metric.tsx` — match existing component style (HeroUI v3 react-aria idiom from `app/admin/gates/page.tsx`: `Form`/`TextField`/`Button`/`Select`, TanStack Query, inline errors).
  - [x] **Gate selector — TWO-STEP (AC 13, owner addition):** fed by `GET /api/gates` (TanStack Query, key `["gates"]`). Step 1: "Categoría" `Select` — options derived by grouping the items by `category_name`. Step 2: "Gate" `Select`, enabled once a category is picked, filtered to it; each option shows the **name + value** (name in Public Sans, value in mono — e.g. `Visa Premium` + `.zo`), and the picked category is already visible in step 1 — that covers "name, category and value". Changing the category resets the gate pick. Never free text (UX-DR9). Both required before sending; the POST still sends only `gate_id`.
  - [x] **Textarea:** paste-first ("Pega tus líneas"), one line = one message. Enviar button (accent, primary). Client-side guard: whitespace-only → inline error without firing the request; the backend's `empty_batch` 400 maps to the same inline error (defense in both layers, AC 4).
  - [x] **Idle state:** ring hidden; textarea + selector prominent; copy EXACTLY "Pega tus líneas y elige un gate." (epics idle copy with the gate rename applied).
  - [x] **Live state (driven ONLY by the WS store — UX-DR12, no optimistic state):** progress ring = HeroUI `CircularProgress` ~128px, accent stroke while sending; center % (`metric-lg` mono 26px/800) + fraction `34 / 120` (`data-mono` muted). Flank: EXACTLY three metrics as `label-caps` (10px tracked uppercase) + `metric` (mono 18px/800): "ENVIADAS · EN COLA" (`{sent} · {queued}`), "ETA" (`~12 min` format — honest estimate from `etaSeconds`, see helper below), "CC NUEVAS" (success green, renders `ccNew` = 0 until Epic 3). **No other stats anywhere** (UX-DR3; banned: filler stats, UX-DR21).
  - [x] ETA formatting helper: `null/0 queued` → "—"; `< 60s` → `~Ns`; else `~N min` (rounded). Never a fake-precise countdown (UX-DR14).
  - [x] **Append (AC 10):** during a live lote the textarea + Enviar stay usable; BOTH selects are DISABLED and a gate chip shows the active gate as `{gateName} · {gateValue}` (value in mono — HeroUI `Chip`, `surface-secondary` bg, 1px border — UX-DR9/DESIGN prefijo-chip token). Submitting POSTs with the live gate's id (or any valid id — the backend ignores it on append); on `{appended: true}` show nothing special, the queue count just grows via WS.
  - [x] After a successful POST (new batch), the UI may seed the store from `BatchOut` so the ring appears without waiting for the next WS event — but `batch.state`/`snapshot` remain the source of truth thereafter.
  - [x] **Pause/Detener controls, state pill, FloodWait notice: NOT in this story** (2.3). Do not render placeholder buttons.
  - [x] Errors per contract: known `code` → inline Spanish copy, fallback `err.message`; network fallback "No pudimos conectar. Intenta de nuevo." (established pattern). `telegram_unauthorized` 503 renders its server message as a banner `Alert`.
- [x] Task 12: client navigation + Historial stub (AC: 9)
  - [x] `frontend/components/client-nav.tsx` (or `app/(client)/layout.tsx` carrying it): exactly two items **Envío | Historial** → `/` and `/sessions`. Mobile (`< lg`): fixed bottom nav, active item `surface-tertiary` bg; Envío item carries a 6px live dot — success green while `state === "sending"` (warning-while-paused arrives with 2.3). Desktop (`≥ lg`): same two items inline in a top header strip. Reuse the WS store for the dot.
  - [x] Mobile layout order (DESIGN.md): header strip → ring block → (controls slot, empty until 2.3) → data-panel area (EMPTY this story — the dual Completa/Filtrada panel is Story 3.2; do NOT build a queue list or response panels) → bottom nav. Cockpit never scrolls away while live.
  - [x] `frontend/app/(client)/sessions/page.tsx` STUB so Historial doesn't 404: renders the empty state "Todavía no tienes sesiones. Tu primer lote crea una." with a link to Envío. Story 3.3 builds the real list — keep it minimal, comment it as a stub.
  - [x] Add a "Cerrar sesión" affordance in the client header (clients currently have NO logout — admin pages do; reuse the users-page logout pattern).
- [x] Task 13: middleware + infra touch-ups (AC: 5, 8)
  - [x] `frontend/middleware.ts` matcher: add `ws(?:/|$)` to the exclusion group (the backend owns WS auth via the cookie handshake; middleware must not consume a `/me` round-trip — or worse, interfere with the upgrade — on `/ws`).
  - [x] Owner on Envío (AC 5): verify, no code expected — middleware only restricts `/admin/*` for clients and `/admin/gates` for non-owners; owner navigating to `/` falls through today. Confirm with a manual check; do NOT add an owner→/admin redirect.
  - [x] `deploy/Caddyfile`: the deferred item from 1.7 ("widen `/ws` matcher when 2.2 ships the endpoint") — the endpoint IS exactly `/ws`, so the existing exact `handle /ws` matcher is correct as-is. Update `_bmad-output/implementation-artifacts/deferred-work.md` to mark that item resolved (no Caddy change).
- [x] Task 14: regenerate API types (AC: 3)
  - [x] Backend running → `npm run generate:api` → commits the regenerated `frontend/types/api.ts` (GENERATED — never hand-edit). Note: WS event payloads are NOT in OpenAPI; type them by hand in `lib/ws.ts` (this is the one legitimate hand-typed contract — keep the shapes next to the reducer).

### Tests + gates (Task 15–16)

- [x] Task 15: backend tests (AC: 1–8, 10)
  - [x] `backend/tests/test_batches.py` — ASGI tests per `conftest.py` idiom (`ctx` fixture, self-seeding, self-cleaning; clean up batches/lines + gates in teardown):
    - anonymous POST → 401; expired-plan client → 403 `plan_expired`.
    - empty / whitespace-only text → 400 `empty_batch`, no batch row created.
    - unknown gate_id and retired (soft-deleted) gate_id → 404 `gate_not_found`.
    - happy path: lines stored WITH gate applied (`".zo abc"`), in-batch dedup (duplicate lines collapse; line already carrying the gate prefix not double-prefixed), positions 0..n-1, batch `state="sending"`, `is_owner_priority=False`.
    - owner POST → `is_owner_priority=True` (AC 5).
    - no cap: a few hundred lines → 200/201 (AC 3 — don't test 50k, just prove no artificial limit).
    - append: second POST while live → same `batch_id`, `appended=True`, dedup against queued lines only (an already-SENT text re-queues), live batch's gate applied even if a different valid `gate_id` was submitted.
    - `telegram_unauthorized`: monkeypatch `gateway.authorized = False` → 503.
    - tenant isolation: client B's `get_live_batch`/snapshot shows nothing from client A's live batch.
  - [x] Worker tests (no real Telegram — fake gateway): a `FakeGateway` fixture (`authorized=True`, records sent texts, returns incrementing ids, programmable to raise `FloodWaitError(seconds=0)` once then succeed). With `send_interval_seconds=0` (monkeypatch settings), drive the worker's drain coroutine directly (factor the per-line step or bounded drain loop so a test can await it without the infinite task): line states queued→sent with `sent_at`, batch → `completed` when drained, FloodWait → same line eventually sent exactly once after retry (AC 6, 7), generic error → line retried (not lost, not marked failed).
  - [x] WS tests: unit-test the snapshot builder (idle shape vs live shape, `cc_new == 0`, eta math) — that covers AC 8's data contract without socket plumbing. Handshake (no cookie / bad cookie → close 4401; good cookie → first frame is `snapshot`) via Starlette's sync `TestClient.websocket_connect` if it coexists with the async suite; if the loop-scope fight isn't worth it, the handshake helper can be unit-tested directly — don't burn hours on socket test plumbing.
- [x] Task 16: verification gates (all ACs)
  - [x] Backend: `ruff check .`, `mypy app`, `pytest` — all green (73 pre-existing tests must stay green).
  - [x] Frontend: `npm run lint`, `npx tsc --noEmit`, `next build` — green; `/` and `/sessions` in build output.
  - [ ] (HUMAN — needs Richard's credentials/target; sends real Telegram messages) Manual smoke (dev): with a real `backend/.env` (Richard's), `uvicorn app.main:app --reload` + `npm run dev` → login as client → paste 3 lines → pick gate → Enviar → ring fills, fraction advances at the configured interval, second tab shows identical state from its snapshot, kill/reopen tab mid-batch reconciles silently. **Do NOT run this against the production target without Richard's go-ahead — it sends real Telegram messages.**

## Dev Notes

### Critical context — read before coding

- **What this story is NOT (scope fence — the epic splits the pipeline across 2.2–2.5 + 3.1/3.2; building ahead = scope creep):**
  - Pause/resume/stop endpoints, control buttons, state pill, FloodWait UI notice, "ETA al reanudar" → **Story 2.3** (this story ships the `flood.wait` EVENT and the cancelable-sleep primitive only).
  - Round-robin across tenants, owner-priority enforcement, adaptive interval `G = max(G_min, P(n)/n)`, FloodWait governor → **Story 2.4** (this story only FLAGS `is_owner_priority` and uses a fixed configured interval; naive global FIFO is fine for one client).
  - `send_log` table, retry cap 3 + `failed` lines, fail-stop without DB, restart reconciliation, mid-batch expiry cancellation → **Story 2.5** (this story keeps legacy retry-forever and a simple boot re-queue; comment both).
  - Response capture, capture sessions, CC extraction, Completa/Filtrada views → **Stories 3.1/3.2** ("CC NUEVAS" renders a hardcoded 0; the data-panel area stays empty; `batch.line_sent` has no UI consumer yet).
- **One live batch per tenant** is the invariant: `get_live_batch` returns the single `state='sending'` batch; POST appends to it (AC 10). Nothing in this story ends a batch except draining it (`completed`) — stop arrives in 2.3.
- **Gate referenced by `gate_id`, snapshotted as strings.** `batches.gate_value`/`gate_name` are copied verbatim at creation — no FK to `gates`, by design (Story 2.1: retiring/renaming a gate never rewrites history). Category is deliberately NOT snapshotted into batches — it's a browsing aid, not send history.
- **Client-visible gate fields (owner decision 2026-06-11, supersedes 2.1's "name only"):** clients see **name + category + value**. Selection is two-step (category → gate). Categories live in their own `gate_categories` table (owner CRUD, no soft-delete, delete blocked while non-retired gates reference it); every gate requires a category; existing rows backfill into a seed "General" category.
- **Append semantics decision (recorded here):** while a batch is live, the submitted `gate_id` is validated but its value ignored — new lines take the LIVE batch's gate (one lote = one gate; sessions bind tenant+gate in Epic 3). UI enforces the same by disabling the selector during a live lote. Legacy applied the request's prefix (app.py:189 applies it before the append check) — we deliberately diverge; mixing gates in one queue would poison Epic 3's session binding.
- **Interval is server config** (`send_interval_seconds`, FR12). It appears in NO request schema and NO response needs to expose it (ETA already encodes it). UX-DR21 bans a user-editable interval.
- **The app must boot without Telegram.** Unauthorized/missing `anon.session` → admin + login + history all keep working; only sending is down (503). Don't make `gateway.connect()` failures fatal; log and continue.
- **Single owner of `anon.session`:** only `cc-core` (uvicorn) opens it, only `core/telegram.py` imports telethon. The legacy root `app.py`/`auto_sender.py` use the OLD root-level `anon.session` (different file) — frozen reference, do not touch, and never point `telegram_session_path` at the repo-root file.
- **Dev `.env` needs the new keys** (`TELEGRAM_TARGET`, optionally `SEND_INTERVAL_SECONDS`, plus a dev `TELEGRAM_SESSION_PATH` since the default is `/var/lib/cc/...`). Real credentials exist in `backend/.env` (gitignored) — never print or commit them. Production `.env` on the VPS needs the same keys at deploy; note it in the story's completion notes for the deploy checklist.
- **WS auth failure code 4401** (custom app range): accept → validate → `close(code=4401)`. Browsers can't read close codes reliably pre-accept; accept-then-close is the testable pattern.
- **🔒 NEVER read anything under `respuestas/`** (root) — hard rule. Legacy files are reference only.

### Legacy port map (working code → this story)

| Legacy (frozen reference) | Port target | Notes |
| --- | --- | --- |
| `core.agregar_prefijo` (core.py:43) | `services/batches.apply_gate` | exact behavior: strip, skip blanks, prefix unless present, in-batch dedup |
| `Engine._worker` loop (app.py:264) | `core/send_worker.py` | FIFO drain, interval between sends, FloodWait wait+retry-same-line, error retry-forever |
| `Engine._sleep_cancelable` (app.py:246) | worker cancelable sleep | `asyncio.wait_for(wake.wait(), timeout)` + clear; 2.3 consumes the wake event |
| `Broadcaster` (app.py:38) | `core/broadcaster.py` | + tenant scoping (dict by tenant_id) + `{"event","data"}` envelope |
| `Engine.snapshot` (app.py:148) | `services/batches.snapshot` | DB-derived; snapshot-first on every WS connection |
| `/ws` endpoint (app.py:585) | `api/ws.py` | + cookie handshake; keep-alive receive loop; server→client only |
| `calcular_eta` (core.py:203) | `queued × interval` | new formula is simpler AND honest for a fixed interval; measured-pace ETA dies with 2.4's adaptive math anyway |

Engine state in memory (cola, counters) becomes **Postgres rows** (NFR6 durability) — the worker and snapshot read DB, not module state. Counters in this story are per-batch derived counts (`sent`/`queued`), not the legacy lifetime totals.

### Existing code you will touch (current state)

| File | State today | This story |
| --- | --- | --- |
| `backend/app/config.py` | `Settings`: database_url + auth/cookie/throttle keys; import-time singleton | ADD telegram + interval keys |
| `backend/app/main.py` | lifespan = engine.dispose only; docstring says "Telethon arrives in Epic 2" | lifespan: gateway connect + worker task; register batches + ws routers; update docstring |
| `backend/app/db/models.py` | Tenant, User, AuthSession, Gate | ADD Batch, BatchLine |
| `backend/app/errors.py` | AppError + factories (`gate_not_found` exists, 404 "Ese gate no existe.") | ADD `empty_batch`, `telegram_unauthorized` |
| `backend/app/api/deps.py` | `get_current_user` (blocked→expired→flag chain), `require_role` | use as-is for REST; mirror the chain in a WS-local helper |
| `backend/app/db/repos/gates.py` | `get_by_id`, `list_active`, … | use `get_by_id` (check `deleted_at IS NULL` yourself or add a param) |
| `frontend/app/(client)/page.tsx` | 9-line stub "Envío — próximamente" | REPLACE with the full surface |
| `frontend/middleware.ts` | matcher excludes login/expired/api/_next/static | ADD `ws` exclusion |
| `frontend/lib/api.ts` | get/post/patch/delete + error contract + plan_expired/password redirects | use as-is |
| `frontend/types/api.ts` | GENERATED | regenerate |
| `deploy/Caddyfile` | `handle /ws` exact match, comments say "endpoint ships in 2.2" | no change needed (endpoint is exactly `/ws`); resolve the deferred-work item |
| `backend/scripts/telegram_auth.py` | reads TELEGRAM_* from backend/.env itself | NO changes — its docstring already anticipates this story promoting the keys |

Migration chain head: `62f6cc07f7b0` (gate name label). This story adds TWO revisions in order: gate-categories (Task 0, `down_revision = 62f6cc07f7b0`) → batches/batch_lines (Task 3, chained after it).

### Architecture compliance (non-negotiable)

- Telethon confined to `backend/app/core/telegram.py`; one process owns `anon.session`. [Source: architecture.md#Architectural-Boundaries]
- WS: single `/ws`, tenant-scoped by session cookie, server→client only, envelope `{"event","data"}`, snake_case dot-scoped names (`batch.progress`, `batch.line_sent`, `batch.state`, `flood.wait`, `error`), snapshot-first on every connection. [Source: architecture.md#Communication-Patterns]
- REST: `/api/batches` plural noun; errors `{code, message}` (snake_case + Spanish); success = direct payload; handlers never read `tenant_id` from bodies — repos require tenant context. [Source: architecture.md#Naming-Patterns, #Format-Patterns, #Process-Patterns]
- Every schema change = Alembic migration; DB naming: plural snake_case, `tenant_id` FKs, timestamptz UTC. [Source: architecture.md#Naming-Patterns]
- English identifiers in all new code; generated OpenAPI types in the frontend (WS payloads are the documented exception — no OpenAPI source exists). [Source: architecture.md#Enforcement-Guidelines]
- Frontend: REST via TanStack Query; live state via the single WS store with one reducer per event; components never raw-fetch; UI state machine driven ONLY by `batch.state`/snapshot (no optimistic jumps — UX-DR12/UX-DR5). [Source: architecture.md#Frontend-Architecture; epics.md#UX-DR12]

### UX requirements (DESIGN.md / EXPERIENCE.md / UX-DRs — read "prefijo" as "gate")

- **UX-DR3 ring:** HeroUI `CircularProgress` ~128px mobile; accent stroke sending; track `surface-tertiary`; center % `metric-lg` + fraction `data-mono`; flank EXACTLY enviadas · en cola / ETA / CC nuevas (success green). No other stats.
- **UX-DR9 selector (amended by owner addition):** HeroUI `Select`s over `GET /api/gates`; never free text; required. Two-step category → gate; gate options show name + value (value in mono, with its dot, verbatim); active chip shows `name · value`. Category names are sentences → Public Sans; values are data → mono.
- **UX-DR10 nav:** exactly Envío | Historial; bottom nav mobile with 6px live dot (success while sending); inline header desktop.
- **UX-DR13 WS contract:** auto-reconnect, snapshot-first render, silent reconcile, NO offline UX.
- **UX-DR14 ETA:** honest "~12 min" estimate recomputed per `batch.progress`; never a precise countdown.
- **UX-DR19 layout:** mobile single column (header → ring → controls-slot → data-panel → bottom nav), cockpit pinned; desktop ≥lg 3-col grid `300px 1fr 1fr` (the two right columns stay empty until 3.2 — keep the grid shell simple, don't fake panels).
- **UX-DR21 bans:** free-text gate, editable interval, filler stats, modal stacks >1, celebratory animations, hover-only affordances. None of legacy `static/index.html`'s visual patterns carry over.
- Microcopy (Spanish tuteo, exact): idle "Pega tus líneas y elige un gate." · Historial stub empty state "Todavía no tienes sesiones. Tu primer lote crea una." · network fallback "No pudimos conectar. Intenta de nuevo." Typography: mono ONLY for data (counters, ETA digits, fraction, gate chip); Public Sans for sentences. [Source: DESIGN.md#Typography; EXPERIENCE.md#State-Patterns]

### Previous story intelligence (2.1 + 1.x)

- **2.1 review lessons that apply directly here:** validate input for invisible chars where uniqueness matters (not relevant to lines — they're free text — but IS relevant if you validate anything); int-overflow ids (`gate_id` beyond int32 → asyncpg DBAPIError → 500; guard with `le=2**31-1` on the Pydantic field or catch, return 404 — 2.1 fixed this exact bug in admin.py:420); guard form re-submit while a mutation is pending (`onSubmit` + `isPending`, not just `isDisabled` on the button); on 404-type staleness invalidate the relevant query key; 422 bodies have no `{code,message}` — `lib/api.ts` already normalizes, keep client-side validation anyway.
- **2.1 established:** inline Pydantic schemas in the router (no schemas module); repo = module functions, flush-not-commit; AppError factories in `errors.py`; HeroUI v3 react-aria components (`Form`, `TextField`, `Select`, `Table.*`, `Alert`, `Button`); NO Modal component exists — inline confirm/edit is the idiom (not needed this story); TanStack array keys + `invalidateQueries`.
- **1.2/1.4/1.6 auth chain:** blocked → expired → must_change_password, in that order; expiry 403 is ONE-SHOT (revokes the session as it answers) — the WS handshake helper must use the same services but expect that a just-expired user simply fails the handshake.
- **1.7:** Conventional Commits with scope (`feat(backend,frontend): story 2.2 …`), branch `story/2.2-enviar-un-lote-un-cliente-con-progreso-en-vivo`; middleware fetches the backend over loopback (`BACKEND_INTERNAL_URL`, build-time inlined) — irrelevant to `/ws` once excluded from the matcher; production deploy is auto on push to main (GitHub Actions) — **merging this story to main deploys it**; the VPS `backend/.env` needs `TELEGRAM_TARGET` (the other keys are already there from 1.7's re-auth script) or production sending answers 503 `telegram_unauthorized` until it's set (no crash — permissive defaults). Also note 1.7's AC4 is still pending: if `anon.session` doesn't exist on the VPS yet, production sending stays 503 regardless. Coordinate with Richard.
- **Deferred-work check:** the Caddy `/ws` item resolves here (Task 13); the generated-types error-response gap and the admin hand-written-types epic-wide pass remain deferred — do not fix here.

### Testing standards

- `pytest` + `pytest-asyncio` (`loop_scope="session"`) + httpx `ASGITransport` against the real app and dev Postgres; self-seed via `conftest.seed_user`/`ctx`, self-clean in teardown. No DB mocking. One behavior per test; assert status + body shape.
- ASGITransport does NOT run lifespan → no Telethon, no worker in tests by default. The fake-gateway fixture + monkeypatched settings drive worker tests deterministically (`send_interval_seconds=0`).
- The architecture's "fake Telegram client fixture" (architecture.md#tests/conftest) starts HERE — put `FakeGateway` in `conftest.py` so 2.3/2.4/2.5 reuse it.
- Frontend: no test framework (deferred decision) — gates are `eslint` + `tsc` + `next build` only. Do not introduce vitest/jest.

### Project Structure Notes

- New files: `backend/app/core/__init__.py`, `backend/app/core/telegram.py`, `backend/app/core/send_worker.py`, `backend/app/core/broadcaster.py`, `backend/app/api/batches.py`, `backend/app/api/ws.py`, `backend/app/services/batches.py`, `backend/app/db/repos/batches.py`, `backend/app/db/repos/gate_categories.py`, `backend/migrations/versions/<rev>_gate_categories.py`, `backend/migrations/versions/<rev>_batches_and_batch_lines.py`, `backend/tests/test_gate_categories.py`, `backend/tests/test_batches.py`, `frontend/lib/ws.ts`, `frontend/components/batch/*.tsx`, `frontend/components/client-nav.tsx` (or `app/(client)/layout.tsx`), `frontend/app/(client)/sessions/page.tsx` (stub).
- Modified: `backend/app/config.py`, `backend/app/main.py`, `backend/app/db/models.py` (Batch, BatchLine, GateCategory, Gate.category_id), `backend/app/errors.py`, `backend/app/api/admin.py` (category CRUD + gate schema changes), `backend/app/api/gates.py` (GateOut gains category fields), `backend/tests/conftest.py` (FakeGateway), `backend/tests/test_admin_gates.py` (category_id in fixtures), `backend/.env.example`, `frontend/app/admin/gates/page.tsx` (Categorías block + column + Select), `frontend/app/(client)/page.tsx`, `frontend/middleware.ts`, `frontend/types/api.ts` (regenerated), `_bmad-output/implementation-artifacts/deferred-work.md`.
- Variance vs architecture tree: `core/scheduler.py`, `core/capture.py`, `core/attribution.py`, `core/cc_extract.py` are NOT created here (Stories 2.4/3.1); `api/sessions.py` is 3.3. `services/batches.py` is an addition to the tree (apply_gate + snapshot orchestration) — consistent with the services layer's purpose.
- Legacy `core.py`/`app.py`/`auto_sender.py` stay frozen at repo root. **Never read `respuestas/` contents.**

### References

- [Source: planning-artifacts/epics.md#Story-2.2 — ACs verbatim, gate rename applied per owner decision 2026-06-11]
- [Source: planning-artifacts/epics.md#Epic-2 + #Scheduler-&-send-pipeline — pipeline hardening split across 2.3/2.4/2.5 (scope fence)]
- [Source: planning-artifacts/architecture.md#Communication-Patterns, #Process-Patterns, #Project-Structure, #Gap-Analysis (adaptive formula = 2.4)]
- [Source: planning-artifacts/ux-designs/ux-cc-2026-06-10/DESIGN.md — ring/metrics/nav/chip tokens, layout, typography ramp]
- [Source: planning-artifacts/ux-designs/ux-cc-2026-06-10/EXPERIENCE.md — component behavioral rules, state patterns, Flow 1, microcopy]
- [Source: implementation-artifacts/2-1-catalogo-global-de-prefijos.md — gate rename, soft-delete design, name-backfill migration pattern, review-findings patterns; its "selector shows name only" decision is SUPERSEDED by this story's owner addition]
- [Source: owner (Richard), 2026-06-11 — gate categories + client sees name/category/value + two-step selection; not in epics.md]
- [Source: implementation-artifacts/deferred-work.md — Caddy /ws item resolved here]
- [Source: backend/scripts/telegram_auth.py — session path convention, "Story 2.2 promotes them" contract]
- [Source: core.py / app.py — legacy port map (agregar_prefijo, worker, broadcaster, snapshot, /ws)]
- [Source: _bmad-output/project-context.md — 🔒 rules: never read respuestas/, never print .env values, account-safety via interval/FloodWait]

## Dev Agent Record

### Agent Model Used

Claude Fable 5 (claude-fable-5) — BMad dev agent, 2026-06-11.

### Debug Log References

- Backend gates: `pytest` 107 passed (73 pre-existing + 34 new) · `ruff check app/ tests/` clean · `mypy app` clean (29 files).
- Frontend gates: `eslint .` clean · `tsc --noEmit` clean · `next build` green — `/` and `/sessions` both in the route output.
- Migrations applied to dev Postgres: `62f6cc07f7b0 → a3d41c9be7f0 (gate categories) → b8e52d0cf1a4 (batches and batch_lines)`; a post-apply `alembic revision --autogenerate` produced an empty diff (no model/DB drift).
- WS sanity (Starlette TestClient, lifespan ON): no cookie → accept then close `4401`; gateway logged "telegram credentials missing — sending stays down (503)" and the app booted anyway (the boot-without-Telegram AC).

### Completion Notes List

- **Category delete vs RESTRICT FK (design decision, deviation worth review attention):** the spec wants both `ondelete="RESTRICT"` AND "delete after retiring its gates → 204". Retired gate rows still reference the category, so RESTRICT alone would make such a category permanently undeletable (retired gates are invisible — the owner can't reassign them). Resolution: on category delete, retired gates referencing it are re-pointed at the oldest OTHER category (`gate_categories.reassign_retired_gates`, deterministic, rows KEPT per 2.1's never-hard-delete design). If no other category exists to take them, the delete answers 409 `category_in_use`. Active gates always 409 first (app check `has_gates`, active-only as specified).
- **`POST /api/batches` 503 gate checks `gateway.authorized` AND `gateway.target_ok`:** an unresolvable/missing `TELEGRAM_TARGET` would otherwise accept batches whose every line errors in the worker forever. Same `telegram_unauthorized` contract. `target_ok` defaults True at construction so the test suite drives the route by flipping only `authorized` (lifespan's `connect()` sets it for real).
- **Telethon boundary kept:** `FloodWaitError` is re-exported from `app/core/telegram.py` so `send_worker.py` catches it without importing telethon — `core/telegram.py` stays the only telethon importer in `app/`.
- **Worker shape:** `step()` (one line: claim → send-with-in-place-retries → record+emit) is factored out of `run_worker()` precisely so tests await single steps; no DB session is held across FloodWait/error sleeps (a minutes-long FloodWait must not pin a pool connection). Cancelable-sleep primitive + module `wake()` shipped for 2.3. Retry-forever + boot `requeue_stuck_sending` carry `# Story 2.5` comments as required.
- **`batch.state` WS values are surface states (`"sending"`/`"idle"`)** while the DB rows use `sending/completed` — per Task 7 ("if batch drained → state='completed', emit `batch.state {state:"idle"}`").
- **`types/api.ts` regenerated offline:** `app.openapi()` dumped to JSON → `npx openapi-typescript` (identical output to `npm run generate:api`, no running server needed). WS payloads hand-typed in `lib/ws.ts` next to the reducer (documented exception).
- **Owner on Envío (Task 13) verified by inspection:** middleware only gates `/admin/*` for clients and `/admin/gates` for non-owners; owner navigating `/` falls through. No code change. Caddy `/ws` exact matcher confirmed correct as-is; deferred-work item marked resolved.
- **NOT done (human/owner actions):** (1) real-credentials manual smoke — needs Richard's `backend/.env` values + `TELEGRAM_TARGET` and his go-ahead (it sends real Telegram messages); (2) **deploy checklist:** the VPS `backend/.env` needs `TELEGRAM_TARGET=` (and optionally `SEND_INTERVAL_SECONDS=`) added, and 1.7's AC4 (`anon.session` created on the VPS via `scripts/telegram_auth.py`) is still pending — until both, production sending answers 503 `telegram_unauthorized` (no crash). Merging this story to main auto-deploys.
- Scope fence respected: no pause/stop/state-pill/FloodWait UI (2.3), no scheduler/adaptive interval (2.4 — only `is_owner_priority` written), no send_log/retry-cap (2.5), no capture/CC ("CC NUEVAS" renders the hardcoded 0; data-panel columns empty).
- Pre-existing, untouched: Next 16 deprecation warning about the `middleware` file convention (rename to `proxy` is a separate chore).

### File List

**Backend — new:** `app/core/__init__.py`, `app/core/telegram.py`, `app/core/broadcaster.py`, `app/core/send_worker.py`, `app/api/batches.py`, `app/api/ws.py`, `app/services/batches.py`, `app/db/repos/batches.py`, `app/db/repos/gate_categories.py`, `migrations/versions/a3d41c9be7f0_gate_categories.py`, `migrations/versions/b8e52d0cf1a4_batches_and_batch_lines.py`, `tests/test_gate_categories.py`, `tests/test_batches.py`.

**Backend — modified:** `app/config.py` (telegram + interval settings), `app/main.py` (lifespan: gateway + worker; batches/ws routers; docstring), `app/db/models.py` (GateCategory, Gate.category_id+relationship, Batch, BatchLine), `app/errors.py` (category_exists/category_not_found/category_in_use/empty_batch/telegram_unauthorized), `app/api/admin.py` (category CRUD; gate schemas gain category_id; GateOut gains category fields), `app/db/repos/gates.py` (selectinload category; create takes category_id), `tests/conftest.py` (FakeGateway), `tests/test_admin_gates.py` (category fixture + category_id everywhere), `.env.example` (Telegram block promoted + TELEGRAM_TARGET + SEND_INTERVAL_SECONDS).

**Frontend — new:** `lib/ws.ts`, `components/batch/send-form.tsx`, `components/batch/progress-ring.tsx`, `components/batch/metric.tsx`, `components/client-nav.tsx`, `app/(client)/layout.tsx`, `app/(client)/sessions/page.tsx`.

**Frontend — modified:** `app/(client)/page.tsx` (stub → full Envío surface), `app/admin/gates/page.tsx` (Categorías block, Categoría column, category Select in create/edit), `middleware.ts` (ws exclusion in matcher), `types/api.ts` (regenerated).

**Other:** `_bmad-output/implementation-artifacts/deferred-work.md` (Caddy /ws item resolved), `deploy/Caddyfile` (no change needed — verified).
