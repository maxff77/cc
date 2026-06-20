"""Data access for captured responses (Story 3.1).

TENANT-SCOPED writes (``tenant_id`` is explicit on every insert) — reads used
by the capture pipeline run outside any request (the documented worker-style
exception, see repos/batches.py): ``last_full_revision`` is keyed on
``(chat_id, message_id)`` (Telegram ids are per-CHAT, not account-wide — see
``SendLog``; the row carries the tenant the caller trusts), and ``cc_count`` on
a capture-session id the caller already resolved tenant-scoped.

Pure ORM, flush not commit — callers own the transaction.
"""

from sqlalchemy import func, select, update
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
    session: AsyncSession,
    capture_session_id: int,
    status: str | None = None,
    *,
    include_hidden: bool = False,
) -> int:
    """Total 'full' revisions of one capture session (Story 3.2: the Completa
    badge — the REAL total, honest even when the snapshot list is capped).

    ``status`` (e.g. ``STATUS_OK``) restricts the count to that status — the
    "Filtrada con response" badge: only the ✅ revisions.

    ``include_hidden=False`` (default) EXCLUDES soft-hidden rows
    (clear-declined): this is a DISPLAY count (the Completa badge), so it must
    match ``list_full``. Integrity counters (e.g. ``responded_line_count``) do
    NOT go through here and keep counting hidden rows."""
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
    if not include_hidden:
        stmt = stmt.where(Response.hidden_at.is_(None))
    count: int = (await session.execute(stmt)).scalar_one()
    return count


async def hide_rejected(session: AsyncSession, capture_session_id: int) -> int:
    """Soft-hide every still-visible REJECTED (❌) 'full' revision of a capture
    session (clear-declined) — the cockpit "Limpiar" action.

    Stamps ``hidden_at`` so ``list_full``/``full_count`` drop these rows from
    Completa and the ``.txt`` export, while EVERY integrity query keeps seeing
    them (none filter ``hidden_at``): ``responded_line_count`` still counts the
    line — so "esperando respuesta" does NOT spike — and the reply reconciler's
    ``_answered_full_exists`` still finds a 'full' row, so a hidden ❌ is never
    re-fetched from Telegram and re-inserted. ✅ ('ok') and 'cc' rows are never
    touched. Idempotent: a second call hides 0 (the ``hidden_at IS NULL`` guard).
    Returns the number of rows hidden. Flush-not-commit — the caller owns the
    transaction."""
    result = await session.execute(
        update(Response)
        .where(
            Response.capture_session_id == capture_session_id,
            Response.kind == KIND_FULL,
            Response.status == STATUS_REJECTED,
            Response.hidden_at.is_(None),
        )
        .values(hidden_at=func.now())
    )
    await session.flush()
    return result.rowcount


async def responded_line_count(
    session: AsyncSession, capture_session_id: int
) -> int:
    """Number of ANSWERED LINES in this session — the denominator of the
    "esperando respuesta" counter.

    Counts ``COUNT(DISTINCT line_id)`` among 'full' rows: every ✅/❌ revision of
    a line carries the same ``line_id``, so this collapses all revisions — AND
    all per-attempt rows of a rotated cookie-mode line — to ONE answered line.

    Why ``line_id`` and not ``(chat_id, message_id)`` (Phase 2 PATCH 7): an
    Amazon cookie-mode line that rotated yields TWO answered ``(chat_id,
    message_id)`` full rows (the dead attempt's terminal revision + the resend's
    verdict), each with a DISTINCT ``.amz`` ``message_id`` but the SAME
    ``line_id``. Counting distinct message pairs would count that one line
    TWICE, over-counting answered and under-counting "esperando respuesta". The
    minuend (``send_log.sent_count_for_session``) counts each line ONCE (send_log
    reuses one row per line), so the denominator must too. ``line_id`` is the
    line-identity the minuend keys on, so the two agree.

    Rows with ``line_id = NULL`` (a SET-NULL'd batch / an unattributed full row)
    are EXCLUDED by ``COUNT(DISTINCT line_id)`` (NULLs don't count) — matching
    the minuend, which only counts send_log rows bound to a real line. Runs over
    ``responses.line_id``."""
    stmt = select(func.count(func.distinct(Response.line_id))).where(
        Response.capture_session_id == capture_session_id,
        Response.kind == KIND_FULL,
    )
    count: int = (await session.execute(stmt)).scalar_one()
    return count


async def _list_last(
    session: AsyncSession,
    capture_session_id: int,
    kind: str,
    limit: int | None,
    status: str | None = None,
    include_hidden: bool = False,
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
    if not include_hidden:
        # DISPLAY/export read: drop soft-hidden rows (clear-declined). 'cc' rows
        # are never hidden, so this is a no-op for the Filtrada list.
        stmt = stmt.where(Response.hidden_at.is_(None))
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
    *,
    include_hidden: bool = False,
) -> list[Response]:
    """The session's last ``limit`` 'full' revisions (``None`` = all),
    oldest→newest — the rows that rebuild the Completa view. ``status`` (e.g.
    ``STATUS_OK``) yields only that status — the "Filtrada con response"
    view's full ✅ texts.

    ``include_hidden=False`` (default) drops soft-hidden rows (clear-declined):
    this feeds the Completa display and the ``.txt`` export, both of which must
    hide the cleared declined revisions."""
    return await _list_last(
        session, capture_session_id, KIND_FULL, limit, status, include_hidden
    )


async def list_cc(
    session: AsyncSession, capture_session_id: int, limit: int | None
) -> list[Response]:
    """The session's last ``limit`` deduped CC values (``None`` = all) in
    insertion order — the rows that rebuild the Filtrada view."""
    return await _list_last(session, capture_session_id, KIND_CC, limit)
