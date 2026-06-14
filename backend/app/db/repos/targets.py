"""Data access for the global send-target list (multi-target sending).

GLOBAL — intentionally NOT tenant-scoped (same deliberate exception as
``repos.gates`` / ``repos.users``): the owner curates one shared list of
destinations. Do NOT inject a tenant_id filter. The authorization boundary is
the route's ``require_owner`` dependency, not a tenant scope.

flush-not-commit: the request/caller owns the transaction (mirrors every other
repo). ``for_update`` locks the row on read-modify-write paths (toggle/delete).
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SendTarget


async def list_all(session: AsyncSession) -> list[SendTarget]:
    """Every target (enabled or not), stable order for the owner view."""
    stmt = select(SendTarget).order_by(SendTarget.created_at, SendTarget.id)
    return list((await session.execute(stmt)).scalars().all())


async def list_enabled(session: AsyncSession) -> list[SendTarget]:
    """Only ENABLED targets — what the gateway should try to resolve+rotate."""
    stmt = (
        select(SendTarget)
        .where(SendTarget.enabled.is_(True))
        .order_by(SendTarget.created_at, SendTarget.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_by_id(
    session: AsyncSession, target_id: int, *, for_update: bool = False
) -> SendTarget | None:
    """Return the target with this id, or ``None``.

    ``for_update=True`` locks the row until commit — toggle/delete are
    read-modify-write and must serialize concurrent mutations.
    """
    return await session.get(SendTarget, target_id, with_for_update=for_update)


async def get_by_chat_id(session: AsyncSession, chat_id: int) -> SendTarget | None:
    """Return the target for this marked chat id, or ``None`` (duplicate check)."""
    stmt = select(SendTarget).where(SendTarget.chat_id == chat_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def count(session: AsyncSession) -> int:
    """Total rows — used by the boot seed (empty list ⇒ seed from env)."""
    stmt = select(func.count()).select_from(SendTarget)
    return int((await session.execute(stmt)).scalar_one())


async def create(session: AsyncSession, *, chat_id: int, label: str) -> SendTarget:
    """Insert and flush a fresh enabled target."""
    target = SendTarget(chat_id=chat_id, label=label)
    session.add(target)
    await session.flush()
    return target


async def delete(session: AsyncSession, target: SendTarget) -> None:
    """Hard-delete a target (no history references it)."""
    await session.delete(target)
