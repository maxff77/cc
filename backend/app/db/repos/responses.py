"""Data access for captured responses (Story 3.1).

TENANT-SCOPED writes (``tenant_id`` is explicit on every insert) — reads used
by the capture pipeline run outside any request (the documented worker-style
exception, see repos/batches.py): ``last_full_revision`` is keyed on
``(chat_id, message_id)`` (Telegram ids are per-CHAT, not account-wide — see
``SendLog``; the row carries the tenant the caller trusts), and ``cc_count`` on
a capture-session id the caller already resolved tenant-scoped.

Pure ORM, flush not commit — callers own the transaction.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Batch, Response, SendLog
from app.db.repos import tenants as tenants_repo

# Row discriminator — plain strings, no DB enum (2.2 decision).
KIND_FULL = "full"
KIND_CC = "cc"

# Effective status of a 'full' revision; NULL on 'cc' rows.
STATUS_OK = "ok"
STATUS_REJECTED = "rejected"
# A captured reply with NO ✅/❌ glyph (the bot's terminal no-verdict answer).
# Visible ONLY in Completa; never counted as Aprobada/Rechazada, never billed,
# never extracted to CC, and invisible to "esperando respuesta".
STATUS_NEUTRAL = "neutral"

# Hard cap on an INDEXED CC value (review 3-1): ``uq_responses_session_msg_cc``
# is a btree including the raw text and Postgres rejects index rows over ~2704
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
    """Insert this MESSAGE's new CC values, preserving order; return them.

    SELECT the existing 'cc' texts of THIS ``(capture_session_id, chat_id,
    message_id)`` among ``values`` → INSERT the rest. Dedup is PER-MESSAGE, not
    cross-message (Datos CC mirrors Aprobadas one-row-per-approved-card: the
    same CC value seen on two different approved messages lands TWICE — the old
    tenant-lifetime collapse is gone). The per-message scope still makes capture
    retries / reconciler edit-replays of the SAME message idempotent. Race-free
    without locks: the capture consumer is single (core.capture) — the partial
    unique index ``uq_responses_session_msg_cc`` is the net, not the mechanism.
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
                    Response.chat_id == chat_id,
                    Response.message_id == message_id,
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


async def cc_count(
    session: AsyncSession,
    capture_session_id: int,
    *,
    cleared_response_id: int | None = None,
) -> int:
    """Total deduped CC rows of one capture session (the ``cc_total`` /
    snapshot ``cc_new`` metric — counters never reset, legacy parity).

    ``cleared_response_id`` (sessionless cockpit, PR-1) is a DISPLAY cutoff: when
    set, only rows with ``Response.id > cleared_response_id`` count — the cockpit
    "Limpiar" high-water-mark, applied ONLY on the cockpit/snapshot read path
    (NEVER on the ``add_new_cc`` dedup SELECT, which is per-message)."""
    stmt = (
        select(func.count())
        .select_from(Response)
        .where(
            Response.capture_session_id == capture_session_id,
            Response.kind == KIND_CC,
        )
    )
    if cleared_response_id is not None:
        stmt = stmt.where(Response.id > cleared_response_id)
    count: int = (await session.execute(stmt)).scalar_one()
    return count


def _latest_full_ids(
    capture_session_id: int,
    *,
    include_hidden: bool = False,
    cleared_response_id: int | None = None,
):  # type: ignore[no-untyped-def]
    """Subquery of the LATEST 'full' revision id per ``(chat_id, message_id)`` of
    one capture session — the cockpit Completa "one row per message" identity
    (parity with ``history_grouped``: the cockpit now collapses a message's
    revisions to its newest one instead of listing every edit).

    ``DISTINCT ON ((chat_id, message_id)) ORDER BY …, id DESC`` keeps the newest
    revision. The ``Limpiar`` cutoff and the ``hidden_at`` filter restrict the
    ELIGIBLE revisions BEFORE the pick (a message whose only post-cutoff activity
    is an edit still surfaces its latest post-cutoff revision). ``status`` is
    deliberately NOT applied here — the caller filters the OUTER row so "latest
    revision is ✅" is what Aprobadas counts/lists (a ✅→❌ message drops out)."""
    stmt = select(Response.id).where(
        Response.capture_session_id == capture_session_id,
        Response.kind == KIND_FULL,
    )
    if not include_hidden:
        stmt = stmt.where(Response.hidden_at.is_(None))
    if cleared_response_id is not None:
        stmt = stmt.where(Response.id > cleared_response_id)
    return (
        stmt.distinct(Response.chat_id, Response.message_id)
        .order_by(Response.chat_id, Response.message_id, Response.id.desc())
        .subquery()
    )


async def full_count(
    session: AsyncSession,
    capture_session_id: int,
    status: str | None = None,
    *,
    include_hidden: bool = False,
    cleared_response_id: int | None = None,
) -> int:
    """Number of distinct MESSAGES of one capture session (Story 3.2: the
    Completa badge — the REAL total, honest even when the snapshot list is
    capped). Counts the latest-revision-per-``(chat_id, message_id)`` set, so it
    matches the collapsed ``list_full`` row count (one per message, not per edit).

    ``status`` (e.g. ``STATUS_OK``) restricts to messages whose LATEST revision
    is that status — the "Filtrada con response" badge: only the ✅ messages.

    ``include_hidden=False`` (default) EXCLUDES soft-hidden rows
    (clear-declined): this is a DISPLAY count (the Completa badge), so it must
    match ``list_full``. Integrity counters (e.g. ``responded_line_count``) do
    NOT go through here and keep counting hidden rows.

    ``cleared_response_id`` (sessionless cockpit, PR-1) is a DISPLAY cutoff: when
    set, only rows with ``Response.id > cleared_response_id`` count — the cockpit
    "Limpiar" high-water-mark, applied ONLY on the cockpit/snapshot read path
    (admin reads pass it ``None`` positionally — keyword-only keeps that
    correct)."""
    latest = _latest_full_ids(
        capture_session_id,
        include_hidden=include_hidden,
        cleared_response_id=cleared_response_id,
    )
    inner = select(Response.id).where(Response.id.in_(select(latest.c.id)))
    if status is not None:
        inner = inner.where(Response.status == status)
    count: int = (
        await session.execute(select(func.count()).select_from(inner.subquery()))
    ).scalar_one()
    return count


async def hide_rejected(session: AsyncSession, capture_session_id: int) -> int:
    """Soft-hide every still-visible REJECTED (❌) 'full' revision of a capture
    session (the legacy per-session clear-declined).

    ponytail: DEAD since PR-1 removed the per-id ``clear-declined`` endpoint (its
    only caller). The cockpit "Limpiar" is now a NON-destructive view-cutoff
    (``capture_sessions.clear_view``), not a soft-hide — nothing writes
    ``hidden_at`` today, so the ``include_hidden`` display filter is an inert
    no-op. Kept (with the ``hidden_at`` column) for forward-compat / possible
    PR-2 reuse rather than ripping out a column + filter; delete if PR-2 lands
    without needing it.

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
        # Neutral (no-verdict) rows are NOT "answered" — a line whose only reply
        # carried no ✅/❌ stays esperando, identical to the pre-neutral behavior
        # (a ⏳ used to write no row at all). Only ✅/❌ resolve a line.
        Response.status.in_((STATUS_OK, STATUS_REJECTED)),
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
    *,
    cleared_response_id: int | None = None,
) -> list[Response]:
    """The LAST ``limit`` rows of ``kind``, returned ASCENDING by ``id``.

    SELECT newest-first + reverse in Python: the snapshot must carry the most
    RECENT rows when capped, but the panel paints oldest→newest and anchors
    its scroll at the bottom (Story 3.2). ``limit=None`` ⇒ no LIMIT and no
    reverse-dance — the COMPLETE data, ascending directly (Story 3.3: the
    full data belongs to Historial and export, the cap is snapshot-only).

    ``status`` restricts 'full' rows to that status (the "Filtrada con
    response" view = only ✅ revisions).

    ``cleared_response_id`` (sessionless cockpit, PR-1) is a DISPLAY cutoff: when
    set, only rows with ``Response.id > cleared_response_id`` are returned — the
    cockpit "Limpiar" high-water-mark. ``Response.id`` is already the sort key,
    so this composes cleanly and is tie-immune (unlike a ``created_at`` cutoff).
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
    if cleared_response_id is not None:
        stmt = stmt.where(Response.id > cleared_response_id)
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
    cleared_response_id: int | None = None,
) -> list[Response]:
    """The session's ONE-ROW-PER-MESSAGE Completa view (last ``limit`` messages,
    ``None`` = all), oldest→newest — each message's LATEST 'full' revision, not
    every edit (cockpit-completa-one-row-per-message: parity with Historial).
    ``status`` (e.g. ``STATUS_OK``) keeps only messages whose LATEST revision is
    that status — the Aprobadas / "Filtrada con response" ✅ texts (a ✅→❌
    message drops out).

    ``include_hidden=False`` (default) drops soft-hidden rows (clear-declined):
    this feeds the Completa display and the ``.txt`` export, both of which must
    hide the cleared declined revisions.

    ``cleared_response_id`` (sessionless cockpit, PR-1) is the cockpit "Limpiar"
    DISPLAY cutoff (``Response.id > cleared_response_id``). Keyword-only so the
    admin support read keeps calling ``list_full(session, id, None)``
    POSITIONALLY with no cutoff."""
    latest = _latest_full_ids(
        capture_session_id,
        include_hidden=include_hidden,
        cleared_response_id=cleared_response_id,
    )
    stmt = select(Response).where(Response.id.in_(select(latest.c.id)))
    if status is not None:
        stmt = stmt.where(Response.status == status)
    # Same cap dance as the old per-revision read: SELECT newest-first + reverse
    # so a capped snapshot carries the most RECENT messages but paints
    # oldest→newest. ``limit=None`` ⇒ the COMPLETE set, ascending directly.
    if limit is None:
        stmt = stmt.order_by(Response.id.asc())
        return list((await session.execute(stmt)).scalars().all())
    stmt = stmt.order_by(Response.id.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    rows.reverse()
    return rows


async def list_cc(
    session: AsyncSession,
    capture_session_id: int,
    limit: int | None,
    *,
    cleared_response_id: int | None = None,
) -> list[Response]:
    """The session's last ``limit`` deduped CC values (``None`` = all) in
    insertion order — the rows that rebuild the Filtrada view.

    ``cleared_response_id`` (sessionless cockpit, PR-1) is the cockpit "Limpiar"
    DISPLAY cutoff (``Response.id > cleared_response_id``). Keyword-only so the
    admin support read keeps calling ``list_cc(session, id, None)``
    POSITIONALLY with no cutoff."""
    return await _list_last(
        session,
        capture_session_id,
        KIND_CC,
        limit,
        cleared_response_id=cleared_response_id,
    )


# --- PR-2: approved-✅ history grouped by gate -------------------------------
#
# A read + three destructive deletes over the tenant's PERSISTED ``responses``
# rows directly — fully independent of the cockpit "Limpiar" cutoff (these NEVER
# touch ``cleared_response_id``). A message is "approved" iff its LATEST
# ``kind='full'`` revision is ``STATUS_OK`` (a ✅→❌→✅ message stays in; a
# ✅→❌ message drops out; a ⏳-only message never wrote a 'full' row at all).
# Grouped + keyed on the batch's ``gate_name`` / ``gate_display_value`` snapshot
# ONLY — ``gate_value`` is owner-only and is NEVER selected here.


@dataclass
class HistoryMessage:
    """One approved-✅ message in the tenant's history.

    ``id`` is the latest ✅ revision's ``responses.id`` — the delete handle the
    router returns and ``delete_message_group`` resolves. ``gate_name`` is
    ``None`` for a message whose batch was SET-NULL'd / never had a gate (the
    trailing "Sin gate" group). ``gate_value`` is deliberately absent (owner-only)."""

    id: int
    text: str
    created_at: datetime
    gate_name: str | None
    gate_display_value: str | None
    cc: list[str] = field(default_factory=list)


async def history_grouped(
    session: AsyncSession, tenant_id: int
) -> list[HistoryMessage]:
    """Every approved-✅ message of ``tenant_id``, newest-first, with its cc.

    The LATEST ``kind='full'`` revision per ``(chat_id, message_id)`` is picked
    with ``DISTINCT ON ((chat_id, message_id)) ORDER BY chat_id, message_id,
    id DESC``; only those whose ``status == STATUS_OK`` are kept (so a message
    whose terminal revision is ❌ is excluded). ``Batch`` is LEFT JOIN'd
    (``responses.batch_id`` is SET-NULL on batch cleanup) for the client-visible
    ``gate_name`` / ``gate_display_value`` snapshot. Each message's cc values
    (its ``kind='cc'`` rows sharing the same ``(chat_id, message_id)``) are
    attached. NO ``cleared_response_id`` filter — Limpiar never affects history.

    Returns the messages newest-first (by the ✅ revision's ``id``). The router
    shapes them into the gate groups + contract; this stays a flat, ordered list
    so the grouping/ordering policy lives in ONE place (the router)."""
    # DISTINCT ON the per-chat message identity, newest 'full' revision first.
    latest = (
        select(
            Response.id,
            Response.chat_id,
            Response.message_id,
            Response.status,
            Response.text,
            Response.created_at,
            Response.batch_id,
        )
        .where(
            Response.tenant_id == tenant_id,
            Response.kind == KIND_FULL,
        )
        .distinct(Response.chat_id, Response.message_id)
        .order_by(
            Response.chat_id,
            Response.message_id,
            Response.id.desc(),
        )
        .subquery()
    )
    # Keep only messages whose LATEST full revision is ✅, attach the gate snapshot.
    stmt = (
        select(
            latest.c.id,
            latest.c.chat_id,
            latest.c.message_id,
            latest.c.text,
            latest.c.created_at,
            Batch.gate_name,
            Batch.gate_display_value,
        )
        .select_from(latest)
        .outerjoin(Batch, Batch.id == latest.c.batch_id)
        .where(latest.c.status == STATUS_OK)
        .order_by(latest.c.id.desc())  # newest approved message first
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []

    # Attach cc per message: every 'cc' row of this tenant keyed by its
    # (chat_id, message_id). One query, grouped in memory by the message key.
    keys = {(row.chat_id, row.message_id) for row in rows}
    cc_stmt = (
        select(Response.chat_id, Response.message_id, Response.text, Response.id)
        .where(
            Response.tenant_id == tenant_id,
            Response.kind == KIND_CC,
        )
        .order_by(Response.id)
    )
    cc_by_message: dict[tuple[int | None, int], list[str]] = {}
    for cc_row in (await session.execute(cc_stmt)).all():
        key = (cc_row.chat_id, cc_row.message_id)
        if key in keys:
            cc_by_message.setdefault(key, []).append(cc_row.text)

    return [
        HistoryMessage(
            id=row.id,
            text=row.text,
            created_at=row.created_at,
            gate_name=row.gate_name,
            gate_display_value=row.gate_display_value,
            cc=cc_by_message.get((row.chat_id, row.message_id), []),
        )
        for row in rows
    ]


async def delete_message_group(
    session: AsyncSession, tenant_id: int, response_id: int
) -> int:
    """Delete EVERY ``responses`` row of one message (full revisions + cc).

    Resolves ``(tenant_id, chat_id, message_id)`` from ``response_id`` first; if
    the row is missing OR belongs to another tenant, returns ``-1`` (the router
    maps that to the SAME 404 as any not-found — no existence leak). Otherwise
    DELETEs every row sharing that ``(tenant_id, chat_id, message_id)`` and
    returns the rowcount. Only ``responses`` rows are touched — never
    ``batches`` / ``send_log`` / ``batch_lines``. Flush-not-commit."""
    owner = (
        await session.execute(
            select(Response.tenant_id, Response.chat_id, Response.message_id).where(
                Response.id == response_id
            )
        )
    ).first()
    if owner is None or owner.tenant_id != tenant_id:
        return -1
    result = await session.execute(
        delete(Response).where(
            Response.tenant_id == tenant_id,
            Response.chat_id.is_(owner.chat_id)
            if owner.chat_id is None
            else Response.chat_id == owner.chat_id,
            Response.message_id == owner.message_id,
        )
    )
    # Tombstone the line's send_log row so the reply reconciler won't re-fetch
    # this reply from Telegram and re-insert it (the bug: deleted history
    # reappearing). Only the (chat_id, message_id) the reconciler keys on needs
    # tombstoning — a NULL pair was never reconcilable to begin with.
    if owner.chat_id is not None and owner.message_id is not None:
        await session.execute(
            update(SendLog)
            .where(
                SendLog.tenant_id == tenant_id,
                SendLog.chat_id == owner.chat_id,
                SendLog.message_id == owner.message_id,
            )
            .values(reply_purged_at=func.now())
        )
    await session.flush()
    return result.rowcount


async def delete_by_gate(
    session: AsyncSession, tenant_id: int, gate_name: str
) -> int:
    """Delete every ``responses`` row of ``tenant_id`` whose batch's
    ``gate_name`` matches (the per-gate "borrar historial").

    Scoped via ``batch_id IN (SELECT batches.id WHERE tenant_id AND gate_name)``
    — a tenant-scoped subquery so a foreign batch can never be reached. Rows with
    ``batch_id IS NULL`` (the "Sin gate" group) match no named gate and survive.
    An unknown name deletes 0. Only ``responses`` rows are touched.
    Flush-not-commit."""
    batch_ids = (
        select(Batch.id)
        .where(Batch.tenant_id == tenant_id, Batch.gate_name == gate_name)
        .scalar_subquery()
    )
    result = await session.execute(
        delete(Response).where(
            Response.tenant_id == tenant_id,
            Response.batch_id.in_(batch_ids),
        )
    )
    # Tombstone these lines' send_log rows so the reconciler won't resurrect them.
    await session.execute(
        update(SendLog)
        .where(
            SendLog.tenant_id == tenant_id,
            SendLog.batch_id.in_(batch_ids),
        )
        .values(reply_purged_at=func.now())
    )
    await session.flush()
    return result.rowcount


async def delete_all_for_tenant(session: AsyncSession, tenant_id: int) -> int:
    """Delete EVERY ``responses`` row of ``tenant_id`` (the "borrar todo").

    Tenant-scoped — another tenant's rows are untouched. Only ``responses`` rows
    are touched; the batches/lines/send_log stay. Flush-not-commit."""
    result = await session.execute(
        delete(Response).where(Response.tenant_id == tenant_id)
    )
    # Tombstone every send_log row of the tenant so the reconciler won't
    # resurrect any purged reply.
    await session.execute(
        update(SendLog)
        .where(SendLog.tenant_id == tenant_id)
        .values(reply_purged_at=func.now())
    )
    await session.flush()
    return result.rowcount
