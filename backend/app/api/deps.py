"""Shared FastAPI dependencies — the single source of request identity.

``get_current_user`` is the ONLY place a request's user (and its ``tenant_id``,
for later tenant scoping) comes from. Handlers must never read ``tenant_id``
from request bodies.
"""

from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import async_session_factory, get_session
from app.db.models import User
from app.errors import forbidden, not_authenticated, plan_expired
from app.services import auth as auth_service
from app.services import plans as plans_service


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    """Resolve the authenticated user from the session cookie.

    Raises ``not_authenticated`` (401) when the cookie is absent or the session
    is unknown / revoked / expired.
    """
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise not_authenticated()
    auth_session = await auth_service.get_valid_session(session, token)
    if auth_session is None:
        raise not_authenticated()

    user = auth_session.user
    # Blocked check BEFORE expiry (mirrors login's gate order). Block-time
    # revocation (services/plans.set_blocked) covers sessions that existed when
    # the block ran, but a login racing the block can commit its session AFTER
    # the bulk revoke — this per-request check closes that hole (1.5 review).
    # 401 (not 403) keeps the documented UX: middleware → /login → the login
    # attempt shows the blocked notice (account_blocked).
    if user.is_blocked:
        await _revoke_own_session(token)
        raise not_authenticated()
    # Lazy, auth-time plan expiry (AC1/AC3): the FIRST request after a client's
    # plan lapses revokes their session and returns 403 plan_expired; any later
    # request with the now-revoked cookie falls into the 401 branch above.
    if plans_service.is_plan_expired(user):
        await _revoke_own_session(token)
        raise plan_expired()
    return user


async def _revoke_own_session(token: str) -> None:
    """Revoke ``token`` on its OWN short-lived session and commit.

    ``get_current_user`` must stay read-only on the request-scoped session —
    committing that mid-dependency would also persist anything an earlier
    dependency had staged on it, even though the request then fails.
    """
    async with async_session_factory() as revoke_db:
        await auth_service.revoke_session(revoke_db, token)
        await revoke_db.commit()


def require_role(
    *roles: str,
) -> Callable[[User], Awaitable[User]]:
    """Dependency factory gating a route to the given roles.

    Unused by Story 1.2's endpoints (``/api/auth/me`` is open to any
    authenticated user) but established here so admin stories reuse one gate.
    """
    allowed = frozenset(roles)

    async def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise forbidden()
        return user

    return _checker
