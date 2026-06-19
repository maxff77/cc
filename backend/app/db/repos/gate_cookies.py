"""Data access for gate cookies (cookie-vault feature, Phase 1).

TENANT-SCOPED — unlike the GLOBAL ``repos.gates``/``repos.gate_categories``
catalogs, every function takes ``tenant_id`` explicitly: a client stores, lists
and deletes only their OWN cookies. ``tenant_id`` always comes from the session
at the route, never from body/path.

🔒 The repo is intentionally DUMB about the credential: it never computes the
hash, never masks, never validates, never logs the value — the router does all
of that and passes a pre-computed ``value_hash``, so the same canonical bytes
the validator saw key the unique index. Dedup is DB-enforced
(``uq_gate_cookies_tenant_gate_hash``): ``create`` is store-first and lets a
unique violation RAISE to the caller, which rolls back and re-fetches via
``get_by_hash`` — never SELECT-then-INSERT.

Pure ORM, flush not commit — callers own the transaction.
"""

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GateCookie

# Phase-2 rotation: ``status`` values the rotation layer reads (Phase 1 left
# the column at server_default ``'active'`` with no reader). 'dead' is set on a
# cookie-dead verdict — kept for the UI, excluded from the FIFO active pick.
COOKIE_ACTIVE = "active"
COOKIE_DEAD = "dead"


async def create(
    session: AsyncSession,
    *,
    tenant_id: int,
    gate_id: int,
    value: str,
    value_hash: str,
    label: str | None,
) -> GateCookie:
    """Insert and flush a fresh cookie (store-first dedup).

    ``value_hash`` is computed by the caller (the repo stays dumb). The flush
    surfaces a unique violation on ``(tenant_id, gate_id, value_hash)`` as an
    ``IntegrityError`` — the router catches it narrowly, rolls back and
    re-fetches the existing row with ``get_by_hash`` (store-first / catch-second
    is the only dedup arbiter; never SELECT-then-INSERT).
    """
    cookie = GateCookie(
        tenant_id=tenant_id,
        gate_id=gate_id,
        value=value,
        value_hash=value_hash,
        label=label,
    )
    session.add(cookie)
    await session.flush()
    return cookie


async def get_by_hash(
    session: AsyncSession, tenant_id: int, gate_id: int, value_hash: str
) -> GateCookie | None:
    """The tenant's cookie for ``(gate_id, value_hash)``, or ``None``.

    The idempotent re-fetch after a unique violation: the caller rolls back
    FIRST (the txn is aborted by the violation), THEN calls this in a clean
    transaction to return the pre-existing row 200.
    """
    stmt = select(GateCookie).where(
        GateCookie.tenant_id == tenant_id,
        GateCookie.gate_id == gate_id,
        GateCookie.value_hash == value_hash,
    )
    return (await session.execute(stmt)).scalars().first()


async def count_for(session: AsyncSession, tenant_id: int, gate_id: int) -> int:
    """How many cookies the tenant has stored for this gate (cap guard).

    SQL ``COUNT`` (not ``len(rows)``) — the count runs on every store, so it
    must not materialize rows.
    """
    stmt = (
        select(func.count())
        .select_from(GateCookie)
        .where(
            GateCookie.tenant_id == tenant_id,
            GateCookie.gate_id == gate_id,
        )
    )
    return (await session.execute(stmt)).scalar_one()


async def list_by_tenant_gate(
    session: AsyncSession,
    tenant_id: int,
    gate_id: int,
    *,
    limit: int,
) -> list[GateCookie]:
    """The tenant's cookies for this gate, newest first, bounded ``limit``.

    A foreign/unknown ``gate_id`` simply returns an empty list (tenant scoping
    makes the lookup miss) — identical to "no cookies", so no existence leaks.
    """
    stmt = (
        select(GateCookie)
        .where(
            GateCookie.tenant_id == tenant_id,
            GateCookie.gate_id == gate_id,
        )
        .order_by(GateCookie.id.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def delete_by_id(
    session: AsyncSession, tenant_id: int, cookie_id: int
) -> bool:
    """Tenant-scoped hard delete; ``True`` iff a row matched.

    The ``tenant_id`` predicate makes an unknown/foreign id a clean no-op
    (rowcount 0) — the router 404s identically, so existence is never leaked.
    """
    stmt = delete(GateCookie).where(
        GateCookie.id == cookie_id,
        GateCookie.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    rowcount: int = getattr(result, "rowcount", 0) or 0
    return rowcount > 0


# --- Phase-2 rotation (the active-cookie FIFO pick) --------------------------
#
# Used by the send worker's cookie-mode branch, never by request handlers. The
# active cookie for a cookie-mode send is the OLDEST ``status='active'`` row by
# ``id ASC`` for ``(tenant_id, gate_id)`` (FIFO). On a cookie-dead verdict the
# worker ``mark_dead``s the current cookie (committed BEFORE the next pick, or
# in one txn that marks dead THEN selects the next excluding the just-marked id)
# so a just-dead cookie can never be re-picked. The composite index
# ``ix_gate_cookies_tenant_gate_status_id`` keeps this off a full scan.


async def get_active_for_rotation(
    session: AsyncSession,
    tenant_id: int,
    gate_id: int,
    *,
    exclude_id: int | None = None,
) -> GateCookie | None:
    """The oldest ``status='active'`` cookie for ``(tenant, gate)``, or ``None``.

    FIFO by ``id ASC`` with ``FOR UPDATE SKIP LOCKED`` so two worker turns (or
    the worker and a concurrent verdict) never contend on the same row — the
    loser skips to the next active cookie instead of blocking. ``None`` ⇒ no
    active cookie remains (exhaustion).

    ``exclude_id`` lets the rotation resend run ``mark_dead`` + this pick in ONE
    transaction: pass the just-marked cookie's id so the next-oldest active row
    is chosen even before the ``status='dead'`` write is visible to a fresh
    snapshot (the just-dead cookie can never be re-picked).
    """
    stmt = (
        select(GateCookie)
        .where(
            GateCookie.tenant_id == tenant_id,
            GateCookie.gate_id == gate_id,
            GateCookie.status == COOKIE_ACTIVE,
        )
        .order_by(GateCookie.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if exclude_id is not None:
        stmt = stmt.where(GateCookie.id != exclude_id)
    return (await session.execute(stmt)).scalars().first()


async def count_active_for(
    session: AsyncSession, tenant_id: int, gate_id: int
) -> int:
    """How many ``status='active'`` cookies remain for ``(tenant, gate)``.

    SQL ``COUNT`` (not ``len(rows)``). 0 ⇒ exhausted (the worker pauses the
    batch ``cookies_exhausted``).
    """
    stmt = (
        select(func.count())
        .select_from(GateCookie)
        .where(
            GateCookie.tenant_id == tenant_id,
            GateCookie.gate_id == gate_id,
            GateCookie.status == COOKIE_ACTIVE,
        )
    )
    return (await session.execute(stmt)).scalar_one()


async def mark_dead(
    session: AsyncSession, cookie_id: int, tenant_id: int
) -> bool:
    """Tenant-scoped, idempotent ``status='dead'`` set; ``True`` iff a row matched.

    Tenant-scoped (the ``tenant_id`` predicate makes a foreign id a clean
    no-op) and idempotent — re-running on an already-dead cookie still matches
    the row (rowcount 1) but changes nothing, so a replayed cookie-dead verdict
    never errors. Flush, not commit: the caller (the rotation resend) owns the
    transaction and decides whether ``mark_dead`` commits before the next pick
    or rides one txn with ``get_active_for_rotation(exclude_id=cookie_id)``.
    """
    stmt = (
        update(GateCookie)
        .where(
            GateCookie.id == cookie_id,
            GateCookie.tenant_id == tenant_id,
        )
        .values(status=COOKIE_DEAD)
    )
    result = await session.execute(stmt)
    await session.flush()
    rowcount: int = getattr(result, "rowcount", 0) or 0
    return rowcount > 0
