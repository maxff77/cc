"""Plan service: expiry predicate (1.4) + renew/extend & block/unblock (1.5)
+ the owner-managed pricing-plan catalog (plan-catalog feature).

The expiry predicate stays pure domain logic over a ``User`` row. The renew/
block/unblock operations and the catalog CRUD need the DB, so they take an
``AsyncSession`` like ``services/users.create_account`` does: the service
orchestrates and flushes, the router maps errors and commits. The router
validates inputs BEFORE calling.

Clock source: this module deliberately uses the APP clock (``datetime.now(UTC)``)
— the documented exception to the SQL-``now()`` convention (see
``is_plan_expired``). ``compute_renewed_expiry`` keeps that.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Plan, User
from app.db.repos import plans as plans_repo
from app.db.repos import users as users_repo
from app.errors import (
    invalid_plan,
    plan_in_use,
    plan_name_taken,
    plan_not_found,
)


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


# --- Duration-based expiry helpers (plan-catalog feature) ----------------


def compute_expiry_from_duration(duration_days: int) -> datetime:
    """Fresh expiry for a newly-assigned plan: ``now(UTC) + duration_days``.

    Used by ``create_account`` when a client is created on a plan — the same
    APP-clock source as ``compute_renewed_expiry`` (the documented exception to
    the SQL-``now()`` convention; plan deadlines are day-scale, so clock skew
    cannot move the lockout meaningfully).
    """
    return datetime.now(UTC) + timedelta(days=duration_days)


def compute_renewed_expiry_from_duration(
    current: datetime | None, duration_days: int
) -> datetime:
    """Renew variant: extend ``duration_days`` from ``max(now(UTC), current)``.

    Thin alias of ``compute_renewed_expiry`` keyed on a plan's ``duration_days``
    — renewing an ACTIVE plan stacks days onto the current expiry, renewing an
    EXPIRED one grants days from today (so the renewal actually restores
    access, the AC2 rationale documented on ``compute_renewed_expiry``).
    """
    return compute_renewed_expiry(current, duration_days)


# --- Plan-catalog CRUD (plan-catalog feature) ----------------------------
#
# GLOBAL/owner-curated like the gate catalog — no tenant scope here; the
# authorization boundary is the route's owner ``require_role`` gate. Each op
# flushes; the router commits. Field-bound validation (antispam/duration/
# max-lines >= 1, price >= 0) belongs to the router BEFORE calling.


async def create_plan(
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
    """Create a plan; rejects a duplicate name with ``plan_name_taken``.

    The pre-check is racy (two concurrent creates of the same name only trip
    the DB unique constraint at flush), so the IntegrityError is mapped to the
    SAME ``plan_name_taken`` contract instead of a 500 — the ``create_account``
    duplicate-email idiom.
    """
    if await plans_repo.get_by_name(session, name) is not None:
        raise plan_name_taken()
    try:
        return await plans_repo.create(
            session,
            name=name,
            price_usd=price_usd,
            duration_days=duration_days,
            antispam_seconds=antispam_seconds,
            max_lines_per_batch=max_lines_per_batch,
            credits=credits,
            is_active=is_active,
        )
    except IntegrityError as exc:
        raise plan_name_taken() from exc


async def list_plans(session: AsyncSession, *, active_only: bool = False) -> list[Plan]:
    """Return the catalog — every plan, or only active ones (``active_only``)."""
    if active_only:
        return await plans_repo.list_active(session)
    return await plans_repo.list_all(session)


async def get_plan(session: AsyncSession, plan_id: int) -> Plan:
    """Return the plan with this id or raise ``plan_not_found``."""
    plan = await plans_repo.get_by_id(session, plan_id)
    if plan is None:
        raise plan_not_found()
    return plan


async def update_plan(session: AsyncSession, plan_id: int, **fields: object) -> Plan:
    """Edit a plan (locks the row); flush, caller commits.

    A name change duplicating another plan's name is rejected with
    ``plan_name_taken`` — pre-checked AND backstopped by the DB unique
    constraint at flush. Only the provided ``fields`` are written.
    """
    plan = await plans_repo.get_by_id(session, plan_id, for_update=True)
    if plan is None:
        raise plan_not_found()
    new_name = fields.get("name")
    if (
        isinstance(new_name, str)
        and new_name != plan.name
        and await plans_repo.get_by_name(session, new_name) is not None
    ):
        raise plan_name_taken()
    # A retired plan can no longer be the gift-key default: a deactivated default
    # would still carry the "Keys" flag yet break key generation with a
    # misleading no_default_plan. Clear it in the same write (gift-keys feature).
    if fields.get("is_active") is False:
        fields = {**fields, "is_default": False}
    try:
        return await plans_repo.update(session, plan, **fields)
    except IntegrityError as exc:
        raise plan_name_taken() from exc


async def delete_plan(session: AsyncSession, plan_id: int) -> None:
    """Delete a plan; rejects deletion while ≥1 user references it.

    The ``users.plan_id`` FK is ``RESTRICT``; the explicit ``plan_in_use``
    guard (mirror of ``category_in_use``) gives the owner a clear "deactivate
    instead" message rather than a raw IntegrityError. Retire via
    ``is_active=false``; never delete a plan with historical assignments.
    """
    plan = await plans_repo.get_by_id(session, plan_id, for_update=True)
    if plan is None:
        raise plan_not_found()
    if await plans_repo.count_users_with_plan(session, plan_id) > 0:
        raise plan_in_use()
    # The plan-row lock does NOT serialize a concurrent create/renew that reads
    # the plan WITHOUT for_update and assigns ``user.plan_id`` — so a reference
    # can land between the count above and this delete. The FK is RESTRICT, so
    # that case raises at flush rather than orphaning; map it to the clean
    # plan_in_use ("deactivate instead") rather than a raw 500.
    try:
        await plans_repo.delete(session, plan)
    except IntegrityError as exc:
        raise plan_in_use() from exc


async def set_default_plan(session: AsyncSession, plan_id: int) -> Plan:
    """Flag ``plan_id`` as the default ("basic") plan for gift keys.

    Clears the prior default FIRST so the partial unique index
    ``uq_plans_one_default`` never sees two trues (the documented flip pattern).
    Locks the target row; unknown id → ``plan_not_found``. Caller commits.
    """
    plan = await plans_repo.get_by_id(session, plan_id, for_update=True)
    if plan is None:
        raise plan_not_found()
    # Only an ACTIVE plan can be the default: generate() rejects an inactive
    # default with no_default_plan, so flagging one would set a default that
    # visibly carries the "Keys" badge yet silently breaks key generation.
    if not plan.is_active:
        raise invalid_plan("Solo un plan activo puede ser el predeterminado de keys.")
    await plans_repo.clear_default(session)
    plan.is_default = True
    await session.flush()
    return plan
