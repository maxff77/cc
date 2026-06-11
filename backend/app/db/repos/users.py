"""Data access for users and auth sessions.

The single place SQL queries for authentication live. Services orchestrate
over these; routers never query the ORM directly.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models import AuthSession, Tenant, User


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    """Return the user with this email (case-insensitive), or ``None``.

    The ``users.email`` unique constraint is case-sensitive, so case-variant
    duplicates are technically storable. ``first()`` (ordered for determinism)
    is used instead of ``scalar_one_or_none`` so such a collision degrades to a
    deterministic match rather than a 500.
    """
    stmt = (
        select(User)
        .where(func.lower(User.email) == email.lower())
        .order_by(User.id)
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


# --- User / tenant management (Story 1.3) --------------------------------
#
# Admin/owner user-management queries are intentionally GLOBAL (cross-tenant):
# an admin manages all clients regardless of tenant, so list/get/delete here
# carry NO tenant filter. The authorization boundary is the route's
# ``require_role`` dependency, not a tenant_id scope. (This is distinct from
# client-owned data, which IS tenant-scoped — Epics 2/3.)


async def create_tenant(session: AsyncSession, name: str) -> Tenant:
    """Insert and flush a fresh tenant (one tenant per user)."""
    tenant = Tenant(name=name)
    session.add(tenant)
    await session.flush()
    return tenant


async def create_user(
    session: AsyncSession,
    *,
    tenant_id: int,
    email: str,
    password_hash: str,
    role: str,
    expires_at: datetime | None,
) -> User:
    """Insert and flush a fresh user row."""
    user = User(
        tenant_id=tenant_id,
        email=email,
        password_hash=password_hash,
        role=role,
        expires_at=expires_at,
    )
    session.add(user)
    await session.flush()
    return user


async def list_by_roles(
    session: AsyncSession, roles: Sequence[str]
) -> list[User]:
    """Return all users whose role is in ``roles`` (GLOBAL — not tenant-scoped).

    Ordered by id for a stable listing. Injecting a tenant filter here would
    break admin user management (an admin would see only their own empty
    tenant) — see the module note above.
    """
    stmt = select(User).where(User.role.in_(roles)).order_by(User.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_user_by_id(
    session: AsyncSession, user_id: int, *, for_update: bool = False
) -> User | None:
    """Return the user with this id, or ``None`` (GLOBAL — not tenant-scoped).

    ``for_update=True`` locks the row (``SELECT … FOR UPDATE``) until commit —
    required by read-modify-write callers (e.g. plan renewal) so two concurrent
    mutations serialize instead of silently losing one write.
    """
    return await session.get(User, user_id, with_for_update=for_update)


async def delete_user(session: AsyncSession, user: User) -> None:
    """Delete a user row (its now-empty tenant may be left orphaned — MVP-ok)."""
    await session.delete(user)
    await session.flush()


async def get_active_session_with_user(
    session: AsyncSession, token: str
) -> AuthSession | None:
    """Return the auth session for ``token`` with its ``user`` eagerly loaded.

    Returns ``None`` when the token is unknown, revoked, or expired. Validity is
    decided in SQL (``now()``) so there is no python/DB clock drift.
    """
    stmt = (
        select(AuthSession)
        .options(joinedload(AuthSession.user))
        .where(
            AuthSession.token == token,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > func.now(),
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def add_session(
    session: AsyncSession, *, user_id: int, token: str, expires_at: datetime
) -> AuthSession:
    """Insert and flush a fresh auth-session row."""
    row = AuthSession(user_id=user_id, token=token, expires_at=expires_at)
    session.add(row)
    await session.flush()
    return row


async def mark_session_revoked(session: AsyncSession, token: str) -> None:
    """Set ``revoked_at = now()`` for the row carrying ``token`` (no-op if absent)."""
    row = (
        await session.execute(select(AuthSession).where(AuthSession.token == token))
    ).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)


async def revoke_all_sessions_for_user(
    session: AsyncSession, user_id: int
) -> None:
    """Revoke every live auth session for ``user_id`` in one statement.

    Sets ``revoked_at`` on all of the user's rows where it IS NULL — the bulk,
    per-user variant of ``mark_session_revoked`` (which is per-token). A single
    ``update()`` keeps it race-free in one round trip; this is the immediate
    lockout backing ``block_user`` (Story 1.5). The user's next request then
    falls into ``get_current_user``'s ``not_authenticated`` (401) branch.
    """
    await session.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
