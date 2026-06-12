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
import grp
import os
import pwd
import sys
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from telethon import TelegramClient

# Same backend/.env the app reads (app Settings has extra="ignore", so these
# keys don't break it). Resolved relative to this file, CWD-independent.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# The systemd service user that will own the session file from Story 2.2 on.
_SERVICE_USER = "cc"


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
    """chmod 600 the session file and verify; ensure service-user ownership."""
    try:
        os.chmod(session_path, 0o600)
    except PermissionError:
        sys.exit(
            f"cannot chmod {session_path}: not the file owner. Fix as root:\n"
            f"  chmod 600 {session_path} && "
            f"chown {_SERVICE_USER}:{_SERVICE_USER} {session_path}"
        )
    mode = session_path.stat().st_mode & 0o777
    if mode != 0o600:
        sys.exit(f"FAILED to set mode 600 on {session_path} (got {mode:o})")
    print(f"session file: {session_path} (mode 600)")
    _ensure_owner(session_path)


def _ensure_owner(session_path: Path) -> None:
    """The service user must own the file — a root-owned 600 file inside the
    700 ``cc:cc`` dir is unreadable by cc-core. Auto-chown when run as root."""
    try:
        cc_uid = pwd.getpwnam(_SERVICE_USER).pw_uid
        cc_gid = grp.getgrnam(_SERVICE_USER).gr_gid
    except KeyError:
        print(
            f"NOTE: user '{_SERVICE_USER}' does not exist on this machine — "
            f"on the VPS the file must be {_SERVICE_USER}:{_SERVICE_USER}."
        )
        return
    if session_path.stat().st_uid == cc_uid:
        return
    if os.geteuid() == 0:
        os.chown(session_path, cc_uid, cc_gid)
        print(f"chowned to {_SERVICE_USER}:{_SERVICE_USER}")
    else:
        print(
            f"WARNING: {session_path} is NOT owned by {_SERVICE_USER} — "
            f"run as root:  chown {_SERVICE_USER}:{_SERVICE_USER} {session_path}"
        )


async def authenticate(settings: TelegramAuthSettings, session_path: Path) -> None:
    client = TelegramClient(
        str(session_path), settings.telegram_api_id, settings.telegram_api_hash
    )

    # Idempotency: an existing authorized session is left untouched.
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"already authorized as {me.first_name} (id={me.id}) — nothing to do")
            return

        # start() drives phone → code → optional 2FA password interactively.
        await client.start(phone=lambda: input("phone (international format): "))
        me = await client.get_me()
        print(f"authorized as {me.first_name} (id={me.id})")
    finally:
        await client.disconnect()


def main() -> None:
    try:
        settings = TelegramAuthSettings()
    except ValidationError as exc:
        missing = ", ".join(
            str(err["loc"][0]).upper() for err in exc.errors() if err.get("loc")
        )
        sys.exit(
            f"missing/invalid settings: {missing or 'TELEGRAM_*'}.\n"
            "Set TELEGRAM_API_ID and TELEGRAM_API_HASH in backend/.env "
            "(get them at https://my.telegram.org/apps)."
        )

    session_path = Path(settings.telegram_session_path)
    # Restrictive perms from birth: Telethon creates the SQLite file at
    # connect() and writes the auth key BEFORE _harden() runs — without this
    # umask an interrupted auth (Ctrl+C at the code/2FA prompt) would leave
    # an authorized session world-readable.
    os.umask(0o077)
    session_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    try:
        asyncio.run(authenticate(settings, session_path))
    finally:
        # Harden even on interrupted/failed auth — the file may already
        # contain a usable auth key.
        if session_path.exists():
            _harden(session_path)


if __name__ == "__main__":
    main()
