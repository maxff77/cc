"""Gift-key service: generate / claim / revoke (gift-keys feature).

A key carries only ``days`` + a SNAPSHOT of the owner-designated default
("basic") plan; admins never choose the tier (anti-abuse). Claiming adds days
(``compute_renewed_expiry`` — stacks on an active plan, grants from today on a
lapsed one) and NEVER touches credits; it assigns the basic plan ONLY to a
plan-less client (a new/just-registered user), leaving an existing client's
plan intact.

GLOBAL like the plan/gate catalogs — no tenant scope here; the authorization
boundary is the route's role gate. Each op flushes; the router commits.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GiftKey, Plan, User
from app.db.repos import gift_keys as gift_keys_repo
from app.db.repos import plans as plans_repo
from app.db.repos import users as users_repo
from app.errors import (
    key_already_claimed,
    key_not_found,
    key_revoked,
    no_default_plan,
)
from app.services import plans as plans_service

logger = logging.getLogger(__name__)

# Collision retries on the random code. 31^12 makes a single collision
# astronomically unlikely — this loop never realistically exhausts.
_CODE_ATTEMPTS = 8


async def generate(
    session: AsyncSession, *, days: int, created_by_user_id: int
) -> tuple[GiftKey, Plan]:
    """Mint an active key snapshotting the default plan; returns ``(key, plan)``.

    The tier is the owner-designated default plan (``plans.is_default``), NOT an
    admin choice. No ACTIVE default configured → ``no_default_plan``. ``days``
    bounds are validated by the route before this runs.
    """
    plan = await plans_repo.get_default(session)
    if plan is None or not plan.is_active:
        raise no_default_plan()
    for _ in range(_CODE_ATTEMPTS):
        code = gift_keys_repo.generate_code()
        if await gift_keys_repo.get_by_code(session, code) is not None:
            continue
        try:
            key = await gift_keys_repo.create(
                session,
                code=code,
                days=days,
                plan_id=plan.id,
                created_by_user_id=created_by_user_id,
            )
        except IntegrityError:
            # A concurrent mint raced us onto the same code (essentially
            # impossible at 31^12) — try a fresh one.
            continue
        return key, plan
    raise RuntimeError("could not generate a unique gift-key code")


async def claim(
    session: AsyncSession, *, user_id: int, code: str
) -> tuple[User, int]:
    """Redeem ``code`` for ``user_id``: +days, basic plan if plan-less, NO credits.

    Locks the user row AND the key row (``FOR UPDATE``) so concurrent claims of
    the same key serialize — exactly one wins; the rest see a non-active status.
    Returns ``(user, days_added)``; caller commits.
    """
    user = await users_repo.get_user_by_id(session, user_id, for_update=True)
    if user is None:
        # The user came from a valid session — defensive only.
        raise key_not_found()
    key = await gift_keys_repo.get_by_code(session, code, for_update=True)
    if key is None:
        raise key_not_found()
    if key.status == "revoked":
        raise key_revoked()
    if key.status != "active":
        raise key_already_claimed()
    # Assign the basic plan ONLY to a plan-less client; an existing client keeps
    # their tier and only gains days. Never grant credits — keys are time-only.
    if user.plan_id is None:
        user.plan_id = key.plan_id
    user.expires_at = plans_service.compute_renewed_expiry(
        user.expires_at, key.days
    )
    await gift_keys_repo.mark_claimed(session, key, claimed_by_user_id=user.id)
    await session.flush()
    return user, key.days


async def revoke(
    session: AsyncSession, key_id: int, *, revoked_by_user_id: int
) -> GiftKey:
    """Revoke an UNCLAIMED key (idempotent on already-revoked).

    A claimed key cannot be revoked (the days are already granted) →
    ``key_already_claimed``. Locks the row; caller commits.
    """
    key = await gift_keys_repo.get_by_id(session, key_id, for_update=True)
    if key is None:
        raise key_not_found()
    if key.status == "revoked":
        return key
    if key.status == "claimed":
        raise key_already_claimed()
    return await gift_keys_repo.revoke(
        session, key, revoked_by_user_id=revoked_by_user_id
    )
