"""Data access for captured responses (Story 3.1).

TENANT-SCOPED writes (``tenant_id`` is explicit on every insert) — reads used
by the capture pipeline run outside any request (the documented worker-style
exception, see repos/batches.py): ``last_full_revision`` is keyed on the
GLOBAL ``message_id`` (Telegram ids are account-wide; the row carries the
tenant the caller trusts), and ``cc_count`` on a capture-session id the
caller already resolved tenant-scoped.

Pure ORM, flush not commit — callers own the transaction.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Response

# Row discriminator — plain strings, no DB enum (2.2 decision).
KIND_FULL = "full"
KIND_CC = "cc"

# Effective status of a 'full' revision; NULL on 'cc' rows.
STATUS_OK = "ok"
STATUS_REJECTED = "rejected"

# Hard cap on an INDEXED CC value (review 3-1): ``uq_responses_session_cc``
# is a btree over the raw text and Postgres rejects index rows over ~2704
# bytes (btree v4 limit) — a Telegram message carries up to 4096 chars, so an
# adversarial "CC:" line would otherwise make the INSERT fail identically on
# every retry and wedge the capture consumer. 600 chars × 4 bytes/char (UTF-8
# worst case) = 2400 bytes, safely under the limit; no legitimate datum is
# remotely that long.
CC_MAX_CHARS = 600


async def last_full_revision(
    session: AsyncSession, message_id: int
) -> Response | None:
    """Latest 'full' revision for ``message_id`` (via ``ix_responses_message_id``).

    This IS the durable per-message_id state of AC 5: it replaces the legacy
    in-memory dict — survives restarts and dedups the replays ``catch_up``
    re-delivers after a disconnection.
    """
    stmt = (
        select(Response)
        .where(Response.message_id == message_id, Response.kind == KIND_FULL)
        .order_by(Response.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def add_full(
    session: AsyncSession,
    *,
    tenant_id: int,
    capture_session_id: int,
    batch_id: int | None,
    line_id: int | None,
    message_id: int,
    status: str,
    text: str,
) -> Response:
    """Append one 'full' revision (every revision is kept — edit history)."""
    response = Response(
        tenant_id=tenant_id,
        capture_session_id=capture_session_id,
        batch_id=batch_id,
        line_id=line_id,
        message_id=message_id,
        kind=KIND_FULL,
        status=status,
        text=text,
    )
    session.add(response)
    await session.flush()
    return response


async def add_new_cc(
    session: AsyncSession,
    *,
    tenant_id: int,
    capture_session_id: int,
    batch_id: int | None,
    line_id: int | None,
    message_id: int,
    values: list[str],
) -> list[str]:
    """Insert only the session-NEW CC values, preserving order; return them.

    SELECT the session's existing 'cc' texts among ``values`` → INSERT the
    rest. Race-free without locks: the capture consumer is single
    (core.capture) — the partial unique index ``uq_responses_session_cc`` is
    the net, not the mechanism.
    """
    if not values:
        return []
    # Truncate BEFORE the dedup SELECT so the lookup and the insert agree on
    # the stored value (CC_MAX_CHARS — the indexed-row-size guard above).
    values = [value[:CC_MAX_CHARS] for value in values]
    existing = set(
        (
            await session.execute(
                select(Response.text).where(
                    Response.capture_session_id == capture_session_id,
                    Response.kind == KIND_CC,
                    Response.text.in_(values),
                )
            )
        )
        .scalars()
        .all()
    )
    inserted: list[str] = []
    for value in values:
        if value in existing:
            continue
        existing.add(value)  # in-call dedup too (legacy set semantics)
        session.add(
            Response(
                tenant_id=tenant_id,
                capture_session_id=capture_session_id,
                batch_id=batch_id,
                line_id=line_id,
                message_id=message_id,
                kind=KIND_CC,
                status=None,
                text=value,
            )
        )
        inserted.append(value)
    if inserted:
        await session.flush()
    return inserted


async def cc_count(session: AsyncSession, capture_session_id: int) -> int:
    """Total deduped CC rows of one capture session (the ``cc_total`` /
    snapshot ``cc_new`` metric — counters never reset, legacy parity)."""
    stmt = (
        select(func.count())
        .select_from(Response)
        .where(
            Response.capture_session_id == capture_session_id,
            Response.kind == KIND_CC,
        )
    )
    count: int = (await session.execute(stmt)).scalar_one()
    return count


async def full_count(session: AsyncSession, capture_session_id: int) -> int:
    """Total 'full' revisions of one capture session (Story 3.2: the Completa
    badge — the REAL total, honest even when the snapshot list is capped)."""
    stmt = (
        select(func.count())
        .select_from(Response)
        .where(
            Response.capture_session_id == capture_session_id,
            Response.kind == KIND_FULL,
        )
    )
    count: int = (await session.execute(stmt)).scalar_one()
    return count


async def _list_last(
    session: AsyncSession, capture_session_id: int, kind: str, limit: int
) -> list[Response]:
    """The LAST ``limit`` rows of ``kind``, returned ASCENDING by ``id``.

    SELECT newest-first + reverse in Python: the snapshot must carry the most
    RECENT rows when capped, but the panel paints oldest→newest and anchors
    its scroll at the bottom (Story 3.2).
    """
    stmt = (
        select(Response)
        .where(
            Response.capture_session_id == capture_session_id,
            Response.kind == kind,
        )
        .order_by(Response.id.desc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    rows.reverse()
    return rows


async def list_full(
    session: AsyncSession, capture_session_id: int, limit: int
) -> list[Response]:
    """The session's last ``limit`` 'full' revisions, oldest→newest — the
    rows the snapshot ships to rebuild the Completa view."""
    return await _list_last(session, capture_session_id, KIND_FULL, limit)


async def list_cc(
    session: AsyncSession, capture_session_id: int, limit: int
) -> list[Response]:
    """The session's last ``limit`` deduped CC values in insertion order —
    the rows the snapshot ships to rebuild the Filtrada view."""
    return await _list_last(session, capture_session_id, KIND_CC, limit)
