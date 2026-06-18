"""Data access for gate categories (Story 2.2 owner addition).

GLOBAL catalog — intentionally NOT tenant-scoped, same deliberate exception as
``repos.gates``: the owner curates one shared category list for all tenants.
Do NOT inject a tenant_id filter here; the authorization boundary is the
route's ``require_owner`` dependency.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Gate, GateCategory


async def list_all(session: AsyncSession) -> list[GateCategory]:
    """Return every category ordered by name (no soft-delete — all rows live)."""
    stmt = select(GateCategory).order_by(GateCategory.name)
    return list((await session.execute(stmt)).scalars().all())


async def get_by_id(
    session: AsyncSession, category_id: int, *, for_update: bool = False
) -> GateCategory | None:
    """Return the category with this id, or ``None``.

    ``for_update=True`` locks the row — rename is read-modify-write.
    """
    return await session.get(GateCategory, category_id, with_for_update=for_update)


async def get_by_name(session: AsyncSession, name: str) -> GateCategory | None:
    """Return the category with exactly this name (case-sensitive), or ``None``."""
    stmt = select(GateCategory).where(GateCategory.name == name)
    return (await session.execute(stmt)).scalar_one_or_none()


async def create(
    session: AsyncSession, *, name: str, special_mode: bool = False
) -> GateCategory:
    """Insert and flush a fresh category."""
    category = GateCategory(name=name, special_mode=special_mode)
    session.add(category)
    await session.flush()
    return category


async def has_gates(session: AsyncSession, category_id: int) -> bool:
    """``True`` iff any ACTIVE gate references this category.

    Retired (soft-deleted) gates don't block deletion at the API layer; the
    DB-level RESTRICT still protects rows referenced by retired gates — the
    route maps that IntegrityError to ``category_in_use`` as well.
    """
    stmt = (
        select(Gate.id)
        .where(Gate.category_id == category_id, Gate.deleted_at.is_(None))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def reassign_retired_gates(session: AsyncSession, category_id: int) -> bool:
    """Detach RETIRED gates from ``category_id`` before deleting the category.

    Retired gates must not block deletion (they are invisible everywhere — an
    owner can't reassign them, so leaving them attached would make the
    category permanently undeletable under the RESTRICT FK). Their rows are
    KEPT (2.1 design: gate rows are never hard-deleted) and re-pointed at the
    oldest OTHER category, deterministically.

    Returns ``True`` when the category is now free of retired gates (none
    existed, or all were moved); ``False`` when retired gates exist but no
    other category can take them (the caller must reject the delete —
    RESTRICT would fire).
    """
    retired_stmt = select(Gate).where(
        Gate.category_id == category_id, Gate.deleted_at.is_not(None)
    )
    retired = list((await session.execute(retired_stmt)).scalars().all())
    if not retired:
        return True
    fallback_stmt = (
        select(GateCategory.id)
        .where(GateCategory.id != category_id)
        .order_by(GateCategory.id)
        .limit(1)
    )
    fallback_id = (await session.execute(fallback_stmt)).scalar_one_or_none()
    if fallback_id is None:
        return False
    for gate in retired:
        gate.category_id = fallback_id
    await session.flush()
    return True


async def delete(session: AsyncSession, category: GateCategory) -> None:
    """Hard-delete a category row (no soft-delete for categories).

    Callers must have verified no ACTIVE gate references it (``has_gates``)
    and detached retired ones (``reassign_retired_gates``) — otherwise the
    RESTRICT FK raises at flush.
    """
    await session.delete(category)
    await session.flush()
