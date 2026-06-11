---
project_name: 'cc'
user_name: 'Richard'
date: '2026-06-10'
sections_completed:
  ['technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'quality_rules', 'workflow_rules', 'anti_patterns']
status: 'complete'
rule_count: 35
optimized_for_llm: true
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

- **Python 3.12** — match this interpreter; uses 3.10+ syntax freely.
- **Telethon** (MTProto) — user-account client, NOT a bot. Auth via phone, session in `anon.session` (SQLite). `pip install -r requirements.txt`.
- **FastAPI + uvicorn[standard]** — web UI (`app.py`), single `TelegramClient` in uvicorn's asyncio loop (connected in lifespan), WebSocket at `/ws`.
- **pyperclip** — legacy clipboard CLI (`auto_sender.py`).
- **python-dotenv** — all config loaded from `.env` in `core.py`.
- **Frontend**: vanilla HTML/CSS/JS SPA, `static/index.html`, NO build step, NO framework.

**Version constraints:** `requirements.txt` is UNPINNED. Do not introduce pins or new deps without explicit ask. No linter, no build, no committed tests.

## Critical Implementation Rules

### Language-Specific Rules (Python / asyncio)

- **Naming is Spanish.** Functions, vars, dirs use Spanish (`agregar_prefijo`, `enviar`, `enviados_total`, `respuestas/`, `prefijo`, `destinos`). Match this — do NOT translate to English.
- **`core.py` stays pure**: no terminal I/O, no Telethon imports. Shared logic only. Both front-ends import it. Keep it that way.
- **State is per-instance, not module-global.** `Sesion` holds its own dedup sets so multiple prefixes coexist without restart. Don't hoist state to module globals.
- **asyncio (web)**: send worker is a single background `asyncio.Task` draining `Engine.cola`. Sleeps must stay cancelable (pause/stop interrupt instantly). Pause/resume via `asyncio.Event`.
- **Atomic file writes**: `escribir_meta` uses tmp + `os.replace`, preserves `creada`. Preserve this pattern for any meta mutation.
- **Lazy dir creation**: `Sesion` dir is created on first `guardar_respuesta`, not at construction (web callers mkdir earlier via `meta.json`). `_ultima` symlink updated on first save only.

### Framework-Specific Rules (FastAPI + WebSocket + Telethon)

- **WebSocket is server→client ONLY.** All commands go through REST. WS emits events: `snapshot`, `cola`, `linea_enviada`, `progreso`, `respuesta`, `contadores`, `estado_envio`, `flood`, `error`, `estado_auth`, `sesion_activa`. New tab gets a full `snapshot`. Never accept commands over `/ws`.
- **State lives in `Engine`, outside any WebSocket**, so it survives reconnects. `Broadcaster` fans events to every open tab. Don't tie send/session state to a socket's lifetime.
- **Response handlers registered once, NO chat filter** (web). They filter on the live `destinos_ids` set, so changing destinos needs no re-registration. CLI handlers ARE chat-filtered (`chats=destinos`) — different on purpose.
- **`FloodWaitError`**: sleep the requested duration (cancelable — stop exits without waiting), then retry the SAME line. Other send errors: emit `error`, retry same line after 2s, forever. A permanently failing line blocks the queue until stop.
- **Counters never reset** between batches — they accumulate for process lifetime.
- **`/api/enviar` while a batch is live only APPENDS** lines not already queued; ignores request's destinos/intervalo, keeps active `Sesion`. Starting fresh resolves destinos/interval from the request.
- **Session lifecycle**: `nueva`/`continuar` return HTTP 409 while a batch is live or paused (`_lote_vivo`); `renombrar` is unguarded. Both clients use `catch_up=True`.
- **History paths guarded against traversal** via `_safe_dir`. Keep this on any new history endpoint.

### Testing & Code Quality Rules

- **No test framework, no linter, no formatter.** Don't invent a `jest`/`pytest`/`eslint` setup or assume one exists. If you add tests, ask first and keep them as standalone scripts (none are committed).
- **No build step.** Frontend is served as-is from `static/index.html`. Don't add a bundler/transpiler.
- **Match surrounding style** — 4-space indent, Spanish names, f-strings, type hints where already present. No enforced line length.
- **Keep `core.py` import-safe** (no side effects on import beyond dotenv config load).

### Development Workflow Rules

- **Run**: `python app.py` (web UI → http://127.0.0.1:8000) or `python auto_sender.py <prefijo>` (CLI; prefix is the only arg, required positional). No flags.
- **Commits**: Conventional Commits with scope (e.g. `feat(web): …`, `fix(web): …`) per git history. Branch `main` is primary.
- **Config**: exactly 5 env vars in `.env` — `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`, `TELEGRAM_DESTINO`, `TELEGRAM_INTERVALO`. No host/port/prefix env vars exist. Don't add new env vars silently.

### Critical Don't-Miss Rules (Security & Gotchas)

- **🔒 `.env` holds REAL credentials.** It's gitignored. NEVER commit it, print its values, or hardcode them elsewhere.
- **🔒 User-account client, not a bot.** Phone number, not bot token. Aggressive sending risks Telegram account bans → respect `TELEGRAM_INTERVALO` and FloodWait. Don't remove rate-limiting.
- **🔒 NEVER read contents of `respuestas/`.** Hard rule — it holds sensitive captured data. Operate on its structure/paths only, never open the files.
- **Don't delete `anon.session`** unless re-auth is the explicit goal (it's the auth state, shared by both front-ends).
- **`extraer_cc` / `RE_CC`**: each captured value is truncated at the literal substring `Status`. Don't "fix" this — it's intentional parsing.
- **CC dedup is session-scoped.** `filtrada.txt` dedups within a session; `continuar=True` preloads the set via `cargar_cc_existentes()`. Preserve dedup when touching save logic.
- **Legacy `meta.json`-less sessions**: history UI falls back to slug/`nombre_bonito`; restored prefix is dotless → UI warns "Verificá el prefijo". Handle missing `meta.json` gracefully.

---

## Usage Guidelines

**For AI Agents:**

- Read this file before implementing any code.
- Follow ALL rules exactly as documented.
- When in doubt, prefer the more restrictive option.
- The 🔒 rules are non-negotiable (credentials, account safety, `respuestas/` privacy).

**For Humans:**

- Keep this file lean and focused on agent needs.
- Update when the technology stack or save-session layout changes.
- Remove rules that become obvious over time.

Last Updated: 2026-06-10
