"""Interactive Telegram re-auth on the VPS (Story 1.7, AC4).

Authenticates the user account ON the VPS and writes the Telethon session
file (default ``/var/lib/cc/anon.session``) with mode 600. The session is
ALWAYS created on the VPS, never copied from another machine — a session
created elsewhere risks being invalidated when first used from a
datacenter IP (architecture risk deep-dive). NOT an API route — run-once
operational CLI, like ``scripts.bootstrap_owner``.

Reads ``TELEGRAM_API_ID`` / ``TELEGRAM_API_HASH`` (and optionally
``TELEGRAM_SESSION_PATH``) from ``backend/.env``. These keys stay OUT of
``app.config.Settings`` until Story 2.2 promotes them — the app process has
no Telethon yet. Get API credentials at https://my.telegram.org/apps.

Usage (from backend/, venv active, ON the VPS):
    python -m scripts.telegram_auth

Flow: phone → login code → optional 2FA password (Telethon's ``start()``
drives it). Re-running with an already-authorized session exits 0 without
prompting. If Telegram auth ever dies in production (``AuthKeyError``),
re-run this script on the VPS (full runbook: Story 4.4).
"""

import asyncio
import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from telethon import TelegramClient

# Same backend/.env the app reads (app Settings has extra="ignore", so these
# keys don't break it). Resolved relative to this file, CWD-independent.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class TelegramAuthSettings(BaseSettings):
    """Telethon credentials, read only by this script (never by the app)."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_api_id: int
    telegram_api_hash: str
    # Outside the repo (git pull never touches it) and outside anything Caddy
    # serves — AC4's "outside the web root". Dir cc:cc mode 700 (runbook).
    telegram_session_path: str = "/var/lib/cc/anon.session"


def _harden(session_path: Path) -> None:
    """chmod 600 the session file and verify; remind about ownership."""
    os.chmod(session_path, 0o600)
    mode = session_path.stat().st_mode & 0o777
    if mode != 0o600:
        sys.exit(f"FAILED to set mode 600 on {session_path} (got {mode:o})")
    print(f"session file: {session_path} (mode 600)")
    print(
        "REMINDER: the file must be owned by the service user — if this ran "
        "as root or another user, run:  chown cc:cc " + str(session_path)
    )


async def authenticate(settings: TelegramAuthSettings) -> None:
    session_path = Path(settings.telegram_session_path)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(
        str(session_path), settings.telegram_api_id, settings.telegram_api_hash
    )

    # Idempotency: an existing authorized session is left untouched.
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"already authorized as {me.first_name} (id={me.id}) — nothing to do")
        await client.disconnect()
        _harden(session_path)
        return

    # start() drives phone → code → optional 2FA password interactively.
    await client.start(phone=lambda: input("phone (international format): "))
    me = await client.get_me()
    print(f"authorized as {me.first_name} (id={me.id})")
    await client.disconnect()

    _harden(session_path)


def main() -> None:
    asyncio.run(authenticate(TelegramAuthSettings()))


if __name__ == "__main__":
    main()
