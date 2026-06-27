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
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GiftKey, Plan, User
from app.db.repos import gift_keys as gift_keys_repo
from app.db.repos import plans as plans_repo
from app.db.repos import tenants as tenants_repo
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
    session: AsyncSession,
    *,
    days: int,
    created_by_user_id: int,
    credits: int = 0,
) -> tuple[GiftKey, Plan]:
    """Mint an active key snapshotting the default plan; returns ``(key, plan)``.

    The tier is the owner-designated default plan (``plans.is_default``), NOT an
    admin choice. ``credits`` (admin-chosen, gift-key-credits feature) rides on
    the key and is granted at claim. No ACTIVE default configured →
    ``no_default_plan``. ``days``/``credits`` bounds are validated by the route
    before this runs.
    """
    plan = await plans_repo.get_default(session)
    if plan is None or not plan.is_active:
        raise no_default_plan()
    for _ in range(_CODE_ATTEMPTS):
        code = gift_keys_repo.generate_code()
        if await gift_keys_repo.get_by_code(session, code) is not None:
            continue
        try:
            # Savepoint: a unique-violation from the (astronomically unlikely)
            # concurrent same-code race rolls back to HERE, leaving the OUTER
            # transaction usable so the loop can retry. Without it the failed
            # flush poisons the session and the next iteration would raise
            # PendingRollbackError instead of retrying.
            async with session.begin_nested():
                key = await gift_keys_repo.create(
                    session,
                    code=code,
                    days=days,
                    credits=credits,
                    plan_id=plan.id,
                    created_by_user_id=created_by_user_id,
                )
        except IntegrityError:
            continue
        return key, plan
    raise RuntimeError("could not generate a unique gift-key code")


async def claim(
    session: AsyncSession, *, user_id: int, code: str
) -> tuple[User, int, int, int | None]:
    """Redeem ``code`` for ``user_id``: +days, basic plan if plan-less, +credits.

    Locks the user row AND the key row (``FOR UPDATE``) so concurrent claims of
    the same key serialize — exactly one wins; the rest see a non-active status.
    Days/plan only apply when ``key.days > 0``; credits (gift-key-credits
    feature) are added when ``key.credits > 0``. Returns
    ``(user, days_added, credits_added, new_balance)`` (``new_balance`` is
    ``None`` when no credits were granted); caller commits.
    """
    # Tolerate manual entry: drop ALL whitespace and match case-insensitively
    # (the copy button preserves case, but a typed code may lowercase or split).
    # Generated codes are unique regardless of case, so this never collapses two.
    code = "".join(code.split())
    user = await users_repo.get_user_by_id(session, user_id, for_update=True)
    if user is None:
        # The user came from a valid session — defensive only.
        raise key_not_found()
    key = await gift_keys_repo.get_by_code(
        session, code, for_update=True, case_insensitive=True
    )
    if key is None:
        raise key_not_found()
    if key.status == "revoked":
        raise key_revoked()
    if key.status != "active":
        raise key_already_claimed()
    # Days/plan only when the key grants days. A credits-only key (days==0)
    # must never assign a plan (it would expire instantly) nor rewind expiry.
    # Assign the basic plan ONLY to a plan-less client; an existing client keeps
    # their tier and only gains days.
    if key.days > 0:
        if user.plan_id is None:
            user.plan_id = key.plan_id
        user.expires_at = plans_service.compute_renewed_expiry(
            user.expires_at, key.days
        )
    # Credits ride the existing money path (row-locked add) inside this same
    # claim transaction, so the single-use key lock grants them exactly once.
    new_balance: int | None = None
    credits_added = 0
    if key.credits > 0:
        new_balance = await tenants_repo.add_credits(
            session, user.tenant_id, key.credits
        )
        # add_credits returns None only if the tenant row vanished (defensive);
        # report what was ACTUALLY granted, not the key's nominal value.
        if new_balance is not None:
            credits_added = key.credits
    await gift_keys_repo.mark_claimed(session, key, claimed_by_user_id=user.id)
    await session.flush()
    return user, key.days, credits_added, new_balance


async def revoke(
    session: AsyncSession, key_id: int, *, revoked_by_user_id: int
) -> GiftKey:
    """Revoke a key (idempotent on already-revoked); caller commits.

    Revoking a CLAIMED key is a kill-switch: it cancels the claimer's plan in
    the same transaction — expires it NOW (``is_plan_expired`` boundary is
    ``<=``, so ``get_current_user`` locks them out on their next request) and
    bulk-revokes their live sessions for an immediate kick, mirroring
    ``set_blocked``. Expiry is the right semantic over a block: an expired
    client can recover by claiming another key (``allow_expired`` flow).

    The cascade runs ONLY when the key granted days (``key.days > 0``) — a
    credits-only key never created a plan, so there is nothing to expire (and
    granted credits are not clawed back). Already-revoked → no-op (so a key
    whose claimer was later renewed is NOT re-expired).
    """
    # ponytail: this locks KEY then USER, the inverse of claim()'s USER→KEY.
    # An ABBA deadlock needs a claim of THIS same already-claimed key to race
    # this revoke — a microscopic window, and Postgres aborts one txn cleanly
    # (no corruption): the loser is either the already-doomed re-claim or an
    # admin retry. If revoke throughput ever matters, peek the key unlocked to
    # get the claimer, lock the user first, then re-lock + re-check the key.
    key = await gift_keys_repo.get_by_id(session, key_id, for_update=True)
    if key is None:
        raise key_not_found()
    if key.status == "revoked":
        return key
    if key.status == "claimed" and key.days > 0 and key.claimed_by_user_id:
        claimer = await users_repo.get_user_by_id(
            session, key.claimed_by_user_id, for_update=True
        )
        # role guard: a client promoted to staff after claiming carries no plan
        # (expiry is a no-op for them), so don't stamp expires_at / kick them.
        if claimer is not None and claimer.role == "client":
            claimer.expires_at = datetime.now(UTC)
            await users_repo.revoke_all_sessions_for_user(session, claimer.id)
    return await gift_keys_repo.revoke(
        session, key, revoked_by_user_id=revoked_by_user_id
    )
