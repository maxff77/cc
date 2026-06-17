"""Data access for captured responses (Story 3.1).

TENANT-SCOPED writes (``tenant_id`` is explicit on every insert) — reads used
by the capture pipeline run outside any request (the documented worker-style
exception, see repos/batches.py): ``last_full_revision`` is keyed on
``(chat_id, message_id)`` (Telegram ids are per-CHAT, not account-wide — see
``SendLog``; the row carries the tenant the caller trusts), and ``cc_count`` on
a capture-session id the caller already resolved tenant-scoped.

Pure ORM, flush not commit — callers own the transaction.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Response
from app.db.repos import tenants as tenants_repo

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
    session: AsyncSession, *, chat_id: int, message_id: int
) -> Response | None:
    """Latest 'full' revision for ``(chat_id, message_id)`` (via
    ``ix_responses_chat_message``).

    This IS the durable per-message state of AC 5: it replaces the legacy
    in-memory dict — survives restarts and dedups the replays ``catch_up``
    re-delivers after a disconnection. Keyed on the PAIR because message ids
    are per-chat (supergroups reuse ids), so the bare id would collapse two
    distinct replies that share an id across two destinations.
    """
    stmt = (
        select(Response)
        .where(
            Response.chat_id == chat_id,
            Response.message_id == message_id,
            Response.kind == KIND_FULL,
        )
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
    chat_id: int,
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
        chat_id=chat_id,
        message_id=message_id,
        kind=KIND_FULL,
        status=status,
        text=text,
    )
    session.add(response)
    await session.flush()
    return response


async def has_ok_revision(
    session: AsyncSession, *, chat_id: int, message_id: int
) -> bool:
    """Does a 'full' revision with status 'ok' already exist for this message?

    Keyed on the ``(chat_id, message_id)`` PAIR (the per-chat message identity,
    via ``ix_responses_chat_message``) — the same key as ``last_full_revision``.
    This is the "first ✅" predicate of the credits charge: a message is charged
    at most once even across a ✅→❌→✅ re-bounce (a transition-based check would
    double-charge that), so the rule is "no prior ✅ row" rather than "previous
    state wasn't ✅". MUST be called BEFORE ``add_full`` inserts the new 'ok'
    row, or it would always find one.
    """
    stmt = (
        select(Response.id)
        .where(
            Response.chat_id == chat_id,
            Response.message_id == message_id,
            Response.kind == KIND_FULL,
            Response.status == STATUS_OK,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def charge_if_first_ok(
    session: AsyncSession,
    *,
    tenant_id: int,
    chat_id: int,
    message_id: int,
    cost: int,
) -> int | None:
    """Debit ``cost`` credits the FIRST time a message reaches ✅; else no-op.

    Returns the tenant's NEW balance when a charge happened, or ``None`` when
    nothing was charged (free gate ``cost <= 0``, or this message was already
    charged on an earlier ✅). Idempotent by the ``has_ok_revision`` check, so a
    capture retry (the whole ``process_incoming`` re-runs after a transient DB
    failure) never double-charges: if the prior attempt committed, the 'ok' row
    now exists and this returns ``None``; if it didn't, neither did the debit.

    🔒 Call BEFORE ``add_full`` (the existence check must not see the row being
    added) and in the SAME transaction (atomic with the persisted revision).
    Clamps at 0 via ``tenants_repo.add_credits`` — a mid-batch overrun past zero
    never goes negative, and the response is still persisted by the caller.
    """
    if cost <= 0:
        return None
    if await has_ok_revision(session, chat_id=chat_id, message_id=message_id):
        return None
    return await tenants_repo.add_credits(
        session, tenant_id, -cost, clamp_zero=True
    )


async def add_new_cc(
    session: AsyncSession,
    *,
    tenant_id: int,
    capture_session_id: int,
    batch_id: int | None,
    line_id: int | None,
    chat_id: int,
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
                chat_id=chat_id,
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


async def full_count(
    session: AsyncSession, capture_session_id: int, status: str | None = None
) -> int:
    """Total 'full' revisions of one capture session (Story 3.2: the Completa
    badge — the REAL total, honest even when the snapshot list is capped).

    ``status`` (e.g. ``STATUS_OK``) restricts the count to that status — the
    "Filtrada con response" badge: only the ✅ revisions."""
    stmt = (
        select(func.count())
        .select_from(Response)
        .where(
            Response.capture_session_id == capture_session_id,
            Response.kind == KIND_FULL,
        )
    )
    if status is not None:
        stmt = stmt.where(Response.status == status)
    count: int = (await session.execute(stmt)).scalar_one()
    return count


async def responded_message_count(
    session: AsyncSession, capture_session_id: int
) -> int:
    """Number of ANSWERED lines in this session — the denominator of the
    "esperando respuesta" counter.

    ``(chat_id, Response.message_id)`` identifies the attributed reply (the
    checker bot edits ONE reply per line, so every ✅/❌ revision of a line
    shares the pair). ``COUNT(DISTINCT (chat_id, message_id))`` therefore
    collapses all revisions of a line to one → the count of lines that received
    at least one ✅/❌. The PAIR (not the bare id) is the key because message ids
    are per-chat: two answered lines in two supergroups can share an id, and
    counting the id alone would under-count answered lines (over-counting
    awaiting). If a line ever drew two DISTINCT reply messages it would
    over-count, but the caller's ``max(0, …)`` already pins that to 0. Runs over
    ``ix_responses_chat_message``."""
    distinct_msgs = (
        select(Response.chat_id, Response.message_id)
        .where(
            Response.capture_session_id == capture_session_id,
            Response.kind == KIND_FULL,
        )
        .distinct()
        .subquery()
    )
    stmt = select(func.count()).select_from(distinct_msgs)
    count: int = (await session.execute(stmt)).scalar_one()
    return count


async def _list_last(
    session: AsyncSession,
    capture_session_id: int,
    kind: str,
    limit: int | None,
    status: str | None = None,
) -> list[Response]:
    """The LAST ``limit`` rows of ``kind``, returned ASCENDING by ``id``.

    SELECT newest-first + reverse in Python: the snapshot must carry the most
    RECENT rows when capped, but the panel paints oldest→newest and anchors
    its scroll at the bottom (Story 3.2). ``limit=None`` ⇒ no LIMIT and no
    reverse-dance — the COMPLETE data, ascending directly (Story 3.3: the
    full data belongs to Historial and export, the cap is snapshot-only).

    ``status`` restricts 'full' rows to that status (the "Filtrada con
    response" view = only ✅ revisions).
    """
    stmt = select(Response).where(
        Response.capture_session_id == capture_session_id,
        Response.kind == kind,
    )
    if status is not None:
        stmt = stmt.where(Response.status == status)
    if limit is None:
        stmt = stmt.order_by(Response.id.asc())
        return list((await session.execute(stmt)).scalars().all())
    stmt = stmt.order_by(Response.id.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    rows.reverse()
    return rows


async def list_full(
    session: AsyncSession,
    capture_session_id: int,
    limit: int | None,
    status: str | None = None,
) -> list[Response]:
    """The session's last ``limit`` 'full' revisions (``None`` = all),
    oldest→newest — the rows that rebuild the Completa view. ``status`` (e.g.
    ``STATUS_OK``) yields only that status — the "Filtrada con response"
    view's full ✅ texts."""
    return await _list_last(session, capture_session_id, KIND_FULL, limit, status)


async def list_cc(
    session: AsyncSession, capture_session_id: int, limit: int | None
) -> list[Response]:
    """The session's last ``limit`` deduped CC values (``None`` = all) in
    insertion order — the rows that rebuild the Filtrada view."""
    return await _list_last(session, capture_session_id, KIND_CC, limit)
