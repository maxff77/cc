"""Send-target orchestration: the bridge between the DB list and the live gateway.

The gateway is DB-agnostic — this service is the ONLY place that reads
``send_targets`` and pushes the resolved set into it. Owner-only at the API
boundary (recurso global, like the gate catalog). On boot it seeds the first
target from the legacy ``TELEGRAM_TARGET`` env so production keeps sending
without manual setup, then loads the enabled list into the gateway.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.telegram import gateway
from app.db.models import SendTarget
from app.db.repos import targets as targets_repo

logger = logging.getLogger(__name__)


async def reload_gateway(session: AsyncSession) -> dict[str, list[int]]:
    """Push the ENABLED targets into the gateway; return its resolution report."""
    enabled = await targets_repo.list_enabled(session)
    return await gateway.reload_targets([(t.chat_id, t.label) for t in enabled])


async def list_with_status(
    session: AsyncSession,
) -> list[tuple[SendTarget, bool]]:
    """Every target paired with whether the gateway currently has it resolved.

    Resolution is transient (depends on the live session) so it is NOT stored —
    derived here by intersecting the saved chat ids with the gateway's live set.
    """
    targets = await targets_repo.list_all(session)
    live = gateway.resolved_ids()
    return [(t, t.chat_id in live) for t in targets]


async def discover(limit: int = 100) -> list[tuple[int, str]]:
    """Chats the account is in, for the owner to pick from. Raises (RuntimeError)
    when the gateway isn't authorized — the route maps it to 503."""
    return await gateway.list_dialogs(limit=limit)


async def ensure_seeded(session: AsyncSession) -> None:
    """First boot: if the list is empty and ``TELEGRAM_TARGET`` is set, seed one
    row from it (resolved to a chat_id) so the single-target deployment keeps
    sending unchanged. No-op when rows already exist or the env is unset/unresolvable.
    """
    if await targets_repo.count(session) > 0:
        return
    raw = settings.telegram_target.strip().lstrip("@")
    if not raw:
        return
    chat_id = await gateway.resolve_one(raw)
    if chat_id is None:
        logger.warning("TELEGRAM_TARGET %r did not resolve — no seed target", raw)
        return
    try:
        await targets_repo.create(session, chat_id=chat_id, label=raw)
        await session.commit()
    except IntegrityError:
        # Another (overlapping) boot raced us to seed the same chat — idempotent.
        await session.rollback()
        return
    logger.info("seeded send target from TELEGRAM_TARGET: %s (%s)", raw, chat_id)
