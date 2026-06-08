# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A Telegram clipboard-to-bot message forwarder. It monitors the system clipboard, prepends a configurable prefix to each line, and sends messages through a user account (not a bot token) to a target Telegram chat using the Telethon MTProto library.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the sender (uses .env for config)
python auto_sender.py

# Run with a different prefix override
python auto_sender.py --prefijo ".otro"
```

There are no tests, linters, or build steps.

## Architecture

**Single-file async app** (`auto_sender.py`, ~350 lines). Everything runs in one `asyncio` event loop.

### Core loop
1. **Clipboard polling** (line 225): polls `pyperclip.paste()` every 0.5s. When new text is detected, enters sending mode (guarded by `threading.Lock`).
2. **Prefix injection** (`agregar_prefijo`, line 169): prepends the prefix (default `.zo`) to every non-empty line that doesn't already have it. Deduplicates lines.
3. **Rate-limited sending** (lines 241–330): iterates through lines with a constant interval between sends (configurable via `TELEGRAM_INTERVALO`), plus adaptive extras when anti-spam is detected.
4. **Incoming message handler** (line 212): listens for responses from the target chat. If the response contains anti-spam keywords, activates a cooldown.

### Adaptive anti-spam system (lines 135–157, 275–308)
- When an incoming message matches anti-spam keywords ("antispam", "flood", "repeated requests", etc.), the global `antispam_hasta` timestamp is extended by `ANTISPAM_COOLDOWN` seconds.
- On each send attempt, `esperar_si_antispam()` checks whether to pause before sending.
- Additionally, an `extra_adaptativo` accumulator increases send intervals by `ADAPTIVE_INCREMENTO` (up to `ADAPTIVE_EXTRA_MAX`) on each anti-spam detection or `FloodWaitError`, then slowly decays by `ADAPTIVE_RECUPERACION` after every `ADAPTIVE_RECUPERACION_CADA` clean sends.
- `FloodWaitError` (Telegram server-side rate limit) is caught separately and sleeps for the exact duration Telegram requests.

### Logging (lines 97–132)
All send attempts are logged to a CSV file (`telegram_antispam_log.csv` by default) with columns: timestamp, batch_id, message position, attempt number, result (`ok`/`antispam`/`flood_wait`), timing metrics, and adaptive state.

### Configuration
All settings live in `.env` (loaded via `python-dotenv`). Key env vars:
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE` — Telethon credentials (get them at https://my.telegram.org/apps)
- `TELEGRAM_DESTINO` — target Telegram username
- `TELEGRAM_PREFIJO` — prefix prepended to each message line
- `TELEGRAM_INTERVALO` — constant interval between sends (seconds, default 8.0)
- `TELEGRAM_ANTISPAM_COOLDOWN` — pause duration when anti-spam detected
- `TELEGRAM_ADAPTIVE_*` — adaptive timing parameters (see above)

### Session persistence
Telethon persists authentication state in `anon.session` (SQLite). Delete this file to force re-authentication.

## Important notes

- This is a **user account** client, not a bot. It uses `client.start(phone=...)` with a phone number, not a bot token. The target must be a chat that the user account can message.
- The script is interactive on first run (Telethon may prompt for a verification code in the terminal).
- The `.env` file is in `.gitignore` but **contains real credentials** — never commit it or hardcode its values elsewhere.
