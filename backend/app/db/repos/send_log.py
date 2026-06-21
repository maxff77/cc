"""Data access for the write-ahead send log (Story 2.5).

DELIBERATELY UNSCOPED — like the worker section of repos/batches.py, this is
NOT the gates/users global exception nor a handler-facing module: every row is
written by ``core.send_worker`` (which runs outside any request and serves all
tenants) and read by Story 3.1's capture/attribution
(``get_by_chat_and_message_id``). ``tenant_id``/``batch_id`` are copied from
the line so attribution never needs a join back.

ASSUMES one Telegram account for the lifetime of the data, and keys attribution
on the ``(chat_id, message_id)`` PAIR: the message-id sequence is per-CHAT
(supergroup/channel destinations each start at 1 and reuse ids), so the bare id
is NOT unique across the multi-target send set — ``chat_id`` (the marked peer
id) namespaces it. Re-authenticating ``anon.session`` as another account
restarts those sequences; a stale row whose pair collides with a new reply
would mis-attribute it across tenants. The re-auth runbook must wipe this state
first (``scripts/telegram_auth.py`` prints the mandatory step).

Pure ORM, flush not commit — callers own the transaction.
"""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Batch, BatchLine, Response, SendLog
from app.db.repos.responses import KIND_FULL


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
    session: AsyncSession, line_id: int, chat_id: int, message_id: int
) -> None:
    """Confirm delivery: fill ``chat_id`` + ``message_id`` on the line's intent.

    A plain UPDATE keyed on ``line_id`` — idempotent, so the fail-stop record
    retry (worker, Task 6) can safely re-run it after a partially lost commit.
    Both are written together: attribution needs the PAIR (the id is per-chat).
    """
    await session.execute(
        update(SendLog)
        .where(SendLog.line_id == line_id)
        .values(chat_id=chat_id, message_id=message_id)
    )


async def clear_intent(session: AsyncSession, line_id: int) -> None:
    """Reset a line's write-ahead intent to "unconfirmed": NULL its
    ``chat_id``/``message_id`` (Phase 2 cookie rotation/timeout resend).

    A rotation/timeout resend is a NEW ``.amz`` message with a NEW
    ``message_id`` for the SAME line, but ``record_intent`` REUSES the line's
    one ``send_log`` row (keyed on ``uq_send_log_line_id``). Before the resend
    the worker persists the dead attempt's terminal ``kind='full'`` row (so its
    later edits still resolve via attribution's prior-responses path keyed on
    the OLD ``(chat_id, message_id)``) and then calls THIS to clear the row so
    the reused intent carries the NEW send's pair — no orphaned, unattributable
    ``send_log.message_id`` remains. A plain UPDATE keyed on ``line_id``;
    flush-not-commit, the caller owns the txn (and holds the batch FOR UPDATE).
    """
    await session.execute(
        update(SendLog)
        .where(SendLog.line_id == line_id)
        .values(chat_id=None, message_id=None)
    )
    await session.flush()


async def get_by_chat_and_message_id(
    session: AsyncSession, chat_id: int, message_id: int
) -> SendLog | None:
    """The hot attribution lookup of Story 3.1: ``(chat_id, reply_to_msg_id)``
    → row.

    Keyed on the PAIR (over ``ix_send_log_chat_message``) because message ids
    are per-chat, not account-global — a bare-id match would return a row from
    the wrong supergroup. The returned row carries ``tenant_id``/``batch_id``/
    ``line_id`` denormalized — attribution resolves the exact tenant, batch and
    line with no join.
    """
    stmt = (
        select(SendLog)
        .where(SendLog.chat_id == chat_id, SendLog.message_id == message_id)
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def used_message_pairs(
    session: AsyncSession, candidate_pairs: Iterable[tuple[int, int]]
) -> set[tuple[int, int]]:
    """Subset of ``(chat_id, message_id)`` pairs already attributed to a line.

    Boot reconciliation filter: an old outgoing message with identical text
    whose pair is already recorded in send_log must not confirm a NEW line.
    Keyed on the pair (the id alone collides across chats).
    """
    pairs = list(candidate_pairs)
    if not pairs:
        return set()
    rows = (
        await session.execute(
            select(SendLog.chat_id, SendLog.message_id).where(
                tuple_(SendLog.chat_id, SendLog.message_id).in_(pairs)
            )
        )
    ).all()
    return {(c, m) for c, m in rows if c is not None and m is not None}


async def awaiting_sent_keys(
    session: AsyncSession, *, within: datetime
) -> set[tuple[int, int]]:
    """Delivered sends still missing a captured reply — the reply reconciler's
    work-list (recovers replies the Telethon update stream dropped).

    Returns ``(chat_id, message_id)`` PAIRS. A row qualifies when ALL hold:
    ``chat_id``/``message_id`` are filled (delivery confirmed — a NULL pair is
    an unconfirmed attempt or a pre-fix row that can't be reconciled per-chat),
    its batch was created since ``within`` (bounds the reconciler's history
    scan; older sends are treated as lost-for-good and stop driving it), and NO
    'full' response row exists for its line (the line never received a ✅/❌).
    The pair is what a bot reply's ``(chat_id, reply_to_msg_id)`` points back
    at, so the returned set is matched directly against scanned inbound replies.
    The hot filter runs over ``ix_send_log_chat_message``; the per-row ``NOT
    EXISTS`` on ``responses.line_id`` is unindexed (acceptable: this runs once
    per ~45s reconciler pass, not on any request path).
    """
    answered = _answered_full_exists()
    stmt = (
        select(SendLog.chat_id, SendLog.message_id)
        .join(Batch, Batch.id == SendLog.batch_id)
        .where(
            SendLog.chat_id.is_not(None),
            SendLog.message_id.is_not(None),
            # A user-purged line (deleted from Historial) is NOT awaiting — its
            # missing 'full' row is a deliberate delete, not a dropped reply.
            SendLog.reply_purged_at.is_(None),
            Batch.created_at >= within,
            ~answered,
        )
    )
    rows = (await session.execute(stmt)).all()
    return {(c, m) for c, m in rows if c is not None and m is not None}


def _answered_full_exists():  # type: ignore[no-untyped-def]
    """Correlated ``EXISTS`` — a 'full' response for the outer ``SendLog`` line
    (shared by the awaiting work-list and its beyond-window counter)."""
    return (
        select(Response.id)
        .where(Response.line_id == SendLog.line_id, Response.kind == KIND_FULL)
        .exists()
    )


async def count_awaiting_beyond_window(
    session: AsyncSession, *, within: datetime
) -> int:
    """Delivered-but-unanswered sends OLDER than ``within`` — the ones the
    reconciler's bounded scan deliberately leaves behind.

    Surfaced (not silently dropped) so a growing tail of permanently-lost
    replies is visible to the owner: the reconciler folds this count into its
    pass log. Mirror of ``awaiting_sent_keys`` with the window flipped.
    """
    stmt = (
        select(func.count())
        .select_from(SendLog)
        .join(Batch, Batch.id == SendLog.batch_id)
        .where(
            SendLog.chat_id.is_not(None),
            SendLog.message_id.is_not(None),
            SendLog.reply_purged_at.is_(None),  # user-purged lines aren't awaiting
            Batch.created_at < within,
            ~_answered_full_exists(),
        )
    )
    return (await session.execute(stmt)).scalar_one()


async def sent_count_for_session(
    session: AsyncSession, capture_session_id: int
) -> int:
    """Lines DELIVERED (``message_id`` filled) across EVERY batch of a capture
    session — the numerator of the "esperando respuesta" counter.

    Joins ``batches`` on ``capture_session_id`` so it spans the session's
    batches, not just the live one (legacy "counters never reset" — the
    session, not the batch, owns the tally). A NULL ``message_id`` is an
    attempted-but-unconfirmed send and is excluded: only real deliveries can be
    awaiting a reply. Runs over ``ix_send_log_message_id``.
    """
    stmt = (
        select(func.count())
        .select_from(SendLog)
        .join(Batch, Batch.id == SendLog.batch_id)
        .where(
            Batch.capture_session_id == capture_session_id,
            SendLog.message_id.is_not(None),
        )
    )
    return (await session.execute(stmt)).scalar_one()
