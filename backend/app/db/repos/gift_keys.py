"""Data access for redeemable gift keys (gift-keys feature).

GLOBAL — no tenant scoping (the gate/plan-catalog convention): the actor's
identity is the authorization boundary at the route, not a tenant filter. The
claim path is a read-modify-write that MUST serialize concurrent claims of the
same code, so ``get_by_code(for_update=True)`` locks the row.

Pure ORM, flush not commit — callers own the transaction.
"""

import secrets
from datetime import UTC, datetime

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import GiftKey, Plan, User

# Human-typed claim codes: an UNAMBIGUOUS base-32 alphabet (no 0/O/1/I/L) so a
# client copying a code can't trip on look-alike glyphs. Three 4-char groups
# after the brand prefix ⇒ 31^12 ≈ 7.8e17 space (collisions astronomically
# unlikely; the service still pre-checks + retries).
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_PREFIX = "RangerX"
_CODE_GROUPS = 3
_CODE_GROUP_LEN = 4


def generate_code() -> str:
    """Return a fresh random claim code, e.g. ``RangerX-AB2C-DE3F-GH4J``."""
    groups = [
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_GROUP_LEN))
        for _ in range(_CODE_GROUPS)
    ]
    return "-".join([_CODE_PREFIX, *groups])


async def create(
    session: AsyncSession,
    *,
    code: str,
    days: int,
    plan_id: int,
    created_by_user_id: int,
    credits: int = 0,
) -> GiftKey:
    """Insert and flush a fresh active key row."""
    key = GiftKey(
        code=code,
        days=days,
        credits=credits,
        plan_id=plan_id,
        status="active",
        created_by_user_id=created_by_user_id,
    )
    session.add(key)
    await session.flush()
    return key


async def get_by_code(
    session: AsyncSession,
    code: str,
    *,
    for_update: bool = False,
    case_insensitive: bool = False,
) -> GiftKey | None:
    """Return the key with this code, or ``None``.

    ``for_update=True`` locks the row until commit — the claim path is a
    read-modify-write and without it two simultaneous claims of the same code
    would both pass the status check and double-grant.

    ``case_insensitive=True`` (the claim path) matches regardless of case so a
    manually-typed code still resolves; generated codes are unique either way,
    so this never collapses two distinct keys.
    """
    column = func.lower(GiftKey.code) if case_insensitive else GiftKey.code
    target = code.lower() if case_insensitive else code
    stmt = select(GiftKey).where(column == target)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_id(
    session: AsyncSession, key_id: int, *, for_update: bool = False
) -> GiftKey | None:
    """Return the key with this id, or ``None`` (revoke path locks the row)."""
    return await session.get(GiftKey, key_id, with_for_update=for_update)


async def list_all(session: AsyncSession) -> list[Row]:
    """The keys log, newest first: each row carries the key + plan name +
    minting/claiming emails (the owner's admin-abuse audit view).

    Returns ``Row`` objects with ``.GiftKey``, ``.plan_name``,
    ``.created_by_email``, ``.claimed_by_email`` — the router maps them.
    """
    creator = aliased(User)
    claimer = aliased(User)
    stmt = (
        select(
            GiftKey,
            Plan.name.label("plan_name"),
            creator.email.label("created_by_email"),
            claimer.email.label("claimed_by_email"),
        )
        .join(Plan, GiftKey.plan_id == Plan.id)
        .outerjoin(creator, GiftKey.created_by_user_id == creator.id)
        .outerjoin(claimer, GiftKey.claimed_by_user_id == claimer.id)
        .order_by(GiftKey.id.desc())
    )
    return list((await session.execute(stmt)).all())


async def delete_stale(session: AsyncSession) -> int:
    """Hard-delete stale gift keys; return the number of rows removed.

    Two cases go (the keys-view-declutter feature):
    - any ``revoked`` key (it can never be claimed again), and
    - an UNCLAIMED (``active``) key past its shelf life, where shelf life =
      ``created_at + days`` (the key's own grant size doubles as its lifespan).

    ``claimed`` keys are NEVER deleted — they are the mint/claim audit trail (the
    frontend hides them behind a toggle instead). Credits-only keys (``days==0``)
    are EXEMPT from the days rule: ``created_at + 0`` would purge them instantly.

    Set-based DELETE — no ``FOR UPDATE`` (idempotent, no read-modify-write);
    caller commits.
    """
    expired_unclaimed = and_(
        GiftKey.status == "active",
        GiftKey.days > 0,
        GiftKey.created_at + func.make_interval(0, 0, 0, GiftKey.days) < func.now(),
    )
    stmt = delete(GiftKey).where(
        or_(GiftKey.status == "revoked", expired_unclaimed)
    )
    result = await session.execute(stmt)
    rowcount: int = getattr(result, "rowcount", 0) or 0
    return rowcount


async def mark_claimed(
    session: AsyncSession, key: GiftKey, *, claimed_by_user_id: int
) -> GiftKey:
    """Transition ``key`` to claimed (caller already locked + validated it)."""
    key.status = "claimed"
    key.claimed_by_user_id = claimed_by_user_id
    key.claimed_at = datetime.now(UTC)
    await session.flush()
    return key


async def revoke(
    session: AsyncSession, key: GiftKey, *, revoked_by_user_id: int
) -> GiftKey:
    """Transition an unclaimed ``key`` to revoked (caller locked + validated)."""
    key.status = "revoked"
    key.revoked_by_user_id = revoked_by_user_id
    key.revoked_at = datetime.now(UTC)
    await session.flush()
    return key
