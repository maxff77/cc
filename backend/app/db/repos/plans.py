"""Data access for the owner-managed pricing-plan catalog.

The catalog is GLOBAL — intentionally NOT tenant-scoped: the owner curates one
shared list of plans for all clients (same deliberate exception as the gate
catalog in ``repos.gates`` and ``repos.system_settings``). Do NOT inject a
tenant_id filter here; the authorization boundary is the route's
``require_role`` owner gate, not a tenant scope.

Pure ORM, flush not commit — callers own the transaction. Read-modify-write
callers (edit/delete) lock the row with ``for_update`` so concurrent mutations
serialize instead of losing a write.
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Plan, User


async def create(
    session: AsyncSession,
    *,
    name: str,
    price_usd: Decimal,
    duration_days: int,
    antispam_seconds: Decimal,
    max_lines_per_batch: int,
    credits: int = 0,
    is_active: bool = True,
) -> Plan:
    """Insert and flush a fresh plan row."""
    plan = Plan(
        name=name,
        price_usd=price_usd,
        duration_days=duration_days,
        antispam_seconds=antispam_seconds,
        max_lines_per_batch=max_lines_per_batch,
        credits=credits,
        is_active=is_active,
    )
    session.add(plan)
    await session.flush()
    return plan


async def list_all(session: AsyncSession) -> list[Plan]:
    """Return every plan (active AND retired) ordered by id (admin catalog view)."""
    stmt = select(Plan).order_by(Plan.id)
    return list((await session.execute(stmt)).scalars().all())


async def list_active(session: AsyncSession) -> list[Plan]:
    """Return only active plans ordered by id (the client assign/renew selector)."""
    stmt = select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_by_id(
    session: AsyncSession, plan_id: int, *, for_update: bool = False
) -> Plan | None:
    """Return the plan with this id (active or retired), or ``None``.

    ``for_update=True`` locks the row until commit — read-modify-write callers
    (edit/delete) must serialize concurrent mutations instead of losing a write.
    """
    return await session.get(Plan, plan_id, with_for_update=for_update)


async def get_by_name(session: AsyncSession, name: str) -> Plan | None:
    """Return the plan with exactly this name (case-sensitive), or ``None``.

    Mirrors the ``uq_plans_name`` constraint — the duplicate-name pre-check for
    create/update (the DB still backstops it on flush).
    """
    stmt = select(Plan).where(Plan.name == name)
    return (await session.execute(stmt)).scalar_one_or_none()


async def update(session: AsyncSession, plan: Plan, **fields: object) -> Plan:
    """Apply the given field changes to ``plan`` and flush (caller commits).

    Only keys present in ``fields`` are written, so a partial edit leaves the
    untouched columns alone. The caller is responsible for fetching ``plan``
    with a row lock (``get_by_id(..., for_update=True)``).
    """
    for key, value in fields.items():
        setattr(plan, key, value)
    await session.flush()
    return plan


async def delete(session: AsyncSession, plan: Plan) -> None:
    """Delete a plan row and flush (caller commits).

    The caller MUST guard against in-use plans first (``count_users_with_plan``);
    the ``users.plan_id`` FK is ``RESTRICT``, so a still-referenced plan would
    otherwise raise an IntegrityError at flush.
    """
    await session.delete(plan)
    await session.flush()


async def count_users_with_plan(session: AsyncSession, plan_id: int) -> int:
    """Return how many users currently reference ``plan_id`` (the in-use guard)."""
    stmt = select(func.count()).select_from(User).where(User.plan_id == plan_id)
    return (await session.execute(stmt)).scalar_one()


async def get_default(session: AsyncSession) -> Plan | None:
    """The owner-designated default ("basic") plan, or ``None`` (gift-keys
    feature). At most one row has ``is_default=true`` — DB-enforced by the
    partial unique index ``uq_plans_one_default``."""
    stmt = select(Plan).where(Plan.is_default.is_(True))
    return (await session.execute(stmt)).scalar_one_or_none()


async def clear_default(session: AsyncSession) -> None:
    """Unset ``is_default`` on whatever plan currently holds it.

    Run BEFORE flagging a new default so the partial unique index never sees two
    trues (the "flip carefully to dodge the partial index" pattern)."""
    await session.execute(
        sql_update(Plan).where(Plan.is_default.is_(True)).values(is_default=False)
    )
