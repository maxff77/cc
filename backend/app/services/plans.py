"""Plan service: expiry predicate (1.4) + renew/extend & block/unblock (1.5).

The expiry predicate stays pure domain logic over a ``User`` row. The renew/
block/unblock operations need the DB, so they take an ``AsyncSession`` like
``services/users.create_account`` does: the service orchestrates and flushes,
the router maps errors and commits. The router validates inputs BEFORE calling.

Clock source: this module deliberately uses the APP clock (``datetime.now(UTC)``)
— the documented exception to the SQL-``now()`` convention (see
``is_plan_expired``). ``compute_renewed_expiry`` keeps that.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.repos import users as users_repo


def is_plan_expired(user: User) -> bool:
    """Return ``True`` iff ``user`` is a client whose plan has lapsed.

    Predicate: ``role == "client" AND expires_at IS NOT NULL AND expires_at <=
    now(UTC)``. The boundary is ``<=`` (expired exactly at the instant of
    expiry).

    owner/admin rows carry no plan (``expires_at IS NULL``) and are never
    expired. A client with ``expires_at = None`` is treated as NOT expired —
    defensive only: ``create_account`` always sets an expiry for clients, so
    this branch should not occur in practice.

    ``expires_at`` is timezone-aware (timestamptz), so it is compared against
    ``datetime.now(UTC)``; stripping tzinfo would raise ``TypeError``.

    Clock source: this uses the APP clock — a deliberate exception to the
    repo convention of deciding expiry in SQL (``func.now()``, see
    ``repos/users.get_active_session_with_user``). This module is pure (no DB
    round-trip available) and plan deadlines are day-scale, so seconds of
    app/DB clock skew cannot move the lockout meaningfully.
    """
    if user.role != "client":
        return False
    if user.expires_at is None:
        return False
    return user.expires_at <= datetime.now(UTC)


def compute_renewed_expiry(current: datetime | None, plan_days: int) -> datetime:
    """Return the new expiry when adding ``plan_days`` to a plan.

    Anchored on ``max(now(UTC), current)``: renewing an ACTIVE plan extends it
    from its current expiry (days stack — paying early loses no days), while
    renewing an EXPIRED plan grants days from TODAY. The latter is essential for
    AC2 — anchoring on ``current`` alone would let an admin add 30 days to a plan
    that lapsed 60 days ago and still leave the account expired, so the renewal
    would not restore access.

    ``current`` is timezone-aware (timestamptz); ``None`` (no prior plan) anchors
    on now.
    """
    now = datetime.now(UTC)
    anchor = now if current is None else max(current, now)
    return anchor + timedelta(days=plan_days)


async def renew_plan(
    session: AsyncSession, user: User, new_expiry: datetime
) -> User:
    """Set ``user``'s plan expiry and flush (caller commits).

    The router resolves the renewal mode into ``new_expiry`` (add-days via
    ``compute_renewed_expiry``, or a validated future ``expires_at`` verbatim)
    and is responsible for fetching ``user`` with a row lock — this is a
    read-modify-write, and without ``FOR UPDATE`` two concurrent renewals would
    silently lose one of the extensions.
    """
    user.expires_at = new_expiry
    await session.flush()
    return user


async def set_blocked(session: AsyncSession, user: User, *, blocked: bool) -> User:
    """Set ``user.is_blocked`` and flush (caller commits). Idempotent.

    Blocking ALSO bulk-revokes every live auth session in the same transaction
    — the immediate-lockout mechanism: the user's next request hits
    ``get_current_user``'s 401 branch, and their next login shows the blocked
    notice (Story 1.2's ``account_blocked``). ``get_current_user`` additionally
    checks ``is_blocked`` per request (1.5 review), closing the race where a
    login concurrent with the block commits a session after the bulk revoke ran.

    Unblocking does NOT restore the revoked sessions — the client simply logs
    in again (AC4).
    """
    user.is_blocked = blocked
    if blocked:
        await users_repo.revoke_all_sessions_for_user(session, user.id)
    await session.flush()
    return user
