# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A Telegram message forwarder that sends lines through a user account (not a bot token) to one or more target Telegram chats using the Telethon MTProto library, prepending a prefix to each line. Bot responses containing ✅ are saved to disk, with `CC:` data extracted into a filtered file.

Two front-ends share the same core logic and `anon.session`:
- **`app.py`** — web UI (FastAPI + WebSocket). The recommended interface: paste text, watch the queue drain line by line, pause/resume/stop, live response panel, history browser.
- **`auto_sender.py`** — legacy CLI that polls the system clipboard.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Web UI (recommended) — opens http://127.0.0.1:8000 in the browser
python app.py

# Legacy CLI by clipboard (prefix is a REQUIRED positional argument)
python auto_sender.py .zo
```

There are no linters or build steps. Tests are ad-hoc scripts (none committed).

## Architecture

### `core.py` — shared logic (no terminal I/O, no Telethon)
Imported by both front-ends. Holds env-loaded config **defaults** (`API_ID`, `API_HASH`, `PHONE`, `DESTINOS_DEFAULT`, `INTERVALO_DEFAULT`), `agregar_prefijo(texto, prefijo)` (prefix + in-batch dedup), `extraer_cc`/`RE_CC` (each captured value is truncated at the literal substring `Status`), `prefijo_slug`, `nombre_bonito`, the `meta.json` helpers (`leer_meta`, `escribir_meta` — atomic tmp+`os.replace`, preserves `creada` —, `escribir_nombre`), progress helpers (`formatear_progreso` for the CLI, `calcular_eta` for the web), `esperar_intervalo`, and the **`Sesion`** class.

`Sesion(prefijo, base_dir=RESPUESTAS_DIR, sello=None, continuar=False)` encapsulates one save session: its `respuestas/<prefix-slug>/<timestamp>/` dir (created **lazily** on the first `guardar_respuesta`, not at construction — though web callers `mkdir` it earlier by writing `meta.json`), the session-wide `CC:` dedup set, `guardar_respuesta(texto)` (appends to `completa.txt` + appends only-new `CC:` lines to `filtrada.txt`, returns the list of new data), `cargar_cc_existentes()` (preloads the dedup set from an existing `filtrada.txt`; called automatically when `continuar=True`), `info()` (`{id, nombre, prefijo, slug}`, reading `meta.json`), and the relative `_ultima` symlink. `meta.json` (friendly `nombre`, original `prefijo` with its dot, `creada`) is written by callers via `escribir_meta`, not by `Sesion` itself. State is per-instance (not module globals) so multiple prefixes can coexist without restarting. There is no close/finalize — sessions are replaced by reassignment.

### `app.py` — web UI (FastAPI + WebSocket)
A single `TelegramClient` lives in uvicorn's asyncio loop (connected in the FastAPI lifespan). The `Engine` class holds all send state outside any WebSocket so it survives reconnects and broadcasts to every open tab via `Broadcaster`.
- **Send worker**: a background `asyncio.Task` drains `Engine.cola`, respecting the interval (cancelable sleep so pause/stop interrupt instantly), round-robin across resolved destino entities (keyed on `enviados_total`). `FloodWaitError` sleeps the requested duration (also cancelable — stop exits without waiting; pause→resume can retry before the window elapses) and retries the same line; any other send error emits an `error` event and retries the same line after 2s, forever — a permanently failing line blocks the queue until stop. Pause/resume via an `asyncio.Event`; stop clears the queue. Counters accumulate for the process lifetime — they are never reset between batches.
- **`/api/enviar` semantics**: starting a new batch resolves destinos/interval from the request; calling it while a batch is live only **appends** lines not currently in the queue (already-sent lines can be re-queued; returns `{agregadas, anexado: true}`), applies the request's prefijo to the new lines but ignores its destinos/intervalo and keeps the active `Sesion`.
- **Save-session lifecycle**: `POST /api/sesion/nueva` (optional friendly name), `/api/sesion/continuar` (takes the folder **slug** as `prefijo`; rebuilds `Sesion(..., sello=..., continuar=True)` so the CC dedup set is preloaded from the old `filtrada.txt`; for legacy sessions without `meta.json` the restored prefix falls back to the dotless slug — the UI warns "Verificá el prefijo"), `/api/sesion/renombrar` (writes `nombre`, max 200 chars, into `meta.json`). Nueva/continuar return HTTP 409 while a batch is live or paused (`_lote_vivo`); renombrar is not guarded. `/api/enviar` reuses the active `Sesion` when its slug matches the submitted prefix (a session set via the buttons wins), otherwise auto-creates one; either way it persists the original prefix into `meta.json` (which also `mkdir`s the session dir immediately on the web path).
- **Response capture**: `NewMessage` + `MessageEdited` handlers are registered once with **no chat filter**; they filter on the live `destinos_ids` set (so changing destinos needs no re-registration). The `_manejar_bot` logic mirrors the CLI's (per-`message_id` state, ❌→✅ counter moves, edit dedup) but emits WebSocket events and saves to the active prefix's `Sesion`. Capture stays armed between batches (`destinos_ids` persists), so late bot replies keep being counted/saved; both clients pass `catch_up=True`, recovering messages that arrived while disconnected.
- **WebSocket `/ws`**: server→client events only — all commands go through REST (`snapshot`, `cola`, `linea_enviada`, `progreso`, `respuesta`, `contadores`, `estado_envio`, `flood`, `error`, `estado_auth`, `sesion_activa`). A newly connected tab gets a full `snapshot` (which includes `sesion_activa`).
- **REST**: `/api/config`, `/api/enviar`, `/api/pausar|reanudar|detener`, `/api/login/send_code|sign_in`, `/api/sesion/nueva|continuar|renombrar`, and history (`/api/prefijos`, `/api/sesiones/{prefijo}` — returns `{id, nombre}` objects, newest first —, `/api/respuesta/{prefijo}/{sesion}?tipo=completa|filtrada`). History paths are guarded against traversal via `_safe_dir`.
- **Frontend** (`static/index.html`): vanilla HTML/CSS/JS SPA, no build step. Auto-reconnecting WebSocket. Live responses are split into side-by-side **Completa**/**Filtrada** columns. The history browser **live-follows** the active session by default (debounced refresh on each `respuesta` event, auto-scroll only if the pane was already at the bottom); manually browsing elsewhere detaches it and a "↻ Ver sesión actual" button re-attaches.

### `auto_sender.py` — legacy CLI
Polls `pyperclip.paste()` every 0.5s (identical clipboard content is not resent), applies `agregar_prefijo`, sends rate-limited with round-robin, captures responses into a `Sesion` created unconditionally at startup (saving is always on). Same response logic as the web Engine, but its Telethon handlers ARE chat-filtered (`chats=destinos`). The prefix is the required positional CLI arg — the only argument; there are no flags, no dry-run, and no pause mechanism (stop with Ctrl+C). Exits with code 1 if credentials, phone, or `TELEGRAM_DESTINO` are missing or a destino can't be resolved.

### Configuration
All settings live in `.env` (loaded via `python-dotenv` in `core.py`). These five are the **only** env vars — there is no prefix env var, no host/port override, and none of the old anti-spam/logging tuning vars. In the web UI destinos and interval are only **defaults**, editable per request; in the CLI they come from `.env` and the prefix is the required positional argument. Env vars:
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE` — Telethon credentials (get them at https://my.telegram.org/apps)
- `TELEGRAM_DESTINO` — target username(s), comma-separated for round-robin (`@` stripped); required by the CLI
- `TELEGRAM_INTERVALO` — constant interval between sends (seconds, default 8.0)

### Saved responses layout
`respuestas/<prefix-slug>/<YYYY-MM-DD_HH-MM-SS>/` with `completa.txt` (every saved response revision, timestamped — edits of an already-✅ message are re-appended), `filtrada.txt` (session-deduped `CC:` data, one per line), and `meta.json` (`nombre`, original `prefijo`, `creada` — written only by web-UI code paths; CLI sessions have no `meta.json` and the history UI falls back to `nombre_bonito`/slug); plus a relative `_ultima` symlink in `respuestas/<slug>/` pointing at the latest session that actually saved a response (it is updated on first `guardar_respuesta`, not at session creation). The timestamp folder name is the stable session id; the friendly name lives only in `meta.json`.

### Session persistence
Telethon persists authentication state in `anon.session` (SQLite), shared by both front-ends. If it's already authorized the web UI connects directly; otherwise it shows a login form (phone → code → optional 2FA). Delete this file to force re-authentication.

## Important notes

- This is a **user account** client, not a bot. It uses a phone number, not a bot token. The target must be a chat the user account can message.
- The `.env` file is in `.gitignore` but **contains real credentials** — never commit it or hardcode its values elsewhere.
