"""Data access for the global gate catalog (Story 2.1).

The catalog is GLOBAL — intentionally NOT tenant-scoped: the owner curates one
shared list of gates for all tenants (same deliberate exception as admin user
management in ``repos.users``). Do NOT inject a tenant_id filter here. The
authorization boundary is the route's ``require_owner`` / ``get_current_user``
dependency, not a tenant scope.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Gate


async def list_active(session: AsyncSession) -> list[Gate]:
    """Return active catalog entries (``deleted_at IS NULL``) ordered by value.

    The category relationship is eager-loaded (``selectinload``) — ``GateOut``
    carries ``category_name`` and an async lazy-load would raise (no N+1
    either, Story 2.2).
    """
    stmt = (
        select(Gate)
        .options(selectinload(Gate.category))
        .where(Gate.deleted_at.is_(None))
        .order_by(Gate.value)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_by_id(
    session: AsyncSession, gate_id: int, *, for_update: bool = False
) -> Gate | None:
    """Return the gate with this id (active or retired), or ``None``.

    ``for_update=True`` locks the row until commit — read-modify-write callers
    (edit) must serialize concurrent mutations instead of losing one write.
    """
    return await session.get(Gate, gate_id, with_for_update=for_update)


async def get_active_by_value(session: AsyncSession, value: str) -> Gate | None:
    """Return the ACTIVE gate with exactly this value (case-sensitive), or ``None``.

    Mirrors the partial unique index ``uq_gates_value_active``: retired rows
    don't count, so a retired value can be re-created.
    """
    stmt = select(Gate).where(Gate.value == value, Gate.deleted_at.is_(None))
    return (await session.execute(stmt)).scalar_one_or_none()


async def create(
    session: AsyncSession,
    *,
    value: str,
    name: str,
    display_value: str,
    category_id: int,
) -> Gate:
    """Insert and flush a fresh active gate.

    ``value`` is the real command (verbatim), ``name`` the friendly label,
    ``display_value`` the owner-authored "Comando visible" clients see.
    """
    gate = Gate(
        value=value,
        name=name,
        display_value=display_value,
        category_id=category_id,
    )
    session.add(gate)
    await session.flush()
    return gate


async def soft_delete(session: AsyncSession, gate: Gate) -> None:
    """Retire a gate: set ``deleted_at = now(UTC)``. Idempotent.

    Never deletes the row — history (batches/sessions snapshot the gate string)
    must keep displaying retired gates verbatim.
    """
    if gate.deleted_at is None:
        gate.deleted_at = datetime.now(UTC)
