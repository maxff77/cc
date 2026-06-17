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
from app.errors import (
    forbidden,
    not_authenticated,
    password_change_required,
    plan_expired,
)
from app.services import auth as auth_service
from app.services import plans as plans_service


async def _resolve_session_user(
    request: Request, session: AsyncSession, *, enforce_expiry: bool = True
) -> User:
    """Resolve the authenticated user from the session cookie.

    Raises ``not_authenticated`` (401) when the cookie is absent or the session
    is unknown / revoked / expired.

    ``enforce_expiry=False`` skips ONLY the plan-expiry 403 (the blocked
    hard-revoke still runs): the gift-key claim path needs it so a lapsed /
    just-registered client can redeem to regain access.
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
    # Plan expiry (AC1/AC3): a client whose plan has lapsed gets a REPEATABLE
    # 403 plan_expired on every request — the session is NOT revoked, mirroring
    # the must_change_password gate in get_current_user. Keeping the session
    # alive is what lets an admin's renewal auto-recover the client: their open
    # /expired tab polls /me, the 403 flips to 200 the instant the plan is
    # renewed, and the page re-enters them with no manual re-login (the /expired
    # screen has no logout/login affordance of its own). The expired session can
    # still DO nothing — every gated endpoint 403s — and it dies naturally at
    # SESSION_TTL. Contrast the is_blocked branch above, which DOES hard-revoke
    # (a deliberate, irreversible lockout).
    if enforce_expiry and plans_service.is_plan_expired(user):
        raise plan_expired()
    return user


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    """``_resolve_session_user`` + the forced-password-change gate (Story 1.6).

    Gate order: blocked → expired → flag. Only the blocked gate hard-revokes
    the session (a deliberate lockout); the expired and flag gates leave it
    intact and return a REPEATABLE 403 the user recovers from in place — a plan
    renewal for ``plan_expired``, completing the change-password flow for the
    flag — so middleware/prefetch consumption is harmless for both.
    """
    user = await _resolve_session_user(request, session)
    if user.must_change_password:
        raise password_change_required()
    return user


async def get_current_user_allow_pending_password(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    """``_resolve_session_user`` WITHOUT the flag gate.

    Used EXCLUSIVELY by the change-password endpoint — the single hole the
    architecture mandates ("flag on user; middleware blocks everything except
    the change-password endpoint").
    """
    return await _resolve_session_user(request, session)


async def get_current_user_allow_expired(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    """``_resolve_session_user`` WITHOUT the expiry gate (nor the flag gate).

    Used EXCLUSIVELY by the gift-key claim endpoint: a just-registered or lapsed
    client (``plan_expired``, or still ``must_change_password``) must be able to
    redeem a key to (re)gain access — claiming is the recovery path. The blocked
    hard-revoke still applies (a blocked session is 401'd) and ``tenant_id``
    still comes only from the session; the route additionally guards
    ``role == 'client'``. Mirror of ``get_current_user_allow_pending_password``,
    one more deliberate hole.
    """
    return await _resolve_session_user(request, session, enforce_expiry=False)


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
