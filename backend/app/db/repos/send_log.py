"""Data access for the write-ahead send log (Story 2.5).

DELIBERATELY UNSCOPED — like the worker section of repos/batches.py, this is
NOT the gates/users global exception nor a handler-facing module: every row is
written by ``core.send_worker`` (which runs outside any request and serves all
tenants) and read by Story 3.1's capture/attribution. ``tenant_id``/``batch_id``
are copied from the line so attribution never needs a join back.

Pure ORM, flush not commit — callers own the transaction.
"""

from collections.abc import Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BatchLine, SendLog


async def record_intent(session: AsyncSession, line: BatchLine) -> SendLog:
    """Get-or-create the line's send_log row (the write-ahead intent).

    Idempotent via ``uq_send_log_line_id``: a re-claim after a pause release
    (or a boot re-queue) REUSES the existing row — its ``message_id`` stays
    NULL until a delivery is confirmed. Committed in the SAME transaction as
    the 'sending' claim, so the intent exists BEFORE Telegram is called.
    """
    existing = (
        await session.execute(select(SendLog).where(SendLog.line_id == line.id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    intent = SendLog(
        tenant_id=line.tenant_id,
        batch_id=line.batch_id,
        line_id=line.id,
    )
    session.add(intent)
    await session.flush()
    return intent


async def set_message_id(
    session: AsyncSession, line_id: int, message_id: int
) -> None:
    """Confirm delivery: fill ``message_id`` on the line's intent row.

    A plain UPDATE keyed on ``line_id`` — idempotent, so the fail-stop record
    retry (worker, Task 6) can safely re-run it after a partially lost commit.
    """
    await session.execute(
        update(SendLog)
        .where(SendLog.line_id == line_id)
        .values(message_id=message_id)
    )


async def used_message_ids(
    session: AsyncSession, candidate_ids: Iterable[int]
) -> set[int]:
    """Subset of ``candidate_ids`` already attributed to some line.

    Boot reconciliation filter: an old outgoing message with identical text
    whose id is already recorded in send_log must not confirm a NEW line.
    """
    ids = list(candidate_ids)
    if not ids:
        return set()
    rows = (
        await session.execute(
            select(SendLog.message_id).where(SendLog.message_id.in_(ids))
        )
    ).scalars()
    return {message_id for message_id in rows if message_id is not None}
