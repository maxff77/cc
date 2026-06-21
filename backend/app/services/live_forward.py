"""Forward Amazon "live" verdicts to an owner-configured Telegram channel.

A "live" is an ``Approved`` Amazon cookie-mode verdict. The owner stores a
single GLOBAL destination channel (a runtime ``system_settings`` knob, like the
send interval / admission cap); every fresh live across every tenant is
forwarded there VERBATIM (the redacted ``clean_text`` — never the raw reply,
never the LIVE/DEAD rebrand).

The forward is best-effort and out-of-band: it does NOT go through the send
worker / scheduler (no pacing, no ``send_log``, no attribution) and any failure
is swallowed by ``gateway.send_to`` so capture is never affected.
"""

import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.telegram import gateway
from app.db.base import async_session_factory
from app.db.repos import system_settings as system_settings_repo

logger = logging.getLogger(__name__)

# Runtime knob key (lives in ``system_settings``; empty/missing = disabled).
# Stores the resolved MARKED chat id as a string (validated at config time via
# ``gateway.resolve_one`` in the admin PUT, same as send targets).
LIVE_FORWARD_KEY = "live_forward_channel"


async def get_channel(session: AsyncSession) -> str:
    """Current forward channel id (resolved marked id), or "" when unset."""
    return (await system_settings_repo.get_value(session, LIVE_FORWARD_KEY)) or ""


def as_identifier(raw: str) -> int | str:
    """Parse a channel ref: a signed ASCII-int marked id → ``int``, anything
    else (an ``@username``) → the raw string. ``-?[0-9]+`` (ASCII only) so
    ``--5`` / ``5-`` / unicode digits fall to the string branch and
    ``resolve_one`` rejects them cleanly — never an ``int()`` crash (→ 500)."""
    return int(raw) if re.fullmatch(r"-?[0-9]+", raw) else raw


async def set_channel(session: AsyncSession, value: str) -> None:
    """Persist the forward channel (flush; the caller owns the transaction).

    ``value`` is the already-resolved marked chat id as a string, or "" to
    disable forwarding.
    """
    await system_settings_repo.set_value(session, LIVE_FORWARD_KEY, value)


async def forward_live(text: str) -> None:
    """Best-effort forward of one verbatim live card to the configured channel.

    Opens its own short read session (the capture session is already committed
    by the time this runs). No-op when no channel is configured. The ENTIRE body
    is exception-safe: ``send_to`` already swallows telegram errors, and this
    wrapper additionally swallows any DB-read / parse failure so a forward can
    NEVER propagate into the capture consumer (which would otherwise abort
    ``process_incoming`` and skip its post-commit WS emits on a re-run).
    """
    try:
        async with async_session_factory() as session:
            channel = await get_channel(session)
        if not channel:
            return
        await gateway.send_to(as_identifier(channel), text)
    except Exception as e:  # noqa: BLE001 — forwarding must never touch capture
        logger.warning(
            "event=live_forward_failed stage=read error=%s: %s",
            type(e).__name__,
            e,
        )
