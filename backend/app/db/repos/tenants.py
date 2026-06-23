"""Data access for tenant rows — the credit-balance read/modify/write paths
(credits feature).

GLOBAL-ish like the gate/plan catalogs: callers (capture pipeline, owner
recharge, plan grant) already resolved the tenant they trust — ``tenant_id`` is
never read from a request here. The capture consumer charges outside any
request and is single, but the balance is ALSO written by the owner-recharge
endpoint and the plan grant, so every mutation takes ``SELECT … FOR UPDATE`` to
serialize a concurrent recharge against an in-flight charge.

Pure ORM, flush not commit — callers own the transaction.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant, User


async def labels(
    session: AsyncSession, tenant_ids: list[int]
) -> dict[int, tuple[str, str | None]]:
    """Map ``tenant_id → (name, client_email)`` for the owner monitoring panel.

    ``name`` is the tenant's display name; ``client_email`` is any user with
    ``role='client'`` on that tenant (the human behind the volume), the lowest
    user id wins, or ``None`` when the tenant has no client user. Two queries
    (names, then emails). Missing ids are simply absent from the map.
    """
    if not tenant_ids:
        return {}
    names = {
        tid: name
        for tid, name in (
            await session.execute(
                select(Tenant.id, Tenant.name).where(Tenant.id.in_(tenant_ids))
            )
        ).all()
    }
    emails: dict[int, str] = {}
    rows = (
        await session.execute(
            select(User.tenant_id, User.email)
            .where(User.tenant_id.in_(tenant_ids), User.role == "client")
            .order_by(User.id)
        )
    ).all()
    for tid, email in rows:
        emails.setdefault(tid, email)  # first (lowest-id) client per tenant
    return {tid: (name, emails.get(tid)) for tid, name in names.items()}


async def get_credit_balance(session: AsyncSession, tenant_id: int) -> int:
    """The tenant's current credit balance (0 if the tenant is gone)."""
    stmt = select(Tenant.credit_balance).where(Tenant.id == tenant_id)
    balance = (await session.execute(stmt)).scalar_one_or_none()
    return balance if balance is not None else 0


async def get_credit_balances(
    session: AsyncSession, tenant_ids: list[int]
) -> dict[int, int]:
    """Map ``tenant_id → credit_balance`` for the given ids (admin user list).

    One query instead of N — the admin users table shows each client's balance.
    Missing ids simply don't appear in the map (caller defaults to 0).
    """
    if not tenant_ids:
        return {}
    stmt = select(Tenant.id, Tenant.credit_balance).where(Tenant.id.in_(tenant_ids))
    return {tid: bal for tid, bal in (await session.execute(stmt)).all()}


async def add_credits(
    session: AsyncSession,
    tenant_id: int,
    delta: int,
    *,
    clamp_zero: bool = False,
) -> int | None:
    """Add ``delta`` (may be negative) to the tenant's balance under a row lock.

    ``clamp_zero=True`` floors the result at 0 — the charge path never lets a
    balance go negative (a mid-batch overrun past zero clamps; the response is
    still persisted). Returns the new balance, or ``None`` if the tenant row is
    gone (deleted mid-charge). Flush, caller commits.
    """
    tenant = (
        await session.execute(
            select(Tenant).where(Tenant.id == tenant_id).with_for_update()
        )
    ).scalar_one_or_none()
    if tenant is None:
        return None
    new_balance = tenant.credit_balance + delta
    if clamp_zero and new_balance < 0:
        new_balance = 0
    tenant.credit_balance = new_balance
    await session.flush()
    return new_balance


async def set_credit_balance(
    session: AsyncSession, tenant_id: int, value: int
) -> int | None:
    """Set the tenant's balance to ``value`` (owner recharge) under a row lock.

    Returns the new balance, or ``None`` if the tenant row is gone. Flush,
    caller commits. ``value`` must be ``>= 0`` (validated by the route).
    """
    tenant = (
        await session.execute(
            select(Tenant).where(Tenant.id == tenant_id).with_for_update()
        )
    ).scalar_one_or_none()
    if tenant is None:
        return None
    tenant.credit_balance = value
    await session.flush()
    return value
