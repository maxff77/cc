"""Data access for capture sessions (Story 3.1; Historial reads/deletes 3.3).

TENANT-SCOPED — this is NOT the gates/users global exception: every function
takes ``tenant_id`` explicitly. Callers are the batches handler (binding at
batch start), the Historial router (3.3) and the capture/attribution pipeline
— the latter runs OUTSIDE any request (like the worker section of
repos/batches.py, the documented exception) but still resolves one explicit
``tenant_id`` per call, derived from ``send_log``, never from a request.

Pure ORM, flush not commit — callers own the transaction.
"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CaptureSession


async def get_active(
    session: AsyncSession, tenant_id: int, *, for_update: bool = False
) -> CaptureSession | None:
    """The tenant's single ACTIVE capture session, or ``None``.

    At most one exists — DB-enforced by the partial unique index
    ``uq_capture_sessions_one_active_per_tenant``.

    ``for_update=True`` locks the active row until commit (same knob as
    ``get_for_tenant``). ``new_session`` passes it so it serializes against a
    concurrent Nueva-sesión / Continuar / batch-start the way
    ``continue_session`` locks its target — instead of leaning only on the
    partial unique index tripping at commit.
    """
    stmt = select(CaptureSession).where(
        CaptureSession.tenant_id == tenant_id,
        CaptureSession.is_active,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().first()


async def list_for_tenant(
    session: AsyncSession, tenant_id: int
) -> list[CaptureSession]:
    """ALL of the tenant's sessions, newest first (Story 3.3 Historial list).

    No pagination — MVP scale (NFR2); the grouping by gate is presentation
    (client-side).
    """
    stmt = (
        select(CaptureSession)
        .where(CaptureSession.tenant_id == tenant_id)
        .order_by(CaptureSession.id.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_for_tenant(
    session: AsyncSession,
    tenant_id: int,
    session_id: int,
    *,
    for_update: bool = False,
) -> CaptureSession | None:
    """TENANT-SCOPED lookup by id (Story 3.3 detail/rename/delete).

    Another tenant's id returns ``None`` (the handler 404s — existence is
    never leaked; exact mirror of ``batches_repo.get_batch``).

    ``for_update=True`` locks the row until commit (same knob as
    ``batches_repo.get_batch``). The DELETE path must pass it so it
    serializes with a concurrent ``POST /api/batches`` binding this same
    session: the batch INSERT's FK check takes ``FOR KEY SHARE`` on this row,
    which conflicts with ``FOR UPDATE`` — see ``delete_session``.
    """
    stmt = select(CaptureSession).where(
        CaptureSession.id == session_id,
        CaptureSession.tenant_id == tenant_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().first()


async def delete(
    session: AsyncSession, capture_session: CaptureSession
) -> None:
    """Hard-delete one capture session (Story 3.3 AC 5).

    Zero manual child cleanup: ``responses`` rows die via the DB FK CASCADE
    (models.py) and batches survive with ``capture_session_id`` NULL (FK SET
    NULL) — the lote history is the lote's, not the session's.
    """
    await session.delete(capture_session)
    await session.flush()


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


async def activate(
    session: AsyncSession, capture_session: CaptureSession
) -> None:
    """Reactivate an EXISTING session as the tenant's active one (Story 3.4).

    Mirror of ``create_active``'s UPDATE-first dance (the partial unique
    index ``uq_capture_sessions_one_active_per_tenant`` never trips on the
    honest path) — but flipping an existing row instead of inserting.

    The ``id != capture_session.id`` exclusion in the UPDATE is load-bearing:
    if the UPDATE included the target, "continuing" the ALREADY-active session
    would set ``is_active=False`` in the DB while the loaded ORM instance
    stays ``True`` in memory — the ``True → True`` assignment registers no
    change, the flush emits no UPDATE and the row would end up INACTIVE. With
    the exclusion the already-active path is a clean no-op (idempotent) and
    the closed→active path registers ``False → True`` and flushes.
    """
    await session.execute(
        update(CaptureSession)
        .where(
            CaptureSession.tenant_id == capture_session.tenant_id,
            CaptureSession.is_active,
            CaptureSession.id != capture_session.id,
        )
        .values(is_active=False)
    )
    capture_session.is_active = True
    await session.flush()


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
