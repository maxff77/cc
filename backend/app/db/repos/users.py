"""Data access for users and auth sessions.

The single place SQL queries for authentication live. Services orchestrate
over these; routers never query the ORM directly.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models import AuthSession, User


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
