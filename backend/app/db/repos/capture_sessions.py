"""Data access for capture sessions (Story 3.1).

TENANT-SCOPED — this is NOT the gates/users global exception: every function
takes ``tenant_id`` explicitly. Callers are the batches handler (binding at
batch start) and the capture/attribution pipeline — the latter runs OUTSIDE
any request (like the worker section of repos/batches.py, the documented
exception) but still resolves one explicit ``tenant_id`` per call, derived
from ``send_log``, never from a request.

Pure ORM, flush not commit — callers own the transaction.
"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CaptureSession


async def get_active(
    session: AsyncSession, tenant_id: int
) -> CaptureSession | None:
    """The tenant's single ACTIVE capture session, or ``None``.

    At most one exists — DB-enforced by the partial unique index
    ``uq_capture_sessions_one_active_per_tenant``.
    """
    stmt = select(CaptureSession).where(
        CaptureSession.tenant_id == tenant_id,
        CaptureSession.is_active,
    )
    return (await session.execute(stmt)).scalars().first()


async def create_active(
    session: AsyncSession, tenant_id: int, gate_value: str, gate_name: str
) -> CaptureSession:
    """Deactivate the previous active session and insert the new active one.

    Activation by replacement — legacy: "sessions are replaced by
    reassignment", never closed. The UPDATE runs first so the partial unique
    index (the belt) never trips on the honest path.
    """
    await session.execute(
        update(CaptureSession)
        .where(CaptureSession.tenant_id == tenant_id, CaptureSession.is_active)
        .values(is_active=False)
    )
    capture_session = CaptureSession(
        tenant_id=tenant_id,
        gate_value=gate_value,
        gate_name=gate_name,
        is_active=True,
    )
    session.add(capture_session)
    await session.flush()
    return capture_session


async def resolve_for_batch(
    session: AsyncSession, tenant_id: int, gate_value: str, gate_name: str
) -> CaptureSession:
    """The AC 3 legacy semantics: reuse the active session when its gate
    matches, otherwise auto-create a fresh active one.

    Exact port of "/api/enviar reuses the active Sesion when its slug matches
    the submitted prefix, otherwise auto-creates one".
    """
    active = await get_active(session, tenant_id)
    if active is not None and active.gate_value == gate_value:
        return active
    return await create_active(session, tenant_id, gate_value, gate_name)


async def resolve_for_backfill(
    session: AsyncSession, tenant_id: int, gate_value: str, gate_name: str
) -> CaptureSession:
    """Late-reply backfill (attribution path): like ``resolve_for_batch`` but
    it NEVER changes which session is active (review 3-1).

    A stray late reply to an unbound batch must not deactivate the tenant's
    live session as a side effect — that would silently move the snapshot's
    ``cc_new`` mid-use, split the tenant's next batch into yet another
    session, and let a concurrent batch start trip
    ``uq_capture_sessions_one_active_per_tenant`` outside the one race its
    IntegrityError fallback covers. Reuse the active session on exact gate
    match; otherwise insert an INACTIVE fallback — activation stays an
    API-only act at batch start.
    """
    active = await get_active(session, tenant_id)
    if active is not None and active.gate_value == gate_value:
        return active
    capture_session = CaptureSession(
        tenant_id=tenant_id,
        gate_value=gate_value,
        gate_name=gate_name,
        is_active=False,
    )
    session.add(capture_session)
    await session.flush()
    return capture_session
