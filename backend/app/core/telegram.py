"""Telethon gateway — the ONLY module importing telethon anywhere in ``app/``.

Architecture boundary (enforced by review): one process (cc-core/uvicorn) owns
``anon.session``, and inside it only this module talks MTProto. Everything
else goes through the ``gateway`` singleton.

The app must BOOT without Telegram: missing/zero credentials, a missing or
unauthorized session file, or an unresolvable target leave ``authorized``/
``target_ok`` False — login/admin keep working, the worker idles and
``POST /api/batches`` answers 503 ``telegram_unauthorized``. Re-auth is
operational (run ``scripts/telegram_auth.py`` on the VPS); ``AuthKeyError``
detection/watchdog is Story 4.1 — deliberately NOT built here.
"""

import logging

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from app.config import settings

# Re-exported so the worker can catch FloodWaitError without importing
# telethon itself (the boundary: telethon imports live ONLY in this module).
__all__ = ["FloodWaitError", "TelegramGateway", "gateway"]

logger = logging.getLogger(__name__)


class TelegramGateway:
    """Single owner of the Telethon client + the resolved send target."""

    def __init__(self) -> None:
        self.client: TelegramClient | None = None
        self.authorized: bool = False
        # True by default so tests can drive sending by flipping only
        # ``authorized``; ``connect()`` sets it for real.
        self.target_ok: bool = True
        self._entity: object | None = None

    @property
    def ready(self) -> bool:
        """True iff the gateway can actually deliver messages."""
        return self.authorized and self.target_ok and self._entity is not None

    async def connect(self) -> None:
        """Connect + resolve the target. NEVER raises — failures log and leave
        the gateway unauthorized (the app boots regardless)."""
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            logger.warning(
                "telegram credentials missing — sending stays down (503)"
            )
            self.authorized = False
            return
        try:
            self.client = TelegramClient(
                settings.telegram_session_path,
                settings.telegram_api_id,
                settings.telegram_api_hash,
                catch_up=True,
            )
            await self.client.connect()
            self.authorized = await self.client.is_user_authorized()
        except Exception:
            logger.exception("telegram connect failed — sending stays down")
            self.authorized = False
            return
        if not self.authorized:
            logger.warning(
                "anon.session missing/unauthorized — run scripts/telegram_auth.py"
            )
            return
        await self._resolve_target()

    async def _resolve_target(self) -> None:
        """Resolve ``telegram_target`` once; failure marks ``target_ok=False``."""
        target = settings.telegram_target.strip().lstrip("@")
        if not target or self.client is None:
            logger.warning("TELEGRAM_TARGET not set — sending stays down")
            self.target_ok = False
            return
        try:
            self._entity = await self.client.get_input_entity(target)
            self.target_ok = True
        except Exception:
            logger.exception("could not resolve TELEGRAM_TARGET %r", target)
            self.target_ok = False

    async def send(self, text: str) -> int:
        """Send ``text`` to the resolved target; return the Telegram message id.

        Story 2.5's send_log will consume the id — returned now so the worker
        signature doesn't change. ``FloodWaitError`` propagates to the worker
        (the worker owns retry policy).
        """
        if self.client is None or self._entity is None:
            raise RuntimeError("telegram gateway not ready")
        message = await self.client.send_message(self._entity, text)
        return int(message.id)

    async def disconnect(self) -> None:
        """Disconnect on shutdown (no-op when never connected)."""
        if self.client is not None:
            await self.client.disconnect()


# Module-level singleton, same idiom as ``settings`` (wired in main's lifespan).
gateway = TelegramGateway()
