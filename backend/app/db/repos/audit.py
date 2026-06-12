"""Data access for the audit log (Story 3.6).

The ONLY writer is the cross-tenant support view in ``api/admin.py`` —
architecture :248: "Owner/admin cross-tenant access goes through explicit
``for_tenant(id)`` support paths, audit-logged". No other endpoint records
here (the global admin user/gate routes are cross-tenant BY DESIGN and don't
touch session data — auditing them is not this story).

Pure ORM, flush not commit — the caller owns the transaction (and the
support handler commits the audit row BEFORE serving data: fail-closed).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


async def record(
    session: AsyncSession,
    *,
    actor_user_id: int,
    tenant_id: int,
    action: str,
    capture_session_id: int | None = None,
) -> AuditLog:
    """Insert and flush one audit row for a cross-tenant support read.

    ``tenant_id`` is the TARGET tenant of the cross (never the actor's);
    ``capture_session_id`` only on per-session actions — a plain historical
    reference, no FK (see the model docstring).
    """
    row = AuditLog(
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        action=action,
        capture_session_id=capture_session_id,
    )
    session.add(row)
    await session.flush()
    return row
