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

### `core.py` — shared logic (no terminal I/O)
Imported by both front-ends. Holds env-loaded config **defaults** (`API_ID`, `API_HASH`, `PHONE`, `DESTINOS_DEFAULT`, `INTERVALO_DEFAULT`), `agregar_prefijo(texto, prefijo)`, `extraer_cc`, `RE_CC`, `esperar_intervalo`, and the **`Sesion`** class.

`Sesion(prefijo)` encapsulates one save session: its `respuestas/<prefix-slug>/<timestamp>/` dir, the session-wide `CC:` dedup set, `guardar_respuesta(texto)` (writes `completa.txt` + appends new `CC:` lines to `filtrada.txt`, returns the list of new data), and the relative `_ultima` symlink. State is per-instance (not module globals) so multiple prefixes can coexist without restarting.

### `app.py` — web UI (FastAPI + WebSocket)
A single `TelegramClient` lives in uvicorn's asyncio loop (connected in the FastAPI lifespan). The `Engine` class holds all send state outside any WebSocket so it survives reconnects and broadcasts to every open tab via `Broadcaster`.
- **Send worker**: a background `asyncio.Task` drains `Engine.cola`, respecting the interval (cancelable sleep so pause/stop interrupt instantly), round-robin across resolved destino entities. `FloodWaitError` sleeps the exact requested duration and retries the same line. Pause/resume via an `asyncio.Event`; stop clears the queue.
- **Response capture**: `NewMessage` + `MessageEdited` handlers are registered once with **no chat filter**; they filter on the live `destinos_ids` set (so changing destinos needs no re-registration). The `_manejar_bot` logic mirrors the CLI's (per-`message_id` state, ❌→✅ counter moves, edit dedup) but emits WebSocket events and saves to the active prefix's `Sesion`.
- **WebSocket `/ws`**: server→client events (`snapshot`, `cola`, `linea_enviada`, `progreso`, `respuesta`, `contadores`, `estado_envio`, `flood`, `error`, `estado_auth`). A newly connected tab gets a full `snapshot`.
- **REST**: `/api/config`, `/api/enviar`, `/api/pausar|reanudar|detener`, `/api/login/send_code|sign_in`, and history (`/api/prefijos`, `/api/sesiones/{prefijo}`, `/api/respuesta/{prefijo}/{sesion}?tipo=completa|filtrada`). History paths are guarded against traversal via `_safe_dir`.
- **Frontend** (`static/index.html`): vanilla HTML/CSS/JS SPA, no build step. Auto-reconnecting WebSocket.

### `auto_sender.py` — legacy CLI
Polls `pyperclip.paste()` every 0.5s, applies `agregar_prefijo`, sends rate-limited with round-robin, captures responses into a `Sesion`. Same response logic as the web Engine. The prefix is the required positional CLI arg.

### Configuration
All settings live in `.env` (loaded via `python-dotenv` in `core.py`). In the web UI these are only **defaults** — prefix, destinos and interval are editable in the interface. In the CLI the prefix is the required positional argument. Env vars:
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE` — Telethon credentials (get them at https://my.telegram.org/apps)
- `TELEGRAM_DESTINO` — target username(s), comma-separated for round-robin
- `TELEGRAM_INTERVALO` — constant interval between sends (seconds)

### Session persistence
Telethon persists authentication state in `anon.session` (SQLite), shared by both front-ends. If it's already authorized the web UI connects directly; otherwise it shows a login form (phone → code → optional 2FA). Delete this file to force re-authentication.

## Important notes

- This is a **user account** client, not a bot. It uses a phone number, not a bot token. The target must be a chat the user account can message.
- The `.env` file is in `.gitignore` but **contains real credentials** — never commit it or hardcode its values elsewhere.
